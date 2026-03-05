from sqlalchemy import (
    Column, Integer, String, Text, TIMESTAMP, ForeignKey, Index, Float
)
from sqlalchemy.sql import func
from app.database.session import Base

class ProcessorAreaEvent(Base):
    __tablename__ = "processor_area_events"

    id = Column(Integer, primary_key=True)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=False)
    area_id = Column(Integer, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True)

    area_href = Column(Text, nullable=False)
    area_code = Column(Integer)

    level = Column(Integer)
    occupancy_status = Column(String)
    current_scene_href = Column(Text)
    current_scene_code = Column(Integer)
    instantaneous_power = Column(Integer)
    instantaneous_max_power = Column(Integer)

    # New fields for keypad tracking
    button_code = Column(Integer, nullable=True)         # e.g., 2706
    button_activity = Column(String, nullable=True)      # e.g., "pressed"

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_processor_area_events_processor_id_area_id", "processor_id", "area_id"),
    )


class ProcessorZoneEvent(Base):
    __tablename__ = "processor_zone_events"

    id = Column(Integer, primary_key=True)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=False)
    area_id = Column(Integer, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True)
    zone_id = Column(Integer, ForeignKey("zones.id", ondelete="SET NULL"), nullable=True)

    zone_href = Column(Text, nullable=False)
    zone_code = Column(Integer)

    level = Column(Integer)
    switched_level = Column(String)
    white_tuning_kelvin = Column(Integer)
    status_accuracy = Column(String)

    # Manual energy logger: computed from level + zone.max_power + zone.high_end_trim
    zone_instantaneous_power = Column(Float, nullable=True)
    zone_instantaneous_max_power = Column(Float, nullable=True)

    # New fields for keypad tracking
    button_code = Column(Integer, nullable=True)         # e.g., 2706
    button_activity = Column(String, nullable=True)      # e.g., "pressed"

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_processor_zone_events_processor_id_zone_id", "processor_id", "zone_id"),
    )


class ProcessorConnectionError(Base):
    __tablename__ = "processor_connection_errors"

    id = Column(Integer, primary_key=True)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=False)
    message = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class ProcessorEvent(Base):
    __tablename__ = "processor_events"

    id = Column(Integer, primary_key=True)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String)  # 'area', 'zone', 'ping'
    event_reference_id = Column(Integer)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class CurrentAreaEvent(Base):
    __tablename__ = "current_area_status"

    id = Column(Integer, primary_key=True)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=False)
    area_id = Column(Integer, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True)

    area_href = Column(Text, nullable=True)
    area_code = Column(Integer)

    occupancy_status = Column(String, nullable=True)
    current_scene_href = Column(Text, nullable=True)
    current_scene_code = Column(Integer, nullable=True)
    instantaneous_power = Column(Float, nullable=True)
    instantaneous_max_power = Column(Float, nullable=True)

    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_current_area_status_processor_id_area_code", "processor_id", "area_code"),
    )


class CurrentZoneEvent(Base):
    __tablename__ = "current_zone_status"

    id = Column(Integer, primary_key=True)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=False)
    area_id = Column(Integer, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True)
    zone_id = Column(Integer, ForeignKey("zones.id", ondelete="SET NULL"), nullable=True)

    area_code = Column(Integer, nullable=True)
    zone_code = Column(Integer, nullable=True)
    zone_href = Column(Text, nullable=False)

    level = Column(Integer, nullable=True)
    switched_level = Column(String, nullable=True)
    white_tuning_kelvin = Column(Integer, nullable=True)
    status_accuracy = Column(String, nullable=True)

    # Manual energy logger: zone_instantaneous_max_power = max_power; zone_instantaneous_power uses trim
    zone_instantaneous_power = Column(Float, nullable=True)
    zone_instantaneous_max_power = Column(Float, nullable=True)

    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_current_zone_status_processor_id_zone_code", "processor_id", "zone_code"),
    )
