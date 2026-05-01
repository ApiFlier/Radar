import os
import time
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
            "description": "Filter by data source (e.g., adsb-lol-reapi, swim-sfdps, swim-stdds, opensky)",
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

        sourceCounts = {}
        planesByKey = {}
        icaoIndex = {}

        # Load existing FAA/SWIM/OpenSky state first.
        for key in redis.scan_iter(match="state:*", count=1000):
            flightId = key.split("state:", 1)[1]
            plane = self.loadPlane(redis, flightId)

            if not plane:
                continue

            source = plane.get("source", "unknown") or "unknown"
            sourceCounts[source] = sourceCounts.get(source, 0) + 1

            planeKey = plane.get("flightId") or flightId
            planesByKey[planeKey] = plane

            icao = (plane.get("icaoHex") or "").upper()
            if icao:
                icaoIndex[icao] = planeKey

        adsbHealthy = self.adsbLolHealthy(redis)

        # Load ADSB.lol state second. If healthy, it wins for live position.
        if adsbHealthy:
            for key in redis.scan_iter(match="adsblol:state:icao:*", count=1000):
                adsbPlane = self.loadAdsbLolPlane(redis, key)

                if not adsbPlane:
                    continue

                source = adsbPlane.get("source", "adsb-lol-reapi")
                sourceCounts[source] = sourceCounts.get(source, 0) + 1

                icao = (adsbPlane.get("icaoHex") or "").upper()

                if icao and icao in icaoIndex:
                    existingKey = icaoIndex[icao]
                    planesByKey[existingKey] = self.mergeAdsbPrimary(planesByKey[existingKey], adsbPlane)
                else:
                    newKey = adsbPlane.get("flightId") or f"adsblol:{icao}"
                    planesByKey[newKey] = adsbPlane
                    if icao:
                        icaoIndex[icao] = newKey

        planes = []

        for plane in planesByKey.values():
            if self.filterPlane(plane):
                plane.update(self.classifyAircraft(plane))
                planes.append(plane)

        planes.sort(key=lambda p: float(p.get("lastUpdate", 0)), reverse=True)

        self.debugMessage("execute", f"Found {len(planes)} planes")

        self.responseData = {
            "count": len(planes),
            "sources": sourceCounts,
            "adsbLolHealthy": adsbHealthy,
            "adsbLolHeartbeat": redis.hgetall("adsblol:heartbeat"),
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
            "registration": plane.get("registration", ""),
            "aircraftType": plane.get("aircraft_type", ""),
            "dep": plane.get("dep", ""),
            "arr": plane.get("arr", ""),
            "depTime": plane.get("dep_time", ""),
            "eta": plane.get("eta", ""),
            "faaTs": plane.get("faa_ts", ""),
            "flightStatus": plane.get("flight_status", ""),
            "icaoHex": plane.get("icao_hex", ""),
            "source": plane.get("source", ""),
            "positionSource": plane.get("source", ""),
            "enrichmentSource": plane.get("source", ""),
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

    def loadAdsbLolPlane(self, redis, key: str) -> dict:
        plane = redis.hgetall(key)

        if not plane:
            return None

        normalized = {
            "flightId": plane.get("flight_id", key.replace("adsblol:state:", "adsblol:")),
            "callsign": plane.get("callsign", ""),
            "operator": plane.get("operator", ""),
            "registration": plane.get("registration", ""),
            "aircraftType": plane.get("aircraft_type", ""),
            "dep": "",
            "arr": "",
            "depTime": "",
            "eta": "",
            "faaTs": "",
            "flightStatus": "",
            "icaoHex": plane.get("icao_hex", ""),
            "source": plane.get("source", "adsb-lol-reapi"),
            "positionSource": plane.get("source", "adsb-lol-reapi"),
            "enrichmentSource": "",
            "sourceFacility": plane.get("source_facility", ""),
            "trackKey": plane.get("track_key", ""),
            "gufi": "",
            "lat": self.safeFloat(plane.get("lat", 0)),
            "lon": self.safeFloat(plane.get("lon", 0)),
            "speed": self.safeFloat(plane.get("speed", 0)),
            "heading": self.safeFloat(plane.get("heading", 0)),
            "alt": self.safeFloat(plane.get("alt", 0)),
            "assignedAlt": "",
            "verticalRate": plane.get("vertical_rate", ""),
            "airborne": plane.get("airborne", "0"),
            "lastUpdate": self.safeFloat(plane.get("last_update", 0)),
            "squawk": plane.get("squawk", ""),
            "emergency": plane.get("emergency", ""),
            "category": plane.get("category", ""),
            "dbFlags": plane.get("db_flags", "")
        }

        if normalized["lat"] == 0 and normalized["lon"] == 0:
            return None

        return normalized

    def mergeAdsbPrimary(self, faaPlane: dict, adsbPlane: dict) -> dict:
        merged = dict(faaPlane)

        # ADSB.lol wins for live position and aircraft movement.
        for field in [
            "lat",
            "lon",
            "speed",
            "heading",
            "alt",
            "verticalRate",
            "airborne",
            "lastUpdate",
            "squawk",
            "emergency",
            "category",
            "dbFlags"
        ]:
            if field in adsbPlane:
                merged[field] = adsbPlane[field]

        # Prefer ADSB callsign/registration/type when present, keep FAA fields otherwise.
        for field in ["callsign", "operator", "registration", "aircraftType", "icaoHex"]:
            if adsbPlane.get(field):
                merged[field] = adsbPlane[field]

        merged["source"] = "adsb-lol-reapi"
        merged["positionSource"] = "adsb-lol-reapi"
        merged["enrichmentSource"] = faaPlane.get("source", "")
        merged["faaFlightId"] = faaPlane.get("flightId", "")
        merged["adsbFlightId"] = adsbPlane.get("flightId", "")
        merged["sourceFacility"] = adsbPlane.get("sourceFacility", "adsb.lol re-api")

        return merged

    def adsbLolHealthy(self, redis) -> bool:
        heartbeat = redis.hgetall("adsblol:heartbeat")

        if not heartbeat:
            return False

        maxAge = self.safeFloat(os.getenv("ADSBLOL_HEALTH_MAX_AGE_SECONDS", "45"), 45)

        lastSuccess = self.safeFloat(heartbeat.get("last_success", 0), 0)
        if lastSuccess <= 0:
            return False

        age = time.time() - lastSuccess
        if age > maxAge:
            return False

        if heartbeat.get("status") == "degraded":
            return False

        return True

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
            positionSource = plane.get("positionSource", "").lower()
            enrichmentSource = plane.get("enrichmentSource", "").lower()

            search = source.lower()
            if search not in planeSource and search not in positionSource and search not in enrichmentSource:
                return False

        near = params.get("near", "")
        if near:
            nearUpper = near.upper()
            if plane.get("dep", "").upper() != nearUpper and plane.get("arr", "").upper() != nearUpper:
                return False

        return True

    def classifyAircraft(self, plane: dict) -> dict:
        callsign = (plane.get("callsign") or "").strip().upper()
        operator = (plane.get("operator") or "").strip().upper()
        registration = (plane.get("registration") or "").strip().upper()
        aircraft_type = (plane.get("aircraftType") or plane.get("aircraft_type") or "").strip().upper()
        db_flags = self.safeInt(plane.get("dbFlags", plane.get("db_flags", 0)), 0)

        is_military = bool(db_flags & 1)
        is_pia = bool(db_flags & 4)
        is_ladd = bool(db_flags & 8)
        is_helicopter = self.isHelicopterType(aircraft_type)

        commercial_prefixes = {
            "AAL", "ACA", "AFR", "ASA", "ASH", "ATN", "AWI", "BAW", "DAL",
            "EDV", "ENY", "FFT", "FDX", "GJS", "JBU", "JIA", "KLM", "NKS",
            "QXE", "RPA", "SKW", "SWA", "UAL", "UPS", "VOI", "WJA", "AAY",
            "SCX", "UCA", "ROU", "DLH", "AUA", "JZA", "ENY", "PDT", "UAL"
        }

        prefix = operator[:3] if len(operator) >= 3 else callsign[:3]

        if is_military:
            aircraft_class = "military"
            aircraft_role = "military"
            icon_type = "military"
        elif is_helicopter:
            aircraft_class = "helicopter"
            aircraft_role = "helicopter"
            icon_type = "helicopter"
        elif prefix in commercial_prefixes:
            aircraft_class = "commercial"
            aircraft_role = "airline"
            icon_type = "commercial"
        elif registration.startswith("N") or callsign.startswith("N"):
            aircraft_class = "private"
            aircraft_role = "private"
            icon_type = "private"
        elif callsign:
            aircraft_class = "private"
            aircraft_role = "general"
            icon_type = "private"
        else:
            aircraft_class = "unknown"
            aircraft_role = "unknown"
            icon_type = "private"

        return {
            "aircraftClass": aircraft_class,
            "aircraftRole": aircraft_role,
            "iconType": icon_type,
            "isMilitary": is_military,
            "isPia": is_pia,
            "isLadd": is_ladd,
            "isHelicopter": is_helicopter,
        }

    def isHelicopterType(self, aircraft_type: str) -> bool:
        if not aircraft_type:
            return False

        helicopter_prefixes = (
            "H",
            "R22", "R44", "R66",
            "B06", "B47",
            "AS3", "AS5",
            "EC1", "EC2", "EC3", "EC4", "EC5",
            "BK1",
            "S61", "S64", "S76", "S92",
            "A109", "A119", "A139", "A169", "A189",
        )

        return aircraft_type.startswith(helicopter_prefixes)

    def safeInt(self, value, default=0) -> int:
        try:
            if value in (None, ""):
                return default
            return int(float(value))
        except (ValueError, TypeError):
            return default

    def safeFloat(self, value, default=0.0) -> float:
        try:
            return float(value) if value not in (None, "") else default
        except (ValueError, TypeError):
            return default

    def requiresGet(self):
        return True
