from pydantic import BaseModel
from typing import List, Optional

class Point(BaseModel):
    x: float
    y: float

class ProcessorAreaMap(BaseModel):
    processor_id: int
    area_ids: List[int]


class ReferenceLengthInput(BaseModel):
    first_point: Point
    second_point: Point
    length_in_feet: Optional[float] = None
    length_in_meters: Optional[float] = None
    floor_id: int
