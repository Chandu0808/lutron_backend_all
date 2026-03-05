from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict
import calendar
from sqlalchemy import text, func, case, cast, Date, Time, String, extract
from app.models.area_energy_stats import AreaEnergyStat
from app.models.area_occupancy_stats import AreaOccupancyStat
from app.models.area import Area
from app.models.floor import Floor
from app.models.area_group import AreaGroupMapping, AreaGroup
from app.models.widget_title import WidgetTitle
from app.crud.widget_title import get_title_of_widget
from app.database.session import get_db
from app.crud.energy_stats import (
    get_energy_consumption,
    get_energy_savings,
    get_peak_min_consumption,
    get_occupancy_count_over_time,
    get_light_power_density,
    spaceutilization_by_area_group,
    spaceutilization_by_area_group_from_logs,
    get_space_utilization_by_area,
    get_peak_min_occupancy,
    get_saving_by_strategy,
    get_total_consumption_by_area_id,
    get_unified_energy_data_of_a_day,
    get_unified_energy_data_of_a_week,
    get_unified_energy_data_of_a_month,
    get_unified_energy_data_of_a_year
)
from app.crud.area_group import occupancy_percentage_by_area_group_from_logs
from app.crud.energy_stats_optimized import (
    get_energy_consumption_optimized,
    get_occupancy_count_optimized,
    get_energy_savings_optimized,
    spaceutilization_by_area_group_optimized,
    get_space_utilization_by_area_optimized,
    get_space_utilization_by_area_from_logs_optimized,
    get_total_consumption_by_area_id_optimized,
    get_instant_occupancy_count_optimized
)
from app.crud.user import get_floors_mapped_to_operator_user, refine_area_and_floors_of_operator_user
from app.models.user_model import User
from app.dependencies.auth import get_current_user
from app.utils.widget_title import append_widget_title
from app.dependencies.permissions import require_operator_permission_for_scope
from pydantic import BaseModel, validator
from app.energy_logger import (
    check_and_fill_missing_data_simple,
    smart_gap_detection,
    fill_missing_energy_data_smart,
    fill_missing_occupancy_data_smart,
    generate_15min_intervals
)

router = APIRouter()

@router.get("/unified_energy_consumption_savings_data")
def unified_energy_consumption_savings_data(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if(current_user.role == "Operator"):
        if(area_ids or floor_ids):
            area_ids, floor_ids = refine_area_and_floors_of_operator_user(db, area_ids, floor_ids, current_user)
        else:
            print("DB session:", db)
            print("Bind:", db.get_bind())
            floor_ids = get_floors_mapped_to_operator_user(db, current_user)

    # In case of Custom dates, correct any error in Start & End dates 
    # This makes our future verifactions simple 
    if(time_range == "custom"):
        if(end_date >= date.today()):
            end_date = date.today()
    
        if(start_date > end_date):
            start_date = end_date


    if(time_range == "this_day" or (time_range == "custom" and start_date == end_date)):
        if(time_range == "this_day"):
            data_date = date.today()
        else:
            data_date = start_date
        
        data = get_unified_energy_data_of_a_day(db, area_ids, floor_ids, data_date)
        
    else:
       
        if(time_range == "this_week" or (time_range == "custom" and (end_date - start_date).days <= 6)):
            
            data = get_unified_energy_data_of_a_week(db, area_ids, floor_ids, time_range, start_date, end_date)

        elif(time_range == "this_month" or (time_range == "custom" and (end_date - start_date).days <= 30)):

            data = get_unified_energy_data_of_a_month(db, area_ids, floor_ids, time_range, start_date, end_date)
        
        else:
            data = get_unified_energy_data_of_a_year(db, area_ids, floor_ids, time_range, start_date, end_date)

    # Now lets get title for all 3 charts
    savings_chart_name = get_title_of_widget(db, "savings") or "Savings"
    consumption_chart_name = get_title_of_widget(db, "consumption") or "Consumption"
    peak_and_min_chart_name = get_title_of_widget(db, "peak_and_minimum_consumption") or "Peak and Minimum Consumption"
    return {"status": "Success", 
            "chart-type": data["chart-type"],
            "x-axis": data["x-axis"], 
            "consumption": data["consumption"], 
            "consumption_data": data["consumption_data"], 
            "savings": data["savings"], 
            "savings_data": data["savings_data"], 
            "max_limit": data["max_limit"], 
            "unit": data["unit"], 
            "consumption_peak": data["consumption_peak"], 
            "consumption_min": data["consumption_min"],
            "savings_chart_name": savings_chart_name,
            "consumption_chart_name": consumption_chart_name,
            "peak_and_min_chart_name": peak_and_min_chart_name,
            } 


@router.get("/energy_consumption")
def energy_consumption_api(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,                 # pass through as-is (None or list)
            floor_ids=floor_ids,               # IMPORTANT: also pass floors
            enforce_on_empty_scope=bool(       # only enforce when some scope is provided
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don't have permission to view energy consumption for one or more selected areas/floors."
            }
        raise

    # ---------- Business Logic ----------
    try:
        data = get_energy_consumption_optimized(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
        return append_widget_title(db, "consumption", data)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    
@router.get("/energy_savings")
def energy_savings_api(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don’t have permission to view energy savings for one or more selected areas/floors.",
            }
        raise  # propagate other errors (422, etc.)

    # ---------- Business Logic ----------
    try:
        data = get_energy_savings_optimized(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        return append_widget_title(db, "savings", data)

    except ValueError as e:
        return {"status": "error", "message": str(e)}
    

@router.get("/peak_min_consumption")
def peak_min_consumption_api(
    floor_ids: Optional[List[int]] = Query(None),
    area_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don’t have permission to view peak/min consumption for one or more selected areas/floors.",
            }
        raise  # bubble up 422 or other unexpected errors

    # ---------- Business Logic ----------
    try:
        result = get_peak_min_consumption(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        return append_widget_title(db, "peak_and_minimum_consumption", result)

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
    

# @router.get("/peak_min_consumption/download")
# def download_peak_min_consumption_csv(
#     area_ids: Optional[List[int]] = Query(None),
#     floor_ids: Optional[List[int]] = Query(None),
#     time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
#     start_date: Optional[datetime] = Query(None),
#     end_date: Optional[datetime] = Query(None),
#     db: Session = Depends(get_db),
#     user: User = Depends(get_current_user),
# ):
#     # ---------- Permission Enforcement ----------
#     try:
#         require_operator_permission_for_scope(
#             required_level=1,  # monitor level
#             area_ids=area_ids,
#             floor_ids=floor_ids,
#             enforce_on_empty_scope=bool(
#                 (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
#             ),
#             db=db,
#             current_user=user,
#         )
#     except HTTPException as e:
#         if e.status_code == 403:
#             return {
#                 "status": "failed",
#                 "message": "You don’t have permission to download peak/min consumption for one or more selected areas/floors.",
#             }
#         raise

#     try:
#         # Resolve date range
#         now = datetime.now()
#         if time_range == "this_day":
#             start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
#             end_date = now.replace(hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "this_week":
#             start_date = now - timedelta(days=now.weekday())
#             start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
#             end_date = start_date + timedelta(days=6)
#             end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "this_month":
#             start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
#             last_day = calendar.monthrange(now.year, now.month)[1]
#             end_date = start_date.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "this_year":
#             start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
#             end_date = start_date.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "custom":
#             if not (start_date and end_date):
#                 return {"status": "error", "message": "Custom range requires both start_date and end_date"}
#             start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
#             end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
#         else:
#             raise ValueError("Invalid time_range value")
        
#         # --- Fetch title from widget_titles ---
#         widget_key = "peak_and_minimum_consumption"
#         widget_title = (
#             db.query(WidgetTitle.display_name)
#             .filter(WidgetTitle.widget_key == widget_key)
#             .scalar()
#             or "Peak/Min Consumption"
#         )

#         # --- Build duration string ---
#         if time_range == "this_day":
#             duration_str = start_date.strftime("%Y-%m-%d")
#         elif time_range == "custom":
#             duration_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
#         else:
#             duration_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"

#         # Prepare CSV
#         output = StringIO()
#         writer = csv.writer(output)

#         # Title & Duration at top
#         writer.writerow(["Title", widget_title])
#         writer.writerow(["Duration", duration_str])
#         writer.writerow([])

#         # CSV header
#         writer.writerow([
#             "Date",
#             "Area Name",
#             "Energy Consumed (Wh)",
#             "Peak Consumption (Wh)",
#             "Min Consumption (Wh)",
#         ])

#         # Process each area separately for peak/min
#         for area in db.query(Area).filter(Area.id.in_(area_ids or [])).all():
#             # Peak
#             peak_row = (
#                 db.query(AreaEnergyStat)
#                 .filter(AreaEnergyStat.area_code == area.code)
#                 .filter(AreaEnergyStat.created_at >= start_date)
#                 .filter(AreaEnergyStat.created_at <= end_date)
#                 .order_by(AreaEnergyStat.energy_consumed_in_Wh.desc())
#                 .first()
#             )

#             # Minimum
#             min_row = (
#                 db.query(AreaEnergyStat)
#                 .filter(AreaEnergyStat.area_code == area.code)
#                 .filter(AreaEnergyStat.created_at >= start_date)
#                 .filter(AreaEnergyStat.created_at <= end_date)
#                 .order_by(AreaEnergyStat.energy_consumed_in_Wh.asc())
#                 .first()
#             )

#             # Write to CSV if data exists
#             if peak_row and min_row:
#                 writer.writerow([
#                     peak_row.created_at.date().isoformat(),
#                     area.name,
#                     peak_row.energy_consumed_in_Wh,
#                     peak_row.energy_consumed_in_Wh,  # Peak Consumption
#                     min_row.energy_consumed_in_Wh,   # Min Consumption
#                 ])

#         output.seek(0)
#         filename = f"peak_min_consumption_{time_range}.csv"
#         return StreamingResponse(
#             output,
#             media_type="text/csv",
#             headers={"Content-Disposition": f"attachment; filename={filename}"},
#         )

#     except Exception as e:
#         return {"status": "error", "message": str(e)}


# @router.post("/peak_min_consumption/send_by_email")
# def send_peak_min_consumption_email(
#     to_email: str = Query(...),
#     area_ids: Optional[List[int]] = Query(None),
#     floor_ids: Optional[List[int]] = Query(None),
#     time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
#     start_date: Optional[datetime] = Query(None),
#     end_date: Optional[datetime] = Query(None),
#     db: Session = Depends(get_db),
#     user: User = Depends(get_current_user),
# ):
#     # ---------- Permission Enforcement ----------
#     try:
#         require_operator_permission_for_scope(
#             required_level=1,  # monitor level
#             area_ids=area_ids,
#             floor_ids=floor_ids,
#             enforce_on_empty_scope=bool(
#                 (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
#             ),
#             db=db,
#             current_user=user,
#         )
#     except HTTPException as e:
#         if e.status_code == 403:
#             return {
#                 "status": "failed",
#                 "message": "You don’t have permission to send peak/min consumption email for one or more selected areas/floors.",
#             }
#         raise

#     try:
#         # Resolve date range 
#         now = datetime.now()
#         if time_range == "this_day":
#             start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
#             end_date = now.replace(hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "this_week":
#             start_date = now - timedelta(days=now.weekday())
#             start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
#             end_date = start_date + timedelta(days=6)
#             end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "this_month":
#             start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
#             last_day = calendar.monthrange(now.year, now.month)[1]
#             end_date = start_date.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "this_year":
#             start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
#             end_date = start_date.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "custom":
#             if not (start_date and end_date):
#                 return {"status": "error", "message": "Custom range requires both start_date and end_date"}
#             start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
#             end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)

#         # Fetch widget title
#         widget_key = "peak_and_minimum_consumption"
#         widget_title = (
#             db.query(WidgetTitle.display_name)
#             .filter(WidgetTitle.widget_key == widget_key)
#             .scalar()
#             or "Peak/Min Consumption"
#         )

#         # Duration string
#         if time_range == "this_day":
#             duration_str = start_date.strftime("%Y-%m-%d")
#         elif time_range == "custom":
#             duration_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
#         else:
#             duration_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"

#         # --- Create temp CSV file ---
#         fd, temp_path = tempfile.mkstemp(suffix=".csv")
#         os.close(fd)

#         with open(temp_path, "w", newline="") as f:
#             writer = csv.writer(f)
#             writer.writerow(["Title", widget_title])
#             writer.writerow(["Duration", duration_str])
#             writer.writerow([])
#             writer.writerow([
#                 "Date",
#                 "Area Name",
#                 "Energy Consumed (Wh)",
#                 "Peak Consumption (Wh)",
#                 "Min Consumption (Wh)",
#             ])

#             # Process each area for peak & min
#             for area in db.query(Area).filter(Area.id.in_(area_ids or [])).all():
#                 peak_row = (
#                     db.query(AreaEnergyStat)
#                     .filter(AreaEnergyStat.area_code == area.code)
#                     .filter(AreaEnergyStat.created_at >= start_date)
#                     .filter(AreaEnergyStat.created_at <= end_date)
#                     .order_by(AreaEnergyStat.energy_consumed_in_Wh.desc())
#                     .first()
#                 )
#                 min_row = (
#                     db.query(AreaEnergyStat)
#                     .filter(AreaEnergyStat.area_code == area.code)
#                     .filter(AreaEnergyStat.created_at >= start_date)
#                     .filter(AreaEnergyStat.created_at <= end_date)
#                     .order_by(AreaEnergyStat.energy_consumed_in_Wh.asc())
#                     .first()
#                 )

#                 if peak_row and min_row:
#                     writer.writerow([
#                         peak_row.created_at.date().isoformat(),
#                         area.name,
#                         peak_row.energy_consumed_in_Wh,
#                         peak_row.energy_consumed_in_Wh,
#                         min_row.energy_consumed_in_Wh,
#                     ])

#         # --- Send email with attachment ---
#         success = email_crud.send_email(
#             db=db,
#             to_email=to_email,
#             subject=f"{widget_title} Report",
#             body=f"Please find attached the {widget_title} report for {duration_str}.",
#             is_html=False,
#             attachment_path=temp_path,
#         )

#         os.remove(temp_path)

#         if not success:
#             raise HTTPException(status_code=500, detail="CSV generated but email sending failed.")

#         return {"status": "success", "message": "Email sent successfully with CSV report."}

#     except Exception as e:
#         return {"status": "error", "message": str(e)}


@router.get("/light_power_density")
def light_power_density(
    floor_ids: Optional[List[int]] = Query(None, description="List of floor IDs"),
    area_ids: Optional[List[int]] = Query(None, description="List of area IDs"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Route: Light Power Density (LPD) summary
    - If floor_ids not given → all floors
    - If area_ids not given → all areas under given floor(s)
    """

    # ---------- Resolve floors ----------
    if not floor_ids:
        floor_ids = [f.id for f in db.query(Floor).all()]

    # ---------- Resolve areas ----------
    if not area_ids:
        db_areas = db.query(Area).filter(Area.floor_id.in_(floor_ids)).all()
        area_ids = [a.id for a in db_areas]

    if not area_ids:
        raise HTTPException(status_code=404, detail="No areas found for given criteria")

    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(area_ids or floor_ids),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don't have permission to view light power density for one or more selected floors/areas.",
            }
        raise  # propagate 422 or unexpected errors

    # ---------- Business Logic ----------
    try:
        return get_light_power_density(
            db=db,
            floor_ids=floor_ids,
            area_ids=area_ids,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/total_consumption/by_group")
def total_consumption_by_area_ids(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don’t have permission to view total consumption for one or more selected areas/floors.",
            }
        raise  # propagate 422 or unexpected errors

    # ---------- Business Logic ----------
    try:
        data = get_total_consumption_by_area_id_optimized(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        return append_widget_title(db, "consumption_by_area_groups", data)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")   

@router.get("/occupancy_count")
def occupancy_count_api(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don't have permission to view occupancy count for one or more selected areas/floors.",
            }
        raise  

    # ---------- Business Logic ----------
    try:
        result = get_occupancy_count_optimized(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        return append_widget_title(db, "utilization", result)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/instant_occupancy_count")
def instant_occupancy_count_api(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don't have permission to view instant occupancy count for one or more selected areas/floors.",
            }
        raise  

    # ---------- Business Logic ----------
    try:
        result = get_instant_occupancy_count_optimized(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        return append_widget_title(db, "instant_occupancy_count", result)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/occupancy_by_group")
def space_utilization_all_groups_api(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don't have permission to view occupancy by group for one or more selected areas/floors.",
            }
        raise  # propagate other errors (422, unexpected, etc.)

    # ---------- Business Logic ----------
    try:
        data = spaceutilization_by_area_group_optimized(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        return append_widget_title(db, "utilization_by_area_group", data)

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/occupancy_by_group_from_logs")
def space_utilization_all_groups_from_logs_api(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    API endpoint for occupancy percentage by group using occupancy_logs table.
    This endpoint calculates the percentage of time each area group is occupied vs unoccupied
    based on occupancy status changes over time. An area group is considered occupied if
    at least one area in the group is occupied.
    
    Returns occupancy percentages, total occupied/unoccupied seconds, and total time range.
    """
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don't have permission to view occupancy by group from logs for one or more selected areas/floors.",
            }
        raise  # propagate other errors (422, unexpected, etc.)

    # ---------- Business Logic ----------
    try:
        data = occupancy_percentage_by_area_group_from_logs(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        # Wrap data in dict to match expected format
        result = {"status": "success", "data": data}
        return append_widget_title(db, "utilization_by_area_group", result)

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
@router.get("/space_utilization_per")
def space_utilization_api(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don’t have permission to view space utilization for one or more selected areas/floors.",
            }
        raise  # bubble up 422 or other unexpected errors

    # ---------- Business Logic ----------
    try:
        result = get_space_utilization_by_area_optimized(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        return append_widget_title(db, "utilization_by_area", result)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/space_utilization_per_from_logs")
def space_utilization_from_logs_api(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don’t have permission to view space utilization for one or more selected areas/floors.",
            }
        raise  # bubble up 422 or other unexpected errors

    # ---------- Business Logic ----------
    try:
        result = get_space_utilization_by_area_from_logs_optimized(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        return append_widget_title(db, "utilization_by_area", result)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/peak_min_occupancy")
def peak_min_occupancy_api(
    floor_ids: Optional[List[int]] = Query(None),
    area_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the percentage of areas that are currently Occupied (Peak)
    and Unoccupied (Min) within the given time range.
    """
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don’t have permission to view peak/min occupancy for one or more selected areas/floors.",
            }
        raise  # bubble up 422 or other unexpected errors

    # ---------- Business Logic ----------
    try:
        data = get_peak_min_occupancy(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        return append_widget_title(db, "peak_and_minimum_utilization", data)

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/peak_min_occupancy_from_logs")
def peak_min_occupancy_from_logs_api(
    floor_ids: Optional[List[int]] = Query(None),
    area_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Variant of peak/min occupancy API that sources data from occupancy_logs table.
    """
    try:
        require_operator_permission_for_scope(
            required_level=1,
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don’t have permission to view peak/min occupancy for one or more selected areas/floors.",
            }
        raise

    try:
        data = get_peak_min_occupancy(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        return append_widget_title(db, "peak_and_minimum_utilization", data)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")


# @router.get("/peak_min_occupancy/download")
# def download_peak_min_occupancy_csv(
#     floor_ids: Optional[List[int]] = Query(None),
#     area_ids: Optional[List[int]] = Query(None),
#     time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
#     start_date: Optional[datetime] = None,
#     end_date: Optional[datetime] = None,
#     db: Session = Depends(get_db),
#     user: User = Depends(get_current_user),
# ):
#     # ---------- Permission Enforcement ----------
#     try:
#         require_operator_permission_for_scope(
#             required_level=1,  # monitor level
#             area_ids=area_ids,
#             floor_ids=floor_ids,
#             enforce_on_empty_scope=bool(
#                 (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
#             ),
#             db=db,
#             current_user=user,
#         )
#     except HTTPException as e:
#         if e.status_code == 403:
#             return {
#                 "status": "failed",
#                 "message": "You don’t have permission to download peak/min occupancy for one or more selected areas/floors.",
#             }
#         raise

#     now = datetime.now()

#     # Resolve area_ids if missing
#     if not area_ids and floor_ids:
#         area_ids = [a.id for a in db.query(Area).filter(Area.floor_id.in_(floor_ids)).all()]
#     elif not area_ids:
#         area_ids = [a.id for a in db.query(Area).all()]

#     if not area_ids:
#         raise HTTPException(status_code=400, detail="No areas found for the given filters")
    
#     # Map area_id → area_name
#     area_map = {
#         a.id: a.name
#         for a in db.query(Area).filter(Area.id.in_(area_ids)).all()
#     }

#     # Resolve time range
#     if time_range == "this_day":
#         start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
#         end_date = now.replace(hour=23, minute=59, second=59, microsecond=999)
#     elif time_range == "this_week":
#         start_date = now - timedelta(days=now.weekday())
#         start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
#         end_date = start_date + timedelta(days=6)
#         end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
#     elif time_range == "this_month":
#         start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
#         last_day = calendar.monthrange(now.year, now.month)[1]
#         end_date = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999)
#     elif time_range == "this_year":
#         start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
#         end_date = start_date.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999)
#     elif time_range == "custom":
#         if not (start_date and end_date):
#             raise HTTPException(status_code=400, detail="Custom range requires both start_date and end_date")
#         start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
#         end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
#     else:
#         raise HTTPException(status_code=400, detail="Invalid time_range value")
    
#     # --- Fetch title from widget_titles ---
#     widget_key = "peak_and_minimum_utilization"
#     widget_title = (
#         db.query(WidgetTitle.display_name)
#         .filter(WidgetTitle.widget_key == widget_key)
#         .scalar()
#         or "Peak/Min Occupancy"
#     )

#     # --- Build duration string ---
#     if time_range == "this_day":
#         duration_str = start_date.strftime("%Y-%m-%d")
#     elif time_range == "custom":
#         duration_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
#     else:
#         duration_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"

#     # Query occupancy with area_id, date, and time split
#     results = (
#         db.query(
#             AreaOccupancyStat.area_id,
#             func.date(AreaOccupancyStat.created_at).label("date"),
#             func.to_char(AreaOccupancyStat.created_at, "HH24:MI").label("time"),
#             func.count().filter(AreaOccupancyStat.occupancy_status == 'Occupied').label('occupied'),
#             func.count().filter(AreaOccupancyStat.occupancy_status == 'Unoccupied').label('unoccupied'),
#         )
#         .filter(AreaOccupancyStat.created_at >= start_date)
#         .filter(AreaOccupancyStat.created_at <= end_date)
#         .filter(AreaOccupancyStat.area_id.in_(area_ids))
#         .group_by(AreaOccupancyStat.area_id, "date", "time")
#         .order_by("date", "time", AreaOccupancyStat.area_id)
#         .all()
#     )

#     # Prepare CSV output
#     output = StringIO()
#     writer = csv.writer(output)

#     # Title & Duration at top
#     writer.writerow(["Title", widget_title])
#     writer.writerow(["Duration", duration_str])
#     writer.writerow([])
#     writer.writerow(["date", "time", "area_name", "occupied", "unoccupied", "percentage", "peak_occupancy", "min_occupancy"])

#     for row in results:
#         total = row.occupied + row.unoccupied
#         percentage = round((row.occupied / total) * 100, 2) if total > 0 else 0.0
#         peak = percentage
#         min_occ = round((row.unoccupied / total) * 100, 2) if total > 0 else 0.0
#         area_name = area_map.get(row.area_id, "")
#         writer.writerow([row.date, row.time, area_name, row.occupied, row.unoccupied, percentage, peak, min_occ])

#     output.seek(0)
#     return StreamingResponse(
#         output,
#         media_type="text/csv",
#         headers={"Content-Disposition": "attachment; filename=peak_min_occupancy.csv"},
#     )

# @router.post("/peak_min_occupancy/send_by_email")
# def send_peak_min_occupancy_email(
#     to_email: str = Query(...),
#     floor_ids: Optional[List[int]] = Query(None),
#     area_ids: Optional[List[int]] = Query(None),
#     time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
#     start_date: Optional[datetime] = None,
#     end_date: Optional[datetime] = None,
#     db: Session = Depends(get_db),
#     user: User = Depends(get_current_user),
# ):
#     # ---------- Permission Enforcement ----------
#     try:
#         require_operator_permission_for_scope(
#             required_level=1,  # monitor level
#             area_ids=area_ids,
#             floor_ids=floor_ids,
#             enforce_on_empty_scope=bool(
#                 (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
#             ),
#             db=db,
#             current_user=user,
#         )
#     except HTTPException as e:
#         if e.status_code == 403:
#             return {
#                 "status": "failed",
#                 "message": "You don’t have permission to send peak/min occupancy email for one or more selected areas/floors.",
#             }
#         raise

#     try:
#         now = datetime.now()

#         # Resolve area_ids if missing
#         if not area_ids and floor_ids:
#             area_ids = [a.id for a in db.query(Area).filter(Area.floor_id.in_(floor_ids)).all()]
#         elif not area_ids:
#             area_ids = [a.id for a in db.query(Area).all()]

#         if not area_ids:
#             raise HTTPException(status_code=400, detail="No areas found for the given filters")

#         # Map area_id → name
#         area_map = {a.id: a.name for a in db.query(Area).filter(Area.id.in_(area_ids)).all()}

#         # --- Resolve time range ---
#         if time_range == "this_day":
#             start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
#             end_date = now.replace(hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "this_week":
#             start_date = now - timedelta(days=now.weekday())
#             start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
#             end_date = start_date + timedelta(days=6)
#             end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "this_month":
#             start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
#             last_day = calendar.monthrange(now.year, now.month)[1]
#             end_date = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "this_year":
#             start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
#             end_date = start_date.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999)
#         elif time_range == "custom":
#             if not (start_date and end_date):
#                 return {"status": "error", "message": "Custom range requires both start_date and end_date"}
#             start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
#             end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)

#         # --- Fetch title from widget_titles ---
#         widget_key = "peak_and_minimum_utilization"
#         widget_title = (
#             db.query(WidgetTitle.display_name)
#             .filter(WidgetTitle.widget_key == widget_key)
#             .scalar()
#             or "Peak & Minimum Occupancy"
#         )

#         # --- Build duration string ---
#         if time_range == "this_day":
#             duration_str = start_date.strftime("%Y-%m-%d")
#         elif time_range == "custom":
#             duration_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
#         else:
#             duration_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"

#         # --- Query data ---
#         results = (
#             db.query(
#                 AreaOccupancyStat.area_id,
#                 func.date(AreaOccupancyStat.created_at).label("date"),
#                 func.to_char(AreaOccupancyStat.created_at, "HH24:MI").label("time"),
#                 func.count().filter(AreaOccupancyStat.occupancy_status == 'Occupied').label('occupied'),
#                 func.count().filter(AreaOccupancyStat.occupancy_status == 'Unoccupied').label('unoccupied'),
#             )
#             .filter(AreaOccupancyStat.created_at >= start_date)
#             .filter(AreaOccupancyStat.created_at <= end_date)
#             .filter(AreaOccupancyStat.area_id.in_(area_ids))
#             .group_by(AreaOccupancyStat.area_id, "date", "time")
#             .order_by("date", "time", AreaOccupancyStat.area_id)
#             .all()
#         )

#         # --- Create temp CSV ---
#         fd, temp_path = tempfile.mkstemp(suffix=".csv")
#         os.close(fd)

#         with open(temp_path, "w", newline="") as f:
#             writer = csv.writer(f)
#             writer.writerow(["Title", widget_title])
#             writer.writerow(["Duration", duration_str])
#             writer.writerow([])
#             writer.writerow(["date", "time", "area_name", "occupied", "unoccupied", "percentage", "peak_occupancy", "min_occupancy"])

#             for row in results:
#                 total = row.occupied + row.unoccupied
#                 percentage = round((row.occupied / total) * 100, 2) if total > 0 else 0.0
#                 peak = percentage
#                 min_occ = round((row.unoccupied / total) * 100, 2) if total > 0 else 0.0
#                 area_name = area_map.get(row.area_id, "")
#                 writer.writerow([row.date, row.time, area_name, row.occupied, row.unoccupied, percentage, peak, min_occ])

#         # --- Send email with attachment ---
#         success = email_crud.send_email(
#             db=db,
#             to_email=to_email,
#             subject=f"{widget_title} Report",
#             body=f"Please find attached the {widget_title} report for {duration_str}.",
#             is_html=False,
#             attachment_path=temp_path,
#         )

#         os.remove(temp_path)

#         if not success:
#             raise HTTPException(status_code=500, detail="CSV generated but email sending failed.")

#         return {"status": "success", "message": "Email sent successfully with CSV report."}

#     except Exception as e:
#         return {"status": "error", "message": str(e)}


@router.get("/saving_by_stratergy")
def dashboard_saving_by_strategy(
    floor_ids: Optional[List[int]] = Query(None, description="List of Floor IDs"),
    area_ids: Optional[List[int]] = Query(None, description="List of Area IDs"),
    time_range: str = Query(..., enum=["this_day","this_week","this_month","this_year","custom"]),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ---------- Permission Enforcement ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don’t have permission to view saving by strategy for one or more selected areas/floors.",
            }
        raise  # bubble up 422 or unexpected errors

    # ---------- Business Logic ----------
    result = get_saving_by_strategy(
        db=db,
        area_ids=area_ids,
        floor_ids=floor_ids,
        time_range=time_range,
        start_date=start_date,
        end_date=end_date,
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    
    return result

# --------------------- TEST API FOR MISSING DATA FILLING --------------------- #

class MissingDataFillRequest(BaseModel):
    start_time: str
    end_time: str
    area_codes: Optional[List[int]] = None  # If None, process all areas
    
    @validator('start_time')
    def validate_start_time(cls, v):
        try:
            return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise ValueError('start_time must be in format "YYYY-MM-DD HH:MM:SS" (e.g., "2025-09-29 13:29:40")')
    
    @validator('end_time')
    def validate_end_time(cls, v):
        try:
            return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise ValueError('end_time must be in format "YYYY-MM-DD HH:MM:SS" (e.g., "2025-09-29 13:29:40")')


@router.post("/test-fill-missing-data")
def test_fill_missing_data_api(
    request: MissingDataFillRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Test API to fill missing data in a custom time range.
    Uses existing functions from energy_logger.py to detect and fill gaps.
    
    Parameters:
    - start_time: Start of the time range to check
    - end_time: End of the time range to check  
    - area_codes: Optional list of area codes to process (if None, processes all areas)
    
    Returns:
    - Summary of filled data for both energy and occupancy
    """
    try:
        # Validate time range
        if request.start_time >= request.end_time:
            raise HTTPException(status_code=400, detail="start_time must be before end_time")
        
        # Check if time range is not too far in the future
        current_time = datetime.now()
        if request.start_time > current_time:
            raise HTTPException(status_code=400, detail="start_time cannot be in the future")
        
        # Get area composite keys to process
        if request.area_codes:
            # Get processor_id for each provided area_code
            area_keys = db.query(Area.code, Area.processor_id).filter(
                Area.code.in_([str(c) for c in request.area_codes])
            ).distinct().all()
            area_keys = [(int(code), proc_id) for code, proc_id in area_keys]
        else:
            # Get all unique (area_code, processor_id) pairs that have data
            area_keys = db.query(AreaEnergyStat.area_code, AreaEnergyStat.processor_id).distinct().all()
            area_keys = [(code, proc_id) for code, proc_id in area_keys if code is not None and proc_id is not None]
        
        if not area_keys:
            return {
                "status": "success",
                "message": "No areas found to process",
                "energy_filled": 0,
                "occupancy_filled": 0,
                "processed_areas": 0,
                "time_range": {
                    "start_time": request.start_time,
                    "end_time": request.end_time
                }
            }
        
        total_energy_filled = 0
        total_occupancy_filled = 0
        processed_areas = 0
        area_details = []
        
        # Process each area using composite key
        for area_code, processor_id in area_keys:
            try:
                # Check for missing energy data using composite key
                missing_energy_intervals = smart_gap_detection(
                    db, area_code, processor_id, request.start_time, request.end_time, "energy"
                )
                
                # Check for missing occupancy data using composite key
                missing_occupancy_intervals = smart_gap_detection(
                    db, area_code, processor_id, request.start_time, request.end_time, "occupancy"
                )
                
                energy_filled = 0
                occupancy_filled = 0
                
                # Fill missing energy data using composite key
                if missing_energy_intervals:
                    energy_filled = fill_missing_energy_data_smart(db, area_code, processor_id, missing_energy_intervals)
                    total_energy_filled += energy_filled
                
                # Fill missing occupancy data using composite key
                if missing_occupancy_intervals:
                    occupancy_filled = fill_missing_occupancy_data_smart(db, area_code, processor_id, missing_occupancy_intervals)
                    total_occupancy_filled += occupancy_filled
                
                # Record details for this area
                area_details.append({
                    "area_code": area_code,
                    "processor_id": processor_id,
                    "missing_energy_intervals": len(missing_energy_intervals),
                    "missing_occupancy_intervals": len(missing_occupancy_intervals),
                    "energy_filled": energy_filled,
                    "occupancy_filled": occupancy_filled
                })
                
                processed_areas += 1
                
            except Exception as area_error:
                print(f"[TEST API ERROR] Failed to process area_code {area_code}: {area_error}")
                continue
        
        # Commit all changes
        db.commit()
        
        return {
            "status": "success",
            "message": f"Successfully processed {processed_areas} areas",
            "energy_filled": total_energy_filled,
            "occupancy_filled": total_occupancy_filled,
            "processed_areas": processed_areas,
            "time_range": {
                "start_time": request.start_time,
                "end_time": request.end_time
            },
            "area_details": area_details
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/test-missing-data-analysis")
def test_missing_data_analysis_api(
    start_time: str = Query(..., description='Start time for analysis in format "YYYY-MM-DD HH:MM:SS" (e.g., "2025-09-29 13:29:40")'),
    end_time: str = Query(..., description='End time for analysis in format "YYYY-MM-DD HH:MM:SS" (e.g., "2025-09-29 13:29:40")'),
    area_codes: Optional[List[int]] = Query(None, description="Area codes to analyze (optional)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Test API to analyze missing data gaps without filling them.
    Useful for understanding data gaps before running the fill operation.
    
    Parameters:
    - start_time: Start of the time range to analyze
    - end_time: End of the time range to analyze
    - area_codes: Optional list of area codes to analyze (if None, analyzes all areas)
    
    Returns:
    - Analysis of missing data gaps for both energy and occupancy
    """
    try:
        # Parse datetime strings
        try:
            start_time_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
            end_time_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise HTTPException(status_code=400, detail='Time format must be "YYYY-MM-DD HH:MM:SS" (e.g., "2025-09-29 13:29:40")')
        
        # Validate time range
        if start_time_dt >= end_time_dt:
            raise HTTPException(status_code=400, detail="start_time must be before end_time")
        
        # Get area composite keys to analyze
        if area_codes:
            # Get processor_id for each provided area_code
            area_keys = db.query(Area.code, Area.processor_id).filter(
                Area.code.in_([str(c) for c in area_codes])
            ).distinct().all()
            area_keys = [(int(code), proc_id) for code, proc_id in area_keys]
        else:
            # Get all unique (area_code, processor_id) pairs that have data
            area_keys = db.query(AreaEnergyStat.area_code, AreaEnergyStat.processor_id).distinct().all()
            area_keys = [(code, proc_id) for code, proc_id in area_keys if code is not None and proc_id is not None]
        
        if not area_keys:
            return {
                "status": "success",
                "message": "No areas found to analyze",
                "analysis": {
                    "total_expected_intervals": 0,
                    "total_missing_energy_intervals": 0,
                    "total_missing_occupancy_intervals": 0,
                    "area_analysis": []
                }
            }
        
        # Generate expected intervals for the time range
        expected_intervals = generate_15min_intervals(start_time_dt, end_time_dt)
        total_expected_intervals = len(expected_intervals)
        
        total_missing_energy = 0
        total_missing_occupancy = 0
        area_analysis = []
        
        # Analyze each area using composite key
        for area_code, processor_id in area_keys:
            try:
                # Check for missing energy data using composite key
                missing_energy_intervals = smart_gap_detection(
                    db, area_code, processor_id, start_time_dt, end_time_dt, "energy"
                )
                
                # Check for missing occupancy data using composite key
                missing_occupancy_intervals = smart_gap_detection(
                    db, area_code, processor_id, start_time_dt, end_time_dt, "occupancy"
                )
                
                total_missing_energy += len(missing_energy_intervals)
                total_missing_occupancy += len(missing_occupancy_intervals)
                
                # Record analysis for this area
                area_analysis.append({
                    "area_code": area_code,
                    "processor_id": processor_id,
                    "missing_energy_intervals": len(missing_energy_intervals),
                    "missing_occupancy_intervals": len(missing_occupancy_intervals),
                    "missing_energy_percentage": round((len(missing_energy_intervals) / total_expected_intervals) * 100, 2) if total_expected_intervals > 0 else 0,
                    "missing_occupancy_percentage": round((len(missing_occupancy_intervals) / total_expected_intervals) * 100, 2) if total_expected_intervals > 0 else 0
                })
                
            except Exception as area_error:
                print(f"[ANALYSIS ERROR] Failed to analyze area_code {area_code}, processor {processor_id}: {area_error}")
                continue
        
        return {
            "status": "success",
            "message": f"Analysis completed for {len(area_keys)} areas",
            "analysis": {
                "time_range": {
                    "start_time": start_time,
                    "end_time": end_time
                },
                "total_expected_intervals": total_expected_intervals,
                "total_missing_energy_intervals": total_missing_energy,
                "total_missing_occupancy_intervals": total_missing_occupancy,
                "missing_energy_percentage": round((total_missing_energy / (total_expected_intervals * len(area_keys))) * 100, 2) if (total_expected_intervals > 0 and len(area_keys) > 0) else 0,
                "missing_occupancy_percentage": round((total_missing_occupancy / (total_expected_intervals * len(area_keys))) * 100, 2) if (total_expected_intervals > 0 and len(area_keys) > 0) else 0,
                "area_analysis": area_analysis
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
