import csv
from io import StringIO
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
from app.models.processor import Processor
from app.models.area import Area
from app.models.coordinate import Coordinate
from app.utils.lutron_helpers import is_processor_reachable
from app.utils.json_connection import create_ssl_connection, send_json, recv_json


def get_root_area(sock):
    send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": "/area/rootarea"}})
    response = recv_json(sock)
    return response.get("Body", {}).get("Area", {})


def get_child_areas(sock, area_href):
    send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": f"{area_href}/childarea/summary"}})
    response = recv_json(sock)
    return response.get("Body", {}).get("AreaSummaries", [])


def get_area_status(sock, area_href):
    send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": f"{area_href}/status"}})
    response = recv_json(sock)
    return response.get("Body", {}).get("AreaStatus", {})


def build_area_tree(sock, area, processor_id, parent_href=None, root_area=None, root_subarea=None, root_sub_subarea=None):
    name = area.get("Name", "")
    href = area.get("href")
    is_leaf = area.get("IsLeaf", False)

    if parent_href is None:
        root_area = name
    elif root_area and not root_subarea:
        root_subarea = name
    elif root_subarea and not root_sub_subarea:
        root_sub_subarea = name

    node = {
        "href": href,
        "name": name,
        "is_leaf": is_leaf,
        "processor_id": processor_id,
        "root_area": root_area,
        "root_subarea": root_subarea,
        "root_sub_subarea": root_sub_subarea,
        "children": [],
        "area_status": get_area_status(sock, href)
    }

    if not is_leaf:
        for child in get_child_areas(sock, href):
            node["children"].append(
                build_area_tree(sock, child, processor_id, href, root_area, root_subarea, root_sub_subarea)
            )

    return node


def get_leaf_areas_csv(
    processor_id: str,
    db: Session,
    root_area_filter: str = None,
    root_subarea_filter: str = None,
    root_sub_subarea_filter: str = None
):
    processor = db.query(Processor).filter(Processor.id == processor_id).first()
    if not processor:
        return JSONResponse(
            content={"status": "error", "message": "Processor not found."},
            status_code=404
        )

    if not is_processor_reachable(processor.ipv4):
        return JSONResponse(
            content={"status": "error", "message": f"Processor at {processor.ipv4} not reachable."},
            status_code=503
        )

    ssock = create_ssl_connection(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)
    if not ssock:
        return JSONResponse(
            content={"status": "error", "message": f"Failed to connect to processor {processor.ipv4}."},
            status_code=500
        )

    try:
        root_area = get_root_area(ssock)
        full_tree = build_area_tree(ssock, root_area, processor_id)
        ssock.close()
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"Failed to retrieve area tree: {e}"},
            status_code=500
        )

    leaf_nodes = []

    def extract_leaf_areas(node):
        if node.get("is_leaf"):
            if (not root_area_filter or node["root_area"] == root_area_filter) and \
               (not root_subarea_filter or node["root_subarea"] == root_subarea_filter) and \
               (not root_sub_subarea_filter or node["root_sub_subarea"] == root_sub_subarea_filter):

                area_code = node["href"].split("/")[-1]
                area = db.query(Area).filter_by(processor_id=processor_id, code=area_code).first()
                coordinates = []
                if area:
                    coords = db.query(Coordinate).filter_by(area_id=area.id).all()
                    coordinates = [f"{c.x}~{c.y}" for c in coords]

                leaf_nodes.append({
                    "processor_id": node["processor_id"],
                    "root_area": node.get("root_area", ""),
                    "root_subarea": node.get("root_subarea", ""),
                    "root_sub_subarea": node.get("root_sub_subarea", ""),
                    "code": area_code,
                    "name": node["name"],
                    "coordinates": coordinates
                })

        for child in node.get("children", []):
            extract_leaf_areas(child)

    extract_leaf_areas(full_tree)

    csv_buffer = StringIO()
    base_fields = ["processor_id", "root_area", "root_subarea", "root_sub_subarea", "code", "name"]
    max_coords = max((len(row["coordinates"]) for row in leaf_nodes), default=0)
    header = base_fields + [""] * max_coords

    writer = csv.writer(csv_buffer)
    writer.writerow(header)

    for row in leaf_nodes:
        base = [
            row["processor_id"],
            row["root_area"],
            row["root_subarea"],
            row["root_sub_subarea"],
            row["code"],
            row["name"]
        ]
        writer.writerow(base + row.get("coordinates", []))

    csv_buffer.seek(0)
    return StreamingResponse(
        csv_buffer,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=leafareas_{processor_id}.csv"}
    )



