from pydantic import BaseModel
from typing import Optional

class ProcessorBase(BaseModel):
    server: str
    ipv4: str
    system: str
    serial: str
    mac: str
    claimed: str
    sw_version: str
    status:str

    class Config:
        from_attributes = True

class ProcessorOut(ProcessorBase):
    id: int
    server: str
