#!/usr/bin/env python3
import json
import os
import time
import traceback
import urllib.parse
import urllib.request

import redis


REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

ADSBLOL_REAPI_URL = os.getenv("ADSBLOL_REAPI_URL", "https://re-api.adsb.lol/")
ADSBLOL_REAPI_CIRCLES = os.getenv("ADSBLOL_REAPI_CIRCLES", "40.491389,-80.232778,250")
ADSBLOL_REAPI_INTERVAL_SECONDS = float(os.getenv("ADSBLOL_REAPI_INTERVAL_SECONDS", "10"))
ADSBLOL_REAPI_TTL_SECONDS = int(os.getenv("ADSBLOL_REAPI_TTL_SECONDS", "45"))
ADSBLOL_REAPI_REQUEST_SPACING_SECONDS = float(os.getenv("ADSBLOL_REAPI_REQUEST_SPACING_SECONDS", "1.2"))
ADSBLOL_REAPI_TIMEOUT_SECONDS = float(os.getenv("ADSBLOL_REAPI_TIMEOUT_SECONDS", "20"))

SOURCE_NAME = "adsb-lol-reapi"


def log(msg):
    print(f"[adsblol-reapi] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def redis_client():
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True,
        socket_keepalive=True,
        health_check_interval=30,
    )


def split_circles(raw):
    circles = []
    for part in raw.split(";"):
        part = part.strip()
        if part:
            circles.append(part)
    return circles


def build_url(circle):
    base = ADSBLOL_REAPI_URL.rstrip("/")
    encoded_circle = urllib.parse.quote(circle, safe=",.-")
    return f"{base}/?circle={encoded_circle}"


def safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def normalize_alt(value):
    if value is None:
        return 0.0, "0"

    if isinstance(value, str) and value.lower() == "ground":
        return 0.0, "1"

    alt = safe_float(value, 0.0)
    return alt, "0"


def normalize_aircraft(ac, source_now):
    hex_id = str(ac.get("hex") or "").strip().upper()
    if not hex_id:
        return None

    lat = ac.get("lat")
    lon = ac.get("lon")
    if lat is None or lon is None:
        return None

    lat = safe_float(lat)
    lon = safe_float(lon)

    if lat == 0 and lon == 0:
        return None

    alt, is_ground_alt = normalize_alt(ac.get("alt_baro", ac.get("altitude")))
    speed = safe_float(ac.get("gs", ac.get("speed", 0)))
    heading = safe_float(ac.get("track", 0))
    vertical_rate = ac.get("baro_rate", ac.get("geom_rate", ""))

    seen = safe_float(ac.get("seen"), 0)
    seen_pos = safe_float(ac.get("seen_pos"), seen)
    position_age = seen_pos if seen_pos else seen
    last_update = source_now - position_age if source_now else time.time()

    airborne = "0" if is_ground_alt == "1" else "1"
    if speed < 30 and alt < 500:
        airborne = "0"

    callsign = str(ac.get("flight") or "").strip()

    return {
        "flight_id": f"adsblol:icao:{hex_id}",
        "icao_hex": hex_id,
        "callsign": callsign,
        "operator": callsign[:3] if len(callsign) >= 3 else "",
        "registration": str(ac.get("r") or "").strip(),
        "aircraft_type": str(ac.get("t") or "").strip(),
        "lat": str(lat),
        "lon": str(lon),
        "speed": str(speed),
        "heading": str(heading),
        "alt": str(alt),
        "alt_baro": str(ac.get("alt_baro", "")),
        "alt_geom": str(ac.get("alt_geom", "")),
        "vertical_rate": str(vertical_rate),
        "squawk": str(ac.get("squawk", "")),
        "airborne": airborne,
        "source": SOURCE_NAME,
        "source_facility": "adsb.lol re-api",
        "track_key": f"adsblol:icao:{hex_id}",
        "last_update": str(last_update),
        "seen": str(seen),
        "seen_pos": str(seen_pos),
        "db_flags": str(ac.get("dbFlags", ac.get("dbflags", ""))),
        "emergency": str(ac.get("emergency", "")),
        "category": str(ac.get("category", "")),
        "messages": str(ac.get("messages", "")),
        "rssi": str(ac.get("rssi", "")),
    }


def fetch_circle(circle):
    url = build_url(circle)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "RadarADSBLOLReAPI/1.0",
        },
    )

    started = time.time()
    with urllib.request.urlopen(req, timeout=ADSBLOL_REAPI_TIMEOUT_SECONDS) as response:
        raw = response.read()
        elapsed = time.time() - started

    data = json.loads(raw.decode("utf-8", errors="replace"))
    aircraft = data.get("aircraft") or data.get("ac") or []
    source_now = safe_float(data.get("now"), time.time())

    return {
        "url": url,
        "elapsed": elapsed,
        "bytes": len(raw),
        "count": len(aircraft),
        "source_now": source_now,
        "aircraft": aircraft,
        "ptime": data.get("ptime", ""),
    }


def store_aircraft(r, normalized):
    key = f"adsblol:state:icao:{normalized['icao_hex']}"
    r.hset(key, mapping=normalized)
    r.expire(key, ADSBLOL_REAPI_TTL_SECONDS)


def update_heartbeat(r, mapping):
    r.hset("adsblol:heartbeat", mapping=mapping)
    r.expire("adsblol:heartbeat", 300)


def main():
    circles = split_circles(ADSBLOL_REAPI_CIRCLES)

    if not circles:
        raise SystemExit("No ADSBLOL_REAPI_CIRCLES configured")

    log(f"Starting. Redis={REDIS_HOST}:{REDIS_PORT} circles={len(circles)} interval={ADSBLOL_REAPI_INTERVAL_SECONDS}s ttl={ADSBLOL_REAPI_TTL_SECONDS}s")

    r = redis_client()
    consecutive_errors = 0

    while True:
        cycle_started = time.time()
        total_seen = 0
        total_stored = 0
        last_error = ""

        for idx, circle in enumerate(circles):
            try:
                result = fetch_circle(circle)
                stored = 0

                for ac in result["aircraft"]:
                    normalized = normalize_aircraft(ac, result["source_now"])
                    if not normalized:
                        continue
                    store_aircraft(r, normalized)
                    stored += 1

                total_seen += result["count"]
                total_stored += stored
                consecutive_errors = 0

                update_heartbeat(r, {
                    "source": SOURCE_NAME,
                    "status": "healthy",
                    "last_success": str(time.time()),
                    "last_circle": circle,
                    "last_count": str(result["count"]),
                    "last_stored": str(stored),
                    "last_elapsed": f"{result['elapsed']:.3f}",
                    "last_bytes": str(result["bytes"]),
                    "last_ptime": str(result["ptime"]),
                    "total_seen": str(total_seen),
                    "total_stored": str(total_stored),
                    "circle_count": str(len(circles)),
                    "consecutive_errors": str(consecutive_errors),
                })

                log(f"circle={circle} count={result['count']} stored={stored} elapsed={result['elapsed']:.2f}s bytes={result['bytes']}")

            except Exception as exc:
                consecutive_errors += 1
                last_error = f"{type(exc).__name__}: {exc}"
                log(f"ERROR circle={circle} {last_error}")
                traceback.print_exc()

                update_heartbeat(r, {
                    "source": SOURCE_NAME,
                    "status": "degraded",
                    "last_error": last_error,
                    "last_error_time": str(time.time()),
                    "consecutive_errors": str(consecutive_errors),
                    "circle_count": str(len(circles)),
                })

            if idx < len(circles) - 1:
                time.sleep(ADSBLOL_REAPI_REQUEST_SPACING_SECONDS)

        elapsed = time.time() - cycle_started
        sleep_for = max(1.0, ADSBLOL_REAPI_INTERVAL_SECONDS - elapsed)

        log(f"cycle complete seen={total_seen} stored={total_stored} elapsed={elapsed:.2f}s sleep={sleep_for:.2f}s errors={consecutive_errors}")

        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
