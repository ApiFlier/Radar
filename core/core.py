import os, time, logging, redis, json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Flight-Core")

AIRBORNE_SPEED = 40
TRAIL_INTERVAL = 30
PLANE_TTL = 86400
TRAIL_TTL = 300

r = redis.Redis(host=os.getenv('REDIS_HOST', 'localhost'), port=6379, decode_responses=True)

trail_timers = {}


def process_message(data):
    try:
        plane = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return

    callsign = plane.get("callsign")
    if not callsign:
        return

    now = time.time()
    speed = float(plane.get("speed", 0))

    # ---- CURRENT POSITION ----
    key = f"plane:{callsign}"
    plane["last_update"] = now

    r.hset(key, mapping={
        "callsign": callsign,
        "lat": plane["lat"],
        "lon": plane["lon"],
        "speed": speed,
        "heading": plane.get("heading", 0),
        "alt": plane.get("alt", "0"),
        "assigned_alt": plane.get("assigned_alt", ""),
        "dep": plane.get("dep", ""),
        "arr": plane.get("arr", ""),
        "dep_time": plane.get("dep_time", ""),
        "eta": plane.get("eta", ""),
        "faa_ts": plane.get("faa_ts", ""),
        "flight_status": plane.get("flight_status", ""),
        "operator": plane.get("operator", ""),
        "icao_hex": plane.get("icao_hex", ""),
        "source": plane.get("source", ""),
        "last_update": now
    })
    r.expire(key, PLANE_TTL)

    # Broadcast to frontend
    r.publish("planes_out", json.dumps(plane))

    # ---- TRAIL HISTORY ----
    if speed > AIRBORNE_SPEED:
        last_append = trail_timers.get(callsign, 0)
        if now - last_append >= TRAIL_INTERVAL:
            trail_key = f"trail:{callsign}"
            breadcrumb = json.dumps({
                "lat": plane["lat"],
                "lon": plane["lon"],
                "alt": plane.get("alt", "0"),
                "ts": now
            })
            r.rpush(trail_key, breadcrumb)
            r.expire(trail_key, TRAIL_TTL)
            trail_timers[callsign] = now


def run():
    pubsub = r.pubsub()
    pubsub.subscribe("live_planes")
    logger.info("Core worker subscribed to live_planes channel")
    logger.info(f"Trail: every {TRAIL_INTERVAL}s when speed > {AIRBORNE_SPEED}kts, TTL {TRAIL_TTL}s")
    logger.info(f"Position: TTL {PLANE_TTL}s (24hr)")

    for message in pubsub.listen():
        if message["type"] == "message":
            process_message(message["data"])


if __name__ == "__main__":
    run()
