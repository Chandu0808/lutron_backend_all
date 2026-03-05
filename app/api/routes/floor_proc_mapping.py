from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from schemas.floor_proc_mapping import FloorProcMapCreate, FloorProcMapOut
from crud.floor_proc_mapping import create_floor_proc_mapping
from app.models.user_model import User
from app.dependencies.auth import get_current_user

router = APIRouter()

@router.post("/floor-processor/map", response_model=FloorProcMapOut)
def map_floor_processor(payload: FloorProcMapCreate, db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    try:
        return create_floor_proc_mapping(db, payload.floor_id, payload.processor_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
