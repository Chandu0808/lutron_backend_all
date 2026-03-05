from sqlalchemy import Column, Integer, Float, ForeignKey, DateTime, func, Boolean, Computed, Index, Date, String
from sqlalchemy.orm import relationship
from app.database.session import Base


class AreaEnergyStat(Base):
    __tablename__ = "area_energy_stats"

    id = Column(Integer, primary_key=True, index=True)

    area_id = Column(Integer, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True)
    area_code = Column(Integer, nullable=False)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=True)  # Added for multi-processor support

    instantaneous_power = Column(Float, nullable=True)
    instantaneous_max_power = Column(Float, nullable=True)

    # GENERATED ALWAYS column
    instantaneous_saved_power = Column(
        Float,
        Computed("instantaneous_max_power - instantaneous_power", persisted=True),
    )

    time_elapsed_in_sec = Column(Integer, nullable=True)
    energy_consumed_in_Wh = Column(Float, nullable=True)
    energy_saved_in_Wh = Column(Float, nullable=True)
    total_energy = Column(Float, nullable=True)

    # store TRUE for dummy rows, NULL for real rows
    approximated_filler = Column(Boolean, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Time-based analysis columns (manually populated by energy_logger)
    created_date = Column(Date, nullable=True)
    timespan_15min = Column(String(4), nullable=True)
    timespan_6hr = Column(Integer, nullable=True)

    area = relationship("Area", back_populates="energy_stats", lazy="joined")
    
    # Composite indexes for efficient queries
    __table_args__ = (
        Index("ix_area_energy_stats_code_processor", "area_code", "processor_id"),
        Index("ix_area_energy_stats_date_timespan", "created_date", "timespan_6hr"),
        Index("ix_area_energy_stats_timespan_15min", "timespan_15min"),
    )
