# app/models/widget_title.py
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint, ForeignKey, func
from app.database.session import Base

class WidgetTitle(Base):
    __tablename__ = "widget_titles"
    __table_args__ = (UniqueConstraint("widget_key", name="uq_widget_titles_widget_key"),)

    id = Column(Integer, primary_key=True, index=True)
    widget_key = Column(String(64), nullable=False)        # e.g., 'savings_by_strategy'
    display_name = Column(String(128), nullable=False)     # e.g., 'Savings by Strategy'
    dropdown_name = Column(String(128), nullable=True)     # e.g., 'Savings Strategy'
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
