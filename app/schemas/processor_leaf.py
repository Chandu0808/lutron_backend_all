# app/schemas/processor_leaf.py

from pydantic import BaseModel

class LeafArea(BaseModel):
    code: str
    name: str
