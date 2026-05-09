import httpx
import asyncio
import time
import uuid
import importlib
from typing import Any, Optional
from xml.etree import ElementTree


class ApiInterface:
    concurrency: int = 25
    
    def sendRest(self, request: dict, timeout: int = 120) -> Any:
        startTime = time.time()
        
        headers = request.get("HEADERS", {"Content-Type": "application/json"})
        body = request.get("BODY")
        
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(
                    method=request["METHOD"],
                    url=request["URL"],
                    headers=headers,
                    json=body if body else None
                )
        except httpx.RequestError as e:
            error = f"Failed to call API: {str(e)}"
            self.handleError(503, error)
            return None
        
        responseTime = f"{(time.time() - startTime) * 1000:.2f}ms"
        httpCode = response.status_code
        
        logData = self._buildLogData(request, httpCode, responseTime, "sendRest")
        
        if httpCode >= 400:
            logData["error"] = True
            logData["errorMessage"] = f"HTTP error occurred: {httpCode}"
            logData["response"] = response.text
            self.logger.log(logData)
        
        try:
            decoded = response.json()
        except Exception as e:
            self.handleError(503, f"Failed to decode API response: {response.text}")
            return None
        
        if isinstance(decoded, dict) and "error" in decoded:
            errorMessage = decoded["error"].get("message", "Unknown error") if isinstance(decoded["error"], dict) else str(decoded["error"])
            self.handleError(503, errorMessage)
            return None
        
        logData["response"] = decoded
        self.logger.log(logData)
        return decoded
    
    async def sendRestAsync(self, requests: list, timeout: int = 120) -> dict:
        results = {}
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            tasks = []
            for req in requests:
                headers = req.get("HEADERS", {"Content-Type": "application/json"})
                body = req.get("BODY")
                
                task = client.request(
                    method=req["METHOD"],
                    url=req["URL"],
                    headers=headers,
                    json=body if body else None
                )
                tasks.append((req["URL"], task))
            
            for url, task in tasks:
                try:
                    response = await task
                    results[url] = response.json()
                except Exception as e:
                    results[url] = {"error": True, "errorMessage": str(e)}
        
        return results
    
    def runAsync(self, requests: list, timeout: int = 120) -> dict:
        return asyncio.run(self.sendRestAsync(requests, timeout))
    
    def sendGraphQL(self, request: dict) -> Any:
        body = request.get("BODY", {})
        
        if "query" not in body:
            queryParts = []
            for key, value in body.items():
                queryParts.append(f"{key} {{ {value} }}")
            query = "query { " + " ".join(queryParts) + " }"
        else:
            query = body["query"]
        
        headers = request.get("HEADERS", {"Content-Type": "application/json"})
        
        try:
            with httpx.Client() as client:
                response = client.post(
                    request["URL"],
                    headers=headers,
                    json={"query": query}
                )
        except httpx.RequestError as e:
            self.handleError(400, f"GraphQL request failed: {str(e)}")
            return None
        
        if response.status_code != 200:
            self.handleError(response.status_code, f"GraphQL error: {response.text}")
            return None
        
        try:
            decoded = response.json()
        except Exception:
            self.handleError(503, "Failed to decode GraphQL response")
            return None
        
        if "errors" in decoded and decoded["errors"]:
            errorMessages = [e.get("message", "Unknown error") for e in decoded["errors"]]
            self.handleError(400, "; ".join(errorMessages))
            return None
        
        return decoded
    
    def sendSoap(self, request: dict) -> Any:
        body = request.get("BODY", {})
        headers = request.get("HEADERS", {"Content-Type": "text/xml; charset=utf-8"})
        
        if isinstance(body, dict):
            soapBody = self._buildSoapBody(body)
            soapXml = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
                f'<soap:Body>{soapBody}</soap:Body>'
                '</soap:Envelope>'
            )
        else:
            soapXml = body
        
        try:
            with httpx.Client() as client:
                response = client.post(request["URL"], headers=headers, content=soapXml)
        except httpx.RequestError as e:
            self.handleError(400, f"SOAP request failed: {str(e)}")
            return None
        
        if response.status_code != 200:
            self.handleError(response.status_code, f"SOAP error: {response.text}")
            return None
        
        try:
            decoded = self._xmlToDict(response.text)
        except Exception as e:
            self.handleError(503, f"Failed to parse SOAP response: {str(e)}")
            return None
        
        return decoded
    
    def _buildSoapBody(self, data: dict, namespace: str = "http://www.dataaccess.com/webservicesserver/") -> str:
        parts = []
        for key, value in data.items():
            if isinstance(value, dict):
                inner = self._buildSoapBody(value, namespace)
                parts.append(f'<{key} xmlns="{namespace}">{inner}</{key}>')
            else:
                escaped = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                parts.append(f'<{key} xmlns="{namespace}">{escaped}</{key}>')
        return "".join(parts)
    
    def _xmlToDict(self, xmlString: str) -> dict:
        import re
        xmlString = re.sub(r'<(/?)(\w+):', r'<\1\2_', xmlString)
        root = ElementTree.fromstring(xmlString)
        return self._elementToDict(root)
    
    def _elementToDict(self, element) -> Any:
        result = {}
        for child in element:
            tagName = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            childValue = self._elementToDict(child) if len(child) > 0 else child.text
            
            if tagName in result:
                if not isinstance(result[tagName], list):
                    result[tagName] = [result[tagName]]
                result[tagName].append(childValue)
            else:
                result[tagName] = childValue
        
        return result if result else element.text
    
    def sendRpcJson(self, request: dict, timeout: int = 120) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "method": request["METHOD"],
            "params": request.get("BODY", {}),
            "id": str(uuid.uuid4())
        }
        
        headers = {"Content-Type": "application/json"}
        
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(request["URL"], headers=headers, json=payload)
        except httpx.RequestError as e:
            self.handleError(400, f"RPC JSON request failed: {str(e)}")
            return None
        
        if response.status_code != 200:
            self.handleError(response.status_code, f"RPC error: {response.text}")
            return None
        
        try:
            decoded = response.json()
        except Exception:
            self.handleError(503, "Failed to decode RPC JSON response")
            return None
        
        if "error" in decoded:
            errorMessage = decoded["error"].get("message", "Unknown error")
            self.handleError(503, errorMessage)
            return None
        
        return decoded
    
    def sendInternal(self, moduleName: str, params: dict = {}) -> Any:
        try:
            modulePath = f"Modules.Api{moduleName}"
            module = importlib.import_module(modulePath)
            endpointClass = getattr(module, f"Api{moduleName}")
        except (ImportError, AttributeError):
            raise Exception(f"Module endpoint 'Api{moduleName}' not found.")
        
        instance = endpointClass(moduleName)
        
        if not hasattr(instance, "handle"):
            raise Exception(f"Module 'Api{moduleName}' does not implement a handle method.")
        
        response = instance.handle(params)
        
        self.logger.log({
            "level": "INFO",
            "logType": "internal",
            "endpoint": f"Api{moduleName}",
            "method": "INTERNAL",
            "url": "internal",
            "code": 200,
            "requestId": self.requestId,
            "body": params,
            "response": response
        })
        
        return response
    
    def _buildLogData(self, request: dict, httpCode: int, responseTime: str, endpoint: str) -> dict:
        return {
            "level": "INFO",
            "logType": "outgoing",
            "code": httpCode,
            "endpoint": endpoint,
            "method": request.get("METHOD", "GET"),
            "url": request.get("URL", ""),
            "body": request.get("BODY"),
            "error": False,
            "errorMessage": None,
            "requestId": getattr(self, "requestId", "unknown"),
            "response": None,
            "responseTime": responseTime
        }
