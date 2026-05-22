"""
Internal FOFP layout service.

Scope and safety contract:
- READ-ONLY access to existing tables: coordinates, areas, zones, floors.
- WRITE access only to: zone_floorplan_positions.
- Does NOT import any FastAPI / router code.
- Does NOT import or call any existing CRUD that mutates legacy tables.
- Does NOT change existing API responses, occupancy logic, or energy logic.
- All randomness funnels through the shared ``random`` module so callers /
  tests can seed deterministically.
"""

from __future__ import annotations

import random
from itertools import groupby
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.area import Area
from app.models.coordinate import Coordinate
from app.crud.fofp_settings import get_fofp_config, normalize_marker_size, normalize_shape
from app.models.fofp import ZoneFloorplanPosition
from app.models.zone import Zone
from app.utils.floorplan_geometry import (
    BBox,
    Point,
    calculate_polygon_bbox,
    fallback_to_bbox_center,
    generate_candidate_point,
    point_in_polygon,
    validate_spacing,
)
from app.utils.logger import logger


# -------------------- Reads --------------------


def get_area_polygons(db: Session, area_id: int) -> List[List[Point]]:
    """
    Load polygon rings for an area from the existing ``coordinates`` table.

    The legacy schema stores polygons as one row per point keyed by
    ``polygon_index``. This helper reuses that exact pattern (see
    :func:`app.crud.floor.area_coordinates_to_rings`) and returns a list of
    rings, each a list of ``(x, y)`` tuples.

    Behavior:
      * Points with ``NULL`` ``x`` or ``y`` are dropped.
      * Rings with fewer than 3 valid points are dropped.
      * Per-polygon point order follows ``coordinates.id``, matching
        legacy rendering behavior so this service stays consistent with
        existing floor rendering.

    Args:
        db: Active SQLAlchemy session.
        area_id: Area whose coordinates to read.

    Returns:
        ``List[List[(x, y)]]``. Empty list when the area has no usable rings.
    """
    coords = db.query(Coordinate).filter(Coordinate.area_id == area_id).all()
    if not coords:
        return []

    sorted_coords = sorted(
        coords,
        key=lambda c: (
            getattr(c, "polygon_index", 0) or 0,
            getattr(c, "id", 0) or 0,
        ),
    )

    polygons: List[List[Point]] = []
    for _idx, group in groupby(
        sorted_coords, key=lambda c: getattr(c, "polygon_index", 0) or 0
    ):
        ring: List[Point] = [
            (float(c.x), float(c.y))
            for c in group
            if c.x is not None and c.y is not None
        ]
        if len(ring) >= 3:
            polygons.append(ring)
    return polygons


def get_existing_zone_positions(
    db: Session, floor_id: int
) -> Dict[int, ZoneFloorplanPosition]:
    """
    Return existing FOFP placements for a floor, keyed by ``zone_id``.

    Used both to preserve manual placements and to seed the spacing-constraint
    set during auto layout.

    Args:
        db: Active SQLAlchemy session.
        floor_id: Floor whose positions to read.

    Returns:
        Dict mapping ``zone_id`` to the matching :class:`ZoneFloorplanPosition`
        ORM object. Empty dict when none exist.
    """
    rows = (
        db.query(ZoneFloorplanPosition)
        .filter(ZoneFloorplanPosition.floor_id == floor_id)
        .all()
    )
    return {row.zone_id: row for row in rows}


# -------------------- Candidate generation --------------------


def _bbox_area(bbox: Optional[BBox]) -> float:
    """Return the rectangular area of a bbox; 0 for invalid/degenerate inputs."""
    if not bbox:
        return 0.0
    width = max(0.0, float(bbox["max_x"]) - float(bbox["min_x"]))
    height = max(0.0, float(bbox["max_y"]) - float(bbox["min_y"]))
    return width * height


def generate_zone_candidate_position(
    polygons: Optional[Sequence[Sequence[Any]]],
    existing_points: Sequence[Any],
    min_distance: float,
    max_attempts: int,
) -> Optional[Point]:
    """
    Pick a candidate ``(x, y)`` inside one of ``polygons``.

    Strategy:
      1. Drop empty / degenerate rings (< 3 points). If none remain, return None.
      2. For up to ``max_attempts`` attempts:
         * Select a polygon (weighted by bbox area so big polygons get more
           shots; falls back to uniform if all bboxes are degenerate).
         * Sample a uniform point inside that polygon's bbox.
         * Accept iff the point is inside the polygon AND meets the
           ``min_distance`` spacing rule vs ``existing_points``.
      3. If random sampling exhausts ``max_attempts``, fall back to each
         polygon's bbox center; accept the first center that is inside the
         polygon and respects spacing.
      4. If everything fails, return None — callers (e.g.
         :func:`generate_layout_for_floor`) treat this as a failure for the
         zone and continue.

    The function never raises for valid input shapes; malformed elements are
    skipped instead. Negative or zero ``min_distance`` disables the spacing
    check (delegated to :func:`validate_spacing`).
    """
    if not polygons:
        return None

    valid_polygons: List[List[Point]] = []
    for poly in polygons:
        if not poly:
            continue
        # Normalize each ring to plain (x, y) tuples once so downstream calls
        # don't re-coerce on every iteration.
        ring = [(float(p[0]), float(p[1])) if isinstance(p, (tuple, list))
                else (float(p["x"]), float(p["y"])) if isinstance(p, dict)
                else (float(p.x), float(p.y))
                for p in poly]
        if len(ring) >= 3:
            valid_polygons.append(ring)

    if not valid_polygons:
        return None

    bboxes: List[Optional[BBox]] = [calculate_polygon_bbox(p) for p in valid_polygons]
    weights = [_bbox_area(b) for b in bboxes]
    weight_sum = sum(weights)

    attempts = max(0, int(max_attempts or 0))
    for _ in range(attempts):
        if weight_sum > 0:
            idx = random.choices(range(len(valid_polygons)), weights=weights, k=1)[0]
        else:
            idx = random.randrange(len(valid_polygons))

        bbox = bboxes[idx]
        if not bbox:
            continue
        try:
            candidate = generate_candidate_point(bbox)
        except ValueError:
            continue
        if not point_in_polygon(candidate, valid_polygons[idx]):
            continue
        if not validate_spacing(candidate, existing_points, min_distance):
            continue
        return candidate

    # Fallback: try each polygon's geometric center.
    for poly, bbox in zip(valid_polygons, bboxes):
        if not bbox:
            continue
        try:
            center = fallback_to_bbox_center(bbox)
        except ValueError:
            continue
        if not point_in_polygon(center, poly):
            continue
        if not validate_spacing(center, existing_points, min_distance):
            continue
        return center

    return None


# -------------------- Writes (zone_floorplan_positions only) --------------------


def create_zone_position(
    db: Session,
    floor_id: int,
    area_id: int,
    zone_id: int,
    x: float,
    y: float,
    placement_source: str = "auto",
    marker_shape: Optional[str] = None,
    shape_size: Optional[int] = None,
) -> ZoneFloorplanPosition:
    """
    Insert one ``zone_floorplan_positions`` row.

    Each insert is its own transaction: success commits and refreshes,
    failure rolls back and re-raises so the caller can count and continue.

    No other tables are touched.
    """
    try:
        defaults = get_fofp_config(db)
        resolved_shape = (
            normalize_shape(marker_shape)
            if marker_shape is not None
            else normalize_shape(defaults.get("shape"))
        )
        resolved_size = (
            normalize_marker_size(shape_size)
            if shape_size is not None
            else normalize_marker_size(defaults.get("marker_size"))
        )
        position = ZoneFloorplanPosition(
            floor_id=floor_id,
            area_id=area_id,
            zone_id=zone_id,
            x=float(x),
            y=float(y),
            marker_shape=resolved_shape,
            shape_size=resolved_size,
            placement_source=placement_source,
        )
        db.add(position)
        db.commit()
        db.refresh(position)
        return position
    except Exception:
        db.rollback()
        raise


# -------------------- Orchestration --------------------


def generate_layout_for_floor(
    db: Session,
    floor_id: int,
    min_distance: float = 25.0,
    max_attempts: int = 100,
) -> Dict[str, int]:
    """
    Auto-generate missing zone placements for a floor.

    Behavior:
      * Reads all areas on the floor (``Area.floor_id == floor_id``).
      * Reads existing positions on the floor; preserves them as-is regardless
        of their ``placement_source`` (manual or auto).
      * For each zone in each area, skips zones that already have a position
        and otherwise attempts to generate a candidate position inside one of
        the area's polygons that respects the floor-wide spacing constraint.
      * Each successful candidate is inserted via :func:`create_zone_position`
        in its own transaction, and added to the running spacing set so later
        zones avoid clustering.
      * Partial failures are isolated: a candidate that cannot be generated
        or an insert that fails increments ``failed`` and the loop continues.

    Args:
        db: Active SQLAlchemy session.
        floor_id: Floor to populate.
        min_distance: Minimum allowed distance between any two placements.
        max_attempts: Max random samples per zone before the bbox-center
            fallback runs.

    Returns:
        ``{"generated": int, "skipped": int, "failed": int}``.
    """
    summary: Dict[str, int] = {"generated": 0, "skipped": 0, "failed": 0}

    areas = db.query(Area).filter(Area.floor_id == floor_id).all()
    if not areas:
        logger.info(f"[FOFP] floor {floor_id} has no areas; nothing to generate")
        return summary

    existing_positions = get_existing_zone_positions(db, floor_id)
    existing_points: List[Point] = [
        (float(p.x), float(p.y)) for p in existing_positions.values()
    ]

    for area in areas:
        polygons = get_area_polygons(db, area.id)
        zones = db.query(Zone).filter(Zone.area_id == area.id).all()

        for zone in zones:
            if zone.id in existing_positions:
                summary["skipped"] += 1
                continue

            if not polygons:
                logger.warning(
                    f"[FOFP] floor {floor_id} area {area.id}: no usable polygons; "
                    f"cannot place zone {zone.id}"
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
                    f"[FOFP] floor {floor_id} area {area.id} zone {zone.id}: "
                    f"no valid placement after {max_attempts} attempts + fallback"
                )
                summary["failed"] += 1
                continue

            try:
                created = create_zone_position(
                    db=db,
                    floor_id=floor_id,
                    area_id=area.id,
                    zone_id=zone.id,
                    x=candidate[0],
                    y=candidate[1],
                    placement_source="auto",
                )
            except Exception as exc:
                logger.exception(
                    f"[FOFP] floor {floor_id} zone {zone.id}: insert failed: {exc}"
                )
                summary["failed"] += 1
                continue

            existing_positions[zone.id] = created
            existing_points.append((float(created.x), float(created.y)))
            summary["generated"] += 1

    return summary


__all__ = [
    "get_area_polygons",
    "get_existing_zone_positions",
    "generate_zone_candidate_position",
    "create_zone_position",
    "generate_layout_for_floor",
]
