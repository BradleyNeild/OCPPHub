import logging
import json
import requests
from requests.exceptions import ConnectionError
from datetime import datetime
from asgiref.sync import sync_to_async
import socket
from email.mime.text import MIMEText
from dotenv import load_dotenv
import os
import time
import asyncio
from logging_config import configure_logging
import websockets

# Set up logging
logger, error_logger = configure_logging()

# Load environment variables early
load_dotenv()

# Import models after setting up Django
from ocpp_hub_app.models import Authorization, ChargePoint, LogEntry

WEB_SERVICE_URL = os.getenv('WEB_SERVICE_URL')
OAUTH2_PROXY_CLIENT_ID = os.getenv('OAUTH2_PROXY_CLIENT_ID')
OAUTH2_PROXY_CLIENT_SECRET = os.getenv('OAUTH2_PROXY_CLIENT_SECRET')
OAUTH2_TOKEN_URL = os.getenv('OAUTH2_TOKEN_URL')

def get_access_token(retries=5, delay=2):
    """
    Obtains an access token from the OAuth2 server.
    
    Parameters:
        retries (int): Number of retries in case of failure.
        delay (int): Delay between retries.
    
    Returns:
        str: Access token.
    """
    data = {
        'grant_type': 'client_credentials',
        'client_id': OAUTH2_PROXY_CLIENT_ID,
        'client_secret': OAUTH2_PROXY_CLIENT_SECRET,
        'scope': "proxy_access",
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    
    for attempt in range(retries):
        try:
            logger.info(f"Requesting access token with data (attempt {attempt + 1}/{retries})")
            response = requests.post(OAUTH2_TOKEN_URL, data=data, headers=headers)
            response.raise_for_status()
            return response.json().get('access_token')
        except ConnectionError as e:
            logger.error(f"Failed to obtain access token: {e}. Retrying in {delay} seconds...")
            time.sleep(delay)
            delay = min(delay * 2, 60)  # Exponential backoff with a maximum delay
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
            break

    raise ConnectionError(f"Failed to obtain access token after {retries} attempts")

@sync_to_async
def create_log_entry(chargepoint=None, authorization=None, event_type="Information", message="", raw_message=""):
    """
    Creates a log entry in the database.
    
    Parameters:
        chargepoint (str): UUID of the chargepoint.
        authorization (Authorization): Authorization object.
        event_type (str): Type of event.
        message (str): Log message.
        raw_message (str): Raw message.
    """
    if chargepoint:
        try:
            chargepoint_instance = ChargePoint.objects.get(uuid=chargepoint)
        except ChargePoint.DoesNotExist:
            error_logger.error(f"ChargePoint with UUID {chargepoint} does not exist.")
            return
    else:
        chargepoint_instance = None
    
    log_entry = LogEntry(chargepoint=chargepoint_instance, authorization=authorization, event_type=event_type, message=message, raw_message=raw_message)
    log_entry.save()
    logger.info(f"Log entry created: {message} | Raw message: {raw_message}")

@sync_to_async
def update_authorization_connection_status_sync(authorization_uuid, connection_status):
    """
    Updates the connection status of an authorization.
    
    Parameters:
        authorization_uuid (str): UUID of the authorization.
        connection_status (str): New connection status.
    """
    authorization = Authorization.objects.get(uuid=authorization_uuid)
    authorization.connection_status = connection_status
    authorization.save()
    return authorization

@sync_to_async
def update_chargepoint_connection_status_sync(chargepoint_uuid, connection_status):
    """
    Updates the connection status of a chargepoint.
    
    Parameters:
        chargepoint_uuid (str): UUID of the chargepoint.
        connection_status (str): New connection status.
    """
    chargepoint = ChargePoint.objects.get(uuid=chargepoint_uuid)
    chargepoint.connection_status = connection_status
    chargepoint.save()
    return chargepoint

@sync_to_async
def update_chargepoint_status_sync(chargepoint_uuid, status):
    """
    Updates the status of a chargepoint.
    
    Parameters:
        chargepoint_uuid (str): UUID of the chargepoint.
        status (str): New status.
    """
    chargepoint = ChargePoint.objects.get(uuid=chargepoint_uuid)
    chargepoint.status = status
    chargepoint.save()
    return chargepoint

@sync_to_async
def get_authorizations_by_chargepoint_uuid(charger_uuid):
    """
    Gets the authorizations associated with a chargepoint UUID.
    
    Parameters:
        charger_uuid (str): UUID of the chargepoint.
    
    Returns:
        list: List of Authorization objects.
    """
    return list(Authorization.objects.filter(chargepoint__uuid=charger_uuid))

def identify_event_type(parsed_message):
    """
    Identifies the event type from a parsed message.
    
    Parameters:
        parsed_message (list): Parsed JSON message.
    
    Returns:
        str: Event type.
    """
    if not isinstance(parsed_message, list) or len(parsed_message) < 3:
        return "Information"

    message_type_id = parsed_message[0]
    action = parsed_message[2] if message_type_id == 2 else None

    event_types = {
        'BootNotification': "Boot Notification",
        'Heartbeat': "Heartbeat",
        'MeterValues': "Meter Values",
        'Authorize': "Authorize",
        'StartTransaction': "Start Transaction",
        'StopTransaction': "Stop Transaction",
        'StatusNotification': "Status Notification",
        'FirmwareStatusNotification': "Firmware Status Notification",
        'DiagnosticsStatusNotification': "Diagnostics Status Notification",
        'DataTransfer': "Data Transfer",
        3: "Call Result"
    }

    return event_types.get(action, event_types.get(message_type_id, "Information"))

def get_ip_address():
    """
    Gets the IP address of the current machine.
    
    Returns:
        str: IP address.
    """
    try:
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        return ip_address
    except Exception as e:
        error_logger.error(f"Failed to get IP address: {str(e)}")
        return "Unknown"

async def exception_handler(chargepoint_websocket, exception, charger_uuid, access_token=None):
    """
    Handles exceptions during websocket communication.
    
    Parameters:
        chargepoint_websocket (websockets.WebSocketClientProtocol): WebSocket connection.
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
        await chargepoint_websocket.close(code=close_code, reason=reason)
    else:
        if type(exception) == websockets.exceptions.ConnectionClosedError:
            logger.warning(reason)
        elif type(exception) == websockets.exceptions.ConnectionClosedOK:
            logger.info(reason)
        else:
            error_logger.error(reason)

    if type(exception) not in exception_handlers:
        error_logger.exception(f"Unexpected error: {str(exception)}")
        await chargepoint_websocket.close(code=1011, reason="Unexpected error occurred")
