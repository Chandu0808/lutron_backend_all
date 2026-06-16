from sqlalchemy import Column, Integer
from sqlalchemy.types import JSON

from app.database.session import Base

SINGLETON_ROW_ID = 1


class DashboardChartOrder(Base):
    """Global dashboard widget slot order (single row, id=1)."""

    __tablename__ = "dashboard_chart_order"

    id = Column(Integer, primary_key=True, default=SINGLETON_ROW_ID)
    energy_slot_order = Column(JSON, nullable=True)
    space_charts_tab_order = Column(JSON, nullable=True)
    space_main_tab_order = Column(JSON, nullable=True)
