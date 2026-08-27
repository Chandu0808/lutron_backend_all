"""Typed exceptions for the Monitoring Storage layer."""

from __future__ import annotations


class StorageError(Exception):
    """Base error for monitoring persistence failures."""


class StorageNotFoundError(StorageError):
    """Requested monitoring row was not found."""


class StorageConflictError(StorageError):
    """Insert/upsert violated a uniqueness or integrity constraint."""


class StorageTransientError(StorageError):
    """Transient database failure that may be retried by the caller."""
