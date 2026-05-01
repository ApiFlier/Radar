#!/usr/bin/env python3
import csv
import json
import urllib.request
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
FREQS_URL = "https://davidmegginson.github.io/ourairports-data/airport-frequencies.csv"

AIRPORTS_CSV = DATA_DIR / "airports.csv"
FREQS_CSV = DATA_DIR / "airport-frequencies.csv"
OUT_JSON = DATA_DIR / "airports.json"

def download(url, path):
    print(f"Downloading {url}")
    urllib.request.urlretrieve(url, path)

def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None

download(AIRPORTS_URL, AIRPORTS_CSV)
download(FREQS_URL, FREQS_CSV)

freqs = defaultdict(list)

with FREQS_CSV.open(newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        ident = row.get("airport_ident", "").strip()
        if not ident:
            continue

        freq_type = row.get("type", "").strip()
        desc = row.get("description", "").strip()
        mhz = row.get("frequency_mhz", "").strip()

        if mhz:
            freqs[ident].append({
                "type": freq_type,
                "description": desc,
                "mhz": mhz,
            })

airports = []

with AIRPORTS_CSV.open(newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if row.get("iso_country") != "US":
            continue

        airport_type = row.get("type", "")
        if airport_type == "closed_airport":
            continue

        if airport_type not in {
            "large_airport",
            "medium_airport",
            "small_airport",
            "seaplane_base",
            "heliport",
        }:
            continue

        lat = safe_float(row.get("latitude_deg"))
        lon = safe_float(row.get("longitude_deg"))

        if lat is None or lon is None:
            continue

        ident = row.get("ident", "").strip()
        iata = row.get("iata_code", "").strip()
        gps = row.get("gps_code", "").strip()
        local = row.get("local_code", "").strip()
        icao = row.get("icao_code", "").strip()

        code = iata or local or gps or icao or ident

        airports.append({
            "ident": ident,
            "code": code,
            "iata": iata,
            "icao": icao,
            "gps": gps,
            "local": local,
            "name": row.get("name", "").strip(),
            "type": airport_type,
            "lat": lat,
            "lon": lon,
            "elevation_ft": row.get("elevation_ft", "").strip(),
            "municipality": row.get("municipality", "").strip(),
            "region": row.get("iso_region", "").strip(),
            "scheduled_service": row.get("scheduled_service", "").strip(),
            "home_link": row.get("home_link", "").strip(),
            "wikipedia_link": row.get("wikipedia_link", "").strip(),
            "frequencies": freqs.get(ident, [])[:12],
        })

airports.sort(key=lambda a: (
    {"large_airport": 0, "medium_airport": 1, "small_airport": 2, "seaplane_base": 3, "heliport": 4}.get(a["type"], 9),
    a["code"],
))

OUT_JSON.write_text(json.dumps({
    "source": "OurAirports",
    "warning": "Not for navigation. Verify against official FAA data.",
    "count": len(airports),
    "airports": airports,
}, separators=(",", ":")), encoding="utf-8")

print(f"Wrote {OUT_JSON} with {len(airports)} airports")
