import json
import logging
from asgiref.sync import sync_to_async
from ocpp_hub_app.models import Authorization, ChargePoint
from proxy_utils import identify_event_type
from database_utils import update_chargepoint_status_async, create_log_entry_async

logger = logging.getLogger('app_logger')

class MessageProcessor:
    def __init__(self, connection_manager):
        self.connection_manager = connection_manager
        self.always_forward_actions = [
            "StatusNotification", "Heartbeat", "MeterValues", "BootNotification"
        ]
        self.read_only_actions = [
            "GetConfiguration", "GetDiagnostics", "GetLocalListVersion",
            "GetLog", "GetStatus", "GetStopTransactionLog"
        ]
        self.write_actions = [
            "ChangeAvailability", "ChangeConfiguration", "ClearCache",
            "RemoteStartTransaction", "RemoteStopTransaction", "Reset",
            "SendLocalList", "UnlockConnector", "UpdateFirmware"
        ]

    async def process_message_from_chargepoint(self, chargepoint_uuid: str, message: str) -> None:
        try:
            parsed_message = json.loads(message)
            chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
            chargepoint_name = chargepoint.name

            event_type = identify_event_type(parsed_message)
            action = parsed_message[2] if len(parsed_message) > 2 else "Unknown"

            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                event_type=event_type,
                action=action,
                from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                to_entity={"name": "CSMS", "type": "CSMS"},
                message=f"{event_type} message received from Chargepoint {chargepoint_name}",
                raw_message=message,
                level='INFO'
            )

            authorizations = await sync_to_async(list)(Authorization.objects.filter(chargepoint__uuid=chargepoint_uuid))
            for authorization in authorizations:
                csms_status = "(P)" if authorization.is_primary else "(S)"
                await self.connection_manager.send_to_csms(authorization.uuid, message)
                
                await create_log_entry_async(
                    chargepoint_uuid=chargepoint_uuid,
                    authorization=authorization,
                    event_type=event_type,
                    action=f"Forwarded to {authorization.csms_name}",
                    from_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                    to_entity={"name": authorization.csms_name, "type": "CSMS", "uuid": str(authorization.uuid)},
                    message=f"Message forwarded to {authorization.csms_name} {csms_status}",
                    raw_message=message,
                    level='INFO'
                )

            if event_type == 'StatusNotification' and 'status' in parsed_message[3]:
                status = parsed_message[3]['status']
                await update_chargepoint_status_async(chargepoint_uuid, status)
                logger.info(f"Updated chargepoint {chargepoint_name} status to {status}")

        except Exception as e:
            logger.error(f"Error processing message from chargepoint {chargepoint_uuid}: {str(e)}", exc_info=True)
            await self._log_error(chargepoint_uuid, "Processing Error", f"Unexpected error in processing message from chargepoint: {str(e)}", message)

    async def process_message_from_csms(self, chargepoint_uuid: str, message: str, csms_uuid: str) -> None:
        try:
            parsed_message = json.loads(message)
            authorization = await sync_to_async(Authorization.objects.get)(uuid=csms_uuid)
            chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
            chargepoint_name = chargepoint.name
            csms_status = "(P)" if authorization.is_primary else "(S)"

            event_type = identify_event_type(parsed_message)
            action = parsed_message[2] if len(parsed_message) > 2 else "Unknown"

            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=authorization,
                event_type=event_type,
                action=action,
                from_entity={"name": authorization.csms_name, "type": "CSMS", "uuid": str(csms_uuid)},
                to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                message=f"{event_type} message received from CSMS {authorization.csms_name} {csms_status}",
                raw_message=message,
                level='INFO'
            )

            if authorization.is_primary:
                await self.connection_manager.send_to_chargepoint(chargepoint_uuid, message, csms_uuid)
                
                await create_log_entry_async(
                    chargepoint_uuid=chargepoint_uuid,
                    authorization=authorization,
                    event_type=event_type,
                    action="Forwarded to Chargepoint",
                    from_entity={"name": authorization.csms_name, "type": "CSMS", "uuid": str(csms_uuid)},
                    to_entity={"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)},
                    message=f"Message forwarded to Chargepoint {chargepoint_name} from primary CSMS {authorization.csms_name}",
                    raw_message=message,
                    level='INFO'
                )
            else:
                if parsed_message[0] == 2:  # It's a request
                    if action in self.always_forward_actions or action in self.read_only_actions:
                        await self.connection_manager.send_to_chargepoint(chargepoint_uuid, message, csms_uuid)
                        logger.info(f"Forwarded allowed action from secondary CSMS to chargepoint {chargepoint_name}: {message}")
                    else:
                        call_error = [4, parsed_message[1], "NotSupported", f"Action from secondary CSMS is not supported", {}]
                        await self.connection_manager.send_to_csms(csms_uuid, json.dumps(call_error))
                        logger.info(f"Sent CallError to secondary CSMS {authorization.csms_name}: {json.dumps(call_error)}")

                        await create_log_entry_async(
                            chargepoint_uuid=chargepoint_uuid,
                            authorization=authorization,
                            event_type='CallError',
                            action="Unsupported action from secondary CSMS",
                            from_entity={"name": "Proxy", "type": "System"},
                            to_entity={"name": authorization.csms_name, "type": "CSMS", "uuid": str(csms_uuid)},
                            message="Action from secondary CSMS is not supported",
                            raw_message=json.dumps(call_error),
                            level='WARNING'
                        )
                else:
                    logger.info(f"Received response from secondary CSMS {authorization.csms_name} (not forwarded): {message}")

        except Exception as e:
            logger.error(f"Error processing message from CSMS {csms_uuid}: {str(e)}", exc_info=True)
            await self._log_error(chargepoint_uuid, "Processing Error", f"Unexpected error in processing message from CSMS: {str(e)}", message, csms_uuid)

    async def _log_error(self, chargepoint_uuid: str, action: str, error_message: str, raw_message: str, csms_uuid: str = None):
        try:
            chargepoint = await sync_to_async(ChargePoint.objects.get)(uuid=chargepoint_uuid)
            chargepoint_name = chargepoint.name

            from_entity = {"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)}
            to_entity = {"name": "System", "type": "System"}

            if csms_uuid:
                authorization = await sync_to_async(Authorization.objects.get)(uuid=csms_uuid)
                csms_status = "(P)" if authorization.is_primary else "(S)"
                from_entity = {"name": f"{authorization.csms_name} {csms_status}", "type": "CSMS", "uuid": str(csms_uuid)}
                to_entity = {"name": chargepoint_name, "type": "Chargepoint", "uuid": str(chargepoint_uuid)}

            await create_log_entry_async(
                chargepoint_uuid=chargepoint_uuid,
                authorization=authorization if csms_uuid else None,
                event_type='Error',
                action=action,
                from_entity=from_entity,
                to_entity=to_entity,
                message=error_message,
                raw_message=raw_message,
                level='ERROR'
            )
            logger.error(f"{action}: {error_message}")
        except Exception as e:
            logger.error(f"Error while logging error: {str(e)}", exc_info=True)