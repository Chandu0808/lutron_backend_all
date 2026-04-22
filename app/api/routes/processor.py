# app/api/routes/processor.py

from fastapi import APIRouter, Depends, HTTPException, Query, File, UploadFile
from fastapi.responses import StreamingResponse, PlainTextResponse
from sqlalchemy.orm import Session
from zeroconf import Zeroconf, ServiceBrowser
from typing import List
import time
from datetime import datetime
import ipaddress
import subprocess
import sys

from app.dependencies.auth import get_current_user
from app.database.session import SessionLocal
from app.models.processor import Processor
from app.schemas.processor import ProcessorOut, ProcessorListAllOut
from app.crud.processor import (
    MyListener, 
    ensure_processor_table,
    get_processor_cert_dir,
    ensure_processor_cert_dir,
    verify_lap_certificates_in_dir,
    create_lap_chain_in_dir,
    generate_leap_keys_for_processor,
    perform_lap_handshake_for_processor,
    save_leap_certificates_to_dir
)
from app.crud.processor_leaf import get_leaf_areas_csv
from app.crud.area_coord import upload_area_coordinates
from app.models.user_model import User
from app.utils.lutron_helpers import is_processor_reachable
from app.utils.json_connection import create_ssl_connection, send_json, recv_json


router = APIRouter()


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _parse_ipv4(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid IPv4 address") from e
    if not isinstance(ip, ipaddress.IPv4Address):
        raise HTTPException(status_code=400, detail="Invalid IPv4 address")
    return str(ip)


def _spawn_windows_ping_terminal(ipv4: str) -> None:
    """
    Open a new interactive CMD window and run continuous ping.
    Output is shown only in that window, not in the API response.
    """
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(
        ["cmd.exe", "/c", "start", "cmd.exe", "/k", "ping", ipv4, "-t"],
        creationflags=creationflags,
        close_fds=True,
    )


# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Enrichment helper
def enrich_processor_details(db: Session, processor: Processor, ip: str):
    """Populate missing processor details by reading /project and /device/{id} via LEAP SSL socket."""
    try:
        with create_ssl_connection(ip, processor.mac, processor.system, processor_ipv4=ip) as sock:
            # 1. Read /project
            send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": "/project"}})
            project_resp = recv_json(sock)

            devices = (
                project_resp.get("Body", {})
                .get("Project", {})
                .get("MasterDeviceList", {})
                .get("Devices", [])
            )
            for dev in devices:
                dev_href = dev.get("href")
                if not dev_href:
                    continue

                # 2. Read each /device/{id}
                send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": dev_href}})
                dev_resp = recv_json(sock)
                device_body = dev_resp.get("Body", {}).get("Device", {})

                macs = [ni.get("MACAddress") for ni in device_body.get("NetworkInterfaces", [])]
                if processor.mac in macs:
                    processor.associated_area = device_body.get("AssociatedArea", {}).get("href")
                    processor.device_code = device_body.get("href")
                    processor.model_number = device_body.get("ModelNumber")

                    installed = device_body.get("FirmwareImage", {}).get("Installed")
                    if installed:
                        processor.installed_at = datetime(
                            installed["Year"],
                            installed["Month"],
                            installed["Day"],
                            installed["Hour"],
                            installed["Minute"],
                            installed["Second"],
                        )
                    db.commit()
                    break
    except Exception:
        # fail silently, discovery must not break
        pass


# Shared logic for processor discovery
def perform_processor_discovery(db: Session) -> list[str]:
    ensure_processor_table()

    listener = MyListener()
    zeroconf = Zeroconf()
    ServiceBrowser(zeroconf, "_lutron._tcp.local.", listener)

    try:
        time.sleep(5)
    finally:
        zeroconf.close()

    found_serials = []

    for device in listener.devices:
        device["status"] = "active"
        found_serials.append(device["serial"])

        existing = db.query(Processor).filter_by(serial=device["serial"]).first()
        if existing:
            for key, value in device.items():
                setattr(existing, key, value)
            processor = existing
        else:
            processor = Processor(**device)
            db.add(processor)
            db.flush()  # get id for enrichment

        # ---- Enrichment step ----
        enrich_processor_details(db, processor, device["ipv4"])

    db.commit()
    return found_serials


@router.get("/discover", response_model=List[ProcessorOut])  # processor discovery
def discover_lutron(db: Session = Depends(get_db)):
    found_serials = perform_processor_discovery(db)
    if not found_serials:
        raise HTTPException(status_code=404, detail="No processor found")
    return db.query(Processor).filter(Processor.serial.in_(found_serials)).all()


@router.get("/get/{processor_id}", response_model=ProcessorOut)
def get_processor_by_id(
    processor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    processor = db.query(Processor).filter(Processor.id == processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")
    return processor


@router.get("/list", response_model=List[ProcessorOut])
def list_all_processors(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
) -> List[ProcessorOut]:
    perform_processor_discovery(db)
    processors = db.query(Processor).filter_by(status="active", handshake_status=True).all()

    reachable_processors = [
        p for p in processors if is_processor_reachable(p.ipv4)
    ]

    if not reachable_processors:
        raise HTTPException(status_code=503, detail="No processor available")

    return reachable_processors


@router.get("/list_all", response_model=List[ProcessorListAllOut])
def list_all_processors_rows(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> List[ProcessorListAllOut]:
    """
    Return all rows in the processor table (no discovery, no filtering).
    """
    return db.query(Processor).all()


@router.post("/toggle_handshake_status")
def toggle_handshake_status(
    processor_id: int = Query(..., description="Processor ID to toggle handshake status"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    processor = db.query(Processor).filter(Processor.id == processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")

    if processor.handshake_status is None:
        raise HTTPException(status_code=400, detail="do handshake first to enable this")

    processor.handshake_status = not processor.handshake_status
    db.commit()
    db.refresh(processor)

    return {"processor_id": processor.id, "handshake_status": processor.handshake_status}


@router.post("/ping_terminal", response_class=PlainTextResponse)
def ping_terminal_popup(
    processor_id: int = Query(..., description="Processor ID whose IPv4 will be pinged"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Windows only: opens a new CMD window running `ping <ipv4> -t`.
    The HTTP response is plain text `ok` (no ping output in the response body).
    """
    if not _is_windows():
        raise HTTPException(
            status_code=501,
            detail="Ping terminal popup is only supported when the API runs on Windows.",
        )

    processor = db.query(Processor).filter(Processor.id == processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")
    if not processor.ipv4:
        raise HTTPException(status_code=400, detail="Processor IPv4 address not available")

    ipv4 = _parse_ipv4(processor.ipv4)

    try:
        _spawn_windows_ping_terminal(ipv4)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to start ping window: {e}") from e

    return "ok"


@router.get("/leaf_areas", response_class=StreamingResponse)
def download_leaf_areas_csv(
    processor_id: int = Query(..., description="Processor ID"),
    root_area: str = Query(None, include_in_schema=False),
    root_subarea: str = Query(None, include_in_schema=False),
    root_sub_subarea: str = Query(None, include_in_schema=False),
    db: Session = Depends(get_db)
):
    return get_leaf_areas_csv(
        processor_id=processor_id,
        db=db,
        root_area_filter=root_area,
        root_subarea_filter=root_subarea,
        root_sub_subarea_filter=root_sub_subarea
    )


@router.post("/area_coord")
def create_area_coord(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    return upload_area_coordinates(file, db)


@router.post("/processor_handshake")
def processor_handshake(
    processor_id: int = Query(..., description="Processor ID to perform handshake with"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Perform certificate handshake with a specific Lutron processor.
    
    This endpoint:
    1. Fetches processor details from database
    2. Creates/reuses processor-specific certificate folder
    3. Performs LAP handshake (requires physical button press on processor)
    4. Generates and saves LEAP certificates
    5. Updates handshake_status in database
    
    Note: This requires physical access to press the button on the processor
    within 120 seconds of starting the handshake.
    """
    try:
        # Step 1: Fetch processor from database
        processor = db.query(Processor).filter(Processor.id == processor_id).first()
        if not processor:
            raise HTTPException(status_code=404, detail=f"Processor with ID {processor_id} not found")
        
        # Step 2: Validate processor has IPv4
        if not processor.ipv4:
            raise HTTPException(
                status_code=400, 
                detail="Processor IPv4 address not available. Please run discovery first."
            )
        
        # Step 3: Setup processor certificate directory
        success, message = ensure_processor_cert_dir(processor.ipv4)
        if not success:
            processor.handshake_status = False
            db.commit()
            raise HTTPException(status_code=500, detail=message)
        
        processor_cert_dir = get_processor_cert_dir(processor.ipv4)
        
        # Step 4: Verify LAP certificates exist in processor directory
        success, message = verify_lap_certificates_in_dir(processor_cert_dir)
        if not success:
            processor.handshake_status = False
            db.commit()
            raise HTTPException(
                status_code=400, 
                detail=f"{message}. Please ensure base LAP certificates exist in app/certificates/"
            )
        
        # Step 5: Create LAP chain in processor directory
        success, message = create_lap_chain_in_dir(processor_cert_dir)
        if not success:
            processor.handshake_status = False
            db.commit()
            raise HTTPException(status_code=500, detail=message)
        
        # Step 6: Generate LEAP keys
        private_key, csr, success, message = generate_leap_keys_for_processor()
        if not success:
            processor.handshake_status = False
            db.commit()
            raise HTTPException(status_code=500, detail=message)
        
        # Step 7: Prepare processor dict for handshake
        processor_dict = {
            'ipv4': processor.ipv4,
            'server': processor.server,
            'serial': processor.serial,
            'mac': processor.mac,
            'system': processor.system
        }
        
        # Step 8: Perform LAP handshake (requires physical button press)
        certs, success, message = perform_lap_handshake_for_processor(
            processor_dict, 
            private_key, 
            csr, 
            processor_cert_dir
        )
        
        if not success:
            processor.handshake_status = False
            db.commit()
            
            # Check if it's a timeout error
            if "timeout" in message.lower() or "button" in message.lower():
                raise HTTPException(status_code=408, detail=message)
            else:
                raise HTTPException(status_code=500, detail=message)
        
        # Step 9: Save LEAP certificates
        success, message = save_leap_certificates_to_dir(certs, processor_cert_dir)
        if not success:
            processor.handshake_status = False
            db.commit()
            raise HTTPException(status_code=500, detail=message)
        
        # Step 10: Update database - handshake successful
        processor.handshake_status = True
        db.commit()
        
        # Return success response
        return {
            "status": "success",
            "message": "Certificate handshake completed successfully",
            "processor_id": processor_id,
            "processor_ipv4": processor.ipv4,
            "processor_serial": processor.serial,
            "certificate_directory": processor_cert_dir,
            "handshake_status": True
        }
    
    except HTTPException:
        # Re-raise HTTPException as-is
        raise
    
    except Exception as e:
        # Catch any unexpected errors
        if processor:
            processor.handshake_status = False
            db.commit()
        
        raise HTTPException(
            status_code=500, 
            detail=f"Unexpected error during handshake: {str(e)}"
        )
