"""
Pydantic schemas for FOFP admin APIs.

These schemas are exclusive to the new ``/fofp/*`` endpoints and are not used
by any existing response model. They follow the project's mixed v2 style:
``model_config`` with ``from_attributes=True`` for ORM-friendly response
models, and ``conint`` / ``confloat`` for incoming numeric validation (same
pattern as ``app.schemas.floor``).
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, confloat, conint


# -------------------- Generate --------------------


class FOFPGenerateRequest(BaseModel):
    """Body for POST /fofp/generate-layout."""

    floor_id: conint(gt=0)


class FOFPGenerateResponse(BaseModel):
    """Summary returned by POST /fofp/generate-layout."""

    generated: int
    skipped: int
    failed: int


# -------------------- Layout retrieval --------------------


class FOFPPositionOut(BaseModel):
    """One FOFP placement entry as returned by GET /fofp/layout/{floor_id}."""

    zone_id: int
    area_id: int
    zone_name: Optional[str] = None
    x: float
    y: float
    marker_shape: Optional[str] = None
    shape_size: int
    shape_size_x: Optional[int] = None
    shape_size_y: Optional[int] = None
    placement_source: str

    model_config = ConfigDict(from_attributes=True)


class FOFPLayoutResponse(BaseModel):
    """Full layout payload for one floor."""

    floor_id: int
    positions: List[FOFPPositionOut]


# -------------------- Bulk save --------------------


class FOFPPositionIn(BaseModel):
    """One incoming placement for PUT /fofp/layout."""

    zone_id: conint(gt=0)
    area_id: conint(gt=0)
    x: confloat(allow_inf_nan=False)
    y: confloat(allow_inf_nan=False)
    marker_shape: Optional[str] = None
    shape_size: Optional[conint(ge=4)] = None
    shape_size_x: Optional[conint(ge=4)] = None
    shape_size_y: Optional[conint(ge=4)] = None


class FOFPSaveRequest(BaseModel):
    """Body for PUT /fofp/layout."""

    floor_id: conint(gt=0)
    positions: List[FOFPPositionIn]


class FOFPSaveResponse(BaseModel):
    """Counts returned by PUT /fofp/layout."""

    updated: int
    created: int


# -------------------- Global config (settings phase) --------------------


class FOFPConfigOut(BaseModel):
    """Full FOFP global configuration."""

    fofp_enabled: bool
    shape: str
    marker_size: int
    marker_color: str
    last_generated_at: Optional[str] = None
    generation_status: str


class FOFPConfigUpdate(BaseModel):
    """Partial update body for PUT /fofp/config."""

    fofp_enabled: Optional[bool] = None
    shape: Optional[str] = None
    marker_size: Optional[conint(ge=4, le=20)] = None
    marker_color: Optional[str] = None
