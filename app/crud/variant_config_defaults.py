from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Tuple

VALID_VARIANTS: Tuple[str, ...] = ("basic", "advanced", "customized")

DEFAULT_BACKGROUND_IMAGE = "/background_image/defaultBg.png"

WIDGET_TITLES: Dict[str, str] = {
    "overview_page": "Dashboard Overview",
    "energy": "Energy",
    "alerts": "Alerts",
    "schedules": "Schedules",
    "quick_controls": "Quick Controls",
    "shades": "External Link",
    "space_utilization": "Space Utilization",
    "savings_by_strategy": "Savings by Strategy",
    "total_consumption_by_group": "Consumption by area groups",
    "consumption_saving": "Energy (Combined)",
    "light_power_density": "Light Power Density",
    "consumption": "Consumption",
    "savings": "Savings",
    "peak_and_minimum_consumption": "Peak & Minimum Consumption",
    "utilization": "Utilization",
    "utilization_by_area_group": "Utilization By Area Group",
    "utilization_by_area": "Utilization By Area",
    "peak_and_minimum_utilization": "Peak And Minimum Utilization",
    "instant_occupancy_count": "Occupancy",
    "instant_utilization_combined": "Space Utilization (Combined)",
}

WIDGET_ORDER: Tuple[str, ...] = (
    "overview_page",
    "energy",
    "alerts",
    "schedules",
    "quick_controls",
    "shades",
    "space_utilization",
    "savings_by_strategy",
    "total_consumption_by_group",
    "consumption_saving",
    "light_power_density",
    "consumption",
    "savings",
    "peak_and_minimum_consumption",
    "utilization",
    "utilization_by_area_group",
    "utilization_by_area",
    "peak_and_minimum_utilization",
    "instant_occupancy_count",
    "instant_utilization_combined",
)

_BASIC_VISIBLE = {
    "overview_page",
    "energy",
    "alerts",
    "schedules",
    "quick_controls",
    "shades",
    "space_utilization",
    "consumption_saving",
    "instant_utilization_combined",
}

_ADVANCED_VISIBLE = {
    "savings_by_strategy",
    "total_consumption_by_group",
    "light_power_density",
    "consumption",
    "savings",
    "peak_and_minimum_consumption",
    "utilization",
    "utilization_by_area_group",
    "utilization_by_area",
    "peak_and_minimum_utilization",
    "instant_occupancy_count",
}

# Customized matches Advanced: overview off; graphs on; Combined charts off.
_CUSTOMIZED_VISIBLE = set(_ADVANCED_VISIBLE)

DEFAULT_VISIBLE_WIDGETS_BY_VARIANT = {
    "basic": _BASIC_VISIBLE,
    "advanced": _ADVANCED_VISIBLE,
    "customized": _CUSTOMIZED_VISIBLE,
}

DEFAULT_LAYOUTS: Dict[str, Dict[str, Any]] = {
    "basic": {
        "energy_slot_order": [
            "consumption",
            "consumption_saving",
            "savings",
            "savings_by_strategy",
            "total_consumption_by_group",
            "light_power_density",
            "peak_and_minimum_consumption",
        ],
        "space_charts_tab_order": [
            "instant_occupancy_count",
            "instant_utilization_combined",
            "utilization_by_area_group",
            "utilization_by_area",
            "peak_and_minimum_utilization",
        ],
        "space_main_tab_order": [
            "utilization",
            "utilization_by_area_group",
            "peak_and_minimum_utilization",
            "utilization_by_area",
        ],
        "overview_order": [
            "energy",
            "alerts",
            "schedules",
            "quick_controls",
            "shades",
            "space_utilization",
        ],
    },
    "advanced": {
        "energy_slot_order": [
            "savings_by_strategy",
            "total_consumption_by_group",
            "light_power_density",
            "consumption",
            "savings",
            "peak_and_minimum_consumption",
            "consumption_saving",
        ],
        "space_charts_tab_order": [
            "instant_occupancy_count",
            "utilization_by_area_group",
            "utilization_by_area",
            "peak_and_minimum_utilization",
            "instant_utilization_combined",
        ],
        "space_main_tab_order": [
            "utilization",
            "utilization_by_area_group",
            "peak_and_minimum_utilization",
            "utilization_by_area",
        ],
        "overview_order": [
            "energy",
            "alerts",
            "schedules",
            "quick_controls",
            "shades",
            "space_utilization",
        ],
    },
    "customized": {
        # Same layout defaults as Advanced (overview off / Combined charts last).
        "energy_slot_order": [
            "savings_by_strategy",
            "total_consumption_by_group",
            "light_power_density",
            "consumption",
            "savings",
            "peak_and_minimum_consumption",
            "consumption_saving",
        ],
        "space_charts_tab_order": [
            "instant_occupancy_count",
            "utilization_by_area_group",
            "utilization_by_area",
            "peak_and_minimum_utilization",
            "instant_utilization_combined",
        ],
        "space_main_tab_order": [
            "utilization",
            "utilization_by_area_group",
            "peak_and_minimum_utilization",
            "utilization_by_area",
        ],
        "overview_order": [
            "energy",
            "alerts",
            "schedules",
            "quick_controls",
            "shades",
            "space_utilization",
        ],
    },
}

DEFAULT_THEME_SETTINGS: Dict[str, Dict[Tuple[str, str], Any]] = {
    "basic": {
        ("application", "background"): "#ffffff",
        ("application", "content"): "#f5f5f5",
        ("application", "button"): "#1565C0",
        ("heatmap", "light"): "#F2FF00",
        ("heatmap", "occupancy"): "#4318D1",
        ("heatmap", "energy"): "#5D8C00",
        ("background", "image"): DEFAULT_BACKGROUND_IMAGE,
        ("shell", "preset_slug"): "basic",
        ("shell", "overview_locked"): True,
    },
    "advanced": {
        ("application", "background"): "#6f809d",
        ("application", "content"): "#3d4a5c",
        ("application", "button"): "#232323",
        ("heatmap", "light"): "#F2FF00",
        ("heatmap", "occupancy"): "#4318D1",
        ("heatmap", "energy"): "#5D8C00",
        ("background", "image"): DEFAULT_BACKGROUND_IMAGE,
        ("shell", "preset_slug"): "advanced",
        ("shell", "overview_locked"): False,
    },
    "customized": {
        ("application", "background"): "#CDC0A0",
        ("application", "content"): "#807864",
        ("application", "button"): "#232323",
        ("heatmap", "light"): "#F2FF00",
        ("heatmap", "occupancy"): "#4318D1",
        ("heatmap", "energy"): "#5D8C00",
        ("background", "image"): DEFAULT_BACKGROUND_IMAGE,
        ("shell", "preset_slug"): "customized",
        ("shell", "overview_locked"): False,
    },
}


def normalize_variant_slug(variant: str | None) -> str:
    raw = str(variant or "").strip().lower()
    if raw not in VALID_VARIANTS:
        return "basic"
    return raw


def iter_seed_widget_rows(variant: str) -> Iterable[Dict[str, Any]]:
    slug = normalize_variant_slug(variant)
    visible = DEFAULT_VISIBLE_WIDGETS_BY_VARIANT[slug]
    for index, widget_key in enumerate(WIDGET_ORDER):
        label = WIDGET_TITLES[widget_key]
        is_visible = widget_key in visible
        yield {
            "variant": slug,
            "widget_key": widget_key,
            "display_name": label,
            "dropdown_name": label,
            "is_available": True,
            "default_visible": is_visible,
            "is_visible": is_visible,
            "sort_order": index,
            "config": {"seeded": True},
        }


def get_default_layouts(variant: str) -> Dict[str, Any]:
    slug = normalize_variant_slug(variant)
    return deepcopy(DEFAULT_LAYOUTS[slug])


def iter_seed_theme_rows(variant: str) -> Iterable[Dict[str, Any]]:
    slug = normalize_variant_slug(variant)
    for (config_group, config_key), config_value in DEFAULT_THEME_SETTINGS[slug].items():
        yield {
            "variant": slug,
            "config_group": config_group,
            "config_key": config_key,
            "config_value": deepcopy(config_value),
        }


def get_all_default_variants() -> List[str]:
    return list(VALID_VARIANTS)
