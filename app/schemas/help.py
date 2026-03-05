# schemas/help_upload.py

from pydantic import BaseModel

class HelpUploadSchema(BaseModel):
    id: int
    name: str
    file_path: str

    class Config:
        from_attributes = True
