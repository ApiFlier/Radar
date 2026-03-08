import os
import time
import json
import math
import logging
import redis
from lxml import etree
from dotenv import load_dotenv
from solace.messaging.messaging_service import MessagingService
from solace.messaging.resources.queue import Queue
from solace.messaging.receiver.message_receiver import MessageHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SWIM-Ingestor")
load_dotenv()

r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=6379, decode_responses=True)


def safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def calc_heading(vx, vy):
    if vx == 0 and vy == 0:
        return 0.0
    hdg = math.degrees(math.atan2(vx, vy))
    return round((hdg + 360) % 360, 1)


def heading_from_points(lat1, lon1, lat2, lon2):
    try:
        y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
        x = (
            math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
            - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1))
        )
        brng = math.degrees(math.atan2(y, x))
        return round((brng + 360) % 360, 1)
    except Exception:
        return 0.0


def get_first(node, xpath_expr, default=""):
    vals = node.xpath(xpath_expr)
    return vals[0] if vals else default


def get_supplemental(flight, name):
    vals = flight.xpath(f".//*[local-name()='nameValue'][@name='{name}']/@value")
    return vals[0] if vals else ""


def dms_to_decimal(degrees, minutes, direction):
    deg = float(degrees)
    mins = float(minutes)
    value = deg + (mins / 60.0)
    if direction in ("WEST", "SOUTH"):
        value *= -1
    return round(value, 6)


def resolve_flight_id_by_icao(icao_hex):
    if not icao_hex or icao_hex == "000000":
        return None
    try:
        fid = r.get(f"corr:icao:{icao_hex.upper()}")
        return fid if fid else None
    except Exception:
        return None


def build_flight_id(gufi="", callsign="", icao_hex="", source="", source_facility="", track_num="", flight_ref=""):
    if gufi:
        return f"gufi:{gufi}"
    if callsign:
        return f"callsign:{callsign.upper()}"
    if icao_hex and len(icao_hex) == 6:
        return f"icao:{icao_hex.upper()}"
    if source == "tfms" and flight_ref:
        return f"tfms:{flight_ref}"
    if source == "stdds" and source_facility and track_num:
        return f"stdds:{source_facility}:{track_num}"
    return f"unknown:{source}:{int(time.time() * 1000)}"


class SWIMHandler(MessageHandler):
    def __init__(self, vpn_name):
        super().__init__()
        self.count = 0
        self.vpn = vpn_name
        self.last_message_time = time.time()

    def on_message(self, message):
        self.count += 1
        self.last_message_time = time.time()

        if self.count % 500 == 0:
            logger.info("[%s] processed %s messages", self.vpn, self.count)

        try:
            payload = message.get_payload_as_string()
            if not payload:
                raw = message.get_payload_as_bytes()
                if raw:
                    payload = raw.decode("utf-8", errors="ignore")

            if not payload or "<" not in payload:
                return

            root = etree.fromstring(payload.encode("utf-8"))

            if self.vpn == "FDPS":
                self.parse_sfdps(root)
            elif self.vpn == "STDDS":
                self.parse_stdds(root)
            elif self.vpn == "TFMS":
                self.parse_tfms(root)

        except Exception as e:
            logger.debug("parse failure: %s", e)

    def publish_plane(self, plane):
        r.publish("live_planes", json.dumps(plane))

    def parse_sfdps(self, root):
        for flight in root.xpath("//*[local-name()='flight']"):
            try:
                callsign = get_first(flight, ".//*[local-name()='flightIdentification']/@aircraftIdentification")
                pos = get_first(flight, ".//*[local-name()='pos']/text()")
                if not callsign or not pos:
                    continue

                parts = pos.strip().split()
                if len(parts) != 2:
                    continue
                lat, lon = parts

                vx = safe_float(get_first(flight, ".//*[local-name()='trackVelocity']/*[local-name()='x']/text()", 0))
                vy = safe_float(get_first(flight, ".//*[local-name()='trackVelocity']/*[local-name()='y']/text()", 0))
                heading = calc_heading(vx, vy)

                speed = safe_float(get_first(flight, ".//*[local-name()='surveillance']/text()", 0))
                alt = get_first(flight, ".//*[local-name()='enRoute']//*[local-name()='altitude']/text()", "0")
                assigned = get_first(flight, ".//*[local-name()='assignedAltitude']//*[local-name()='simple']/text()", "")
                dep = get_first(flight, ".//*[local-name()='departure']/@departurePoint")
                arr = get_first(flight, ".//*[local-name()='arrival']/@arrivalPoint")
                dep_time = get_first(flight, ".//*[local-name()='departure']//*[local-name()='actual']/@time")
                eta = get_first(flight, ".//*[local-name()='arrival']//*[local-name()='estimated']/@time")
                pos_time = get_first(flight, ".//*[local-name()='position']/@positionTime")
                status = get_first(flight, ".//*[local-name()='flightStatus']/@fdpsFlightStatus")
                operator = get_first(flight, ".//*[local-name()='organization']/@name")
                gufi = get_first(flight, ".//*[local-name()='gufi']/text()")
                source_facility = get_first(flight, ".//*[local-name()='flight']/@centre") or get_first(flight, "./@centre")

                icao_raw = get_supplemental(flight, "ADSB_02M_52B")
                icao_hex = icao_raw.lstrip("-").upper() if icao_raw else ""

                flight_id = build_flight_id(
                    gufi=gufi,
                    callsign=callsign,
                    icao_hex=icao_hex,
                    source="sfdps",
                    source_facility=source_facility
                )

                plane = {
                    "flight_id": flight_id,
                    "callsign": callsign,
                    "gufi": gufi,
                    "icao_hex": icao_hex,
                    "operator": operator,
                    "dep": dep,
                    "arr": arr,
                    "lat": lat,
                    "lon": lon,
                    "speed": speed,
                    "heading": heading,
                    "alt": alt,
                    "assigned_alt": assigned,
                    "vertical_rate": "",
                    "dep_time": dep_time,
                    "eta": eta,
                    "faa_ts": pos_time,
                    "flight_status": status,
                    "source": "swim-sfdps",
                    "source_facility": source_facility,
                    "track_key": "",
                    "aircraft_type": "",
                }

                self.publish_plane(plane)

            except Exception as e:
                logger.debug("SFDPS record skipped: %s", e)

    def parse_stdds(self, root):
        src = get_first(root, "/*[local-name()='TATrackAndFlightPlan']/*[local-name()='src']/text()")
        for record in root.xpath("//*[local-name()='record']"):
            try:
                track_node = record.xpath(".//*[local-name()='track']")
                if not track_node:
                    continue
                track = track_node[0]

                lat = get_first(track, ".//*[local-name()='lat']/text()")
                lon = get_first(track, ".//*[local-name()='lon']/text()")
                if not lat or not lon:
                    continue

                track_num = get_first(track, ".//*[local-name()='trackNum']/text()")
                ac_addr = get_first(track, ".//*[local-name()='acAddress']/text()")
                icao_hex = ac_addr.upper() if ac_addr and ac_addr != "000000" else ""
                beacon = get_first(track, ".//*[local-name()='reportedBeaconCode']/text()")
                altitude = get_first(track, ".//*[local-name()='reportedAltitude']/text()", "0")
                status = get_first(track, ".//*[local-name()='status']/text()", "")

                vx = safe_float(get_first(track, ".//*[local-name()='vx']/text()", 0))
                vy = safe_float(get_first(track, ".//*[local-name()='vy']/text()", 0))
                vvert = get_first(track, ".//*[local-name()='vVert']/text()", "")
                speed = round(math.sqrt(vx ** 2 + vy ** 2), 1)
                heading = calc_heading(vx, vy)

                callsign = get_first(record, ".//*[local-name()='flightPlan']/*[local-name()='acid']/text()")
                operator = ""
                dep = get_first(record, ".//*[local-name()='enhancedData']/*[local-name()='departureAirport']/text()")
                arr = get_first(record, ".//*[local-name()='enhancedData']/*[local-name()='destinationAirport']/text()")
                gufi = get_first(record, ".//*[local-name()='enhancedData']/*[local-name()='sfdpsGufi']/text()")

                resolved_flight_id = resolve_flight_id_by_icao(icao_hex) if icao_hex else None

                if resolved_flight_id:
                    flight_id = resolved_flight_id
                else:
                    flight_id = build_flight_id(
                        gufi=gufi,
                        callsign=callsign,
                        icao_hex=icao_hex,
                        source="stdds",
                        source_facility=src,
                        track_num=track_num
                    )

                if not callsign:
                    callsign = f"{src}T{track_num}" if src and track_num else (track_num or flight_id)

                plane = {
                    "flight_id": flight_id,
                    "callsign": callsign,
                    "gufi": gufi,
                    "icao_hex": icao_hex,
                    "operator": operator,
                    "dep": dep,
                    "arr": arr,
                    "lat": lat,
                    "lon": lon,
                    "speed": speed,
                    "heading": heading,
                    "alt": altitude,
                    "assigned_alt": "",
                    "vertical_rate": vvert,
                    "dep_time": "",
                    "eta": "",
                    "faa_ts": get_first(record, ".//*[local-name()='recSAFAReceiptTime']/text()"),
                    "flight_status": status.upper(),
                    "source": f"swim-stdds-{src.lower()}" if src else "swim-stdds",
                    "source_facility": src,
                    "track_key": f"{src}:{track_num}" if src and track_num else track_num,
                    "aircraft_type": "",
                    "beacon": beacon,
                }

                self.publish_plane(plane)

            except Exception as e:
                logger.debug("STDDS record skipped: %s", e)

    def parse_tfms(self, root):
        for msg in root.xpath("//*[local-name()='fltdMessage']"):
            try:
                callsign = get_first(msg, "./@acid")
                operator = get_first(msg, "./@airline")
                dep = get_first(msg, "./@depArpt")
                arr = get_first(msg, "./@arrArpt")
                flight_ref = get_first(msg, "./@flightRef")
                source_facility = get_first(msg, "./@sourceFacility")
                status = get_first(msg, "./@msgType")

                gufi = get_first(msg, ".//*[local-name()='qualifiedAircraftId']/*[local-name()='gufi']/text()")

                lat_deg = get_first(msg, ".//*[local-name()='position']/*[local-name()='latitude']/*[local-name()='latitudeDMS']/@degrees")
                lat_min = get_first(msg, ".//*[local-name()='position']/*[local-name()='latitude']/*[local-name()='latitudeDMS']/@minutes")
                lat_dir = get_first(msg, ".//*[local-name()='position']/*[local-name()='latitude']/*[local-name()='latitudeDMS']/@direction")

                lon_deg = get_first(msg, ".//*[local-name()='position']/*[local-name()='longitude']/*[local-name()='longitudeDMS']/@degrees")
                lon_min = get_first(msg, ".//*[local-name()='position']/*[local-name()='longitude']/*[local-name()='longitudeDMS']/@minutes")
                lon_dir = get_first(msg, ".//*[local-name()='position']/*[local-name()='longitude']/*[local-name()='longitudeDMS']/@direction")

                if not all([lat_deg, lat_min, lat_dir, lon_deg, lon_min, lon_dir]):
                    continue

                lat = dms_to_decimal(lat_deg, lat_min, lat_dir)
                lon = dms_to_decimal(lon_deg, lon_min, lon_dir)

                speed = safe_float(get_first(msg, ".//*[local-name()='speed']/text()", 0))

                alt_raw = get_first(msg, ".//*[local-name()='reportedAltitude']//*[local-name()='simpleAltitude']/text()", "0")
                alt_num = safe_float(alt_raw)
                alt = int(alt_num * 100) if alt_num < 1000 else int(alt_num)

                pos_time = get_first(msg, ".//*[local-name()='timeAtPosition']/text()")

                next_lat = get_first(msg, ".//*[local-name()='nextEvent']/@latitudeDecimal")
                next_lon = get_first(msg, ".//*[local-name()='nextEvent']/@longitudeDecimal")
                if next_lat and next_lon:
                    heading = heading_from_points(lat, lon, float(next_lat), float(next_lon))
                else:
                    heading = 0.0

                flight_id = build_flight_id(
                    gufi=gufi,
                    callsign=callsign,
                    source="tfms",
                    source_facility=source_facility,
                    flight_ref=flight_ref
                )

                plane = {
                    "flight_id": flight_id,
                    "callsign": callsign,
                    "gufi": gufi,
                    "icao_hex": "",
                    "operator": operator,
                    "dep": dep,
                    "arr": arr,
                    "lat": lat,
                    "lon": lon,
                    "speed": speed,
                    "heading": heading,
                    "alt": alt,
                    "assigned_alt": "",
                    "vertical_rate": "",
                    "dep_time": "",
                    "eta": get_first(msg, ".//*[local-name()='eta']/@timeValue"),
                    "faa_ts": pos_time,
                    "flight_status": status,
                    "source": "swim-tfms",
                    "source_facility": source_facility,
                    "track_key": flight_ref,
                    "aircraft_type": get_first(msg, ".//*[local-name()='qualifiedAircraftId']/@aircraftCategory"),
                }

                self.publish_plane(plane)

            except Exception as e:
                logger.debug("TFMS record skipped: %s", e)


def connect_vpn(vpn_name, queue_name, tcp_host):
    props = {
        "solace.messaging.transport.host": tcp_host,
        "solace.messaging.service.vpn-name": vpn_name,
        "solace.messaging.authentication.scheme.basic.username": os.getenv("FAA_USER"),
        "solace.messaging.authentication.scheme.basic.password": os.getenv("FAA_PASS"),
    }

    svc = MessagingService.builder().from_properties(props).build()
    svc.connect()
    logger.info("Connected to VPN %s via %s", vpn_name, tcp_host)

    q = Queue.durable_exclusive_queue(queue_name)
    rcv = svc.create_persistent_message_receiver_builder().build(q)
    rcv.start()
    handler = SWIMHandler(vpn_name)
    rcv.receive_async(handler)
    logger.info("Listening on queue %s", queue_name)
    return svc


def run():
    services = []

    queue_sfdps = os.getenv("QUEUE_SFDPS")
    queue_stdds = os.getenv("QUEUE_STDDS")
    queue_tfms = os.getenv("QUEUE_TFMS")

    if queue_sfdps:
        services.append(connect_vpn("FDPS", queue_sfdps, "tcp://127.0.0.1:55003"))

    if queue_stdds:
        services.append(connect_vpn("STDDS", queue_stdds, "tcp://127.0.0.1:55003"))

    if queue_tfms:
        services.append(connect_vpn("TFMS", queue_tfms, "tcp://127.0.0.1:55004"))

    logger.info("All configured VPNs connected")

    try:
        while True:
            time.sleep(60)
            logger.info("Heartbeat: ingestor alive")
    except KeyboardInterrupt:
        for svc in services:
            svc.disconnect()


if __name__ == "__main__":
    run()
