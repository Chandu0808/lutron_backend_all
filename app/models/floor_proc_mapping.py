from sqlalchemy import Column, Integer, ForeignKey
from app.database.session import Base

class FloorProcMapping(Base):
    __tablename__ = "floor_proc_mapping"

    id = Column(Integer, primary_key=True, index=True)
    floor_id = Column(Integer, ForeignKey("floors.id", ondelete="CASCADE"), nullable=False)
    processor_id = Column(Integer, ForeignKey("processor.id", ondelete="CASCADE"), nullable=False)
