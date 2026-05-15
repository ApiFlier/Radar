import os
import time
import json
import httpx
import concurrent.futures
from Classes.ApiBase import ApiBase
from Classes.Redis import getRedis

class ApiWeather(ApiBase):
    title = "Weather"
    description = "Get METAR, TAF, and active NWS alerts for an airport"
    
    apiParameters = {
        "airport": {
            "description": "Airport ICAO code (e.g., KPIT)",
            "required": True,
            "type": "string"
        },
        "lat": {
            "description": "Latitude for NWS alerts",
            "required": False,
            "type": "float"
        },
        "lon": {
            "description": "Longitude for NWS alerts",
            "required": False,
            "type": "float"
        }
    }
    
    def execute(self):
        airport = self.params.get("airport", "").strip().upper()
        lat = self.params.get("lat")
        lon = self.params.get("lon")
        
        if not airport:
            self.dieError(self.BAD_REQUEST, "Airport ICAO code is required")
            return

        # Cache check
        redis = getRedis()
        cache_key = f"weather:{airport}"
        if redis.exists(cache_key):
            cached_data = redis.get(cache_key)
            if cached_data:
                try:
                    self.responseData = json.loads(cached_data)
                    self.sendResponse(self.SUCCESS)
                    return
                except:
                    pass

        # Fetch fresh data
        data = {
            "configured": True,
            "airport": airport,
            "source": "AviationWeather.gov",
            "updatedAt": int(time.time()),
            "metar": None,
            "taf": None,
            "alerts": [],
            "advisory": "Weather is advisory. Verify official aviation weather."
        }

        def _fmt_wind(m):
            wdir = m.get("wdir")
            wspd = m.get("wspd") or 0
            wgst = m.get("wgst")
            if wspd == 0 and (wdir is None or wdir == 0):
                wind = "Calm"
            else:
                wind = f"{int(wdir or 0):03d}@{int(wspd)}KT"
            if wgst:
                wind += f" G{int(wgst)}KT"
            return wind

        def _fetch_metar(client):
            try:
                url = f"https://aviationweather.gov/api/data/metar?ids={airport}&format=json"
                r = client.get(url)
                if r.status_code == 200:
                    metars = r.json()
                    if metars and isinstance(metars, list):
                        m = metars[0]
                        visib  = m.get("visib")
                        ceil_v = m.get("ceil")
                        altim  = m.get("altim")
                        altim_f = float(altim) if altim is not None else None
                        if altim_f is not None:
                            # API returns hPa; convert to inHg to match raw METAR Axxxx field
                            if altim_f > 100:
                                altim_f = altim_f / 33.8639
                            altim_str = f"{altim_f:.2f} inHg"
                        else:
                            altim_str = "—"
                        return {
                            "raw":             m.get("rawOb"),
                            "observationTime": m.get("obsTime"),
                            "flightCategory":  m.get("fltcat"),
                            "wind":            _fmt_wind(m),
                            "visibility":      f"{visib}SM"  if visib  is not None else "—",
                            "ceiling":         f"{ceil_v} ft" if ceil_v is not None else "—",
                            "temperature":     m.get("temp"),
                            "dewpoint":        m.get("dewp"),
                            "altimeter":       altim_str,
                        }
            except Exception as e:
                self.debugMessage("METAR Fetch Error", str(e))
            return None

        def _fetch_taf(client):
            try:
                url = f"https://aviationweather.gov/api/data/taf?ids={airport}&format=json"
                r = client.get(url)
                if r.status_code == 200:
                    tafs = r.json()
                    if tafs and isinstance(tafs, list):
                        t = tafs[0]
                        return {
                            "raw":       t.get("rawTAF"),
                            "issueTime": t.get("issueTime"),
                            "validFrom": t.get("validFrom"),
                            "validTo":   t.get("validTo"),
                        }
            except Exception as e:
                self.debugMessage("TAF Fetch Error", str(e))
            return None

        def _fetch_alerts(client):
            if lat is None or lon is None:
                return []
            try:
                headers = {"User-Agent": "AviationRadar/1.0 (github.com/ApiFlier/aviation-radar)"}
                url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
                r = client.get(url, headers=headers)
                if r.status_code == 200:
                    features = r.json().get("features", [])
                    return [
                        {"event": f.get("properties", {}).get("event"),
                         "severity": f.get("properties", {}).get("severity"),
                         "headline": f.get("properties", {}).get("headline")}
                        for f in features
                    ]
            except Exception as e:
                self.debugMessage("NWS Alerts Fetch Error", str(e))
            return []

        with httpx.Client(timeout=10.0) as client:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                f_metar  = pool.submit(_fetch_metar,  client)
                f_taf    = pool.submit(_fetch_taf,    client)
                f_alerts = pool.submit(_fetch_alerts, client)
                data["metar"]  = f_metar.result()
                data["taf"]    = f_taf.result()
                data["alerts"] = f_alerts.result()

        # Finalize and cache
        self.responseData = data
        redis.set(cache_key, json.dumps(data), ex=600) # 10 minute cache
        self.sendResponse(self.SUCCESS)

    def requiresGet(self):
        return True
