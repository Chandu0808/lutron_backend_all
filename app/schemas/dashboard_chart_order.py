from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DashboardChartOrderResponse(BaseModel):
    energy_slot_order: Optional[List[str]] = None
    space_charts_tab_order: Optional[List[str]] = None
    space_main_tab_order: Optional[List[str]] = None
    # Basic resize maps (widget_key -> column span)
    energy_slot_span: Optional[Dict[str, Any]] = None
    space_charts_tab_span: Optional[Dict[str, Any]] = None
    space_main_tab_span: Optional[Dict[str, Any]] = None
    # Full layout blobs for Advanced / Customized (order + span per page)
    advanced_dashboard_order: Optional[Dict[str, Any]] = None
    customized_dashboard_order: Optional[Dict[str, Any]] = None


class DashboardChartOrderUpdate(BaseModel):
    energy_slot_order: Optional[List[str]] = Field(default=None)
    space_charts_tab_order: Optional[List[str]] = Field(default=None)
    space_main_tab_order: Optional[List[str]] = Field(default=None)
    energy_slot_span: Optional[Dict[str, Any]] = Field(default=None)
    space_charts_tab_span: Optional[Dict[str, Any]] = Field(default=None)
    space_main_tab_span: Optional[Dict[str, Any]] = Field(default=None)
    advanced_dashboard_order: Optional[Dict[str, Any]] = Field(default=None)
    customized_dashboard_order: Optional[Dict[str, Any]] = Field(default=None)
