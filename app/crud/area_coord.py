import csv
from io import StringIO
from collections import defaultdict
from fastapi import UploadFile, HTTPException
from sqlalchemy.orm import Session
from app.models.area import Area
from app.models.coordinate import Coordinate
from app.models.zone import Zone
from app.models.processor import Processor
from app.models.floor_proc_mapping import FloorProcMapping
from app.utils.lutron_helpers import is_processor_reachable
from app.utils.json_connection import create_ssl_connection, send_json, recv_json


def _assign_floor_id_from_processor_mapping(db: Session, area: Area) -> None:
    """If area has no floor, assign when the processor maps to exactly one floor."""
    if area is None or area.floor_id is not None or area.processor_id is None:
        return
    mappings = (
        db.query(FloorProcMapping)
        .filter(FloorProcMapping.processor_id == area.processor_id)
        .all()
    )
    if len(mappings) == 1:
        area.floor_id = mappings[0].floor_id


def upload_area_coordinates(file: UploadFile, db: Session, expected_processor_id: int | None = None):
    contents = file.file.read().decode("utf-8")
    reader = csv.reader(StringIO(contents))
    headers = next(reader)

    area_ids = []
    processor_area_map = defaultdict(list)

    for row in reader:
        if len(row) < 6 or not row[0].strip() or not row[4].strip():
            continue

        try:
            processor_id = int(row[0].strip())
            code = row[4].strip()
            name = row[5].strip()
            coord_strings = row[6:]
        except Exception:
            continue

        if expected_processor_id is not None and processor_id != expected_processor_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Processor ID mismatch: CSV contains processor {processor_id}, "
                    f"but expected processor {expected_processor_id}."
                ),
            )

        area = db.query(Area).filter_by(processor_id=processor_id, code=code).first()
        if area:
            area.name = name
            db.query(Coordinate).filter_by(area_id=area.id).delete()
        else:
            area = Area(processor_id=processor_id, code=code, name=name)
            db.add(area)
            db.flush()

        polygon_index = 0
        for coord in coord_strings:
            coord_str = str(coord).strip() if coord else ""
            if coord_str == "|":
                polygon_index += 1
                continue
            if "~" in coord_str:
                try:
                    x, y = map(float, coord_str.split("~"))
                    db.add(Coordinate(area_id=area.id, x=x, y=y, polygon_index=polygon_index))
                except ValueError:
                    continue

        _assign_floor_id_from_processor_mapping(db, area)

        area_ids.append(area.id)
        processor_area_map[processor_id].append((code, area.id))

    for processor_id, area_list in processor_area_map.items():
        processor = db.query(Processor).filter(Processor.id == processor_id).first()
        if not processor or not is_processor_reachable(processor.ipv4):
            continue

        ssock = create_ssl_connection(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)
        if not ssock:
            continue

        try:
            for code, area_id in area_list:
                if not code:
                    continue

                send_json(ssock, {
                    "CommuniqueType": "ReadRequest",
                    "Header": {"Url": f"/area/{code}/associatedzone"}
                })

                metadata_resp = recv_json(ssock)
                metadata_zones = metadata_resp.get("Body", {}).get("Zones", []) if metadata_resp else []

                for zone in metadata_zones:
                    zone_href = zone.get("href", "")
                    if not zone_href:
                        continue

                    zone_code = zone_href.split("/")[-1]
                    zone_name = zone.get("Name", f"Zone {zone_code}")
                    zone_type = zone.get("ControlType", "Unknown")

                    existing_zone = db.query(Zone).filter_by(processor_id=processor_id, code=zone_code).first()
                    if existing_zone:
                        existing_zone.name = zone_name
                        existing_zone.type = zone_type
                        existing_zone.area_id = area_id
                        existing_zone.processor_id = processor_id
                    else:
                        db.add(
                            Zone(
                                code=zone_code,
                                name=zone_name,
                                type=zone_type,
                                area_id=area_id,
                                processor_id=processor_id,
                            )
                        )
        except Exception:
            continue
        finally:
            ssock.close()

    db.commit()

    # Refresh floor boundary boxes from the newly uploaded coordinates.
    floor_ids = set()
    if area_ids:
        for (fid,) in (
            db.query(Area.floor_id)
            .filter(Area.id.in_(area_ids), Area.floor_id.isnot(None))
            .distinct()
            .all()
        ):
            floor_ids.add(fid)

    if floor_ids:
        from app.crud.floor import update_floor_boundaries

        for floor_id in floor_ids:
            try:
                update_floor_boundaries(db, floor_id)
            except Exception:
                continue

    return {
        "status": "success",
        "area_id": area_ids
    }
