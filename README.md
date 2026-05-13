# Aviation Radar

A self-hosted live aircraft tracking and airport operations dashboard. Aviation Radar aggregates real-time data from public aviation feeds, correlates ADS-B and FAA flight plan data, and presents it through a browser-based map UI with alerts, ground ops, weather overlays, and system health monitoring.

This is a production-style Docker deployment — not a demo app. All tracked aircraft are live, pulled from operational public data sources.

---

## Quick Start

### 1. Install Docker

**Windows / macOS** — [Docker Desktop](https://www.docker.com/products/docker-desktop/)

**Linux** — [Docker Engine](https://docs.docker.com/engine/install/) + Docker Compose plugin:
```bash
# Example for Ubuntu/Debian
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

### 2. Verify Docker

```bash
docker --version
docker compose version
```

Both commands must succeed before proceeding.

### 3. Clone, configure, and run

```bash
git clone https://github.com/ApiFlier/aviation-radar.git radar
cd radar
cp deploy.env.example deploy.env
nano deploy.env
chmod +x setup.sh
./setup.sh
```

`deploy.env` is **only for user-supplied values** such as API keys and credentials. `setup.sh` reads it and generates a complete `.env` with production-ready defaults, then builds and starts Docker.

### 4. Updating

For servers where the repository folder is intentionally kept, use the update script to pull the latest code and rebuild containers:

```bash
./update.sh
```

The update script preserves all Docker volumes and your `.env` configuration.

---

## Deployment Workflow

### setup.sh vs update.sh

- **setup.sh** is for the **initial installation**. It generates the environment configuration, validates credentials, and performs the first build. It also offers to delete source files after completion if you only need the runtime containers.
- **update.sh** is for **ongoing updates** on development or test servers where the source repository is preserved. It pulls the latest code from git, rebuilds the images, and recreates the containers without losing data.

### Resource Guardrails

Aviation Radar includes built-in guardrails to ensure system stability:

- **Disk Space Checks** — Both setup and update scripts check for available disk space (Warn: 25GB, Critical: 10GB).
- **Docker Logging** — Container logs are capped at 10MB per file with a maximum of 3 files to prevent disk exhaustion.
- **Redis Memory Management** — Redis is configured with a memory limit (default 512MB) and an LRU eviction policy (`allkeys-lru`) suitable for ephemeral aircraft state.
- **Docker Cleanup** — Use `./scripts/docker-cleanup.sh` to safely prune build cache and unused images.

---

## What Aviation Radar Does

Aviation Radar is a fully self-hosted flight tracking system. **All data is advisory only. Do not use for operational flight safety decisions. This application is not a certified aviation or weather source.**

- Displays live aircraft positions on an interactive Leaflet map with smooth animation
- Ingests ADS-B position data from [ADSB.lol](https://adsb.lol) every 10 seconds
- Runs a ground sweep to track aircraft taxiing at airports
- Optionally ingests FAA SWIM (FDPS, STDDS, TFMS) flight plan and track data when credentials are configured
- Fuses data from multiple sources into a single canonical aircraft state per tail/GUFI
- Streams fused updates to the browser in real time via Server-Sent Events
- Shows weather overlays (RainViewer precipitation), METAR/TAF cards, and NWS alerts

---

## Key Features

- **Live Aircraft Tracking** — Real-time map with zoom-based density filtering and smooth marker animation
- **Airport Focus Mode** — Click any airport to highlight inbound traffic and trigger proximity alerts
- **Ground Operations** — ADS-B ground sweep tracks aircraft on airport surfaces
- **Aviation Weather** — METAR and TAF cards loaded lazily per airport; RainViewer radar overlay with playback
- **Alerts Dashboard** — Active NWS weather alerts and data-quality flags
- **Aircraft Search** — Paginated aircraft list with filtering by callsign, type, origin, destination
- **System Health** — Live worker status, Redis connectivity, ingestor metrics
- **Multiple Basemaps** — Dark, Light, Street, Satellite, Hybrid (default)
- **Local Settings** — Basemap, weather layer, and default airport persist in browser local storage

---

## Architecture

Docker Compose orchestrates three services:

```
aviation-radar-app          FastAPI app — serves the UI, REST API, SSE stream,
                            and runs internal ADS-B ingestor workers

aviation-radar-redis        Redis — canonical aircraft state, pub/sub message bus

aviation-radar-swim-ingestor  (optional) FAA SWIM consumer — connects to FAA
                              Solace queues via stunnel TLS tunnel
                              Only started when ENABLE_SWIM_INGESTOR=true
```

### Aircraft Data Path

1. **Ingest** — Internal workers (ADS-B.lol Re-API, ground sweep) or the SWIM container fetch data from live sources
2. **Normalize** — Raw source data is mapped to a standard snake_case schema and published to the `live_planes` Redis channel
3. **Fuse** — `CoreProcessor` subscribes to `live_planes`, correlates ADS-B and FAA identifiers, and updates `state:*` and `profile:*` keys in Redis
4. **Stream** — `/api/stream` subscribes to the `planes_out` channel and pushes updates to the browser via SSE
5. **Snapshot** — `/api?action=Planes` scans Redis state keys for the full aircraft list (initial load and periodic fallback)

---

## Tech Stack

| Layer | Technology |
|---|---|
| API / App server | Python 3.11, FastAPI, Uvicorn |
| State / Messaging | Redis 7 (Alpine) |
| FAA SWIM connectivity | Solace PubSub+ Python SDK, stunnel4 TLS tunnel |
| ADS-B ingestion | ADSB.lol Re-API (HTTP polling) |
| Frontend | Vanilla JS, Leaflet.js, SSE |
| Containerization | Docker, Docker Compose v2 |

---

## Data Sources

| Source | What it provides | Requires credentials? |
|---|---|---|
| [ADSB.lol Re-API](https://adsb.lol) | ADS-B airborne positions | IP must be a registered feeder |
| ADSB.lol ground sweep | ADS-B ground positions at airports | Same as above |
| [FAA SWIM](https://www.faa.gov/air_traffic/technology/swim) | FDPS/STDDS/TFMS flight plan and track data | Yes — FAA SWIM account required |
| [AviationWeather.gov](https://aviationweather.gov) | METAR, TAF | No |
| [api.weather.gov](https://api.weather.gov) | NWS active alerts | No |
| [RainViewer](https://www.rainviewer.com) | Precipitation radar tiles | No (public API) |
| [OpenSky Network](https://opensky-network.org) | Supplemental ADS-B coverage | Optional client credentials |

**All data is advisory only. Do not use for operational flight safety decisions. This application is not a certified aviation or weather source.**

---

## API Keys / Required Configuration

### No keys needed to get started

A fresh deployment works out of the box with public ADS-B data from ADSB.lol — provided your server's public IP is a registered ADS-B feeder on that network. No API keys are required for the base deployment.

### Optional: OpenSky Network

Adding OpenSky credentials improves coverage. Set in `deploy.env`:
```
OPENSKY_CLIENT_ID=your-client-id
OPENSKY_CLIENT_SECRET=your-secret
```

### Optional: FAA SWIM (FDPS / STDDS / TFMS)

FAA SWIM provides official FAA flight plan and en-route track data. This requires a free FAA SWIM account and approved queue subscriptions.

To enable, set in `deploy.env`:
```
ENABLE_SWIM_INGESTOR=true

FAA_USER=your.email@example.com
FAA_PASS=your-swim-password
QUEUE_SFDPS=your.email@example.com.FDPS.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.OUT
QUEUE_STDDS=your.email@example.com.STDDS.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.OUT
QUEUE_TFMS=your.email@example.com.TFMS.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.OUT
```

When `ENABLE_SWIM_INGESTOR=false` (the default), the `aviation-radar-swim-ingestor` container is not started. This prevents the restart loop that occurs when FAA credentials are missing or rejected.

Apply for SWIM access: [https://www.faa.gov/air_traffic/technology/swim](https://www.faa.gov/air_traffic/technology/swim)

---

## Runtime State and Persistence

Docker Compose creates a named volume `aviation-radar_redis_data` for Redis persistence. This volume:

- Survives container restarts and `docker compose down`
- Is reused automatically on `./setup.sh` re-runs
- Is **not** deleted when you delete the source files

Aircraft state is ephemeral within Redis (TTL-based). There is no database for historical data in the current release.

---

## Pages and Routes

| Route | Description |
|---|---|
| `/` | Main live map and dashboard |
| `/aircraft` | Aircraft search, pagination, filtering |
| `/airports` | Airport directory and metrics |
| `/ground` | Ground operations sweep data |
| `/alerts` | Operational and weather alerts |
| `/stats` | System statistics and flight data summaries |
| `/health` | Service health and worker status |
| `/settings` | User preference configuration |
| `/api/workers/status` | JSON worker health endpoint |

---

## Testing

```bash
# Confirm containers are running
docker compose ps

# Tail all service logs
docker compose logs -f

# Check live aircraft API response
curl "http://localhost:8080/api?action=Planes" | python3 -m json.tool | head -40

# Check worker health
curl http://localhost:8080/api/workers/status | python3 -m json.tool
```

---

## Deployment Notes

- **setup.sh is idempotent** — safe to run again after changing `deploy.env`. It regenerates `.env` and rebuilds only changed layers.
- **Port selection** — If port 8080 is busy, setup.sh finds the next available port automatically. The chosen port is written to `.env`.
- **Source cleanup** — At the end of setup, you can delete the source directory. Containers keep running. To manage them without the source: `docker ps`, `docker logs aviation-radar-app`, `docker stop aviation-radar-app aviation-radar-redis`.
- **ADSB.lol Re-API** — Requires your server's public IP to be a registered feeder on adsb.lol. Without this, the Re-API ingestor will still run but may return empty data.
- **Firewall** — Open the chosen host port on your firewall if you want LAN or external access.

---

## Known Limitations

- **No historical playback** — Aircraft state is live only. There is no database-backed history store yet.
- **NOTAMs** — NOTAM ingestion is not yet active. Planned for a future release.
- **ADSB.lol coverage** — ADS-B coverage depends on the global feeder network. Remote/oceanic coverage is limited.
- **RainViewer** — Public radar tiles have rate limits and inherent update latency.
- **Not certified** — This is not a certified aviation or weather source. Do not use for flight safety decisions.

---

## Roadmap

- FAA FNS/SWIM NOTAM ingestion and map display
- Historical flight track playback
- Database-backed long-term analytics store
- Expanded weather provider support
- Optional alert sounds and browser notifications

---

## Security Notes

- `deploy.env` and `.env` are excluded from git via `.gitignore`. Never commit them.
- Review the terms of service for all external data providers before using this stack in a public or commercial context.

---

## Disclaimer

All data presented by this application — including weather, aircraft positioning, and alerts — is for display and advisory purposes only. It must not be used as the sole source for flight safety or operational decision-making. Always verify against official certified aviation sources.
