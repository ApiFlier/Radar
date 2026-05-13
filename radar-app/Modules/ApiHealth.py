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
        onGroundCount = 0
        parkedCount = 0
        sourceCounts = {}
        
        adsbLolCount = 0
        adsbLolHeartbeat = {}

        now = time.time()
        if redisOk:
            # Process both primary state and ADSB.lol state for accurate counts
            for pattern in ["state:*", "adsblol:state:icao:*"]:
                for key in redis.scan_iter(match=pattern, count=1000):
                    planeCount += 1
                    state = redis.hgetall(key)

                    last_update = float(state.get("last_update", state.get("lastUpdate", 0)) or 0)
                    age = now - last_update if last_update > 1e9 else 999999
                    
                    # Use same airborne logic as CoreProcessor/ApiPlanes
                    speed = float(state.get("speed", 0) or 0)
                    is_airborne = state.get("airborne") == "1" or speed >= 40
                    
                    # Check ground_cluster or ground source to match ApiPlanes _isGroundPlane
                    is_ground = bool(state.get("ground_cluster", state.get("groundCluster"))) or \
                                (state.get("source") == "adsblol-ground-sweep")

                    if is_airborne and not is_ground:
                        airborneCount += 1
                    else:
                        # Ground target: active vs parked
                        if age < 300:
                            onGroundCount += 1
                        else:
                            parkedCount += 1

                    source = state.get("source", "unknown")
                    sourceCounts[source] = sourceCounts.get(source, 0) + 1

            for _ in redis.scan_iter(match="adsblol:state:icao:*", count=1000):
                adsbLolCount += 1

            adsbLolHeartbeat = redis.hgetall("adsblol:heartbeat") or {}

        self.responseData = {
            "status": "ok" if redisOk else "degraded",
            "timestamp": time.time(),
            "redis": {
                "connected": redisOk
            },
            "planes": {
                "total": planeCount,
                "airborne": airborneCount,
                "onGround": onGroundCount,
                "parked": parkedCount
            },
            "sources": sourceCounts,
            "adsbLol": {
                "count": adsbLolCount,
                "heartbeat": adsbLolHeartbeat
            }
        }
        
        if not redisOk:
            self.responseCode = self.SERVICE_UNAVAILABLE
        
        self.sendResponse()
    
    def requiresGet(self):
        return True
