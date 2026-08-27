"""Shared helpers for monitoring Storage repositories."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.monitoring.storage.exceptions import (
    StorageConflictError,
    StorageError,
    StorageTransientError,
)


def as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def as_optional_uuid(value: Any) -> Optional[UUID]:
    if value is None:
        return None
    return as_uuid(value)


def as_json_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else {}
    # psycopg2 may return mapped objects
    return dict(value)


def mapping_row(result) -> Optional[Mapping[str, Any]]:
    row = result.mappings().first()
    return row


def mapping_rows(result):
    return list(result.mappings().all())


def run_write(session: Session, fn):
    """Execute write callable; translate integrity/transient errors."""
    try:
        return fn()
    except IntegrityError as exc:
        raise StorageConflictError(str(exc.orig if hasattr(exc, "orig") else exc)) from exc
    except OperationalError as exc:
        raise StorageTransientError(str(exc)) from exc
    except StorageError:
        raise
    except Exception as exc:
        raise StorageError(str(exc)) from exc
