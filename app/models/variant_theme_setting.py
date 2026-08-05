from sqlalchemy import Column, ForeignKey, Integer, String, TIMESTAMP, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.database.session import Base


class VariantThemeSetting(Base):
    """Per-variant theme/application settings."""

    __tablename__ = "variant_theme_setting"
    __table_args__ = (
        UniqueConstraint(
            "variant",
            "config_group",
            "config_key",
            name="uq_variant_theme_setting_variant_group_key",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    variant = Column(String(32), nullable=False, index=True)
    config_group = Column(String(32), nullable=False)
    config_key = Column(String(64), nullable=False)
    config_value = Column(JSON, nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
