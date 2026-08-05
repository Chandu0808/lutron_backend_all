from sqlalchemy import Column, ForeignKey, Integer, String, TIMESTAMP, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.database.session import Base


class VariantDashboardLayout(Base):
    """Per-variant dashboard layout/order documents."""

    __tablename__ = "variant_dashboard_layout"
    __table_args__ = (
        UniqueConstraint(
            "variant",
            "layout_key",
            name="uq_variant_dashboard_layout_variant_layout_key",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    variant = Column(String(32), nullable=False, index=True)
    layout_key = Column(String(64), nullable=False)
    layout_json = Column(JSON, nullable=False)
    layout_version = Column(Integer, nullable=False, default=1, server_default="1")
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
