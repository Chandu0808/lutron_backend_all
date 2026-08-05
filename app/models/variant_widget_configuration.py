from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, TIMESTAMP, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.database.session import Base


class VariantWidgetConfiguration(Base):
    """Per-variant widget visibility and metadata."""

    __tablename__ = "variant_widget_configuration"
    __table_args__ = (
        UniqueConstraint(
            "variant",
            "widget_key",
            name="uq_variant_widget_configuration_variant_widget_key",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    variant = Column(String(32), nullable=False, index=True)
    widget_key = Column(String(64), nullable=False)
    display_name = Column(String(128), nullable=False)
    dropdown_name = Column(String(128), nullable=True)
    is_available = Column(Boolean, nullable=False, default=True, server_default="true")
    default_visible = Column(Boolean, nullable=False, default=False, server_default="false")
    is_visible = Column(Boolean, nullable=False, default=True, server_default="true")
    sort_order = Column(Integer, nullable=True)
    config = Column(JSON, nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
