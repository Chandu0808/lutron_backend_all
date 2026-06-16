from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DashboardLayoutItem(BaseModel):
    id: int
    layout_key: str
    layout_json: Any
    layout_version: int
    updated_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class DashboardLayoutListResponse(BaseModel):
    items: List[DashboardLayoutItem]


class DashboardLayoutUpsert(BaseModel):
    layout_key: str = Field(..., min_length=1, max_length=64)
    layout_json: Any
    layout_version: int = Field(default=1, ge=1)
