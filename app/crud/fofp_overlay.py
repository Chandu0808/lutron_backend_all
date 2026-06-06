"""
Defensive helpers for serving FOFP marker positions on the read-only overlay.

Strict isolation goals (Step 6):
- Read-only: no writes, no DDL.
- Independent of ``app/crud/fofp_layout.py`` (which is the admin-write side).
- Independent of occupancy/energy/zone state.
- Never raises: any failure (missing table, missing rows, malformed config,
  database error) MUST collapse silently to an empty position list so that
  ``/floor/light_status`` keeps responding with its existing shape.

The single public entry point is :func:`get_overlay_positions_for_floor`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.drivers import Driver
from app.models.events import CurrentZoneEvent
from app.models.fofp import ZoneFloorplanPosition
from app.models.zone import Zone


logger = logging.getLogger(__name__)


_LIGHT_ON_VALUES = {"on"}
_LIGHT_OFF_VALUES = {"off"}


def _serialize_position(row: ZoneFloorplanPosition) -> Optional[Dict[str, Any]]:
    """Convert one ORM row to the wire format. Returns ``None`` if malformed."""
    try:
        zone_id = int(row.zone_id) if row.zone_id is not None else None
        area_id = int(row.area_id) if row.area_id is not None else None
        x = float(row.x) if row.x is not None else None
        y = float(row.y) if row.y is not None else None
    except (TypeError, ValueError):
        return None

    if zone_id is None or area_id is None or x is None or y is None:
        return None

    try:
        from app.crud.fofp_settings import resolve_marker_half_axes

        half_x, half_y, shape_size = resolve_marker_half_axes(
            row.shape_size,
            getattr(row, "shape_size_x", None),
            getattr(row, "shape_size_y", None),
        )
    except (TypeError, ValueError):
        half_x, half_y, shape_size = 5, 5, 5

    raw_source = row.placement_source if isinstance(row.placement_source, str) else "auto"
    placement_source = raw_source if raw_source in ("auto", "manual") else "auto"

    marker_shape = None
    raw_shape = getattr(row, "marker_shape", None)
    if isinstance(raw_shape, str) and raw_shape.strip():
        marker_shape = raw_shape.strip().lower()

    payload = {
        "zone_id": zone_id,
        "area_id": area_id,
        "x": x,
        "y": y,
        "shape_size": shape_size,
        "shape_size_x": half_x,
        "shape_size_y": half_y,
        "placement_source": placement_source,
    }
    if marker_shape:
        payload["marker_shape"] = marker_shape
    return payload


def lookup_zone_names(
    db: Optional[Session], zone_ids: Iterable[int]
) -> Dict[int, str]:
    """Bulk-load ``zones.name`` keyed by ``zones.id``. Never raises."""
    if db is None:
        return {}

    safe_ids: List[int] = []
    for zid in zone_ids or []:
        try:
            safe_ids.append(int(zid))
        except (TypeError, ValueError):
            continue
    if not safe_ids:
        return {}

    try:
        rows = db.query(Zone.id, Zone.name).filter(Zone.id.in_(safe_ids)).all()
        return {
            int(zid): (name if isinstance(name, str) and name.strip() else f"Zone {zid}")
            for zid, name in rows or []
            if zid is not None
        }
    except SQLAlchemyError as exc:
        logger.warning("FOFP zone name lookup failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
    except Exception as exc:
        logger.warning("FOFP zone name lookup unexpected: %s", exc)
    return {}


def attach_zone_names_to_positions(
    db: Optional[Session], positions: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Add ``zone_name`` to each position dict (additive wire field)."""
    if not positions:
        return []

    zone_ids = [
        p.get("zone_id")
        for p in positions
        if isinstance(p, Mapping) and p.get("zone_id") is not None
    ]
    names = lookup_zone_names(db, zone_ids)
    enriched: List[Dict[str, Any]] = []
    for pos in positions:
        if not isinstance(pos, Mapping):
            continue
        try:
            zid = int(pos.get("zone_id")) if pos.get("zone_id") is not None else None
        except (TypeError, ValueError):
            zid = None
        zone_name = names.get(zid) if zid is not None else None
        enriched.append({**pos, "zone_name": zone_name})
    return enriched


def get_overlay_positions_for_floor(
    db: Optional[Session], floor_id: Optional[int]
) -> List[Dict[str, Any]]:
    """Return FOFP marker positions for a floor.

    This function NEVER raises. On any error (no db, invalid floor id, missing
    table, malformed rows) it returns ``[]`` so the augmented ``light_status``
    response always degrades gracefully to "FOFP visually disabled".

    Uses a single indexed query on ``ix_zone_floorplan_positions_floor_id`` —
    no joins, no N+1.
    """

    if db is None:
        return []

    try:
        floor_id_int = int(floor_id) if floor_id is not None else None
    except (TypeError, ValueError):
        return []

    if floor_id_int is None or floor_id_int <= 0:
        return []

    try:
        rows = (
            db.query(ZoneFloorplanPosition)
            .filter(
                ZoneFloorplanPosition.floor_id == floor_id_int,
                ZoneFloorplanPosition.zone_available.is_(True),
                ZoneFloorplanPosition.zone_id.isnot(None),
            )
            .all()
        )
    except SQLAlchemyError as exc:
        logger.warning(
            "FOFP overlay positions lookup failed for floor %s; returning empty: %s",
            floor_id_int,
            exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return []
    except Exception as exc:
        logger.warning(
            "FOFP overlay positions unexpected error for floor %s; returning empty: %s",
            floor_id_int,
            exc,
        )
        return []

    positions: List[Dict[str, Any]] = []
    for row in rows or []:
        serialized = _serialize_position(row)
        if serialized is not None:
            positions.append(serialized)

    return attach_zone_names_to_positions(db, positions)


# -------------------- live status (Step 7) --------------------


def _normalize_light_status(raw: Any) -> Optional[bool]:
    """Translate the legacy "on"/"off"/None string into a tri-state boolean."""
    if isinstance(raw, bool):
        return raw
    if not isinstance(raw, str):
        return None
    val = raw.strip().lower()
    if val in _LIGHT_ON_VALUES:
        return True
    if val in _LIGHT_OFF_VALUES:
        return False
    return None


def _normalize_light_level(raw: Any) -> int:
    """Clamp Lutron dimming/intensity level into the 0-100 FOFP wire value."""
    try:
        return max(0, min(100, int(round(float(raw)))))
    except (TypeError, ValueError):
        return 0


def _empty_status() -> Dict[str, Any]:
    return {
        "light_level": 0,
        "light_status": None,
    }


def _status_from_area_fallback(
    area_id: Optional[int],
    light_level_by_area: Optional[Mapping[int, Any]],
    light_status_by_area: Optional[Mapping[int, Any]],
) -> Dict[str, Any]:
    """Area-level fallback when zone-wise data is unavailable."""
    if area_id is None:
        return _empty_status()
    level_raw = None
    status_raw = None
    if isinstance(light_level_by_area, Mapping):
        level_raw = light_level_by_area.get(area_id)
        if level_raw is None:
            try:
                level_raw = light_level_by_area.get(int(area_id))
            except (TypeError, ValueError):
                pass
    if isinstance(light_status_by_area, Mapping):
        status_raw = light_status_by_area.get(area_id)
        if status_raw is None:
            try:
                status_raw = light_status_by_area.get(int(area_id))
            except (TypeError, ValueError):
                pass
    return {
        "light_level": _normalize_light_level(level_raw),
        "light_status": _normalize_light_status(status_raw),
    }


def _load_zone_status_from_cache(
    db: Optional[Session], zone_ids: Iterable[int]
) -> Dict[int, Dict[str, Any]]:
    """Bulk read ``current_zone_status`` keyed by ``zones.id``. Never raises."""
    if db is None:
        return {}

    safe_ids: List[int] = []
    for zid in zone_ids or []:
        try:
            safe_ids.append(int(zid))
        except (TypeError, ValueError):
            continue
    if not safe_ids:
        return {}

    out: Dict[int, Dict[str, Any]] = {}
    try:
        rows = (
            db.query(CurrentZoneEvent.zone_id, CurrentZoneEvent.level)
            .filter(CurrentZoneEvent.zone_id.in_(safe_ids))
            .all()
        )
        for zone_id, level in rows or []:
            if zone_id is None:
                continue
            zid_int = int(zone_id)
            lvl = _normalize_light_level(level)
            out[zid_int] = {
                "light_level": lvl,
                "light_status": False if lvl == 0 else True if lvl > 0 else None,
            }
    except SQLAlchemyError as exc:
        logger.warning("%s cache zone status lookup failed: %s", "FOFP", exc)
        try:
            db.rollback()
        except Exception:
            pass
    except Exception as exc:
        logger.warning("%s cache zone status unexpected: %s", "FOFP", exc)
    return out


def get_zone_live_status_for_fofp(
    db: Optional[Session],
    positions: List[Dict[str, Any]],
    *,
    light_status_by_area: Optional[Mapping[int, Any]] = None,
    light_level_by_area: Optional[Mapping[int, Any]] = None,
) -> Dict[int, Dict[str, Any]]:
    """
    Build per-zone status for FOFP markers keyed by ``zones.id``.

    Reads **only** from ``current_zone_status`` (maintained by the listener).
    Does not call the processor. Zones with no row get ``light_level`` 0 and
    ``light_status`` None (off/grey on the overlay).

    ``light_status_by_area`` / ``light_level_by_area`` are accepted for call-site
    compatibility but ignored.
    """
    del light_status_by_area, light_level_by_area

    zone_ids: Set[int] = set()
    for pos in positions or []:
        if not isinstance(pos, Mapping):
            continue
        try:
            zid = int(pos.get("zone_id")) if pos.get("zone_id") is not None else None
        except (TypeError, ValueError):
            zid = None
        if zid is not None:
            zone_ids.add(zid)

    if not zone_ids:
        return {}

    out: Dict[int, Dict[str, Any]] = _load_zone_status_from_cache(db, zone_ids)

    for zid in zone_ids:
        if zid not in out:
            out[zid] = _empty_status()

    return out


def get_overlay_live_status_for_floor(
    db: Optional[Session],
    floor_id: Optional[int],
    area_ids: Iterable[int],
    light_status_by_area: Optional[Mapping[int, Any]] = None,
    light_level_by_area: Optional[Mapping[int, Any]] = None,
) -> Dict[int, Dict[str, Any]]:
    """
    Legacy per-area status map (kept for tests and backward compatibility).

    FOFP production path uses :func:`get_zone_live_status_for_fofp` instead.
    """
    del db, floor_id

    out: Dict[int, Dict[str, Any]] = {}
    safe_area_ids: List[int] = []
    for aid in area_ids or []:
        try:
            safe_area_ids.append(int(aid))
        except (TypeError, ValueError):
            continue

    for aid in safe_area_ids:
        out[aid] = _status_from_area_fallback(
            aid, light_level_by_area, light_status_by_area
        )
    return out


_DRIVER_ALERT_STATUSES = ("not_ok", "not_okay")
FOFP_DRIVER_ALERT_COLOR = "red"
FOFP_DRIVER_ERROR_TO_ALERT_TYPE = {
    "E2": "Ballast Failure",
    "FC": "Lamp Failure",
}


def get_active_driver_alerts_by_zone(
    db: Optional[Session], zone_ids: Iterable[int]
) -> Dict[int, str]:
    """
    Active FOFP driver alerts keyed by ``zones.id``.

    Only Ballast Failure (E2) and Lamp Failure (FC). Matches active-alerts rules:
    ``alert_status`` not ok, ``display`` true. Never raises.
    """
    if db is None:
        return {}

    safe_ids: List[int] = []
    for zid in zone_ids or []:
        try:
            safe_ids.append(int(zid))
        except (TypeError, ValueError):
            continue
    if not safe_ids:
        return {}

    try:
        rows = (
            db.query(Driver.zone_id, Driver.error_code)
            .filter(
                Driver.zone_id.in_(safe_ids),
                Driver.alert_status.in_(_DRIVER_ALERT_STATUSES),
                Driver.display.is_(True),
                Driver.error_code.in_(tuple(FOFP_DRIVER_ERROR_TO_ALERT_TYPE.keys())),
            )
            .all()
        )
        out: Dict[int, str] = {}
        for row in rows or []:
            if not row or row[0] is None:
                continue
            code = row[1]
            alert_type = FOFP_DRIVER_ERROR_TO_ALERT_TYPE.get(code)
            if not alert_type:
                continue
            zid = int(row[0])
            if zid not in out:
                out[zid] = alert_type
        return out
    except SQLAlchemyError as exc:
        logger.warning("FOFP driver alert zone lookup failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
    except Exception as exc:
        logger.warning("FOFP driver alert zone lookup unexpected: %s", exc)
    return {}


def get_active_driver_alert_zone_ids(
    db: Optional[Session], zone_ids: Iterable[int]
) -> Set[int]:
    """Backward-compatible zone-id set for tests and callers that only need IDs."""
    return set(get_active_driver_alerts_by_zone(db, zone_ids).keys())


def attach_driver_alerts_to_positions(
    positions: List[Dict[str, Any]],
    alerts_by_zone: Optional[Mapping[int, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Add ``driver_alert`` and ``driver_alert_type`` per FOFP marker. When alerting,
    clear dimming fields and set ``alert_color`` for solid red styling.
    """
    alert_map = dict(alerts_by_zone) if alerts_by_zone is not None else {}
    enriched: List[Dict[str, Any]] = []
    for pos in positions or []:
        try:
            zone_id = int(pos.get("zone_id")) if pos and pos.get("zone_id") is not None else None
        except (TypeError, ValueError):
            zone_id = None
        alert_type = alert_map.get(zone_id) if zone_id is not None else None
        is_alert = alert_type is not None
        item = {**pos, "driver_alert": is_alert}
        if is_alert:
            item["driver_alert_type"] = alert_type
            item["alert_color"] = FOFP_DRIVER_ALERT_COLOR
            item["light_level"] = None
            item["light_status"] = None
        else:
            item.pop("driver_alert_type", None)
            item.pop("alert_color", None)
        enriched.append(item)
    return enriched


def attach_live_status_to_positions(
    positions: List[Dict[str, Any]],
    status_by_zone: Mapping[int, Mapping[str, Any]],
    *,
    status_by_area: Optional[Mapping[int, Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Merge live light fields into each position (zone-wise, area fallback)."""
    enriched: List[Dict[str, Any]] = []
    for pos in positions or []:
        try:
            zone_id = int(pos.get("zone_id")) if pos and pos.get("zone_id") is not None else None
        except (TypeError, ValueError):
            zone_id = None
        try:
            area_id = int(pos.get("area_id")) if pos and pos.get("area_id") is not None else None
        except (TypeError, ValueError):
            area_id = None

        status = None
        if zone_id is not None and isinstance(status_by_zone, Mapping):
            status = status_by_zone.get(zone_id)
        if (not isinstance(status, Mapping) or not status) and status_by_area and area_id is not None:
            status = status_by_area.get(area_id)
        if not isinstance(status, Mapping):
            status = _empty_status()

        enriched.append(
            {
                **pos,
                "light_level": _normalize_light_level(status.get("light_level")),
                "light_status": status.get("light_status"),
            }
        )
    return enriched
