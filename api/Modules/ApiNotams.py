import time
from Classes.ApiBase import ApiBase

class ApiNotams(ApiBase):
    title = "NOTAMs"
    description = "Get NOTAMs for an airport (Placeholder)"
    
    apiParameters = {
        "airport": {
            "description": "Airport ICAO code (e.g., KPIT)",
            "required": True,
            "type": "string"
        }
    }
    
    def execute(self):
        airport = self.params.get("airport", "").strip().upper()
        
        if not airport:
            self.dieError(self.BAD_REQUEST, "Airport ICAO code is required")
            return

        # Normalized placeholder JSON
        self.responseData = {
            "configured": False,
            "airport": airport,
            "source": "FAA FNS/SWIM",
            "notams": [],
            "officialUrl": "https://notams.aim.faa.gov/notamSearch/",
            "message": "NOTAM source is not configured for this deployment.",
            "advisory": "NOTAM display is advisory only. Verify official FAA NOTAM sources before operational use."
        }
        
        self.sendResponse(self.SUCCESS)

    def requiresGet(self):
        return True
