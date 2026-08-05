from sqlalchemy.orm import Session
from app.models.floor_proc_mapping import FloorProcMapping

def create_floor_proc_mapping(db: Session, floor_id: int, processor_id: int):
    """Create floor↔processor mapping if it does not already exist. Does not commit."""
    existing = (
        db.query(FloorProcMapping)
        .filter(
            FloorProcMapping.floor_id == floor_id,
            FloorProcMapping.processor_id == processor_id,
        )
        .first()
    )
    if existing:
        return existing

    mapping = FloorProcMapping(floor_id=floor_id, processor_id=processor_id)
    db.add(mapping)
    db.flush()
    return mapping


def ensure_floor_processor_mappings_from_areas(db: Session, floor_id: int) -> None:
    """Ensure every processor that owns areas on this floor has a floor_proc_mapping row."""
    from app.models.area import Area

    area_processor_ids = (
        db.query(Area.processor_id)
        .filter(Area.floor_id == floor_id, Area.processor_id.isnot(None))
        .distinct()
        .all()
    )
    for (processor_id,) in area_processor_ids:
        create_floor_proc_mapping(db, floor_id=floor_id, processor_id=int(processor_id))
