from sqlalchemy.orm import Session
from app.models.area import Area
from app.models.floor_proc_mapping import FloorProcMapping
from app.models.floor import Floor
from app.models.events import CurrentAreaEvent


def enrich_tree_with_size_and_load(tree_node: dict, db: Session, floor_id: int) -> dict:
    """
    Recursively enrich area tree nodes with size (sqft, sqm) and load data.
    Calculates cumulative values from children.
    Uses area_tree from floor table which is already filtered floor-wise.
    """
    area_code = tree_node.get("area_code")
    area_id = tree_node.get("area_id")
    
    # Recursively enrich children first
    enriched_children = [
        enrich_tree_with_size_and_load(child, db, floor_id) 
        for child in tree_node.get("children", [])
    ]
    
    # Create enriched node (preserve all existing fields)
    enriched_node = tree_node.copy()
    enriched_node["children"] = enriched_children
    
    # Initialize values
    area_sqft = 0
    area_sqm = 0
    area_load = 0
    
    # Get node's own values if it exists in DB
    db_area = None
    if area_id:
        # Use area_id directly if available (most reliable)
        db_area = db.query(Area).filter(Area.id == area_id).first()
    elif area_code:
        # Fallback: query by area_code and floor_id
        db_area = db.query(Area).filter(
            Area.code == str(area_code),
            Area.floor_id == floor_id
        ).first()
    
    if db_area:
        area_sqft = db_area.area_sqft or 0
        area_sqm = db_area.area_sqm or 0
        
        current_event = db.query(CurrentAreaEvent).filter(
            CurrentAreaEvent.area_id == db_area.id,
            CurrentAreaEvent.processor_id == db_area.processor_id
        ).first()
        
        area_load = current_event.instantaneous_max_power or 0 if current_event else 0
    
    # Add children's cumulative values
    for child in enriched_children:
        area_sqft += child.get("area_sqft", 0)
        area_sqm += child.get("area_sqm", 0)
        area_load += child.get("area_load", 0)
    
    # Set cumulative values
    enriched_node["area_sqft"] = round(area_sqft, 2)
    enriched_node["area_sqm"] = round(area_sqm, 2)
    enriched_node["area_load"] = round(area_load, 2)
    
    return enriched_node


def get_size_and_load_tree_all_floors(db: Session) -> dict:
    floor_proc_mappings = db.query(FloorProcMapping).all()
    floor_tree_list = []

    total_sqft = 0
    total_sqm = 0
    total_load = 0

    seen_floors = set()  # track floor_ids we already processed

    for mapping in floor_proc_mappings:
        if mapping.floor_id in seen_floors:
            continue  # skip duplicates

        floor = db.query(Floor).filter(Floor.id == mapping.floor_id).first()

        if not floor:
            continue

        try:
            # Get area tree directly from floor table (already filtered floor-wise)
            area_tree = floor.area_tree
            
            if not area_tree or len(area_tree) == 0:
                seen_floors.add(mapping.floor_id)
                continue

            # Enrich tree with size and load data
            enriched_trees = [
                enrich_tree_with_size_and_load(tree_root, db, mapping.floor_id) 
                for tree_root in area_tree
            ]
            
            # Calculate totals from enriched tree(s)
            area_sqft = sum(tree.get("area_sqft", 0) for tree in enriched_trees)
            area_sqm = sum(tree.get("area_sqm", 0) for tree in enriched_trees)
            area_load = sum(tree.get("area_load", 0) for tree in enriched_trees)

            # Update totals
            total_sqft += area_sqft
            total_sqm += area_sqm
            total_load += area_load

            floor_tree_list.append({
                "floor_id": floor.id,
                "floor_name": floor.name,
                "area_sqft": round(area_sqft, 2),
                "area_sqm": round(area_sqm, 2),
                "area_load": round(area_load, 2),
                "tree": enriched_trees
            })

            seen_floors.add(floor.id)

        except Exception as e:
            print(f"Error processing floor {mapping.floor_id}: {e}")
            continue  

    return {
        "status": "success",
        "floors": floor_tree_list,
        "total": {
            "total_area_sqft": round(total_sqft, 2),
            "total_area_sqm": round(total_sqm, 2),
            "total_area_load": round(total_load, 2)
        }
    }