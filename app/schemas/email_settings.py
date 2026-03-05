from pydantic import BaseModel, EmailStr

class EmailServerSettingsCreate(BaseModel):
    server_name: str
    port: int
    server_email: str
    sender_name: str
    app_password: str  # plain text key for input

class EmailServerSettingsInDB(EmailServerSettingsCreate):
    id: int
    server_name: str
    port: int
    server_email: str
    sender_name: str
    app_password: str

    class Config:
        from_attributes = True


class SendEmailRequest(BaseModel):
    to_email: EmailStr
    subject: str


# Public schema to return (excluding sensitive `key`)
class EmailServerSettingsPublic(BaseModel):
    id: int
    server_name: str
    port: int
    server_email: str
    sender_name: str

    class Config:
        from_attributes = True
