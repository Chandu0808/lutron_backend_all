from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database.session import Base


class AreaScene(Base):
    """Cached LEAP area scene list for fallback when the processor is unreachable."""

    __tablename__ = "area_scenes"
    __table_args__ = (
        UniqueConstraint("area_id", "scene_code", name="uq_area_scenes_area_scene_code"),
    )

    id = Column(Integer, primary_key=True)
    area_id = Column(Integer, ForeignKey("areas.id", ondelete="CASCADE"), nullable=False, index=True)
    scene_code = Column(Integer, nullable=False)
    name = Column(String(200), nullable=False, default="")

    area = relationship("Area", backref="cached_scenes")
