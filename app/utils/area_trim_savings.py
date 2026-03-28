"""
Area-level trim savings from zone high_end_trim and max_power.

For each zone with high_end_trim < 100 and non-null max_power, contributes:
  (100 - high_end_trim) / 100 * max_power
Zones with null high_end_trim or null max_power contribute 0 (fallback).
"""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def zone_trim_contribution_watts(high_end_trim: Optional[float], max_power: Optional[float]) -> float:
    """
    Watts of headroom "cut" vs 100% high-end trim for one zone.
    Only applies when both values are set and high_end_trim is strictly below 100.
    """
    if high_end_trim is None or max_power is None:
        return 0.0
    try:
        ht = float(high_end_trim)
        mp = float(max_power)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(ht) or math.isnan(mp) or math.isinf(ht) or math.isinf(mp):
        return 0.0
    if ht < 0.0 or ht >= 100.0:
        return 0.0
    if mp <= 0:
        return 0.0
    return (100.0 - ht) / 100.0 * mp


def compute_trim_savings_for_area(db: "Session", area_code: int, processor_id: int) -> float:
    """
    Sum trim contributions for all zones in the area identified by (area_code, processor_id).
    Returns 0.0 if the area is missing or on unexpected errors (logged).
    """
    from app.models.area import Area
    from app.models.zone import Zone

    try:
        area = (
            db.query(Area)
            .filter(Area.code == str(area_code), Area.processor_id == processor_id)
            .first()
        )
        if not area:
            return 0.0
        zones = db.query(Zone).filter(Zone.area_id == area.id).all()
        total = 0.0
        for z in zones:
            total += zone_trim_contribution_watts(z.high_end_trim, z.max_power)
        return total
    except Exception as e:
        logger.warning("compute_trim_savings_for_area failed area_code=%s processor_id=%s: %s", area_code, processor_id, e)
        return 0.0
