# from sqlalchemy import Column, Integer, String, ForeignKey
# from sqlalchemy.orm import relationship
# from app.database.session import Base
# # from app.models.user_model import User
# from app.models.floor_model import Floor

# class UserFloorAccess(Base):
#     __tablename__ = "user_floor_access"

#     id = Column(Integer, primary_key=True)
#     user_id = Column(Integer, ForeignKey("users.id"))
#     floor_id = Column(Integer, ForeignKey("floors.id"))
#     access_right = Column(String)  # e.g., "monitor", "monitor_control", "edit"

#     user = relationship("User", back_populates="floor_access")
#     # floor = relationship("Floor")