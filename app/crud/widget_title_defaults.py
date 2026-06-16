"""Canonical default widget title labels (shared by API adapter and startup seeding)."""

from typing import Dict, List

TITLE_DEFAULTS: Dict[str, str] = {
    "savings_by_strategy": "Savings by Strategy",
    "consumption_by_area_groups": "Consumption By Area Groups",
    "light_power_density": "Light Power Density",
    "consumption": "Consumption",
    "savings": "Savings",
    "peak_and_minimum_consumption": "Peak And Minimum Consumption",
    "utilization": "Utilization",
    "instant_occupancy_count": "Occupancy",
    "utilization_by_area_group": "Utilization By Area Group",
    "utilization_by_area": "Utilization By Area",
    "peak_and_minimum_utilization": "Peak And Minimum Utilization",
}

DROPDOWN_DEFAULTS: Dict[str, str] = {
    "savings_by_strategy": "savings by strategy",
    "consumption_by_area_groups": "consumption by area groups",
    "light_power_density": "light power density",
    "consumption": "Consumption",
    "savings": "Savings",
    "peak_and_minimum_consumption": "Peak/Min Consumption",
    "utilization": "Utilization",
    "instant_occupancy_count": "Occupancy",
    "utilization_by_area_group": "utilization by area group",
    "utilization_by_area": "utilization by area",
    "peak_and_minimum_utilization": "Peak/Min Utilization",
}

KNOWN_WIDGET_KEYS: List[str] = list(TITLE_DEFAULTS.keys())
