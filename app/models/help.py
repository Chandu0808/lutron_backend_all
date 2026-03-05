# models/help_upload.py

from sqlalchemy import Column, Integer, String
from app.database.session import Base

class HelpUpload(Base):
    __tablename__ = "help_uploads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)       # e.g., "Project Information/Scope"
    file_path = Column(String, nullable=False)  # e.g., "static/uploads/scope.pdf"
