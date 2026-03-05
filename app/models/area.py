from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, Float
from sqlalchemy.orm import relationship
from app.database.session import Base

class Area(Base):
    __tablename__ = "areas"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, index=True)
    name = Column(String)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=False)
    floor_id = Column(Integer, ForeignKey("floors.id", ondelete="CASCADE"), nullable=True)
    is_leaf = Column(Boolean, default=False)

    area_sqft = Column(Float, nullable=True)
    area_sqm = Column(Float, nullable=True)

    coordinates = relationship("Coordinate", back_populates="area", cascade="all, delete")
    zones = relationship("Zone", back_populates="area", cascade="all, delete")
    floor = relationship("Floor", back_populates="areas")
    processor = relationship("Processor", back_populates="areas")
    energy_stats = relationship("AreaEnergyStat", back_populates="area", cascade="all, delete-orphan")
