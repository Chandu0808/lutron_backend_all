"""Shared-secret auth for Monitoring ingest (Phase 5)."""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException, status


INGEST_TOKEN_HEADER = "X-Monitoring-Ingest-Token"
INGEST_TOKEN_ENV = "MONITORING_INGEST_TOKEN"


def get_configured_ingest_token() -> Optional[str]:
    from app.installation_config import get_monitoring_ingest_token

    token = (get_monitoring_ingest_token() or "").strip()
    return token or None


def require_monitoring_ingest_token(
    x_monitoring_ingest_token: Optional[str] = Header(
        default=None, alias=INGEST_TOKEN_HEADER
    ),
    authorization: Optional[str] = Header(default=None),
) -> str:
    """
    Authenticate ingest callers with a shared secret.

    Accepts either:
    - ``X-Monitoring-Ingest-Token: <secret>``
    - ``Authorization: Bearer <secret>`` (ingest secret, not LMS JWT)
    """
    expected = get_configured_ingest_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitoring ingest token is not configured",
        )

    provided = (x_monitoring_ingest_token or "").strip()
    if not provided and authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided = parts[1].strip()

    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid monitoring ingest token",
        )
    return provided
