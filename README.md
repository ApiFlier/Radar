# Radar / Airport Operations Dashboard

A self-hosted, Docker-based radar and airport operations dashboard. Radar provides live aircraft tracking, configurable airport focus modes, aviation weather integrations, and system health monitoring in a professional, browser-based UI.

---

## Screenshots

*(Placeholders for future screenshots)*
- ![Radar map screenshot](docs/screenshots/radar.png)
- ![Airport panel screenshot](docs/screenshots/airport-panel.png)

---

## Features

- **Live Aircraft Tracking**: Real-time tracking with smooth marker animation and zoom-based density adjustments.
- **Airport Focus Mode**: Focus on a specific airport to highlight related flights, with configurable inbound and emergency alerts.
- **Aviation Weather**: Airport weather cards load METAR and TAF data lazily to keep the dashboard fast.
- **Weather Radar**: RainViewer precipitation overlay with crossfaded playback and adjustable opacity.
- **Rich Aircraft Data**: Distinct icons for commercial, private, military, and helicopters, plus parked vs. taxiing states.
- **Interactive UI**: Viewport-first loading, hover tooltips, and shift-click popup behaviors.
- **Multiple Basemaps**: Choose from Dark, Light, Street, Satellite, and Hybrid (default) basemaps.
- **Comprehensive Dashboards**: Dedicated pages for Alerts, Aircraft Search, Ground Ops, System Stats, and Health.
- **Local Persistence**: User settings (basemap, weather layer, default airport) are saved locally.
- **Placeholder Ready**: A NOTAM shell is present and officially linked, ready for future backend integration.

---

## Architecture

Radar uses a modular, microservice architecture orchestrated via Docker Compose:

- **Web Frontend**: A fast, browser-based UI using HTML/JS/CSS and Leaflet for mapping.
- **API Backend**: An internal Python/FastAPI service that serves normalized JSON to the frontend.
- **Cache / Message Broker**: Redis is used for internal state storage and fast message brokering between services.
- **Ingestors**: Python services connecting to external data feeds (FAA SWIM, ADSB.lol) and pushing updates to Redis.

---

## Data Sources & Attribution

Radar aggregates data from several sources. **All data is advisory only and must not be used for operational decision-making.**

- **FAA SWIM**: Core flight data integration.
- **ADSB.lol / OpenSky**: Airborne and ground-level ADS-B target feeds.
- **RainViewer**: Public weather radar tiles (Advisory only; API limits may apply).
- **AviationWeather.gov**: Source for METAR and TAF airport weather.
- **api.weather.gov**: Source for active National Weather Service alerts.

---

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url> radar
   cd radar
   ```

2. **Configure your deployment environment:**
   ```bash
   cp deploy.env.example .env
   # Edit .env with your specific credentials if necessary
   ```

3. **Build and start the containers:**
   ```bash
   docker compose up -d --build
   ```

4. **Access the application:**
   Find the assigned web port:
   ```bash
   WEB_PORT=$(grep '^WEB_PORT=' .env | cut -d= -f2)
   echo "Open http://127.0.0.1:${WEB_PORT} in your browser"
   ```

---

## Configuration

Environment variables are configured in the `.env` file (copied from `deploy.env.example`).

Typical configuration variables include:
- `FAA_USER` / `FAA_PASS`
- `QUEUE_SFDPS`, `QUEUE_STDDS`, `QUEUE_TFMS`
- `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` (Optional)

**⚠️ SECURITY WARNING:** Never commit `.env` or `deploy.env` to version control. They contain sensitive credentials.

---

## Pages & Routes

- `/` : Main Radar Map & Dashboard
- `/aircraft` : Aircraft search, pagination, and filtering
- `/airports` : Airport directories and metrics
- `/ground` : Ground operations sweep data
- `/alerts` : Operational and Data-Quality alerts
- `/stats` : System statistics and flight data summaries
- `/health` : Microservice health and status monitoring
- `/settings` : User preference configuration

---

## Usage Notes

- **Map Controls**: Use the bottom-right buttons for zooming and the bottom-left control bar for switching basemaps or toggling the weather overlay.
- **Weather Overlay**: Enable weather to see RainViewer data. Use the Play/Pause buttons to cycle through recent frames with smooth crossfading.
- **Airport Focus**: Click an airport to open the side panel. Click "Focus Airport" to highlight inbound traffic and trigger proximity alerts.
- **Aircraft Interaction**:
  - *Hover*: View basic identification and state.
  - *Click*: Open the detailed side panel.
  - *Shift + Click*: Open a quick popup directly on the map.

---

## Limitations

- **Not Certified**: This is not a certified aviation, weather, or NOTAM source.
- **NOTAMs**: Real NOTAM ingestion is not yet active. The UI acts as a placeholder linking to official FAA sources.
- **Coverage**: Aircraft coverage is strictly dependent on the availability and health of the configured feeds.
- **Weather**: RainViewer public tiles have inherent limitations and update frequencies.
- **Rate Limits**: OpenSky or other external APIs may enforce rate limits that could throttle data updates.

---

## Security Notes

- **Protect Credentials**: `.env` and `deploy.env` files contain sensitive information and must remain excluded from Git.
- **No Secrets in Docs**: This README and other documentation files must never contain live passwords, tokens, or private URLs.
- **Terms of Service**: Review the terms of service for all external data providers before utilizing this stack in any public or commercial capacity.

---

## Troubleshooting

- **Containers not starting**: Verify Docker daemon is running and check `docker compose logs -f` for specific service failures.
- **Routes not returning 200**: Ensure the API and Web services are fully built and bound to the correct ports.
- **Weather unavailable**: Check browser DevTools for CORS issues or RainViewer/AviationWeather API outages.
- **Aircraft not visible**: Verify that your ingestors (e.g., SWIM or ADSB.lol) are authenticating properly and receiving data.
- **Settings not applying**: Ensure local storage is permitted in your browser, as settings are persisted client-side.

---

## Roadmap

- Implement real FAA FNS/SWIM NOTAM ingestion.
- Add NOTAM map badges and layers following real ingestion.
- Introduce historical flight and radar playback.
- Expand weather provider options for increased reliability.
- Integrate optional alert sounds or browser notifications.
- Transition to a database-backed history store for long-term analytics.

---

## Disclaimer

**All data presented by this application—including weather, NOTAMs, frequencies, and aircraft positioning—is for display and advisory purposes only. It must not be used as the sole source for flight safety or operational decision-making. Always verify against official certified aviation sources.**
