# app/models/sensors_and_modules.py
from sqlalchemy import Column, Integer, String, ForeignKey, TIMESTAMP, Index, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.session import Base


class SensorAndModule(Base):
    """
    Unified table for all devices (sensors, modules, keypads, etc.).
    Replaces old 'sensors' and 'modules' tables.
    """
    __tablename__ = "sensors_and_modules"

    id = Column(Integer, primary_key=True, index=True)

    # Processor reference for multi-processor support
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=True)

    # Device identity
    device_code = Column(Integer, nullable=False)   # LEAP device ID (unique per processor)
    device_name = Column(String, nullable=True)
    serial_number = Column(String, nullable=True)                # unified from sensors.serial_number + modules.serial_no
    device_model = Column(String, nullable=True)                 # unified from sensors.model + modules.device_model
    device_type = Column(String, nullable=True)                  # LEAP-reported type
    device_kind = Column(String, nullable=False, default="other")  # "sensor" | "module" | "other"

    # Area linkage
    area_code = Column(String, nullable=True)  # string allows mixed numeric/text area codes
    area_id = Column(Integer, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True)

    # Status
    availability = Column(String, nullable=True)                 # "Available" | "Unavailable" | "Unknown"
    alert_status = Column(String, default="ok")                  # "ok" / "not_ok"

    # UI visibility toggle for alerts recorded in this table.
    # Used by /settings/disable_alerts and read-side alert filters.
    display = Column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at = Column(TIMESTAMP(timezone=True), nullable=True)
    reported_time = Column(TIMESTAMP(timezone=True), nullable=True)  # When alert was first reported
    solved_time = Column(TIMESTAMP(timezone=True), nullable=True)    # When alert was resolved

    # Composite unique constraint: device_code must be unique per processor
    __table_args__ = (
        Index("ix_sensors_modules_processor_device", "processor_id", "device_code", unique=True),
    )

    # Relationships
    area = relationship("Area", backref="sensors_and_modules")
    processor = relationship("Processor", backref="sensors_and_modules")