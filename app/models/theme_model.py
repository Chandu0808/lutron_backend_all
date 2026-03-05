# models/theme.py
from sqlalchemy import Column, Integer, String
from app.database.session import Base

class Theme(Base):
    __tablename__ = "theme"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)   # e.g., "background_image", "ui.background"
    value = Column(String)              # e.g., "#ffffff", "https://..."
