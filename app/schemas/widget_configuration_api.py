from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class WidgetConfigurationItem(BaseModel):
    id: int
    widget_key: str
    display_name: str
    dropdown_name: Optional[str] = None
    is_visible: bool
    sort_order: Optional[int] = None
    config: Optional[Dict[str, Any]] = None
    updated_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class WidgetConfigurationListResponse(BaseModel):
    items: List[WidgetConfigurationItem]


class WidgetConfigurationUpsert(BaseModel):
    widget_key: str = Field(..., min_length=1, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=128)
    dropdown_name: Optional[str] = Field(default=None, max_length=128)
    is_visible: Optional[bool] = None
    sort_order: Optional[int] = None
    config: Optional[Dict[str, Any]] = None
