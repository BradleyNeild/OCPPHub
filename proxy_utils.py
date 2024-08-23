import logging
import requests
from requests.exceptions import ConnectionError
from dotenv import load_dotenv
import socket
import time
from logging_config import configure_logging
import os 

# Set up logging
logger, error_logger = configure_logging()

# Load environment variables early
load_dotenv()

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

def identify_event_type(parsed_message):
    """
    Identifies the event type from a parsed message.

    Parameters:
        parsed_message (list): Parsed JSON message. Expected to be a list where the first element is the message type ID,
                               and the third element is the action for message type ID 2.

    Returns:
        str: Event type.
    """
    if not isinstance(parsed_message, list) or len(parsed_message) < 3:
        return "Information"

    message_type_id = parsed_message[0]
    action = parsed_message[2] if message_type_id == 2 else None

    event_types = {
        'Authorize': "Authorize",
        'BootNotification': "BootNotification",
        'CancelReservation': "CancelReservation",
        'ChangeAvailability': "ChangeAvailability",
        'ChangeConfiguration': "ChangeConfiguration",
        'ClearCache': "ClearCache",
        'ClearChargingProfile': "ClearChargingProfile",
        'DataTransfer': "DataTransfer",
        'DiagnosticsStatusNotification': "DiagnosticsStatusNotification",
        'FirmwareStatusNotification': "FirmwareStatusNotification",
        'GetCompositeSchedule': "GetCompositeSchedule",
        'GetConfiguration': "GetConfiguration",
        'GetDiagnostics': "GetDiagnostics",
        'GetLocalListVersion': "GetLocalListVersion",
        'Heartbeat': "Heartbeat",
        'MeterValues': "MeterValues",
        'RemoteStartTransaction': "RemoteStartTransaction",
        'RemoteStopTransaction': "RemoteStopTransaction",
        'ReserveNow': "ReserveNow",
        'Reset': "Reset",
        'SendLocalList': "SendLocalList",
        'SetChargingProfile': "SetChargingProfile",
        'StartTransaction': "StartTransaction",
        'StatusNotification': "StatusNotification",
        'StopTransaction': "StopTransaction",
        'TriggerMessage': "TriggerMessage",
        'UnlockConnector': "UnlockConnector",
        'UpdateFirmware': "UpdateFirmware",
        3: "CallResult",
        4: "CallError"
    }

    return event_types.get(action, event_types.get(message_type_id, "Information"))

def get_ip_address():
    """
    Gets the IP address of the current machine.

    Returns:
        str: IP address. Returns "Unknown" if unable to retrieve the IP address.
    """
    try:
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        return ip_address
    except Exception as e:
        error_logger.error(f"Failed to get IP address: {str(e)}")
        return "Unknown"