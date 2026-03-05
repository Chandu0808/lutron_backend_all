# app/schemas/widget_title.py
from pydantic import BaseModel, constr
from typing import Literal, List

WidgetKey = Literal[
    "savings_by_strategy",
    "consumption_by_area_groups",
    "light_power_density",
    "consumption",
    "savings",
    "peak_and_minimum_consumption",
    "utilization",
    "instant_occupancy_count",
    "utilization_by_area_group",
    "utilization_by_area",
    "peak_and_minimum_utilization"
]

class RenameWidgetRequest(BaseModel):
    widget_key: WidgetKey
    new_name: constr(strip_whitespace=True, min_length=1, max_length=128)

class RenameWidgetResponse(BaseModel):
    status: str
    widget_key: WidgetKey
    display_name: str

class WidgetTitleItem(BaseModel):
    key: WidgetKey
    title: str
    dropdown_name: str

class WidgetTitlesResponse(BaseModel):
    status: str
    titles: List[WidgetTitleItem]
