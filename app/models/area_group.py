from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.database.session import Base

class AreaGroup(Base):
    __tablename__ = "area_groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    special = Column(Boolean, default=False, nullable=False)
    mappings = relationship("AreaGroupMapping", back_populates="group", cascade="all, delete")

class AreaGroupMapping(Base):
    __tablename__ = "area_group_mappings"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("area_groups.id", ondelete="CASCADE"))
    area_id = Column(Integer, ForeignKey("areas.id", ondelete="CASCADE"))  # <- must be FK to Area.id
    floor_id = Column(Integer, ForeignKey("floors.id", ondelete="CASCADE"))

    group = relationship("AreaGroup", back_populates="mappings")
    area = relationship("Area") 