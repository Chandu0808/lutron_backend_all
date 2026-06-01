# app/models/drivers.py
from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, ForeignKey, Index, Boolean
from sqlalchemy.sql import func
from app.database.session import Base

class Driver(Base):
    __tablename__ = "drivers"

    id = Column(Integer, primary_key=True, index=True)

    # References
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=True)  # Added for multi-processor support
    area_id = Column(Integer, nullable=True)        # FK to areas.id if needed
    area_code = Column(Integer, nullable=True)      # LEAP area code
    zone_code = Column(Integer, nullable=True)      # LEAP zone code
    zone_id = Column(Integer, ForeignKey("zones.id", ondelete="SET NULL"), nullable=True)
    device_code = Column(Integer, nullable=True)    # Device href/code
    device_type = Column(String, nullable=True)     # e.g., keypad, dimmer, sensor
    loadcontroller_code = Column(Integer, nullable=True)

    # New column for storing friendly device name
    device_name = Column(String, nullable=True)     # e.g., "Living Room Dimmer"

    # Alert info
    error_code = Column(String, nullable=True)      # Error identifier
    description = Column(Text, nullable=True)       # Human-readable explanation
    alert_status = Column(String, nullable=True)    # "ok" / "not_ok"

    # UI visibility toggle for alerts recorded in this table.
    # Used by /settings/disable_alerts and read-side alert filters.
    display = Column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at = Column(TIMESTAMP(timezone=True), nullable=True)
    reported_time = Column(TIMESTAMP(timezone=True), nullable=True)  # When alert was first reported
    solved_time = Column(TIMESTAMP(timezone=True), nullable=True)    # When alert was resolved
    
    # Composite index for efficient loadcontroller lookups
    __table_args__ = (
        Index("ix_drivers_loadcontroller_processor", "loadcontroller_code", "processor_id"),
    )
