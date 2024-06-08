import asyncio
import websockets
import logging
from aiohttp import web
from dotenv import load_dotenv
from connection_manager import ConnectionManager
from proxy_utils import get_ip_address
import os
import signal

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

async def main():
    """
    Main entry point for the proxy server.
    
    Initializes the connection manager, sets up signal handlers for graceful shutdown,
    starts the aiohttp web server and the WebSocket server.
    """
    await connection_manager.initialize()

    ip_address = get_ip_address()
    logger.info(f"Starting proxy server on IP address: {ip_address}")

    aiohttp_app = web.Application()
    aiohttp_app.add_routes([
        web.post('/api/close_authorization', close_authorization_connection),
    ])

    aiohttp_runner = web.AppRunner(aiohttp_app)
    await aiohttp_runner.setup()
    aiohttp_site = web.TCPSite(aiohttp_runner, '0.0.0.0', 8999)
    await aiohttp_site.start()

    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown(s, loop)))

    while True:
        try:
            async with websockets.serve(connection_manager.proxy_handler, "0.0.0.0", 8998):
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

asyncio.run(main())
