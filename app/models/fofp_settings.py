"""
FOFP (Floor Overlay / Floorplan Positioning) global config.

A single-row table holding the feature flag and default shape configuration
for the read-only FOFP overlay on existing floor pages.

Following the per-feature settings table convention already used elsewhere in
the codebase (see ``alert_type_display_settings``, ``email_settings``).

Strictly additive: this model is independent of every existing table and is
only read by the augmented ``/floor/light_status`` response and by the FOFP
admin UI. The lookup helper in ``app/crud/fofp_settings.py`` falls back to
disabled defaults whenever the row or table is missing, so this model is safe
to introduce without breaking any pre-existing deployment.
"""

from sqlalchemy import Boolean, Column, Integer, String, TIMESTAMP
from sqlalchemy.sql import func

from app.database.session import Base


class FOFPSettings(Base):
    __tablename__ = "fofp_settings"

    id = Column(Integer, primary_key=True, index=True)
    enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    default_shape = Column(
        String(64), nullable=False, default="circle", server_default="circle"
    )
    marker_size = Column(
        Integer, nullable=False, default=5, server_default="5"
    )
    marker_color = Column(
        String(7), nullable=False, default="#FDD835", server_default="#FDD835"
    )
    last_generated_at = Column(TIMESTAMP(timezone=True), nullable=True)
    generation_status = Column(
        String(32),
        nullable=False,
        default="not_generated",
        server_default="not_generated",
    )
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    modified_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
