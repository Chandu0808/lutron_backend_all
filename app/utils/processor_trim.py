"""
Fetch zone tuning trims from processor for dimmed load controllers only.
Single connection, sequential ReadRequests (same pattern as other heavy-load processor APIs).
"""
from typing import Dict, List, Optional, Tuple

from app.utils.logger import listener_logger as zone_load_manual_energy_logger
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


def fetch_zone_trims_from_processor(
    processor, timeout: int = 5
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, int], List[str]]:
    """
    Walk load controllers: map every AssociatedZone to loadcontroller id (href numeric id).
    For dimmed controllers only, also fetch tuningsettings (HighEndTrim, EnergyTrim, LowEndTrim).

    Returns:
        (trim_map zone_code_str -> trims, loadcontroller_map zone_code_str -> lc_id, errors).
        Multiple LCs per zone: last one in walk order wins for both maps.
    """
    errors: List[str] = []
    result: Dict[str, Dict[str, float]] = {}
    loadcontroller_by_zone: Dict[str, int] = {}

    processor_id = getattr(processor, "id", None)
    if not processor or not getattr(processor, "ipv4", None) or not getattr(processor, "mac", None) or not getattr(processor, "system", None):
        msg = "Processor missing connection info (ipv4, mac, system)"
        zone_load_manual_energy_logger.warning("[TRIM] %s", msg)
        return {}, {}, [msg]

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
        return {}, {}, [msg]

    zone_load_manual_energy_logger.debug("[TRIM] Connected | processor_id=%s ipv4=%s", processor_id, getattr(processor, "ipv4", None))
    try:
        send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": "/loadcontroller/status"}})
        resp = recv_json(sock)
        if not resp or not isinstance(resp, dict):
            msg = "No response from /loadcontroller/status"
            errors.append(msg)
            zone_load_manual_energy_logger.warning("[TRIM] %s | processor_id=%s", msg, processor_id)
            return result, loadcontroller_by_zone, errors

        body = resp.get("Body") or resp.get("body") or {}
        if not isinstance(body, dict):
            body = {}
        lc_ids = _extract_loadcontroller_ids(body)
        zone_load_manual_energy_logger.debug(
            "[TRIM] /loadcontroller/status | processor_id=%s lc_ids_count=%s lc_ids_sample=%s",
            processor_id, len(lc_ids), lc_ids[:15] if lc_ids else [],
        )
        if not lc_ids:
            msg = "No load controllers in /loadcontroller/status response"
            errors.append(msg)
            zone_load_manual_energy_logger.warning("[TRIM] %s | processor_id=%s body_keys=%s", msg, processor_id, list(body.keys()) if isinstance(body, dict) else "n/a")
            return result, loadcontroller_by_zone, errors

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
            zone_load_manual_energy_logger.debug(
                "[TRIM] LC detail | lc_id=%s zone_code=%s is_dimmed=%s has_DimmedLoadControllerProperties=%s has_TuningSettings_href=%s",
                lc_id, zone_code, is_dimmed,
                "DimmedLoadControllerProperties" in lc_body if isinstance(lc_body, dict) else False,
                bool(isinstance(lc_body.get("TuningSettings"), dict) and lc_body.get("TuningSettings", {}).get("href")) if isinstance(lc_body, dict) else False,
            )
            if zone_code is None:
                zone_load_manual_energy_logger.debug("[TRIM] Skip (no zone_code) | lc_id=%s assoc_zone=%s", lc_id, assoc_zone)
                continue

            loadcontroller_by_zone[str(zone_code)] = lc_id

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
            energy = ts_body.get("EnergyTrim")
            low_end = ts_body.get("LowEndTrim")
            zone_load_manual_energy_logger.debug(
                "[TRIM] Tuningsettings | lc_id=%s zone_code=%s HighEndTrim=%s EnergyTrim=%s LowEndTrim=%s ts_body_keys=%s",
                lc_id, zone_code, high_end, energy, low_end, list(ts_body.keys()) if isinstance(ts_body, dict) else "n/a",
            )
            trim_values: Dict[str, float] = {}
            for key, source_value in (
                ("high_end_trim", high_end),
                ("energy_trim", energy),
                ("low_end_trim", low_end),
            ):
                try:
                    if source_value is None:
                        continue
                    trim_values[key] = float(source_value)
                except (TypeError, ValueError):
                    zone_load_manual_energy_logger.warning(
                        "[TRIM] %s not float | lc_id=%s zone_code=%s value=%s",
                        key, lc_id, zone_code, source_value
                    )
            if trim_values:
                result[str(zone_code)] = trim_values
                zone_load_manual_energy_logger.debug(
                    "[TRIM] Added trims | zone_code=%s values=%s",
                    zone_code,
                    trim_values,
                )
    finally:
        try:
            sock.close()
        except Exception:
            pass

    return result, loadcontroller_by_zone, errors


def fetch_high_end_trim_from_processor(processor, timeout: int = 5) -> Tuple[Dict[str, float], List[str]]:
    """
    Backward-compatible wrapper around fetch_zone_trims_from_processor.
    Returns only HighEndTrim values.
    """
    trim_map, _lc_map, errors = fetch_zone_trims_from_processor(processor=processor, timeout=timeout)
    high_only: Dict[str, float] = {}
    for zone_code, trims in trim_map.items():
        if not isinstance(trims, dict):
            continue
        if trims.get("high_end_trim") is None:
            continue
        try:
            high_only[str(zone_code)] = float(trims["high_end_trim"])
        except (TypeError, ValueError):
            continue
    return high_only, errors
