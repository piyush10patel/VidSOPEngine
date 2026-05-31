"""Logging configuration for the application."""
import logging
import sys
from typing import Optional

from app.core.config import settings


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    """Configure and return the application logger."""
    log_level = level or ("DEBUG" if settings.debug else "INFO")
    
    # Create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    
    # Create application logger
    logger = logging.getLogger("vidsopengine")
    logger.setLevel(log_level)
    
    return logger


# Initialize logger
logger = setup_logging()
