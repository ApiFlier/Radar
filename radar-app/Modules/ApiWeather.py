import os
import time
import json
import httpx
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
            "advisory": "Weather is advisory only. Verify official aviation weather before operational use."
        }

        with httpx.Client(timeout=10.0) as client:
            # 1. Fetch METAR
            try:
                metar_url = f"https://aviationweather.gov/api/data/metar?ids={airport}&format=json"
                r = client.get(metar_url)
                if r.status_code == 200:
                    metars = r.json()
                    if metars and isinstance(metars, list):
                        m = metars[0]
                        data["metar"] = {
                            "raw": m.get("rawOb"),
                            "observationTime": m.get("obsTime"),
                            "flightCategory": m.get("fltcat"),
                            "wind": f"{m.get('wdir', 'VRB')}@{m.get('wspd', 0)}KT",
                            "visibility": f"{m.get('visib', '—')}SM",
                            "ceiling": f"{m.get('ceil', '—')} FT",
                            "temperature": m.get("temp"),
                            "dewpoint": m.get("dewp"),
                            "altimeter": m.get("altim")
                        }
                        if m.get("wgst"):
                            data["metar"]["wind"] += f" G {m.get('wgst')}KT"
            except Exception as e:
                self.debugMessage("METAR Fetch Error", str(e))

            # 2. Fetch TAF
            try:
                taf_url = f"https://aviationweather.gov/api/data/taf?ids={airport}&format=json"
                r = client.get(taf_url)
                if r.status_code == 200:
                    tafs = r.json()
                    if tafs and isinstance(tafs, list):
                        t = tafs[0]
                        data["taf"] = {
                            "raw": t.get("rawTAF"),
                            "issueTime": t.get("issueTime"),
                            "validFrom": t.get("validFrom"),
                            "validTo": t.get("validTo")
                        }
            except Exception as e:
                self.debugMessage("TAF Fetch Error", str(e))

            # 3. Fetch NWS Alerts if lat/lon available
            if lat is not None and lon is not None:
                try:
                    # User-Agent is required by api.weather.gov
                    headers = {"User-Agent": "AviationRadar/1.0 (github.com/ApiFlier/aviation-radar)"}
                    alerts_url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
                    r = client.get(alerts_url, headers=headers)
                    if r.status_code == 200:
                        alerts_json = r.json()
                        features = alerts_json.get("features", [])
                        for feature in features:
                            props = feature.get("properties", {})
                            data["alerts"].append({
                                "event": props.get("event"),
                                "severity": props.get("severity"),
                                "headline": props.get("headline")
                            })
                except Exception as e:
                    self.debugMessage("NWS Alerts Fetch Error", str(e))

        # Finalize and cache
        self.responseData = data
        redis.set(cache_key, json.dumps(data), ex=600) # 10 minute cache
        self.sendResponse(self.SUCCESS)

    def requiresGet(self):
        return True
