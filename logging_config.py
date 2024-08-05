import logging
from logging.handlers import RotatingFileHandler
import json

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'filename': record.filename,
            'funcName': record.funcName,
            'message': record.getMessage()
        }
        if isinstance(record.msg, dict):
            log_record['data'] = record.msg
        return json.dumps(log_record)

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
    
    if not logger.handlers:
        file_handler = RotatingFileHandler('app.log', maxBytes=10*1024*1024, backupCount=5)
        console_handler = logging.StreamHandler()
        
        file_formatter = JsonFormatter()
        console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s - %(funcName)s\n      %(message)s\n', datefmt='%H:%M:%S')
        
        file_handler.setFormatter(file_formatter)
        console_handler.setFormatter(console_formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        logger.setLevel(logging.INFO)

    if not error_logger.handlers:
        error_handler = RotatingFileHandler('error.log', maxBytes=10*1024*1024, backupCount=5)
        error_handler.setFormatter(file_formatter)
        error_logger.addHandler(error_handler)
        error_logger.addHandler(console_handler)
        error_logger.setLevel(logging.ERROR)

    return logger, error_logger