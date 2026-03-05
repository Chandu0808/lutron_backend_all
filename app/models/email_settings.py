from sqlalchemy import Column, Integer, String
from app.database.session import Base

class EmailServerSettings(Base):
    __tablename__ = "email_server_settings"

    id = Column(Integer, primary_key=True, index=True)
    server_name = Column(String, nullable=False)
    port = Column(Integer, nullable=False)
    server_email = Column(String, nullable=False)
    sender_name = Column(String, nullable=False)
    app_password = Column(String, nullable=False) 