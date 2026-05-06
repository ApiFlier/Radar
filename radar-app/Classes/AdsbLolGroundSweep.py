import json
import os
import time
import urllib.parse
import urllib.request
import logging
import redis
from typing import Any, Dict, List, Tuple
from Classes.Ingestors.BaseIngestor import BaseIngestor

logger = logging.getLogger("adsblol-ground")

class AdsbLolGroundSweepIngestor(BaseIngestor):
    def __init__(self):
        super().__init__()
        self.reapi_base = os.getenv("ADSBLOL_REAPI_BASE", "https://re-api.adsb.lol").rstrip("/")
        self.redis_host = os.getenv("REDIS_HOST", "redis")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.interval = int(os.getenv("GROUND_SWEEP_CLUSTER_INTERVAL_SECONDS", "300"))
        self.spacing = float(os.getenv("GROUND_SWEEP_REQUEST_SPACING_SECONDS", "5"))
        self.radius = int(os.getenv("GROUND_SWEEP_RADIUS_NM", "250"))
        self.ttl = int(os.getenv("GROUND_SWEEP_TTL_SECONDS", "900"))
        self.max_seen = float(os.getenv("GROUND_SWEEP_MAX_SEEN_POS_SECONDS", "120"))
        
        self.last_message_time = 0.0
        self.total_batches = 0
        self.total_aircraft_seen = 0
        self.total_errors = 0
        self.r = None
        self.clusters = [
            ("SEA_PDX", 46.0, -122.3, self.radius),
            ("SFO_SJC_OAK_SMF", 37.7, -121.8, self.radius),
            ("LAX_SAN_BUR_ONT", 34.0, -117.8, self.radius),
            ("LAS_PHX", 35.0, -114.6, self.radius),
            ("SLC", 40.8, -112.0, self.radius),
            ("DEN", 39.9, -104.7, self.radius),
            ("DFW_DAL_OKC", 33.0, -97.1, self.radius),
            ("IAH_HOU_SAT_AUS", 30.0, -96.0, self.radius),
            ("MSP_MKE", 44.9, -92.8, self.radius),
            ("ORD_MDW_IND", 41.9, -87.8, self.radius),
            ("DTW_CLE_PIT_CMH", 41.3, -82.0, self.radius),
            ("STL_MCI_MEM_BNA", 37.5, -90.2, self.radius),
            ("ATL_BHM_CHS", 33.6, -84.4, self.radius),
            ("CLT_RDU_GSO", 35.3, -80.3, self.radius),
            ("MCO_TPA_JAX", 28.3, -81.5, self.radius),
            ("MIA_FLL_PBI_RSW", 26.3, -80.6, self.radius),
            ("IAD_DCA_BWI_RIC_ORF", 38.8, -77.0, self.radius),
            ("PHL_EWR_JFK_LGA", 40.3, -74.6, self.radius),
            ("BOS_BDL_PVD_ALB", 42.2, -72.5, self.radius),
        ]

    def log(self, message: str) -> None:
        logger.info(f"[WORKER:adsblol-ground] {message}")

    def run(self):
        self.log("Starting internal ground sweep loop")
        self.r = redis.Redis(host=self.redis_host, port=self.redis_port, decode_responses=True)
        last_run = {name: 0.0 for name, _, _, _ in self.clusters}

        while self._running:
            did_work = False
            self._report_metrics(last_attempt_at=time.time())

            for name, lat, lon, radius_nm in self.clusters:
                if not self._running: break
                now = time.time()
                if now - last_run.get(name, 0) < self.interval:
                    continue

                did_work = True
                last_run[name] = now

                try:
                    self.log(f"Fetch attempt: {name}")
                    data, status_code = self.fetch_circle(lat, lon, radius_nm)
                    self._report_metrics(last_http_status=status_code)

                    aircraft = data.get("aircraft") or data.get("ac") or []
                    stored = 0
                    for ac in aircraft:
                        if self.is_groundish(ac):
                            plane = self.normalize_ground_aircraft(ac, name)
                            if plane:
                                self.store_ground_aircraft(plane)
                                stored += 1
                    
                    if stored > 0:
                        self.last_message_time = time.time()
                    
                    self.total_aircraft_seen += stored
                    self._report_metrics(
                        last_success_at=time.time(),
                        last_message_at=time.time() if stored > 0 else self.last_message_time,
                        last_batch_size=len(aircraft),
                        total_aircraft_seen=self.total_aircraft_seen
                    )
                    
                    self.log(f"{name}: fetched={len(aircraft)} stored={stored}")
                except Exception as e:
                    self.total_errors += 1
                    self.log(f"{name}: ERROR {e}")
                    self._report_metrics(total_errors=self.total_errors, last_error=str(e))

                if self._running:
                    time.sleep(self.spacing)

            if did_work:
                self.total_batches += 1
                self._report_metrics(total_batches=self.total_batches)

            if not did_work and self._running:
                time.sleep(5)

    def fetch_circle(self, lat: float, lon: float, radius_nm: int) -> Tuple[Dict[str, Any], int]:
        circle = f"{lat},{lon},{radius_nm}"
        url = f"{self.reapi_base}/?circle={circle}"
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "MeeksRadarGroundSweep/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8")), resp.getcode()
        except urllib.error.HTTPError as e:
            return {}, e.code
        except Exception as e:
            raise e

    def is_groundish(self, ac: Dict[str, Any]) -> bool:
        gs = float(ac.get("gs", ac.get("speed", 0)) or 0)
        alt = float(ac.get("alt_baro", ac.get("altitude", 0)) if str(ac.get("alt_baro", "")).lower() != "ground" else 0)
        return gs <= 25 and alt <= 250

    def normalize_ground_aircraft(self, ac: Dict[str, Any], cluster_name: str) -> Dict[str, Any] | None:
        hex_id = str(ac.get("hex", "")).strip().upper()
        if not hex_id: return None
        return {
            "icao_hex": hex_id,
            "source": "adsblol-ground-sweep",
            "lastUpdate": time.time()
        }

    def store_ground_aircraft(self, plane: Dict[str, Any]) -> None:
        key = f"adsblol:state:icao:{plane['icao_hex']}"
        self.r.hset(key, mapping={k: str(v) for k, v in plane.items()})
        self.r.expire(key, self.ttl)

def main():
    worker = AdsbLolGroundSweepIngestor()
    worker._running = True
    worker.run()

if __name__ == "__main__":
    main()
