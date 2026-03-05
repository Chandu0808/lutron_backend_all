from pydantic import BaseModel
from typing import List

class ProcessorInput(BaseModel):
    processor_id: int
    area_ids: List[int]

class AreaCSVRequest(BaseModel):
    processors: List[ProcessorInput]
