from pydantic import BaseModel
from typing import Literal

class UpdateOccupancyRequest(BaseModel):
    area_id: int
    occupancy_mode: Literal["Auto", "Vacancy", "Disabled"]
