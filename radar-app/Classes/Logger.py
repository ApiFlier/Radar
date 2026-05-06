import json
import logging
from datetime import datetime
from typing import Any


class Logger:
    def __init__(self, name: str):
        self.name = name
        self._logger = logging.getLogger(name)
        
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)
    
    def log(self, data: dict) -> None:
        level = data.get("level", "INFO").upper()
        logType = data.get("logType", "unknown")
        code = data.get("code", 0)
        endpoint = data.get("endpoint", "")
        method = data.get("method", "")
        url = data.get("url", "")
        responseTime = data.get("responseTime", "")
        
        message = f"[{logType}] {method} {endpoint}"
        if url and url != "internal":
            message += f" -> {url}"
        message += f" | {code}"
        if responseTime:
            message += f" | {responseTime}"
        
        if data.get("error"):
            message += f" | ERROR: {data.get('errorMessage', 'Unknown')}"
        
        if level == "ERROR":
            self._logger.error(message)
        elif level == "WARNING":
            self._logger.warning(message)
        elif level == "DEBUG":
            self._logger.debug(message)
        else:
            self._logger.info(message)
    
    def info(self, message: str, context: dict = None) -> None:
        self._logger.info(message)
    
    def error(self, message: str, context: dict = None) -> None:
        self._logger.error(message)
    
    def warning(self, message: str, context: dict = None) -> None:
        self._logger.warning(message)
    
    def debug(self, message: str, context: dict = None) -> None:
        self._logger.debug(message)
