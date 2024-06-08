import asyncio
import json
import logging
import django
import os
from typing import Any

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ocpp_hub_project.settings')
django.setup()

from asgiref.sync import sync_to_async
from ocpp_hub_app.models import Authorization, ChargePoint, LogEntry
from proxy_utils import identify_event_type
from database_utils import update_chargepoint_status_async, create_log_entry_async
from logging_config import configure_logging

logger, error_logger = configure_logging()

class MessageProcessor:
    """
    Processes messages from chargepoints and CSMS.
    
    Attributes:
        connection_manager (ConnectionManager): The connection manager instance.
    """

    def __init__(self, connection_manager):
        self.connection_manager = connection_manager

    async def process_message_from_chargepoint(self, chargepoint_uuid: str, message: str) -> None:
        """
        Processes a message from a chargepoint.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
            message (str): Message received from the chargepoint.
        """
        logger.debug(f"Processing message from chargepoint {chargepoint_uuid}: {message}")
        raw_message = message
        try:
            parsed_message = json.loads(message)
            event_type = identify_event_type(parsed_message)

            if isinstance(parsed_message, list) and len(parsed_message) > 3:
                message_type_id = parsed_message[0]
                action = parsed_message[2] if message_type_id == 2 else None
                payload = parsed_message[3] if len(parsed_message) > 3 else {}

                if action == 'StatusNotification' and 'status' in payload:
                    status = payload['status']
                    logger.debug(f"StatusNotification received with status: {status}")
                    await update_chargepoint_status_async(chargepoint_uuid, status)
                    await create_log_entry_async(chargepoint_uuid=chargepoint_uuid, event_type=event_type, message=f"Status changed to {status}", raw_message=raw_message)

            await self.connection_manager.send_to_csms(chargepoint_uuid, message)
        except json.JSONDecodeError as e:
            error_logger.error(f"Invalid JSON message from chargepoint: {raw_message}")
            error_logger.error(f"JSON decode error: {str(e)}")
            await create_log_entry_async(chargepoint_uuid=chargepoint_uuid, event_type="Error", message="Invalid JSON from chargepoint", raw_message=raw_message)
        except Exception as e:
            error_logger.error(f"Unexpected error in processing message from chargepoint: {str(e)}")
            await create_log_entry_async(chargepoint_uuid=chargepoint_uuid, event_type="Error", message="Unexpected error in processing message from chargepoint", raw_message=raw_message)

    async def process_message_from_csms(self, chargepoint_uuid: str, message: str, csms_uuid: str) -> None:
        """
        Processes a message from the CSMS.

        Parameters:
            chargepoint_uuid (str): UUID of the chargepoint.
            message (str): Message received from the CSMS.
            csms_uuid (str): UUID of the CSMS.
        """
        logger.debug(f"Processing message from CSMS {csms_uuid} to chargepoint {chargepoint_uuid}: {message}")
        raw_message = message
        try:
            parsed_message = json.loads(message)
            event_type = identify_event_type(parsed_message)

            await self.connection_manager.send_to_chargepoint(chargepoint_uuid, message, csms_uuid)
            await create_log_entry_async(chargepoint_uuid=chargepoint_uuid, event_type=event_type, message="Message sent to Chargepoint", raw_message=raw_message)
        except json.JSONDecodeError as e:
            error_logger.error(f"Invalid JSON message from CSMS: {raw_message}")
            error_logger.error(f"JSON decode error: {str(e)}")
            await create_log_entry_async(chargepoint_uuid=chargepoint_uuid, event_type="Error", message="Invalid JSON from CSMS", raw_message=raw_message)
        except Exception as e:
            error_logger.error(f"Unexpected error in processing message from CSMS: {str(e)}")
            await create_log_entry_async(chargepoint_uuid=chargepoint_uuid, event_type="Error", message="Unexpected error in processing message from CSMS", raw_message=raw_message)
