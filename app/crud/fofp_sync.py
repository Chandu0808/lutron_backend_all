"""
FOFP incremental sync and post-sync maintenance (Step 8).

Hooks into the existing zone sync flow to:
- auto-place ONLY missing zones (never full regeneration)
- mark removed zones unavailable (rows preserved via ON DELETE SET NULL)
- run self-healing validation/repair when configured

Rollout is controlled by:
- ``fofp_settings.enabled`` (master feature flag)
- ``FOFP_SYNC_MODE`` environment variable:
    * ``off``       — no operational FOFP work during sync
    * ``log``       — health checks + structured logging only (Stage 1)
    * ``auto_place``— log + incremental auto-placement (Stage 2)
    * ``heal``      — log + auto-place + disable invalid rows (Stage 3)
    * ``full``      — same as ``heal`` (default when FOFP is enabled)

All hooks are fail-closed: exceptions are logged and swallowed so zone sync,
occupancy, energy, and floor APIs continue unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.crud.fofp_health import (
    disable_invalid_positions,
    validate_floor_layout,
)
from app.crud.fofp_layout import (
    generate_zone_candidate_position,
    get_area_polygons,
    get_existing_zone_positions,
)
from app.crud.fofp_settings import get_fofp_settings
from app.models.area import Area
from app.models.fofp import ZoneFloorplanPosition
from app.models.zone import Zone
from app.utils.floorplan_geometry import Point


logger = logging.getLogger(__name__)

LOG_PREFIX = "[FOFP]"

_VALID_SYNC_MODES = frozenset({"off", "log", "auto_place", "heal", "full"})


def get_fofp_sync_mode() -> str:
    """Resolve operational sync mode from environment (default ``log``)."""
    raw = (os.getenv("FOFP_SYNC_MODE") or "log").strip().lower()
    if raw in _VALID_SYNC_MODES:
        return raw
    logger.warning("%s Unknown FOFP_SYNC_MODE=%r; using log", LOG_PREFIX, raw)
    return "log"


def generate_missing_positions_for_floor(
    db: Session,
    floor_id: int,
    *,
    min_distance: float = 25.0,
    max_attempts: int = 100,
    commit: bool = True,
) -> Dict[str, int]:
    """
    Incrementally generate placements for zones that lack a position.

    Safety contract:
    - Skips every zone that already has a row (manual or auto preserved).
    - Never updates existing coordinates or placement_source.
    - Never deletes rows.
    - Uses a single transaction when ``commit=True`` so partial failure
      rolls back all new rows for this call.
  """
    summary: Dict[str, int] = {"generated": 0, "skipped": 0, "failed": 0}

    try:
        areas = db.query(Area).filter(Area.floor_id == floor_id).all()
        if not areas:
            logger.info("%s floor %s has no areas; nothing to place", LOG_PREFIX, floor_id)
            return summary

        existing_positions = get_existing_zone_positions(db, floor_id)
        existing_points: List[Point] = [
            (float(p.x), float(p.y))
            for p in existing_positions.values()
            if p.x is not None and p.y is not None
        ]

        pending: List[ZoneFloorplanPosition] = []

        for area in areas:
            polygons = get_area_polygons(db, area.id)
            zones = db.query(Zone).filter(Zone.area_id == area.id).all()

            for zone in zones:
                if zone.id in existing_positions:
                    summary["skipped"] += 1
                    continue

                if not polygons:
                    logger.warning(
                        "%s floor %s area %s: no polygons; cannot place zone %s",
                        LOG_PREFIX,
                        floor_id,
                        area.id,
                        zone.id,
                    )
                    summary["failed"] += 1
                    continue

                candidate = generate_zone_candidate_position(
                    polygons=polygons,
                    existing_points=existing_points,
                    min_distance=min_distance,
                    max_attempts=max_attempts,
                )
                if candidate is None:
                    logger.warning(
                        "%s floor %s area %s zone %s: no valid placement",
                        LOG_PREFIX,
                        floor_id,
                        area.id,
                        zone.id,
                    )
                    summary["failed"] += 1
                    continue

                row = ZoneFloorplanPosition(
                    floor_id=floor_id,
                    area_id=area.id,
                    zone_id=zone.id,
                    x=float(candidate[0]),
                    y=float(candidate[1]),
                    placement_source="auto",
                    zone_available=True,
                )
                pending.append(row)
                existing_points.append((float(candidate[0]), float(candidate[1])))
                existing_positions[zone.id] = row

        if pending:
            for row in pending:
                db.add(row)
            if commit:
                db.commit()
            summary["generated"] = len(pending)
            logger.info(
                "%s Auto-placed %s missing zone(s) for floor %s",
                LOG_PREFIX,
                len(pending),
                floor_id,
            )
        else:
            if commit:
                # Ensure session is clean even when nothing was added.
                pass

    except SQLAlchemyError as exc:
        logger.exception(
            "%s generate_missing_positions_for_floor failed floor %s: %s",
            LOG_PREFIX,
            floor_id,
            exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        summary["failed"] = summary.get("failed", 0) + 1
    except Exception as exc:
        logger.exception(
            "%s generate_missing_positions_for_floor unexpected floor %s: %s",
            LOG_PREFIX,
            floor_id,
            exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        summary["failed"] = summary.get("failed", 0) + 1

    return summary


def run_fofp_post_sync_maintenance(
    db: Session,
    floor_id: int,
    *,
    sync_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run FOFP operational maintenance after zone sync for one floor.

    Never raises. Returns a summary dict suitable for logging/diagnostics.
    """
    result: Dict[str, Any] = {
        "floor_id": floor_id,
        "fofp_enabled": False,
        "mode": "off",
        "skipped": True,
    }

    try:
        cfg = get_fofp_settings(db)
        if not cfg.enabled:
            return result

        mode = sync_mode if sync_mode is not None else get_fofp_sync_mode()
        result["fofp_enabled"] = True
        result["mode"] = mode
        result["skipped"] = False

        if mode == "off":
            result["skipped"] = True
            return result

        health = validate_floor_layout(db, floor_id)
        result["health"] = health.as_dict()

        if health.missing_positions:
            logger.info(
                "%s floor %s: %s zone(s) missing FOFP positions",
                LOG_PREFIX,
                floor_id,
                len(health.missing_positions),
            )
        if health.orphaned_positions:
            logger.info(
                "%s floor %s: %s orphaned placement row(s)",
                LOG_PREFIX,
                floor_id,
                len(health.orphaned_positions),
            )

        if mode == "log":
            return result

        if mode in ("auto_place", "heal", "full"):
            place_summary = generate_missing_positions_for_floor(
                db, floor_id, commit=True
            )
            result["auto_place"] = place_summary

        if mode in ("heal", "full"):
            disabled = disable_invalid_positions(db, floor_id, commit=True)
            result["disabled_invalid"] = disabled
            # Re-validate after repairs for diagnostics.
            result["health_after"] = validate_floor_layout(db, floor_id).as_dict()

        return result

    except Exception as exc:
        logger.exception(
            "%s post-sync maintenance failed floor %s: %s",
            LOG_PREFIX,
            floor_id,
            exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        result["error"] = str(exc)
        return result


__all__ = [
    "get_fofp_sync_mode",
    "generate_missing_positions_for_floor",
    "run_fofp_post_sync_maintenance",
]
