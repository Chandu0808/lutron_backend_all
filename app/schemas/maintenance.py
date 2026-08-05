from typing import List, Literal

from pydantic import BaseModel, field_validator

MaintenanceDeviceType = Literal[
    "devices",
    "keypad",
    "sensors",
    "drivers",
    "others",
    "awn_rf",
    "awn_occ",
    "occupancy_mode",
]


class MaintenanceReportRequest(BaseModel):
    types: List[MaintenanceDeviceType]

    @field_validator("types")
    @classmethod
    def types_not_empty(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("At least one type is required")
        return list(dict.fromkeys(value))
