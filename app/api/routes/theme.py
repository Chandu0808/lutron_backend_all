# routes/theme.py
import os, random, shutil
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from app.database.session import get_db
from app.models.theme_model import Theme



router = APIRouter()

@router.get("/")
def get_theme(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Theme).all()
    data = {row.key: row.value for row in rows}

    base_url = str(request.base_url).rstrip("/")

    return {
        "status": "Success",
        "background_image": f"{base_url}{data.get('background_image', '')}",
        "ui_theme_colors": {
            "background": data.get("ui.background", ""),
            "content": data.get("ui.content", ""),
            "button": data.get("ui.button", "")
        },
        "heatmap_colors": {
            "light": data.get("heatmap.light", ""),
            "occupancy": data.get("heatmap.occupancy", ""),
            "energy": data.get("heatmap.energy", "")
        }
    }



@router.get("/background")
def get_background_image(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Theme).all()
    data = {row.key: row.value for row in rows}

    base_url = str(request.base_url).rstrip("/")

    return {
        "status": "Success",
        "background_image": f"{base_url}{data.get('background_image', '')}"
    }

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPLOAD_DIR = os.path.join(APP_DIR, "background_image")
os.makedirs(UPLOAD_DIR, exist_ok=True)
@router.post("/background")
async def update_background_image_with_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        ext = file.filename.split('.')[-1]
        unique_filename = f"bg_{random.randint(1000, 9999)}.{ext}"
        save_path = os.path.join(UPLOAD_DIR, unique_filename)
        with open(save_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        relative_path = f"/background_image/{unique_filename}"
        theme_row = db.query(Theme).filter(Theme.key == "background_image").first()
        if not theme_row:
            theme_row = Theme(key="background_image", value=relative_path)
            db.add(theme_row)
        else:
            theme_row.value = relative_path
        db.commit()
        db.refresh(theme_row)

        return {"status": "Updated", "background_image": relative_path}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- GET: current application theme colors ---
@router.get("/application")
def get_application_theme(db: Session = Depends(get_db)):
    theme_keys = ['ui.background', 'ui.content', 'ui.button']
    rows = db.query(Theme).filter(Theme.key.in_(theme_keys)).all()
    data = {row.key: row.value for row in rows}

    return {
        "status": "Success",
        "application_theme": {
            "background": data.get("ui.background", ""),
            "content": data.get("ui.content", ""),
            "button": data.get("ui.button", "")
        }
    }

# --- GET: current heatmap theme colors ---
@router.get("/heatmap")
def get_application_theme(db: Session = Depends(get_db)):
    theme_keys = ['heatmap.light', 'heatmap.occupancy', 'heatmap.energy']
    rows = db.query(Theme).filter(Theme.key.in_(theme_keys)).all()
    data = {row.key: row.value for row in rows}

    return {
        "status": "Success",
        "application_theme": {
            "light": data.get("heatmap.light", ""),
            "occupancy": data.get("heatmap.occupancy", ""),
            "energy": data.get("heatmap.energy", "")
        }
    }


# --- POST: update theme color ---
class ApplicationThemeUpdateRequest(BaseModel):
    background: Optional[str]
    content: Optional[str]
    button: Optional[str]

@router.post("/application")
def update_application_theme_bulk(update: ApplicationThemeUpdateRequest, db: Session = Depends(get_db)):
    update_map = {
        "ui.background": update.background,
        "ui.content": update.content,
        "ui.button": update.button
    }

    updated_items = []

    for key, value in update_map.items():
        if value is None:
            continue  # Skip if not provided

        theme_row = db.query(Theme).filter(Theme.key == key).first()
        if theme_row:
            theme_row.value = value
        else:
            theme_row = Theme(key=key, value=value)
            db.add(theme_row)

        updated_items.append({key: value})

    db.commit()

    return {
        "status": "Updated",
        "updated_fields": updated_items
    }



class HeatmapBulkUpdateRequest(BaseModel):
    light: Optional[str]
    occupancy: Optional[str]
    energy: Optional[str]


@router.post("/heatmap")
def update_heatmap_theme_bulk(update: HeatmapBulkUpdateRequest, db: Session = Depends(get_db)):
    update_map = {
        "heatmap.light": update.light,
        "heatmap.occupancy": update.occupancy,
        "heatmap.energy": update.energy
    }

    updated_items = []

    for key, value in update_map.items():
        if value is None:
            continue  # skip if the field wasn't included in the request

        theme_row = db.query(Theme).filter(Theme.key == key).first()
        if theme_row:
            theme_row.value = value
        else:
            theme_row = Theme(key=key, value=value)
            db.add(theme_row)

        updated_items.append({key: value})

    db.commit()

    return {
        "status": "Updated",
        "updated_fields": updated_items
    }
