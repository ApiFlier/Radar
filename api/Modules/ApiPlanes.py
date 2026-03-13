import json
from Classes.ApiBase import ApiBase
from Classes.Redis import getRedis


class ApiPlanes(ApiBase):
    
    title = "Planes"
    description = "Get all current plane positions"
    
    apiParameters = {
        "airline": {
            "description": "Filter by airline/operator code (e.g., SWA, DAL, UAL)",
            "required": False,
            "type": "string"
        },
        "minAlt": {
            "description": "Minimum altitude in feet",
            "required": False,
            "type": "integer",
            "default": 0
        },
        "maxAlt": {
            "description": "Maximum altitude in feet",
            "required": False,
            "type": "integer",
            "default": 100000
        },
        "airborne": {
            "description": "Filter to only airborne planes (true/false)",
            "required": False,
            "type": "boolean",
            "default": False
        },
        "source": {
            "description": "Filter by data source (e.g., swim-sfdps, swim-stdds, opensky)",
            "required": False,
            "type": "string"
        },
        "near": {
            "description": "Airport code to filter planes near (e.g., PIT, JFK)",
            "required": False,
            "type": "string"
        }
    }
    
    def execute(self):
        self.debugMessage("execute", "Starting Planes request")
        
        redis = getRedis()
        
        if not redis.ping():
            self.dieError(self.SERVICE_UNAVAILABLE, "Redis connection failed")
            return
        
        planes = []
        sourceCounts = {}
        
        for key in redis.scan_iter(match="state:*", count=1000):
            flightId = key.split("state:", 1)[1]
            plane = self.loadPlane(redis, flightId)
            
            if plane:
                source = plane.get("source", "unknown")
                sourceCounts[source] = sourceCounts.get(source, 0) + 1
                
                if self.filterPlane(plane):
                    planes.append(plane)
        
        planes.sort(key=lambda p: float(p.get("lastUpdate", 0)), reverse=True)
        
        self.debugMessage("execute", f"Found {len(planes)} planes")
        
        self.responseData = {
            "count": len(planes),
            "sources": sourceCounts,
            "planes": planes
        }
        
        self.sendResponse(self.SUCCESS)
    
    def loadPlane(self, redis, flightId: str) -> dict:
        profile = redis.hgetall(f"profile:{flightId}")
        state = redis.hgetall(f"state:{flightId}")
        
        if not profile and not state:
            return None
        
        plane = {}
        plane.update(profile)
        plane.update(state)
        
        normalized = {
            "flightId": plane.get("flight_id", flightId),
            "callsign": plane.get("callsign", ""),
            "operator": plane.get("operator", ""),
            "dep": plane.get("dep", ""),
            "arr": plane.get("arr", ""),
            "depTime": plane.get("dep_time", ""),
            "eta": plane.get("eta", ""),
            "faaTs": plane.get("faa_ts", ""),
            "flightStatus": plane.get("flight_status", ""),
            "icaoHex": plane.get("icao_hex", ""),
            "source": plane.get("source", ""),
            "sourceFacility": plane.get("source_facility", ""),
            "trackKey": plane.get("track_key", ""),
            "gufi": plane.get("gufi", ""),
            "lat": self.safeFloat(plane.get("lat", 0)),
            "lon": self.safeFloat(plane.get("lon", 0)),
            "speed": self.safeFloat(plane.get("speed", 0)),
            "heading": self.safeFloat(plane.get("heading", 0)),
            "alt": self.safeFloat(plane.get("alt", 0)),
            "assignedAlt": plane.get("assigned_alt", ""),
            "verticalRate": plane.get("vertical_rate", ""),
            "airborne": plane.get("airborne", "0"),
            "lastUpdate": self.safeFloat(plane.get("last_update", 0))
        }
        
        if normalized["lat"] == 0 and normalized["lon"] == 0:
            return None
        
        return normalized
    
    def filterPlane(self, plane: dict) -> bool:
        params = self.params
        
        airline = params.get("airline", "")
        if airline:
            planeOp = plane.get("operator", "").upper()
            planeCall = plane.get("callsign", "").upper()
            if airline.upper() not in planeOp and not planeCall.startswith(airline.upper()):
                return False
        
        alt = plane.get("alt", 0)
        minAlt = params.get("minAlt", 0)
        maxAlt = params.get("maxAlt", 100000)
        if alt < minAlt or alt > maxAlt:
            return False
        
        if params.get("airborne"):
            if plane.get("airborne") != "1" and plane.get("speed", 0) < 40:
                return False
        
        source = params.get("source", "")
        if source:
            planeSource = plane.get("source", "").lower()
            if source.lower() not in planeSource:
                return False
        
        near = params.get("near", "")
        if near:
            nearUpper = near.upper()
            if plane.get("dep", "").upper() != nearUpper and plane.get("arr", "").upper() != nearUpper:
                return False
        
        return True
    
    def safeFloat(self, value, default=0.0) -> float:
        try:
            return float(value) if value else default
        except (ValueError, TypeError):
            return default
    
    def requiresGet(self):
        return True
