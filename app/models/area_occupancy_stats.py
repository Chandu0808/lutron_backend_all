# app/models/area_occupancy_stats.py
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Index, Date
from app.database.session import Base
from datetime import datetime

class AreaOccupancyStat(Base):
    __tablename__ = "area_occupancy_stats"

    id = Column(Integer, primary_key=True, index=True)
    area_id = Column(Integer, nullable=False)
    area_code = Column(String, nullable=False)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=True)  # Added for multi-processor support
    occupancy_status = Column(String, nullable=True)

    # store TRUE for dummy rows, NULL for real rows
    approximated_filler = Column(Boolean, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Time-based analysis columns (manually populated by energy_logger)
    created_date = Column(Date, nullable=True)
    timespan_15min = Column(String(4), nullable=True)
    timespan_6hr = Column(Integer, nullable=True)
    
    # Composite indexes for efficient queries
    __table_args__ = (
        Index("ix_area_occupancy_stats_code_processor", "area_code", "processor_id"),
        Index("ix_area_occupancy_stats_date_timespan", "created_date", "timespan_6hr"),
        Index("ix_area_occupancy_stats_timespan_15min", "timespan_15min"),
    )
