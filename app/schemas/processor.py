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


class ProcessorListAllOut(BaseModel):
    id: int
    ipv4: Optional[str] = None
    system: Optional[str] = None
    serial: Optional[str] = None
    handshake_status: Optional[bool] = None

    class Config:
        from_attributes = True
