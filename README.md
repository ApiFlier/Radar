# Radar

A private real-time flight radar project built around FAA SWIM data, Redis, Python services, and a browser-based radar display.

Radar connects to FAA SWIM queues, ingests flight messages, stores live aircraft state in Redis, and serves a web dashboard through a Dockerized frontend.

---

## Current status

This repo is private and still under active development.

For now, credentials are stored in the tracked root .env file to make testing and deployment easier. Later, before making this repo public or handing it off, .env should be replaced with .env.example, and the real .env should be ignored.

---

## Architecture

Radar runs as four Docker services:

| Service | Container | Purpose |
|---|---|---|
| Redis | flight-redis | Internal state store and message broker |
| FAA SWIM ingestor | swim-ingestor | Connects to FAA SWIM queues and publishes flight data |
| API | flight-api | Internal API service used by the web frontend |
| Web | flight-web | Public browser UI |

Network flow:

    Browser -> flight-web -> flight-api -> flight-redis
                          swim-ingestor -> flight-redis

Redis and API are internal-only. Only the web service is exposed to the host.

---

## Deploy

### Part 1 - Install Docker

Run once on a fresh Ubuntu server:

    sudo apt update && sudo apt upgrade -y
    sudo apt install -y git curl iproute2
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER

Log out and back in, then verify:

    docker --version
    docker compose version

### Part 2 - Clone and run

    git clone https://github.com/ApiFlier/Radar.git ./radar
    cd ./radar
    chmod +x setup.sh
    ./setup.sh

The setup script will:

- Check that Docker and Docker Compose are installed
- Validate required FAA and OpenSky credentials in .env
- Find the first open web port starting at 8080
- Write the selected port to .env as WEB_PORT
- Build and start the Docker containers
- Print the final web URL

If port 8080 is already in use, setup will choose the next open port.

---

## Configuration

The central config file is:

    /radar/.env

Required values:

    FAA_USER=
    FAA_PASS=
    QUEUE_SFDPS=
    QUEUE_STDDS=
    QUEUE_TFMS=
    FAA_URL=tcps://ems1.swim.faa.gov:55443
    REDIS_HOST=flight-redis
    OPENSKY_CLIENT_ID=
    OPENSKY_CLIENT_SECRET=
    WEB_PORT=

WEB_PORT is managed by setup.sh. If it is missing, setup assigns the first open port starting at 8080.

---

## Day-to-day management

Run these from the repo folder:

    cd /radar

View containers:

    docker compose ps

Follow all logs:

    docker compose logs -f

Follow one service:

    docker compose logs -f web
    docker compose logs -f api
    docker compose logs -f swim-ingestor

Restart everything:

    docker compose restart

Stop everything:

    docker compose down

Rebuild after code changes:

    docker compose up -d --build

---

## Ports

Only the web service is exposed to the host.

| Service | Host port | Container port | Public? |
|---|---:|---:|---|
| Web | Assigned by setup.sh, usually 8080 | 8080 | Yes |
| API | Not exposed | 8081 | No |
| Redis | Not exposed | 6379 | No |

Check the chosen web port:

    grep '^WEB_PORT=' .env

Open the app:

    http://SERVER_IP:WEB_PORT

Example:

    http://192.168.1.206:8080

---

## Project structure

    /radar/
    ├── .env
    ├── README.md
    ├── setup.sh
    ├── docker-compose.yml
    ├── index.html
    ├── api/
    ├── core/
    ├── ingestors/
    ├── lib/
    ├── web/
    └── samples/

---

## Useful checks

Validate Docker Compose without starting containers:

    docker compose config --quiet && echo "Compose OK"

Check running containers:

    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

Confirm Redis and API are not exposed to the host:

    docker compose config | grep -A12 "redis:"
    docker compose config | grep -A16 "api:"
    docker compose config | grep -A16 "web:"

---

## Cleanup

Stop the stack:

    docker compose down

Stop the stack and remove the Redis volume:

    docker compose down -v

---

## Future cleanup before public release

Before making this repo public or sharing it broadly:

1. Replace tracked .env with .env.example
2. Add .env to .gitignore
3. Remove real FAA/OpenSky credentials from Git history
4. Replace README credential instructions with placeholders
5. Decide whether the repo should support optional deletion after Docker build, like FlightConn and Mollie

For now, this repo is private and optimized for fast testing.
