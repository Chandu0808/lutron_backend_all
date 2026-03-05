# app/theme_data.py
from app.database.session import SessionLocal
from app.models.theme_model import Theme

def load_theme_defaults():
    """
    Seed the theme table with default values if they are missing.
    - Inserts defaults only if key does not exist.
    - Does not overwrite user changes.
    """
    db = SessionLocal()
    defaults = [
        {"key": "background_image", "value": "/background_image/defaultBg.png"},
        {"key": "ui.background", "value": "#CDC0A0"},
        {"key": "ui.content", "value": "#807864"},
        {"key": "ui.button", "value": "#232323"},
        {"key": "heatmap.light", "value": "#F2FF00"},
        {"key": "heatmap.occupancy", "value": "#4318D1"},
        {"key": "heatmap.energy", "value": "#5D8C00"},
    ]

    for item in defaults:
        exists = db.query(Theme).filter_by(key=item["key"]).first()
        if not exists:
            db.add(Theme(**item))  # Insert only if missing

    db.commit()
    db.close()
