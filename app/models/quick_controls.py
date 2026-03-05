from sqlalchemy import Column, Integer, String, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from app.database.session import Base
import enum


class CreationMode(str, enum.Enum):
    quick_control = "quick_control"
    schedule = "schedule"


class QuickControl(Base):
    __tablename__ = "quick_controls"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    # NEW: marks whether created directly or via a schedule
    creation_mode = Column(
        SAEnum(CreationMode, name="creation_mode_enum"),
        nullable=False,
        default=CreationMode.quick_control
    )

    quick_control_areas = relationship(
        "QuickControlArea",
        back_populates="quick_control",
        cascade="all, delete-orphan"
    )


class QuickControlArea(Base):
    __tablename__ = 'quick_control_areas'

    id = Column(Integer, primary_key=True, index=True)
    quick_control_id = Column(Integer, ForeignKey('quick_controls.id', ondelete='CASCADE'), index=True)

    # area_id now nullable, will be set NULL when area is deleted
    area_id = Column(Integer, ForeignKey('areas.id', ondelete='SET NULL'), index=True, nullable=True)

    quick_control = relationship("QuickControl", back_populates="quick_control_areas")
    area = relationship("Area")

    actions = relationship(
        "QuickControlAreaAction",
        back_populates="quick_control_area",
        cascade="all, delete-orphan"
    )

    def to_dict(self):
        area_dict = {
            "area_id": self.area_id,
            "area_name": self.area.name if self.area else None,
            "actions": [action.to_dict() for action in self.actions]
        }
        return {k: v for k, v in area_dict.items() if v is not None}



class QuickControlAreaAction(Base):
    __tablename__ = "quick_control_area_actions"

    id = Column(Integer, primary_key=True, index=True)
    quick_control_area_id = Column(Integer, ForeignKey("quick_control_areas.id"))

    type = Column(String, nullable=False)
    scene_name = Column(String, nullable=True)
    scene_code = Column(Integer, nullable=True)

    #  No FK to zone table
    zone_id = Column(String, nullable=True)
    zone_name = Column(String, nullable=True)
    zone_type = Column(String, nullable=True)
    zone_status = Column(String, nullable=True)
    zone_brightness = Column(String, nullable=True)
    zone_temperature = Column(String, nullable=True)

    shade_group_id = Column(Integer, nullable=True)
    shade_group_name = Column(String, nullable=True)
    shade_level = Column(String, nullable=True)

    occupancy_setting = Column(String, nullable=True)

    area_status = Column(String, nullable=True)

    quick_control_area = relationship("QuickControlArea", back_populates="actions")

    def to_dict(self):
        return {
            k: v
            for k, v in {
                "type": self.type,
                "shade_group_id": self.shade_group_id,
                "shade_group_name": self.shade_group_name,
                "shade_level": self.shade_level,
                "occupancy_setting": self.occupancy_setting,
                "zone_id": self.zone_id,
                "zone_name": self.zone_name,
                "zone_type": self.zone_type,
                "zone_status": self.zone_status,
                "zone_brightness": self.zone_brightness,
                "zone_temperature": self.zone_temperature,
                "scene_code": self.scene_code,
                "scene_name": self.scene_name,
                "area_status": self.area_status
            }.items() if v is not None
        }
