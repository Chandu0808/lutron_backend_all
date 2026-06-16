from fastapi import APIRouter
from app.api.routes import (
    auth, email_settings, floor, users, processor, theme,
    full_area_status, home, dashboard_home, zone_update,
    area_group, schedule, area_tree, device, edit_scene,
    update_occupancy, quick_controls, zone,
    energy_stats, help, activity_report, widget_title, dashboard_chart_order, alert, exports, reconciliation, settings,
    area_rename,
    fofp,
    installation_config,
    widget_configuration_api,
    dashboard_layout_api,
)

api_router = APIRouter()

# -------------------- Core Modules -------------------- #
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(theme.router, prefix="/theme", tags=["lutron"])
api_router.include_router(processor.router, prefix="/processor", tags=["Processor"])
api_router.include_router(floor.router, prefix="/floor", tags=["Floor"])
api_router.include_router(home.router, prefix="/home", tags=["Home"])
api_router.include_router(dashboard_home.router, prefix="/home", tags=["Dashboard Home"])

# -------------------- Area Related -------------------- #
api_router.include_router(full_area_status.router, prefix="/area", tags=["Area"])
api_router.include_router(area_rename.router, prefix="/area", tags=["Area"])
api_router.include_router(zone_update.router, prefix="/area", tags=["Zone Update"])
api_router.include_router(zone.router, prefix="/area", tags=["Zone List"])
api_router.include_router(area_group.router, prefix="/area_group", tags=["Area group"])

# -------------------- Schedule, Occupancy, Devices -------------------- #
api_router.include_router(schedule.router, prefix="/schedule", tags=["Schedule"])  
api_router.include_router(update_occupancy.router, prefix="", tags=["Update Occupancy"])
api_router.include_router(device.router, prefix="/setting", tags=["Device Settings"])
api_router.include_router(edit_scene.router, prefix="/setting", tags=["Edit Scene"])

# -------------------- Quick Control -------------------- #
api_router.include_router(quick_controls.router, prefix="/quick_control", tags=["Quick Control"])
api_router.include_router(email_settings.router, prefix="/email", tags=["Email Settings"])

# -------------------- Dashboard / Energy Stats -------------------- #
api_router.include_router(energy_stats.router, prefix="/dashboard", tags=["Energy Stats"])
api_router.include_router(dashboard_layout_api.router, prefix="/dashboard", tags=["Configuration"])
api_router.include_router(help.router, prefix="/help", tags=["Help"])

# -------------------- Activity Report -------------------- #
api_router.include_router(activity_report.router, prefix="/activity_report", tags=["Activity Report"]) 

# -------------------- Widget title -------------------- #

api_router.include_router(widget_title.router, prefix="/widgets", tags=["Widget Titles"])
api_router.include_router(dashboard_chart_order.router, prefix="/widgets", tags=["Widget Titles"])
api_router.include_router(widget_configuration_api.router, prefix="/widgets", tags=["Configuration"])

# -------------------- Configuration -------------------- #
api_router.include_router(installation_config.router, prefix="/config", tags=["Configuration"])

# -------------------- Alerts -------------------- #
api_router.include_router(alert.router, prefix="/alert", tags=["Alerts"])

# -------------------- Settings -------------------- #
api_router.include_router(settings.router, prefix="/settings", tags=["Settings"])

# -------------------- Exports -------------------- #
api_router.include_router(exports.router, prefix="/exports", tags=["Exports"])

# -------------------- Reconciliation -------------------- #
api_router.include_router(reconciliation.router, prefix="", tags=["Reconciliation"])

# -------------------- FOFP (Floor Overlay / Floorplan Positioning) -------------------- #
api_router.include_router(fofp.router, prefix="/fofp", tags=["FOFP"])