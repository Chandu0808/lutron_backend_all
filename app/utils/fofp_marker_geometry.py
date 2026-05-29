"""
FOFP marker outline containment (mirrors frontend markerContainment.js).

Pure geometry: no DB, no FastAPI imports.
"""

from __future__ import annotations

import math
from typing import Any, List, Sequence, Tuple

from app.utils.floorplan_geometry import Point, point_in_polygon


MIN_MARKER_HALF_SIZE = 4
ELLIPSE_SAMPLES = 32
GLOWING_DOT_HALO_SCALE = 1.5

VALID_SHAPES = frozenset(
    {"circle", "glowing_dot", "square", "triangle", "hexagon", "bulb"}
)


def _normalize_shape(shape: Any) -> str:
    if not isinstance(shape, str):
        return "circle"
    key = shape.strip().lower()
    return key if key in VALID_SHAPES else "circle"


def _half(raw: Any, fallback: float) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(fallback)
    if not math.isfinite(v):
        v = float(fallback)
    return max(MIN_MARKER_HALF_SIZE, v)


def _sample_ellipse(cx: float, cy: float, rx: float, ry: float, n: int = ELLIPSE_SAMPLES) -> List[Point]:
    return [
        (cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def marker_outline_points(
    shape: Any,
    cx: float,
    cy: float,
    half_x: Any,
    half_y: Any = None,
) -> List[Point]:
    """Return boundary sample points for the visible marker geometry."""
    rx = _half(half_x, MIN_MARKER_HALF_SIZE)
    ry = _half(half_y if half_y is not None else half_x, rx)
    resolved = _normalize_shape(shape)

    if resolved == "square":
        return [
            (cx - rx, cy - ry),
            (cx + rx, cy - ry),
            (cx + rx, cy + ry),
            (cx - rx, cy + ry),
        ]
    if resolved == "triangle":
        return [(cx, cy - ry), (cx - rx, cy + ry), (cx + rx, cy + ry)]
    if resolved == "hexagon":
        return [
            (
                cx + rx * math.cos((math.pi / 3) * i - math.pi / 2),
                cy + ry * math.sin((math.pi / 3) * i - math.pi / 2),
            )
            for i in range(6)
        ]
    if resolved == "bulb":
        stem_w = rx * 0.55
        stem_h = ry * 0.85
        bulb_cx = cx
        bulb_cy = cy - ry * 0.35
        bulb_rx = rx * 0.95
        bulb_ry = ry * 0.95
        stem_left = cx - stem_w / 2
        stem_top = cy + ry * 0.15
        stem_right = cx + stem_w / 2
        stem_bottom = stem_top + stem_h
        pts = _sample_ellipse(bulb_cx, bulb_cy, bulb_rx, bulb_ry, 24)
        pts.extend(
            [
                (stem_left, stem_top),
                (stem_right, stem_top),
                (stem_right, stem_bottom),
                (stem_left, stem_bottom),
            ]
        )
        return pts
    if resolved == "glowing_dot":
        return _sample_ellipse(
            cx,
            cy,
            rx * GLOWING_DOT_HALO_SCALE,
            ry * GLOWING_DOT_HALO_SCALE,
            ELLIPSE_SAMPLES,
        )
    return _sample_ellipse(cx, cy, rx, ry, ELLIPSE_SAMPLES)


def _point_in_any_ring(point: Point, rings: Sequence[Sequence[Any]]) -> bool:
    for ring in rings or []:
        if len(ring) < 3:
            continue
        if point_in_polygon(point, ring):
            return True
    return False


def has_valid_area_rings(rings: Sequence[Sequence[Any]] | None) -> bool:
    if not rings:
        return False
    return any(isinstance(ring, Sequence) and len(ring) >= 3 for ring in rings)


def marker_fits_area_rings(
    rings: Sequence[Sequence[Any]],
    cx: float,
    cy: float,
    shape: Any,
    half_x: Any,
    half_y: Any = None,
) -> bool:
    """True when every outline sample lies inside at least one area ring."""
    if not has_valid_area_rings(rings):
        return False
    outline = marker_outline_points(shape, cx, cy, half_x, half_y)
    if not outline:
        return False
    for pt in outline:
        if not _point_in_any_ring(pt, rings):
            return False
    return True
