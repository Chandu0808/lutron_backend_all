#E:\Gcon\lutron\lutron_backend\app\models\zone.py
from sqlalchemy import Column, Integer, String, ForeignKey, Float
from sqlalchemy.orm import relationship
from app.database.session import Base

class Zone(Base):
    __tablename__ = "zones"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    type = Column(String(50))
    area_id = Column(Integer, ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)

    # Manual energy logger: max power (W) and high end trim (%); optional, no default
    max_power = Column(Float, nullable=True)
    high_end_trim = Column(Float, nullable=True)

    area = relationship("Area", back_populates="zones")
