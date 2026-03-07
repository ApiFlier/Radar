import os, time, logging, redis, json, math
from lxml import etree
from dotenv import load_dotenv
from solace.messaging.messaging_service import MessagingService
from solace.messaging.resources.queue import Queue
from solace.messaging.receiver.message_receiver import MessageHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SWIM-Ingestor")
load_dotenv()

r = redis.Redis(host=os.getenv('REDIS_HOST', 'localhost'), port=6379, decode_responses=True)


def calc_heading(vx, vy):
    if vx == 0 and vy == 0:
        return 0.0
    hdg = math.degrees(math.atan2(vx, vy))
    return round((hdg + 360) % 360, 1)


def get_supplemental(flight, name):
    nodes = flight.xpath(
        f".//*[local-name()='nameValue'][@name='{name}']/@value"
    )
    return nodes[0] if nodes else ""


def resolve_icao(icao_hex):
    """Look up callsign from the ICAO mapping table built by the core worker."""
    if not icao_hex or icao_hex == "000000":
        return None
    try:
        data = r.hgetall(f"icao:{icao_hex}")
        if data and data.get("callsign"):
            return data
    except Exception:
        pass
    return None


class SWIMHandler(MessageHandler):
    def __init__(self, vpn_name):
        super().__init__()
        self.count = 0
        self.vpn = vpn_name
        self.stdds_resolved = 0
        self.stdds_unresolved = 0

    def on_message(self, message):
        self.count += 1
        if self.count % 500 == 0:
            logger.info(f"[{self.vpn}] Processed {self.count} messages")
            if self.vpn == "STDDS" and (self.stdds_resolved + self.stdds_unresolved) > 0:
                total = self.stdds_resolved + self.stdds_unresolved
                pct = round(self.stdds_resolved / total * 100, 1)
                logger.info(f"[STDDS] Resolved {self.stdds_resolved}/{total} ({pct}%) callsigns via ICAO lookup")

        try:
            payload = message.get_payload_as_string()
            if not payload:
                raw = message.get_payload_as_bytes()
                if raw:
                    payload = raw.decode('utf-8', errors='ignore')
            if not payload or "<" not in payload:
                return

            root = etree.fromstring(payload.encode('utf-8'))

            # ---- SFDPS / FIXM flight tracks ----
            for flight in root.xpath("//*[local-name()='flight']"):
                try:
                    cid = flight.xpath(
                        ".//*[local-name()='flightIdentification']/@aircraftIdentification"
                    )
                    pos = flight.xpath(".//*[local-name()='pos']/text()")

                    if not cid or not pos:
                        continue
                    parts = pos[0].strip().split()
                    if len(parts) != 2:
                        continue

                    lat, lon = parts

                    spd = flight.xpath(".//*[local-name()='surveillance']/text()")
                    alt = flight.xpath(
                        ".//*[local-name()='enRoute']//*[local-name()='altitude']/text()"
                    )

                    vx_node = flight.xpath(
                        ".//*[local-name()='trackVelocity']/*[local-name()='x']/text()"
                    )
                    vy_node = flight.xpath(
                        ".//*[local-name()='trackVelocity']/*[local-name()='y']/text()"
                    )
                    vx = float(vx_node[0]) if vx_node else 0.0
                    vy = float(vy_node[0]) if vy_node else 0.0
                    heading = calc_heading(vx, vy)

                    dep = flight.xpath(".//*[local-name()='departure']/@departurePoint")
                    arr = flight.xpath(".//*[local-name()='arrival']/@arrivalPoint")

                    dep_time = flight.xpath(
                        ".//*[local-name()='departure']//*[local-name()='actual']/@time"
                    )
                    eta = flight.xpath(
                        ".//*[local-name()='arrival']//*[local-name()='estimated']/@time"
                    )
                    pos_time = flight.xpath(".//*[local-name()='position']/@positionTime")

                    fstatus = flight.xpath(
                        ".//*[local-name()='flightStatus']/@fdpsFlightStatus"
                    )
                    operator = flight.xpath(".//*[local-name()='organization']/@name")

                    assigned = flight.xpath(
                        ".//*[local-name()='assignedAltitude']//*[local-name()='simple']/text()"
                    )

                    icao_raw = get_supplemental(flight, "ADSB_02M_52B")
                    icao_hex = icao_raw.lstrip("-") if icao_raw else ""

                    speed = float(spd[0]) if spd else 0.0

                    plane = {
                        "callsign": cid[0],
                        "lat": lat,
                        "lon": lon,
                        "speed": speed,
                        "heading": heading,
                        "alt": alt[0] if alt else "0",
                        "assigned_alt": assigned[0] if assigned else "",
                        "dep": dep[0] if dep else "",
                        "arr": arr[0] if arr else "",
                        "dep_time": dep_time[0] if dep_time else "",
                        "eta": eta[0] if eta else "",
                        "faa_ts": pos_time[0] if pos_time else "",
                        "flight_status": fstatus[0] if fstatus else "",
                        "operator": operator[0] if operator else "",
                        "icao_hex": icao_hex,
                        "source": "swim-sfdps"
                    }

                    r.publish("live_planes", json.dumps(plane))

                except Exception:
                    continue

            # ---- STDDS Terminal (STARS TRACON tracks) ----
            for record in root.xpath("//*[local-name()='record']"):
                try:
                    track_node = record.xpath(".//*[local-name()='track']")
                    if not track_node:
                        continue
                    track = track_node[0]

                    lat_node = track.xpath(".//*[local-name()='lat']/text()")
                    lon_node = track.xpath(".//*[local-name()='lon']/text()")
                    if not lat_node or not lon_node:
                        continue

                    lat = lat_node[0]
                    lon = lon_node[0]

                    # Get ICAO hex — try acAddress field
                    ac_addr = track.xpath(".//*[local-name()='acAddress']/text()")
                    icao_hex = ac_addr[0] if ac_addr and ac_addr[0] != "000000" else ""

                    # Get squawk code
                    beacon = track.xpath(".//*[local-name()='reportedBeaconCode']/text()")

                    # Velocity components
                    vx_node = track.xpath(".//*[local-name()='vx']/text()")
                    vy_node = track.xpath(".//*[local-name()='vy']/text()")
                    vx = float(vx_node[0]) if vx_node else 0.0
                    vy = float(vy_node[0]) if vy_node else 0.0
                    # STDDS velocity is in different units, compute rough speed
                    speed = round(math.sqrt(vx**2 + vy**2), 1)
                    heading = calc_heading(vx, vy)

                    alt_node = track.xpath(".//*[local-name()='reportedAltitude']/text()")
                    altitude = alt_node[0] if alt_node else "0"

                    # Track number as fallback ID
                    tnum = track.xpath(".//*[local-name()='trackNum']/text()")

                    # Source facility
                    src_node = record.xpath("ancestor::*[local-name()='TATrackAndFlightPlan']/*[local-name()='src']/text()")
                    src = src_node[0] if src_node else ""

                    # Try to resolve callsign from ICAO lookup table
                    callsign = None
                    resolved_data = None
                    if icao_hex:
                        resolved_data = resolve_icao(icao_hex)
                        if resolved_data:
                            callsign = resolved_data["callsign"]
                            self.stdds_resolved += 1
                        else:
                            self.stdds_unresolved += 1

                    if not callsign:
                        # Use track number as ID — prefixed with facility
                        if tnum:
                            callsign = f"{src}T{tnum[0]}" if src else f"T{tnum[0]}"
                        else:
                            continue

                    plane = {
                        "callsign": callsign,
                        "lat": lat,
                        "lon": lon,
                        "speed": speed,
                        "heading": heading,
                        "alt": altitude,
                        "assigned_alt": "",
                        "dep": resolved_data.get("dep", "") if resolved_data else "",
                        "arr": resolved_data.get("arr", "") if resolved_data else "",
                        "dep_time": "",
                        "eta": "",
                        "faa_ts": "",
                        "flight_status": "",
                        "operator": resolved_data.get("operator", "") if resolved_data else "",
                        "icao_hex": icao_hex,
                        "source": f"swim-stdds-{src.lower()}" if src else "swim-stdds"
                    }

                    r.publish("live_planes", json.dumps(plane))

                except Exception:
                    continue

        except Exception:
            pass


def connect_vpn(vpn_name, queue_name):
    props = {
        "solace.messaging.transport.host": "tcp://127.0.0.1:55003",
        "solace.messaging.service.vpn-name": vpn_name,
        "solace.messaging.authentication.scheme.basic.username": os.getenv("FAA_USER"),
        "solace.messaging.authentication.scheme.basic.password": os.getenv("FAA_PASS")
    }
    svc = MessagingService.builder().from_properties(props).build()
    svc.connect()
    logger.info(f"Connected to VPN: {vpn_name}")

    q = Queue.durable_exclusive_queue(queue_name)
    rcv = svc.create_persistent_message_receiver_builder().build(q)
    rcv.start()
    rcv.receive_async(SWIMHandler(vpn_name))
    logger.info(f"Listening on: {queue_name}")
    return svc


def run():
    services = []
    services.append(connect_vpn("FDPS", os.getenv("QUEUE_SFDPS")))
    services.append(connect_vpn("STDDS", os.getenv("QUEUE_STDDS")))
    logger.info("All VPNs connected. Ingesting...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        for svc in services:
            svc.disconnect()


if __name__ == "__main__":
    run()
