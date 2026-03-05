from sqlalchemy.orm import Session
from app.models.floor_proc_mapping import FloorProcMapping

def create_floor_proc_mapping(db: Session, floor_id: int, processor_id: int):
    mapping = FloorProcMapping(floor_id=floor_id, processor_id=processor_id)
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping
