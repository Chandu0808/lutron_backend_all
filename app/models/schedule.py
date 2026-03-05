from sqlalchemy import Column, Integer, String, JSON, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.database.session import Base


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    schedule_type = Column(String, nullable=False)           # "DayOfWeek" or "SpecificDates"
    schedule_span = Column(String, nullable=False)           # "Forever" or "CustomDates"
    time_of_day = Column(JSON, nullable=False)               # Always required


    days = Column(JSON, nullable=True)                       # Required if schedule_type == "DayOfWeek"
    specific_dates = Column(JSON, nullable=True)             # Required if schedule_type == "SpecificDates"

    begin_date = Column(JSON, nullable=True)                 # Required if schedule_span == "CustomDates"
    end_date = Column(JSON, nullable=True)

    quick_control_id = Column(Integer, ForeignKey("quick_controls.id", ondelete="SET NULL"), nullable=True)
    quick_control = relationship("QuickControl")

    group_id = Column(Integer, ForeignKey("schedule_groups.id", ondelete="SET NULL"), nullable=True)
    group = relationship("ScheduleGroups", back_populates="schedules")

    is_active = Column(Boolean, default=True)

class ScheduleGroups(Base):
    __tablename__ = "schedule_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    schedules = relationship("Schedule", back_populates="group", cascade="all, delete-orphan")
