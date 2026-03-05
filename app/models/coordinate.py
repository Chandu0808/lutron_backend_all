from sqlalchemy import Column, Integer, ForeignKey, Float
from sqlalchemy.orm import relationship
from app.database.session import Base

class Coordinate(Base):
    __tablename__ = "coordinates"

    id = Column(Integer, primary_key=True, index=True)
    area_id = Column(Integer, ForeignKey("areas.id"))
    x = Column(Float)
    y = Column(Float)
    polygon_index = Column(Integer, default=0, nullable=False, server_default="0")

    area = relationship("Area", back_populates="coordinates")
