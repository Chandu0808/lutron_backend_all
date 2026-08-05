import csv
import io
from datetime import datetime
from typing import Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.models.processor import Processor
from app.utils.json_connection import connect_to_processor, send_json, recv_json

DEVICE_CSV_BASE_HEADERS = [
    "ProcessorIP",
    "Location",
    "DeviceName",
    "Model",
    "Serial",
    "AddressedState",
    "Availability",
]

OCCUPANCY_COLUMN = "Occupancy"
DEVICES_TYPE = "devices"
KEYPAD_TYPE = "keypad"
SENSORS_TYPE = "sensors"
DRIVERS_TYPE = "drivers"
OTHERS_TYPE = "others"
AWN_RF_TYPE = "awn_rf"
AWN_OCC_TYPE = "awn_occ"
OCCUPANCY_COLUMN_TYPES = {SENSORS_TYPE, AWN_RF_TYPE, AWN_OCC_TYPE}


def _normalized_model(model: Optional[str]) -> str:
    return (model or "").lower().replace("-", "").replace("_", "")


def classify_device_type(model: Optional[str]) -> str:
    model_lower = (model or "").lower()
    normalized = _normalized_model(model)
    if "awn" in normalized and "rf" in model_lower:
        return AWN_RF_TYPE
    if "awn" in normalized and "occ" in model_lower:
        return AWN_OCC_TYPE
    if "lrf" in model_lower:
        return SENSORS_TYPE
    if "dali" in model_lower or "ballast" in model_lower:
        return DRIVERS_TYPE
    if "pn" in model_lower or "pj" in model_lower or "pm" in model_lower:
        return KEYPAD_TYPE
    if "qs" in model_lower or "qw" in model_lower:
        return DEVICES_TYPE
    return OTHERS_TYPE


def _device_csv_headers(device_types: Set[str]) -> List[str]:
    headers = list(DEVICE_CSV_BASE_HEADERS)
    if device_types & OCCUPANCY_COLUMN_TYPES:
        headers.append(OCCUPANCY_COLUMN)
    return headers


OCCUPANCY_MODE_CSV_HEADERS = [
    "ProcessorIP",
    "AreaID",
    "Location",
    "OccupancyMode",
    "LED_Auto",
    "LED_Vacancy",
    "LED_Disabled",
]
OCCUPANCY_MODE_TYPE = "occupancy_mode"


def format_serial(serial_number) -> str:
    if serial_number is None or serial_number == "":
        return ""
    if isinstance(serial_number, int):
        return f"0{serial_number:X}"
    text = str(serial_number).strip()
    if text.lower().startswith("0x"):
        return "0" + text[2:].upper()
    return text


def serial_for_maintenance_csv(serial_number) -> str:
    """Format serial for CSV export; Excel preserves leading zeros via =\"...\"."""
    serial = format_serial(serial_number)
    if not serial:
        return ""
    return f'="{serial}"'


def _resolve_area_path(sock, area_code: str, cache: Dict[str, str]) -> str:
    if not area_code:
        return ""
    if area_code in cache:
        return cache[area_code]

    path_parts: List[str] = []
    current_href = f"/area/{area_code}"

    while current_href:
        send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": current_href}})
        resp = recv_json(sock)
        area = (resp or {}).get("Body", {}).get("Area")
        if not area:
            break

        name = area.get("Name")
        if name:
            path_parts.insert(0, name)

        parent = area.get("Parent") or {}
        current_href = parent.get("href") if isinstance(parent, dict) else None

    path = " > ".join(path_parts)
    cache[area_code] = path
    return path


def _get_area_occupancy_status(sock, area_code: Optional[str], cache: Dict[str, str]) -> str:
    """Return processor AreaStatus.OccupancyStatus as-is, or empty when unavailable."""
    if not area_code:
        return ""
    if area_code in cache:
        return cache[area_code]

    result = ""
    try:
        send_json(
            sock,
            {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"/area/{area_code}/status"},
            },
        )
        resp = recv_json(sock)
        status = (resp or {}).get("Body", {}).get("AreaStatus", {}).get("OccupancyStatus")
        if status is not None and status != "":
            result = str(status)
    except Exception:
        result = ""
    cache[area_code] = result
    return result


def _fetch_processor_rows(processor: Processor, requested_types: Set[str]) -> List[dict]:
    sock = connect_to_processor(
        ip=processor.ipv4,
        mac=processor.mac,
        system=processor.system,
        processor_ipv4=processor.ipv4,
    )
    if not sock:
        raise ConnectionError("Could not connect to processor")

    rows: List[dict] = []
    area_path_cache: Dict[str, str] = {}
    area_occupancy_cache: Dict[str, str] = {}
    include_occupancy = bool(requested_types & OCCUPANCY_COLUMN_TYPES)

    try:
        send_json(
            sock,
            {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": "/device/status/availability"},
            },
        )
        resp = recv_json(sock)
        statuses = (resp or {}).get("Body", {}).get("DeviceAvailabilityStatuses") or []

        for dev in statuses:
            href = (
                dev.get("Device", {}).get("href")
                if isinstance(dev.get("Device"), dict)
                else dev.get("Device")
            )
            if not href:
                continue

            try:
                device_code = int(href.strip("/").split("/")[-1])
            except (TypeError, ValueError):
                continue

            availability = dev.get("Availability") or ""

            send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": href}})
            dev_resp = recv_json(sock)
            dev_info = (dev_resp or {}).get("Body", {}).get("Device") or {}
            if not dev_info:
                continue

            device_model = dev_info.get("ModelNumber") or ""
            device_type = classify_device_type(device_model)
            if device_type not in requested_types:
                continue

            area_code = None
            area_field = dev_info.get("AssociatedArea") or dev_info.get("Area")
            if isinstance(area_field, dict) and area_field.get("href"):
                area_code = str(area_field["href"].strip("/").split("/")[-1])

            occupancy = ""
            if include_occupancy and device_type in OCCUPANCY_COLUMN_TYPES:
                occupancy = _get_area_occupancy_status(sock, area_code, area_occupancy_cache)

            row = {
                "ProcessorIP": processor.ipv4 or "",
                "Location": _resolve_area_path(sock, area_code, area_path_cache) if area_code else "",
                "DeviceName": dev_info.get("Name") or "",
                "Model": device_model,
                "Serial": serial_for_maintenance_csv(dev_info.get("SerialNumber")),
                "AddressedState": dev_info.get("AddressedState") or "",
                "Availability": availability,
            }
            if include_occupancy:
                row[OCCUPANCY_COLUMN] = occupancy
            rows.append(row)
    finally:
        try:
            sock.close()
        except Exception:
            pass

    return rows


def _read_led_state_via_button(sock, button_href: str) -> str:
    send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": button_href}})
    button_resp = recv_json(sock) or {}
    led_href = (
        button_resp.get("Body", {})
        .get("Button", {})
        .get("AssociatedLED", {})
        .get("href")
    )
    if not led_href:
        return ""
    send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": f"{led_href}/status"}})
    led_resp = recv_json(sock) or {}
    state = led_resp.get("Body", {}).get("LEDStatus", {}).get("State")
    return state if state else ""


def _resolve_occupancy_mode(led_states: Dict[str, str]) -> str:
    for mode, state in led_states.items():
        if state == "On":
            return mode
    for mode, state in led_states.items():
        if state == "Off":
            return mode
    return ""


def _process_area_occupancy(
    sock, area_id: str, processor_ip: str, area_path_cache: Dict[str, str]
) -> dict:
    row = {
        "ProcessorIP": processor_ip,
        "AreaID": area_id,
        "Location": _resolve_area_path(sock, area_id, area_path_cache),
        "OccupancyMode": "",
        "LED_Auto": "",
        "LED_Vacancy": "",
        "LED_Disabled": "",
    }
    try:
        send_json(
            sock,
            {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"/area/{area_id}/associatedcontrolstation"},
            },
        )
        cs_resp = recv_json(sock) or {}
        control_stations = cs_resp.get("Body", {}).get("ControlStations") or []
        occupancy_buttons: Dict[str, str] = {}
        for cs in control_stations:
            for ganged in cs.get("AssociatedGangedDevices", []):
                device_href = ganged.get("Device", {}).get("href")
                if not device_href:
                    continue
                send_json(
                    sock,
                    {
                        "CommuniqueType": "ReadRequest",
                        "Header": {"Url": f"{device_href}/buttongroup/expanded"},
                    },
                )
                bg_resp = recv_json(sock) or {}
                groups = bg_resp.get("Body", {}).get("ButtonGroupsExpanded") or []
                for group in groups:
                    for button in group.get("Buttons", []):
                        name = button.get("Name", "")
                        engraving = button.get("Engraving", {}).get("Text", "")
                        href = button.get("href")
                        if not href:
                            continue
                        text = f"{name} {engraving}".lower()
                        if "enable" in text or "auto" in text:
                            occupancy_buttons["Auto"] = href
                        elif "vacancy" in text:
                            occupancy_buttons["Vacancy"] = href
                        elif "disable" in text:
                            occupancy_buttons["Disabled"] = href
        led_states: Dict[str, str] = {}
        for mode, href in occupancy_buttons.items():
            led_state = _read_led_state_via_button(sock, href)
            led_states[mode] = led_state
            row[f"LED_{mode}"] = led_state
        row["OccupancyMode"] = _resolve_occupancy_mode(led_states)
    except Exception:
        pass
    return row


def _crawl_occupancy_rows(
    sock, area_id: str, processor_ip: str, rows: List[dict], area_path_cache: Dict[str, str]
) -> None:
    send_json(
        sock,
        {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area_id}/childarea/summary"},
        },
    )
    resp = recv_json(sock) or {}
    children = resp.get("Body", {}).get("AreaSummaries") or []
    for child in children:
        child_href = child.get("href", "")
        child_id = child_href.strip("/").split("/")[-1] if child_href else ""
        if child.get("IsLeaf", False):
            rows.append(_process_area_occupancy(sock, child_id, processor_ip, area_path_cache))
        else:
            _crawl_occupancy_rows(sock, child_id, processor_ip, rows, area_path_cache)


def _fetch_occupancy_mode_rows(processor: Processor) -> List[dict]:
    sock = connect_to_processor(
        ip=processor.ipv4,
        mac=processor.mac,
        system=processor.system,
        processor_ipv4=processor.ipv4,
    )
    if not sock:
        raise ConnectionError("Could not connect to processor")

    rows: List[dict] = []
    area_path_cache: Dict[str, str] = {}
    try:
        send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": "/area/rootarea"}})
        root_resp = recv_json(sock) or {}
        root_href = root_resp.get("Body", {}).get("Area", {}).get("href")
        if not root_href:
            return rows
        root_id = root_href.strip("/").split("/")[-1]
        _crawl_occupancy_rows(sock, root_id, processor.ipv4 or "", rows, area_path_cache)
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return rows


def _build_filename(types: List[str], now: Optional[datetime] = None) -> str:
    timestamp = now or datetime.now()
    type_part = "_".join(sorted(types))
    return f"{type_part}_{timestamp.strftime('%d-%m-%Y_%H-%M')}.csv"


def generate_maintenance_report(db: Session, types: List[str]) -> dict:
    requested_types = set(types)
    device_types = requested_types - {OCCUPANCY_MODE_TYPE}
    occupancy_requested = OCCUPANCY_MODE_TYPE in requested_types

    if occupancy_requested and device_types:
        return {
            "status": "error",
            "message": "occupancy_mode cannot be combined with device types",
            "processors_not_responding": [],
            "filename": None,
            "csv": None,
        }

    processors = db.query(Processor).filter_by(handshake_status=True).all()

    if not processors:
        has_any_processor = db.query(Processor).count() > 0
        return {
            "status": "error",
            "message": (
                "No processors with completed handshake"
                if has_any_processor
                else "No processors configured"
            ),
            "processors_not_responding": [],
            "filename": None,
            "csv": None,
        }

    all_rows: List[dict] = []
    processors_not_responding: List[str] = []
    any_responded = False

    for processor in processors:
        if not processor.ipv4 or not processor.mac or not processor.system:
            if processor.ipv4:
                processors_not_responding.append(processor.ipv4)
            continue

        try:
            if occupancy_requested:
                rows = _fetch_occupancy_mode_rows(processor)
            else:
                rows = _fetch_processor_rows(processor, device_types)
            any_responded = True
            all_rows.extend(rows)
        except Exception:
            processors_not_responding.append(processor.ipv4)

    if not any_responded:
        return {
            "status": "error",
            "message": "No processor is responding",
            "processors_not_responding": processors_not_responding,
            "filename": None,
            "csv": None,
        }

    csv_headers = (
        OCCUPANCY_MODE_CSV_HEADERS if occupancy_requested else _device_csv_headers(device_types)
    )
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=csv_headers)
    writer.writeheader()
    writer.writerows(all_rows)

    status = "partial" if processors_not_responding else "success"
    return {
        "status": status,
        "processors_not_responding": processors_not_responding,
        "filename": _build_filename(types),
        "csv": output.getvalue(),
    }
