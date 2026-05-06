import json
from Classes.ApiBase import ApiBase
from Classes.Redis import getRedis


class ApiTrail(ApiBase):
    
    title = "Trail"
    description = "Get the flight trail (breadcrumb history) for a specific flight"
    
    apiParameters = {
        "id": {
            "description": "Flight ID, callsign, or ICAO hex code",
            "required": True,
            "type": "string"
        }
    }
    
    def execute(self):
        identifier = self.params.get("id", "")
        
        self.debugMessage("execute", f"Getting trail for: {identifier}")
        
        redis = getRedis()
        
        if not redis.ping():
            self.dieError(self.SERVICE_UNAVAILABLE, "Redis connection failed")
            return
        
        flightId = self.resolveFlightId(redis, identifier)
        
        if not flightId:
            self.responseData = {
                "flightId": identifier,
                "resolved": False,
                "points": []
            }
            self.sendResponse(self.SUCCESS)
            return
        
        trailKey = f"trail:{flightId}"
        rawTrail = redis.lrange(trailKey, 0, -1)
        
        points = []
        for item in rawTrail:
            try:
                pt = json.loads(item)
                lat = float(pt.get("lat", 0) or 0)
                lon = float(pt.get("lon", 0) or 0)
                
                if lat == 0 and lon == 0:
                    continue
                
                points.append({
                    "lat": lat,
                    "lon": lon,
                    "alt": pt.get("alt", ""),
                    "ts": pt.get("ts", "")
                })
            except (json.JSONDecodeError, ValueError):
                continue
        
        self.debugMessage("execute", f"Found {len(points)} trail points")
        
        self.responseData = {
            "flightId": flightId,
            "resolved": True,
            "count": len(points),
            "points": points
        }
        
        self.sendResponse(self.SUCCESS)
    
    def resolveFlightId(self, redis, identifier: str) -> str:
        raw = identifier.strip()
        if not raw:
            return None
        
        for prefix in ("profile:", "state:", "trail:"):
            key = f"{prefix}{raw}"
            if redis.exists(key):
                return raw
        
        upper = raw.upper()
        
        corr = redis.get(f"corr:callsign:{upper}")
        if corr:
            return corr
        
        corr = redis.get(f"corr:icao:{upper}")
        if corr:
            return corr
        
        synthetic = f"callsign:{upper}"
        for prefix in ("profile:", "state:", "trail:"):
            key = f"{prefix}{synthetic}"
            if redis.exists(key):
                return synthetic
        
        return None
    
    def requiresGet(self):
        return True
