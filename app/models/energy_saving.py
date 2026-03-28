from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from app.database.session import Base


class AreaEnergySavingByStrategy(Base):
    __tablename__ = "area_energy_saving_by_strategy"

    id = Column(Integer, primary_key=True, index=True)
    area_code = Column(Integer, nullable=False)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=True)  # Added for multi-processor support

    # Power metrics
    instantaneous_power = Column(Float, nullable=True)
    instantaneous_max_power = Column(Float, nullable=True)

    # Timestamp
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Link to activity_report
    activity_report_id = Column(
        Integer,
        ForeignKey("activity_report.id", ondelete="SET NULL"),
        nullable=True
    )

    # Metadata
    last_activity = Column(String, nullable=True)
    activity_description = Column(String, nullable=True)

    # Strategy classification: GUI, Keypad, Schedule, Sensors, etc.
    strategy_type = Column(String, nullable=True)

    # Calculations
    time_elapsed_in_sec = Column(Integer, nullable=True)  # duration in seconds
    energy_consumed_in_Wh = Column(Float, nullable=True)
    energy_saved_in_Wh = Column(Float, nullable=True)
    trim_savings = Column(Float, nullable=True)  # Wh: zone trim power × hours when row is closed (see listener)
    total_energy = Column(Float, nullable=True)  # sum of consumed + saved
    
    # Composite index for efficient queries by (area_code, processor_id)
    __table_args__ = (
        Index("ix_area_energy_saving_code_processor", "area_code", "processor_id"),
    )
