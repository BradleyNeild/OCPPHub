import asyncio
import websockets
import logging
from aiohttp import web
from dotenv import load_dotenv
from connection_manager import ConnectionManager
from proxy_utils import get_ip_address, get_access_token
import os
import signal
import json

# Load environment variables early
load_dotenv()

from logging_config import configure_logging
logger, error_logger = configure_logging()

WEB_SERVICE_URL = os.getenv('WEB_SERVICE_URL')

connection_manager = ConnectionManager()

async def close_authorization_connection(request: web.Request) -> web.Response:
    """
    Closes an authorization connection based on an HTTP request.

    Parameters:
        request (web.Request): HTTP request.

    Returns:
        web.Response: JSON response.
    """
    data = await request.json()
    authorization_uuid = data.get('authorization_uuid')

    if not authorization_uuid:
        return web.json_response({'error': 'authorization_uuid is required'}, status=400)

    response = await connection_manager.close_authorization_connection(authorization_uuid)
    return web.json_response(response)

async def restart_chargepoint(request: web.Request) -> web.Response:
    """
    Restarts connections for a chargepoint.

    Parameters:
        request (web.Request): HTTP request containing the chargepoint UUID.

    Returns:
        web.Response: JSON response indicating success or failure.
    """
    try:
        data = await request.json()
        chargepoint_uuid = data.get('chargepoint_uuid')

        if not chargepoint_uuid:
            logger.error('chargepoint_uuid is required')
            return web.json_response({'error': 'chargepoint_uuid is required'}, status=400)

        # Close existing connections
        await connection_manager.close_chargepoint_connections(chargepoint_uuid)

        # Re-fetch details and re-establish connections
        access_token = get_access_token()
        details = await connection_manager.get_chargepoint_details(chargepoint_uuid, access_token)
        actual_chargepoint_uuid = details.get('uuid', chargepoint_uuid)
        await connection_manager._add_chargepoint_connection(actual_chargepoint_uuid, None)
        
        logger.info(f'Successfully restarted chargepoint {chargepoint_uuid}')
        return web.json_response({'status': 'success'})
    except Exception as e:
        logger.error(f'Error restarting chargepoint {chargepoint_uuid}: {str(e)}', exc_info=True)
        return web.json_response({'error': 'Internal server error', 'details': str(e)}, status=500)

async def set_primary_authorization(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        chargepoint_uuid = data.get('chargepoint_uuid')
        authorization_uuid = data.get('authorization_uuid')
        authorizations = data.get('authorizations', [])

        if not chargepoint_uuid or not authorization_uuid or not authorizations:
            logger.error(f"Missing required data: chargepoint_uuid={chargepoint_uuid}, authorization_uuid={authorization_uuid}, authorizations={bool(authorizations)}")
            return web.json_response({'error': 'chargepoint_uuid, authorization_uuid, and authorizations are required'}, status=400)

        success = await connection_manager.set_primary_authorization_async(chargepoint_uuid, authorization_uuid, authorizations)

        if success:
            logger.info(f'Successfully set primary authorization {authorization_uuid} for chargepoint {chargepoint_uuid}')
            return web.json_response({'status': 'success'})
        else:
            logger.error(f'Failed to set primary authorization {authorization_uuid} for chargepoint {chargepoint_uuid}')
            return web.json_response({'error': 'Failed to set primary authorization'}, status=500)
    except Exception as e:
        logger.error(f'Error setting primary authorization {authorization_uuid} for chargepoint {chargepoint_uuid}: {str(e)}', exc_info=True)
        return web.json_response({'error': 'Internal server error', 'details': str(e)}, status=500)

async def reset_chargepoint_connections(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        chargepoint_uuid = data.get('chargepoint_uuid')
        
        logger.info(f"Received request to reset connections for chargepoint {chargepoint_uuid}")
        
        if not chargepoint_uuid:
            logger.error('chargepoint_uuid is required')
            return web.json_response({'error': 'chargepoint_uuid is required'}, status=400)
        
        logger.info(f"Initiating connection reset for chargepoint {chargepoint_uuid}")
        success = await connection_manager.reset_chargepoint_connections(chargepoint_uuid)
        
        if success:
            logger.info(f'Successfully reset connections for chargepoint {chargepoint_uuid}')
            return web.json_response({'status': 'success'})
        else:
            logger.error(f'Failed to reset connections for chargepoint {chargepoint_uuid}')
            return web.json_response({'error': 'Failed to reset connections'}, status=500)
    except Exception as e:
        logger.error(f'Error resetting connections for chargepoint {chargepoint_uuid}: {str(e)}', exc_info=True)
        return web.json_response({'error': str(e)}, status=500)

async def main():
    """
    Main entry point for the proxy server.
    
    Initializes the connection manager, sets up signal handlers for graceful shutdown,
    starts the aiohttp web server and the WebSocket server.
    """
    await connection_manager.initialize()

    ip_address = "172.21.0.32"  # Hardcoded IP address for the proxy service
    logger.info(f"Starting proxy server on IP address: {ip_address}")

    # Print access information
    print(f"Access the web interface using")
    print(f"- http://172.21.0.31:8997/dashboard")
    print(f"WebSocket/JSON endpoint for OCPP")
    print(f"- ws://{ip_address}:8998/(Charge Point UUID)\n")

    app = web.Application()
    app.add_routes([
        web.post('/api/close_authorization', close_authorization_connection),
        web.post('/api/reset_chargepoint_connections', reset_chargepoint_connections),
        web.post('/api/set_primary_authorization', set_primary_authorization),
        web.post('/api/restart_chargepoint', restart_chargepoint),
    ])

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8999)  # REST API on port 8999
    await site.start()

    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown(s, loop)))

    while True:
        try:
            async with websockets.serve(connection_manager.proxy_handler, "0.0.0.0", 8998):  # WebSocket on port 8998
                logger.info("WebSocket proxy server started")
                await asyncio.Future()  # Keeps the WebSocket server running
        except OSError as e:
            error_logger.critical(f"Failed to start WebSocket proxy server: {str(e)}")
            await asyncio.sleep(5)
        except Exception as e:
            error_logger.exception(f"Unexpected error while starting WebSocket proxy server: {str(e)}")
            await asyncio.sleep(5)

async def shutdown(signal, loop):
    """
    Shutdown the server gracefully.

    Parameters:
        signal (signal): The received signal.
        loop (asyncio.AbstractEventLoop): The event loop.
    """
    logger.info(f"Received exit signal {signal.name}...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    list(map(lambda task: task.cancel(), tasks))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

if __name__ == "__main__":
    asyncio.run(main())