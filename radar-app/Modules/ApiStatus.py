import time
from Classes.ApiBase import ApiBase
from Classes.Redis import getRedis
from Classes.FaaConnection import getFaaConnection
import os
from workers.supervisor import getSupervisor

class ApiStatus(ApiBase):

    title = "Status"
    description = "Get detailed status of all data sources (FAA SWIM, OpenSky, etc.)"

    apiParameters = {
        "source": {
            "description": "Filter to specific source",
            "required": False,
            "type": "string"
        }
    }

    def execute(self):
        redis = getRedis()
        if not redis.ping():
            self.dieError(self.SERVICE_UNAVAILABLE, "Redis connection failed")
            return

        supervisor = getSupervisor()
        supervisor_status = supervisor.get_status().get("workers", {})
        
        sources = {}

        # 1. FAA SWIM (external container — reads data written to Redis by aviation-radar-swim-ingestor)
        has_swim_creds = bool(os.getenv("FAA_USER") and os.getenv("FAA_PASS"))
        swim_configured = has_swim_creds and bool(os.getenv("QUEUE_SFDPS") or os.getenv("QUEUE_STDDS") or os.getenv("QUEUE_TFMS"))

        faa = getFaaConnection()
        faa_feeds = faa.getSourceStats()
        faa_active = any(f.get("count", 0) > 0 and time.time() - f.get("lastUpdate", 0) < 300 for f in faa_feeds.values())

        if not swim_configured:
            swim_status = "not_configured"
        elif faa_active:
            swim_status = "healthy"
        else:
            swim_status = "configured_no_data"

        sources["faa-swim"] = {
            "configured": swim_configured,
            "enabled": swim_configured,
            "healthy": faa_active,
            "status": swim_status,
            "auth_mode": "authenticated",
            "advisory": "" if swim_configured else "Requires FAA credentials and active queue subscriptions."
        }

        # 2. ADSB.lol Re-API
        adsb_worker = supervisor_status.get("adsblol-reapi", {})
        sources["adsblol-reapi"] = {
            "configured": True, # Publicly available
            "enabled": adsb_worker.get("enabled", False),
            "healthy": adsb_worker.get("status") == "RUNNING",
            "status": adsb_worker.get("status", "DISABLED").lower(),
            "auth_mode": "anonymous",
            "last_error": str(adsb_worker.get("last_error") or ""),
            "advisory": "Requires public IP to be a registered feeder to receive data."
        }

        # 3. ADSB.lol Ground Sweep
        ground_worker = supervisor_status.get("adsblol-ground", {})
        sources["adsblol-ground-sweep"] = {
            "configured": True,
            "enabled": ground_worker.get("enabled", False),
            "healthy": ground_worker.get("status") == "RUNNING",
            "status": ground_worker.get("status", "DISABLED").lower(),
            "auth_mode": "anonymous",
            "last_error": str(ground_worker.get("last_error") or ""),
            "advisory": "Requires public IP to be a registered feeder."
        }

        # 4. OpenSky
        has_opensky = bool(os.getenv("OPENSKY_CLIENT_ID") and os.getenv("OPENSKY_CLIENT_SECRET"))
        
        from Classes.Ingestors import getOpenSkyIngestor
        os_ingestor = getOpenSkyIngestor()
        os_stats = os_ingestor.stats if os_ingestor else {}
        
        sources["opensky"] = {
            "configured": True,
            "enabled": True, # Runs by default in CoreProcessor
            "healthy": os_stats.get("planes_all", 0) > 0 or os_stats.get("planes_own", 0) > 0,
            "status": "healthy" if os_stats.get("planes_all", 0) > 0 else "empty",
            "auth_mode": "authenticated" if has_opensky else "anonymous",
            "last_error": "",
            "advisory": "Anonymous mode is rate-limited and lacks own-receiver polling." if not has_opensky else "Authenticated mode active."
        }

        activeCount = sum(1 for s in sources.values() if s.get("healthy"))

        if activeCount == 0:
            overallStatus = "offline"
        elif activeCount < len(sources):
            overallStatus = "partial"
        else:
            overallStatus = "online"

        self.responseData = {
            "overall": overallStatus,
            "timestamp": time.time(),
            "activeSources": activeCount,
            "totalSources": len(sources),
            "sources": sources
        }

        self.sendResponse(self.SUCCESS)

    def requiresGet(self):
        return True
