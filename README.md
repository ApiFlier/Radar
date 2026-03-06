# ✈️ RadarAPI: Real-Time FAA SWIM Flight Tracker

A high-performance, event-driven microservices stack that connects directly to the FAA's System Wide Information Management (SWIM) enterprise data feed. It parses live XML flight data, computes real-time headings, stores historical flight trails in Redis, and serves a live-updating, dead-reckoning radar map to your web browser.

![Architecture: FAA SWIM -> Stunnel -> Python Ingestor -> Redis Pub/Sub -> Flask SSE -> Leaflet Map]

## 🏗️ Architecture

This project is broken down into four lightweight Docker containers orchestrated via `docker-compose`:

1. **`flight-redis`**: An in-memory Redis database acting as both the state store for current coordinates/flight paths and the Pub/Sub message broker.
2. **`swim-ingestor`**: A Python worker that establishes a secure Stunnel proxy to the FAA, connects to the Solace queue, parses the nested FIXM XML using `lxml` and XPath, calculates true headings from track velocities, and broadcasts the plane data to Redis Pub/Sub.
3. **`flight-core`**: A background Python worker that subscribes to the live feed, manages the 24-hour TTL state of every plane, and records historical breadcrumb trails for aircraft traveling over 40 knots.
4. **`flight-web`**: A Flask server that provides a REST API for bulk loads and a Server-Sent Events (SSE) `/api/stream` endpoint. The frontend uses `Leaflet.js` with a custom physics engine to smoothly animate (dead-reckon) SVG aircraft across the screen between radar pings.


To deploy this entire stack on a new Linux server or Raspberry Pi, just follow these steps.

### 1. Prerequisites
Ensure you have Git and Docker installed on your host machine.
# 1. Install Docker and Docker Compose
```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
```

# 2. Create your environment file (Replace with your actual FAA credentials)
```bash
cat << 'EOF' > .env
# FAA Credentials
FAA_USER=
FAA_PASS=
```

# Queue Names (Get these from your FAA SCDS Dashboard)
```bash
QUEUE_SFDPS=
QUEUE_STDDS=
```

# Connection Info
```bash
FAA_URL=tcps://ems1.swim.faa.gov:55443
REDIS_HOST=flight-redis
EOF
```

# 3. Build and launch the entire microservices stack
```bash
docker compose up -d --build
```

