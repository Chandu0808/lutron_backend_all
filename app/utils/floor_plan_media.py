"""Floor plan media helpers — auth-gated client URLs vs on-disk storage paths."""

from __future__ import annotations

import mimetypes
import os
from typing import Optional, Tuple

from fastapi import HTTPException


FLOOR_PLANS_DIR = os.path.abspath(os.path.join("app", "floor_plans"))


def floor_plan_client_url(floor_id: int) -> str:
    """URL clients must use (requires Bearer). Prefix matches api_router /floor."""
    return f"/floor/{int(floor_id)}/plan"


def resolve_floor_plan_disk_path(image_path: Optional[str]) -> str:
    """
    Map DB image_path (e.g. /floor_plans/foo.pdf) to an absolute file under app/floor_plans.
    Rejects path traversal.
    """
    if not image_path or not str(image_path).strip():
        raise HTTPException(status_code=404, detail="Floor plan file not configured")

    raw = str(image_path).strip().replace("\\", "/")
    prefix = "/floor_plans/"
    if raw.startswith(prefix):
        filename = raw[len(prefix) :]
    elif raw.startswith("floor_plans/"):
        filename = raw[len("floor_plans/") :]
    else:
        filename = raw.lstrip("/")

    filename = os.path.basename(filename)
    if not filename or filename in (".", ".."):
        raise HTTPException(status_code=404, detail="Floor plan file not found")

    full = os.path.abspath(os.path.join(FLOOR_PLANS_DIR, filename))
    if not full.startswith(FLOOR_PLANS_DIR + os.sep) and full != FLOOR_PLANS_DIR:
        raise HTTPException(status_code=404, detail="Floor plan file not found")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Floor plan file not found")
    return full


def floor_plan_media_type(disk_path: str) -> str:
    guessed, _ = mimetypes.guess_type(disk_path)
    if guessed:
        return guessed
    if disk_path.lower().endswith(".pdf"):
        return "application/pdf"
    return "application/octet-stream"


def floor_plan_file_response_parts(image_path: Optional[str]) -> Tuple[str, str, str]:
    disk_path = resolve_floor_plan_disk_path(image_path)
    media_type = floor_plan_media_type(disk_path)
    filename = os.path.basename(disk_path)
    return disk_path, media_type, filename
