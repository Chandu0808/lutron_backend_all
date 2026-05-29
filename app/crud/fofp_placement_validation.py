"""
FOFP placement validation helpers (layout save).

Pure resolution + containment checks shared by the layout PUT route and tests.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence, Tuple

from app.crud.fofp_settings import (
    get_fofp_config,
    normalize_shape,
    normalize_marker_size,
    resolve_marker_half_axes,
)
from app.crud.floor import area_coordinates_to_rings
from app.models.area import Area
from app.models.fofp import ZoneFloorplanPosition
from app.utils.fofp_marker_geometry import marker_fits_area_rings
from app.utils.floorplan_geometry import point_in_polygon


def area_rings_from_model(area: Area) -> list:
    """Load polygon rings from an Area ORM row (same source as layout save)."""
    try:
        return area_coordinates_to_rings(area.coordinates) or []
    except Exception:
        return []


def point_inside_area_rings(rings: Sequence[Sequence[Any]], x: float, y: float) -> bool:
    if not rings:
        return False
    for ring in rings:
        if len(ring) < 3:
            continue
        try:
            if point_in_polygon((x, y), ring):
                return True
        except Exception:
            continue
    return False


def resolve_saved_marker_state(
    *,
    entry_marker_shape: Optional[str],
    entry_shape_size: Optional[int],
    entry_shape_size_x: Optional[int],
    entry_shape_size_y: Optional[int],
    existing_row: Optional[ZoneFloorplanPosition],
    layout_defaults: dict,
) -> Tuple[str, int, int, int]:
    """
    Resolve (shape, half_x, half_y, legacy_shape_size) for validation/persist.

    Uses payload fields when present, otherwise existing DB row, otherwise defaults.
    """
    if entry_marker_shape is not None:
        shape = normalize_shape(entry_marker_shape)
    elif existing_row is not None and getattr(existing_row, "marker_shape", None):
        shape = normalize_shape(existing_row.marker_shape)
    else:
        shape = normalize_shape(layout_defaults.get("shape"))

    default_size = normalize_marker_size(layout_defaults.get("marker_size"))

    if (
        entry_shape_size is not None
        or entry_shape_size_x is not None
        or entry_shape_size_y is not None
    ):
        base = entry_shape_size if entry_shape_size is not None else default_size
        half_x, half_y, legacy = resolve_marker_half_axes(
            base, entry_shape_size_x, entry_shape_size_y
        )
        return shape, half_x, half_y, legacy

    if existing_row is not None:
        half_x, half_y, legacy = resolve_marker_half_axes(
            existing_row.shape_size,
            getattr(existing_row, "shape_size_x", None),
            getattr(existing_row, "shape_size_y", None),
        )
        return shape, half_x, half_y, legacy

    half_x, half_y, legacy = resolve_marker_half_axes(default_size, None, None)
    return shape, half_x, half_y, legacy


def validate_marker_geometry_for_area(
    area: Area,
    x: float,
    y: float,
    shape: str,
    half_x: int,
    half_y: int,
) -> Optional[str]:
    """
    Return an error message when placement is invalid, else None.
    """
    rings = area_rings_from_model(area)
    if not rings:
        return f"Area {area.id} has no floor geometry"

    if not point_inside_area_rings(rings, x, y):
        return "marker center must remain inside the area"

    if not marker_fits_area_rings(rings, x, y, shape, half_x, half_y):
        return "marker must fit inside the area"

    return None
