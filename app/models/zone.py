#E:\Gcon\lutron\lutron_backend\app\models\zone.py
from sqlalchemy import Column, Integer, String, ForeignKey, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database.session import Base

class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (UniqueConstraint("processor_id", "code", name="uq_zones_processor_code"),)

    id = Column(Integer, primary_key=True)
    code = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    type = Column(String(50))
    area_id = Column(Integer, ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=False, index=True)

    # Manual energy logger: optional per-zone load tuning fields
    max_power = Column(Float, nullable=True)
    high_end_trim = Column(Float, nullable=True)
    energy_trim = Column(Float, nullable=True)
    low_end_trim = Column(Float, nullable=True)
    # LEAP load controller id from href /loadcontroller/{id}
    loadcontroller_code = Column(Integer, nullable=True)

    area = relationship("Area", back_populates="zones")
    processor = relationship("Processor", back_populates="zones")
