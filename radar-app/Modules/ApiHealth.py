import time
from Classes.ApiBase import ApiBase
from Classes.Redis import getRedis
from Modules.ApiPlanes import get_planes_snapshot


class ApiHealth(ApiBase):

    title = "Health"
    description = "Health check endpoint - returns Redis status and plane counts"

    apiParameters = {}

    def execute(self):
        redis = getRedis()
        redisOk = redis.ping()

        adsbLolHeartbeat = redis.hgetall("adsblol:heartbeat") or {} if redisOk else {}

        # Fast path: reuse the shared plane snapshot when it is warm, skipping
        # the expensive double Redis scan that ApiHealth previously ran on its own.
        snap = get_planes_snapshot() if redisOk else None
        if snap is not None:
            self.responseData = self._buildFromSnapshot(snap, redisOk, adsbLolHeartbeat)
            self.sendResponse()
            return

        # Slow path: no warm snapshot — fall back to direct Redis scan.
        planeCount = 0
        airborneCount = 0
        onGroundCount = 0
        parkedCount = 0
        sourceCounts = {}
        adsbLolCount = 0

        now = time.time()
        if redisOk:
            planesByKey = {}
            icaoIndex = {}

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

            for key in redis.scan_iter(match="adsblol:state:icao:*", count=1000):
                adsbState = redis.hgetall(key)
                if not adsbState:
                    continue
                adsbLolCount += 1
                icao = (adsbState.get("icao_hex", adsbState.get("icaoHex", "")) or "").upper()
                if not (icao and icao in icaoIndex):
                    newKey = adsbState.get("flightId") or f"adsblol:{icao}"
                    planesByKey[newKey] = adsbState

            for state in planesByKey.values():
                has_identity = bool(
                    (state.get("icao_hex", state.get("icaoHex", "")) or "").strip()
                    or (state.get("callsign") or "").strip()
                    or (state.get("registration") or "").strip()
                    or (state.get("flight_id", state.get("flightId", "")) or "").strip()
                )
                has_route  = bool((state.get("dep") or "").strip() or (state.get("arr") or "").strip())
                has_source = bool((state.get("source") or "").strip())
                if not has_source and not has_identity and not has_route:
                    continue

                planeCount += 1
                last_update = float(state.get("last_update", state.get("lastUpdate", 0)) or 0)
                age = now - last_update if last_update > 1e9 else 999999
                speed = float(state.get("speed", 0) or 0)
                is_airborne = state.get("airborne") == "1" or speed >= 40
                is_ground = bool(state.get("ground_cluster", state.get("groundCluster"))) or \
                            state.get("source") == "adsblol-ground-sweep"

                if is_airborne and not is_ground:
                    airborneCount += 1
                elif age < 300:
                    onGroundCount += 1
                else:
                    parkedCount += 1

                source = state.get("source", "unknown")
                sourceCounts[source] = sourceCounts.get(source, 0) + 1

        self.responseData = {
            "status": "ok" if redisOk else "degraded",
            "timestamp": time.time(),
            "redis": {"connected": redisOk},
            "planes": {
                "total":    planeCount,
                "airborne": airborneCount,
                "onGround": onGroundCount,
                "parked":   parkedCount,
            },
            "sources":  sourceCounts,
            "adsbLol":  {"count": adsbLolCount, "heartbeat": adsbLolHeartbeat},
        }
        if not redisOk:
            self.responseCode = self.SERVICE_UNAVAILABLE
        self.sendResponse()

    def _buildFromSnapshot(self, snap: dict, redisOk: bool, adsbLolHeartbeat: dict) -> dict:
        """Build the health response from the shared planes snapshot (no Redis scan)."""
        planes       = snap["planes"]
        sourceCounts = snap["sourceCounts"]
        now          = time.time()

        planeCount    = len(planes)
        airborneCount = 0
        onGroundCount = 0
        parkedCount   = 0
        adsbLolCount  = 0

        for p in planes:
            src = (p.get("source") or "").lower()
            if "adsblol" in src or "adsb-lol" in src:
                adsbLolCount += 1

            is_ground   = bool(p.get("groundCluster")) or "ground-sweep" in src
            is_live     = bool(p.get("isLiveGround"))
            speed       = float(p.get("speed") or 0)
            is_airborne = p.get("airborne") == "1" or speed >= 40

            if is_airborne and not is_ground:
                airborneCount += 1
            elif is_live or (now - float(p.get("lastUpdate") or 0)) < 300:
                onGroundCount += 1
            else:
                parkedCount += 1

        return {
            "status":    "ok" if redisOk else "degraded",
            "timestamp": now,
            "redis":     {"connected": redisOk},
            "planes": {
                "total":    planeCount,
                "airborne": airborneCount,
                "onGround": onGroundCount,
                "parked":   parkedCount,
            },
            "sources": sourceCounts,
            "adsbLol": {"count": adsbLolCount, "heartbeat": adsbLolHeartbeat},
        }

    def requiresGet(self):
        return True
