from sqlalchemy import Column, ForeignKey, Integer, String, TIMESTAMP, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.database.session import Base


class InstallationSettings(Base):
    """Installation-wide key-value configuration (Phase 3 central config)."""

    __tablename__ = "installation_settings"
    __table_args__ = (
        UniqueConstraint("setting_key", name="uq_installation_settings_setting_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    setting_key = Column(String(64), nullable=False)
    setting_value = Column(JSON, nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
