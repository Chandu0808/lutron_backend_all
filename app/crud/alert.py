import json
import os
import logging
from sqlalchemy.orm import Session
from sqlalchemy import inspect
from datetime import datetime

from app.models.sensors_and_modules import SensorAndModule
from app.models.area import Area
from app.models.processor import Processor
from app.models.drivers import Driver
from app.utils.json_connection import connect_to_processor, send_json, recv_json
from app.database.session import SessionLocal


# ------------------- Device Refresh Logger Setup ------------------- #
def setup_device_refresh_logger():
    """Setup dedicated logger for device refresh operations with console output only (file logging disabled)"""
    logger = logging.getLogger("device_refresh")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # File logging disabled - removed file handler and logs directory creation
    # Ensure logs directory exists
    # logs_dir = "logs"
    # os.makedirs(logs_dir, exist_ok=True)
    
    # File handler - daily log file
    # date_str = datetime.utcnow().strftime("%Y-%m-%d")
    # log_file = os.path.join(logs_dir, f"device_refresh_{date_str}.log")
    # file_handler = logging.FileHandler(log_file, encoding='utf-8')
    # file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # file_handler.setFormatter(file_formatter)
    # logger.addHandler(file_handler)
    
    # Console handler - also log to console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(file_formatter)
    logger.addHandler(console_handler)
    
    return logger

# Initialize logger
device_refresh_logger = setup_device_refresh_logger()


# ------------------- Alert Timestamp Logic ------------------- #
def update_alert_timestamps(record, new_alert_status):
    """
    Update alert timestamps based on status changes.
    
    Args:
        record: Database record with alert_status, reported_time, solved_time fields
        new_alert_status: New alert status ("ok", "not_ok", "unknown")
    """
    current_time = datetime.utcnow()
    
    # Handle different model types - get the appropriate status field
    if hasattr(record, 'alert_status'):
        old_status = record.alert_status
    elif hasattr(record, 'ping_status'):
        # For Processor model, map ping_status to alert_status format
        old_status = "ok" if record.ping_status == "ok" else "not_ok"
    else:
        old_status = "unknown"
    
    # Normalize old status values (handle legacy "okay"/"not_okay" values)
    if old_status in ["okay", "Active"]:
        old_status = "ok"
    elif old_status in ["not_okay", "Resolved"]:
        old_status = "not_ok"
    
    # Normalize new status values
    if new_alert_status in ["okay", "Active"]:
        new_alert_status = "ok"
    elif new_alert_status in ["not_okay", "Resolved"]:
        new_alert_status = "not_ok"
    
    # Alert first appears (ok -> not_ok) - Set reported_time
    if old_status == "ok" and new_alert_status == "not_ok":
        record.reported_time = current_time
        record.solved_time = None
    
    # Alert is resolved (not_ok -> ok) - Set solved_time
    elif old_status == "not_ok" and new_alert_status == "ok":
        record.solved_time = current_time
        # Keep reported_time unchanged
    
    # Alert reappears after being solved (ok -> not_ok again) - Start new cycle
    elif old_status == "ok" and new_alert_status == "not_ok" and record.solved_time is not None:
        record.reported_time = current_time  # New cycle starts - reset reported_time
        record.solved_time = None
    
    # Alert persists (not_ok -> not_ok) - no timestamp changes
    # Alert remains ok (ok -> ok) - no timestamp changes
    
    # Always update created_at (last_updated_time)
    record.created_at = current_time
    
    # Update the appropriate status field based on model type
    if hasattr(record, 'alert_status'):
        record.alert_status = new_alert_status
    elif hasattr(record, 'ping_status'):
        # For Processor model, update ping_status
        record.ping_status = new_alert_status


# ------------------- LEAP Helpers ------------------- #
def get_device_info(sock, href):
    send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": href}})
    resp = recv_json(sock)
    return resp.get("Body", {}).get("Device", {})


# ------------------- Raw Data Logging ------------------- #
def log_raw_device_availability_data(processor_id: int, ip: str, raw_response: dict, statuses: list):
    """Log raw device availability API response before processing"""
    # File logging disabled - all code commented out
    # try:
    #     logs_dir = "logs"
    #     if not os.path.exists(logs_dir):
    #         os.makedirs(logs_dir)
    #     
    #     timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    #     date_str = datetime.utcnow().strftime("%Y-%m-%d")
    #     
    #     log_data = {
    #         "timestamp": timestamp,
    #         "processor_id": processor_id,
    #         "processor_ip": ip,
    #         "raw_api_response": raw_response,
    #         "device_availability_statuses": statuses,
    #         "status_count": len(statuses)
    #     }
    #     
    #     log_file = os.path.join(logs_dir, f"raw_device_availability_{date_str}.log")
    #     with open(log_file, "a", encoding="utf-8") as f:
    #         f.write(json.dumps(log_data, indent=2, ensure_ascii=False))
    #         f.write("\n" + "="*80 + "\n")
    # except Exception as e:
    #     print(f"Failed to log raw device availability data: {e}")
    pass


def log_raw_device_data(processor_id: int, ip: str, raw_device_data: list):
    """Log raw device info data before database processing"""
    # File logging disabled - all code commented out
    # try:
    #     logs_dir = "logs"
    #     if not os.path.exists(logs_dir):
    #         os.makedirs(logs_dir)
    #     
    #     timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    #     date_str = datetime.utcnow().strftime("%Y-%m-%d")
    #     
    #     log_data = {
    #         "timestamp": timestamp,
    #         "processor_id": processor_id,
    #         "processor_ip": ip,
    #         "device_count": len(raw_device_data),
    #         "raw_devices": raw_device_data
    #     }
    #     
    #     log_file = os.path.join(logs_dir, f"raw_sensors_and_modules_{date_str}.log")
    #     with open(log_file, "a", encoding="utf-8") as f:
    #         f.write(json.dumps(log_data, indent=2, ensure_ascii=False))
    #         f.write("\n" + "="*80 + "\n")
    # except Exception as e:
    #     print(f"Failed to log raw device data: {e}")
    pass


# ------------------- Device Discovery ------------------- #
def discover_and_upsert_all_devices(db: Session, ip: str, mac: str, system: str):
    """
    Discover all devices from processor via /device/status/availability
    and upsert into sensors_and_modules table.
    """
    # Get processor_id from IP address
    processor = db.query(Processor).filter(Processor.ipv4 == ip).first()
    if not processor:
        raise Exception(f"Processor with IP {ip} not found in database")
    
    processor_id = processor.id
    
    sock = None
    try:
        sock = connect_to_processor(ip=ip, mac=mac, system=system, processor_ipv4=ip)
        if not sock:
            raise Exception("Could not connect to processor")

        send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": "/device/status/availability"}})
        resp = recv_json(sock)
        statuses = resp.get("Body", {}).get("DeviceAvailabilityStatuses", []) if resp else []

        # Log raw API response before processing
        # log_raw_device_availability_data(processor_id, ip, resp, statuses)

        results = []
        raw_device_data = []  # Collect raw device data for logging
        
        for dev in statuses:
            try:
                href = dev.get("Device", {}).get("href") if isinstance(dev.get("Device"), dict) else dev.get("Device")
                if not href:
                    continue

                device_code = int(href.strip("/").split("/")[-1])
                availability = dev.get("Availability", "Unknown")

                # Read full device info - wrap in try/except to handle individual device failures
                try:
                    dev_info = get_device_info(sock, href)
                except Exception as e:
                    # Log but continue with next device
                    print(f"[Device Discovery] Failed to get device info for {href} on processor {processor_id}: {e}")
                    continue
                    
                if not dev_info:
                    continue

                # Store raw device info before processing
                raw_device_data.append({
                    "device_availability_status": dev,  # Raw status from availability endpoint
                    "device_info": dev_info,  # Raw device info from individual device endpoint
                    "processor_id": processor_id,
                    "processor_ip": ip
                })

                device_name = dev_info.get("Name") or ""
                device_type = dev_info.get("DeviceType") or ""
                serial_number = dev_info.get("SerialNumber")
                if isinstance(serial_number, int):
                    serial_number = hex(serial_number)[2:].upper()
                device_model = dev_info.get("ModelNumber")  # preserve None if missing
                addressed_state = dev_info.get("AddressedState", "")
                area_code = None
                area_id = None

                # Resolve associated area with processor context
                area_field = dev_info.get("AssociatedArea") or dev_info.get("Area")
                if isinstance(area_field, dict) and "href" in area_field:
                    area_code = str(area_field["href"].strip("/").split("/")[-1])
                    area = db.query(Area).filter(Area.code == area_code, Area.processor_id == processor_id).first()
                    if area:
                        area_id = area.id

                # Filter phantom and PN2 devices
                if "phantom" in device_type.lower() or (device_model and device_model.upper().startswith("PN2")):
                    continue

                # Map availability -> alert_status
                if availability == "Unavailable":
                    alert_status = "not_ok"
                elif availability == "Available":
                    alert_status = "ok"
                else:
                    alert_status = "unknown"

                existing = db.query(SensorAndModule).filter(
                    SensorAndModule.device_code == device_code,
                    SensorAndModule.processor_id == processor_id
                ).first()
                if existing:
                    # Update alert timestamps based on status change
                    update_alert_timestamps(existing, alert_status)
                    
                    existing.area_id = area_id
                    existing.area_code = area_code
                    existing.device_name = device_name
                    existing.serial_number = serial_number
                    existing.device_model = device_model
                    existing.device_type = device_type
                    existing.availability = availability
                    existing.alert_status = alert_status
                    existing.device_kind = "sensor_or_module"
                else:
                    # New device - set initial timestamps
                    new_device = SensorAndModule(
                        processor_id=processor_id,
                        device_code=device_code,
                        device_name=device_name,
                        serial_number=serial_number,
                        device_model=device_model,
                        device_type=device_type,
                        availability=availability,
                        alert_status=alert_status,
                        area_code=area_code,
                        area_id=area_id,
                        device_kind="sensor_or_module"
                    )
                    
                    # Set initial timestamps for new device
                    current_time = datetime.utcnow()
                    if alert_status == "not_ok":
                        new_device.reported_time = current_time  # First time alert is discovered
                        new_device.solved_time = None
                    else:
                        new_device.reported_time = None
                        new_device.solved_time = None
                    new_device.created_at = current_time  # Set initial created_at
                        
                    db.add(new_device)

                results.append({
                    "device_code": device_code,
                    "device_name": device_name,
                    "serial_number": serial_number,
                    "availability": availability,
                    "alert_status": alert_status,
                    "device_model": device_model,
                    "area_code": area_code
                })
            except Exception as e:
                # Log error for this device but continue processing other devices
                print(f"[Device Discovery] Error processing device on processor {processor_id}: {e}")
                # Rollback to clean state before continuing
                try:
                    db.rollback()
                except Exception:
                    pass
                continue

        # Log all raw device data after collection (even if commit fails)
        # log_raw_device_data(processor_id, ip, raw_device_data)

        # Commit all changes - handle IntegrityError gracefully
        try:
            db.commit()
        except Exception as commit_error:
            # Rollback on commit failure
            db.rollback()
            # If it's a duplicate key error, try to update existing records instead
            if "UniqueViolation" in str(commit_error) or "duplicate key" in str(commit_error).lower():
                # device_refresh_logger.warning(f"Duplicate key error during commit for processor {processor_id}. Attempting to update existing records...")
                # Re-process devices that failed, but this time update instead of insert
                for dev_data in raw_device_data:
                    try:
                        dev_info = dev_data.get("device_info", {})
                        device_status = dev_data.get("device_availability_status", {})
                        href = device_status.get("Device", {}).get("href") if isinstance(device_status.get("Device"), dict) else device_status.get("Device")
                        if not href:
                            continue
                        device_code = int(href.strip("/").split("/")[-1])
                        availability = device_status.get("Availability", "Unknown")
                        
                        # Map availability -> alert_status
                        if availability == "Unavailable":
                            alert_status = "not_ok"
                        elif availability == "Available":
                            alert_status = "ok"
                        else:
                            alert_status = "unknown"
                        
                        # Get existing device and update it
                        existing = db.query(SensorAndModule).filter(
                            SensorAndModule.device_code == device_code,
                            SensorAndModule.processor_id == processor_id
                        ).first()
                        
                        if existing:
                            update_alert_timestamps(existing, alert_status)
                            existing.device_name = dev_info.get("Name") or ""
                            existing.device_type = dev_info.get("DeviceType") or ""
                            serial_number = dev_info.get("SerialNumber")
                            if isinstance(serial_number, int):
                                serial_number = hex(serial_number)[2:].upper()
                            existing.serial_number = serial_number
                            existing.device_model = dev_info.get("ModelNumber")
                            existing.availability = availability
                            existing.alert_status = alert_status
                            existing.device_kind = "sensor_or_module"
                            
                            area_field = dev_info.get("AssociatedArea") or dev_info.get("Area")
                            if isinstance(area_field, dict) and "href" in area_field:
                                area_code = str(area_field["href"].strip("/").split("/")[-1])
                                area = db.query(Area).filter(Area.code == area_code, Area.processor_id == processor_id).first()
                                if area:
                                    existing.area_id = area.id
                                existing.area_code = area_code
                    except Exception:
                        pass  # Skip individual device errors during recovery
                
                # Try commit again after updates
                try:
                    db.commit()
                    # device_refresh_logger.info(f"Successfully recovered from duplicate key error for processor {processor_id}")
                except Exception as retry_error:
                    db.rollback()
                    raise Exception(f"Failed to recover from duplicate key error: {retry_error}")
            else:
                # Re-raise if it's not a duplicate key error
                raise
        
        return results
    finally:
        # Always close the socket, even if an exception occurs
        if sock:
            try:
                sock.close()
            except Exception:
                pass  # Ignore errors when closing socket


# ------------------- Raw Data Logging ------------------- #
def log_raw_table_data(db: Session):
    """
    Log raw data from sensors_and_modules and drivers tables to separate log files.
    Creates JSON log files with timestamp in logs/ directory.
    """
    # File logging disabled - all code commented out
    # try:
    #     # Create logs directory if it doesn't exist
    #     logs_dir = "logs"
    #     if not os.path.exists(logs_dir):
    #         os.makedirs(logs_dir)
    #     
    #     timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    #     date_str = datetime.utcnow().strftime("%Y-%m-%d")
    #     
    #     # Helper function to convert SQLAlchemy model to dict
    #     def model_to_dict(model_instance):
    #         """Convert SQLAlchemy model instance to dictionary"""
    #         if model_instance is None:
    #             return None
    #         result = {}
    #         for column in inspect(model_instance.__class__).columns:
    #             value = getattr(model_instance, column.name)
    #             # Convert datetime to ISO format string
    #             if isinstance(value, datetime):
    #                 result[column.name] = value.isoformat() if value else None
    #             else:
    #                 result[column.name] = value
    #         return result
    #     
    #     # Log sensors_and_modules data
    #     sensors_modules = db.query(SensorAndModule).all()
    #     sensors_modules_data = {
    #         "timestamp": timestamp,
    #         "record_count": len(sensors_modules),
    #         "records": [model_to_dict(record) for record in sensors_modules]
    #     }
    #     
    #     sensors_modules_log_file = os.path.join(logs_dir, f"sensors_and_modules_{date_str}.log")
    #     with open(sensors_modules_log_file, "a", encoding="utf-8") as f:
    #         f.write(json.dumps(sensors_modules_data, indent=2, ensure_ascii=False))
    #         f.write("\n" + "="*80 + "\n")
    #     
    #     # Log drivers data
    #     drivers = db.query(Driver).all()
    #     drivers_data = {
    #         "timestamp": timestamp,
    #         "record_count": len(drivers),
    #         "records": [model_to_dict(record) for record in drivers]
    #     }
    #     
    #     drivers_log_file = os.path.join(logs_dir, f"drivers_{date_str}.log")
    #     with open(drivers_log_file, "a", encoding="utf-8") as f:
    #         f.write(json.dumps(drivers_data, indent=2, ensure_ascii=False))
    #         f.write("\n" + "="*80 + "\n")
    #     
    #     return {
    #         "status": "success",
    #         "sensors_modules_count": len(sensors_modules),
    #         "drivers_count": len(drivers),
    #         "sensors_modules_log": sensors_modules_log_file,
    #         "drivers_log": drivers_log_file
    #     }
    # except Exception as e:
    #     return {
    #         "status": "error",
    #         "message": str(e)
    #     }
    return {
        "status": "disabled",
        "message": "File logging has been disabled"
    }


def refresh_all_devices():
    """
    Refresh processors (ping) and discover/update all devices into sensors_and_modules.
    Collect alerts for processors and devices.
    """
    db = SessionLocal()
    alerts = []   # collect all alerts here
    try:
        processors = db.query(Processor).filter_by(handshake_status=True).all()
        # device_refresh_logger.info(f"Found {len(processors)} processors with handshake_status=True")
        
        for proc in processors:
            # device_refresh_logger.info(f"Processing processor {proc.id} ({proc.serial}) at {proc.ipv4}")
            
            # ---------- Processor ping ----------
            try:
                sock = connect_to_processor(ip=proc.ipv4, mac=proc.mac, system=proc.system, processor_ipv4=proc.ipv4)
                if not sock:
                    # Update alert timestamps for processor alert
                    update_alert_timestamps(proc, "not_ok")
                    alerts.append({
                        "location": None,
                        "alert_type": "processor not responding",
                        "device_name": proc.system,
                        "serial_no": proc.serial,
                        "description": "not pingable",
                        "time": datetime.utcnow().strftime("%d-%m-%Y %H.%M")
                    })
                    # device_refresh_logger.warning(f"Processor {proc.id} not pingable")
                else:
                    send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": "/server/status/ping"}})
                    resp = recv_json(sock)
                    new_status = "ok" if resp.get("CommuniqueType") == "ReadResponse" else "not_ok"
                    # Update alert timestamps for processor alert
                    update_alert_timestamps(proc, new_status)
                    sock.close()
                proc.pinged_at = datetime.utcnow()
            except Exception as e:
                # Update alert timestamps for processor alert
                update_alert_timestamps(proc, "not_ok")
                proc.pinged_at = datetime.utcnow()
                alerts.append({
                    "location": None,
                    "alert_type": "processor not responding",
                    "device_name": proc.system,
                    "serial_no": proc.serial,
                    "description": str(e),
                    "time": datetime.utcnow().strftime("%d-%m-%Y %H.%M")
                })
                # device_refresh_logger.error(f"Error pinging processor {proc.id}: {e}")
                # Rollback any partial changes for this processor
                try:
                    db.rollback()
                except Exception:
                    pass

            # ---------- Device discovery ----------
            # Store proc attributes BEFORE processing to avoid session issues in error handler
            proc_id = proc.id
            proc_ip = proc.ipv4
            proc_system = proc.system
            proc_serial = proc.serial
            
            try:
                # device_refresh_logger.info(f"Starting device discovery for processor {proc_id}")
                devices = discover_and_upsert_all_devices(db, ip=proc_ip, mac=proc.mac, system=proc_system)
                # device_refresh_logger.info(f"Successfully discovered {len(devices)} devices for processor {proc_id}")

                # Alerts for unavailable devices
                for dev in devices:
                    if dev["alert_status"] == "not_ok":
                        area = db.query(Area).filter(Area.code == dev["area_code"], Area.processor_id == proc_id).first()
                        # Build location using {floor.name}/{area.name}
                        location = None
                        if area:
                            location_parts = []
                            if area.floor and area.floor.name:
                                location_parts.append(area.floor.name)
                            if area.name:
                                location_parts.append(area.name)
                            location = "/".join(location_parts) if location_parts else None

                        alerts.append({
                            "location": location,
                            "alert_type": "Device Not Responding",
                            "device_name": dev["device_name"],
                            "serial_no": dev.get("serial_number"),
                            "description": "",
                            "time": datetime.utcnow().strftime("%d-%m-%Y %H.%M"),
                            "model_number": dev.get("device_model")
                        })
            except Exception as e:
                # Rollback IMMEDIATELY to clean session state before accessing any attributes
                try:
                    db.rollback()
                except Exception:
                    pass
                
                # Now safe to log using stored attributes
                # device_refresh_logger.error(f"ERROR in device discovery for processor {proc_id} ({proc_ip}): {e}", exc_info=True)
                alerts.append({
                    "location": None,
                    "alert_type": "device discovery error",
                    "device_name": proc_system,
                    "serial_no": proc_serial,
                    "description": str(e),
                    "time": datetime.utcnow().strftime("%d-%m-%Y %H.%M")
                })
                # Continue to next processor - don't let one failure stop all processing

        # Log raw table data
        # log_result = log_raw_table_data(db)
        # if log_result.get("status") == "success":
        #     device_refresh_logger.info(f"Logged {log_result['sensors_modules_count']} sensors/modules and {log_result['drivers_count']} drivers")
        # else:
        #     device_refresh_logger.error(f"Failed to log raw data: {log_result.get('message')}")

        db.commit()
        # device_refresh_logger.info(f"Completed processing all processors. Total alerts: {len(alerts)}")
        return {
            "status": "success",
            "alerts": alerts
        }
    except Exception as e:
        # device_refresh_logger.error(f"FATAL ERROR: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {
            "status": "error",
            "message": str(e),
            "alerts": alerts
        }
    finally:
        db.close()


# ------------------- Test Timestamp Logic ------------------- #
def test_alert_timestamp_logic():
    """
    Test function to validate alert timestamp logic with different state transitions.
    This function can be called to verify the timestamp update logic works correctly.
    """
    from datetime import datetime
    from app.models.sensors_and_modules import SensorAndModule
    from app.models.drivers import Driver
    
    print("Testing Alert Timestamp Logic...")
    
    # Test 1: First alert (ok -> not_ok)
    print("\n1. Testing first alert (ok -> not_ok):")
    test_record = type('MockRecord', (), {
        'alert_status': 'ok',
        'reported_time': None,
        'solved_time': None,
        'created_at': None
    })()
    
    update_alert_timestamps(test_record, "not_ok")
    print(f"   reported_time set: {test_record.reported_time is not None}")
    print(f"   solved_time cleared: {test_record.solved_time is None}")
    print(f"   created_at set: {test_record.created_at is not None}")
    print(f"   alert_status: {test_record.alert_status}")
    
    # Test 2: Alert resolved (not_ok -> ok)
    print("\n2. Testing alert resolved (not_ok -> ok):")
    test_record.alert_status = "not_ok"
    test_record.solved_time = None
    old_reported_time = test_record.reported_time
    
    update_alert_timestamps(test_record, "ok")
    print(f"   reported_time unchanged: {test_record.reported_time == old_reported_time}")
    print(f"   solved_time set: {test_record.solved_time is not None}")
    print(f"   created_at updated: {test_record.created_at is not None}")
    print(f"   alert_status: {test_record.alert_status}")
    
    # Test 3: Alert reappears (ok -> not_ok after being solved)
    print("\n3. Testing alert reappears (ok -> not_ok after solved):")
    test_record.alert_status = "ok"
    test_record.solved_time = datetime.utcnow()
    
    update_alert_timestamps(test_record, "not_ok")
    print(f"   reported_time reset: {test_record.reported_time is not None}")
    print(f"   solved_time cleared: {test_record.solved_time is None}")
    print(f"   created_at updated: {test_record.created_at is not None}")
    print(f"   alert_status: {test_record.alert_status}")
    
    # Test 4: Alert persists (not_ok -> not_ok)
    print("\n4. Testing alert persists (not_ok -> not_ok):")
    old_reported_time = test_record.reported_time
    old_solved_time = test_record.solved_time
    
    update_alert_timestamps(test_record, "not_ok")
    print(f"   reported_time unchanged: {test_record.reported_time == old_reported_time}")
    print(f"   solved_time unchanged: {test_record.solved_time == old_solved_time}")
    print(f"   created_at updated: {test_record.created_at is not None}")
    print(f"   alert_status: {test_record.alert_status}")
    
    # Test 5: Alert remains ok (ok -> ok)
    print("\n5. Testing alert remains ok (ok -> ok):")
    test_record.alert_status = "ok"
    test_record.reported_time = None
    test_record.solved_time = None
    old_created_at = test_record.created_at
    
    update_alert_timestamps(test_record, "ok")
    print(f"   reported_time unchanged: {test_record.reported_time is None}")
    print(f"   solved_time unchanged: {test_record.solved_time is None}")
    print(f"   created_at updated: {test_record.created_at != old_created_at}")
    print(f"   alert_status: {test_record.alert_status}")
    
    print("\nTimestamp logic test completed!")
    return True
