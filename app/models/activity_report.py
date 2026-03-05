from sqlalchemy import Column, BigInteger, String, Text, Date, Time, TIMESTAMP, ForeignKey, func, event
from sqlalchemy.orm import relationship, Session
from app.models.events import Base
from app.models.area import Area  # Import Area model


class ActivityReport(Base):
    __tablename__ = "activity_report"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)

    # UI-level fields
    date = Column(Date, nullable=False)                          
    time = Column(Time, nullable=False)                          
    area_id = Column(BigInteger, ForeignKey("areas.id"), nullable=True)
    area_code = Column(String(50), nullable=True, index=True)    # Auto-filled from Area.code
    area_name = Column(String(255), nullable=True)               
    activity_type = Column(String(100), nullable=False)          
    sub_activity_type = Column(String(100), nullable=True)       # NEW COLUMN
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    user_name = Column(String(100), nullable=True)               
    activity_desc = Column(Text, nullable=False)                 

    # Metadata
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    # Relationships
    area = relationship("Area", backref="activity_reports", lazy="joined")
    user = relationship("User", backref="activity_reports", lazy="joined")


# -------------------- AUTO POPULATE LOGIC -------------------- #
@event.listens_for(ActivityReport, "before_insert")
@event.listens_for(ActivityReport, "before_update")
def set_area_code(mapper, connection, target: ActivityReport):
    """
    Auto-populate area_code from Area.code when area_id is set.
    If area_id is invalid, area_code will be None.
    """
    if target.area_id:
        session = Session.object_session(target)
        if session:
            area = session.query(Area).filter(Area.id == target.area_id).first()
            target.area_code = area.code if area else None
    else:
        target.area_code = None
