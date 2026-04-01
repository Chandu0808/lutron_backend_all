# E:\Gcon\lutron\lutron_backend\app\models\processor.py
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database.session import Base


class Processor(Base):
    __tablename__ = "processor"

    id = Column(Integer, primary_key=True, index=True)
    server = Column(String)
    ipv4 = Column(String)
    system = Column(String)
    serial = Column(String, unique=True)
    mac = Column(String)
    claimed = Column(String)
    sw_version = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = Column(String)

    # New columns
    ping_status = Column(String, nullable=True)   # e.g., "reachable" / "unreachable"
    pinged_at = Column(DateTime, nullable=True)   # last time ping was attempted
    
    # Alert timestamp tracking
    reported_time = Column(DateTime, nullable=True)  # When processor alert was first reported
    solved_time = Column(DateTime, nullable=True)    # When processor alert was resolved

    # UI visibility toggle for alerts recorded in this table.
    # Used by /settings/disable_alerts and read-side alert filters.
    display = Column(Boolean, nullable=False, default=True)

    # Enrichment fields from /device/{id}
    associated_area = Column(String, nullable=True)   # href of AssociatedArea
    device_code = Column(String, nullable=True)       # href of device (/device/xxx)
    model_number = Column(String, nullable=True)      # REP-QP-2L
    installed_at = Column(DateTime, nullable=True)    # parsed datetime from FirmwareImage.Installed

    # Handshake status
    handshake_status = Column(Boolean, nullable=True, default=None)  # None=not attempted, True=success, False=failed

    areas = relationship("Area", back_populates="processor")
    zones = relationship("Zone", back_populates="processor")
