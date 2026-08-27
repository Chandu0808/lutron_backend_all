"""Pydantic schemas for Monitoring API (Phase 5)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MonitoringIngestRequest(BaseModel):
    """
    Single-event ingest body.

    For batches, clients may also send ``{\"events\": [ ... ]}`` which is
    accepted as a raw dict by the route (see route docs).
    """

    model_config = ConfigDict(extra="allow")

    event_type: str = Field(..., description="heartbeat|connectivity|leap_ping|job_run|http_aggregate|metric|lifecycle")
    component_code: Optional[str] = None
    status: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    observed_at: Optional[str] = None
    processor_id: Optional[int] = None
    job_key: Optional[str] = None
    outcome: Optional[str] = None
    metric_key: Optional[str] = None
    value: Optional[float] = None
    success: Optional[bool] = None
    rtt_ms: Optional[int] = None
    lifecycle_event_type: Optional[str] = None
    lifecycle_type: Optional[str] = None
    connectivity_event_type: Optional[str] = None
    fingerprint: Optional[str] = None


class MonitoringIngestResultItem(BaseModel):
    accepted: bool
    dropped: bool = False
    rejected: bool = False
    reason: Optional[str] = None
    event_type: Optional[str] = None


class MonitoringIngestResponse(BaseModel):
    ok: bool
    results: List[MonitoringIngestResultItem]
    accepted_count: int
    dropped_count: int
    rejected_count: int
