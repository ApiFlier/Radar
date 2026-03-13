import time
from Classes.ApiBase import ApiBase
from Classes.Redis import getRedis


class ApiHealth(ApiBase):
    
    title = "Health"
    description = "Health check endpoint - returns Redis status and plane counts"
    
    apiParameters = {}
    
    def execute(self):
        redis = getRedis()
        
        redisOk = redis.ping()
        
        planeCount = 0
        airborneCount = 0
        sourceCounts = {}
        
        if redisOk:
            for key in redis.scan_iter(match="state:*", count=1000):
                planeCount += 1
                state = redis.hgetall(key)
                
                if state.get("airborne") == "1" or float(state.get("speed", 0) or 0) >= 40:
                    airborneCount += 1
                
                source = state.get("source", "unknown")
                sourceCounts[source] = sourceCounts.get(source, 0) + 1
        
        self.responseData = {
            "status": "ok" if redisOk else "degraded",
            "timestamp": time.time(),
            "redis": {
                "connected": redisOk
            },
            "planes": {
                "total": planeCount,
                "airborne": airborneCount
            },
            "sources": sourceCounts
        }
        
        if not redisOk:
            self.responseCode = self.SERVICE_UNAVAILABLE
        
        self.sendResponse()
    
    def requiresGet(self):
        return True
