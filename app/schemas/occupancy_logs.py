from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date, time


class OccupancyLogBase(BaseModel):
    processor_id: Optional[int] = None
    area_id: Optional[int] = None
    area_code: Optional[str] = None
    floor_id: Optional[int] = None
    occupation_status: Optional[str] = None
    event_date: Optional[date] = None
    event_time: Optional[datetime] = None
    time: Optional[time] = None
    timespan: Optional[int] = None
    count: Optional[int] = None
    reconcile: Optional[bool] = False

    class Config:
        from_attributes = True


class OccupancyLogCreate(OccupancyLogBase):
    pass


class OccupancyLogUpdate(OccupancyLogBase):
    pass


class OccupancyLogOut(OccupancyLogBase):
    id: int

    class Config:
        from_attributes = True

