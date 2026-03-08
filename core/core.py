import os
import time
import json
import logging
import redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Flight-Core")

AIRBORNE_SPEED = 40

PROFILE_TTL = 7 * 24 * 3600
STATE_AIR_TTL = 10 * 60
STATE_GROUND_TTL = 30 * 60
TRAIL_TTL = 24 * 3600
CORR_TTL = 24 * 3600
TRAIL_INTERVAL = 20

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=6379,
    decode_responses=True
)

trail_timers = {}
processed_count = 0
last_heartbeat = time.time()


def norm_callsign(value):
    return (value or "").strip().upper()


def norm_hex(value):
    return (value or "").strip().upper()


def pick_flight_id(plane):
    flight_id = (plane.get("flight_id") or "").strip()
    if flight_id:
        return flight_id

    gufi = (plane.get("gufi") or "").strip()
    if gufi:
        return f"gufi:{gufi}"

    icao_hex = norm_hex(plane.get("icao_hex"))
    if icao_hex:
        return f"icao:{icao_hex}"

    callsign = norm_callsign(plane.get("callsign"))
    if callsign:
        return f"callsign:{callsign}"

    track_key = (plane.get("track_key") or "").strip()
    if track_key:
        return f"track:{track_key}"

    return ""


def is_airborne(plane):
    airborne_raw = str(plane.get("airborne", "")).strip()
    if airborne_raw in ("1", "true", "True", "TRUE"):
        return 1
    if airborne_raw in ("0", "false", "False", "FALSE"):
        return 0

    speed = float(plane.get("speed", 0) or 0)
    alt = float(plane.get("alt", 0) or 0)
    status = (plane.get("flight_status") or "").upper()

    if "DROP" in status or "DROPPED" in status:
        return 0
    if alt > 0 and speed >= AIRBORNE_SPEED:
        return 1
    if alt > 1000:
        return 1
    return 0


def process_message(data):
    global processed_count, last_heartbeat

    try:
        plane = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return

    callsign = norm_callsign(plane.get("callsign"))
    if not callsign:
        return

    now = time.time()
    flight_id = pick_flight_id(plane)
    if not flight_id:
        return

    icao_hex = norm_hex(plane.get("icao_hex"))
    speed = float(plane.get("speed", 0) or 0)
    airborne = is_airborne(plane)

    profile_key = f"profile:{flight_id}"
    state_key = f"state:{flight_id}"
    trail_key = f"trail:{flight_id}"

    profile_map = {
        "flight_id": flight_id,
        "callsign": callsign,
        "gufi": plane.get("gufi", "") or "",
        "icao_hex": icao_hex,
        "operator": plane.get("operator", "") or "",
        "dep": plane.get("dep", "") or "",
        "arr": plane.get("arr", "") or "",
        "source": plane.get("source", "") or "",
        "source_facility": plane.get("source_facility", "") or "",
        "track_key": plane.get("track_key", "") or "",
        "first_seen": plane.get("first_seen", "") or str(now),
        "last_seen": str(now),
    }

    state_map = {
        "flight_id": flight_id,
        "callsign": callsign,
        "gufi": plane.get("gufi", "") or "",
        "icao_hex": icao_hex,
        "operator": plane.get("operator", "") or "",
        "dep": plane.get("dep", "") or "",
        "arr": plane.get("arr", "") or "",
        "dep_time": plane.get("dep_time", "") or "",
        "eta": plane.get("eta", "") or "",
        "faa_ts": plane.get("faa_ts", "") or "",
        "flight_status": plane.get("flight_status", "") or "",
        "source": plane.get("source", "") or "",
        "source_facility": plane.get("source_facility", "") or "",
        "track_key": plane.get("track_key", "") or "",
        "lat": plane.get("lat", "") or "",
        "lon": plane.get("lon", "") or "",
        "speed": str(speed),
        "heading": str(plane.get("heading", 0) or 0),
        "alt": str(plane.get("alt", 0) or 0),
        "assigned_alt": str(plane.get("assigned_alt", "") or ""),
        "vertical_rate": str(plane.get("vertical_rate", "") or ""),
        "airborne": str(airborne),
        "last_update": str(now),
    }

    pipe = r.pipeline()
    pipe.hset(profile_key, mapping=profile_map)
    pipe.expire(profile_key, PROFILE_TTL)

    pipe.hset(state_key, mapping=state_map)
    pipe.expire(state_key, STATE_AIR_TTL if airborne else STATE_GROUND_TTL)

    pipe.set(f"corr:callsign:{callsign}", flight_id, ex=CORR_TTL)

    if icao_hex:
        pipe.set(f"corr:icao:{icao_hex}", flight_id, ex=CORR_TTL)

    outbound = dict(state_map)
    pipe.publish("planes_out", json.dumps(outbound))

    if airborne and speed >= AIRBORNE_SPEED:
        last_append = trail_timers.get(flight_id, 0)
        if now - last_append >= TRAIL_INTERVAL:
            breadcrumb = json.dumps({
                "lat": plane.get("lat", "") or "",
                "lon": plane.get("lon", "") or "",
                "alt": str(plane.get("alt", 0) or 0),
                "ts": now
            })
            pipe.rpush(trail_key, breadcrumb)
            pipe.expire(trail_key, TRAIL_TTL)
            trail_timers[flight_id] = now

    pipe.execute()

    processed_count += 1
    if processed_count % 500 == 0:
        logger.info("Processed %s live messages, latest=%s (%s)", processed_count, flight_id, callsign)

    if now - last_heartbeat >= 60:
        logger.info("Heartbeat: core alive, processed=%s", processed_count)
        last_heartbeat = now


def run():
    pubsub = r.pubsub()
    pubsub.subscribe("live_planes")
    logger.info("Core worker subscribed to live_planes")
    logger.info(
        f"Profile TTL={PROFILE_TTL}s, state airborne TTL={STATE_AIR_TTL}s, "
        f"ground TTL={STATE_GROUND_TTL}s, trail TTL={TRAIL_TTL}s"
    )

    for message in pubsub.listen():
        if message["type"] == "message":
            process_message(message["data"])


if __name__ == "__main__":
    run()
