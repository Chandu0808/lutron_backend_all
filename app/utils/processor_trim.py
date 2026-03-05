"""
Fetch high_end_trim from processor for dimmed load controllers only.
Single connection, sequential ReadRequests (same pattern as other heavy-load processor APIs).
"""
from typing import Dict, List, Optional, Tuple

from app.utils.logger import zone_load_manual_energy_logger
from app.utils.json_connection import (
    create_ssl_connection,
    send_json,
    recv_json,
)


def _extract_loadcontroller_ids(status_body: dict) -> List[int]:
    """Parse Body from /loadcontroller/status; return list of loadcontroller ids."""
    statuses = status_body.get("LoadControllerStatuses") or status_body.get("LoadControllerStatus") or []
    if not isinstance(statuses, list):
        return []
    ids = []
    for item in statuses:
        lc = item.get("LoadController") if isinstance(item, dict) else None
        href = lc.get("href") if isinstance(lc, dict) else None
        if not href or not isinstance(href, str):
            continue
        parts = href.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "loadcontroller":
            try:
                ids.append(int(parts[1]))
            except (ValueError, IndexError):
                continue
    return ids


def _zone_code_from_zone_href(href) -> Optional[int]:
    if not href or not isinstance(href, str):
        return None
    parts = href.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "zone":
        try:
            return int(parts[1])
        except (ValueError, IndexError):
            pass
    return None


def _is_dimmed(loadcontroller_body: dict) -> bool:
    """True if this load controller has tuningsettings (dimmed)."""
    if not isinstance(loadcontroller_body, dict):
        return False
    if "DimmedLoadControllerProperties" in loadcontroller_body:
        return True
    ts = loadcontroller_body.get("TuningSettings")
    if isinstance(ts, dict) and ts.get("href"):
        return True
    return False


def fetch_high_end_trim_from_processor(processor, timeout: int = 5) -> Tuple[Dict[str, float], List[str]]:
    """
    Fetch HighEndTrim from processor for dimmed load controllers only.
    One connection, sequential ReadRequests. Non-dimmed (switched, shade, etc.) are skipped.

    Args:
        processor: Processor model with ipv4, mac, system.
        timeout: Socket timeout in seconds.

    Returns:
        (zone_code_str -> high_end_trim, list of error messages).
    """
    errors: List[str] = []
    result: Dict[str, float] = {}

    processor_id = getattr(processor, "id", None)
    if not processor or not getattr(processor, "ipv4", None) or not getattr(processor, "mac", None) or not getattr(processor, "system", None):
        msg = "Processor missing connection info (ipv4, mac, system)"
        zone_load_manual_energy_logger.warning("[TRIM] %s", msg)
        return {}, [msg]

    sock = create_ssl_connection(
        processor.ipv4,
        processor.mac,
        processor.system,
        processor_ipv4=getattr(processor, "ipv4", None),
        port=8081,
        timeout=timeout,
    )
    if not sock:
        msg = "Failed to connect to processor"
        zone_load_manual_energy_logger.warning("[TRIM] %s | processor_id=%s ipv4=%s", msg, processor_id, getattr(processor, "ipv4", None))
        return {}, [msg]

    zone_load_manual_energy_logger.info("[TRIM] Connected | processor_id=%s ipv4=%s", processor_id, getattr(processor, "ipv4", None))
    try:
        send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": "/loadcontroller/status"}})
        resp = recv_json(sock)
        if not resp or not isinstance(resp, dict):
            msg = "No response from /loadcontroller/status"
            errors.append(msg)
            zone_load_manual_energy_logger.warning("[TRIM] %s | processor_id=%s", msg, processor_id)
            return result, errors

        body = resp.get("Body") or resp.get("body") or {}
        if not isinstance(body, dict):
            body = {}
        lc_ids = _extract_loadcontroller_ids(body)
        zone_load_manual_energy_logger.info(
            "[TRIM] /loadcontroller/status | processor_id=%s lc_ids_count=%s lc_ids_sample=%s",
            processor_id, len(lc_ids), lc_ids[:15] if lc_ids else [],
        )
        if not lc_ids:
            msg = "No load controllers in /loadcontroller/status response"
            errors.append(msg)
            zone_load_manual_energy_logger.warning("[TRIM] %s | processor_id=%s body_keys=%s", msg, processor_id, list(body.keys()) if isinstance(body, dict) else "n/a")
            return result, errors

        for lc_id in lc_ids:
            send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": f"/loadcontroller/{lc_id}"}})
            lc_resp = recv_json(sock)
            if not lc_resp or not isinstance(lc_resp, dict):
                err = f"LoadController {lc_id}: no response"
                errors.append(err)
                zone_load_manual_energy_logger.warning("[TRIM] %s", err)
                continue
            lc_body = (lc_resp.get("Body") or lc_resp.get("body") or {}).get("LoadController")
            if not isinstance(lc_body, dict):
                err = f"LoadController {lc_id}: invalid body"
                errors.append(err)
                zone_load_manual_energy_logger.warning("[TRIM] %s", err)
                continue

            assoc_zone = lc_body.get("AssociatedZone")
            zone_href = assoc_zone.get("href") if isinstance(assoc_zone, dict) else assoc_zone
            zone_code = _zone_code_from_zone_href(zone_href)
            is_dimmed = _is_dimmed(lc_body)
            zone_load_manual_energy_logger.info(
                "[TRIM] LC detail | lc_id=%s zone_code=%s is_dimmed=%s has_DimmedLoadControllerProperties=%s has_TuningSettings_href=%s",
                lc_id, zone_code, is_dimmed,
                "DimmedLoadControllerProperties" in lc_body if isinstance(lc_body, dict) else False,
                bool(isinstance(lc_body.get("TuningSettings"), dict) and lc_body.get("TuningSettings", {}).get("href")) if isinstance(lc_body, dict) else False,
            )
            if zone_code is None:
                zone_load_manual_energy_logger.info("[TRIM] Skip (no zone_code) | lc_id=%s assoc_zone=%s", lc_id, assoc_zone)
                continue

            if not is_dimmed:
                continue

            send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": f"/loadcontroller/{lc_id}/tuningsettings"}})
            ts_resp = recv_json(sock)
            if not ts_resp or not isinstance(ts_resp, dict):
                err = f"LoadController {lc_id} (zone {zone_code}): no tuningsettings response"
                errors.append(err)
                zone_load_manual_energy_logger.warning("[TRIM] %s", err)
                continue
            ts_body = (ts_resp.get("Body") or ts_resp.get("body") or {}).get("TuningSettings")
            if not isinstance(ts_body, dict):
                err = f"LoadController {lc_id} (zone {zone_code}): invalid tuningsettings body"
                errors.append(err)
                zone_load_manual_energy_logger.warning("[TRIM] %s", err)
                continue
            high_end = ts_body.get("HighEndTrim")
            zone_load_manual_energy_logger.info(
                "[TRIM] Tuningsettings | lc_id=%s zone_code=%s HighEndTrim=%s ts_body_keys=%s",
                lc_id, zone_code, high_end, list(ts_body.keys()) if isinstance(ts_body, dict) else "n/a",
            )
            if high_end is not None:
                try:
                    val = float(high_end)
                    result[str(zone_code)] = val
                    zone_load_manual_energy_logger.info("[TRIM] Added trim | zone_code=%s high_end_trim=%s", zone_code, val)
                except (TypeError, ValueError):
                    zone_load_manual_energy_logger.warning("[TRIM] HighEndTrim not float | lc_id=%s zone_code=%s value=%s", lc_id, zone_code, high_end)
    finally:
        try:
            sock.close()
        except Exception:
            pass

    return result, errors
