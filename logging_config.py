import logging
from logging.handlers import RotatingFileHandler

def configure_logging():
    """
    Configures logging for the application.
    
    Sets up rotating file handlers and console handlers for both application and error loggers.
    The log files will rotate when they reach 10MB, with a maximum of 5 backup files.
    
    Returns:
        tuple: A tuple containing the app logger and the error logger.
    """
    logger = logging.getLogger('app_logger')
    error_logger = logging.getLogger('error_logger')
    
    if not logger.hasHandlers():
        handler = RotatingFileHandler('app.log', maxBytes=10*1024*1024, backupCount=5)
        console_handler = logging.StreamHandler()
        
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s - %(funcName)s\n      %(message)s\n', datefmt='%H:%M:%S')
        
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
