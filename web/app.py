import os
import json
import redis
from flask import Flask, render_template, jsonify, Response

app = Flask(__name__)
r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=6379,
    decode_responses=True
)


def norm_callsign(value):
    return (value or "").strip().upper()


def norm_hex(value):
    return (value or "").strip().upper()


def merge_profile_state(profile, state):
    data = {}
    if profile:
        data.update(profile)
    if state:
        data.update(state)
    return data


def resolve_flight_id(identifier):
    """
    Resolve a user-facing identifier into the canonical flight_id used for trail/state/profile keys.

    Resolution order:
      1. direct trail/profile/state key match
      2. corr:callsign:<CALLSIGN>
      3. corr:icao:<HEX>
      4. profile scan by callsign (fallback)
    """
    raw = (identifier or "").strip()
    if not raw:
        return None

    # direct exact id
    if r.exists(f"trail:{raw}") or r.exists(f"profile:{raw}") or r.exists(f"state:{raw}"):
        return raw

    upper = raw.upper()

    # callsign correlation
    by_callsign = r.get(f"corr:callsign:{upper}")
    if by_callsign:
        return by_callsign

    # icao correlation
    by_icao = r.get(f"corr:icao:{upper}")
    if by_icao:
        return by_icao

    # last-ditch fallback: scan profiles for matching callsign
    profile_keys = r.keys("profile:*")
    if profile_keys:
        pipe = r.pipeline()
        for key in profile_keys:
            pipe.hget(key, "callsign")
        results = pipe.execute()
        for key, callsign in zip(profile_keys, results):
            if norm_callsign(callsign) == upper:
                return key.replace("profile:", "", 1)

    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/planes")
def planes():
    state_keys = r.keys("state:*")
    if not state_keys:
        return jsonify([])

    pipe = r.pipeline()
    for key in state_keys:
        flight_id = key.replace("state:", "", 1)
        pipe.hgetall(key)
        pipe.hgetall(f"profile:{flight_id}")
    results = pipe.execute()

    planes_data = []
    for i in range(0, len(results), 2):
        state = results[i] or {}
        profile = results[i + 1] or {}
        if not state:
            continue

        data = merge_profile_state(profile, state)

        if data.get("lat") and data.get("lon"):
            planes_data.append(data)

    return jsonify(planes_data)


@app.route("/api/trail/<path:identifier>")
def trail(identifier):
    raw = (identifier or "").strip()

    # 1. direct key
    points = r.lrange(f"trail:{raw}", 0, -1)
    if points:
        return jsonify([json.loads(p) for p in points])

    # 2. resolve via correlation/profile
    flight_id = resolve_flight_id(raw)
    if flight_id:
        points = r.lrange(f"trail:{flight_id}", 0, -1)
        if points:
            return jsonify([json.loads(p) for p in points])

    return jsonify([])


@app.route("/api/stream")
def stream():
    def event_stream():
        pubsub = r.pubsub()
        pubsub.subscribe("planes_out")
        for message in pubsub.listen():
            if message["type"] == "message":
                yield f"data: {message['data']}\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
