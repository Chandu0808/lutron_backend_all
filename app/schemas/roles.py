# from pydantic import BaseModel
# from typing import List, Optional

# class SubRoleBase(BaseModel):
#     name: str

# class SubRoleCreate(SubRoleBase):
#     role_id: int

# class SubRole(SubRoleBase):
#     id: int
#     role_id: int

#     class Config:
#         from_attributes = True


# class RoleBase(BaseModel):
#     name: str

# class RoleCreate(RoleBase):
#     pass

# class Role(RoleBase):
#     id: int
#     sub_roles: Optional[List[SubRole]] = []

#     class Config:
#         from_attributes = True
