# app/models/fofp.py
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    ForeignKey,
    TIMESTAMP,
    UniqueConstraint,
    Index,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database.session import Base


class FOFPShape(Base):
    __tablename__ = "fofp_shapes"
    __table_args__ = (
        UniqueConstraint("name", name="uq_fofp_shapes_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False)
    default_color = Column(String(32), nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class ZoneFloorplanPosition(Base):
    __tablename__ = "zone_floorplan_positions"
    __table_args__ = (
        UniqueConstraint("zone_id", name="uq_zone_floorplan_positions_zone_id"),
        Index("ix_zone_floorplan_positions_floor_id", "floor_id"),
        Index("ix_zone_floorplan_positions_area_id", "area_id"),
        Index("ix_zone_floorplan_positions_floor_area", "floor_id", "area_id"),
    )

    id = Column(Integer, primary_key=True, index=True)

    floor_id = Column(
        Integer,
        ForeignKey("floors.id", ondelete="CASCADE"),
        nullable=False,
    )
    area_id = Column(
        Integer,
        ForeignKey("areas.id", ondelete="CASCADE"),
        nullable=False,
    )
    zone_id = Column(
        Integer,
        ForeignKey("zones.id", ondelete="SET NULL"),
        nullable=True,
    )

    x = Column(Float, nullable=False)
    y = Column(Float, nullable=False)

    marker_shape = Column(String(64), nullable=True)
    shape_size = Column(
        Integer,
        nullable=False,
        default=5,
        server_default="5",
    )
    shape_size_x = Column(Integer, nullable=True)
    shape_size_y = Column(Integer, nullable=True)
    placement_source = Column(
        String(16),
        nullable=False,
        default="auto",
        server_default="auto",
    )
    zone_available = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    modified_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    floor = relationship("Floor")
    area = relationship("Area")
    zone = relationship("Zone")
