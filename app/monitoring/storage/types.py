"""DTO / row types for Monitoring Storage (no ORM models)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID


@dataclass(frozen=True)
class ComponentRow:
    id: UUID
    code: str
    kind: str
    display_name: str
    is_active: bool


@dataclass(frozen=True)
class HealthCurrentRow:
    component_id: UUID
    status: str
    last_heartbeat_at: Optional[datetime]
    detail_json: Dict[str, Any]
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class ConnectivityCurrentRow:
    processor_id: int
    observer_component_id: Optional[UUID]
    status: str
    last_ok_at: Optional[datetime]
    last_error_at: Optional[datetime]
    detail_json: Dict[str, Any]
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class LeapPingRow:
    id: int
    processor_id: int
    observer_component_id: Optional[UUID]
    sampled_at: datetime
    rtt_ms: Optional[int]
    success: bool
    detail_json: Dict[str, Any]


@dataclass(frozen=True)
class EventRow:
    id: int
    event_at: datetime
    event_type: str
    severity: str
    component_id: Optional[UUID]
    processor_id: Optional[int]
    fingerprint: Optional[str]
    payload_json: Dict[str, Any]


@dataclass(frozen=True)
class JobDefinitionRow:
    id: UUID
    job_key: str
    component_id: UUID
    display_name: str
    is_active: bool


@dataclass(frozen=True)
class JobRunRow:
    id: int
    job_definition_id: UUID
    started_at: datetime
    finished_at: Optional[datetime]
    outcome: str
    duration_ms: Optional[int]
    error_class: Optional[str]
    error_message: Optional[str]
    host_pid: Optional[int]
    trigger_source: Optional[str]
    detail_json: Dict[str, Any]


@dataclass(frozen=True)
class HttpAggRow:
    id: int
    component_id: UUID
    bucket_start: datetime
    route_template: str
    method: str
    status_class: str
    request_count: int
    error_count: int
    sum_duration_ms: int
    max_duration_ms: int


@dataclass(frozen=True)
class MetricDefinitionRow:
    id: UUID
    metric_key: str
    value_type: str
    description: Optional[str]
    is_active: bool


@dataclass(frozen=True)
class MetricSampleRow:
    id: int
    metric_definition_id: UUID
    sampled_at: datetime
    value: float
    component_id: Optional[UUID]
    processor_id: Optional[int]


@dataclass(frozen=True)
class MetricRollupRow:
    id: int
    metric_definition_id: UUID
    bucket_start: datetime
    bucket_size: str
    sample_count: int
    sum_value: float
    avg_value: Optional[float]
    min_value: Optional[float]
    max_value: Optional[float]
    component_id: Optional[UUID]
    processor_id: Optional[int]


@dataclass(frozen=True)
class AlertRuleRow:
    id: UUID
    code: str
    display_name: str
    severity: str
    rule_type: str
    enabled: bool
    config_json: Dict[str, Any]
    description: Optional[str]


@dataclass(frozen=True)
class AlertInstanceRow:
    id: int
    rule_id: UUID
    status: str
    severity: str
    fingerprint: str
    title: str
    message: Optional[str]
    opened_at: datetime
    acknowledged_at: Optional[datetime]
    resolved_at: Optional[datetime]
    acknowledged_by_user_id: Optional[int]
    component_id: Optional[UUID]
    processor_id: Optional[int]
    job_definition_id: Optional[UUID]
    detail_json: Dict[str, Any] = field(default_factory=dict)
