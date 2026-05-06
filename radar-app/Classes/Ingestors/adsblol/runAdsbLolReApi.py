import os
import json
import time
import traceback
import urllib.parse
import urllib.request
import logging
import redis
from Classes.Ingestors.BaseIngestor import BaseIngestor

logger = logging.getLogger("adsblol-reapi")

class AdsbLolReApiIngestor(BaseIngestor):
    SOURCE_NAME = "adsb-lol-reapi"

    def __init__(self):
        super().__init__()
        self.redis_host = os.getenv("REDIS_HOST", "localhost")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.reapi_url = os.getenv("ADSBLOL_REAPI_URL", "https://re-api.adsb.lol/")
        self.circles_raw = os.getenv("ADSBLOL_REAPI_CIRCLES", "40.491389,-80.232778,250")
        self.interval = float(os.getenv("ADSBLOL_REAPI_INTERVAL_SECONDS", "10"))
        self.ttl = int(os.getenv("ADSBLOL_REAPI_TTL_SECONDS", "45"))
        self.spacing = float(os.getenv("ADSBLOL_REAPI_REQUEST_SPACING_SECONDS", "1.2"))
        self.timeout = float(os.getenv("ADSBLOL_REAPI_TIMEOUT_SECONDS", "20"))
        self.last_message_time = 0.0
        self.total_batches = 0
        self.total_aircraft_seen = 0
        self.total_errors = 0
        self.r = None

    def log(self, msg):
        logger.info(f"[WORKER:adsblol-reapi] {msg}")

    def run(self):
        circles = [c.strip() for c in self.circles_raw.split(";") if c.strip()]
        if not circles:
            self.log("No circles configured")
            return

        self.log("Starting internal fetch loop")
        self.r = redis.Redis(
            host=self.redis_host,
            port=self.redis_port,
            decode_responses=True,
            socket_keepalive=True,
            health_check_interval=30
        )

        while self._running:
            cycle_started = time.time()
            total_seen_this_cycle = 0
            total_stored_this_cycle = 0

            self._report_metrics(last_attempt_at=time.time())

            for idx, circle in enumerate(circles):
                if not self._running: break
                try:
                    self.log(f"Fetching circle: {circle}")
                    result, status_code = self.fetch_circle(circle)
                    self._report_metrics(last_http_status=status_code)

                    stored = 0
                    for ac in result["aircraft"]:
                        normalized = self.normalize_aircraft(ac, result["source_now"])
                        if normalized:
                            self.store_aircraft(normalized)
                            stored += 1
                    
                    total_seen_this_cycle += result["count"]
                    total_stored_this_cycle += stored
                    
                    self.last_message_time = time.time()
                    self.total_aircraft_seen += result["count"]
                    
                    self._report_metrics(
                        last_success_at=time.time(),
                        last_message_at=time.time(),
                        last_batch_size=result["count"],
                        total_aircraft_seen=self.total_aircraft_seen
                    )

                    self.update_heartbeat({
                        "source": self.SOURCE_NAME,
                        "status": "healthy",
                        "last_success": str(time.time()),
                        "last_count": str(result["count"]),
                        "total_seen": str(self.total_aircraft_seen)
                    })
                except Exception as e:
                    self.total_errors += 1
                    self.log(f"Error fetching circle {circle}: {e}")
                    self._report_metrics(total_errors=self.total_errors, last_error=str(e))

                if idx < len(circles) - 1 and self._running:
                    time.sleep(self.spacing)

            self.total_batches += 1
            self._report_metrics(total_batches=self.total_batches)
            
            self.log(f"Cycle complete: seen={total_seen_this_cycle} stored={total_stored_this_cycle}")

            elapsed = time.time() - cycle_started
            sleep_for = max(1.0, self.interval - elapsed)
            
            stop_at = time.time() + sleep_for
            while time.time() < stop_at and self._running:
                time.sleep(0.5)

    def fetch_circle(self, circle):
        base = self.reapi_url.rstrip("/")
        encoded_circle = urllib.parse.quote(circle, safe=",.-")
        url = f"{base}/?circle={encoded_circle}"
        
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "RadarADSBLOLReAPI/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                status_code = response.getcode()
                data = json.loads(response.read().decode("utf-8", errors="replace"))
                return {
                    "aircraft": data.get("aircraft") or data.get("ac") or [],
                    "count": len(data.get("aircraft") or data.get("ac") or []),
                    "source_now": float(data.get("now", time.time()))
                }, status_code
        except urllib.error.HTTPError as e:
            return {"aircraft": [], "count": 0, "source_now": time.time()}, e.code
        except Exception as e:
            raise e

    def normalize_aircraft(self, ac, source_now):
        hex_id = str(ac.get("hex") or "").strip().upper()
        if not hex_id: return None
        lat, lon = ac.get("lat"), ac.get("lon")
        if lat is None or lon is None: return None
        
        alt_baro = ac.get("alt_baro", ac.get("altitude"))
        is_ground = str(alt_baro).lower() == "ground"
        alt = 0.0 if is_ground else float(alt_baro or 0)
        
        # Readsb common fields: flight (callsign), gs (speed), track (heading), baro_rate (vertical rate)
        callsign = str(ac.get("flight") or "").strip().upper()
        speed = float(ac.get("gs") or ac.get("speed") or 0)
        heading = float(ac.get("track") or ac.get("heading") or 0)
        vert_rate = float(ac.get("baro_rate") or ac.get("vert_rate") or 0)
        squawk = str(ac.get("squawk") or "").strip()
        category = str(ac.get("category") or "").strip()
        registration = str(ac.get("r") or "").strip().upper()
        aircraft_type = str(ac.get("t") or "").strip().upper()
        db_flags = ac.get("dbFlags", ac.get("dbflags", 0))

        return {
            "flight_id": f"adsblol:icao:{hex_id}",
            "icao_hex": hex_id,
            "callsign": callsign or hex_id,
            "registration": registration,
            "aircraft_type": aircraft_type,
            "lat": str(lat),
            "lon": str(lon),
            "alt": str(alt),
            "speed": str(speed),
            "heading": str(heading),
            "vertical_rate": str(vert_rate),
            "squawk": squawk,
            "category": category,
            "db_flags": str(db_flags),
            "airborne": "0" if is_ground else ("1" if speed >= 40 else "0"),
            "source": self.SOURCE_NAME,
            "last_update": str(source_now - float(ac.get("seen", 0)))
        }

    def store_aircraft(self, normalized):
        key = f"adsblol:state:icao:{normalized['icao_hex']}"
        self.r.hset(key, mapping=normalized)
        self.r.expire(key, self.ttl)
        # Also publish for real-time map if CoreProcessor/Stream depends on it
        self.r.publish("live_planes", json.dumps(normalized))
        self._report_metrics(last_publish_at=time.time())

    def update_heartbeat(self, mapping):
        self.r.hset("adsblol:heartbeat", mapping=mapping)
        self.r.expire("adsblol:heartbeat", 300)

def main():
    worker = AdsbLolReApiIngestor()
    worker._running = True
    worker.run()

if __name__ == "__main__":
    main()
