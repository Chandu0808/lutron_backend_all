import os
import csv
import tempfile
from io import StringIO
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional, Literal, Union
from datetime import date, time

from app.database.session import get_db
from app.schemas.activity_report import ActivityLogResponse
from app.crud.activity_report import (
    fetch_activity_report,
    fetch_activity_report_by_area_ids,
    expand_area_codes_with_children,
    expand_area_ids_with_children,
    get_area_codes_from_floor,
)
from app.dependencies.permissions import require_operator_permission_for_scope
from app.dependencies.auth import get_current_user
from app.models.user_model import User
from app.crud.widget_title import get_title_of_widget
from app.crud import email_settings as email_crud

router = APIRouter()

# Allowed categories (UI categories)
ALLOWED_DESC_KEYWORDS = (
    "User", "QuickControl", "Schedule", "AreaGroup", "Floor",
    "DeviceControl", "Shades", "Lights", "Occupancy", "Scene"
)


@router.get("/", response_model=Union[dict, List[ActivityLogResponse]])
def get_activity_logs(
    activity_type: Optional[str] = Query(None),
    floor_ids: Optional[List[int]] = Query(None, description="List of floor IDs"),
    area_ids: Optional[List[int]] = Query(None, description="Area IDs (optional)"),
    activity_description: Optional[
        List[Literal[
            "User", "QuickControl", "Schedule", "AreaGroup", "Floor",
            "DeviceControl", "Shades", "Lights", "Occupancy", "Scene"
        ]]
    ] = Query(None, description="Filter by description keywords"),
    start_date: date = Query(...),
    start_time: time = Query(...),
    end_date: date = Query(...),
    end_time: time = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # ---------- Friendly permission enforcement ----------
    if floor_ids:
        permitted_floors = []
        for fid in floor_ids:
            try:
                require_operator_permission_for_scope(
                    required_level=1,
                    floor_ids=[fid],
                    enforce_on_empty_scope=True,
                    db=db,
                    current_user=current_user
                )
                permitted_floors.append(fid)
            except HTTPException as e:
                if e.status_code == 403:
                    continue  # skip this floor
                raise  # other errors

        if not permitted_floors:
            return {
                "status": "failed",
                "message": "No authorized floors found for this user."
            }

        floor_ids = permitted_floors

    # ---------- Keyword validation ----------
    if activity_description:
        invalid = [k for k in activity_description if k not in ALLOWED_DESC_KEYWORDS]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid activity_description keyword(s): {invalid}. Allowed: {list(ALLOWED_DESC_KEYWORDS)}"
            )

    # ---------- Always fetch from activity_report ----------
    resolved_area_ids = expand_area_ids_with_children(db, floor_ids, area_ids)
    logs = fetch_activity_report_by_area_ids(
        db=db,
        activity_type=activity_type,
        floor_ids=floor_ids,
        area_ids=resolved_area_ids,
        activity_desc_keywords=activity_description,
        start_date=start_date,
        start_time=start_time,
        end_date=end_date,
        end_time=end_time,
    )

    # ---------- Map ORM → schema ----------
    response = []
    for row in logs:
        response.append(ActivityLogResponse(
            id=row.id,
            activity_type=row.activity_type,
            activity_description=row.activity_desc,  # map field name
            area_name=row.area_name,
            area_code=row.area_code,                 # NEW: include area_code
            user_id=row.user_id,
            user_name=row.user_name,
            created_at=row.created_at,
        ))
    return response


# -------------------------
# Download CSV
# -------------------------
@router.get("/export/download")
def download_activity_logs_csv(
    activity_type: Optional[str] = Query(None),
    floor_ids: Optional[List[int]] = Query(None, description="List of floor IDs"),
    area_ids: Optional[List[int]] = Query(None, description="Area IDs (optional)"),
    activity_description: Optional[List[str]] = Query(None, description="Filter by description keywords"),
    start_date: date = Query(...),
    start_time: time = Query(...),
    end_date: date = Query(...),
    end_time: time = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    logs = get_activity_logs(
        activity_type=activity_type,
        floor_ids=floor_ids,
        area_ids=area_ids,
        activity_description=activity_description,
        start_date=start_date,
        start_time=start_time,
        end_date=end_date,
        end_time=end_time,
        db=db,
        current_user=current_user
    )

    if isinstance(logs, dict) and logs.get("status") == "failed":
        return logs

    output = StringIO()
    writer = csv.writer(output)

    widget_key = "activity_logs"
    widget_title = get_title_of_widget(db, widget_key) or "Activity Logs"

    writer.writerow(["Title", widget_title])
    writer.writerow([f"{len(logs)} activity log entries"])
    writer.writerow([])
    writer.writerow(["SN", "User", "Description", "Area", "Area Code", "Type", "Date/Time"])

    for idx, log in enumerate(logs, 1):
        created_at = log.created_at.strftime("%Y-%m-%d %H:%M")
        writer.writerow([
            idx,
            log.user_name or "",
            log.activity_description or "",
            log.area_name or "",
            log.area_code or "",
            log.activity_type or "",
            created_at,
        ])

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=activity_logs.csv"},
    )


# -------------------------
# Send CSV by Email
# -------------------------
@router.post("/export/send_by_email")
def send_activity_logs_email(
    to_email: str = Query(...),
    activity_type: Optional[str] = Query(None),
    floor_ids: Optional[List[int]] = Query(None, description="List of floor IDs"),
    area_ids: Optional[List[int]] = Query(None, description="Area IDs (optional)"),
    activity_description: Optional[List[str]] = Query(None, description="Filter by description keywords"),
    start_date: date = Query(...),
    start_time: time = Query(...),
    end_date: date = Query(...),
    end_time: time = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    logs = get_activity_logs(
        activity_type=activity_type,
        floor_ids=floor_ids,
        area_ids=area_ids,
        activity_description=activity_description,
        start_date=start_date,
        start_time=start_time,
        end_date=end_date,
        end_time=end_time,
        db=db,
        current_user=current_user
    )

    if isinstance(logs, dict) and logs.get("status") == "failed":
        return logs

    fd, temp_path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)

    widget_key = "activity_logs"
    widget_title = get_title_of_widget(db, widget_key) or "Activity Logs"

    with open(temp_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Title", widget_title])
        writer.writerow([f"{len(logs)} activity log entries"])
        writer.writerow([])
        writer.writerow(["SN", "User", "Description", "Area", "Area Code", "Type", "Date/Time"])

        for idx, log in enumerate(logs, 1):
            created_at = log.created_at.strftime("%Y-%m-%d %H:%M")
            writer.writerow([
                idx,
                log.user_name or "",
                log.activity_description or "",
                log.area_name or "",
                log.area_code or "",
                log.activity_type or "",
                created_at,
            ])

    success = email_crud.send_email(
        db=db,
        to_email=to_email,
        subject=f"{widget_title} Report",
        body=f"Please find attached the {widget_title} report with {len(logs)} entries.",
        is_html=False,
        attachment_path=temp_path,
    )

    os.remove(temp_path)

    if not success:
        raise HTTPException(status_code=500, detail="CSV generated but email sending failed.")

    return {"status": "success", "message": "Email sent successfully with CSV report."}