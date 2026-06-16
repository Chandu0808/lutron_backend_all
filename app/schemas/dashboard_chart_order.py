from typing import List, Optional

from pydantic import BaseModel, Field


class DashboardChartOrderResponse(BaseModel):
    energy_slot_order: Optional[List[str]] = None
    space_charts_tab_order: Optional[List[str]] = None
    space_main_tab_order: Optional[List[str]] = None


class DashboardChartOrderUpdate(BaseModel):
    energy_slot_order: Optional[List[str]] = Field(default=None)
    space_charts_tab_order: Optional[List[str]] = Field(default=None)
    space_main_tab_order: Optional[List[str]] = Field(default=None)
