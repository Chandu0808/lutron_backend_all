from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models.area import Area
from app.models.processor import Processor
from app.utils.lutron_helpers import is_processor_reachable
from app.utils.json_connection import create_ssl_connection, send_json, recv_json
def edit_scene_assignments(db: Session, area_id: int, scene_id: int, details: list):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")
    if not is_processor_reachable(processor.ipv4):
        raise HTTPException(status_code=400, detail="Processor not reachable")
    ssock = create_ssl_connection(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)
    if not ssock:
        raise HTTPException(status_code=500, detail="Failed to connect to processor")
    area_href = f"/area/{area.code}"
    assignment_urls = {
        "switched": f"/preset/{scene_id}/switchedlevelassignment/commandprocessor",
        "dimmed": f"/preset/{scene_id}/dimmedlevelassignment/commandprocessor",
        "whitetune": f"/preset/{scene_id}/whitetuninglevelassignment/commandprocessor"
    }
    assignment_key_map = {
        "switched": "SwitchedLevelAssignments",
        "dimmed": "DimmedLevelAssignments",
        "whitetune": "WhiteTuningLevelAssignments"
    }
    created_hrefs = {k: [] for k in assignment_urls}
    # Step 1: Filter assignments once per zone_type for this area
    filtered_zone_types = set()
    for detail in details:
        zone_type = detail.get("zone_type")
        if zone_type not in assignment_urls or zone_type in filtered_zone_types:
            continue
        filtered_zone_types.add(zone_type)
        send_json(ssock, {
            "CommuniqueType": "CreateRequest",
            "Header": {"Url": assignment_urls[zone_type]},
            "Body": {
                "Command": {
                    "CommandType": "Filter",
                    "FilterParameters": {
                        "Where": {
                            "Binary": {
                                "Operator": "=",
                                "Left": {"Resource": {"href": "$1/assignableresource/associatedarea"}},
                                "Right": {"Resource": {"href": area_href}}
                            }
                        }
                    }
                }
            }
        })
        response = recv_json(ssock)
        assignments = response.get("Body", {}).get(assignment_key_map[zone_type], [])
        for assignment in assignments:
            href = assignment.get("href")
            if href:
                created_hrefs[zone_type].append(href)
    # Step 2: Update each assignment href with zone-specific data
    for detail in details:
        zone_type = detail.get("zone_type")
        assignment_href = detail.get("assignment_href")
        if not zone_type:
            continue
        # Use the assignment_href from frontend to find the correct zone to update
        # If assignment_href is provided, use it directly; otherwise fall back to index-based (for backwards compatibility)
        if assignment_href:
            update_url = assignment_href
        else:
            # Fallback: index within same zone_type only
            hrefs = created_hrefs.get(zone_type, [])
            same_type_details = [d for d in details if d.get("zone_type") == zone_type]
            idx = same_type_details.index(detail)
            if idx >= len(hrefs):
                continue
            update_url = hrefs[idx]
        if zone_type == "switched":
            update_body = {
                "CommuniqueType": "UpdateRequest",
                "Header": {"Url": update_url},
                "Body": {"switchedlevelassignment": {
                    "SwitchedLevel": detail["SwitchedLevel"]
                }}
            }
        elif zone_type == "dimmed":
            update_body = {
                "CommuniqueType": "UpdateRequest",
                "Header": {"Url": update_url},
                "Body": {"DimmedLevelAssignment": {
                    "Level": detail["Level"],
                    "FadeTime": str(detail.get("FadeTime", "2")),
                    "DelayTime": str(detail.get("DelayTime", "0"))
                }}
            }
        elif zone_type == "whitetune":
            update_body = {
                "CommuniqueType": "UpdateRequest",
                "Header": {"Url": update_url},
                "Body": {"WhiteTuningLevelAssignment": {
                    "Level": detail["Level"],
                    "FadeTime": str(detail.get("FadeTime", "2")),
                    "DelayTime": str(detail.get("DelayTime", "0")),
                    "WhiteTuningLevel": detail["WhiteTuningLevel"]
                }}
            }
        else:
            continue
        send_json(ssock, update_body)
        recv_json(ssock)
    ssock.close()
    return {"status": "success", "message": "Scene edited successfully"}
def get_scene_status(db: Session, area_id: int, scene_id: int):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        return {"status": "error", "message": "Area not found"}
    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        return {"status": "error", "message": "Processor not found"}
    if not is_processor_reachable(processor.ipv4):
        return {"status": "error", "message": "Processor not reachable"}
    ssock = create_ssl_connection(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)
    if not ssock:
        return {"status": "error", "message": "SSL connection failed"}
    # Step 1: Build zone name map
    send_json(ssock, {
        "CommuniqueType": "ReadRequest",
        "Header": {"Url": f"/area/{area.code}/associatedzone"}
    })
    zone_meta_resp = recv_json(ssock)
    zone_meta_map = {
        int(z["href"].split("/")[-1]): z.get("Name", "")
        for z in zone_meta_resp.get("Body", {}).get("Zones", [])
    }
    result = []
    def fetch_assignments(url_key: str, zone_type: str):
        send_json(ssock, {
            "CommuniqueType": "CreateRequest",
            "Header": {"Url": f"/preset/{scene_id}/{url_key}/commandprocessor"},
            "Body": {
                "Command": {
                    "CommandType": "Filter",
                    "FilterParameters": {
                        "Where": {
                            "Binary": {
                                "Operator": "=",
                                "Left": {"Resource": {"href": "$1/assignableresource/associatedarea"}},
                                "Right": {"Resource": {"href": f"/area/{area.code}"}}
                            }
                        }
                    }
                }
            }
        })
        response = recv_json(ssock)
        body_key_map = {
            "switchedlevelassignment": "SwitchedLevelAssignments",
            "dimmedlevelassignment": "DimmedLevelAssignments",
            "whitetuninglevelassignment": "WhiteTuningLevelAssignments",
            "shadelevelassignment": "ShadeLevelAssignments"
        }
        assignments = response.get("Body", {}).get(body_key_map[url_key], [])
        for assignment in assignments:
            zone_href = assignment.get("AssignableResource", {}).get("href", "")
            zone_id = int(zone_href.split("/")[-1]) if zone_href else None
            zone_name = zone_meta_map.get(zone_id, f"Zone {zone_id}")
            out = {
                "assignment_href": assignment.get("href"),
                "zone_id": zone_id,
                "zone_type": zone_type,
                "zone_name": zone_name
            }
            if zone_type == "switched":
                out["SwitchedLevel"] = assignment.get("SwitchedLevel")
            elif zone_type == "dimmed":
                out.update({
                    "Level": assignment.get("Level"),
                    "FadeTime": assignment.get("FadeTime"),
                    "DelayTime": assignment.get("DelayTime")
                })
            elif zone_type == "whitetune":
                white = assignment.get("WhiteTuningLevel") or assignment.get("WhiteTuneLevel", {})
                out.update({
                    "Level": assignment.get("Level"),
                    "FadeTime": assignment.get("FadeTime"),
                    "DelayTime": assignment.get("DelayTime"),
                    "WhiteTuningLevel": {"Kelvin": white.get("Kelvin")}
                })
            elif zone_type == "shade":
                out["Level"] = assignment.get("Level")
            result.append(out)
    # Call all 4 assignment types
    fetch_assignments("switchedlevelassignment", "switched")
    fetch_assignments("dimmedlevelassignment", "dimmed")
    fetch_assignments("whitetuninglevelassignment", "whitetune")
    fetch_assignments("shadelevelassignment", "shade")
    ssock.close()
    return {
        "status": "success",
        "area_id": area_id,
        "scene_id": scene_id,
        "details": result
    }