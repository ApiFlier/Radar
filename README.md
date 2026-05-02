# Radar

Radar is a private Docker-based aircraft tracking stack using FAA/SWIM data, ADSB.lol re-api data, ADSB.lol airport ground sweeps, Redis, Python services, and a browser-based radar display.

Radar ingests live aircraft data, stores aircraft state in Redis, and serves a web dashboard through a Dockerized frontend.

---

## Deploy

### Part 1 — Install Docker

Skip this if Docker is already installed.

```bash
sudo apt update && sudo apt upgrade -y && sudo apt install -y git curl
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
```

Log out and back in after this so the Docker group takes effect, then verify:

```bash
docker --version && docker compose version
```

---

### Part 2 — Clone and run

If `deploy.env` is already included in your private repo:

```bash
git clone https://github.com/ApiFlier/Radar radar && cd radar && chmod +x setup.sh && ./setup.sh
```

If you still need to create `deploy.env`:

```bash
git clone https://github.com/ApiFlier/Radar radar && cd radar && [ -f deploy.env ] || cp deploy.env.example deploy.env && nano deploy.env && chmod +x setup.sh && ./setup.sh
```

The only file you need to edit before setup is:

```text
deploy.env
```

`setup.sh` reads `deploy.env`, generates a local `.env`, checks the required values, chooses an available web port if needed, builds the Docker images locally, starts the containers, verifies the API, and then asks whether to delete the local repo files.

If you delete the repo files, the containers keep running. To use `docker compose` again later, reclone the repo or keep the folder.

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

Optional values:

```env
OPENSKY_CLIENT_ID=
OPENSKY_CLIENT_SECRET=
```

Everything else is handled by `setup.sh`, including:

```text
WEB_BIND
WEB_PORT
REDIS_HOST
REDIS_PORT
FAA_URL
ADSBLOL_REAPI settings
GROUND_SWEEP settings
```

If `WEB_PORT` is busy, `setup.sh` will choose the next available port and write it to the generated `.env`.

---

## Runtime model

Radar follows the same deployment model as the other private projects:

```text
repo folder = setup/build/compose management
Docker containers/images = actual running app
```

After setup, the app keeps running even if the repo folder is deleted.

The generated runtime environment file is:

```text
.env
```

It is created inside the cloned repo folder during setup.

Docker stores the container environment when containers are created, so the running containers can survive reboot without the repo folder. If you need to recreate the containers later, reclone the repo and recreate `deploy.env`.

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

Check the chosen web port while the repo folder exists:

```bash
grep '^WEB_PORT=' .env
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

If you kept the repo folder, run these from that folder:

```bash
cd radar
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

## Management after deleting the repo folder

If you delete the repo folder, the containers keep running.

Without the repo folder, manage containers by name:

```bash
docker ps
docker logs radar-api
docker logs radar-web
docker logs radar-swim-ingestor
docker logs radar-adsblol-reapi
docker logs radar-adsblol-ground
```

Stop containers:

```bash
docker stop radar-web radar-api radar-redis radar-swim-ingestor radar-adsblol-reapi radar-adsblol-ground
```

Start containers:

```bash
docker start radar-redis radar-api radar-swim-ingestor radar-adsblol-reapi radar-adsblol-ground radar-web
```

To regain `docker compose` management, reclone the repo.

---

## Useful checks

Validate Docker Compose without starting containers:

```bash
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

Check aircraft source counts while the repo folder exists:

```bash
WEB_PORT=$(grep '^WEB_PORT=' .env | cut -d= -f2)

curl -sS "http://127.0.0.1:${WEB_PORT}/api/?action=Planes" -o /tmp/radar_planes.json

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

```text
Radar/
├── README.md
├── setup.sh
├── deploy.env.example
├── deploy.env
├── .env
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

Stop the stack while the repo folder exists:

```bash
docker compose down
```

Stop the stack and remove Redis data:

```bash
docker compose down -v
```

Remove local repo files after setup:

```bash
rm -rf /path/to/radar
```

The containers keep running unless you stop or remove them.

Remove containers by name:

```bash
docker rm -f radar-web radar-api radar-redis radar-swim-ingestor radar-adsblol-reapi radar-adsblol-ground
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
