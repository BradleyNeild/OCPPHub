import asyncio
import websockets
import logging
import json
import os
from dotenv import load_dotenv
from asgiref.sync import sync_to_async
import django
import requests
from typing import Optional, Dict

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ocpp_hub_project.settings')
django.setup()
from ocpp_hub_app.models import Authorization, ChargePoint
from proxy_utils import get_access_token, identify_event_type
from database_utils import (
    update_authorization_connection_status_async,
    update_chargepoint_connection_status_async,
    get_authorizations_by_chargepoint_uuid_async,
    create_log_entry_async
)
from message_processor import MessageProcessor

# Load environment variables
load_dotenv()

logger = logging.getLogger('app_logger')

WEB_SERVICE_URL = os.getenv('WEB_SERVICE_URL')
OAUTH2_PROXY_CLIENT_ID = os.getenv('OAUTH2_PROXY_CLIENT_ID')
OAUTH2_PROXY_CLIENT_SECRET = os.getenv('OAUTH2_PROXY_CLIENT_SECRET')
OAUTH2_TOKEN_URL = os.getenv('OAUTH2_TOKEN_URL')
RECONNECT_INTERVAL = int(os.getenv('RECONNECT_INTERVAL', 60))  # Interval in seconds for reconnect attempts

class ConnectionManager:
    def __init__(self):
        self.chargepoint_connections = {}
        self.csms_connections = {}
        self.closed_authorizations = set()
        self.message_processor = None
        self.disconnected_csms = set()
        self.connection_attempts = {}  # To track connection attempts per chargepoint
        self.connection_locks: Dict[str, asyncio.Lock] = {}

    async def initialize(self):
        self.message_processor = MessageProcessor(self)
        asyncio.create_task(self._periodic_reconnect())

    async def get_chargepoint_details(self, chargepoint_uuid: str, access_token: str):
        details_url = f"{WEB_SERVICE_URL}api/v1/chargepoints/{chargepoint_uuid}/details/"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        response = requests.get(details_url, headers=headers)
        if response.status_code != 200:
            logger.error(f"Failed to fetch details for CP {chargepoint_uuid}: {response.content.decode()}")
            return None
        return response.json()

    async def proxy_handler(self, chargepoint_ws: websockets.WebSocketClientProtocol, path: str):
        chargepoint_uuid = path.strip('/')
        logger.info(f"CP connected: {chargepoint_ws.remote_address}")

        access_token = None
        actual_chargepoint_uuid = chargepoint_uuid

        try:
            access_token = get_access_token()
            if not access_token:
                logger.error("Failed to obtain access token")
                await chargepoint_ws.close(code=1011, reason="Failed to obtain access token")
                return

            logger.info(f"Access token obtained: {access_token}")

            details = await self.get_chargepoint_details(chargepoint_uuid, access_token)
            if not details:
                await chargepoint_ws.close(code=1011, reason="Failed to fetch chargepoint details")
                return

            actual_chargepoint_uuid = details.get('uuid', chargepoint_uuid)  # Extract the correct UUID
            await self._add_chargepoint_connection(actual_chargepoint_uuid, chargepoint_ws)

            async for message in chargepoint_ws:
                await self._process_message_from_chargepoint(actual_chargepoint_uuid, message)

        except Exception as e:
            await self._exception_handler(chargepoint_ws, e, chargepoint_uuid, access_token if 'access_token' in locals() else None)
        finally:
            logger.info("CP connection closed")
            await self._remove_chargepoint_connection(actual_chargepoint_uuid)

    async def set_primary_authorization_async(self, chargepoint_uuid: str, new_primary_uuid: str, authorizations: list):
        logger.info(f"Setting primary authorization {new_primary_uuid} for CP {chargepoint_uuid}")
        try:
            new_primary = next((auth for auth in authorizations if auth['uuid'] == new_primary_uuid), None)

            if not new_primary:
                logger.error(f"Authorization {new_primary_uuid} not found for CP {chargepoint_uuid}")
                return False

            for auth in authorizations:
                if auth['uuid'] == new_primary_uuid:
                    auth['is_primary'] = True
                    logger.info(f"Set authorization {new_primary_uuid} as primary for CP {chargepoint_uuid}")
                else:
                    auth['is_primary'] = False
                    logger.info(f"Set authorization {auth['uuid']} as secondary for CP {chargepoint_uuid}")

            # Reset connections after changing primary authorization
            await self.reset_chargepoint_connections(chargepoint_uuid)

            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=new_primary_uuid,  # Changed from authorization_uuid to authorization
                event_type='Authorization',
                action="Set Primary Authorization",
                from_entity={"name": "System", "type": "System"},
                to_entity={"name": "Chargepoint", "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=f"Set authorization {new_primary_uuid} as primary CSMS for CP {chargepoint_uuid}",
                level='INFO'
            )
            return True
        except Exception as e:
            logger.error(f"Error setting primary authorization: {str(e)}", exc_info=True)
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                event_type='Error',
                action="Set Primary Authorization Failed",
                from_entity={"name": "System", "type": "System"},
                to_entity={"name": "Chargepoint", "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=f"Error setting primary authorization: {str(e)}",
                level='ERROR',
                raw_message=str(e)
            )
            return False

    async def reset_chargepoint_connections(self, chargepoint_uuid: str):
        logger.info(f"Resetting connections for CP {chargepoint_uuid}")
        try:
            # Close existing connections
            await self._remove_chargepoint_connection(chargepoint_uuid)
            if chargepoint_uuid in self.csms_connections:
                for websocket, authorization in self.csms_connections[chargepoint_uuid]:
                    await self._remove_csms_connection(chargepoint_uuid, authorization.uuid)
                del self.csms_connections[chargepoint_uuid]

            # Re-fetch chargepoint details
            access_token = get_access_token()
            details = await self.get_chargepoint_details(chargepoint_uuid, access_token)
            if not details:
                logger.error(f"Failed to fetch details for CP {chargepoint_uuid}")
                return False

            # Re-establish CSMS connections
            authorizations = await get_authorizations_by_chargepoint_uuid_async(chargepoint_uuid)
            for authorization in authorizations:
                await self._connect_to_csms(chargepoint_uuid, authorization)

            logger.info(f"Successfully reset connections for CP {chargepoint_uuid}")
            return True
        except Exception as e:
            logger.error(f"Error resetting connections for CP {chargepoint_uuid}: {str(e)}", exc_info=True)
            return False

    async def _add_chargepoint_connection(self, chargepoint_uuid: str, websocket: Optional[websockets.WebSocketClientProtocol]):
        chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
        chargepoint_name = chargepoint.name
        
        logger.info(f"Adding CP connection to proxy: {chargepoint_name}")
        
        if chargepoint_uuid in self.chargepoint_connections:
            if websocket is None:
                logger.info(f"CP {chargepoint_name} is already connected. Skipping connection.")
                return
            else:
                logger.warning(f"CP {chargepoint_name} already connected. Updating existing connection.")
                existing_websocket = self.chargepoint_connections[chargepoint_uuid]
                if existing_websocket:
                    await existing_websocket.close()
                    logger.info(f"Closed existing websocket for CP {chargepoint_name}")

        if websocket:
            self.chargepoint_connections[chargepoint_uuid] = websocket
            logger.info(f"New websocket connection added for CP {chargepoint_name}")
        else:
            if chargepoint_uuid in self.chargepoint_connections:
                del self.chargepoint_connections[chargepoint_uuid]
                logger.info(f"Removed existing connection for CP {chargepoint_name}")
            logger.info(f"No new websocket provided for CP {chargepoint_name}. Connection status will be updated.")
        
        await update_chargepoint_connection_status_async(chargepoint_uuid, 'Connected')
        logger.info(f"Updated connection status to 'Connected' for CP {chargepoint_name}")

        await create_log_entry_async(
            chargepoint_uuid=chargepoint_uuid,
            event_type='Connection',
            action="Chargepoint Connection Updated",
            from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
            to_entity={"name": "Proxy", "type": "System"},
            message=f"CP {chargepoint_name} connection updated in proxy",
            level='INFO'
        )

        authorizations = await get_authorizations_by_chargepoint_uuid_async(chargepoint_uuid)
        logger.info(f"Initiating CSMS connections for CP {chargepoint_name}. Total authorizations: {len(authorizations)}")
        for authorization in authorizations:
            await self._connect_to_csms(chargepoint_uuid, authorization)

        logger.info(f"CP connection added to proxy: {chargepoint_name}")

    async def _remove_chargepoint_connection(self, chargepoint_uuid: str):
        logger.info(f"Removing CP connection: {chargepoint_uuid}")
        chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
        chargepoint_name = chargepoint.name

        logger.debug(f"Removing CP connection: {chargepoint_name}")
        if chargepoint_uuid in self.chargepoint_connections:
            websocket = self.chargepoint_connections.pop(chargepoint_uuid)
            await websocket.close()
            await update_chargepoint_connection_status_async(chargepoint_uuid, 'Disconnected')
            logger.debug(f"Removed CP connection for CP {chargepoint_name}")

            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                event_type='Connection',
                action="Chargepoint Disconnected",
                from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                to_entity={"name": "System", "type": "System"},
                message=f"CP {chargepoint_name} disconnected",
                level='INFO'
            )
        else:
            logger.warning(f"No existing connection found for CP {chargepoint_name}")
        logger.info(f"CP connection removed: {chargepoint_uuid}")

    async def _add_csms_connection(self, chargepoint_uuid: str, authorization: Authorization, websocket: websockets.WebSocketClientProtocol):
        chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
        chargepoint_name = chargepoint.name
        csms_name = authorization.csms_name

        logger.info(f"Adding CSMS connection {csms_name} to proxy for CP {chargepoint_name}")

        if authorization.uuid in self.closed_authorizations:
            await websocket.close()
            logger.warning(f"Rejected connection for closed authorization: {csms_name} for CP {chargepoint_name}")
            return

        if chargepoint_uuid not in self.csms_connections:
            self.csms_connections[chargepoint_uuid] = []
            logger.debug(f"Created new CSMS connections list for CP {chargepoint_name}")

        self.csms_connections[chargepoint_uuid].append((websocket, authorization))
        await update_authorization_connection_status_async(authorization.uuid, 'Connected')
        
        primary_status = "(P)" if authorization.is_primary else "(S)"
        logger.info(f"Added {primary_status} CSMS connection {csms_name} to proxy for CP {chargepoint_name}. Total CSMS connections for this chargepoint: {len(self.csms_connections[chargepoint_uuid])}")

        await create_log_entry_async(
            chargepoint_uuid=chargepoint_uuid,
            authorization=authorization,
            event_type='Connection',
            action="CSMS Connected",
            from_entity={"name": csms_name, "type": "CSMS", "uuid": str(authorization.uuid)},
            to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
            message=f"CSMS {csms_name} {primary_status} connected to CP {chargepoint_name}",
            level='INFO'
        )

    async def _remove_csms_connection(self, chargepoint_uuid: str, authorization_uuid: str):
        logger.info(f"Removing CSMS connection: {authorization_uuid} for CP {chargepoint_uuid}")
        chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
        chargepoint_name = chargepoint.name
        authorization = await sync_to_async(Authorization.objects.get)(uuid=authorization_uuid)
        csms_name = authorization.csms_name

        logger.debug(f"Removing CSMS connection: {csms_name} for CP {chargepoint_name}")
        if chargepoint_uuid in self.csms_connections:
            connections = self.csms_connections[chargepoint_uuid]
            self.csms_connections[chargepoint_uuid] = [
                (ws, auth) for ws, auth in connections if auth.uuid != authorization_uuid
            ]
            await update_authorization_connection_status_async(authorization_uuid, 'Disconnected')
            logger.debug(f"Removed specific CSMS connection for authorization: {csms_name}, remaining connections for CP {chargepoint_name}: {len(self.csms_connections[chargepoint_uuid])}")
            if not self.csms_connections[chargepoint_uuid]:
                del self.csms_connections[chargepoint_uuid]
            self.disconnected_csms.add((chargepoint_uuid, authorization_uuid))
            logger.debug(f"Added to disconnected CSMS set: {csms_name} for CP {chargepoint_name}")

            primary_status = "(P)" if authorization.is_primary else "(S)"
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=authorization,
                event_type='Connection',
                action="CSMS Disconnected",
                from_entity={"name": csms_name, "type": "CSMS", "uuid": str(authorization_uuid)},
                to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=f"CSMS {csms_name} {primary_status} disconnected from CP {chargepoint_name}",
                level='INFO'
            )
        else:
            logger.warning(f"No existing CSMS connections found for CP {chargepoint_name}")
        logger.info(f"CSMS connection removed: {authorization_uuid} for CP {chargepoint_uuid}")

    async def _connect_to_csms(self, chargepoint_uuid: str, authorization: Authorization) -> bool:
        chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
        chargepoint_name = chargepoint.name
        csms_name = authorization.csms_name

        logger.info(f"Initiating CSMS connection to CSMS {csms_name} for CP {chargepoint_name}")

        if chargepoint_uuid in self.csms_connections:
            if any(auth.uuid == authorization.uuid for _, auth in self.csms_connections[chargepoint_uuid]):
                logger.info(f"CSMS {csms_name} for CP {chargepoint_name} is already connected. Skipping connection attempt.")
                return True

        if chargepoint_uuid not in self.connection_attempts:
            self.connection_attempts[chargepoint_uuid] = set()
        if authorization.uuid in self.connection_attempts[chargepoint_uuid]:
            logger.info(f"Connection attempt to CSMS {csms_name} for CP {chargepoint_name} is already in progress. Skipping new attempt.")
            return False

        self.connection_attempts[chargepoint_uuid].add(authorization.uuid)
        logger.debug(f"Added connection attempt for CSMS {csms_name} to tracking set.")

        csms_url = authorization.connect_url.rstrip('/')
        cp_id = authorization.cp_id
        websocket_url = f"{csms_url}/{cp_id}"

        try:
            logger.debug(f"Attempting WebSocket connection to {websocket_url} for CSMS {csms_name}")
            ws = await websockets.connect(websocket_url, subprotocols=['ocpp1.6'])
            logger.info(f"WebSocket connection established to CSMS {csms_name} for CP {chargepoint_name} at {websocket_url}")
            
            await self._add_csms_connection(chargepoint_uuid, authorization, ws)
            
            asyncio.create_task(self._handle_csms_messages(ws, authorization.uuid, chargepoint_uuid))
            logger.debug(f"Created task to handle messages for CSMS {csms_name}")
            
            self.connection_attempts[chargepoint_uuid].remove(authorization.uuid)
            logger.debug(f"Removed connection attempt for CSMS {csms_name} from tracking set.")
            
            success = True
        except Exception as e:
            logger.error(f"Failed to connect to CSMS {csms_name} at {websocket_url} for CP {chargepoint_name}: {str(e)}")
            if (chargepoint_uuid, authorization.uuid) not in self.disconnected_csms:
                self.disconnected_csms.add((chargepoint_uuid, authorization.uuid))
                logger.info(f"Added CSMS {csms_name} for CP {chargepoint_name} to disconnected CSMS set.")
            
            self.connection_attempts[chargepoint_uuid].remove(authorization.uuid)
            logger.debug(f"Removed failed connection attempt for CSMS {csms_name} from tracking set.")
            
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=authorization,
                event_type='ConnectionError',
                action="CSMS Connection Failed",
                from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                to_entity={"name": csms_name, "type": "CSMS", "uuid": str(authorization.uuid)},
                message=f"Failed to connect to CSMS {csms_name} for CP {chargepoint_name}",
                level='ERROR',
                raw_message=f"Error: {str(e)}"
            )
            success = False
        
        logger.info(f"CSMS connection {'established' if success else 'failed'} to CSMS {csms_name} for CP {chargepoint_name}")
        return success

    async def send_to_csms(self, csms_uuid: str, message: str):
        for chargepoint_uuid, connections in self.csms_connections.items():
            for websocket, authorization in connections:
                if authorization.uuid == csms_uuid:
                    primary_status = "(P)" if authorization.is_primary else "(S)"
                    chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
                    chargepoint_name = chargepoint.name
                    logger.info(f"Sending message to CSMS {authorization.csms_name} {primary_status} from CP {chargepoint_name}. Message: {message}")
                    try:
                        parsed_message = json.loads(message)
                        message_with_str_uuids = json.dumps(parsed_message, default=str)
                        await websocket.send(message_with_str_uuids)
                        
                        await create_log_entry_async(
                            chargepoint_uuid=chargepoint_uuid,
                            authorization=authorization,
                            event_type='MessageSent',
                            action="Message Sent to CSMS",
                            from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                            to_entity={"name": authorization.csms_name, "type": "CSMS", "uuid": str(authorization.uuid)},
                            message=f"Message sent to CSMS {authorization.csms_name} {primary_status}",
                            raw_message=message,
                            level='INFO')
                    except Exception as e:
                        error_message = f"Failed to send message to CSMS {authorization.csms_name} {primary_status}: {str(e)}"
                        logger.error(error_message)
                        await create_log_entry_async(
                            chargepoint_uuid=chargepoint_uuid,
                            authorization=authorization,
                            event_type='MessageError',
                            action="Failed to Send Message to CSMS",
                            from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                            to_entity={"name": authorization.csms_name, "type": "CSMS", "uuid": str(authorization.uuid)},
                            message=error_message,
                            raw_message=message,
                            level='ERROR'
                        )
                        await self._remove_csms_connection(chargepoint_uuid, authorization.uuid)
                    return
        logger.warning(f"No CSMS connection for CSMS UUID: {csms_uuid}, message not sent")

    async def send_to_chargepoint(self, chargepoint_uuid: str, message: str, csms_uuid: str):
        websocket = self.chargepoint_connections.get(chargepoint_uuid)

        if websocket is None:
            logger.warning(f"No CP connection for {chargepoint_uuid}, message not sent: {message}")
            return

        try:
            authorization = await sync_to_async(Authorization.objects.get)(uuid=csms_uuid)
            if not authorization.is_primary:
                logger.warning(f"Not sending message from secondary CSMS {authorization.csms_name} to CP {chargepoint_uuid}: {message}")
                return
            
            chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
            chargepoint_name = chargepoint.name
            logger.info(f"Sending message to CP {chargepoint_name} from CSMS {authorization.csms_name}. Message: {message}")
            
            # Convert message to JSON with string UUIDs
            parsed_message = json.loads(message)
            message_with_str_uuids = json.dumps(parsed_message, default=str)
            await websocket.send(message_with_str_uuids)

            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=authorization,
                event_type='MessageSent',
                action="Message Sent to Chargepoint",
                from_entity={"name": authorization.csms_name, "type": "CSMS", "uuid": str(csms_uuid)},
                to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=f"Message sent to CP {chargepoint_name} from CSMS {authorization.csms_name}",
                raw_message=message,
                level='INFO'
            )
        except websockets.exceptions.ConnectionClosed as e:
            error_message = f"WebSocket connection closed for CP {chargepoint_name}: {str(e)}"
            logger.error(error_message)
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=authorization,
                event_type='ConnectionError',
                action="WebSocket Connection Closed",
                from_entity={"name": authorization.csms_name, "type": "CSMS", "uuid": str(csms_uuid)},
                to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=error_message,
                level='ERROR',
                raw_message=str(e)
            )
            await self._remove_chargepoint_connection(chargepoint_uuid)
        except Exception as e:
            error_message = f"Failed to send message to CP {chargepoint_name}: {str(e)}"
            logger.error(error_message)
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=authorization,
                event_type='MessageError',
                action="Failed to Send Message to Chargepoint",
                from_entity={"name": authorization.csms_name, "type": "CSMS", "uuid": str(csms_uuid)},
                to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=error_message,
                raw_message=message,
                level='ERROR'
            )

    async def _handle_csms_messages(self, csms_ws: websockets.WebSocketClientProtocol, csms_uuid: str, chargepoint_uuid: str):
        try:
            authorization = await sync_to_async(Authorization.objects.get)(uuid=csms_uuid)
            csms_name = authorization.csms_name
            primary_status = "(P)" if authorization.is_primary else "(S)"
            chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
            chargepoint_name = chargepoint.name
            async for message in csms_ws:
                logger.info(f"Received message from CSMS {csms_name} {primary_status} for CP {chargepoint_name}. Message: {message}")
                await create_log_entry_async(
                    chargepoint_uuid=chargepoint_uuid,
                    authorization=authorization,
                    event_type='MessageReceived',
                    action="Message Received from CSMS",
                    from_entity={"name": csms_name, "type": "CSMS", "uuid": str(csms_uuid)},
                    to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                    message=f"Message received from CSMS {csms_name} {primary_status}",
                    raw_message=message,
                    level='INFO'
                )
                await self.message_processor.process_message_from_csms(chargepoint_uuid, message, csms_uuid)
        except Exception as e:
            error_message = f"Error handling messages from CSMS {csms_name}: {str(e)}"
            logger.error(error_message)
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=authorization,
                event_type='MessageError',
                action="Error Handling CSMS Messages",
                from_entity={"name": csms_name, "type": "CSMS", "uuid": str(csms_uuid)},
                to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=error_message,
                level='ERROR',
                raw_message=str(e)
            )
            await self._remove_csms_connection(chargepoint_uuid, csms_uuid)


    async def close_authorization_connection(self, authorization_uuid: str):
        logger.info(f"Closing authorization connection: {authorization_uuid}")

        try:
            authorization = await sync_to_async(Authorization.objects.get)(uuid=authorization_uuid)
            chargepoint_uuid = authorization.chargepoint.uuid
            chargepoint_name = authorization.chargepoint.name
            csms_name = authorization.csms_name

            self.closed_authorizations.add(authorization_uuid)
            await self._remove_csms_connection(chargepoint_uuid, authorization_uuid)

            logger.info(f"Successfully closed authorization: {csms_name}")
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=authorization,
                event_type='Connection',
                action="Authorization Connection Closed",
                from_entity={"name": csms_name, "type": "CSMS", "uuid": str(authorization_uuid)},
                to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=f"Authorization connection closed for CSMS {csms_name}",
                level='INFO'
            )
            return {'status': 'success'}
        except Exception as e:
            error_message = f"Error handling request to close authorization: {str(e)}"
            logger.error(error_message)
            await create_log_entry_async(
                event_type='Error',
                action="Failed to Close Authorization Connection",
                from_entity={"name": "System", "type": "System"},
                message=error_message,
                level='ERROR',
                raw_message=str(e)
            )
            return {'error': 'Internal server error'}

    async def _process_message_from_chargepoint(self, chargepoint_uuid: str, message: str):
        try:
            chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
            chargepoint_name = chargepoint.name

            logger.info(f"Received message from CP {chargepoint_name}. Message: {message}")
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                event_type='MessageReceived',
                action="Message Received from Chargepoint",
                from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                to_entity={"name": "Proxy", "type": "System"},
                message=f"Message received from CP {chargepoint_name}",
                raw_message=message,
                level='INFO'
            )
            await self.message_processor.process_message_from_chargepoint(chargepoint_uuid, message)

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received from CP {chargepoint_uuid}: {message}")
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                event_type='Error',
                action="Invalid JSON received",
                from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                to_entity={"name": "System", "type": "System"},
                message=f"Invalid JSON received from CP {chargepoint_name}",
                raw_message=message,
                level='ERROR'
            )
        except Exception as e:
            logger.error(f"Error processing message from CP {chargepoint_uuid}: {str(e)}")
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                event_type='Error',
                action="Error processing message",
                from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                to_entity={"name": "System", "type": "System"},
                message=f"Error processing message from CP {chargepoint_name}: {str(e)}",
                raw_message=message,
                level='ERROR'
            )

    async def _exception_handler(self, chargepoint_ws: websockets.WebSocketClientProtocol, exception: Exception, charger_uuid: str, access_token: str):
        exception_handlers = {
            websockets.exceptions.InvalidStatusCode: (1001, "Unable to connect to CSMS"),
            websockets.exceptions.InvalidMessage: (1002, "Invalid message received"),
            websockets.exceptions.ConnectionClosedError: (None, "Connection closed unexpectedly"),
            websockets.exceptions.ConnectionClosedOK: (None, "Connection closed gracefully"),
            asyncio.TimeoutError: (1008, "Connection timed out"),
            OSError: (1001, "Internal server error")
        }

        close_code, reason = exception_handlers.get(type(exception), (1011, "Unexpected error occurred"))

        if close_code:
            await chargepoint_ws.close(code=close_code, reason=reason)
        else:
            if isinstance(exception, websockets.exceptions.ConnectionClosedError):
                logger.warning(reason)
            elif isinstance(exception, websockets.exceptions.ConnectionClosedOK):
                logger.info(reason)
            else:
                logger.error(reason)

        if type(exception) not in exception_handlers:
            logger.exception(f"Unexpected error: {str(exception)}")
            await chargepoint_ws.close(code=1011, reason="Unexpected error occurred")

        try:
            if 'access_token' in locals():
                chargepoint_details = await self.get_chargepoint_details(charger_uuid, access_token)
                chargepoint_uuid = chargepoint_details.get('uuid', charger_uuid)
                await self._remove_chargepoint_connection(chargepoint_uuid)
                
                await create_log_entry_async(
                    chargepoint_uuid=chargepoint_uuid,
                    event_type='ConnectionError',
                    action="Connection Error Handled",
                    from_entity={"name": charger_uuid, "type": "Chargepoint", "uuid": charger_uuid},
                    to_entity={"name": "System", "type": "System"},
                    message=reason,
                    level='ERROR',
                    raw_message=f"Exception: {str(exception)}"
                )
        except Exception as e:
            logger.error(f"Error in exception handler: {str(e)}", exc_info=True)

    async def _periodic_reconnect(self):
        while True:
            await asyncio.sleep(RECONNECT_INTERVAL)
            if self.disconnected_csms:
                logger.info(f"Attempting to reconnect {len(self.disconnected_csms)} disconnected CSMS connections.")
                for chargepoint_uuid, authorization_uuid in list(self.disconnected_csms):
                    await self._attempt_reconnect(chargepoint_uuid, authorization_uuid)

    async def _attempt_reconnect(self, chargepoint_uuid: str, authorization_uuid: str):
        try:
            authorization = await sync_to_async(Authorization.objects.get)(uuid=authorization_uuid)
            success = await self._connect_to_csms(chargepoint_uuid, authorization)
            if success and (chargepoint_uuid, authorization_uuid) in self.disconnected_csms:
                chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
                chargepoint_name = chargepoint.name
                csms_name = authorization.csms_name
                logger.info(f"Successfully reconnected to CSMS {csms_name} for CP {chargepoint_name}")
                self.disconnected_csms.remove((chargepoint_uuid, authorization_uuid))
                
                await create_log_entry_async(
                    chargepoint_uuid=chargepoint_uuid,
                    authorization=authorization,
                    event_type='Connection',
                    action="CSMS Reconnected",
                    from_entity={"name": csms_name, "type": "CSMS", "uuid": str(authorization_uuid)},
                    to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                    message=f"Successfully reconnected to CSMS {csms_name} for CP {chargepoint_name}",
                    level='INFO'
                )
        except Exception as e:
            logger.error(f"Failed to reconnect to CSMS {authorization_uuid} for CP {chargepoint_uuid}: {str(e)}")
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=authorization,
                event_type='ConnectionError',
                action="CSMS Reconnection Failed",
                from_entity={"name": authorization.csms_name, "type": "CSMS", "uuid": str(authorization_uuid)},
                to_entity={"name": chargepoint.name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=f"Failed to reconnect to CSMS {authorization.csms_name} for CP {chargepoint.name}",
                level='ERROR',
                raw_message=str(e)
            )
            await asyncio.sleep(RECONNECT_INTERVAL)

    async def close_chargepoint_connections(self, chargepoint_uuid: str):
        logger.info(f"Starting process to close all connections for CP {chargepoint_uuid}")
        try:
            chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
            chargepoint_name = chargepoint.name

            logger.info(f"Closing connections for CP {chargepoint_name}")

            # Close CP connection
            if chargepoint_uuid in self.chargepoint_connections:
                logger.info(f"Removing CP connection for {chargepoint_name}")
                await self._remove_chargepoint_connection(chargepoint_uuid)
                logger.info(f"Successfully removed CP connection for {chargepoint_name}")
            else:
                logger.info(f"No active CP connection found for {chargepoint_name}")
            
            # Close CSMS connections
            if chargepoint_uuid in self.csms_connections:
                csms_connections = self.csms_connections[chargepoint_uuid]
                logger.info(f"Found {len(csms_connections)} CSMS connections for {chargepoint_name}")
                for websocket, authorization in csms_connections:
                    logger.info(f"Removing CSMS connection for authorization {authorization.uuid} (CSMS {authorization.csms_name})")
                    await self._remove_csms_connection(chargepoint_uuid, authorization.uuid)
                    logger.info(f"Successfully removed CSMS connection for authorization {authorization.uuid}")
                
                if chargepoint_uuid in self.csms_connections:
                    del self.csms_connections[chargepoint_uuid]
                    logger.info(f"Cleared all CSMS connections for {chargepoint_name}")
            else:
                logger.info(f"No CSMS connections found for {chargepoint_name}")
            
            logger.info(f"Successfully closed all connections for CP {chargepoint_name}")

            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                event_type='Connection',
                action="All Connections Closed",
                from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                to_entity={"name": "System", "type": "System"},
                message=f"Closed all connections for CP {chargepoint_name}",
                level='INFO'
            )
        except Exception as e:
            logger.error(f"Error while closing connections for CP {chargepoint_uuid}: {str(e)}", exc_info=True)
            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                event_type='Error',
                action="Close Connections Failed",
                from_entity={"name": "System", "type": "System"},
                to_entity={"name": "Chargepoint", "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=f"Failed to close connections for CP {chargepoint_uuid}: {str(e)}",
                level='ERROR'
            )
        finally:
            logger.info(f"Finished process of closing connections for CP {chargepoint_uuid}")

    async def reset_chargepoint_connections(self, chargepoint_uuid: str):
        if chargepoint_uuid not in self.connection_locks:
            self.connection_locks[chargepoint_uuid] = asyncio.Lock()
        
        async with self.connection_locks[chargepoint_uuid]:
            logger.info(f"Starting reset process for CP {chargepoint_uuid}")
            try:
                logger.debug(f"Current state before reset: {self.get_connection_state(chargepoint_uuid)}")
                
                # Step 1: Close existing connections
                logger.info(f"Closing existing CP connection for {chargepoint_uuid}")
                await self._remove_chargepoint_connection(chargepoint_uuid)
                
                if chargepoint_uuid in self.csms_connections:
                    logger.info(f"Closing existing CSMS connections for CP {chargepoint_uuid}")
                    csms_connections = self.csms_connections[chargepoint_uuid].copy()
                    for websocket, authorization in csms_connections:
                        logger.debug(f"Closing CSMS connection for authorization {authorization.uuid}")
                        await self._remove_csms_connection(chargepoint_uuid, authorization.uuid)
                    self.csms_connections.pop(chargepoint_uuid, None)
                else:
                    logger.info(f"No existing CSMS connections found for CP {chargepoint_uuid}")

                # Step 2: Wait for a short period to ensure all connections are fully closed
                await asyncio.sleep(2)  # Wait for 2 seconds

                # Step 3: Re-establish connections
                logger.info(f"Re-establishing CP connection for {chargepoint_uuid}")
                await self._add_chargepoint_connection(chargepoint_uuid, None)
                
                logger.info(f"Fetching authorizations for CP {chargepoint_uuid}")
                authorizations = await get_authorizations_by_chargepoint_uuid_async(chargepoint_uuid)
                
                logger.info(f"Re-establishing CSMS connections for CP {chargepoint_uuid}")
                for authorization in authorizations:
                    logger.debug(f"Connecting to CSMS for authorization {authorization.uuid}")
                    await self._connect_to_csms(chargepoint_uuid, authorization)

                logger.info(f"Successfully reset connections for CP {chargepoint_uuid}")
                logger.debug(f"New state after reset: {self.get_connection_state(chargepoint_uuid)}")
                return True
            except Exception as e:
                logger.error(f"Error resetting connections for CP {chargepoint_uuid}: {str(e)}", exc_info=True)
                return False

    def get_connection_state(self, chargepoint_uuid: str) -> dict:
        state = {
            "chargepoint_connected": chargepoint_uuid in self.chargepoint_connections,
            "csms_connections": []
        }
        if chargepoint_uuid in self.csms_connections:
            for _, auth in self.csms_connections[chargepoint_uuid]:
                state["csms_connections"].append({
                    "uuid": str(auth.uuid),
                    "csms_name": auth.csms_name,
                    "is_primary": auth.is_primary,
                    "connection_status": auth.connection_status
                })
        return state