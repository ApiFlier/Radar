import time
from typing import Optional
from Classes.Redis import getRedis


class FaaConnection:
    SOURCE_PREFIX = "faa-"

    def __init__(self):
        self.redis = getRedis()

    def _is_faa_source(self, source: str) -> bool:
        s = (source or "").lower()
        return s.startswith(self.SOURCE_PREFIX)

    def getStatus(self) -> dict:
        if not self.redis.ping():
            return {
                "source": "faa-swim",
                "status": "error",
                "message": "Redis connection failed"
            }

        recentCount = 0
        totalCount = 0
        oldestUpdate = time.time()
        newestUpdate = 0
        sourceBreakdown = {}

        for key in self.redis.scan_iter(match="state:*", count=1000):
            state = self.redis.hgetall(key)
            source = state.get("source", "unknown")

            if self._is_faa_source(source):
                totalCount += 1

                lastUpdate = float(state.get("last_update", 0) or 0)
                if lastUpdate > 0:
                    oldestUpdate = min(oldestUpdate, lastUpdate)
                    newestUpdate = max(newestUpdate, lastUpdate)

                    if time.time() - lastUpdate < 60:
                        recentCount += 1

                if source not in sourceBreakdown:
                    sourceBreakdown[source] = 0
                sourceBreakdown[source] += 1

        now = time.time()
        if recentCount == 0:
            status = "stale"
            message = "No recent updates from FAA SWIM"
        elif newestUpdate > 0 and now - newestUpdate > 30:
            status = "delayed"
            message = f"Last update {int(now - newestUpdate)}s ago"
        else:
            status = "active"
            message = "Receiving data"

        return {
            "source": "faa-swim",
            "status": status,
            "message": message,
            "stats": {
                "totalPlanes": totalCount,
                "recentPlanes": recentCount,
                "oldestUpdate": oldestUpdate if totalCount > 0 else None,
                "newestUpdate": newestUpdate if newestUpdate > 0 else None,
                "ageSeconds": int(now - newestUpdate) if newestUpdate > 0 else None
            },
            "sources": sourceBreakdown
        }

    def getSourceStats(self) -> dict:
        stats = {
            "sfdps": {"count": 0, "recent": 0, "lastUpdate": 0},
            "stdds": {"count": 0, "recent": 0, "lastUpdate": 0},
            "tfms": {"count": 0, "recent": 0, "lastUpdate": 0},
            "other": {"count": 0, "recent": 0, "lastUpdate": 0}
        }

        now = time.time()

        for key in self.redis.scan_iter(match="state:*", count=1000):
            state = self.redis.hgetall(key)
            source = state.get("source", "").lower()
            lastUpdate = float(state.get("last_update", 0) or 0)

            if "sfdps" in source:
                category = "sfdps"
            elif "stdds" in source:
                category = "stdds"
            elif "tfms" in source:
                category = "tfms"
            elif self._is_faa_source(source):
                category = "other"
            else:
                continue

            stats[category]["count"] += 1

            if lastUpdate > 0:
                stats[category]["lastUpdate"] = max(stats[category]["lastUpdate"], lastUpdate)
                if now - lastUpdate < 60:
                    stats[category]["recent"] += 1

        for key in stats:
            if stats[key]["lastUpdate"] > 0:
                stats[key]["ageSeconds"] = int(now - stats[key]["lastUpdate"])
            else:
                stats[key]["ageSeconds"] = None

        return stats

    def isActive(self) -> bool:
        status = self.getStatus()
        return status["status"] == "active"

    def getRecentPlanes(self, seconds: int = 60) -> int:
        count = 0
        cutoff = time.time() - seconds

        for key in self.redis.scan_iter(match="state:*", count=1000):
            state = self.redis.hgetall(key)
            source = state.get("source", "").lower()

            if not self._is_faa_source(source):
                continue

            lastUpdate = float(state.get("last_update", 0) or 0)
            if lastUpdate > cutoff:
                count += 1

        return count


_faaConnection: Optional[FaaConnection] = None

def getFaaConnection() -> FaaConnection:
    global _faaConnection
    if _faaConnection is None:
        _faaConnection = FaaConnection()
    return _faaConnection
