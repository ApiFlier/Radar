import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

import redis


REAPI_BASE = os.getenv("ADSBLOL_REAPI_BASE", "https://re-api.adsb.lol").rstrip("/")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

CLUSTER_INTERVAL_SECONDS = int(os.getenv("GROUND_SWEEP_CLUSTER_INTERVAL_SECONDS", "300"))
REQUEST_SPACING_SECONDS = float(os.getenv("GROUND_SWEEP_REQUEST_SPACING_SECONDS", "5"))
DEFAULT_RADIUS_NM = int(os.getenv("GROUND_SWEEP_RADIUS_NM", "250"))
GROUND_TTL_SECONDS = int(os.getenv("GROUND_SWEEP_TTL_SECONDS", "900"))
MAX_SEEN_POS_SECONDS = float(os.getenv("GROUND_SWEEP_MAX_SEEN_POS_SECONDS", "120"))

REDIS_STATE_PREFIX = os.getenv("ADSBLOL_STATE_PREFIX", "adsblol:state:icao:")


DEFAULT_CLUSTERS: List[Tuple[str, float, float, int]] = [
    ("SEA_PDX", 46.0, -122.3, DEFAULT_RADIUS_NM),
    ("SFO_SJC_OAK_SMF", 37.7, -121.8, DEFAULT_RADIUS_NM),
    ("LAX_SAN_BUR_ONT", 34.0, -117.8, DEFAULT_RADIUS_NM),
    ("LAS_PHX", 35.0, -114.6, DEFAULT_RADIUS_NM),
    ("SLC", 40.8, -112.0, DEFAULT_RADIUS_NM),
    ("DEN", 39.9, -104.7, DEFAULT_RADIUS_NM),
    ("DFW_DAL_OKC", 33.0, -97.1, DEFAULT_RADIUS_NM),
    ("IAH_HOU_SAT_AUS", 30.0, -96.0, DEFAULT_RADIUS_NM),
    ("MSP_MKE", 44.9, -92.8, DEFAULT_RADIUS_NM),
    ("ORD_MDW_IND", 41.9, -87.8, DEFAULT_RADIUS_NM),
    ("DTW_CLE_PIT_CMH", 41.3, -82.0, DEFAULT_RADIUS_NM),
    ("STL_MCI_MEM_BNA", 37.5, -90.2, DEFAULT_RADIUS_NM),
    ("ATL_BHM_CHS", 33.6, -84.4, DEFAULT_RADIUS_NM),
    ("CLT_RDU_GSO", 35.3, -80.3, DEFAULT_RADIUS_NM),
    ("MCO_TPA_JAX", 28.3, -81.5, DEFAULT_RADIUS_NM),
    ("MIA_FLL_PBI_RSW", 26.3, -80.6, DEFAULT_RADIUS_NM),
    ("IAD_DCA_BWI_RIC_ORF", 38.8, -77.0, DEFAULT_RADIUS_NM),
    ("PHL_EWR_JFK_LGA", 40.3, -74.6, DEFAULT_RADIUS_NM),
    ("BOS_BDL_PVD_ALB", 42.2, -72.5, DEFAULT_RADIUS_NM),
]


def log(message: str) -> None:
    print(f"[AdsbLolGroundSweep] {message}", flush=True)


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def fetch_circle(lat: float, lon: float, radius_nm: int) -> Dict[str, Any]:
    circle = f"{lat},{lon},{radius_nm}"
    url = f"{REAPI_BASE}/?circle={circle}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "MeeksRadarGroundSweep/1.0",
        },
        method="GET",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_groundish(ac: Dict[str, Any]) -> bool:
    lat = ac.get("lat")
    lon = ac.get("lon")
    if lat is None or lon is None:
        return False

    seen_pos = to_float(ac.get("seen_pos", ac.get("seen")), 999999)
    if seen_pos > MAX_SEEN_POS_SECONDS:
        return False

    alt_baro = str(ac.get("alt_baro", "")).strip().lower()
    altitude = str(ac.get("altitude", "")).strip().lower()
    gs = to_float(ac.get("gs", ac.get("speed")), 0)
    alt_geom = to_float(ac.get("alt_geom"), 0)

    if alt_baro == "ground" or altitude == "ground" or ac.get("ground") is True:
        return True

    return gs <= 25 and alt_geom <= 250


def normalize_ground_aircraft(ac: Dict[str, Any], cluster_name: str) -> Dict[str, Any] | None:
    hex_id = str(ac.get("hex", "")).strip().upper()
    if not hex_id:
        return None

    lat = ac.get("lat")
    lon = ac.get("lon")
    if lat is None or lon is None:
        return None

    seen = to_float(ac.get("seen_pos", ac.get("seen")), 0)
    now = time.time()
    last_update = now - seen if seen >= 0 else now

    callsign = str(ac.get("flight", "") or "").strip()
    speed = to_float(ac.get("gs", ac.get("speed")), 0)
    heading = to_float(ac.get("track", ac.get("heading")), 0)

    alt_baro = ac.get("alt_baro", "ground")
    alt = 0 if str(alt_baro).lower() == "ground" else to_float(alt_baro, 0)

    return {
        "flightId": f"adsblol-ground:{hex_id}",
        "icao_hex": hex_id,
        "icaoHex": hex_id,
        "callsign": callsign or hex_id,
        "registration": ac.get("r", "") or "",
        "aircraftType": ac.get("t", "") or "",
        "lat": lat,
        "lon": lon,
        "speed": speed,
        "heading": heading,
        "track": heading,
        "alt": alt,
        "alt_baro": alt_baro,
        "airborne": "false",
        "onGround": "true",
        "source": "adsblol-ground-sweep",
        "positionSource": "adsblol-ground-sweep",
        "sourceFacility": cluster_name,
        "groundCluster": cluster_name,
        "lastUpdate": last_update,
        "seen": ac.get("seen", ""),
        "seen_pos": ac.get("seen_pos", ""),
        "dbFlags": ac.get("dbFlags", ac.get("dbflags", 0)) or 0,
        "messages": ac.get("messages", ""),
        "rssi": ac.get("rssi", ""),
    }


def sanitize_mapping(mapping: Dict[str, Any]) -> Dict[str, str]:
    return {str(k): "" if v is None else str(v) for k, v in mapping.items()}


def store_ground_aircraft(r: redis.Redis, plane: Dict[str, Any]) -> None:
    key = f"{REDIS_STATE_PREFIX}{plane['icao_hex']}"
    r.hset(key, mapping=sanitize_mapping(plane))
    r.expire(key, GROUND_TTL_SECONDS)


def main() -> None:
    clusters = DEFAULT_CLUSTERS
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

    log(
        f"Starting. clusters={len(clusters)} "
        f"interval={CLUSTER_INTERVAL_SECONDS}s spacing={REQUEST_SPACING_SECONDS}s "
        f"ttl={GROUND_TTL_SECONDS}s redis={REDIS_HOST}:{REDIS_PORT}"
    )

    last_run = {name: 0.0 for name, _, _, _ in clusters}

    while True:
        did_work = False

        for name, lat, lon, radius_nm in clusters:
            now = time.time()
            if now - last_run.get(name, 0) < CLUSTER_INTERVAL_SECONDS:
                continue

            did_work = True
            last_run[name] = now

            try:
                data = fetch_circle(lat, lon, radius_nm)
                aircraft = data.get("aircraft") or data.get("ac") or []

                groundish = 0
                stored = 0

                for ac in aircraft:
                    if not isinstance(ac, dict):
                        continue
                    if not is_groundish(ac):
                        continue

                    groundish += 1
                    plane = normalize_ground_aircraft(ac, name)
                    if not plane:
                        continue

                    store_ground_aircraft(r, plane)
                    stored += 1

                log(f"{name}: aircraft={len(aircraft)} groundish={groundish} stored={stored}")

            except Exception as exc:
                log(f"{name}: ERROR {exc}")

            time.sleep(REQUEST_SPACING_SECONDS)

        if not did_work:
            time.sleep(5)


if __name__ == "__main__":
    main()
