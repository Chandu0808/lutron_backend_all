"""Session helpers for monitoring Storage (uses LMS SessionLocal)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.monitoring.storage.exceptions import StorageError, StorageTransientError


@contextmanager
def monitoring_session(*, commit: bool = True) -> Iterator[Session]:
    """
    Short-lived session for monitoring Storage operations.

    Commits on success when ``commit`` is True; always closes the session.
    """
    session = SessionLocal()
    try:
        yield session
        if commit:
            session.commit()
    except StorageError:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        # Callers may classify further; wrap unexpected DB issues.
        message = str(exc).lower()
        transient_markers = (
            "connection",
            "deadlock",
            "serialization",
            "could not connect",
            "server closed",
            "timeout",
        )
        if any(m in message for m in transient_markers):
            raise StorageTransientError(str(exc)) from exc
        raise StorageError(str(exc)) from exc
    finally:
        session.close()
