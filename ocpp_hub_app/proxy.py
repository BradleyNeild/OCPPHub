import asyncio
import websockets
import os
import logging
import json
import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)-8s - %(message)s', datefmt='%H:%M:%S')
error_logger = logging.getLogger('error_logger')
error_logger.setLevel(logging.ERROR)
handler = logging.FileHandler('error.log')
handler.setLevel(logging.ERROR)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
error_logger.addHandler(handler)

WEB_SERVICE_URL = os.getenv('WEB_SERVICE_URL')
OAUTH2_CLIENT_ID = os.getenv('OAUTH2_PROXY_CLIENT_ID')
OAUTH2_CLIENT_SECRET = os.getenv('OAUTH2_PROXY_CLIENT_SECRET')
OAUTH2_TOKEN_URL = os.getenv('OAUTH2_TOKEN_URL')

logging.info(f"Client ID: {OAUTH2_CLIENT_ID}")
logging.info(f"Client Secret: {OAUTH2_CLIENT_SECRET}")
logging.info(f"Token URL: {OAUTH2_TOKEN_URL}")

def get_access_token():
    data = {
        'grant_type': 'client_credentials',
        'client_id': OAUTH2_CLIENT_ID,
        'client_secret': OAUTH2_CLIENT_SECRET,
        'scope': "proxy_access",
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    logging.info(f"Requesting access token with data: {data}")
    response = requests.post(OAUTH2_TOKEN_URL, data=data, headers=headers)
    if response.status_code != 200:
        logging.error(f"Failed to obtain access token: {response.content.decode()}")
        response.raise_for_status()
    return response.json().get('access_token')

def update_chargepoint_status(chargepoint_id, status, access_token):
    update_url = f"{WEB_SERVICE_URL}{chargepoint_id}/status/"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    data = {'status': status}
    try:
        response = requests.patch(update_url, json=data, headers=headers)
        if response.status_code == 200:
            logger.info(f"Successfully updated status for ChargePoint {chargepoint_id} to {status}")
        else:
            logger.error(f"Failed to update status for ChargePoint {chargepoint_id}: {response.content.decode()}")
    except Exception as e:
        logger.error(f"Error updating status for ChargePoint {chargepoint_id}: {str(e)}")

def update_authorization_status(chargepoint_id, status, access_token):
    update_url = f"{WEB_SERVICE_URL}{chargepoint_id}/authorization_status/"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    data = {'status': status}
    try:
        response = requests.patch(update_url, json=data, headers=headers)
        if response.status_code == 200:
            logger.info(f"Successfully updated authorization status for ChargePoint {chargepoint_id} to {status}")
        else:
            logger.error(f"Failed to update authorization status for ChargePoint {chargepoint_id}: {response.content.decode()}")
    except Exception as e:
        logger.error(f"Error updating authorization status for ChargePoint {chargepoint_id}: {str(e)}")

async def exception_handler(chargepoint_websocket, exception, charger_id, access_token):
    if isinstance(exception, websockets.exceptions.InvalidStatusCode):
        error_logger.error(f"Error connecting to CSMS: {str(exception)}")
        await chargepoint_websocket.close(code=1001, reason="Unable to connect to CSMS")
    elif isinstance(exception, websockets.exceptions.InvalidMessage):
        error_logger.error(f"Invalid message received: {str(exception)}")
        await chargepoint_websocket.close(code=1002, reason="Invalid message received")
    elif isinstance(exception, websockets.exceptions.ConnectionClosedError):
        error_logger.warning("Connection closed unexpectedly")
    elif isinstance(exception, websockets.exceptions.ConnectionClosedOK):
        logging.info("Connection closed gracefully")
    elif isinstance(exception, asyncio.TimeoutError):
        error_logger.error("Connection timed out")
        await chargepoint_websocket.close(code=1008, reason="Connection timed out")
    elif isinstance(exception, OSError):
        error_logger.error(f"OSError occurred: {str(exception)}")
        await chargepoint_websocket.close(code=1001, reason="Internal server error")
    else:
        error_logger.exception(f"Unexpected error: {str(exception)}")
        await chargepoint_websocket.close(code=1011, reason="Unexpected error occurred")

    # Update the authorization status to "Disconnected"
    update_authorization_status(charger_id, "Disconnected", access_token)

async def proxy_handler(chargepoint_websocket, path):
    charger_id = path.strip('/')
    logging.info(f"Chargepoint connected: {chargepoint_websocket.remote_address}")

    access_token = None  # Ensure access_token is defined
    try:
        # Obtain access token
        access_token = get_access_token()
        if not access_token:
            logging.error("Failed to obtain access token")
            return

        logger.warning(f"Access token obtained: {access_token}")

        # Fetch the ChargePoint details from the Django API
        details_url = f"{WEB_SERVICE_URL}{charger_id}/details/"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        response = requests.get(details_url, headers=headers)
        if response.status_code != 200:
            logging.error(f"Failed to fetch details for ChargePoint {charger_id}: {response.content.decode()}")
            return

        details = response.json()
        csms_url = details['connect_url'].rstrip('/')  # Remove trailing slash if present
        cp_id = details['cp_id']
        auth_key = details['auth_key']
        sec_prof = details['sec_prof']

        logger.warning(f"ChargePoint details fetched: {details}")

        # Combine csms_url and cp_id
        websocket_url = f"{csms_url}/{cp_id}"

        # Update authorization status to Connected
        update_authorization_status(charger_id, 'Connected', access_token)

        async with websockets.connect(websocket_url, subprotocols=['ocpp1.6']) as csms_websocket:
            logging.info(f"Connected to CSMS: {websocket_url}")

            async def forward_to_csms(message):
                try:
                    parsed_message = json.loads(message)
                    logging.info(f"<- CP: {parsed_message}")
                    await csms_websocket.send(message)
                    logging.info(f"-> CSMS: {parsed_message}")

                    # Check if the message is a StatusNotification
                    if isinstance(parsed_message, list) and len(parsed_message) > 2:
                        message_type_id = parsed_message[0]
                        action = parsed_message[2] if message_type_id == 2 else None
                        payload = parsed_message[3] if len(parsed_message) > 3 else {}

                        if action == 'StatusNotification' and 'status' in payload:
                            status = payload['status']
                            logger.info(f"StatusNotification received with status: {status}")
                            update_chargepoint_status(charger_id, status, access_token)

                except json.JSONDecodeError as e:
                    error_logger.error(f"Invalid JSON message from chargepoint: {message}")
                    error_logger.error(f"JSON decode error: {str(e)}")

            async def forward_to_chargepoint(message):
                try:
                    parsed_message = json.loads(message)
                    logging.info(f"<- CSMS: {parsed_message}")

                    await chargepoint_websocket.send(message)
                    logging.info(f"-> CP: {parsed_message}")
                except json.JSONDecodeError as e:
                    error_logger.error(f"Invalid JSON message from CSMS: {message}")
                    error_logger.error(f"JSON decode error: {str(e)}")

            async def handle_csms_messages():
                async for message in csms_websocket:
                    await forward_to_chargepoint(message)

            async def handle_chargepoint_messages():
                async for message in chargepoint_websocket:
                    await forward_to_csms(message)

            await asyncio.gather(handle_csms_messages(), handle_chargepoint_messages())

    except Exception as e:
        await exception_handler(chargepoint_websocket, e, charger_id, access_token)
    finally:
        logging.info("Connection closed")
        # Update authorization status to Disconnected if access_token is available
        if access_token:
            update_authorization_status(charger_id, 'Disconnected', access_token)

import socket

def get_ip_address():
    try:
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        return ip_address
    except Exception as e:
        error_logger.error(f"Failed to get IP address: {str(e)}")
        return "Unknown"

async def main():
    ip_address = get_ip_address()
    logging.info(f"Starting proxy server on IP address: {ip_address}")
    try:
        async with websockets.serve(proxy_handler, "0.0.0.0", 8001):
            logging.info("Proxy server started")
            await asyncio.Future()
    except OSError as e:
        error_logger.critical(f"Failed to start proxy server: {str(e)}")
    except Exception as e:
        error_logger.exception(f"Unexpected error while starting proxy server: {str(e)}")

asyncio.run(main())