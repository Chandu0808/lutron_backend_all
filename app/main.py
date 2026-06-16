from fastapi import FastAPI
from app.scheduler import scheduler, load_all_schedules
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import multiprocessing

from app.database.session import Base, engine
from app.models import *  # Ensure all models are loaded
from app.api.api_router import api_router
from app.theme_data import load_theme_defaults
from app.database.migrate_zones_processor import ensure_zones_processor_scope
from app.database.migrate_fofp_marker_stretch import ensure_fofp_marker_stretch_columns
from app.database.migrate_drivers_zone_id import ensure_drivers_zone_id
from app.database.migrate_central_config import ensure_central_config_tables
from app.database.migrate_widget_titles_to_configuration import ensure_widget_title_configuration
from app.utils.definitions import (
    LEAP_PRIVATE_KEY_FILE,
    LEAP_SIGNED_CSR_FILE,
    LAP_LUTRON_ROOT_FILE
)



# -------------------- FastAPI App Setup -------------------- #
app = FastAPI()

# -------------------- Import Background Tasks -------------------- #
# ===== ENABLE/DISABLE LISTENER PROCESS HERE =====
from app.listener import listener_process_entrypoint

# ===== ENABLE/DISABLE ENERGY LOGGER PROCESS HERE =====
from app.energy_logger import energy_logger_process_entrypoint

# ===== ENABLE/DISABLE LOADCONTROLLER LISTENER PROCESS HERE =====
from app.loadcontroller_listener import loadcontroller_listener_entrypoint

# -------------------- Background Processes (Comment individually to disable) -------------------- #
listener_process = multiprocessing.Process(target=listener_process_entrypoint, daemon=True)
energy_logger_process = multiprocessing.Process(target=energy_logger_process_entrypoint, daemon=True)
loadcontroller_listener_process = multiprocessing.Process(target=loadcontroller_listener_entrypoint, daemon=True)

def _energy_logger_manual() -> bool:
    """True when manual energy logger is enabled (same logic as listener)."""
    v = (os.getenv("energy_logger_manual") or os.getenv("energy_logger_mannual") or "").strip().lower()
    return v in ("true", "1", "yes")


# -------------------- Startup -------------------- #
@app.on_event("startup")
async def on_startup():
    # -------------------- Database Initialization -------------------- #
    Base.metadata.create_all(bind=engine)
    ensure_zones_processor_scope(engine)
    ensure_fofp_marker_stretch_columns(engine)
    ensure_drivers_zone_id(engine)
    ensure_central_config_tables(engine)
    ensure_widget_title_configuration(engine)
    load_theme_defaults()

    # ===== ENERGY LOGGER MODE (visible when running uvicorn) =====
    if _energy_logger_manual():
        print("[Startup] Manual energy logger: ON (zone/area power from zones + max_power/high_end_trim)")
    else:
        print("[Startup] Manual energy logger: OFF (normal – area power from processor)")

    # ===== START LISTENER PROCESS =====
    try:
        if not listener_process.is_alive():
            listener_process.start()
            print("[Startup] Listener process started")
    except Exception as e:
        print(f"[Listener Startup Error] {e}")

    # ===== START ENERGY LOGGER PROCESS =====
    try:
        if not energy_logger_process.is_alive():
            energy_logger_process.start()
            print("[Startup] Energy logger process started")
    except Exception as e:
        print(f"[Energy Logger Startup Error] {e}")

    # ===== START LOADCONTROLLER LISTENER PROCESS =====
    try:
        if not loadcontroller_listener_process.is_alive():
            loadcontroller_listener_process.start()
            print("[Startup] LoadController listener process started")
    except Exception as e:
        print(f"[LoadController Listener Startup Error] {e}")

    # ===== START APScheduler =====
    try:
        load_all_schedules()
        if not scheduler.running:
            scheduler.start()
            print("[Startup] Scheduler started")
    except Exception as e:
        print(f"[Scheduler Startup Error] {e}")


# -------------------- Shutdown -------------------- #
@app.on_event("shutdown")
async def on_shutdown():
    # ===== STOP LISTENER PROCESS =====
    try:
        if listener_process.is_alive():
            listener_process.terminate()
            listener_process.join(timeout=5)
            print("[Shutdown] Listener process stopped")
    except Exception as e:
        print(f"[Listener Shutdown Error] {e}")

    # ===== STOP ENERGY LOGGER PROCESS =====
    try:
        if energy_logger_process.is_alive():
            energy_logger_process.terminate()
            energy_logger_process.join(timeout=5)
            print("[Shutdown] Energy logger process stopped")
    except Exception as e:
        print(f"[Energy Logger Shutdown Error] {e}")

    # ===== STOP LOADCONTROLLER LISTENER PROCESS =====
    try:
        if loadcontroller_listener_process.is_alive():
            loadcontroller_listener_process.terminate()
            loadcontroller_listener_process.join(timeout=5)
            print("[Shutdown] LoadController listener process stopped")
    except Exception as e:
        print(f"[LoadController Listener Shutdown Error] {e}")

    # ===== STOP APScheduler =====
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            print("[Shutdown] Scheduler stopped")
    except Exception as e:
        print(f"[Scheduler Shutdown Error] {e}")


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

# -------------------- Static File Mounts -------------------- #
STATIC_ROOT = os.path.dirname(__file__)
BACKGROUND_IMAGE_DIR = os.path.join(STATIC_ROOT, "background_image")
LOGO_IMAGE_DIR = os.path.join(STATIC_ROOT, "logo_image")
HELP_FILES_DIR = os.path.join(STATIC_ROOT, "help_files")
os.makedirs(BACKGROUND_IMAGE_DIR, exist_ok=True)
os.makedirs(LOGO_IMAGE_DIR, exist_ok=True)
os.makedirs(HELP_FILES_DIR, exist_ok=True)

app.mount("/floor_plans", StaticFiles(directory="app/floor_plans"), name="floor_plans")
app.mount("/background_image", StaticFiles(directory=BACKGROUND_IMAGE_DIR), name="background_image")
app.mount("/logo_image", StaticFiles(directory=LOGO_IMAGE_DIR), name="logo_image")
app.mount("/help_files", StaticFiles(directory=HELP_FILES_DIR), name="help_files")

# -------------------- API Router -------------------- #
app.include_router(api_router)
