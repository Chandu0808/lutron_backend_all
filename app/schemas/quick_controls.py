from pydantic import BaseModel, Field, model_validator
from typing import List, Optional,Literal
from app.models.quick_controls import CreationMode
class QuickControlAreaActionSchema(BaseModel):
    type: str

    # Set Scenes
    # set code

    scene_code: Optional[int] = None
    scene_name: Optional[str] = None


    # Zone Status
    zone_id: Optional[int] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    zone_status: Optional[str] = None
    zone_brightness: Optional[str] = None
    zone_temperature: Optional[str] = None

    # Occupancy
    occupancy_setting: Optional[str] = None

    # Shade Group
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
        from_attributes = True
        populate_by_name = True


class QuickControlAreaSchema(BaseModel):
    area_id: Optional[int]  # allow null
    actions: List[QuickControlAreaActionSchema]

    class Config:
        from_attributes = True


class QuickControlCreate(BaseModel):
    name: str
    areas: List[QuickControlAreaSchema]

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "name": "Night Mode",
                "areas": [
                    {
                        "area_id": 1,
                        "actions": [
                            {
                                "type": "set_scene",
                                "scene_code": "1",
                                "scene_name": "presentation"
                            },
                            {
                                "type": "zone_status",
                                "zone_id": 3,
                                "zone_name": "glass wall",
                                "zone_type": "dimmed",
                                "zone_status": "On",
                                "zone_brightness": "75%",
                                "zone_temperature": "4000K"
                            },
                            {
                                "type": "occupancy",
                                "occupancy_setting": "auto"
                            },
                            {
                                "type": "shade_group_status",
                                "shade_group_id": 5,
                                "shade_group_name":"pantry side",
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
    }

class SuccessResponse(BaseModel):
    status: str
    message: Optional[str] = None
    id: Optional[int] = None



class QuickControlUpdate(QuickControlCreate):
    """Used for update requests — same structure as creation."""


class QuickControlResponse(BaseModel):
    id: int
    name: str
    creation_mode: CreationMode | None=None


    model_config = {
        "from_attributes": True
    }


class QuickControlDetailResponse(BaseModel):
    name: str
    areas: List[QuickControlAreaSchema] = Field(..., alias="quick_control_areas")

    class Config:
        from_attributes = True