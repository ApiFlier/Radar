import os
import time
import threading
from Classes.ApiBase import ApiBase
from Classes.Redis import getRedis

# ── In-process summary cache (15 s TTL) ──────────────────────────────────────
_summary_cache_lock = threading.Lock()
_summary_cache      = None
_summary_cache_ts   = 0.0
_SUMMARY_CACHE_TTL  = 15.0   # seconds


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
        },
        "airport": {
            "description": "Airport code for airport-focused view (e.g., KPIT, KJFK)",
            "required": False,
            "type": "string"
        },
        "view": {
            "description": "Response shape: radar, target, summary, airports (alias for summary), ground, alerts, table. Omit for full response.",
            "required": False,
            "type": "string"
        },
        "limit": {
            "description": "Cap the number of planes returned (e.g., 500). Applied after filters and sort.",
            "required": False,
            "type": "integer",
            "default": 0
        },
        "minLat": {
            "description": "Minimum latitude for radar bounds filter (decimal degrees).",
            "required": False,
            "type": "float"
        },
        "maxLat": {
            "description": "Maximum latitude for radar bounds filter (decimal degrees).",
            "required": False,
            "type": "float"
        },
        "minLon": {
            "description": "Minimum longitude for radar bounds filter (decimal degrees).",
            "required": False,
            "type": "float"
        },
        "maxLon": {
            "description": "Maximum longitude for radar bounds filter (decimal degrees).",
            "required": False,
            "type": "float"
        },
        "icao": {
            "description": "ICAO hex for view=target single-aircraft lookup.",
            "required": False,
            "type": "string"
        },
        "callsign": {
            "description": "Callsign for view=target single-aircraft lookup.",
            "required": False,
            "type": "string"
        },
        "scope": {
            "description": "For view=radar: 'global' skips bounds filtering and returns all fresh aircraft.",
            "required": False,
            "type": "string"
        },
        "page": {
            "description": "1-based page number for view=table server-side paging.",
            "required": False,
            "type": "integer"
        },
        "pageSize": {
            "description": "Page size for view=table server-side paging. 0 = no limit (legacy).",
            "required": False,
            "type": "integer"
        },
        "q": {
            "description": "Search query for view=table: matched against callsign, reg, ICAO, dep, arr, operator, type.",
            "required": False,
            "type": "string"
        },
        "aircraftClass": {
            "description": "Class filter for view=table: commercial/private/military/helicopter/unknown/ground.",
            "required": False,
            "type": "string"
        },
        "category": {
            "description": "For view=alerts: 'operational' returns only flagged aircraft plus DQ summary metadata.",
            "required": False,
            "type": "string"
        }
    }

    def execute(self):
        self.debugMessage("execute", "Starting Planes request")

        view = (self.params.get("view") or "").strip().lower()

        # Fast path for summary/airports: return cached result without scanning Redis.
        # The cache is populated on the first scan; subsequent calls within the TTL are
        # nearly instant, which prevents the Redis scan from serialising concurrent requests.
        if view in ("summary", "airports"):
            with _summary_cache_lock:
                _now = time.time()
                if _summary_cache is not None and (_now - _summary_cache_ts) < _SUMMARY_CACHE_TTL:
                    self.responseData = _summary_cache
                    self.sendResponse(self.SUCCESS)
                    return

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
                    merged = self.mergeAdsbPrimary(planesByKey[existingKey], adsbPlane)
                    planesByKey[existingKey] = merged

                    # Persist stable identity fields to the FAA profile so they survive
                    # the ADSB.lol TTL (catches cases where ingestor ran before FAA correlation existed).
                    aircraft_type = (merged.get("aircraftType") or "").strip()
                    registration = (merged.get("registration") or "").strip()
                    db_flags = (str(merged.get("dbFlags") or "")).strip()
                    rc = redis.client
                    if aircraft_type:
                        rc.hsetnx(f"profile:{existingKey}", "aircraft_type", aircraft_type)
                    if registration:
                        rc.hsetnx(f"profile:{existingKey}", "registration", registration)
                    if db_flags:
                        rc.hset(f"profile:{existingKey}", "db_flags", db_flags)
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

        limit = self.safeInt(self.params.get("limit", 0), 0)

        if view in ("summary", "airports"):
            self.responseData = self._buildSummary(planes, sourceCounts, adsbHealthy, redis)
            self.sendResponse(self.SUCCESS)
            return

        total_count = len(planes)

        if view == "table":
            self.responseData = self._buildTableView(planes, total_count, sourceCounts, adsbHealthy, redis)
            self.sendResponse(self.SUCCESS)
            return

        if view == "alerts":
            cat = (self.params.get("category") or "").strip().lower()
            if cat == "operational":
                self.responseData = self._buildAlertsOperational(planes, total_count)
                self.sendResponse(self.SUCCESS)
                return

        if view == "airport":
            airport = (self.params.get("airport") or self.params.get("near") or "").strip().upper()
            self.responseData = self._buildAirportView(planes, airport, total_count)
            self.sendResponse(self.SUCCESS)
            return

        if view == "radar":
            # Params are already validated floats by _validateParams (None if absent, no default set)
            min_lat = self.params.get("minLat")
            max_lat = self.params.get("maxLat")
            min_lon = self.params.get("minLon")
            max_lon = self.params.get("maxLon")
            scope = (self.params.get("scope") or "").strip().lower()
            self.responseData = self._buildRadarView(planes, min_lat, max_lat, min_lon, max_lon, total_count, scope)
            self.sendResponse(self.SUCCESS)
            return

        if view == "target":
            icao_t = (self.params.get("icao") or "").strip().upper()
            call_t = (self.params.get("callsign") or "").strip().upper()
            self.responseData = self._buildTargetView(planes, icao_t, call_t, total_count)
            self.sendResponse(self.SUCCESS)
            return

        if view == "ground":
            planes = [p for p in planes if self._isGroundPlane(p)]
            planes = self._pickFields(planes, "ground")
        elif view == "alerts":
            planes = self._pickFields(planes, "alerts")
        elif limit > 0:
            planes = planes[:limit]

        self.responseData = {
            "count": len(planes),
            "total": total_count,
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

        _state_src   = (state.get("source", "") or "").strip()
        _profile_src = (profile.get("source", "") or "").strip()
        _src = _state_src or _profile_src

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
            "source": _src,
            "positionSource": _src,
            "enrichmentSource": _profile_src,
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

        # Drop empty zombie records that have a position but no useful identity,
        # no route, and no provenance. These create misleading "Unknown source"
        # counts without adding operational value.
        has_identity = bool(
            (normalized.get("icaoHex") or "").strip()
            or (normalized.get("callsign") or "").strip()
            or (normalized.get("registration") or "").strip()
            or (normalized.get("flightId") or "").strip()
        )
        has_route = bool((normalized.get("dep") or "").strip() or (normalized.get("arr") or "").strip())
        has_source = bool((normalized.get("source") or "").strip())

        if not has_source and not has_identity and not has_route:
            return None

        if not has_source:
            normalized["source"] = "legacy-state"
            normalized["positionSource"] = "legacy-state"
            normalized["sourceFacility"] = normalized.get("sourceFacility") or "unprovenanced"

        return normalized

    def loadAdsbLolPlane(self, redis, key: str) -> dict:
        plane = redis.hgetall(key)

        if not plane:
            return None

        normalized = {
            "flightId": plane.get("flightId", plane.get("flight_id", key.replace("adsblol:state:", "adsblol:"))),
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
            "sourceFacility": plane.get("sourceFacility", plane.get("source_facility", "")),
            "groundCluster": plane.get("groundCluster", plane.get("ground_cluster", "")),
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
            "lastUpdate": self.safeFloat(plane.get("lastUpdate", plane.get("last_update", 0))),
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

    _GROUND_FIELDS = frozenset({
        "flightId", "callsign", "icaoHex", "registration", "aircraftType",
        "lat", "lon", "speed", "heading", "alt", "lastUpdate",
        "source", "positionSource", "sourceFacility", "groundCluster",
        "aircraftClass", "aircraftRole", "iconType",
        "isMilitary", "isPia", "isLadd", "isHelicopter",
    })

    _ALERTS_FIELDS = frozenset({
        "flightId", "callsign", "icaoHex", "registration", "aircraftType",
        "lat", "lon", "speed", "heading", "alt", "lastUpdate",
        "source", "positionSource", "enrichmentSource", "sourceFacility", "groundCluster",
        "airborne", "squawk", "emergency", "dep", "arr", "operator", "dbFlags",
        "aircraftClass", "aircraftRole", "iconType",
        "isMilitary", "isPia", "isLadd", "isHelicopter",
    })

    _TABLE_FIELDS = frozenset({
        "flightId", "callsign", "icaoHex", "registration", "aircraftType",
        "lat", "lon", "speed", "heading", "alt", "lastUpdate",
        "source", "positionSource", "enrichmentSource", "sourceFacility", "groundCluster",
        "airborne", "squawk", "emergency", "dep", "arr", "operator", "dbFlags",
        "aircraftClass", "aircraftRole", "iconType",
        "isMilitary", "isPia", "isLadd", "isHelicopter",
        "depTime", "eta", "flightStatus", "assignedAlt", "verticalRate",
    })

    # Freshness thresholds — must match index.html VISIBLE_AIR_MAX_AGE / VISIBLE_GROUND_MAX_AGE
    _AIR_MAX_AGE    = 120   # seconds — radar display cutoff for airborne aircraft
    _GROUND_MAX_AGE = 900   # seconds — radar display cutoff for ground-sweep aircraft
    _DQ_STALE_SECS  = 180   # seconds — DQ stale flag (broader than radar cutoff; matches alerts.html STALE_SECS)
    _RADAR_PAD      = 0.5   # degrees lat/lon padding around requested bounds

    _RADAR_FIELDS = frozenset({
        "flightId", "callsign", "icaoHex", "registration", "aircraftType",
        "lat", "lon", "alt", "speed", "heading", "verticalRate",
        "aircraftClass", "aircraftRole", "iconType",
        "dep", "arr", "operator",
        "source", "positionSource", "enrichmentSource", "sourceFacility",
        "isMilitary", "isHelicopter", "isLadd", "isPia",
        "airborne", "lastUpdate", "assignedAlt", "squawk", "emergency",
        "groundCluster",
    })

    def _isGroundPlane(self, plane: dict) -> bool:
        return bool(plane.get("groundCluster"))

    def _pickFields(self, planes: list, view: str) -> list:
        if view == "ground":
            fields = self._GROUND_FIELDS
        elif view == "alerts":
            fields = self._ALERTS_FIELDS
        else:
            fields = self._TABLE_FIELDS
        return [{k: v for k, v in p.items() if k in fields} for p in planes]

    def _buildAirportView(self, planes: list, airport: str, total_count: int) -> dict:
        airport = (airport or "").strip().upper()
        if not airport:
            return {
                "airport": "",
                "count": 0,
                "total": total_count,
                "inbound": [],
                "outbound": [],
                "message": "Missing airport parameter",
            }

        inbound = []
        outbound = []

        for p in planes:
            dep = (p.get("dep") or "").strip().upper()
            arr = (p.get("arr") or "").strip().upper()

            if arr == airport:
                inbound.append(p)
            if dep == airport:
                outbound.append(p)

        inbound.sort(key=lambda p: self.safeFloat(p.get("lastUpdate", 0)), reverse=True)
        outbound.sort(key=lambda p: self.safeFloat(p.get("lastUpdate", 0)), reverse=True)

        return {
            "airport": airport,
            "count": len(inbound) + len(outbound),
            "total": total_count,
            "inboundCount": len(inbound),
            "outboundCount": len(outbound),
            "inbound": self._pickFields(inbound[:50], "table"),
            "outbound": self._pickFields(outbound[:50], "table"),
        }

    def _buildRadarView(self, planes: list, min_lat, max_lat, min_lon, max_lon, total_count: int, scope: str = "") -> dict:
        """Return only fresh, positioned aircraft within optional bounds — optimized for map rendering.
        scope='global' skips bounds filtering to return all fresh aircraft nationwide."""
        now = time.time()
        is_global = (scope == "global")
        has_bounds = not is_global and all(v is not None for v in [min_lat, max_lat, min_lon, max_lon])
        pad = self._RADAR_PAD

        result = []
        for p in planes:
            lat = self.safeFloat(p.get("lat"))
            lon = self.safeFloat(p.get("lon"))
            if lat == 0.0 and lon == 0.0:
                continue

            last_update = self.safeFloat(p.get("lastUpdate", 0))
            age = (now - last_update) if last_update > 1e9 else 999999
            is_airborne = str(p.get("airborne", "")) == "1" or self.safeFloat(p.get("speed", 0)) > 40
            max_age = self._AIR_MAX_AGE if is_airborne else self._GROUND_MAX_AGE
            if age > max_age:
                continue

            if has_bounds:
                if lat < (min_lat - pad) or lat > (max_lat + pad):
                    continue
                if lon < (min_lon - pad) or lon > (max_lon + pad):
                    continue

            result.append({k: v for k, v in p.items() if k in self._RADAR_FIELDS})

        return {
            "count": len(result),
            "total": total_count,
            "meta": {
                "countReturned": len(result),
                "totalAvailable": total_count,
                "scope": scope or "local",
                "padDeg": pad if has_bounds else None,
                "boundsUsed": {
                    "minLat": min_lat, "maxLat": max_lat,
                    "minLon": min_lon, "maxLon": max_lon,
                } if has_bounds else None,
            },
            "planes": result,
        }

    def _buildTargetView(self, planes: list, icao: str, callsign: str, total_count: int) -> dict:
        """Return at most one aircraft matching the given ICAO hex or callsign — for deep-link resolution."""
        for p in planes:
            if icao and (p.get("icaoHex") or "").upper() == icao:
                return {"count": 1, "total": total_count, "planes": [p]}
            if callsign and (p.get("callsign") or "").upper() == callsign:
                return {"count": 1, "total": total_count, "planes": [p]}
        return {"count": 0, "total": total_count, "planes": []}

    def _buildTableView(self, planes: list, total_count: int, sourceCounts: dict, adsbHealthy: bool, redis) -> dict:
        """Server-side filtered and paged table view for the aircraft page."""
        q          = (self.params.get("q") or "").strip().lower()
        ac_class   = (self.params.get("aircraftClass") or "").strip().lower()
        page       = max(1, self.safeInt(self.params.get("page") or 1, 1))
        page_size  = self.safeInt(self.params.get("pageSize") or 0, 0)
        limit      = self.safeInt(self.params.get("limit") or 0, 0)

        filtered = planes
        if q or ac_class:
            out = []
            for p in planes:
                src_s    = ((p.get("source") or "") + (p.get("positionSource") or "")).lower()
                is_ground = "ground" in src_s
                if ac_class:
                    if ac_class == "ground":
                        if not is_ground:
                            continue
                    else:
                        if is_ground or (p.get("aircraftClass") or "unknown") != ac_class:
                            continue
                if q:
                    hay = " ".join([
                        p.get("callsign") or "", p.get("registration") or "",
                        p.get("icaoHex") or "", p.get("dep") or "",
                        p.get("arr") or "", p.get("operator") or "",
                        p.get("aircraftType") or "", p.get("source") or "",
                    ]).lower()
                    if q not in hay:
                        continue
                out.append(p)
            filtered = out

        total_matching = len(filtered)

        if page_size > 0:
            start  = (page - 1) * page_size
            paged  = filtered[start:start + page_size]
            has_more = (start + page_size) < total_matching
        elif limit > 0:
            paged    = filtered[:limit]
            has_more = False
        else:
            paged    = filtered
            has_more = False

        return {
            "count":          len(paged),
            "total":          total_count,
            "totalMatching":  total_matching,
            "page":           page,
            "pageSize":       page_size,
            "hasMore":        has_more,
            "planes":         self._pickFields(paged, "table"),
            "sources":        sourceCounts,
            "adsbLolHealthy": adsbHealthy,
            "adsbLolHeartbeat": redis.hgetall("adsblol:heartbeat"),
        }

    _EMRG_SQUAWKS = frozenset({"7500", "7600", "7700"})

    def _buildAlertsOperational(self, planes: list, total_count: int) -> dict:
        """Return only operationally flagged aircraft and DQ summary counts as metadata.
        Reduces payload vs returning all aircraft for the full alerts scan."""
        now_ts = time.time()
        dq = {"noId": 0, "noRoute": 0, "unknownSrc": 0, "stale": 0}
        op = []

        for p in planes:
            # DQ counting — scan all planes for summary
            call     = (p.get("callsign") or "").upper()
            icao     = (p.get("icaoHex") or "").upper()
            reg      = (p.get("registration") or "").strip()
            no_id    = bool(call and call == icao and not reg)
            if no_id:
                dq["noId"] += 1

            src_s     = ((p.get("source") or "") + (p.get("positionSource") or "")).lower()
            is_ground = "ground" in src_s
            dep       = (p.get("dep") or "").strip().upper()
            arr       = (p.get("arr") or "").strip().upper()
            dep_ok    = 3 <= len(dep) <= 4 and dep.isalpha()
            arr_ok    = 3 <= len(arr) <= 4 and arr.isalpha()
            if p.get("aircraftClass") == "commercial" and not is_ground and not (dep_ok or arr_ok):
                dq["noRoute"] += 1

            raw_src = (p.get("source") or p.get("positionSource") or "").lower().strip()
            if not raw_src or raw_src == "unknown":
                dq["unknownSrc"] += 1

            lu = self.safeFloat(p.get("lastUpdate", 0))
            if lu > 1e9 and (now_ts - lu) > self._DQ_STALE_SECS:
                dq["stale"] += 1

            # Operational filter — include only flagged aircraft
            squawk   = str(p.get("squawk") or "").strip()
            emrg     = str(p.get("emergency") or "").lower().strip()
            is_emrg  = squawk in self._EMRG_SQUAWKS or (emrg and emrg not in ("0", "none", ""))
            if is_emrg or p.get("isMilitary") or p.get("isHelicopter") or is_ground:
                op.append(p)

        return {
            "count":      len(op),
            "total":      total_count,
            "dqSummary":  dq,
            "planes":     self._pickFields(op, "alerts"),
        }

    def _buildSummary(self, planes: list, sourceCounts: dict, adsbHealthy: bool, redis) -> dict:
        global _summary_cache, _summary_cache_ts
        now = time.time()
        with _summary_cache_lock:
            if _summary_cache is not None and (now - _summary_cache_ts) < _SUMMARY_CACHE_TTL:
                return _summary_cache

        result = self._computeSummary(planes, sourceCounts, adsbHealthy, redis)

        with _summary_cache_lock:
            _summary_cache    = result
            _summary_cache_ts = time.time()

        return result

    def _computeSummary(self, planes: list, sourceCounts: dict, adsbHealthy: bool, redis) -> dict:
        airborne = 0
        on_ground = 0
        ground_sweep = 0
        class_counts = {"commercial": 0, "private": 0, "military": 0, "helicopter": 0, "ground": 0, "unknown": 0}
        route_coverage = {"withRoute": 0, "none": 0}
        airline_counts = {}
        origin_counts = {}
        dest_counts = {}

        for p in planes:
            if self._isGroundPlane(p):
                ground_sweep += 1
                class_counts["ground"] += 1
            else:
                ac = p.get("aircraftClass", "unknown")
                class_counts[ac] = class_counts.get(ac, 0) + 1
                if p.get("airborne") == "1" or self.safeFloat(p.get("speed", 0)) >= 40:
                    airborne += 1
                else:
                    on_ground += 1

            dep = (p.get("dep") or "").strip().upper()
            arr = (p.get("arr") or "").strip().upper()
            if dep or arr:
                route_coverage["withRoute"] += 1
            else:
                route_coverage["none"] += 1

            op = (p.get("operator") or "").strip().upper()
            if op and p.get("aircraftClass") == "commercial":
                airline_counts[op] = airline_counts.get(op, 0) + 1

            if dep:
                origin_counts[dep] = origin_counts.get(dep, 0) + 1
            if arr:
                dest_counts[arr] = dest_counts.get(arr, 0) + 1

        airline_counts = dict(sorted(airline_counts.items(), key=lambda x: x[1], reverse=True)[:50])
        origin_counts = dict(sorted(origin_counts.items(), key=lambda x: x[1], reverse=True)[:100])
        dest_counts = dict(sorted(dest_counts.items(), key=lambda x: x[1], reverse=True)[:100])

        return {
            "total": len(planes),
            "airborne": airborne,
            "onGround": on_ground,
            "groundSweep": ground_sweep,
            "classCounts": class_counts,
            "routeCoverage": route_coverage,
            "airlineCounts": airline_counts,
            "originCounts": origin_counts,
            "destCounts": dest_counts,
            "sources": sourceCounts,
            "adsbLolHealthy": adsbHealthy,
            "adsbLolHeartbeat": redis.hgetall("adsblol:heartbeat"),
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
