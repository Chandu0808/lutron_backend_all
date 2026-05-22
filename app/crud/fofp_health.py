"""
FOFP layout health validation and safe repair helpers (Step 8).

Read/write scope:
- READ: floors, areas, zones, zone_floorplan_positions
- WRITE: zone_floorplan_positions (zone_available flag, coordinates repair only
  when explicitly repairing — never deletes rows, never full regeneration)

All public entry points are fail-closed: they never raise to callers in the
zone-sync hot path. Return structured summaries instead.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.area import Area
from app.models.fofp import ZoneFloorplanPosition
from app.models.zone import Zone


logger = logging.getLogger(__name__)

LOG_PREFIX = "[FOFP]"


def _is_valid_coordinate(x: Any, y: Any) -> bool:
    try:
        xf = float(x)
        yf = float(y)
    except (TypeError, ValueError):
        return False
    return math.isfinite(xf) and math.isfinite(yf)


@dataclass
class FloorLayoutHealthReport:
    """Structured health snapshot for one floor's FOFP layout."""

    floor_id: int
    missing_positions: List[int] = field(default_factory=list)
    orphaned_positions: List[int] = field(default_factory=list)
    disabled_positions: List[int] = field(default_factory=list)
    duplicate_zone_ids: List[int] = field(default_factory=list)
    invalid_coordinate_position_ids: List[int] = field(default_factory=list)
    healthy: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "floor_id": self.floor_id,
            "missing_positions": list(self.missing_positions),
            "orphaned_positions": list(self.orphaned_positions),
            "disabled_positions": list(self.disabled_positions),
            "duplicate_zone_ids": list(self.duplicate_zone_ids),
            "invalid_coordinate_position_ids": list(
                self.invalid_coordinate_position_ids
            ),
            "healthy": bool(self.healthy),
        }


def _active_zone_ids_for_floor(db: Session, floor_id: int) -> Set[int]:
    rows = (
        db.query(Zone.id)
        .join(Area, Zone.area_id == Area.id)
        .filter(Area.floor_id == floor_id)
        .all()
    )
    return {int(r[0]) for r in rows if r[0] is not None}


def _valid_area_ids_for_floor(db: Session, floor_id: int) -> Set[int]:
    rows = db.query(Area.id).filter(Area.floor_id == floor_id).all()
    return {int(r[0]) for r in rows if r[0] is not None}


def detect_missing_positions(db: Session, floor_id: int) -> List[int]:
    """Zone IDs on this floor that have no FOFP placement row."""
    try:
        zone_ids = _active_zone_ids_for_floor(db, floor_id)
        if not zone_ids:
            return []

        existing = (
            db.query(ZoneFloorplanPosition.zone_id)
            .filter(
                ZoneFloorplanPosition.floor_id == floor_id,
                ZoneFloorplanPosition.zone_id.isnot(None),
            )
            .all()
        )
        placed = {int(r[0]) for r in existing if r[0] is not None}
        return sorted(zone_ids - placed)
    except SQLAlchemyError as exc:
        logger.warning("%s missing-position detection failed floor %s: %s", LOG_PREFIX, floor_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return []
    except Exception as exc:
        logger.warning("%s missing-position detection unexpected floor %s: %s", LOG_PREFIX, floor_id, exc)
        return []


def detect_orphaned_positions(db: Session, floor_id: int) -> List[int]:
    """
    Placement row IDs that reference a missing zone, wrong floor/area, or
    have a NULL zone_id after sync removal.
    """
    try:
        valid_zones = _active_zone_ids_for_floor(db, floor_id)
        valid_areas = _valid_area_ids_for_floor(db, floor_id)

        rows = (
            db.query(ZoneFloorplanPosition)
            .filter(ZoneFloorplanPosition.floor_id == floor_id)
            .all()
        )

        orphaned: List[int] = []
        for row in rows:
            is_orphan = False
            if row.zone_id is None:
                is_orphan = True
            elif int(row.zone_id) not in valid_zones:
                is_orphan = True
            if row.area_id is None or int(row.area_id) not in valid_areas:
                is_orphan = True
            if is_orphan:
                orphaned.append(int(row.id))
        return sorted(orphaned)
    except SQLAlchemyError as exc:
        logger.warning("%s orphan detection failed floor %s: %s", LOG_PREFIX, floor_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return []
    except Exception as exc:
        logger.warning("%s orphan detection unexpected floor %s: %s", LOG_PREFIX, floor_id, exc)
        return []


def _detect_duplicate_zone_ids(db: Session, floor_id: int) -> List[int]:
    """Zone IDs that appear on more than one placement row (should not happen)."""
    try:
        rows = (
            db.query(ZoneFloorplanPosition.zone_id)
            .filter(
                ZoneFloorplanPosition.floor_id == floor_id,
                ZoneFloorplanPosition.zone_id.isnot(None),
            )
            .all()
        )
        seen: Dict[int, int] = {}
        dupes: List[int] = []
        for (zid,) in rows:
            if zid is None:
                continue
            zid = int(zid)
            seen[zid] = seen.get(zid, 0) + 1
        for zid, count in seen.items():
            if count > 1:
                dupes.append(zid)
        return sorted(dupes)
    except Exception:
        return []


def _detect_invalid_coordinates(db: Session, floor_id: int) -> List[int]:
    try:
        rows = (
            db.query(ZoneFloorplanPosition)
            .filter(ZoneFloorplanPosition.floor_id == floor_id)
            .all()
        )
        return [
            int(row.id)
            for row in rows
            if not _is_valid_coordinate(row.x, row.y)
        ]
    except Exception:
        return []


def validate_floor_layout(db: Session, floor_id: int) -> FloorLayoutHealthReport:
    """
    Run all layout health checks for a floor.

    Never raises. Returns a :class:`FloorLayoutHealthReport` with ``healthy``
    set to False when any issue is detected.
    """
    report = FloorLayoutHealthReport(floor_id=floor_id)
    try:
        report.missing_positions = detect_missing_positions(db, floor_id)
        report.orphaned_positions = detect_orphaned_positions(db, floor_id)
        report.duplicate_zone_ids = _detect_duplicate_zone_ids(db, floor_id)
        report.invalid_coordinate_position_ids = _detect_invalid_coordinates(
            db, floor_id
        )

        disabled_rows = (
            db.query(ZoneFloorplanPosition.id)
            .filter(
                ZoneFloorplanPosition.floor_id == floor_id,
                ZoneFloorplanPosition.zone_available.is_(False),
            )
            .all()
        )
        report.disabled_positions = [int(r[0]) for r in disabled_rows]

        report.healthy = not any(
            [
                report.missing_positions,
                report.orphaned_positions,
                report.duplicate_zone_ids,
                report.invalid_coordinate_position_ids,
            ]
        )
    except SQLAlchemyError as exc:
        logger.warning("%s validate_floor_layout failed floor %s: %s", LOG_PREFIX, floor_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
        report.healthy = False
    except Exception as exc:
        logger.warning("%s validate_floor_layout unexpected floor %s: %s", LOG_PREFIX, floor_id, exc)
        report.healthy = False

    return report


def disable_invalid_positions(db: Session, floor_id: int, *, commit: bool = True) -> int:
    """
    Mark unhealthy placement rows as unavailable (``zone_available = false``).

    Does not delete rows. Never modifies manual placement coordinates.
    """
    disabled_count = 0
    try:
        report = validate_floor_layout(db, floor_id)
        position_ids_to_disable: Set[int] = set(report.orphaned_positions)
        position_ids_to_disable.update(report.invalid_coordinate_position_ids)

        if not position_ids_to_disable:
            return 0

        rows = (
            db.query(ZoneFloorplanPosition)
            .filter(
                ZoneFloorplanPosition.floor_id == floor_id,
                ZoneFloorplanPosition.id.in_(position_ids_to_disable),
            )
            .all()
        )
        for row in rows:
            if row.zone_available is not False:
                row.zone_available = False
                disabled_count += 1

        if commit and disabled_count:
            db.commit()
            logger.info(
                "%s Disabled %s invalid/orphaned placement(s) for floor %s",
                LOG_PREFIX,
                disabled_count,
                floor_id,
            )
    except SQLAlchemyError as exc:
        logger.warning("%s disable_invalid_positions failed floor %s: %s", LOG_PREFIX, floor_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
    except Exception as exc:
        logger.warning(
            "%s disable_invalid_positions unexpected floor %s: %s",
            LOG_PREFIX,
            floor_id,
            exc,
        )
        try:
            db.rollback()
        except Exception:
            pass

    return disabled_count


def mark_positions_for_zones_pending_removal(
    db: Session, zone_ids: List[int]
) -> int:
    """
    Before zone sync deletes zone rows, mark linked FOFP placements unavailable.

    Caller owns the transaction commit. Does not delete placement rows.
    """
    if not zone_ids:
        return 0

    marked = 0
    try:
        rows = (
            db.query(ZoneFloorplanPosition)
            .filter(ZoneFloorplanPosition.zone_id.in_(zone_ids))
            .all()
        )
        for row in rows:
            changed = False
            if row.zone_available is not False:
                row.zone_available = False
                changed = True
            # Detach from the zone row before sync deletes it so the placement
            # history survives even when SQLite FK pragmas are off in tests.
            if row.zone_id is not None:
                row.zone_id = None
                changed = True
            if changed:
                marked += 1
        if marked:
            db.flush()
            logger.info(
                "%s Marked %s placement(s) unavailable for %s removed zone(s)",
                LOG_PREFIX,
                marked,
                len(zone_ids),
            )
    except SQLAlchemyError as exc:
        logger.warning("%s mark_positions_for_zones_pending_removal failed: %s", LOG_PREFIX, exc)
        try:
            db.rollback()
        except Exception:
            pass
    except Exception as exc:
        logger.warning(
            "%s mark_positions_for_zones_pending_removal unexpected: %s",
            LOG_PREFIX,
            exc,
        )

    return marked


def repair_missing_positions(
    db: Session,
    floor_id: int,
    *,
    min_distance: float = 25.0,
    max_attempts: int = 100,
) -> Dict[str, int]:
    """
    Safe repair entry point: auto-place only zones that lack a position.

  Delegates to :func:`app.crud.fofp_sync.generate_missing_positions_for_floor`.
    """
    from app.crud.fofp_sync import generate_missing_positions_for_floor

    try:
        return generate_missing_positions_for_floor(
            db,
            floor_id,
            min_distance=min_distance,
            max_attempts=max_attempts,
            commit=True,
        )
    except Exception as exc:
        logger.warning("%s repair_missing_positions failed floor %s: %s", LOG_PREFIX, floor_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"generated": 0, "skipped": 0, "failed": 0, "error": str(exc)}


__all__ = [
    "FloorLayoutHealthReport",
    "validate_floor_layout",
    "detect_missing_positions",
    "detect_orphaned_positions",
    "disable_invalid_positions",
    "mark_positions_for_zones_pending_removal",
    "repair_missing_positions",
]
