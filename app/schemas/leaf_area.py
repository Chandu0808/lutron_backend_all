from pydantic import BaseModel

class LeafAreaOut(BaseModel):
    id: int
    name: str
    code: str
    floor_id: int
    processor_id: int

    class Config:
        from_attributes = True
