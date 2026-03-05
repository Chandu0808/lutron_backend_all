from sqlalchemy import Column, String, Integer, DateTime, Enum, ForeignKey, Boolean
from app.database.session import Base
from datetime import datetime
from sqlalchemy.orm import relationship

# Keep existing enum type name to avoid enum-type migration
FloorPermissionEnum = Enum(
    "monitor",
    "monitor_control",
    "monitor_control_edit",
    name="floor_permission"
)

class UserPermission(Base):
    __tablename__ = "user_permissions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    floor_id = Column(Integer, ForeignKey("floors.id", ondelete="CASCADE"), nullable=False)
    permission_type = Column(FloorPermissionEnum, nullable=False)

    # Relationships with passive_deletes to let DB cascade handle deletions
    user = relationship(
        "User",
        back_populates="user_permissions",
        passive_deletes=True
    )
    floor = relationship(
        "Floor",
        back_populates="user_permissions",
        passive_deletes=True
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum("Superadmin", "Admin", "Operator", name="user_roles"), nullable=False)
    change_password = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user_permissions = relationship(
        "UserPermission",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
