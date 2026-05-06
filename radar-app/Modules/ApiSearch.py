from Classes.ApiBase import ApiBase
from Classes.Redis import getRedis


class ApiSearch(ApiBase):
    
    title = "Search"
    description = "Search for planes by callsign, airport, airline, or flight ID"
    
    apiParameters = {
        "q": {
            "description": "Search query (callsign, airport code, airline, or flight ID)",
            "required": True,
            "type": "string"
        },
        "limit": {
            "description": "Maximum number of results",
            "required": False,
            "type": "integer",
            "default": 50
        }
    }
    
    def execute(self):
        query = self.params.get("q", "").strip().upper()
        limit = self.params.get("limit", 50)
        
        if not query:
            self.dieError(self.BAD_REQUEST, "Search query is required")
            return
        
        self.debugMessage("execute", f"Searching for: {query}")
        
        redis = getRedis()
        
        if not redis.ping():
            self.dieError(self.SERVICE_UNAVAILABLE, "Redis connection failed")
            return
        
        results = []
        
        for key in redis.scan_iter(match="state:*", count=1000):
            if len(results) >= limit:
                break
            
            flightId = key.split("state:", 1)[1]
            state = redis.hgetall(f"state:{flightId}")
            profile = redis.hgetall(f"profile:{flightId}")
            
            plane = {}
            plane.update(profile)
            plane.update(state)
            
            if self.matchesQuery(plane, flightId, query):
                results.append(self.formatResult(plane, flightId))
        
        results.sort(key=lambda p: (
            0 if query in p.get("callsign", "").upper() else 1,
            -float(p.get("lastUpdate", 0))
        ))
        
        self.debugMessage("execute", f"Found {len(results)} results")
        
        self.responseData = {
            "query": query,
            "count": len(results),
            "results": results[:limit]
        }
        
        self.sendResponse(self.SUCCESS)
    
    def matchesQuery(self, plane: dict, flightId: str, query: str) -> bool:
        searchFields = [
            plane.get("callsign", ""),
            plane.get("operator", ""),
            plane.get("dep", ""),
            plane.get("arr", ""),
            plane.get("icao_hex", ""),
            plane.get("gufi", ""),
            flightId
        ]
        
        for field in searchFields:
            if query in str(field).upper():
                return True
        
        return False
    
    def formatResult(self, plane: dict, flightId: str) -> dict:
        return {
            "flightId": flightId,
            "callsign": plane.get("callsign", ""),
            "operator": plane.get("operator", ""),
            "dep": plane.get("dep", ""),
            "arr": plane.get("arr", ""),
            "lat": self.safeFloat(plane.get("lat", 0)),
            "lon": self.safeFloat(plane.get("lon", 0)),
            "alt": self.safeFloat(plane.get("alt", 0)),
            "speed": self.safeFloat(plane.get("speed", 0)),
            "heading": self.safeFloat(plane.get("heading", 0)),
            "icaoHex": plane.get("icao_hex", ""),
            "source": plane.get("source", ""),
            "lastUpdate": self.safeFloat(plane.get("last_update", 0))
        }
    
    def safeFloat(self, value, default=0.0) -> float:
        try:
            return float(value) if value else default
        except (ValueError, TypeError):
            return default
    
    def requiresGet(self):
        return True
