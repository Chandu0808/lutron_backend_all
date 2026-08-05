from typing import List, Optional
from pydantic import BaseModel

# Model for white tuning level
class WhiteTuningLevelModel(BaseModel):
    Kelvin: int

# Detail model for each zone in a scene
class EditSceneDetail(BaseModel):
    zone_type: str  # "switched", "dimmed", or "whitetune"
    assignment_href: Optional[str] = None
    SwitchedLevel: Optional[str] = None         # For switched zones
    Level: Optional[int] = None                 # Common to dimmed/whitetune
    FadeTime: Optional[str] = None              # Optional for dimmed/whitetune
    DelayTime: Optional[str] = None             # Optional for dimmed/whitetune
    WhiteTuningLevel: Optional[WhiteTuningLevelModel] = None  # For white-tune zones

# Request body for editing a scene
class EditSceneRequest(BaseModel):
    area_id: int
    scene_id: int
    details: List[EditSceneDetail]

# Input model for fetching current scene status
class SceneStatusInput(BaseModel):
    area_id: int
    scene_id: int

# Output model for each assignment in scene status response
class SceneAssignmentOut(BaseModel):
    assignment_href: str
    zone_id: Optional[int] = None
    zone_type: str
    zone_name: str
    SwitchedLevel: Optional[str] = None
    Level: Optional[int] = None
    FadeTime: Optional[str] = None
    DelayTime: Optional[str] = None
    WhiteTuningLevel: Optional[WhiteTuningLevelModel] = None
