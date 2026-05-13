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
            # Deduplicate planes across sources to match ApiPlanes logic
            planesByKey = {}
            icaoIndex = {}

            # First scan primary state
            for key in redis.scan_iter(match="state:*", count=1000):
                flightId = key.split("state:", 1)[1]
                state = redis.hgetall(key)
                if not state:
                    continue
                
                planeKey = state.get("flightId") or flightId
                planesByKey[planeKey] = state
                icao = (state.get("icao_hex", state.get("icaoHex", "")) or "").upper()
                if icao:
                    icaoIndex[icao] = planeKey

            # Then scan ADSB.lol state and merge
            for key in redis.scan_iter(match="adsblol:state:icao:*", count=1000):
                adsbState = redis.hgetall(key)
                if not adsbState:
                    continue
                
                adsbLolCount += 1
                icao = (adsbState.get("icao_hex", adsbState.get("icaoHex", "")) or "").upper()
                
                if icao and icao in icaoIndex:
                    # Duplicate: already exists in primary state, skip for plane count
                    # but we can optionally merge if we wanted perfect parity. 
                    # For Health, we just want accurate counts.
                    pass
                else:
                    newKey = adsbState.get("flightId") or f"adsblol:{icao}"
                    planesByKey[newKey] = adsbState

            # Now count the merged set
            for state in planesByKey.values():
                # Drop empty zombie records to match ApiPlanes logic
                has_identity = bool(
                    (state.get("icao_hex", state.get("icaoHex", "")) or "").strip()
                    or (state.get("callsign") or "").strip()
                    or (state.get("registration") or "").strip()
                    or (state.get("flight_id", state.get("flightId", "")) or "").strip()
                )
                has_route = bool((state.get("dep") or "").strip() or (state.get("arr") or "").strip())
                has_source = bool((state.get("source") or "").strip())

                if not has_source and not has_identity and not has_route:
                    continue

                planeCount += 1
                
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
