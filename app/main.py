from fastapi import FastAPI
from app.scheduler import scheduler, load_all_schedules
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.database.session import Base, engine
from app.models import *  # Ensure all models are loaded
from app.api.api_router import api_router
from app.theme_data import load_theme_defaults
from app.database.migrate_zones_processor import ensure_zones_processor_scope
from app.database.migrate_fofp_marker_stretch import ensure_fofp_marker_stretch_columns
from app.database.migrate_drivers_zone_id import ensure_drivers_zone_id
from app.database.migrate_alert_area_path import ensure_alert_area_path_columns
from app.database.migrate_system_key import ensure_system_key_schema
from app.database.migrate_central_config import ensure_central_config_tables
from app.database.migrate_widget_titles_to_configuration import ensure_widget_title_configuration
from app.database.migrate_floor_sort_order import ensure_floor_sort_order_column
from app.database.migrate_variant_config import (
    ensure_variant_config_tables,
    seed_variant_config_defaults,
)
from app.database.migrate_monitoring import ensure_monitoring_schema
from app.monitoring.flags import is_monitoring_environment_enabled
from app.installation_config import is_energy_logger_manual
from app.monitoring.bootstrap import bootstrap_monitoring
from app.monitoring.service import MonitoringService, set_monitoring_service
from app.monitoring import instrumentation as monitoring_instrumentation
from app.monitoring.watchdog import (
    MonitoringWatchdog,
    set_monitoring_watchdog,
)
from app.monitoring import lifecycle_hooks as monitoring_lifecycle
from app.monitoring.http_metrics import (
    install_http_metrics_middleware,
    start_http_metrics,
    stop_http_metrics,
)
from app.monitoring.alerts.engine import start_alert_engine, stop_alert_engine
from app.monitoring.analytics.rollup_engine import (
    start_analytics_engine,
    stop_analytics_engine,
)
from app.monitoring.retention_job import start_retention_job, stop_retention_job
from app.monitoring.config_validation import log_config_validation
from app.monitoring.diagnostics import (
    build_startup_diagnostics,
    log_self_check,
    log_startup_diagnostics,
    run_self_check,
)
from app.monitoring.runtime_bridge import start_runtime_bridge, stop_runtime_bridge
from app.utils.definitions import (
    LEAP_PRIVATE_KEY_FILE,
    LEAP_SIGNED_CSR_FILE,
    LAP_LUTRON_ROOT_FILE
)
from app.runtime import ProcessDescriptor, RuntimeSupervisor
from app.runtime.service_integration import ServiceIntegration
from app.listener import listener_process_entrypoint
from app.energy_logger import energy_logger_process_entrypoint
from app.loadcontroller_listener import loadcontroller_listener_entrypoint
from app.heatmap.live_hub import heatmap_live_hub
from app.api.routes import heatmap_ws



# -------------------- FastAPI App Setup -------------------- #
app = FastAPI()

_monitoring_service = None
_monitoring_watchdog = None
_runtime_bridge = None

# Ownership + health detection + policy/backoff restart for child processes.
_runtime_supervisor = RuntimeSupervisor()
_runtime_supervisor.register(
    ProcessDescriptor(
        name="listener",
        display_name="LEAP Listener",
        entrypoint=listener_process_entrypoint,
        shutdown_timeout=5.0,
        restart_policy="on_failure",
        daemon=False,
    )
)
_runtime_supervisor.register(
    ProcessDescriptor(
        name="energy_logger",
        display_name="Energy Logger",
        entrypoint=energy_logger_process_entrypoint,
        shutdown_timeout=5.0,
        lock_aware=True,
        restart_policy="on_failure",
        daemon=False,
    )
)
_runtime_supervisor.register(
    ProcessDescriptor(
        name="loadcontroller_listener",
        display_name="LoadController Listener",
        entrypoint=loadcontroller_listener_entrypoint,
        shutdown_timeout=5.0,
        restart_policy="on_failure",
        daemon=False,
    )
)

_service_integration = ServiceIntegration(
    event_bus=_runtime_supervisor.event_bus,
)


def _energy_logger_manual() -> bool:
    """True when manual energy logger is enabled (DB setting with env fallback)."""
    return is_energy_logger_manual()


# -------------------- Startup -------------------- #
@app.on_event("startup")
async def on_startup():
    global _monitoring_service, _monitoring_watchdog, _runtime_bridge

    try:
        _service_integration.prepare_startup()
        mode = _service_integration.execution_mode().value
        print(f"[Startup] Execution mode: {mode}")
    except Exception as e:
        print(f"[Service Integration Startup Error] {e}")

    # -------------------- Database Initialization -------------------- #
    Base.metadata.create_all(bind=engine)
    ensure_zones_processor_scope(engine)
    ensure_fofp_marker_stretch_columns(engine)
    ensure_drivers_zone_id(engine)
    ensure_alert_area_path_columns(engine)
    ensure_system_key_schema(engine)
    ensure_central_config_tables(engine)
    ensure_variant_config_tables(engine)
    ensure_widget_title_configuration(engine)
    ensure_floor_sort_order_column(engine)
    load_theme_defaults()
    seed_variant_config_defaults()

    if is_monitoring_environment_enabled():
        try:
            ensure_monitoring_schema(engine)
            report = bootstrap_monitoring()
            if report.ok:
                print(
                    "[Startup] Monitoring bootstrap OK "
                    f"(components={report.components_seeded}, jobs={report.jobs_seeded}, "
                    f"metrics={report.metrics_seeded}, alert_rules={report.alert_rules_seeded})"
                )
            else:
                print(f"[Startup] Monitoring bootstrap failed: {report.error}")
            _monitoring_service = MonitoringService()
            _monitoring_service.start()
            monitoring_instrumentation.attach(_monitoring_service)
            set_monitoring_service(_monitoring_service)
            monitoring_lifecycle.emit_startup()
            _monitoring_watchdog = MonitoringWatchdog(service=_monitoring_service)
            _monitoring_watchdog.start()
            set_monitoring_watchdog(_monitoring_watchdog)
            if start_http_metrics() is not None:
                print("[Startup] Monitoring HTTP metrics started")
            if start_alert_engine():
                print("[Startup] Monitoring alert engine scheduled")
            if start_analytics_engine():
                print("[Startup] Monitoring analytics rollup scheduled")
            if start_retention_job():
                print("[Startup] Monitoring retention job scheduled")
            log_config_validation()
            diag = build_startup_diagnostics(engine=engine)
            log_startup_diagnostics(diag)
            log_self_check(run_self_check(engine=engine))
            print("[Startup] Monitoring service and watchdog started")
            try:
                _runtime_bridge = start_runtime_bridge(
                    service=_monitoring_service,
                    event_bus=_runtime_supervisor.event_bus,
                    supervisor=_runtime_supervisor,
                )
                print("[Startup] Monitoring runtime bridge attached")
            except Exception as bridge_err:
                print(f"[Monitoring Runtime Bridge Error] {bridge_err}")
        except Exception as e:
            print(f"[Monitoring Startup Error] {e}")

    if _energy_logger_manual():
        print("[Startup] Manual energy logger: ON (zone/area power from zones + max_power/high_end_trim)")
    else:
        print("[Startup] Manual energy logger: OFF (normal – area power from processor)")

    try:
        _runtime_supervisor.start()
        results = _runtime_supervisor.start_all()
        labels = {
            "listener": "Listener",
            "energy_logger": "Energy logger",
            "loadcontroller_listener": "LoadController listener",
        }
        for name, ok in results.items():
            label = labels.get(name, name)
            if ok:
                print(f"[Startup] {label} process started")
            else:
                child = _runtime_supervisor.get_child(name)
                err = child.status().error if child else "unknown"
                print(f"[Startup] {label} process not started: {err}")
        _runtime_supervisor.start_monitor()
        print("[Startup] Runtime health monitor started")
    except Exception as e:
        print(f"[Runtime Supervisor Startup Error] {e}")

    try:
        load_all_schedules()
        if not scheduler.running:
            scheduler.start()
            print("[Startup] Scheduler started")
    except Exception as e:
        print(f"[Scheduler Startup Error] {e}")

    try:
        import asyncio

        heatmap_live_hub.start(asyncio.get_running_loop())
        print("[Startup] Heatmap live WebSocket hub started")
    except Exception as e:
        print(f"[Heatmap Live Startup Error] {e}")


# -------------------- Shutdown -------------------- #
@app.on_event("shutdown")
async def on_shutdown():
    try:
        _service_integration.prepare_shutdown(reason="fastapi_shutdown")
    except Exception as e:
        print(f"[Service Integration Shutdown Error] {e}")

    global _monitoring_service, _monitoring_watchdog, _runtime_bridge
    try:
        stop_runtime_bridge()
        _runtime_bridge = None
        stop_retention_job()
        stop_analytics_engine()
        stop_alert_engine()
        stop_http_metrics()
        if _monitoring_watchdog is not None:
            _monitoring_watchdog.stop()
            set_monitoring_watchdog(None)
            _monitoring_watchdog = None
            print("[Shutdown] Monitoring watchdog stopped")
        monitoring_lifecycle.emit_shutdown()
        if _monitoring_service is not None:
            _monitoring_service.stop()
            set_monitoring_service(None)
            _monitoring_service = None
            print("[Shutdown] Monitoring service stopped")
        monitoring_instrumentation.detach()
    except Exception as e:
        print(f"[Monitoring Shutdown Error] {e}")

    try:
        heatmap_live_hub.stop()
        print("[Shutdown] Heatmap live WebSocket hub stopped")
    except Exception as e:
        print(f"[Heatmap Live Shutdown Error] {e}")

    try:
        _runtime_supervisor.shutdown()
        print("[Shutdown] Runtime supervisor stopped")
    except Exception as e:
        print(f"[Runtime Supervisor Shutdown Error] {e}")

    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            print("[Shutdown] Scheduler stopped")
    except Exception as e:
        print(f"[Scheduler Shutdown Error] {e}")

    try:
        _service_integration.finalize_shutdown(reason="fastapi_shutdown")
    except Exception as e:
        print(f"[Service Integration Finalize Error] {e}")


# -------------------- SSL Certificate Validation -------------------- #
missing_files = [
    f for f in [LEAP_PRIVATE_KEY_FILE, LEAP_SIGNED_CSR_FILE, LAP_LUTRON_ROOT_FILE]
    if not os.path.isfile(f)
]
if missing_files:
    raise RuntimeError("Missing certificate files:\n" + "\n".join(missing_files))

# -------------------- CORS Setup -------------------- #
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_http_metrics_middleware(app)

# -------------------- Static File Mounts -------------------- #
STATIC_ROOT = os.path.dirname(__file__)
BACKGROUND_IMAGE_DIR = os.path.join(STATIC_ROOT, "background_image")
LOGO_IMAGE_DIR = os.path.join(STATIC_ROOT, "logo_image")
HELP_FILES_DIR = os.path.join(STATIC_ROOT, "help_files")
os.makedirs(BACKGROUND_IMAGE_DIR, exist_ok=True)
os.makedirs(LOGO_IMAGE_DIR, exist_ok=True)
os.makedirs(HELP_FILES_DIR, exist_ok=True)

app.mount("/background_image", StaticFiles(directory=BACKGROUND_IMAGE_DIR), name="background_image")
app.mount("/logo_image", StaticFiles(directory=LOGO_IMAGE_DIR), name="logo_image")
app.mount("/help_files", StaticFiles(directory=HELP_FILES_DIR), name="help_files")
# Floor plans are served only via authenticated GET /floor/{floor_id}/plan
# (see app.api.routes.floor.download_floor_plan). Do not mount /floor_plans publicly.

# -------------------- API Router -------------------- #
app.include_router(heatmap_ws.router)
app.include_router(api_router)
