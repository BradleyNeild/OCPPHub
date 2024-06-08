from ocpp_hub_app.models import Authorization, ChargePoint, LogEntry
from asgiref.sync import sync_to_async
import logging

logger = logging.getLogger('app_logger')
error_logger = logging.getLogger('error_logger')

@sync_to_async
def create_log_entry_async(chargepoint_uuid: str = None, authorization: Authorization = None, event_type: str = "Information", message: str = "", raw_message: str = ""):
    """
    Creates a log entry in the database.

    Parameters:
        chargepoint_uuid (str): UUID of the chargepoint.
        authorization (Authorization): Authorization object.
        event_type (str): Type of event.
        message (str): Log message.
        raw_message (str): Raw message.
    """
    chargepoint_instance = None
    if chargepoint_uuid:
        try:
            chargepoint_instance = ChargePoint.objects.get(uuid=chargepoint_uuid)
        except ChargePoint.DoesNotExist:
            error_logger.error(f"ChargePoint with UUID {chargepoint_uuid} does not exist.")
            return
    
    log_entry = LogEntry(chargepoint=chargepoint_instance, authorization=authorization, event_type=event_type, message=message, raw_message=raw_message)
    log_entry.save()
    logger.debug(f"Log entry created: {message} | Raw message: {raw_message}")

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
