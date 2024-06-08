import asyncio
import websockets
import logging
import json
from dotenv import load_dotenv
from asgiref.sync import sync_to_async
import aioredis
import aio_pika
import django
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ocpp_hub_project.settings')
django.setup()
from ocpp_hub_app.models import Authorization
from proxy_utils import (
    get_access_token, update_authorization_connection_status_sync,
    update_chargepoint_connection_status_sync, get_authorizations_by_chargepoint_uuid,
    create_log_entry
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
        """Initializes the connection manager, reconnecting to Redis and RabbitMQ."""
        self.message_processor = MessageProcessor(self)
        await self.reconnect_redis()
        await self.initialize_rabbitmq()

    async def reconnect_redis(self):
        """Reconnects to the Redis server."""
        try:
            self.redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
            logger.info(f"Connected to Redis at {REDIS_URL}")
        except Exception as e:
            error_logger.error(f"Failed to reconnect to Redis: {str(e)}")
            await asyncio.sleep(5)
            await self.reconnect_redis()

    async def initialize_rabbitmq(self):
        """Initializes the connection to RabbitMQ."""
        try:
            self.rabbitmq_connection = await aio_pika.connect_robust(RABBITMQ_URL)
            self.channel = await self.rabbitmq_connection.channel()
            logger.info(f"Connected to RabbitMQ at {RABBITMQ_URL}")
        except Exception as e:
            error_logger.error(f"Failed to connect to RabbitMQ: {str(e)}")
            await asyncio.sleep(5)
            await self.initialize_rabbitmq()

    async def add_chargepoint_connection(self, chargepoint_uuid: str, websocket: websockets.WebSocketClientProtocol):
        """Adds a chargepoint connection."""
        logger.info(f"Adding chargepoint connection: {chargepoint_uuid}")
        if not self.redis:
            await self.reconnect_redis()
        self.chargepoint_connections[chargepoint_uuid] = websocket
        await update_chargepoint_connection_status_sync(chargepoint_uuid, 'Connected')
        logger.info(f"Added connection for chargepoint: {chargepoint_uuid}")

        # Connect to CSMS for this chargepoint if not already connected
        authorizations = await get_authorizations_by_chargepoint_uuid(chargepoint_uuid)
        for authorization in authorizations:
            await self.connect_to_csms(chargepoint_uuid, authorization)

    async def remove_chargepoint_connection(self, chargepoint_uuid: str):
        """Removes a chargepoint connection."""
        logger.info(f"Removing chargepoint connection: {chargepoint_uuid}")
        if chargepoint_uuid in self.chargepoint_connections:
            websocket = self.chargepoint_connections.pop(chargepoint_uuid)
            await websocket.close()
            await update_chargepoint_connection_status_sync(chargepoint_uuid, 'Disconnected')
            logger.info(f"Removed chargepoint connection for chargepoint: {chargepoint_uuid}")

    async def add_csms_connection(self, chargepoint_uuid: str, authorization: Authorization, websocket: websockets.WebSocketClientProtocol):
        """Adds a CSMS connection."""
        logger.info(f"Adding CSMS connection: {chargepoint_uuid}, {authorization.uuid}")
        if authorization.uuid in self.closed_authorizations:
            await websocket.close()
            logger.info(f"Rejected connection for closed authorization: {authorization.uuid}")
            return
        if chargepoint_uuid not in self.csms_connections:
            self.csms_connections[chargepoint_uuid] = []
        self.csms_connections[chargepoint_uuid].append((websocket, authorization))
        await update_authorization_connection_status_sync(authorization.uuid, 'Connected')
        logger.info(f"Added CSMS connection for chargepoint: {chargepoint_uuid}, total CSMS connections: {len(self.csms_connections[chargepoint_uuid])}")

    async def remove_csms_connection(self, chargepoint_uuid: str, authorization_uuid: str):
        """Removes a CSMS connection."""
        logger.info(f"Removing CSMS connection: {chargepoint_uuid}, {authorization_uuid}")
        if chargepoint_uuid in self.csms_connections:
            connections = self.csms_connections[chargepoint_uuid]
            self.csms_connections[chargepoint_uuid] = [
                (ws, auth) for ws, auth in connections if auth.uuid != authorization_uuid
            ]
            await update_authorization_connection_status_sync(authorization_uuid, 'Disconnected')
            logger.info(f"Removed specific CSMS connection for chargepoint: {chargepoint_uuid}, remaining connections: {len(self.csms_connections[chargepoint_uuid])}")
            if not self.csms_connections[chargepoint_uuid]:
                del self.csms_connections[chargepoint_uuid]

    async def connect_to_csms(self, chargepoint_uuid: str, authorization: Authorization):
        """Connects to CSMS for a specific chargepoint and authorization."""
        logger.info(f"Connecting to CSMS: {chargepoint_uuid}, {authorization.uuid}")
        csms_url = authorization.connect_url.rstrip('/')
        cp_id = authorization.cp_id
        websocket_url = f"{csms_url}/{cp_id}"
        if chargepoint_uuid in self.csms_connections:
            if any(auth.uuid == authorization.uuid for _, auth in self.csms_connections[chargepoint_uuid]):
                logger.info(f"CSMS {authorization.uuid} for chargepoint {chargepoint_uuid} is already connected.")
                return

        try:
            ws = await websockets.connect(websocket_url, subprotocols=['ocpp1.6'])
            await self.add_csms_connection(chargepoint_uuid, authorization, ws)
            logger.info(f"Connected to CSMS {authorization.uuid} at {websocket_url} for ChargePoint {chargepoint_uuid}")

            asyncio.create_task(self.handle_csms_messages(ws, authorization.uuid, chargepoint_uuid))
        except Exception as e:
            logger.error(f"Failed to connect to CSMS {authorization.uuid} at {websocket_url}: {str(e)}")
            await self.remove_csms_connection(chargepoint_uuid, authorization.uuid)

    async def send_to_csms(self, chargepoint_uuid: str, message: str):
        """Sends a message to the CSMS."""
        logger.info(f"-> [CSMS] {chargepoint_uuid} {message}")
        if chargepoint_uuid in self.csms_connections and self.csms_connections[chargepoint_uuid]:
            for websocket, authorization in self.csms_connections[chargepoint_uuid]:
                try:
                    await websocket.send(message)
                    await create_log_entry(chargepoint=chargepoint_uuid, authorization=authorization, event_type="Information", message="Message sent to CSMS", raw_message=message)
                except Exception as e:
                    error_logger.error(f"Failed to send message to CSMS {authorization.uuid}: {str(e)}")
        else:
            logger.warning(f"No CSMS connection for chargepoint: {chargepoint_uuid}, message not sent")

    async def send_to_chargepoint(self, chargepoint_uuid: str, message: str, csms_uuid: str):
        """Sends a message to the chargepoint."""
        logger.info(f"-> [CP] {chargepoint_uuid} {message}")
        websocket = self.chargepoint_connections.get(chargepoint_uuid)
        
        if websocket is None:
            logger.warning(f"No chargepoint connection for {chargepoint_uuid}, message not sent: {message}")
            return
        
        try:
            await websocket.send(message)
            
            for _, authorization in self.csms_connections.get(chargepoint_uuid, []):
                if csms_uuid == authorization.uuid:
                    await create_log_entry(chargepoint=chargepoint_uuid, authorization=authorization, event_type="Information", message="Message received from CSMS", raw_message=message)
        
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"WebSocket connection closed for chargepoint {chargepoint_uuid}: {str(e)}")
            await self.remove_chargepoint_connection(chargepoint_uuid)
        
        except Exception as e:
            error_logger.error(f"Failed to send message to chargepoint {chargepoint_uuid}: {str(e)}")

    async def handle_csms_messages(self, csms_ws: websockets.WebSocketClientProtocol, csms_uuid: str, chargepoint_uuid: str):
        """Handles incoming messages from the CSMS."""
        logger.info(f"Handling CSMS messages for: {csms_uuid}, {chargepoint_uuid}")
        try:
            async for message in csms_ws:
                logger.info(f"<- [CSMS] {chargepoint_uuid} {message}")
                await self.message_processor.process_message_from_csms(chargepoint_uuid, message, csms_uuid)
        except Exception as e:
            error_logger.error(f"Error handling messages from CSMS {csms_uuid}: {str(e)}")

    async def close_authorization_connection(self, authorization_uuid: str):
        """Closes an authorization connection."""
        logger.info(f"Closing authorization connection: {authorization_uuid}")

        try:
            authorization = await sync_to_async(Authorization.objects.get)(uuid=authorization_uuid)
            chargepoint_uuid = authorization.chargepoint.uuid

            self.closed_authorizations.add(authorization_uuid)
            await self.remove_csms_connection(chargepoint_uuid, authorization_uuid)

            logger.info(f"Successfully closed authorization: {authorization_uuid}")
            return {'status': 'success'}
        except Exception as e:
            logger.error(f"Error handling request to close authorization: {str(e)}")
            return {'error': 'Internal server error'}

    async def process_message_from_chargepoint(self, chargepoint_uuid, message):
        """Processes a message from a chargepoint."""
        logger.info(f"<- [CP] {chargepoint_uuid} {message}")
        await self.message_processor.process_message_from_chargepoint(chargepoint_uuid, message)
