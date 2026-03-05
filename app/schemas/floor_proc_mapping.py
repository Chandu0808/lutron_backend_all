from pydantic import BaseModel

class FloorProcMapCreate(BaseModel):
    floor_id: int
    processor_id: int

class FloorProcMapOut(BaseModel):
    id: int
    floor_id: int
    processor_id: int

    class Config:
        from_attributes = True
