import csv
import io
from datetime import datetime
from typing import Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.models.processor import Processor
from app.utils.json_connection import connect_to_processor, send_json, recv_json

CSV_HEADERS = [
    "ProcessorIP",
    "AreaPath",
    "DeviceName",
    "Model",
    "Serial",
    "AddressedState",
    "Availability",
    "Occupancy",
]


def classify_device_type(model: Optional[str]) -> str:
    model_lower = (model or "").lower()
    if "qsn" in model_lower or "qsm" in model_lower:
        return "devices"
    if "pn" in model_lower:
        return "keypad"
    if "lrf" in model_lower:
        return "sensors"
    if "ballast" in model_lower:
        return "drivers"
    return "others"


def format_serial(serial_number) -> str:
    if serial_number is None or serial_number == "":
        return ""
    if isinstance(serial_number, int):
        return f"0{serial_number:X}"
    text = str(serial_number).strip()
    if text.lower().startswith("0x"):
        return "0" + text[2:].upper()
    return text


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


def _get_device_occupancy(sock, device_code: int) -> str:
    send_json(
        sock,
        {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/device/{device_code}/status"},
        },
    )
    resp = recv_json(sock)
    if not resp:
        return ""

    body = resp.get("Body") or {}
    status_obj = body.get("DeviceStatus") or {}
    occupancy = status_obj.get("OccupancyStatus")
    return occupancy if occupancy is not None else ""


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

            availability = dev.get("Availability", "Unknown")

            send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": href}})
            dev_resp = recv_json(sock)
            dev_info = (dev_resp or {}).get("Body", {}).get("Device") or {}
            if not dev_info:
                continue

            device_model = dev_info.get("ModelNumber") or ""
            if classify_device_type(device_model) not in requested_types:
                continue

            area_code = None
            area_field = dev_info.get("AssociatedArea") or dev_info.get("Area")
            if isinstance(area_field, dict) and area_field.get("href"):
                area_code = str(area_field["href"].strip("/").split("/")[-1])

            occupancy = ""
            if "lrf" in device_model.lower():
                try:
                    occupancy = _get_device_occupancy(sock, device_code)
                except Exception:
                    occupancy = ""

            rows.append(
                {
                    "ProcessorIP": processor.ipv4 or "",
                    "AreaPath": _resolve_area_path(sock, area_code, area_path_cache) if area_code else "",
                    "DeviceName": dev_info.get("Name") or "",
                    "Model": device_model,
                    "Serial": format_serial(dev_info.get("SerialNumber")),
                    "AddressedState": dev_info.get("AddressedState") or "",
                    "Availability": availability,
                    "Occupancy": occupancy,
                }
            )
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
    processors = db.query(Processor).all()

    if not processors:
        return {
            "status": "error",
            "message": "No processors configured",
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
            rows = _fetch_processor_rows(processor, requested_types)
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

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_HEADERS)
    writer.writeheader()
    writer.writerows(all_rows)

    status = "partial" if processors_not_responding else "success"
    return {
        "status": status,
        "processors_not_responding": processors_not_responding,
        "filename": _build_filename(types),
        "csv": output.getvalue(),
    }
