# Radar

Radar is a private Docker-based aircraft tracking stack using FAA/SWIM data, ADSB.lol re-api data, ADSB.lol airport ground sweeps, Redis, Python services, and a browser-based radar display.

Radar ingests live aircraft data, stores aircraft state in Redis, and serves a web dashboard through a Dockerized frontend.

---

## Quick install if you have already set up your deploy.env

```bash
git clone https://github.com/ApiFlier/Radar radar && cd radar  && ./setup.sh
```

## Quick install if you have not set up your deploy.env yet

```bash
git clone https://github.com/ApiFlier/Radar radar && cd radar && cp -n deploy.env.example deploy.env && nano deploy.env && ./setup.sh
```

The only file you need to edit before setup is:

```text
deploy.env
```

`setup.sh` reads `deploy.env`, copies the app into a runtime directory, generates the runtime `.env`, checks the required values, chooses an available web port if needed, builds and starts the containers, then asks whether to delete the original source checkout.

---

## Required access

### FAA API Portal

Use the FAA API Portal to confirm API/SWIM access and credentials:

```text
https://portal.apic4e.faa.gov/
```

### FAA SWIM / NAS Enterprise Messaging

FAA SWIM information:

```text
https://www.faa.gov/air_traffic/technology/swim
```

You need these values in `deploy.env`:

```env
FAA_USER=
FAA_PASS=
QUEUE_SFDPS=
QUEUE_STDDS=
QUEUE_TFMS=
```

### OpenSky, optional

OpenSky credentials are optional:

```text
https://opensky-network.org/
```

```env
OPENSKY_CLIENT_ID=
OPENSKY_CLIENT_SECRET=
```

### ADSB.lol

ADSB.lol re-api access depends on the server/public IP having feeder access:

```text
https://www.adsb.lol/docs/
```

There is no ADSB.lol username/password in `deploy.env` for re-api access.

---

## Deploy config

The deployment config file is:

```text
deploy.env
```

Start from the example:

```bash
cp deploy.env.example deploy.env
nano deploy.env
```

Minimum required values:

```env
FAA_USER=
FAA_PASS=
QUEUE_SFDPS=
QUEUE_STDDS=
QUEUE_TFMS=
```

Common optional values:

```env
INSTALL_DIR=/opt/radar
WEB_BIND=0.0.0.0
WEB_PORT=8080
OPENSKY_CLIENT_ID=
OPENSKY_CLIENT_SECRET=
```

If `WEB_PORT` is busy, `setup.sh` will choose the next available port and write it back to the generated runtime `.env`. If `deploy.env` is writable, setup will also update `WEB_PORT` there.

---

## Runtime layout

By default, setup installs the runtime app here:

```text
/opt/radar
```

The runtime environment file is:

```text
/opt/radar/.env
```

The source checkout can be deleted after setup if you confirm the final prompt.

---

## Architecture

Radar currently runs as six Docker services:

| Service | Container | Purpose |
|---|---|---|
| Redis | `radar-redis` | Internal state store and message broker |
| FAA SWIM ingestor | `radar-swim-ingestor` | Connects to FAA SWIM queues and publishes flight data |
| ADSB.lol re-api ingestor | `radar-adsblol-reapi` | Primary live-ish ADS-B airborne feed |
| ADSB.lol ground sweep | `radar-adsblol-ground` | Airport ground/taxi/gate aircraft sweep |
| API | `radar-api` | Internal API service used by the web frontend |
| Web | `radar-web` | Browser UI |

Network flow:

```text
Browser
  ↓
radar-web
  ↓
radar-api
  ↓
radar-redis

radar-swim-ingestor  → radar-redis
radar-adsblol-reapi  → radar-redis
radar-adsblol-ground → radar-redis
```

Redis and API are internal-only. Only the web service publishes a host port.

---

## Ports

Only `radar-web` is exposed to the host.

| Service | Host port | Container port | Public? |
|---|---:|---:|---|
| Web | Assigned by setup, usually `8080` | `8080` | Yes |
| API | Not exposed | `8081` | No |
| Redis | Not exposed | `6379` | No |

Check the chosen web port:

```bash
grep '^WEB_PORT=' /opt/radar/.env
```

Open the app:

```text
http://SERVER_IP:WEB_PORT
```

Example:

```text
http://192.168.1.206:8080
```

---

## Day-to-day management

Run these from the runtime folder:

```bash
cd /opt/radar
```

View containers:

```bash
docker compose ps
```

Follow all logs:

```bash
docker compose logs -f
```

Follow one service:

```bash
docker compose logs -f web
docker compose logs -f api
docker compose logs -f swim-ingestor
docker compose logs -f adsblol-reapi
docker compose logs -f adsblol-ground
```

Restart everything:

```bash
docker compose restart
```

Stop everything:

```bash
docker compose down
```

Rebuild after code changes:

```bash
docker compose up -d --build
```

---

## Useful checks

Validate Docker Compose without starting containers:

```bash
cd /opt/radar
docker compose config --quiet && echo "Compose OK"
```

Check running containers:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Confirm Redis and API are not exposed to the host:

```bash
docker compose config | grep -A12 "redis:"
docker compose config | grep -A16 "api:"
docker compose config | grep -A16 "web:"
```

Check aircraft source counts:

```bash
curl -sS "http://127.0.0.1:$(grep '^WEB_PORT=' /opt/radar/.env | cut -d= -f2)/api/?action=Planes" -o /tmp/radar_planes.json

python3 - <<'PY'
import json
from pathlib import Path
from collections import Counter

data = json.loads(Path("/tmp/radar_planes.json").read_text())
planes = data.get("response", {}).get("data", {}).get("planes", [])

print("total:", len(planes))
print("sources:", Counter(p.get("source") or "unknown" for p in planes).most_common(20))
print("ground sweep:", sum(
    1 for p in planes
    if p.get("source") == "adsblol-ground-sweep"
    or p.get("positionSource") == "adsblol-ground-sweep"
))
PY
```

---

## Project structure

Source checkout:

```text
Radar/
├── README.md
├── setup.sh
├── deploy.env.example
├── deploy.env
├── docker-compose.yml
├── index.html
├── api/
├── core/
├── data/
├── ingestors/
├── lib/
├── samples/
├── tools/
└── web/
```

Runtime install:

```text
/opt/radar/
├── .env
├── README.md
├── setup.sh
├── deploy.env.example
├── docker-compose.yml
├── index.html
├── api/
├── core/
├── data/
├── ingestors/
├── lib/
├── samples/
├── tools/
└── web/
```

---

## Cleanup

Stop the stack:

```bash
cd /opt/radar
docker compose down
```

Stop the stack and remove the Redis volume:

```bash
cd /opt/radar
docker compose down -v
```

Remove the runtime install:

```bash
sudo rm -rf /opt/radar
```

---

## Private deploy note

This repo is currently private and optimized for fast redeploy.

If this repo may ever become public or be shared broadly:

1. Remove `deploy.env` from Git.
2. Add `deploy.env` and `.env` to `.gitignore`.
3. Rotate FAA/OpenSky credentials that were ever committed.
4. Keep only `deploy.env.example` committed.
5. Clean Git history if needed.
