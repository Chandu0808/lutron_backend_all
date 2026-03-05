# app/utils/energy_unit_converter.py

from typing import Dict, Any, List, Optional, Union


def convert_energy_unit(value: float, unit: str = "Wh") -> Dict[str, Any]:
    """
    Convert energy values based on thresholds:
    - < 2000 Wh → Keep as Wh
    - >= 2000 & < 2,000,000 Wh → Convert to kWh
    - >= 2,000,000 Wh → Convert to MWh
    
    Args:
        value: Energy value in Wh
        unit: Original unit (default: "Wh")
    
    Returns:
        Dict containing converted value, unit, and display string
    """
    if value is None:
        return {"value": None, "unit": unit, "display": None}
    
    if value < 2000:
        return {"value": value, "unit": "Wh", "display": f"{value} Wh"}
    elif value < 2000000:
        converted_value = round(value / 1000, 2)
        return {"value": converted_value, "unit": "kWh", "display": f"{converted_value} kWh"}
    else:
        converted_value = round(value / 1000000, 2)
        return {"value": converted_value, "unit": "MWh", "display": f"{converted_value} MWh"}


def convert_energy_series(values: List[Optional[float]], unit: str = "Wh") -> Dict[str, Any]:
    """
    Convert a series of energy values and determine the appropriate unit.
    
    Args:
        values: List of energy values in Wh
        unit: Original unit (default: "Wh")
    
    Returns:
        Dict containing converted values, unit info, and display values
    """
    if not values:
        return {"values": [], "unit_info": {"unit": unit, "converted": False}, "display_values": []}
    
    # Find the maximum value to determine the appropriate unit
    max_value = max([v for v in values if v is not None], default=0)
    
    if max_value < 2000:
        # Keep as Wh
        converted_values = values
        unit_info = {"unit": "Wh", "converted": False}
        display_values = [f"{v} Wh" if v is not None else None for v in values]
    elif max_value < 2000000:
        # Convert to kWh
        converted_values = [round(v / 1000, 2) if v is not None else None for v in values]
        unit_info = {"unit": "kWh", "converted": True}
        display_values = [f"{v} kWh" if v is not None else None for v in converted_values]
    else:
        # Convert to MWh
        converted_values = [round(v / 1000000, 2) if v is not None else None for v in values]
        unit_info = {"unit": "MWh", "converted": True}
        display_values = [f"{v} MWh" if v is not None else None for v in converted_values]
    
    return {
        "values": converted_values,
        "unit_info": unit_info,
        "display_values": display_values
    }


def convert_energy_dict(data: Dict[str, List[Optional[float]]], unit: str = "Wh") -> Dict[str, Any]:
    """
    Convert energy values in a dictionary format (for y-axis data).
    
    Args:
        data: Dictionary with area names as keys and energy values as lists
        unit: Original unit (default: "Wh")
    
    Returns:
        Dict containing converted data and unit information
    """
    if not data:
        return {"data": {}, "unit": unit}
    
    # Find the maximum value across all series to determine unit
    all_values = []
    for values in data.values():
        all_values.extend([v for v in values if v is not None])
    
    max_value = max(all_values, default=0)
    
    converted_data = {}
    final_unit = unit
    
    if max_value < 2000:
        # Keep as Wh
        converted_data = data
        final_unit = "Wh"
    elif max_value < 2000000:
        # Convert to kWh
        for area_name, values in data.items():
            converted_data[area_name] = [round(v / 1000, 2) if v is not None else None for v in values]
        final_unit = "kWh"
    else:
        # Convert to MWh
        for area_name, values in data.items():
            converted_data[area_name] = [round(v / 1000000, 2) if v is not None else None for v in values]
        final_unit = "MWh"
    
    return {
        "data": converted_data,
        "unit": final_unit
    }


def convert_single_energy_value(value: Optional[float], unit: str = "Wh") -> Dict[str, Any]:
    """
    Convert a single energy value.
    
    Args:
        value: Energy value in Wh
        unit: Original unit (default: "Wh")
    
    Returns:
        Dict containing converted value, unit, and display string
    """
    if value is None:
        return {"value": None, "unit": unit, "display": None}
    
    return convert_energy_unit(value, unit)
