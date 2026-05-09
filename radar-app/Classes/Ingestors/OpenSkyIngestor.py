import logging
import os
import json
import time
import httpx
from .BaseIngestor import BaseIngestor
from Classes.Redis import getRedis

logger = logging.getLogger("OpenSkyIngestor")


class OpenSkyIngestor(BaseIngestor):
    """
    Polls OpenSky Network API for local ADS-B data.
    Fills gaps in FAA SWIM: ground movement, VFR, low altitude.
    Uses OAuth2 Client Credentials flow.

    Two poll loops:
      - /states/own  every 10s (free, your RadarPi receiver only)
      - /states/all  every 30s (credits, full Pittsburgh bbox)
    """

    API_URL      = "https://opensky-network.org/api/states/all"
    OWN_URL      = "https://opensky-network.org/api/states/own"
    TOKEN_URL    = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

    POLL_INTERVAL_ALL = 30   # seconds, costs credits
    POLL_INTERVAL_OWN = 10   # seconds, free

    BBOX = {
        "lamin": 39.8,
        "lamax": 41.2,
        "lomin": -80.8,
        "lomax": -78.8
    }

    def __init__(self):
        super().__init__()
        self.redis = None
        self.client_id     = os.getenv("OPENSKY_CLIENT_ID", "")
        self.client_secret = os.getenv("OPENSKY_CLIENT_SECRET", "")
        self.stats = {
            "polled_all": 0, "polled_own": 0,
            "planes_all": 0, "planes_own": 0,
            "errors": 0
        }
        self._token = None
        self._token_expires_at = 0
        self._last_poll_all = 0
        self._last_poll_own = 0

    def _get_token(self):
        now = time.time()
        if self._token and now < self._token_expires_at - 60:
            return self._token
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    self.TOKEN_URL,
                    data={
                        "grant_type":    "client_credentials",
                        "client_id":     self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                resp.raise_for_status()
                token_data = resp.json()
                self._token = token_data["access_token"]
                self._token_expires_at = now + token_data.get("expires_in", 1800)
                logger.info("Token refreshed")
                return self._token
        except Exception as e:
            logger.error(f"Token fetch failed: {e}")
            return None

    def run(self):
        self.redis = getRedis()

        if not self.client_id or not self.client_secret:
            logger.info("No credentials configured, skipping")
            self._running = False
            return

        logger.info(f"Starting — own@{self.POLL_INTERVAL_OWN}s / all@{self.POLL_INTERVAL_ALL}s")

        while self._running:
            now = time.time()
            try:
                if now - self._last_poll_own >= self.POLL_INTERVAL_OWN:
                    self.poll_own()
                    self._last_poll_own = now

                if now - self._last_poll_all >= self.POLL_INTERVAL_ALL:
                    self.poll_all()
                    self._last_poll_all = now
            except Exception as e:
                self.stats["errors"] += 1
                logger.error(f"Poll error: {e}")

            time.sleep(1)

    def _fetch_states(self, url, params=None, source_label=""):
        token = self._get_token()
        if not token:
            logger.warning(f"No valid token, skipping {source_label}")
            return None
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"}
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP {e.response.status_code} on {source_label}")
            if e.response.status_code == 401:
                self._token = None
            return None
        except Exception as e:
            logger.error(f"Request failed ({source_label}): {e}")
            return None

    def poll_own(self):
        data = self._fetch_states(self.OWN_URL, source_label="own")
        if data is None:
            return
        states = data.get("states") or []
        self.stats["polled_own"] += 1
        self.stats["planes_own"] = len(states)
        now = time.time()
        for state in states:
            self.process_state(state, now, source="opensky-own")

        if self.stats["polled_own"] % 18 == 0:  # log every ~3 min
            logger.info(f"own: polled {self.stats['polled_own']} times, last batch: {self.stats['planes_own']} planes")

    def poll_all(self):
        params = {
            "lamin": self.BBOX["lamin"],
            "lamax": self.BBOX["lamax"],
            "lomin": self.BBOX["lomin"],
            "lomax": self.BBOX["lomax"]
        }
        data = self._fetch_states(self.API_URL, params=params, source_label="all")
        if data is None:
            return
        states = data.get("states") or []
        self.stats["polled_all"] += 1
        self.stats["planes_all"] = len(states)
        now = time.time()
        for state in states:
            self.process_state(state, now, source="opensky")

        if self.stats["polled_all"] % 6 == 0:  # log every ~3 min
            logger.info(f"all: polled {self.stats['polled_all']} times, last batch: {self.stats['planes_all']} planes")

    def process_state(self, state, now, source="opensky"):
        icao24 = (state[0] or "").strip().upper()
        if not icao24:
            return

        lat = state[6]
        lon = state[5]
        if lat is None or lon is None:
            return

        callsign  = (state[1] or "").strip().upper()
        on_ground = state[8] or False
        velocity  = state[9]
        heading   = state[10] or 0
        alt_m     = state[7]
        vert_rate = state[11]
        squawk    = state[14] or ""

        speed_kts = round(velocity * 1.94384, 1) if velocity else 0
        alt_ft    = round(alt_m * 3.28084)        if alt_m    else 0
        vert_fpm  = round(vert_rate * 196.85)     if vert_rate else 0

        plane = {
            "icao_hex":     icao24,
            "callsign":     callsign if callsign else icao24,
            "lat":          lat,
            "lon":          lon,
            "alt":          alt_ft,
            "speed":        speed_kts,
            "heading":      round(heading, 1),
            "vertical_rate": vert_fpm,
            "on_ground":    on_ground,
            "squawk":       squawk,
            "source":       source,
            "source_facility": "adsb"
        }

        self.redis.client.publish("live_planes", json.dumps(plane))


_opensky = None

def getOpenSkyIngestor():
    global _opensky
    if _opensky is None:
        _opensky = OpenSkyIngestor()
    return _opensky
