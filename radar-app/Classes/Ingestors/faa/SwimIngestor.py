import os
import json
import time
import math
import shutil
import socket
import subprocess
from lxml import etree

from Classes.Redis import getRedis
from ..BaseIngestor import BaseIngestor
from solace.messaging.messaging_service import MessagingService
from solace.messaging.resources.queue import Queue
from solace.messaging.receiver.message_receiver import MessageHandler


class SwimIngestor(BaseIngestor):
    VPNS = [
        {"name": "ems1", "host": "ems1.swim.faa.gov", "port": 55443, "local": 55003},
        {"name": "ems2", "host": "ems2.swim.faa.gov", "port": 55443, "local": 55004},
    ]

    def __init__(self):
        super().__init__()
        self.redis = None
        self.stunnel_proc = None
        self.services = []
        self.receivers = []
        self.handlers = []
        self.stats = {
            "FDPS": {"count": 0, "last": 0},
            "STDDS": {"count": 0, "last": 0},
            "TFMS": {"count": 0, "last": 0},
        }

    def latest_message_age(self):
        if not self.handlers:
            return None
        latest = max((h.last_message_time for h in self.handlers), default=0)
        if latest <= 0:
            return None
        return time.time() - latest


    def reconnect_all(self):
        print("[SwimIngestor] Reconnecting all FAA sessions")
        self.disconnect_all()
        self._stop_stunnel()
        time.sleep(2)
        self.start_stunnel()
        self.connect_all()



    def run(self):
        self.redis = getRedis()
        self.start_stunnel()
        self.connect_all()

        try:
            while self._running:
                time.sleep(30)

                age = self.latest_message_age()
                if age is None:
                    print("[SwimIngestor] Heartbeat: ingestor alive, no handler activity yet")
                    continue

                print(f"[SwimIngestor] Heartbeat: ingestor alive, last FAA message {int(age)}s ago")

                if age > 120:
                    print(f"[SwimIngestor] No FAA messages for {int(age)}s, forcing reconnect")
                    self.reconnect_all()

        finally:
            self.disconnect_all()
            self._stop_stunnel()

    def stop(self):
        super().stop()
        self.disconnect_all()
        self._stop_stunnel()

    def disconnect_all(self):
        for rcv in self.receivers:
            try:
                rcv.terminate()
            except Exception:
                pass
        self.receivers = []
        self.handlers = []

        for svc in self.services:
            try:
                svc.disconnect()
            except Exception:
                pass
        self.services = []

    def _stop_stunnel(self):
        if self.stunnel_proc:
            try:
                if self.stunnel_proc.poll() is None:
                    self.stunnel_proc.terminate()
                    self.stunnel_proc.wait(timeout=5)
            except Exception:
                try:
                    self.stunnel_proc.kill()
                except Exception:
                    pass
            self.stunnel_proc = None

    def _read_stunnel_log(self):
        path = "/tmp/stunnel.log"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            return f"[unable to read {path}: {e}]"

    def build_stunnel_conf(self):
        lines = [
            "foreground = yes",
            "debug = 7",
            "output = /tmp/stunnel.log",
            "pid = /tmp/stunnel.pid",
        ]

        for vpn in self.VPNS:
            lines.extend([
                f"[faa-{vpn['name']}]",
                "client = yes",
                f"accept = 127.0.0.1:{vpn['local']}",
                f"connect = {vpn['host']}:{vpn['port']}",
                "verify = 0",
                f"sni = {vpn['host']}",
                "",
            ])

        return "\n".join(lines) + "\n"

    def start_stunnel(self):
        self._stop_stunnel()

        conf = self.build_stunnel_conf()
        conf_path = "/tmp/stunnel.conf"

        with open(conf_path, "w", encoding="ascii", newline="\n") as f:
            f.write(conf)

        print("[SwimIngestor] Using stunnel config:")
        print(conf)

        stunnel_bin = shutil.which("stunnel") or shutil.which("stunnel4")
        if not stunnel_bin:
            raise Exception("stunnel not found in container")

        print(f"[SwimIngestor] Starting stunnel ({stunnel_bin})")

        try:
            os.remove("/tmp/stunnel.log")
        except FileNotFoundError:
            pass
        except Exception:
            pass

        self.stunnel_proc = subprocess.Popen(
            [stunnel_bin, conf_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        time.sleep(2)

        if self.stunnel_proc.poll() is not None:
            print("[SwimIngestor] stunnel startup log:")
            print(self._read_stunnel_log())
            raise Exception("stunnel exited immediately")

        for vpn in self.VPNS:
            if not self.wait_for_listener(vpn["local"], timeout=5):
                print("[SwimIngestor] stunnel startup log:")
                print(self._read_stunnel_log())
                raise Exception(f"stunnel did not open listener on 127.0.0.1:{vpn['local']}")

        print("[SwimIngestor] Stunnel ready")

    def wait_for_listener(self, port, timeout=5):
        start = time.time()
        while time.time() - start < timeout:
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=1)
                s.close()
                return True
            except Exception:
                time.sleep(0.25)
        return False

    def connect_all(self):
        queue_sfdps = os.getenv("QUEUE_SFDPS")
        queue_stdds = os.getenv("QUEUE_STDDS")
        queue_tfms = os.getenv("QUEUE_TFMS")

        if queue_sfdps:
            self.services.append(self.connect_vpn("FDPS", queue_sfdps, "tcp://127.0.0.1:55003"))

        if queue_stdds:
            self.services.append(self.connect_vpn("STDDS", queue_stdds, "tcp://127.0.0.1:55003"))

        if queue_tfms:
            self.services.append(self.connect_vpn("TFMS", queue_tfms, "tcp://127.0.0.1:55004"))

        print("[SwimIngestor] All configured VPNs connected")

    def connect_vpn(self, vpn_name, queue_name, tcp_host):
        props = {
            "solace.messaging.transport.host": tcp_host,
            "solace.messaging.service.vpn-name": vpn_name,
            "solace.messaging.authentication.scheme.basic.username": os.getenv("FAA_USER"),
            "solace.messaging.authentication.scheme.basic.password": os.getenv("FAA_PASS"),
        }

        svc = MessagingService.builder().from_properties(props).build()
        svc.connect()
        print(f"[SwimIngestor] Connected to VPN {vpn_name} via {tcp_host}")

        q = Queue.durable_exclusive_queue(queue_name)
        rcv = svc.create_persistent_message_receiver_builder().build(q)
        rcv.start()

        handler = SWIMHandler(vpn_name, self.redis, self.stats)
        rcv.receive_async(handler)

        self.receivers.append(rcv)
        self.handlers.append(handler)

        print(f"[SwimIngestor] Listening on queue {queue_name}")
        return svc


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


def resolve_flight_id_by_icao(redis_client, icao_hex):
    if not icao_hex or icao_hex == "000000":
        return None
    try:
        fid = redis_client.get(f"corr:icao:{icao_hex.upper()}")
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
    def __init__(self, vpn_name, redis_client, stats):
        super().__init__()
        self.count = 0
        self.vpn = vpn_name
        self.redis = redis_client
        self.stats = stats
        self.last_message_time = time.time()

    def on_message(self, message):
        self.count += 1
        self.last_message_time = time.time()

        if self.count % 500 == 0:
            print(f"[SwimIngestor] [{self.vpn}] processed {self.count} messages")

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
            print(f"[SwimIngestor] parse failure: {e}")

    def publish_plane(self, plane):
        self.redis.client.publish("live_planes", json.dumps(plane))
        self.stats[self.vpn]["count"] += 1
        self.stats[self.vpn]["last"] = time.time()

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
                    "source": "faa-sfdps",
                    "source_facility": source_facility,
                    "track_key": "",
                    "aircraft_type": "",
                }

                self.publish_plane(plane)

            except Exception:
                continue

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

                resolved_flight_id = resolve_flight_id_by_icao(self.redis.client, icao_hex) if icao_hex else None

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
                    "source": f"faa-stdds-{src.lower()}" if src else "faa-stdds",
                    "source_facility": src,
                    "track_key": f"{src}:{track_num}" if src and track_num else track_num,
                    "aircraft_type": "",
                    "beacon": beacon,
                }

                self.publish_plane(plane)

            except Exception:
                continue

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
                    "source": "faa-tfms",
                    "source_facility": source_facility,
                    "track_key": flight_ref,
                    "aircraft_type": get_first(msg, ".//*[local-name()='qualifiedAircraftId']/@aircraftCategory"),
                }

                self.publish_plane(plane)

            except Exception:
                continue


_ingestor = None

def getSwimIngestor():
    global _ingestor
    if _ingestor is None:
        _ingestor = SwimIngestor()
    return _ingestor


if __name__ == "__main__":
    ingestor = getSwimIngestor()
    ingestor._running = True
    ingestor.run()