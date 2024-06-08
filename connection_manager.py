import asyncio
import websockets
import logging
import json
from dotenv import load_dotenv
from asgiref.sync import sync_to_async
import aioredis
import aio_pika
import django
import requests
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ocpp_hub_project.settings')
django.setup()
from ocpp_hub_app.models import Authorization
from proxy_utils import get_access_token
from database_utils import (
    update_authorization_connection_status_async,
    update_chargepoint_connection_status_async,
    get_authorizations_by_chargepoint_uuid_async,
    create_log_entry_async
)
from message_processor import MessageProcessor

# Load environment variables early
load_dotenv()

from logging_config import configure_logging
logger, error_logger = configure_logging()

WEB_SERVICE_URL = os.getenv('WEB_SERVICE_URL')
OAUTH2_PROXY_CLIENT_ID = os.getenv('OAUTH2_PROXY_CLIENT_ID')
OAUTH2_PROXY_CLIENT_SECRET = os.getenv('OAUTH2_PROXY_CLIENT_SECRET')
OAUTH2_TOKEN_URL = os.getenv('OAUTH2_TOKEN_URL')
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost')
RABBITMQ_URL = os.getenv('RABBITMQ_URL', 'amqp://guest:guest@localhost/')

class ConnectionManager:
    """
    Manages the connections between chargepoints and CSMS (Central System Management Software).

    Attributes:
        chargepoint_connections (dict): Active WebSocket connections to chargepoints.
        csms_connections (dict): Active WebSocket connections to CSMS.
        closed_authorizations (set): Set of closed authorization UUIDs.
        redis (aioredis.Redis): Redis client.
        rabbitmq_connection (aio_pika.Connection): RabbitMQ connection.
        channel (aio_pika.Channel): RabbitMQ channel.
        message_processor (MessageProcessor): Instance of MessageProcessor for handling messages.
    """

    def __init__(self):
        self.chargepoint_connections = {}
        self.csms_connections = {}
        self.closed_authorizations = set()
        self.redis = None
        self.rabbitmq_connection = None
        self.channel = None
        self.message_processor = None

    async def initialize(self):
        """
        Initializes the connection manager, reconnecting to Redis and RabbitMQ.
        """
        self.message_processor = MessageProcessor(self)
        await self._reconnect_redis()
        await self._initialize_rabbitmq()

    async def _reconnect_redis(self):
        """
        Reconnects to the Redis server.
        """
        while not self.redis:
            try:
                self.redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
                logger.info(f"Connected to Redis at {REDIS_URL}")
            except Exception as e:
                error_logger.error(f"Failed to reconnect to Redis: {str(e)}")
                await asyncio.sleep(5)

    async def _initialize_rabbitmq(self):
        """
        Initializes the connection to RabbitMQ.
        """
        while not self.rabbitmq_connection:
            try:
                self.rabbitmq_connection = await aio_pika.connect_robust(RABBITMQ_URL)
                self.channel = await self.rabbitmq_connection.channel()
                logger.info(f"Connected to RabbitMQ at {RABBITMQ_URL}")
            except Exception as e:
                error_logger.error(f"Failed to connect to RabbitMQ: {str(e)}")
                await asyncio.sleep(5)

    async def get_chargepoint_details(self, chargepoint_uuid: str, access_token: str):
        """
        Fetches details of a chargepoint.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
            access_token (str): Access token for authorization.

        Returns:
            dict: Chargepoint details.
        """
        details_url = f"{WEB_SERVICE_URL}api/v1/chargepoints/{chargepoint_uuid}/details/"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        response = requests.get(details_url, headers=headers)
        if response.status_code != 200:
            logger.error(f"Failed to fetch details for ChargePoint {chargepoint_uuid}: {response.content.decode()}")
            return None
        return response.json()

    async def proxy_handler(self, chargepoint_ws: websockets.WebSocketClientProtocol, path: str):
        """
        Handles incoming websocket connections from chargepoints.

        Parameters:
            chargepoint_ws (websockets.WebSocketClientProtocol): WebSocket connection.
            path (str): WebSocket path.
        """
        chargepoint_uuid = path.strip('/')
        logger.info(f"Chargepoint connected: {chargepoint_ws.remote_address}")

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
            logger.info("Chargepoint connection closed")
            await self._remove_chargepoint_connection(actual_chargepoint_uuid)

    async def _add_chargepoint_connection(self, chargepoint_uuid: str, websocket: websockets.WebSocketClientProtocol):
        """
        Adds a chargepoint connection.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
            websocket (websockets.WebSocketClientProtocol): WebSocket connection.
        """
        logger.info(f"Adding chargepoint connection: {chargepoint_uuid}")
        if not self.redis:
            await self._reconnect_redis()
        self.chargepoint_connections[chargepoint_uuid] = websocket
        await update_chargepoint_connection_status_async(chargepoint_uuid, 'Connected')
        logger.info(f"Added connection for chargepoint: {chargepoint_uuid}")

        # Connect to CSMS for this chargepoint if not already connected
        authorizations = await get_authorizations_by_chargepoint_uuid_async(chargepoint_uuid)
        for authorization in authorizations:
            await self._connect_to_csms(chargepoint_uuid, authorization)

    async def _remove_chargepoint_connection(self, chargepoint_uuid: str):
        """
        Removes a chargepoint connection.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
        """
        logger.info(f"Removing chargepoint connection: {chargepoint_uuid}")
        if chargepoint_uuid in self.chargepoint_connections:
            websocket = self.chargepoint_connections.pop(chargepoint_uuid)
            await websocket.close()
            await update_chargepoint_connection_status_async(chargepoint_uuid, 'Disconnected')
            logger.info(f"Removed chargepoint connection for chargepoint: {chargepoint_uuid}")

    async def _add_csms_connection(self, chargepoint_uuid: str, authorization: Authorization, websocket: websockets.WebSocketClientProtocol):
        """
        Adds a CSMS connection.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
            authorization (Authorization): Authorization object.
            websocket (websockets.WebSocketClientProtocol): WebSocket connection.
        """
        logger.info(f"Adding CSMS connection: {authorization.uuid} for chargepoint: {chargepoint_uuid}")
        if authorization.uuid in self.closed_authorizations:
            await websocket.close()
            logger.info(f"Rejected connection for closed authorization: {authorization.uuid}")
            return
        if chargepoint_uuid not in self.csms_connections:
            self.csms_connections[chargepoint_uuid] = []
        self.csms_connections[chargepoint_uuid].append((websocket, authorization))
        await update_authorization_connection_status_async(authorization.uuid, 'Connected')
        logger.info(f"Added CSMS connection for authorization: {authorization.uuid}, total CSMS connections for chargepoint {chargepoint_uuid}: {len(self.csms_connections[chargepoint_uuid])}")

    async def _remove_csms_connection(self, chargepoint_uuid: str, authorization_uuid: str):
        """
        Removes a CSMS connection.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
            authorization_uuid (str): UUID of the authorization.
        """
        logger.info(f"Removing CSMS connection: {authorization_uuid} for chargepoint: {chargepoint_uuid}")
        if chargepoint_uuid in self.csms_connections:
            connections = self.csms_connections[chargepoint_uuid]
            self.csms_connections[chargepoint_uuid] = [
                (ws, auth) for ws, auth in connections if auth.uuid != authorization_uuid
            ]
            await update_authorization_connection_status_async(authorization_uuid, 'Disconnected')
            logger.info(f"Removed specific CSMS connection for authorization: {authorization_uuid}, remaining connections for chargepoint {chargepoint_uuid}: {len(self.csms_connections[chargepoint_uuid])}")
            if not self.csms_connections[chargepoint_uuid]:
                del self.csms_connections[chargepoint_uuid]

    async def _connect_to_csms(self, chargepoint_uuid: str, authorization: Authorization):
        """
        Connects to CSMS for a specific chargepoint and authorization.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
            authorization (Authorization): Authorization object.
        """
        logger.info(f"Connecting to CSMS for chargepoint: {chargepoint_uuid}, authorization: {authorization.uuid}")
        csms_url = authorization.connect_url.rstrip('/')
        cp_id = authorization.cp_id
        websocket_url = f"{csms_url}/{cp_id}"
        if chargepoint_uuid in self.csms_connections:
            if any(auth.uuid == authorization.uuid for _, auth in self.csms_connections[chargepoint_uuid]):
                logger.info(f"CSMS {authorization.uuid} for chargepoint {chargepoint_uuid} is already connected.")
                return

        try:
            ws = await websockets.connect(websocket_url, subprotocols=['ocpp1.6'])
            await self._add_csms_connection(chargepoint_uuid, authorization, ws)
            logger.info(f"Connected to CSMS {authorization.uuid} at {websocket_url} for ChargePoint {chargepoint_uuid}")

            asyncio.create_task(self._handle_csms_messages(ws, authorization.uuid, chargepoint_uuid))
        except Exception as e:
            logger.error(f"Failed to connect to CSMS {authorization.uuid} at {websocket_url}: {str(e)}")
            await self._remove_csms_connection(chargepoint_uuid, authorization.uuid)

    async def send_to_csms(self, chargepoint_uuid: str, message: str):
        """
        Sends a message to the CSMS.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
            message (str): Message to send.
        """
        if chargepoint_uuid in self.csms_connections and self.csms_connections[chargepoint_uuid]:
            for websocket, authorization in self.csms_connections[chargepoint_uuid]:
                logger.info(f"-> [CSMS] {authorization.uuid} {message}")
                try:
                    await websocket.send(message)
                    await create_log_entry_async(chargepoint_uuid=chargepoint_uuid, authorization=authorization, event_type="Information", message="Message sent to CSMS", raw_message=message)
                except Exception as e:
                    error_logger.error(f"Failed to send message to CSMS {authorization.uuid}: {str(e)}")
        else:
            logger.warning(f"No CSMS connection for chargepoint: {chargepoint_uuid}, message not sent")

    async def send_to_chargepoint(self, chargepoint_uuid: str, message: str, csms_uuid: str):
        """
        Sends a message to the chargepoint.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
            message (str): Message to send.
            csms_uuid (str): UUID of the CSMS.
        """
        websocket = self.chargepoint_connections.get(chargepoint_uuid)

        if websocket is None:
            logger.warning(f"No chargepoint connection for {chargepoint_uuid}, message not sent: {message}")
            return

        logger.info(f"-> [CP] {chargepoint_uuid} {message}")
        try:
            await websocket.send(message)

            for _, authorization in self.csms_connections.get(chargepoint_uuid, []):
                if csms_uuid == authorization.uuid:
                    await create_log_entry_async(chargepoint_uuid=chargepoint_uuid, authorization=authorization, event_type="Information", message="Message received from CSMS", raw_message=message)

        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"WebSocket connection closed for chargepoint {chargepoint_uuid}: {str(e)}")
            await self._remove_chargepoint_connection(chargepoint_uuid)

        except Exception as e:
            error_logger.error(f"Failed to send message to chargepoint {chargepoint_uuid}: {str(e)}")

    async def _handle_csms_messages(self, csms_ws: websockets.WebSocketClientProtocol, csms_uuid: str, chargepoint_uuid: str):
        """
        Handles incoming messages from the CSMS.

        Parameters:
            csms_ws (websockets.WebSocketClientProtocol): WebSocket connection.
            csms_uuid (str): UUID of the CSMS.
            chargepoint_uuid (str): UUID of the chargepoint.
        """
        logger.info(f"Handling CSMS messages for authorization: {csms_uuid}, chargepoint: {chargepoint_uuid}")
        try:
            async for message in csms_ws:
                logger.info(f"<- [CSMS] {csms_uuid} {message}")
                await self.message_processor.process_message_from_csms(chargepoint_uuid, message, csms_uuid)
        except Exception as e:
            error_logger.error(f"Error handling messages from CSMS {csms_uuid}: {str(e)}")

    async def close_authorization_connection(self, authorization_uuid: str):
        """
        Closes an authorization connection.

        Parameters:
            authorization_uuid (str): UUID of the authorization.
        
        Returns:
            dict: Result of the operation.
        """
        logger.info(f"Closing authorization connection: {authorization_uuid}")

        try:
            authorization = await sync_to_async(Authorization.objects.get)(uuid=authorization_uuid)
            chargepoint_uuid = authorization.chargepoint.uuid

            self.closed_authorizations.add(authorization_uuid)
            await self._remove_csms_connection(chargepoint_uuid, authorization_uuid)

            logger.info(f"Successfully closed authorization: {authorization_uuid}")
            return {'status': 'success'}
        except Exception as e:
            logger.error(f"Error handling request to close authorization: {str(e)}")
            return {'error': 'Internal server error'}

    async def _process_message_from_chargepoint(self, chargepoint_uuid: str, message: str):
        """
        Processes a message from a chargepoint.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
            message (str): Message received from the chargepoint.
        """
        logger.info(f"<- [CP] {chargepoint_uuid} {message}")
        await self.message_processor.process_message_from_chargepoint(chargepoint_uuid, message)

    async def _exception_handler(self, chargepoint_ws: websockets.WebSocketClientProtocol, exception: Exception, charger_uuid: str, access_token: str):
        """
        Handles exceptions during websocket communication.

        Parameters:
            chargepoint_ws (websockets.WebSocketClientProtocol): WebSocket connection.
            exception (Exception): Exception to handle.
            charger_uuid (str): UUID of the chargepoint.
            access_token (str): Access token.
        """
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
                error_logger.error(reason)

        if type(exception) not in exception_handlers:
            error_logger.exception(f"Unexpected error: {str(exception)}")
            await chargepoint_ws.close(code=1011, reason="Unexpected error occurred")
