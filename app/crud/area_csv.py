import io
import csv
from sqlalchemy.orm import Session
from app.schemas.area_csv import AreaCSVRequest
from app.models.area import Area
from app.models.processor import Processor
from app.crud.area_tree import build_area_tree
from app.utils.json_connection import connect_to_processor
from app.utils.lutron_helpers import get_root_area


def find_path_to_area(node, target_id, path=None):
    if path is None:
        path = []
    if node.get("area_id") == target_id:
        return path + [node["name"]]
    for child in node.get("children", []):
        result = find_path_to_area(child, target_id, path + [node["name"]])
        if result:
            return result
    return None


def generate_area_csv(request: AreaCSVRequest, db: Session):
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "processor_id", "root_area", "root_subarea", "root_sub_subarea",
        "code", "name", "area in sqft", "area in sqm"
    ])

    for proc in request.processors:
        processor = db.query(Processor).filter(Processor.id == proc.processor_id).first()
        if not processor:
            continue

        try:
            sock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)
            root_area = get_root_area(sock)
            area_tree = build_area_tree(processor.ipv4, sock, root_area["href"], db)
        except Exception as e:
            print(f"Processor {processor.id} not reachable: {e}")
            continue

        areas = db.query(Area).filter(
            Area.processor_id == proc.processor_id,
            Area.id.in_(proc.area_ids)
        ).all()
        area_map = {area.id: area for area in areas}

        for area_id in proc.area_ids:
            area = area_map.get(area_id)
            if not area:
                continue

            path = find_path_to_area(area_tree, area_id)
            root_area_name = path[0] if len(path) > 0 else ""
            root_subarea = path[1] if len(path) > 1 else ""
            root_sub_subarea = path[2] if len(path) > 2 else ""

            writer.writerow([
                processor.id,
                root_area_name,
                root_subarea,
                root_sub_subarea,
                area.code,
                area.name,
                area.area_sqft,
                area.area_sqm
            ])

    output.seek(0)
    return output
