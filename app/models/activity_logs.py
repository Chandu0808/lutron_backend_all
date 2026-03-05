from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from app.database.session import Base


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    activity_type = Column(String, nullable=False)
    activity_description = Column(String, nullable=False)

    floor_id = Column(Integer, ForeignKey("floors.id", ondelete="SET NULL"), nullable=True)
    floor_name = Column(String, nullable=True)  

    area_id = Column(Integer, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True)
    area_code = Column(String, nullable=True)  

    area_name = Column(String, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_name = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    area = relationship("Area", backref="activity_logs")
    user = relationship("User", backref="activity_logs")
    floor = relationship("Floor", backref="activity_logs")  
