import json
import time
import math
import threading
from .BaseIngestor import BaseIngestor
from Classes.Redis import getRedis


class CoreProcessor(BaseIngestor):
    """
    Smart data fusion from SFDPS, STDDS, and TFMS
    """
    
    TRAIL_MAX = 500
    TRAIL_TTL = 14400
    STATE_TTL = 300
    PROFILE_TTL = 86400
    CORR_TTL = 172800
    TRAIL_INTERVAL = 20
    AIRBORNE_SPEED = 40
    
    NEARBY_THRESHOLD = 0.03
    HEADING_THRESHOLD = 30
    
    def __init__(self):
        super().__init__()
        self.redis = None
        self.pubsub_client = None
        self.pubsub = None
        self.trail_timers = {}
        self.processed_count = 0
        self.last_heartbeat = time.time()
        self.stats = {"sfdps": 0, "stdds_corr": 0, "stdds_new": 0, "stdds_dup": 0, "tfms": 0}
        self._ping_thread = None
    
    def run(self):
        while self._running:
            try:
                self._connect_and_listen()
            except Exception as e:
                print(f"[CoreProcessor] Error: {e}, reconnecting in 2s...")
                time.sleep(2)
    
    def _connect_and_listen(self):
        self.redis = getRedis()
        self.pubsub_client = self.redis.get_pubsub_client()
        self.pubsub = self.pubsub_client.pubsub(ignore_subscribe_messages=True)
        self.pubsub.subscribe("live_planes")
        
        print("[CoreProcessor] Subscribed to live_planes")
        print("[CoreProcessor] Strategy: SFDPS=identity, STDDS=position, TFMS=enrichment")
        
        # Start ping thread to keep connection alive
        self._start_ping_thread()
        
        try:
            for message in self.pubsub.listen():
                if not self._running:
                    break
                if message["type"] != "message":
                    continue
                
                try:
                    data = json.loads(message["data"])
                    self.process(data)
                except Exception as e:
                    print(f"[CoreProcessor] Process error: {e}")
        finally:
            self._stop_ping_thread()
            try:
                self.pubsub.close()
                self.pubsub_client.close()
            except:
                pass
    
    def _start_ping_thread(self):
        def ping_loop():
            while self._running and self.pubsub:
                try:
                    self.pubsub.ping()
                except:
                    break
                time.sleep(15)
        
        self._ping_thread = threading.Thread(target=ping_loop, daemon=True)
        self._ping_thread.start()
    
    def _stop_ping_thread(self):
        self._ping_thread = None
    
    def process(self, data):
        source = (data.get("source") or "").lower()
        
        if "sfdps" in source:
            self.process_sfdps(data)
        elif "stdds" in source:
            self.process_stdds(data)
        elif "tfms" in source:
            self.process_tfms(data)
        
        self.processed_count += 1
        now = time.time()
        
        if now - self.last_heartbeat >= 60:
            print(f"[CoreProcessor] Heartbeat: {self.processed_count} total | "
                  f"SFDPS:{self.stats['sfdps']} STDDS-corr:{self.stats['stdds_corr']} "
                  f"STDDS-new:{self.stats['stdds_new']} STDDS-dup:{self.stats['stdds_dup']} "
                  f"TFMS:{self.stats['tfms']}")
            self.last_heartbeat = now
    
    def process_sfdps(self, data):
        """SFDPS is authoritative for flight identity."""
        self.stats["sfdps"] += 1
        
        gufi = data.get("gufi") or ""
        callsign = (data.get("callsign") or "").strip().upper()
        icao_hex = (data.get("icao_hex") or "").strip().upper()
        
        if gufi:
            flight_id = f"gufi:{gufi}"
        elif callsign:
            flight_id = f"callsign:{callsign}"
        else:
            return
        
        now = time.time()
        r = self.redis.client
        
        profile_key = f"profile:{flight_id}"
        state_key = f"state:{flight_id}"
        
        existing_profile = r.hgetall(profile_key) or {}
        existing_state = r.hgetall(state_key) or {}
        
        profile = dict(existing_profile)
        profile["flight_id"] = flight_id
        profile["last_seen"] = str(now)
        if "first_seen" not in profile:
            profile["first_seen"] = str(now)
        
        for field in ["callsign", "gufi", "icao_hex", "operator", "dep", "arr", 
                      "dep_time", "eta", "assigned_alt", "flight_status"]:
            val = data.get(field) or ""
            if val:
                profile[field] = str(val)
            elif field not in profile:
                profile[field] = ""
        
        profile["source"] = data.get("source", "")
        
        state = dict(existing_state)
        state["flight_id"] = flight_id
        state["callsign"] = callsign or state.get("callsign", "")
        state["last_update"] = str(now)
        
        lat = data.get("lat")
        lon = data.get("lon")
        if lat and lon:
            state["lat"] = str(lat)
            state["lon"] = str(lon)
        
        # SFDPS speed - use it if present
        for field in ["alt", "speed", "heading", "vertical_rate"]:
            val = data.get(field)
            if val is not None:
                state[field] = str(val)
        
        speed = float(state.get("speed", 0) or 0)
        state["airborne"] = "1" if speed >= self.AIRBORNE_SPEED else "0"
        state["source"] = data.get("source", "")
        
        pipe = r.pipeline()
        pipe.hset(profile_key, mapping=profile)
        pipe.expire(profile_key, self.PROFILE_TTL)
        pipe.hset(state_key, mapping=state)
        pipe.expire(state_key, self.STATE_TTL)
        
        # Store correlations - CRITICAL for STDDS matching
        if callsign:
            pipe.set(f"corr:callsign:{callsign}", flight_id, ex=self.CORR_TTL)
        if icao_hex and icao_hex != "000000":
            pipe.set(f"corr:icao:{icao_hex}", flight_id, ex=self.CORR_TTL)
        if gufi:
            pipe.set(f"corr:gufi:{gufi}", flight_id, ex=self.CORR_TTL)
        
        if lat and lon and state["airborne"] == "1":
            self._append_trail(pipe, flight_id, lat, lon, state.get("alt", ""), now)
        
        out = {**profile, **state, "last_update": now}
        pipe.publish("planes_out", json.dumps(out))
        pipe.execute()
    
    def process_stdds(self, data):
        """STDDS provides real-time radar position and speed."""
        lat = data.get("lat")
        lon = data.get("lon")
        if not lat or not lon:
            return
        
        lat = float(lat)
        lon = float(lon)
        
        now = time.time()
        r = self.redis.client
        
        icao_hex = (data.get("icao_hex") or "").strip().upper()
        callsign = (data.get("callsign") or "").strip().upper()
        gufi = data.get("gufi") or ""
        
        flight_id = None
        
        # Try GUFI first
        if gufi:
            flight_id = r.get(f"corr:gufi:{gufi}")
        
        # Try ICAO hex
        if not flight_id and icao_hex and icao_hex != "000000":
            flight_id = r.get(f"corr:icao:{icao_hex}")
        
        # Try callsign (skip synthetic)
        if not flight_id and callsign and not self._is_synthetic(callsign):
            flight_id = r.get(f"corr:callsign:{callsign}")
        
        if flight_id:
            self.stats["stdds_corr"] += 1
            self._update_position(flight_id, data, lat, lon, now)
            return
        
        # No correlation - check for nearby plane (geometric dedup)
        heading = float(data.get("heading", 0) or 0)
        nearby = self._find_nearby(lat, lon, heading)
        
        if nearby:
            self.stats["stdds_dup"] += 1
            self._update_position(nearby, data, lat, lon, now)
        else:
            self.stats["stdds_new"] += 1
            self._create_uncorrelated(data, lat, lon, now)
    
    def process_tfms(self, data):
        """TFMS enriches with ETA/flow data."""
        self.stats["tfms"] += 1
        
        callsign = (data.get("callsign") or "").strip().upper()
        gufi = data.get("gufi") or ""
        
        r = self.redis.client
        
        flight_id = None
        if gufi:
            flight_id = r.get(f"corr:gufi:{gufi}")
        if not flight_id and callsign:
            flight_id = r.get(f"corr:callsign:{callsign}")
        
        if not flight_id:
            return
        
        now = time.time()
        profile_key = f"profile:{flight_id}"
        profile = r.hgetall(profile_key) or {}
        
        for field in ["eta", "dep", "arr", "operator"]:
            val = data.get(field)
            if val and not profile.get(field):
                profile[field] = str(val)
        
        if data.get("flight_status"):
            profile["flight_status"] = data["flight_status"]
        
        profile["last_seen"] = str(now)
        
        r.hset(profile_key, mapping=profile)
        r.expire(profile_key, self.PROFILE_TTL)
    
    def _update_position(self, flight_id, data, lat, lon, now):
        """Update position and speed on existing flight."""
        r = self.redis.client
        
        state_key = f"state:{flight_id}"
        profile_key = f"profile:{flight_id}"
        
        state = r.hgetall(state_key) or {}
        profile = r.hgetall(profile_key) or {}
        
        state["flight_id"] = flight_id
        state["lat"] = str(lat)
        state["lon"] = str(lon)
        state["last_update"] = str(now)
        state["position_source"] = data.get("source", "")
        
        # STDDS has good speed/heading from radar vectors
        for field in ["speed", "heading", "alt", "vertical_rate"]:
            val = data.get(field)
            if val is not None:
                state[field] = str(val)
        
        speed = float(state.get("speed", 0) or 0)
        state["airborne"] = "1" if speed >= self.AIRBORNE_SPEED else "0"
        
        pipe = r.pipeline()
        pipe.hset(state_key, mapping=state)
        pipe.expire(state_key, self.STATE_TTL)
        
        if state["airborne"] == "1":
            self._append_trail(pipe, flight_id, lat, lon, state.get("alt", ""), now)
        
        out = {**profile, **state, "last_update": now}
        pipe.publish("planes_out", json.dumps(out))
        pipe.execute()
    
    def _create_uncorrelated(self, data, lat, lon, now):
        """Create entry for uncorrelated STDDS track."""
        r = self.redis.client
        
        icao_hex = (data.get("icao_hex") or "").strip().upper()
        callsign = (data.get("callsign") or "").strip().upper()
        track_key = data.get("track_key") or ""
        
        if icao_hex and icao_hex != "000000":
            flight_id = f"icao:{icao_hex}"
        elif track_key:
            flight_id = f"track:{track_key}"
        else:
            flight_id = f"stdds:{int(now*1000)}"
        
        profile = {
            "flight_id": flight_id,
            "callsign": callsign,
            "icao_hex": icao_hex,
            "dep": data.get("dep", "") or "",
            "arr": data.get("arr", "") or "",
            "source": data.get("source", ""),
            "first_seen": str(now),
            "last_seen": str(now),
        }
        
        state = {
            "flight_id": flight_id,
            "callsign": callsign,
            "lat": str(lat),
            "lon": str(lon),
            "speed": str(data.get("speed", 0) or 0),
            "heading": str(data.get("heading", 0) or 0),
            "alt": str(data.get("alt", 0) or 0),
            "vertical_rate": str(data.get("vertical_rate", "") or ""),
            "last_update": str(now),
            "source": data.get("source", ""),
        }
        
        speed = float(state.get("speed", 0) or 0)
        state["airborne"] = "1" if speed >= self.AIRBORNE_SPEED else "0"
        
        pipe = r.pipeline()
        pipe.hset(f"profile:{flight_id}", mapping=profile)
        pipe.expire(f"profile:{flight_id}", self.PROFILE_TTL)
        pipe.hset(f"state:{flight_id}", mapping=state)
        pipe.expire(f"state:{flight_id}", self.STATE_TTL)
        
        if icao_hex and icao_hex != "000000":
            pipe.set(f"corr:icao:{icao_hex}", flight_id, ex=self.CORR_TTL)
        
        if state["airborne"] == "1":
            self._append_trail(pipe, flight_id, lat, lon, state.get("alt", ""), now)
        
        out = {**profile, **state, "last_update": now}
        pipe.publish("planes_out", json.dumps(out))
        pipe.execute()
    
    def _find_nearby(self, lat, lon, heading):
        """Find existing plane within ~2nm with similar heading."""
        r = self.redis.client
        
        for key in r.scan_iter(match="state:*", count=500):
            state = r.hgetall(key)
            if not state:
                continue
            
            other_lat = float(state.get("lat", 0) or 0)
            other_lon = float(state.get("lon", 0) or 0)
            
            if other_lat == 0 and other_lon == 0:
                continue
            
            dist = math.sqrt((lat - other_lat)**2 + (lon - other_lon)**2)
            if dist > self.NEARBY_THRESHOLD:
                continue
            
            other_heading = float(state.get("heading", 0) or 0)
            hdiff = abs(heading - other_heading)
            if hdiff > 180:
                hdiff = 360 - hdiff
            
            if hdiff <= self.HEADING_THRESHOLD:
                return state.get("flight_id") or key.split("state:", 1)[1]
        
        return None
    
    def _is_synthetic(self, callsign):
        """Check if callsign is synthetic (e.g., PITT1234)."""
        if not callsign:
            return True
        # Synthetic: 3-letter facility + T + track number
        if len(callsign) >= 5 and callsign[-1].isdigit() and "T" in callsign[2:5]:
            return True
        return False
    
    def _append_trail(self, pipe, flight_id, lat, lon, alt, now):
        last = self.trail_timers.get(flight_id, 0)
        if now - last < self.TRAIL_INTERVAL:
            return
        
        trail_key = f"trail:{flight_id}"
        point = json.dumps({"lat": lat, "lon": lon, "alt": alt, "ts": now})
        pipe.rpush(trail_key, point)
        pipe.ltrim(trail_key, -self.TRAIL_MAX, -1)
        pipe.expire(trail_key, self.TRAIL_TTL)
        self.trail_timers[flight_id] = now


_processor = None

def getProcessor():
    global _processor
    if _processor is None:
        _processor = CoreProcessor()
    return _processor
