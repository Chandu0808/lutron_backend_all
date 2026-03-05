from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Date, Time, Boolean, Index
from sqlalchemy.orm import relationship
from app.database.session import Base


class OccupancyLog(Base):
    __tablename__ = "occupancy_logs"

    id = Column(Integer, primary_key=True, index=True)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=True)
    area_id = Column(Integer, nullable=True)
    area_code = Column(String, nullable=True)
    floor_id = Column(Integer, ForeignKey("floors.id", ondelete="SET NULL"), nullable=True)
    occupation_status = Column(String, nullable=True)
    event_date = Column(Date, nullable=True)
    event_time = Column(DateTime, nullable=True)
    time = Column(Time, nullable=True)
    timespan = Column(Integer, nullable=True)
    count = Column(Integer, nullable=True)
    reconcile = Column(Boolean, default=False, nullable=False)

    # Relationships
    processor = relationship("Processor", backref="occupancy_logs")
    floor = relationship("Floor", backref="occupancy_logs")

    # Composite indexes for efficient queries
    __table_args__ = (
        Index("ix_occupancy_logs_processor_area", "processor_id", "area_id"),
        Index("ix_occupancy_logs_date_timespan", "event_date", "timespan"),
        Index("ix_occupancy_logs_area_code", "area_code"),
        # Performance indexes for instant_occupancy_count API
        Index("ix_occupancy_logs_event_time", "event_time"),  # Critical for time range queries
        Index("ix_occupancy_logs_area_processor_time", "area_code", "processor_id", "event_time"),  # Composite for common query pattern
        Index("ix_occupancy_logs_status_time", "occupation_status", "event_time"),  # For status filtering with time
        # Optimized composite index for instant_occupancy_count API queries
        Index("ix_occupancy_logs_date_time_area_status", "event_date", "event_time", "area_code", "processor_id", "occupation_status"),
    )

