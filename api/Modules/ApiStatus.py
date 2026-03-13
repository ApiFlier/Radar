import time
from Classes.ApiBase import ApiBase
from Classes.Redis import getRedis
from Classes.FaaConnection import getFaaConnection


class ApiStatus(ApiBase):

    title = "Status"
    description = "Get detailed status of all data sources (FAA SWIM, OpenSky, etc.)"

    apiParameters = {
        "source": {
            "description": "Filter to specific source (faa, opensky, adsb)",
            "required": False,
            "type": "string"
        }
    }

    def execute(self):
        sourceFilter = self.params.get("source", "").lower()

        redis = getRedis()

        if not redis.ping():
            self.dieError(self.SERVICE_UNAVAILABLE, "Redis connection failed")
            return

        sources = {}

        if not sourceFilter or sourceFilter == "faa":
            faa = getFaaConnection()
            sources["faa"] = faa.getStatus()
            sources["faa"]["feeds"] = faa.getSourceStats()

        if not sourceFilter or sourceFilter == "opensky":
            sources["opensky"] = {
                "source": "opensky",
                "status": "disabled",
                "message": "OpenSky integration not yet configured"
            }

        if not sourceFilter or sourceFilter == "adsb":
            sources["adsb"] = {
                "source": "adsb-local",
                "status": "disabled",
                "message": "RTL-SDR ADS-B receiver not yet configured"
            }

        activeCount = sum(1 for s in sources.values() if s.get("status") == "active")

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
