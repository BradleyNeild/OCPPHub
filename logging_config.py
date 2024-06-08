import logging
from logging.handlers import RotatingFileHandler

def configure_logging():
    """
    Configures logging for the application.
    """
    logger = logging.getLogger('app_logger')
    error_logger = logging.getLogger('error_logger')
    
    if not logger.hasHandlers():
        handler = RotatingFileHandler('app.log', maxBytes=10*1024*1024, backupCount=5)
        console_handler = logging.StreamHandler()
        
        formatter = logging.Formatter('%(asctime)s - %(levelname)-8s - %(filename)s - %(funcName)s - %(message)s', datefmt='%H:%M:%S')
        
        handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        logger.addHandler(handler)
        logger.addHandler(console_handler)
        logger.setLevel(logging.INFO)

    if not error_logger.hasHandlers():
        error_logger.addHandler(handler)
        error_logger.addHandler(console_handler)
        error_logger.setLevel(logging.ERROR)

    return logger, error_logger
