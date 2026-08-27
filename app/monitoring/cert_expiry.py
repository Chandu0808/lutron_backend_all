"""
Emit days-until-expiry for LEAP client certificates.

Best-effort: failures never raise into the watchdog.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from app.monitoring import instrumentation

logger = logging.getLogger("lutron_monitoring.cert_expiry")

METRIC_KEY = "certs.days_to_expiry"
_CERT_GLOB = "**/leap_signed_csr.pem"
_DEFAULT_CERT_ROOT = Path("app/certificates")


def days_until_pem_expiry(path: Path, *, now: Optional[datetime] = None) -> Optional[float]:
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
    except Exception:
        return None
    try:
        pem = path.read_bytes()
        cert = x509.load_pem_x509_certificate(pem, default_backend())
        expiry = getattr(cert, "not_valid_after_utc", None)
        if expiry is None:
            expiry = cert.not_valid_after
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (expiry - now).total_seconds() / 86400.0
    except Exception as exc:
        logger.warning("[monitoring][certs] parse failed path=%s: %s", path, exc)
        return None


def scan_cert_days(
    root: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> List[Tuple[str, float]]:
    base = root or _DEFAULT_CERT_ROOT
    if not base.exists():
        return []
    out: List[Tuple[str, float]] = []
    for pem in sorted(base.glob(_CERT_GLOB)):
        days = days_until_pem_expiry(pem, now=now)
        if days is None:
            continue
        out.append((str(pem), days))
    return out


def emit_cert_expiry_metrics(*, root: Optional[Path] = None) -> Optional[float]:
    """
    Emit the minimum days-to-expiry across discovered LEAP certs.
    Returns the emitted value, or None if nothing was emitted.
    """
    try:
        rows = scan_cert_days(root)
        if not rows:
            return None
        min_days = min(days for _, days in rows)
        instrumentation.metric(
            METRIC_KEY,
            float(min_days),
            component_code="certificates",
            detail={"cert_count": len(rows)},
        )
        return float(min_days)
    except Exception as exc:
        logger.warning("[monitoring][certs] emit failed: %s", exc)
        return None
