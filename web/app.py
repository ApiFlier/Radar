import os
import json
import time
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


def get_hash(key):
    try:
        if r.exists(key):
            return r.hgetall(key)
    except Exception:
        pass
    return {}


def resolve_flight_id(identifier):
    raw = (identifier or "").strip()
    if not raw:
        return None

    # direct exact match
    for prefix in ("profile:", "state:", "trail:"):
        key = f"{prefix}{raw}"
        try:
            if r.exists(key):
                return raw
        except Exception:
            pass

    upper = raw.upper()

    # callsign correlation
    try:
        corr = r.get(f"corr:callsign:{upper}")
        if corr:
            return corr
    except Exception:
        pass

    # ICAO correlation
    try:
        corr = r.get(f"corr:icao:{upper}")
        if corr:
            return corr
    except Exception:
        pass

    # if somebody entered a bare callsign, try common synthetic ID
    synthetic = f"callsign:{upper}"
    for prefix in ("profile:", "state:", "trail:"):
        key = f"{prefix}{synthetic}"
        try:
            if r.exists(key):
                return synthetic
        except Exception:
            pass

    return None


def load_plane(flight_id):
    if not flight_id:
        return None

    profile = get_hash(f"profile:{flight_id}")
    state = get_hash(f"state:{flight_id}")

    if not profile and not state:
        return None

    plane = merge_profile_state(profile, state)

    plane["flight_id"] = plane.get("flight_id") or flight_id
    plane["callsign"] = plane.get("callsign") or ""
    plane["operator"] = plane.get("operator") or ""
    plane["dep"] = plane.get("dep") or ""
    plane["arr"] = plane.get("arr") or ""
    plane["dep_time"] = plane.get("dep_time") or ""
    plane["eta"] = plane.get("eta") or ""
    plane["faa_ts"] = plane.get("faa_ts") or ""
    plane["flight_status"] = plane.get("flight_status") or ""
    plane["icao_hex"] = plane.get("icao_hex") or ""
    plane["source"] = plane.get("source") or ""
    plane["source_facility"] = plane.get("source_facility") or ""
    plane["track_key"] = plane.get("track_key") or ""
    plane["gufi"] = plane.get("gufi") or ""

    try:
        plane["lat"] = float(plane.get("lat", 0) or 0)
        plane["lon"] = float(plane.get("lon", 0) or 0)
        plane["speed"] = float(plane.get("speed", 0) or 0)
        plane["heading"] = float(plane.get("heading", 0) or 0)
        plane["alt"] = float(plane.get("alt", 0) or 0)
        plane["last_update"] = float(plane.get("last_update", 0) or 0)
    except Exception:
        return None

    assigned = plane.get("assigned_alt", "")
    try:
        plane["assigned_alt"] = float(assigned) if str(assigned).strip() != "" else ""
    except Exception:
        plane["assigned_alt"] = ""

    vr = plane.get("vertical_rate", "")
    try:
        plane["vertical_rate"] = float(vr) if str(vr).strip() != "" else ""
    except Exception:
        plane["vertical_rate"] = ""

    if plane["lat"] == 0 and plane["lon"] == 0:
        return None

    return plane


def all_state_keys():
    try:
        return list(r.scan_iter(match="state:*", count=1000))
    except Exception:
        return []


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    try:
        pong = r.ping()
        return jsonify({"ok": True, "redis": bool(pong), "ts": time.time()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/planes")
def api_planes():
    planes = []
    for key in all_state_keys():
        flight_id = key.split("state:", 1)[1]
        plane = load_plane(flight_id)
        if plane:
            planes.append(plane)

    planes.sort(key=lambda p: p.get("last_update", 0), reverse=True)
    return jsonify(planes)


@app.route("/api/trail/<path:identifier>")
def api_trail(identifier):
    flight_id = resolve_flight_id(identifier)
    if not flight_id:
        return jsonify([])

    trail_key = f"trail:{flight_id}"
    try:
        raw = r.lrange(trail_key, 0, -1)
    except Exception:
        return jsonify([])

    points = []
    for item in raw:
        try:
            pt = json.loads(item)
            lat = float(pt.get("lat", 0) or 0)
            lon = float(pt.get("lon", 0) or 0)
            if lat == 0 and lon == 0:
                continue
            points.append({
                "lat": lat,
                "lon": lon,
                "alt": pt.get("alt", ""),
                "ts": pt.get("ts", "")
            })
        except Exception:
            continue

    return jsonify(points)


@app.route("/api/stream")
def api_stream():
    def generate():
        pubsub = r.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe("planes_out")
        try:
            for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = message.get("data")
                if not data:
                    continue
                yield f"data: {data}\n\n"
        except GeneratorExit:
            pass
        finally:
            try:
                pubsub.close()
            except Exception:
                pass

    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
