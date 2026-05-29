"""
Geometry utilities for FOFP (floorplan) layout generation.

Design rules (intentional):
- Pure utility module: no DB access, no API code, no SQLAlchemy models,
  no FastAPI imports, no I/O, no logging, no module-level side effects.
- Only Python standard library is used (math, random).
- Functions accept plain (x, y) tuples; dicts with x/y keys and objects with
  .x / .y attributes are also accepted to stay friendly with both the
  Coordinate ORM model and Pydantic Point schema without importing them.
- Determinism: every function is deterministic except generate_candidate_point,
  which is the only intentional source of randomness.
"""

from __future__ import annotations

import math
import random
from typing import Any, Iterable, List, Optional, Sequence, Tuple, TypedDict


Point = Tuple[float, float]


class BBox(TypedDict):
    """Axis-aligned bounding box."""

    min_x: float
    max_x: float
    min_y: float
    max_y: float


def _coerce_point(p: Any) -> Point:
    """
    Normalize a point-like value into a ``(x, y)`` tuple of floats.

    Accepted forms:
      * 2-element tuple or list, e.g. ``(120, 250)`` or ``[120.0, 250.0]``
      * dict with ``"x"`` and ``"y"`` keys, e.g. ``{"x": 120, "y": 250}``
      * any object exposing ``.x`` and ``.y`` attributes
        (works for SQLAlchemy ``Coordinate`` rows and Pydantic ``Point`` models)

    Raises:
        ValueError: if the input cannot be interpreted as a 2D point.
    """
    if isinstance(p, (tuple, list)) and len(p) == 2:
        return float(p[0]), float(p[1])
    if isinstance(p, dict) and "x" in p and "y" in p:
        return float(p["x"]), float(p["y"])
    if hasattr(p, "x") and hasattr(p, "y"):
        return float(p.x), float(p.y)
    raise ValueError(f"Invalid point value: {p!r}")


def distance_between_points(p1: Any, p2: Any) -> float:
    """
    Euclidean distance between two points.

    Args:
        p1: First point (any point-like value, see :func:`_coerce_point`).
        p2: Second point (any point-like value).

    Returns:
        Non-negative float distance.
    """
    x1, y1 = _coerce_point(p1)
    x2, y2 = _coerce_point(p2)
    return math.hypot(x2 - x1, y2 - y1)


def _is_point_on_segment(point: Point, a: Point, b: Point, eps: float = 1e-7) -> bool:
    """True when ``point`` lies on closed segment ``a``–``b`` (matches frontend FOFP)."""
    px, py = point
    ax, ay = a
    bx, by = b
    cross = (py - ay) * (bx - ax) - (px - ax) * (by - ay)
    if abs(cross) > eps:
        return False
    dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
    if dot < 0:
        return False
    len_sq = (bx - ax) ** 2 + (by - ay) ** 2
    return dot <= len_sq


def point_in_polygon(point: Any, polygon: Sequence[Any]) -> bool:
    """
    Test whether ``point`` lies inside ``polygon`` using ray casting.

    Points on an edge are treated as inside (parity with frontend FOFP).
    """
    if not polygon:
        return False

    pts: List[Point] = [_coerce_point(p) for p in polygon]
    n = len(pts)
    if n < 3:
        return False

    px, py = _coerce_point(point)
    inside = False

    j = n - 1
    for i in range(n):
        pi = pts[i]
        pj = pts[j]
        if _is_point_on_segment((px, py), pi, pj):
            return True
        xi, yi = pi
        xj, yj = pj

        if (yi > py) != (yj > py):
            x_intersect = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_intersect:
                inside = not inside
        j = i

    return inside


def calculate_polygon_bbox(polygon: Sequence[Any]) -> Optional[BBox]:
    """
    Compute the axis-aligned bounding box of a polygon.

    Args:
        polygon: Sequence of point-like vertices.

    Returns:
        A :class:`BBox` dict with ``min_x``, ``max_x``, ``min_y``, ``max_y``,
        or ``None`` if the polygon has no usable vertices.
    """
    if not polygon:
        return None

    pts: List[Point] = [_coerce_point(p) for p in polygon]
    if not pts:
        return None

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
    }


def _bbox_is_valid(bbox: Any) -> bool:
    """Return True if ``bbox`` is a well-formed BBox dict."""
    if not isinstance(bbox, dict):
        return False
    required = ("min_x", "max_x", "min_y", "max_y")
    if not all(k in bbox for k in required):
        return False
    try:
        min_x = float(bbox["min_x"])
        max_x = float(bbox["max_x"])
        min_y = float(bbox["min_y"])
        max_y = float(bbox["max_y"])
    except (TypeError, ValueError):
        return False
    return min_x <= max_x and min_y <= max_y


def generate_candidate_point(bbox: BBox) -> Point:
    """
    Generate a uniformly random candidate point inside ``bbox``.

    For a degenerate bbox (zero width and/or zero height) the returned point
    coincides with the relevant edge or corner. This is the only function in
    this module that is non-deterministic.

    Args:
        bbox: A valid BBox dict.

    Returns:
        A ``(x, y)`` tuple of floats inside the bbox (inclusive of edges).

    Raises:
        ValueError: if ``bbox`` is missing keys or has inverted bounds.
    """
    if not _bbox_is_valid(bbox):
        raise ValueError(f"Invalid bbox: {bbox!r}")

    min_x = float(bbox["min_x"])
    max_x = float(bbox["max_x"])
    min_y = float(bbox["min_y"])
    max_y = float(bbox["max_y"])

    x = random.uniform(min_x, max_x)
    y = random.uniform(min_y, max_y)
    return x, y


def validate_spacing(
    candidate_point: Any,
    existing_points: Iterable[Any],
    min_distance: float,
) -> bool:
    """
    Verify that ``candidate_point`` is at least ``min_distance`` away from
    every point in ``existing_points``.

    Args:
        candidate_point: Point to validate.
        existing_points: Iterable of already-placed points.
        min_distance: Required minimum separation. Values <= 0 or ``None``
            disable the spacing check and the function returns True.

    Returns:
        True if the candidate satisfies the spacing rule, otherwise False.
    """
    if min_distance is None or min_distance <= 0:
        return True
    if not existing_points:
        return True

    cx, cy = _coerce_point(candidate_point)
    threshold = float(min_distance)
    for ep in existing_points:
        ex, ey = _coerce_point(ep)
        if math.hypot(cx - ex, cy - ey) < threshold:
            return False
    return True


def fallback_to_bbox_center(bbox: BBox) -> Point:
    """
    Return the geometric center of ``bbox``.

    Intended as a deterministic fallback when a polygon is malformed,
    degenerate, or too small to randomly place a point inside.

    Args:
        bbox: A valid BBox dict.

    Returns:
        ``(center_x, center_y)`` tuple.

    Raises:
        ValueError: if ``bbox`` is missing keys or has inverted bounds.
    """
    if not _bbox_is_valid(bbox):
        raise ValueError(f"Invalid bbox: {bbox!r}")

    cx = (float(bbox["min_x"]) + float(bbox["max_x"])) / 2.0
    cy = (float(bbox["min_y"]) + float(bbox["max_y"])) / 2.0
    return cx, cy


__all__ = [
    "Point",
    "BBox",
    "distance_between_points",
    "point_in_polygon",
    "calculate_polygon_bbox",
    "generate_candidate_point",
    "validate_spacing",
    "fallback_to_bbox_center",
]
