"""
Centralized FOFP global configuration (read + write).

Read paths are fail-closed for hot consumers (``/floor/light_status``).
Write paths are used by ``/fofp/config`` and layout generation flows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.fofp_settings import FOFPSettings


logger = logging.getLogger(__name__)

LOG_PREFIX = "[FOFP]"

DEFAULT_SHAPE = "circle"
DEFAULT_MARKER_SIZE = 5
DEFAULT_MARKER_COLOR = "#FDD835"
MIN_MARKER_SIZE = 4
MAX_MARKER_SIZE = 20
DEFAULT_GENERATION_STATUS = "not_generated"

VALID_SHAPES = frozenset(
    {"circle", "glowing_dot", "square", "triangle", "hexagon", "bulb"}
)

VALID_GENERATION_STATUSES = frozenset(
    {"not_generated", "generated", "partial", "failed"}
)


@dataclass(frozen=True)
class FOFPGlobalConfig:
    """In-memory FOFP config with safe defaults."""

    enabled: bool = False
    shape: str = DEFAULT_SHAPE
    marker_size: int = DEFAULT_MARKER_SIZE
    marker_color: str = DEFAULT_MARKER_COLOR
    last_generated_at: Optional[datetime] = None
    generation_status: str = DEFAULT_GENERATION_STATUS

    def as_response_dict(self) -> Dict[str, Any]:
        """Shape embedded in ``/floor/light_status`` ``fofp_config``."""
        return {
            "shape": self.shape,
            "marker_size": self.marker_size,
            "marker_color": self.marker_color,
        }

    def as_config_api_dict(self) -> Dict[str, Any]:
        """Full config payload for GET/PUT ``/fofp/config``."""
        ts = self.last_generated_at
        if ts is not None and hasattr(ts, "isoformat"):
            ts_out = ts.isoformat()
        else:
            ts_out = None
        return {
            "fofp_enabled": bool(self.enabled),
            "shape": self.shape,
            "marker_size": self.marker_size,
            "marker_color": self.marker_color,
            "last_generated_at": ts_out,
            "generation_status": self.generation_status,
        }


def normalize_shape(raw: Any) -> str:
    if not isinstance(raw, str):
        return DEFAULT_SHAPE
    key = raw.strip().lower()
    if key in VALID_SHAPES:
        return key
    return DEFAULT_SHAPE


def normalize_marker_size_min(raw: Any) -> int:
    """Per-zone layout half-axis: minimum only (no global max cap)."""
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MARKER_SIZE
    return max(MIN_MARKER_SIZE, size)


def normalize_marker_size(raw: Any) -> int:
    """Global config default marker_size (legacy 4–20 cap)."""
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MARKER_SIZE
    return max(MIN_MARKER_SIZE, min(MAX_MARKER_SIZE, size))


def resolve_marker_half_axes(
    shape_size: Any,
    shape_size_x: Any = None,
    shape_size_y: Any = None,
) -> tuple[int, int, int]:
    """
    Return (half_x, half_y, legacy_shape_size).
    Missing x/y fall back to shape_size for backward compatibility.
    """
    base = normalize_marker_size_min(shape_size)
    half_x = (
        normalize_marker_size_min(shape_size_x)
        if shape_size_x is not None
        else base
    )
    half_y = (
        normalize_marker_size_min(shape_size_y)
        if shape_size_y is not None
        else base
    )
    return half_x, half_y, max(half_x, half_y)


def normalize_marker_color(raw: Any) -> str:
    if not isinstance(raw, str):
        return DEFAULT_MARKER_COLOR
    key = raw.strip().upper()
    if len(key) == 7 and key.startswith("#"):
        try:
            int(key[1:], 16)
            return key
        except ValueError:
            return DEFAULT_MARKER_COLOR
    return DEFAULT_MARKER_COLOR


def normalize_generation_status(raw: Any) -> str:
    if not isinstance(raw, str):
        return DEFAULT_GENERATION_STATUS
    key = raw.strip().lower()
    if key in VALID_GENERATION_STATUSES:
        return key
    return DEFAULT_GENERATION_STATUS


def _row_to_config(row: Optional[FOFPSettings]) -> FOFPGlobalConfig:
    if row is None:
        return FOFPGlobalConfig()
    return FOFPGlobalConfig(
        enabled=bool(row.enabled),
        shape=normalize_shape(row.default_shape),
        marker_size=normalize_marker_size(getattr(row, "marker_size", DEFAULT_MARKER_SIZE)),
        marker_color=normalize_marker_color(
            getattr(row, "marker_color", DEFAULT_MARKER_COLOR)
        ),
        last_generated_at=getattr(row, "last_generated_at", None),
        generation_status=normalize_generation_status(
            getattr(row, "generation_status", DEFAULT_GENERATION_STATUS)
        ),
    )


def get_or_create_settings_row(db: Session) -> Optional[FOFPSettings]:
    """Return the singleton settings row, creating it with defaults if missing."""
    try:
        row = db.query(FOFPSettings).order_by(FOFPSettings.id.asc()).first()
        if row is not None:
            return row
        row = FOFPSettings(
            enabled=False,
            default_shape=DEFAULT_SHAPE,
            marker_size=DEFAULT_MARKER_SIZE,
            marker_color=DEFAULT_MARKER_COLOR,
            generation_status=DEFAULT_GENERATION_STATUS,
        )
        db.add(row)
        db.flush()
        return row
    except SQLAlchemyError as exc:
        logger.warning("%s get_or_create_settings_row failed: %s", LOG_PREFIX, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def get_fofp_settings(db: Optional[Session]) -> FOFPGlobalConfig:
    """Fail-closed read for hot paths (e.g. ``/floor/light_status``)."""
    if db is None:
        return FOFPGlobalConfig()
    try:
        row = db.query(FOFPSettings).order_by(FOFPSettings.id.asc()).first()
        return _row_to_config(row)
    except SQLAlchemyError as exc:
        logger.warning("%s settings lookup failed: %s", LOG_PREFIX, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return FOFPGlobalConfig()
    except Exception as exc:
        logger.warning("%s settings lookup unexpected: %s", LOG_PREFIX, exc)
        return FOFPGlobalConfig()


def get_fofp_config(db: Session) -> Dict[str, Any]:
    """Return full config for the admin API; never raises."""
    try:
        row = get_or_create_settings_row(db)
        return _row_to_config(row).as_config_api_dict()
    except Exception as exc:
        logger.warning("%s get_fofp_config failed: %s", LOG_PREFIX, exc)
        return FOFPGlobalConfig().as_config_api_dict()


def update_fofp_config(
    db: Session,
    *,
    enabled: Optional[bool] = None,
    shape: Optional[str] = None,
    marker_size: Optional[int] = None,
    marker_color: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Persist partial config updates. Raises on unrecoverable DB errors.
    """
    row = get_or_create_settings_row(db)
    if row is None:
        raise RuntimeError("FOFP settings row unavailable")

    if enabled is not None:
        row.enabled = bool(enabled)
    if shape is not None:
        row.default_shape = normalize_shape(shape)
    if marker_size is not None:
        row.marker_size = normalize_marker_size(marker_size)
    if marker_color is not None:
        row.marker_color = normalize_marker_color(marker_color)

    db.commit()
    db.refresh(row)
    logger.info(
        "%s Config updated enabled=%s shape=%s marker_size=%s marker_color=%s",
        LOG_PREFIX,
        row.enabled,
        row.default_shape,
        row.marker_size,
        row.marker_color,
    )
    return _row_to_config(row).as_config_api_dict()


def compute_generation_status(summary: Mapping[str, Any]) -> str:
    """Derive status string from a generate-layout summary dict."""
    try:
        generated = int(summary.get("generated") or 0)
        failed = int(summary.get("failed") or 0)
    except (TypeError, ValueError):
        return DEFAULT_GENERATION_STATUS

    if failed > 0 and generated == 0:
        return "failed"
    if failed > 0 and generated > 0:
        return "partial"
    if generated > 0:
        return "generated"
    return "generated" if int(summary.get("skipped") or 0) > 0 else DEFAULT_GENERATION_STATUS


def record_generation_result(db: Session, summary: Mapping[str, Any]) -> None:
    """Update last-generated timestamp and generation status after layout work."""
    try:
        row = get_or_create_settings_row(db)
        if row is None:
            return
        row.last_generated_at = datetime.now(timezone.utc)
        row.generation_status = compute_generation_status(summary)
        db.commit()
        logger.info(
            "%s Generation recorded status=%s generated=%s failed=%s",
            LOG_PREFIX,
            row.generation_status,
            summary.get("generated"),
            summary.get("failed"),
        )
    except SQLAlchemyError as exc:
        logger.warning("%s record_generation_result failed: %s", LOG_PREFIX, exc)
        try:
            db.rollback()
        except Exception:
            pass
