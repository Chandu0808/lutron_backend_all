#E:\Gcon\lutron\Lutron_backend_app\app\database\session.py
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os

load_dotenv('environment.env')
DATABASE_URL = os.getenv("DATABASE_HOST_URL")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=_env_int("DB_POOL_SIZE", 20),
    max_overflow=_env_int("DB_MAX_OVERFLOW", 40),
    pool_timeout=_env_int("DB_POOL_TIMEOUT", 60),
    pool_recycle=_env_int("DB_POOL_RECYCLE", 1800),
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
