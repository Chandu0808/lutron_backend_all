from typing import List, Optional
from pydantic import BaseModel

class AreaGroupCreate(BaseModel):
    name: str
    special: bool = False 

class AreaGroupMappingCreate(BaseModel):
    floor_id: int
    area_ids: List[int]  # Changed from area_codes to area_ids

class AreaGroupArea(BaseModel):
    area_id: int
    floor_id: int
    name: Optional[str] = None

class AreaGroupOut(BaseModel):
    group_id: int
    name: str
    special: bool  
    areas: List[AreaGroupArea]


class FloorAreaMapping(BaseModel):
    floor_id: int
    area_ids: List[int]  # Changed from area_codes to area_ids

class AreaGroupUpdateRequest(BaseModel):
    name: str
    special: bool = False
    floors: List[FloorAreaMapping]

class CombinedAreaGroupCreate(BaseModel):
    name: str
    special: bool = False 
    floors: List[FloorAreaMapping]

class AreaGroupListOut(BaseModel):
    special_area_groups: List[AreaGroupOut]
    user_area_groups: List[AreaGroupOut]
