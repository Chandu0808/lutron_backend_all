from pydantic import BaseModel, Field


class AreaRenameRequest(BaseModel):
    area_id: int = Field(..., ge=1)
    new_name: str = Field(..., min_length=1, max_length=512)
