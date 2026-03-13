import os
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Optional
from Classes.ApiInterface import ApiInterface
from Classes.Logger import Logger


class ApiBase(ApiInterface, ABC):
    SUCCESS = 200
    BAD_REQUEST = 400
    AUTH_ERROR = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    METHOD_ERROR = 405
    INVALID_CONTENT_TYPE = 415
    EXPECTATION_ERROR = 417
    SERVER_ERROR = 500
    BAD_GATEWAY = 502
    SERVICE_UNAVAILABLE = 503
    TIMEOUT_ERROR = 504
    
    title: str = ""
    description: str = ""
    apiParameters: dict = {}
    
    def __init__(self, action: str):
        self.action = action
        self.responseCode = self.SUCCESS
        self.responseData = {}
        self.error = False
        self.errorMessage = None
        self.debug = False
        self.debugMessages = []
        self.method = "GET"
        self.requestId = uuid.uuid4().hex
        self.startTime = time.time()
        self.internalCall = False
        self.logger = Logger(self.__class__.__name__)
        self.user = "NoLogin"
        self.isAdmin = False
        self.params = {}
    
    def setDebug(self, debug: bool) -> None:
        self.debug = debug
    
    def setMethod(self, method: str) -> None:
        self.method = method.upper()
    
    def processApi(self, params: dict) -> dict:
        try:
            if not self._validateMethod():
                return self._buildResponse()
            
            validatedParams = self._validateParams(params)
            if self.error:
                return self._buildResponse()
            
            self.params = validatedParams
            self.execute()
            
        except Exception as e:
            self.dieError(self.SERVER_ERROR, str(e))
        
        return self._buildResponse()
    
    def handle(self, params: dict) -> Any:
        self.internalCall = True
        
        validatedParams = self._validateParams(params)
        if self.error:
            return {"error": True, "message": self.errorMessage}
        
        self.params = validatedParams
        self.execute()
        
        return self.responseData
    
    def _validateMethod(self) -> bool:
        allowed = {
            "GET": self.requiresGet(),
            "POST": self.requiresPost(),
            "DELETE": self.requiresDelete(),
            "PATCH": self.requiresPatch(),
            "PUT": self.requiresPut()
        }
        
        if not allowed.get(self.method, False):
            if not any(allowed.values()):
                return True
            self.dieError(self.METHOD_ERROR, f"{self.method} Method Is Not Allowed")
            return False
        
        return True
    
    def _validateParams(self, params: dict) -> dict:
        validated = {}
        
        for paramName, options in self.apiParameters.items():
            value = params.get(paramName)
            
            if value is not None:
                converted = self._convertParam(paramName, value, options)
                if converted is not None:
                    validated[paramName] = converted
            elif options.get("required", False):
                desc = options.get("description", "")
                self.dieError(self.BAD_REQUEST, f"Missing required parameter: {paramName}. {desc}")
                return {}
            elif "default" in options:
                validated[paramName] = options["default"]
        
        return validated
    
    def _convertParam(self, name: str, value: Any, options: dict) -> Any:
        expectedType = options.get("type", "string")
        
        try:
            if expectedType == "integer":
                if isinstance(value, str):
                    if value.lstrip("-").isdigit():
                        return int(value)
                    else:
                        raise ValueError(f"Cannot convert '{value}' to integer")
                return int(value)
            
            elif expectedType == "float" or expectedType == "double":
                return float(value)
            
            elif expectedType == "boolean":
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    return value.lower() in ("true", "1", "yes")
                return bool(value)
            
            elif expectedType == "string":
                return str(value).strip()
            
            elif expectedType == "array":
                if isinstance(value, list):
                    return value
                raise ValueError(f"Expected array for {name}")
            
            elif expectedType == "object":
                if isinstance(value, dict):
                    return value
                raise ValueError(f"Expected object for {name}")
            
            else:
                return value
                
        except (ValueError, TypeError):
            desc = options.get("description", "")
            self.dieError(self.BAD_REQUEST, f"Invalid type for parameter {name}. Expected {expectedType}. {desc}")
            return None
    
    def _buildResponse(self) -> dict:
        return {
            "statusCode": self.responseCode,
            "response": {
                "debug": self._parseDebugMessages() if self.debug else [],
                "error": self.error,
                "errorMessage": self.errorMessage,
                "data": self.responseData
            }
        }
    
    def sendResponse(self, code: int = None) -> dict:
        if code is not None:
            self.responseCode = code
        
        if self.internalCall:
            return self.responseData
        
        return self._buildResponse()
    
    def dieError(self, code: int, message: str) -> None:
        self.error = True
        self.errorMessage = message
        self.responseCode = code
    
    def handleError(self, code: int, message: str) -> None:
        self.dieError(code, message)
    
    def debugMessage(self, title: str, message: str) -> None:
        import traceback
        
        stack = traceback.extract_stack()
        if len(stack) >= 2:
            caller = stack[-2]
            filename = os.path.basename(caller.filename)
            line = caller.lineno
        else:
            filename = "unknown"
            line = 0
        
        self.debugMessages.append({
            "title": title,
            "message": message,
            "file": filename,
            "line": line
        })
    
    def _parseDebugMessages(self) -> dict:
        debug = {}
        
        for msg in self.debugMessages:
            filename = msg["file"]
            title = msg["title"]
            
            if filename not in debug:
                debug[filename] = {}
            if title not in debug[filename]:
                debug[filename][title] = []
            
            debug[filename][title].append(msg["message"])
        
        return debug
    
    def loginRequired(self) -> bool:
        return False
    
    def tokenRequired(self) -> bool:
        return False
    
    def logging(self) -> bool:
        return False
    
    def requiresGet(self) -> bool:
        return False
    
    def requiresPost(self) -> bool:
        return False
    
    def requiresDelete(self) -> bool:
        return False
    
    def requiresPatch(self) -> bool:
        return False
    
    def requiresPut(self) -> bool:
        return False
    
    @abstractmethod
    def execute(self) -> None:
        pass
