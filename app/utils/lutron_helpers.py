import socket
import ssl
import json


from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.base import JobLookupError
from datetime import datetime, timedelta
import pytz


from app.utils.definitions import (
    LAP_LUTRON_ROOT_FILE,
    LEAP_SIGNED_CSR_FILE,
    LEAP_PRIVATE_KEY_FILE
)


def is_processor_reachable(ip: str, port: int = 8081, timeout: int = 3) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, socket.error):
        return False


def get_proc_hostname(system, mac):
    return f"{system}-{mac}-server"


def get_leap_ssl_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=LAP_LUTRON_ROOT_FILE)
    context.load_cert_chain(certfile=LEAP_SIGNED_CSR_FILE, keyfile=LEAP_PRIVATE_KEY_FILE)
    context.check_hostname = False
    return context


def connect_to_processor(ipv4, hostname, port=8081):
    context = get_leap_ssl_context()
    sock = socket.create_connection((ipv4, port), timeout=5)
    return context.wrap_socket(sock, server_hostname=hostname)


def send_json(sock, json_msg):
    msg = (json.dumps(json_msg) + "\r\n").encode("ASCII")
    sock.sendall(msg)


# def recv_json(sock):
#     buffer = b""
#     while not buffer.endswith(b"\r\n"):
#         chunk = sock.recv(4096)
#         if not chunk:
#             break
#         buffer += chunk

#     try:
#         return json.loads(buffer.decode("ASCII").strip())
#     except json.JSONDecodeError as e:
#         print(" JSON Decode Error:", e)
#         print(" Raw received data:\n", buffer.decode("ASCII", errors="replace"))
#         raise



def recv_json(sock):
    buffer = b""
    while True:
        chunk = sock.recv(4096)  # Receive in 4KB chunks
        if not chunk:
            break
        buffer += chunk
        try:
            return json.loads(buffer.decode("utf-8"))
        except json.JSONDecodeError:
            continue  # Keep reading if JSON is incomplete
    return None



def get_root_area(sock):
    send_json(sock, {
        "CommuniqueType": "ReadRequest",
        "Header": {
            "Url": "/area/rootarea"
        }
    })
    response = recv_json(sock)
    if response and "Body" in response and "Area" in response["Body"]:
        return response["Body"]["Area"]
    return None


def leap_handshake(sock):
    send_json(sock, {
        "CommuniqueType": "ReadRequest",
        "Header": {
            "Url": "/server/1/status"
        }
    })
    return recv_json(sock)




def get_child_areas(area_href, sock):
    send_json(sock, {
        "CommuniqueType": "ReadRequest",
        "Header": {
            "Url": f"{area_href}/childarea/summary"
        }
    })
    response = recv_json(sock)
    return response.get("Body", {}).get("AreaSummaries", [])


def read_area_details(area_href, sock):
    request = {
        "CommuniqueType": "ReadRequest",
        "Header": {
            "Url": area_href
        }
    }
    send_json(sock, request)
    response = recv_json(sock)
    return response.get("Body", {}).get("Area", {})

def get_occupancy_mapping(sock, area_code: str):
    from app.utils.lutron_helpers import send_json, recv_json

    def get_control_stations(sock, area_href):
        send_json(sock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"{area_href}/associatedcontrolstation"}
        })
        resp = recv_json(sock)
        return resp.get("Body", {}).get("ControlStations", [])

    def get_buttons(sock, device_href):
        send_json(sock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"{device_href}/buttongroup/expanded"}
        })
        resp = recv_json(sock)
        buttons = []
        for group in resp.get("Body", {}).get("ButtonGroupsExpanded", []):
            buttons.extend(group.get("Buttons", []))
        return buttons

    area_href = f"/area/{area_code}"
    mapping = {}
    control_stations = get_control_stations(sock, area_href)

    for cs in control_stations:
        for device in cs.get("AssociatedGangedDevices", []):
            dev_href = device.get("Device", {}).get("href", "")
            if not dev_href:
                continue
            buttons = get_buttons(sock, dev_href)

            for b in buttons:
                engraving = b.get("Engraving", {}).get("Text", "").lower()
                button_href = b.get("href", "")
                if not button_href or not engraving:
                    continue

                button_id = int(button_href.split("/")[-1])
                led_id = button_id - 1  # Assumption based on your system

                if "enable" in engraving and "disable" not in engraving:
                    mapping["Auto"] = {"button_id": button_id, "led_id": led_id}
                elif "vacancy" in engraving or "vacant" in engraving:
                    mapping["Vacancy"] = {"button_id": button_id, "led_id": led_id}
                elif "disable" in engraving:
                    mapping["Disabled"] = {"button_id": button_id, "led_id": led_id}

    return mapping




scheduler = BackgroundScheduler(timezone="Asia/Kolkata")  # Use your timezone
scheduler.start()
