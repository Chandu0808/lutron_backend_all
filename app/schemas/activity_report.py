from pydantic import BaseModel
from typing import List, Union, Optional
from datetime import date, time, datetime


class FloorFilter(BaseModel):
    floor_id: int
    areas: Union[str, List[str]]  # "all" or list of area_codes


class ActivityReportFilter(BaseModel):
    floors: List[FloorFilter]
    activity_type: Optional[str] = None
    start_date: date
    start_time: time
    end_date: date
    end_time: time


class ActivityLogResponse(BaseModel):
    id: int
    activity_type: str
    activity_description: str
    area_name: Optional[str]
    area_code: Optional[str]  # Added to match API response
    user_id: Optional[int]
    user_name: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
