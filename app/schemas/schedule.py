from pydantic import BaseModel, Field, model_validator, field_validator
from typing import Optional, List, Dict, Literal
from datetime import datetime


class DateSchema(BaseModel):
    year: int = Field(..., alias="Year")
    month: int = Field(..., alias="Month")
    day: int = Field(..., alias="Day")

    @field_validator('year')
    @classmethod
    def validate_year(cls, v):
        if v < 1900 or v > 2100:
            raise ValueError("Year must be between 1900 and 2100")
        return v

    @field_validator('month')
    @classmethod
    def validate_month(cls, v):
        if v < 1 or v > 12:
            raise ValueError("Month must be between 1 and 12")
        return v

    @model_validator(mode='after')
    def validate_day(self):
        """Validate day is valid for the given month and year"""
        try:
            # This will raise ValueError if the date is invalid
            datetime(self.year, self.month, self.day)
        except ValueError as e:
            raise ValueError(f"Invalid date: {str(e)}")
        return self

    class Config:
        populate_by_name = True


class TimeOfDaySchema(BaseModel):
    hour: int = Field(..., alias="Hour", ge=0, le=23)
    minute: int = Field(..., alias="Minute", ge=0, le=59)
    second: Optional[int] = Field(0, alias="Second", ge=0, le=59)

    @field_validator('hour')
    @classmethod
    def validate_hour(cls, v):
        if v < 0 or v > 23:
            raise ValueError("Hour must be between 0 and 23")
        return v

    @field_validator('minute')
    @classmethod
    def validate_minute(cls, v):
        if v < 0 or v > 59:
            raise ValueError("Minute must be between 0 and 59")
        return v

    @field_validator('second')
    @classmethod
    def validate_second(cls, v):
        if v is not None and (v < 0 or v > 59):
            raise ValueError("Second must be between 0 and 59")
        return v

    class Config:
        populate_by_name = True


class QuickControlAreaActionSchema(BaseModel):
    type: str

    # Scene
    scene_code: Optional[int] = Field(None, alias="scene_code")
    scene_name: Optional[str] = None

    # Zone
    zone_id: Optional[int] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    zone_status: Optional[str] = None
    zone_brightness: Optional[str] = None
    zone_temperature: Optional[str] = None

    # Occupancy
    occupancy_setting: Optional[str] = None

    # Shade
    shade_group_id: Optional[int] = None
    shade_group_name: Optional[str] = None
    shade_level: Optional[str] = None

    # Area Status
    area_status: Optional[str] = None

    @model_validator(mode="after")
    def validate_required_fields(self) -> "QuickControlAreaActionSchema":
        if self.type == "zone_status":
            if any([self.zone_type, self.zone_status, self.zone_brightness, self.zone_temperature]) and self.zone_id is None:
                raise ValueError("zone_id is required when any zone_* field is provided.")
        if self.type == "shade_group_status":
            if self.shade_level is not None and self.shade_group_id is None:
                raise ValueError("shade_group_id is required when shade_level is provided.")
        if self.type == "area_status":
            if self.area_status is not None and self.area_status not in ["On", "Off"]:
                raise ValueError("area_status must be 'On' or 'Off' when type is 'area_status'.")
        return self

    class Config:
        populate_by_name = True
        from_attributes = True


class QuickControlAreaSchema(BaseModel):
    area_id: int
    actions: List[QuickControlAreaActionSchema]

    class Config:
        from_attributes = True


class ScheduleCreate(BaseModel):
    name: str
    schedule_type: Literal["DayOfWeek", "SpecificDates"]
    schedule_span: Literal["Forever", "CustomDates"]
    days: Optional[Dict[str, bool]] = None
    specific_dates: Optional[List[DateSchema]] = None
    begin_date: Optional[DateSchema] = None
    end_date: Optional[DateSchema] = None
    time_of_day: TimeOfDaySchema
    areas: List[QuickControlAreaSchema]
    is_active: Optional[bool] = True
    schedule_group_id: Optional[int] = Field(default=None, alias="schedule_group_id")
    new_schedule_group_name: Optional[str] = None
    quick_control_id: Optional[int] = Field(default=None, alias="quick_control_id")

    @model_validator(mode="after")
    def validate_schedule_fields(self) -> "ScheduleCreate":
        if self.schedule_type == "DayOfWeek":
            if not self.days:
                raise ValueError("Days of the week must be selected when schedule type is 'DayOfWeek'")
            if self.schedule_span == "CustomDates":
                if not self.begin_date or not self.end_date:
                    raise ValueError("Begin date and end date are required when schedule span is 'CustomDates'")
            elif self.schedule_span == "Forever":
                if self.begin_date or self.end_date:
                    raise ValueError("Begin date and end date must not be provided when schedule span is 'Forever'")

        elif self.schedule_type == "SpecificDates":
            if not self.specific_dates:
                raise ValueError("At least one specific date must be provided when schedule type is 'SpecificDates'")
            if self.schedule_span != "CustomDates":
                raise ValueError("Schedule span must be 'CustomDates' when schedule type is 'SpecificDates'")
            if not self.begin_date or not self.end_date:
                raise ValueError("Begin date and end date are required when schedule type is 'SpecificDates'")
            # Ensure specific_dates list is not empty
            if len(self.specific_dates) == 0:
                raise ValueError("At least one specific date must be provided")
        return self

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "name": "Morning Lights On",
                "schedule_group_id": 1,
                "new_schedule_group_name": "",
                "schedule_type": "DayOfWeek",
                "schedule_span": "Forever",
                "days": {"Monday": True, "Tuesday": True},
                "specific_dates": [
                    {"Year": 2025, "Month": 7, "Day": 26},
                    {"Year": 2025, "Month": 8, "Day": 15}
                ],
                "begin_date": {"Year": 2025, "Month": 7, "Day": 14},
                "end_date": {"Year": 2025, "Month": 8, "Day": 14},
                "time_of_day": {"Hour": 7, "Minute": 30, "Second": 0},
                "areas": [
                    {
                        "area_id": 1,
                        "actions": [
                            {"type": "set_scene", "scene_code": 1, "scene_name": "presentation"},
                            {
                                "type": "zone_status",
                                "zone_id": 3,
                                "zone_name": "A",
                                "zone_type": "dimmed",
                                "zone_status": "On",
                                "zone_brightness": "75%",
                                "zone_temperature": "4000K"
                            },
                            {"type": "occupancy", "occupancy_setting": "auto"},
                            {
                                "type": "shade_group_status",
                                "shade_group_id": 5,
                                "shade_group_name": "pantry side",
                                "shade_level": "50%"
                            },
                            {
                                "type": "area_status",
                                "area_status": "On"
                            }
                        ]
                    }
                ]
            }
        }


class ScheduleUpdate(ScheduleCreate):
    pass


class ScheduleResponse(ScheduleCreate):
    id: int

    class Config:
        from_attributes = True


class SuccessResponse(BaseModel):
    status: str


class TriggerRequest(BaseModel):
    schedule_type: Literal["pre_configure", "internal"]
    timeclock_id: Optional[int] = None
    schedule_id: Optional[int] = None

    @model_validator(mode="after")
    def validate_fields(self):
        if self.schedule_type == "pre_configure" and not self.timeclock_id:
            raise ValueError("timeclock_id is required for pre_configure schedule_type")
        if self.schedule_type == "internal" and not self.schedule_id:
            raise ValueError("schedule_id is required for internal schedule_type")
        return self


class ScheduleGroupsResponse(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


class ScheduleGroupsListResponse(BaseModel):
    status: str
    groups: List[ScheduleGroupsResponse]
