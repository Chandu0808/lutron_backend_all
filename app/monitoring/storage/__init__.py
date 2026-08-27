"""Monitoring Storage facade — sole DB access to monitoring.*."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.monitoring.storage.alerts import AlertRepository
from app.monitoring.storage.components import ComponentRepository
from app.monitoring.storage.connectivity import ConnectivityRepository
from app.monitoring.storage.events import EventRepository
from app.monitoring.storage.health import HealthRepository
from app.monitoring.storage.http_agg import HttpAggRepository
from app.monitoring.storage.jobs import JobRepository
from app.monitoring.storage.metrics import MetricsRepository
from app.monitoring.storage.ping import PingRepository


class MonitoringStorage:
    """
    Public Storage API for Bootstrap / future Service / Alert / Analytics.

    All monitoring SQL must go through these repositories.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.components = ComponentRepository(session)
        self.health = HealthRepository(session)
        self.connectivity = ConnectivityRepository(session)
        self.ping = PingRepository(session)
        self.events = EventRepository(session)
        self.jobs = JobRepository(session)
        self.http_agg = HttpAggRepository(session)
        self.metrics = MetricsRepository(session)
        self.alerts = AlertRepository(session)


__all__ = [
    "MonitoringStorage",
    "ComponentRepository",
    "HealthRepository",
    "ConnectivityRepository",
    "PingRepository",
    "EventRepository",
    "JobRepository",
    "HttpAggRepository",
    "MetricsRepository",
    "AlertRepository",
]
