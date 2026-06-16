from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, RootModel


class InstallationSettingsUpdate(BaseModel):
    """Partial installation settings POST body (arbitrary top-level keys)."""

    model_config = ConfigDict(extra="allow")

    def to_updates(self) -> Dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class InstallationSettingsResponse(RootModel[Dict[str, Any]]):
    """Flat map of setting_key -> setting_value."""
