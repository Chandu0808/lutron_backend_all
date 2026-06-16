from sqlalchemy import Column, ForeignKey, Integer, String, TIMESTAMP, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.database.session import Base


class DashboardLayout(Base):
    """Keyed dashboard layout documents (Phase 3 central config)."""

    __tablename__ = "dashboard_layout"
    __table_args__ = (
        UniqueConstraint("layout_key", name="uq_dashboard_layout_layout_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
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
