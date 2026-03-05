#E:\Gcon\lutron\lutron_backend\app\models\home.py
from sqlalchemy import Column, Integer, String, Float, Text
from app.database.session import Base


class HomePageContent(Base):
    __tablename__ = "home_page_content"

    id = Column(Integer, primary_key=True, index=True)
    page = Column(String, nullable=False)
    item = Column(String, nullable=False)
    value = Column(Text, nullable=False)
