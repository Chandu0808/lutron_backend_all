# routes/theme.py
import os
import random
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.crud.variant_runtime import resolve_variant
from app.crud.variant_theme_setting import (
    get_variant_theme_settings_map,
    upsert_variant_theme_setting,
)
from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.models.user_model import User

router = APIRouter()

_THEME_EDITOR_ROLES = frozenset({"Admin", "Superadmin"})


def require_theme_editor(current_user: User = Depends(get_current_user)) -> User:
    """Restrict theme mutations to Superadmin and Admin."""
    if current_user.role not in _THEME_EDITOR_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is restricted to Admin and Superadmin only.",
        )
    return current_user


APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPLOAD_DIR = os.path.join(APP_DIR, "background_image")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DEFAULT_BACKGROUND_IMAGE = "/background_image/defaultBg.png"


def _resolve_theme_map(db: Session, variant: Optional[str]) -> tuple[str, dict]:
    resolved_variant = resolve_variant(db, variant)
    data = get_variant_theme_settings_map(db, resolved_variant)
    return resolved_variant, data


def _resolve_background_image(base_url: str, data: dict) -> str:
    raw = data.get("background", {}).get("image") or DEFAULT_BACKGROUND_IMAGE
    if isinstance(raw, str) and raw.startswith("/"):
        return f"{base_url}{raw}"
    return str(raw)


@router.get("/")
def get_theme(
    request: Request,
    db: Session = Depends(get_db),
    variant: Optional[str] = Query(default=None),
):
    _resolved_variant, data = _resolve_theme_map(db, variant)
    base_url = str(request.base_url).rstrip("/")

    return {
        "status": "Success",
        "background_image": _resolve_background_image(base_url, data),
        "ui_theme_colors": {
            "background": data.get("application", {}).get("background", ""),
            "content": data.get("application", {}).get("content", ""),
            "button": data.get("application", {}).get("button", ""),
        },
        "heatmap_colors": {
            "light": data.get("heatmap", {}).get("light", ""),
            "occupancy": data.get("heatmap", {}).get("occupancy", ""),
            "energy": data.get("heatmap", {}).get("energy", ""),
        },
    }


@router.get("/background")
def get_background_image(
    request: Request,
    db: Session = Depends(get_db),
    variant: Optional[str] = Query(default=None),
):
    _resolved_variant, data = _resolve_theme_map(db, variant)
    base_url = str(request.base_url).rstrip("/")

    return {
        "status": "Success",
        "background_image": _resolve_background_image(base_url, data),
    }


@router.post("/background_image_clear")
def clear_background_image(
    request: Request,
    db: Session = Depends(get_db),
    _editor: User = Depends(require_theme_editor),
    variant: Optional[str] = Query(default=None),
):
    """Reset the application background image to the seeded default."""
    resolved_variant, data = _resolve_theme_map(db, variant)
    previous_value = data.get("background", {}).get("image")
    upsert_variant_theme_setting(
        db,
        resolved_variant,
        "background",
        "image",
        DEFAULT_BACKGROUND_IMAGE,
    )

    if previous_value and previous_value != DEFAULT_BACKGROUND_IMAGE:
        uploaded_name = os.path.basename(str(previous_value))
        if uploaded_name.startswith("bg_"):
            uploaded_path = os.path.join(UPLOAD_DIR, uploaded_name)
            if os.path.isfile(uploaded_path):
                try:
                    os.remove(uploaded_path)
                except OSError:
                    pass

    base_url = str(request.base_url).rstrip("/")
    return {
        "status": "Updated",
        "background_image": f"{base_url}{DEFAULT_BACKGROUND_IMAGE}",
    }


@router.post("/background")
async def update_background_image_with_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _editor: User = Depends(require_theme_editor),
    variant: Optional[str] = Query(default=None),
):
    try:
        ext = file.filename.split(".")[-1]
        unique_filename = f"bg_{random.randint(1000, 9999)}.{ext}"
        save_path = os.path.join(UPLOAD_DIR, unique_filename)
        with open(save_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        relative_path = f"/background_image/{unique_filename}"
        resolved_variant = resolve_variant(db, variant)
        upsert_variant_theme_setting(
            db,
            resolved_variant,
            "background",
            "image",
            relative_path,
        )
        return {"status": "Updated", "background_image": relative_path}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class ApplicationThemeUpdateRequest(BaseModel):
    background: Optional[str] = None
    content: Optional[str] = None
    button: Optional[str] = None


@router.get("/application")
def get_application_theme(
    db: Session = Depends(get_db),
    variant: Optional[str] = Query(default=None),
):
    _resolved_variant, data = _resolve_theme_map(db, variant)
    return {
        "status": "Success",
        "application_theme": {
            "background": data.get("application", {}).get("background", ""),
            "content": data.get("application", {}).get("content", ""),
            "button": data.get("application", {}).get("button", ""),
        },
    }


@router.post("/application")
def update_application_theme_bulk(
    update: ApplicationThemeUpdateRequest,
    db: Session = Depends(get_db),
    _editor: User = Depends(require_theme_editor),
    variant: Optional[str] = Query(default=None),
):
    resolved_variant = resolve_variant(db, variant)
    update_map = {
        "background": update.background,
        "content": update.content,
        "button": update.button,
    }

    updated_items = []
    for key, value in update_map.items():
        if value is None:
            continue
        upsert_variant_theme_setting(
            db,
            resolved_variant,
            "application",
            key,
            value,
        )
        updated_items.append({f"application.{key}": value})

    return {
        "status": "Updated",
        "updated_fields": updated_items,
    }


class HeatmapBulkUpdateRequest(BaseModel):
    light: Optional[str] = None
    occupancy: Optional[str] = None
    energy: Optional[str] = None


@router.get("/heatmap")
def get_heatmap_theme(
    db: Session = Depends(get_db),
    variant: Optional[str] = Query(default=None),
):
    _resolved_variant, data = _resolve_theme_map(db, variant)
    return {
        "status": "Success",
        "application_theme": {
            "light": data.get("heatmap", {}).get("light", ""),
            "occupancy": data.get("heatmap", {}).get("occupancy", ""),
            "energy": data.get("heatmap", {}).get("energy", ""),
        },
    }


@router.post("/heatmap")
def update_heatmap_theme_bulk(
    update: HeatmapBulkUpdateRequest,
    db: Session = Depends(get_db),
    _editor: User = Depends(require_theme_editor),
    variant: Optional[str] = Query(default=None),
):
    resolved_variant = resolve_variant(db, variant)
    update_map = {
        "light": update.light,
        "occupancy": update.occupancy,
        "energy": update.energy,
    }

    updated_items = []
    for key, value in update_map.items():
        if value is None:
            continue
        upsert_variant_theme_setting(
            db,
            resolved_variant,
            "heatmap",
            key,
            value,
        )
        updated_items.append({f"heatmap.{key}": value})

    return {
        "status": "Updated",
        "updated_fields": updated_items,
    }
