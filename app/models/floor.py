from sqlalchemy import Column, Integer, String, Float
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.database.session import Base

class Floor(Base):
    __tablename__ = "floors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    image_path = Column(String, nullable=False)
    area_tree = Column(JSONB, nullable=True)
    x_left = Column(Float, nullable=True)
    x_right = Column(Float, nullable=True)
    y_top = Column(Float, nullable=True)
    y_bottom = Column(Float, nullable=True)

    areas = relationship("Area", back_populates="floor")
    user_permissions = relationship("UserPermission", back_populates="floor")