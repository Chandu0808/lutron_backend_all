from typing import List
from pydantic import BaseModel, conint, confloat,field_validator, model_validator
from typing import Literal,Optional
from enum import Enum


# In schemas/floor.py

class AreaOut(BaseModel):
    area_id: int
    name: str  # Add this if returned

    class Config:
        from_attributes = True

class ProcessorFloorOut(BaseModel):
    processor_id: int
    server: Optional[str] = ""
    areas: List[AreaOut]

    class Config:
        from_attributes = True

class FloorListOut(BaseModel):
    id: int
    floor_name: str
    floor_image: Optional[str] = ""
    processors: List[ProcessorFloorOut]  # Use renamed model
    sort_order: Optional[int] = None

    class Config:
        from_attributes = True


class FloorListResponse(BaseModel):
    manual_sort_enabled: bool = False
    floors: List[FloorListOut]


class FloorSortSettingsUpdate(BaseModel):
    manual_sort_enabled: bool


class FloorSortSettingsResponse(BaseModel):
    manual_sort_enabled: bool
    floors: List[FloorListOut]


class FloorReorderRequest(BaseModel):
    floor_ids: List[int]


class FloorReorderResponse(BaseModel):
    status: str = "success"
    floors: List[FloorListOut]
    manual_sort_enabled: bool = True


class Operation(str, Enum):
    move_x = "move_x"
    move_y = "move_y"
    scale_x = "scale_x"
    scale_y = "scale_y"

class Unit(str, Enum):
    pixels = "pixels"
    percentage = "percentage"

class ModifyCoordinatesRequest(BaseModel):
    floor_id: conint(gt=0)
    operation: Operation

    # for move_x/move_y
    move_by: Optional[Unit] = None
    move_value: Optional[confloat(allow_inf_nan=False)] = None

    # for scale_x/scale_y
    scale_factor: Optional[confloat(gt=0, allow_inf_nan=False)] = None

    @model_validator(mode="after")
    def validate_combinations(self):
        if self.operation in (Operation.move_x, Operation.move_y):
            if self.move_by is None or self.move_value is None:
                raise ValueError("For move_x/move_y, provide both move_by and move_value.")
            if self.scale_factor is not None:
                raise ValueError("scale_factor must not be provided for move_x/move_y.")
        else:
            # scale_x / scale_y
            if self.scale_factor is None:
                raise ValueError("For scale_x/scale_y, provide scale_factor.")
            if self.move_by is not None or self.move_value is not None:
                raise ValueError("move_by/move_value must not be provided for scale_x/scale_y.")
        return self

class ModifyCoordinatesResponse(BaseModel):
    status: Literal["success"]
    floor_id: int
    operation: Operation
    affected_coordinates: int
    details: dict
