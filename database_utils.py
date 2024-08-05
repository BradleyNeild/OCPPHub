from ocpp_hub_app.models import Authorization, ChargePoint, LogEntry
from asgiref.sync import sync_to_async
import logging
import json
from datetime import datetime
logger = logging.getLogger('app_logger')
error_logger = logging.getLogger('error_logger')


@sync_to_async
def create_log_entry_async(chargepoint_uuid=None, authorization=None, event_type=None, action=None, 
                           from_entity=None, to_entity=None, message=None, raw_message=None, 
                           level='INFO'):
    try:
        log_entry = LogEntry(
            event_type=event_type,
            action=action,
            message=message,
            level=level
        )

        if chargepoint_uuid:
            try:
                log_entry.chargepoint = ChargePoint.objects.get(uuid=chargepoint_uuid)
            except ChargePoint.DoesNotExist:
                logger.error(f"ChargePoint with UUID {chargepoint_uuid} does not exist.")

        if authorization:
            if isinstance(authorization, Authorization):
                log_entry.authorization = authorization
            elif isinstance(authorization, str):
                try:
                    log_entry.authorization = Authorization.objects.get(uuid=authorization)
                except Authorization.DoesNotExist:
                    logger.error(f"Authorization with UUID {authorization} does not exist.")
            else:
                logger.error(f"Invalid authorization type: {type(authorization)}")

        if from_entity:
            log_entry.set_from_entity(
                from_entity.get('name', 'Unknown'),
                from_entity.get('type', 'Unknown'),
                from_entity.get('uuid')
            )

        if to_entity:
            log_entry.set_to_entity(
                to_entity.get('name', 'Unknown'),
                to_entity.get('type', 'Unknown'),
                to_entity.get('uuid')
            )

        if raw_message:
            log_entry.set_raw_message(raw_message)

        log_entry.save()
        logger.debug(f"Log entry created: {log_entry}")
        return log_entry
    except Exception as e:
        logger.error(f"Error creating log entry: {str(e)}", exc_info=True)
        raise


@sync_to_async
def update_authorization_connection_status_async(authorization_uuid: str, connection_status: str):
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
def update_chargepoint_connection_status_async(chargepoint_uuid: str, connection_status: str):
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
def update_chargepoint_status_async(chargepoint_uuid: str, status: str):
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
def get_authorizations_by_chargepoint_uuid_async(chargepoint_uuid: str):
    """
    Gets the authorizations associated with a chargepoint UUID.

    Parameters:
        chargepoint_uuid (str): UUID of the chargepoint.

    Returns:
        list: List of Authorization objects.
    """
    return list(Authorization.objects.filter(chargepoint__uuid=chargepoint_uuid))

@sync_to_async
def set_primary_authorization_async(chargepoint_uuid: str, authorization_uuid: str):
    """
    Sets an authorization as the primary for a given chargepoint.

    Parameters:
        chargepoint_uuid (str): UUID of the chargepoint.
        authorization_uuid (str): UUID of the authorization to be set as primary.
    """
    authorizations = Authorization.objects.filter(chargepoint__uuid=chargepoint_uuid)
    for auth in authorizations:
        auth.is_primary = (auth.uuid == authorization_uuid)
        auth.save()
    return authorizations