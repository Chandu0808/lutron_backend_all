"""Pydantic schemas for Monitoring Dashboard API (Phase 12 + Phase 2 contracts)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MonitoringSummaryResponse(BaseModel):
    monitoring_enabled: bool
    pipeline: Optional[Dict[str, Any]] = None
    component_counts: Dict[str, int] = Field(default_factory=dict)
    active_alert_count: int = 0
    processor_status_summary: Dict[str, int] = Field(default_factory=dict)
    job_summary: Dict[str, Any] = Field(default_factory=dict)
    latest_analytics_timestamp: Optional[str] = None


class AlertAcknowledgeResponse(BaseModel):
    id: int
    status: str
    acknowledged_at: Optional[str] = None
    acknowledged_by_user_id: Optional[int] = None


class PaginatedAlertsResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int
    limit: int
    offset: int


# ----- Phase 2 product contracts (read models) -----


class MonitoringStatusResponse(BaseModel):
    """Effective monitoring enablement / runtime snapshot (Phase 7)."""

    enabled: bool = Field(description="Effective monitoring enabled state")
    monitoring_enabled: bool = Field(
        description="Backward-compatible alias of enabled"
    )
    source: str = Field(
        description="environment | installation_settings"
    )
    writable: bool = Field(
        description="True when Superadmin can PATCH the installation setting"
    )
    environment_enabled: bool = Field(
        description="Raw MONITORING_ENABLED environment flag"
    )
    configured_enabled: Optional[bool] = Field(
        default=None,
        description="Persisted installation setting (null if settings unavailable)",
    )
    configured_defaulted: bool = Field(
        default=False,
        description="True when configured_enabled used the absent-setting default",
    )
    settings_available: bool = Field(
        default=True,
        description="False when installation_settings could not be read",
    )
    flags: Dict[str, bool] = Field(default_factory=dict)
    service: Dict[str, Any] = Field(default_factory=dict)


class MonitoringStatusUpdateRequest(BaseModel):
    enabled: bool


class PaginatedIssuesResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int
    limit: int
    offset: int


class MonitoringResourcesResponse(BaseModel):
    """
    Resource usage contract (Phase 6).

    Live process RSS via Supervisor PIDs + psutil. No historical samples.
    """

    available: bool = False
    reason: Optional[str] = None
    host: Optional[Dict[str, Any]] = None
    processes: List[Dict[str, Any]] = Field(default_factory=list)
