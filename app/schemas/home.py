from pydantic import BaseModel, RootModel
from typing import Optional, Dict

class HomeWidgetOut(BaseModel):
    id: int
    name: str
    description: str
    background_image: Optional[str]
    logo: Optional[str] = None
    address: Optional[str]
    location_link: Optional[str]
    overall_area_size: Optional[float]

    class Config:
        from_attributes = True

class HomeWidgetLiteOut(BaseModel):
    name: str
    description: str
    # background_image: str
    # logo: str
    background_image: Optional[str] = None
    logo: Optional[str] = None

    class Config:
        from_attributes = True



# Home Page Content Schemas
class HomePageContentBase(BaseModel):
    page: str
    item: str
    value: str

class HomePageContentIn(RootModel[Dict[str, str]]):
    pass

# Schema for Lutron Home
class HomePageContentLutronIn(BaseModel):
    description: Optional[str] = None
    background_image: Optional[str] = None

# Schema for Client Home
class HomePageContentClientIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    background_image: Optional[str] = None
    logo_image: Optional[str] = None


class HomePageContentListOut(BaseModel):
    id: int
    page: str
    item: str
    value: str
    
    class Config:
        from_attributes = True
