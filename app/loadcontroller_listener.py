import ssl
import json
import asyncio
import logging
import os
from datetime import datetime
from asyncio import StreamReader, StreamWriter, TimeoutError
from app.database.session import SessionLocal
from app.models.processor import Processor
from app.models.area import Area
from app.models.events import ProcessorConnectionError
from app.models.drivers import Driver
from app.models.zone import Zone
from app.utils.definitions import (
    get_proc_hostname,
    LAP_LUTRON_ROOT_FILE,
    LEAP_SIGNED_CSR_FILE,
    LEAP_PRIVATE_KEY_FILE,
    get_processor_cert_paths,
)

CRLF = b"\r\n"
shutdown_event = asyncio.Event()

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_BASE = 0.5  # Base delay in seconds
REQUEST_TIMEOUT = 5.0  # Timeout for API requests in seconds

# ---------------------- Logger Setup ---------------------- #
def setup_driver_alert_logger():
    """Setup dedicated logger for driver alert missing data tracking with console output only"""
    logger = logging.getLogger("driver_alert_missing_data")
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # Console handler for warnings and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    return logger

# Initialize logger
driver_alert_logger = setup_driver_alert_logger()

# ---------------------- Socket Helpers ---------------------- #
async def _send_json(writer: StreamWriter, json_msg: dict):
    """Safely send JSON message with error handling"""
    try:
        msg = (json.dumps(json_msg) + "\r\n").encode("utf-8")
        writer.write(msg)
        await writer.drain()
    except Exception:
        # Re-raise to allow retry logic to handle it
        raise

async def _recv_raw(reader: StreamReader, timeout: float = REQUEST_TIMEOUT) -> list:
    """Safely receive raw messages with timeout"""
    try:
        buffer = b""
        start_time = asyncio.get_event_loop().time()
        
        while not buffer.endswith(CRLF):
            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout:
                break
                
            remaining_time = max(0.1, timeout - elapsed)  # Ensure at least 0.1s remaining
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=remaining_time)
            except TimeoutError:
                break
                
            if not chunk:
                break
            buffer += chunk
            
        messages = buffer.split(CRLF)
        return [m.decode("utf-8", errors="replace").strip() for m in messages if m.strip()]
    except Exception:
        return []

# ---------------------- Safe Parsing Helpers ---------------------- #
def safe_get_href(obj, default="/0"):
    """Safely extract href from object (handles dict, string, or None)"""
    if obj is None:
        return default
    
    # If it's already a string (href), return it
    if isinstance(obj, str):
        return obj
    
    # If it's a dict, try to get href
    if isinstance(obj, dict):
        href = obj.get("href")
        if href:
            return href if isinstance(href, str) else default
    
    return default

def safe_extract_code(href_str, default=0):
    """Safely extract numeric code from href string"""
    try:
        if not href_str or href_str == "/0":
            return default
        # Remove leading/trailing slashes and get last part
        parts = href_str.strip("/").split("/")
        if parts:
            return int(parts[-1])
    except (ValueError, AttributeError, IndexError, TypeError):
        pass
    return default

def safe_get_nested(data: dict, *keys, default=None):
    """Safely navigate nested dictionary structure"""
    if not isinstance(data, dict):
        return default
        
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current

# ---------------------- Error Mappings ---------------------- #
ERROR_MAP = {
    "E2": "MissingInAction",
    "FC": "LampFailure",
    "FD": "BackendFailure",
    "08": "InEmergencyMode",
    "E0": "UnAddressed",
    "D0": "ShortedComponent",
    "D1": "ShortedComponent",
    "D2": "ShortedComponent",
    "D3": "AirGapFailure",
    "D4": "AirGapFailure",
    "D5": "OverCurrentError",
    "D6": "OverVoltageError",
    "D7": "OverLoadError",
    "D8": "UnitWarm",
    "D9": "UnitHotScaleBackTo25Percent",
    "DA": "UnitOverheatedOutputsOff",
    "DB": "MultipleError"
}

def _resolve_driver_zone_id(db, processor_id, zone_code):
    if processor_id is None or zone_code is None:
        return None
    zone = (
        db.query(Zone)
        .filter(Zone.processor_id == processor_id, Zone.code == str(zone_code))
        .first()
    )
    return zone.id if zone else None


# ---------------------- Helper: Resolve LoadController Mapping with Full Job Retry ---------------------- #
async def resolve_loadcontroller_mapping(writer, reader, loadcontroller_code, db, processor_id):
    """
    Robustly resolve loadcontroller mapping with full-job retry logic.
    Retries ALL read requests from scratch if any critical data is missing.
    Ensures complete data retrieval by retrying the entire process.
    Returns: (area_id, area_code, zone_code, device_code, device_type, device_name)
    """
    log_context = {'processor_id': processor_id, 'loadcontroller_code': loadcontroller_code}
    
    # Full job retry - retry entire process (all read requests) if any critical data is missing
    for full_attempt in range(MAX_RETRIES):
        # Initialize variables for each full attempt
        device_code, zone_code, area_code, device_type, device_name = None, None, None, None, None
        area_id = None
        
        try:
            # Step 1: /loadcontroller/{id} with retry
            loadcontroller_data = None
            loadcontroller_responses = []
            
            for attempt in range(MAX_RETRIES):
                try:
                    await _send_json(writer, {
                        "CommuniqueType": "ReadRequest",
                        "Header": {"Url": f"/loadcontroller/{loadcontroller_code}"}
                    })
                    responses = await _recv_raw(reader, timeout=REQUEST_TIMEOUT)
                    loadcontroller_responses.extend(responses)
                    
                    for raw in responses:
                        try:
                            data = json.loads(raw)
                            lc = safe_get_nested(data, "Body", "LoadController")
                            if lc and isinstance(lc, dict):
                                loadcontroller_data = lc
                                break
                        except (json.JSONDecodeError, TypeError) as e:
                            driver_alert_logger.debug(
                                f"JSON parse error in loadcontroller response: {str(e)}",
                                extra=log_context
                            )
                            continue
                    
                    if loadcontroller_data:
                        break
                        
                except Exception as e:
                    driver_alert_logger.warning(
                        f"LoadController API call failed (attempt {attempt + 1}/{MAX_RETRIES}): {str(e)}",
                        extra=log_context
                    )
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAY_BASE * (2 ** attempt))
                    else:
                        # Final attempt failed, log all responses
                        driver_alert_logger.error(
                            f"LoadController API call failed after {MAX_RETRIES} attempts. "
                            f"Responses received: {loadcontroller_responses}",
                            extra=log_context
                        )
                        break
            
            # Parse loadcontroller data safely
            if loadcontroller_data:
                # Extract device_code
                assoc_device = loadcontroller_data.get("AssociatedDevice")
                device_href = safe_get_href(assoc_device)
                device_code = safe_extract_code(device_href)
                
                # Extract zone_code
                assoc_zone = loadcontroller_data.get("AssociatedZone")
                zone_href = safe_get_href(assoc_zone)
                zone_code = safe_extract_code(zone_href)
                
                # Extract device_name
                device_name = loadcontroller_data.get("Name")
                if device_name and not isinstance(device_name, str):
                    device_name = None
                
                # Log if critical data is missing
                if not device_code or device_code == 0:
                    driver_alert_logger.warning(
                        f"Missing device_code from LoadController response. "
                        f"AssociatedDevice: {assoc_device}, "
                        f"Full LoadController data: {json.dumps(loadcontroller_data, default=str)}",
                        extra=log_context
                    )
                if not zone_code or zone_code == 0:
                    driver_alert_logger.warning(
                        f"Missing zone_code from LoadController response. "
                        f"AssociatedZone: {assoc_zone}",
                        extra=log_context
                    )
            else:
                driver_alert_logger.error(
                    f"No LoadController data found after {MAX_RETRIES} attempts. "
                    f"All responses: {loadcontroller_responses}",
                    extra=log_context
                )
            
            # Step 2: /zone/{zone_code} with retry (to get area_code)
            if zone_code and zone_code > 0:
                zone_data = None
                zone_responses = []
                
                for attempt in range(MAX_RETRIES):
                    try:
                        await _send_json(writer, {
                            "CommuniqueType": "ReadRequest",
                            "Header": {"Url": f"/zone/{zone_code}"}
                        })
                        responses = await _recv_raw(reader, timeout=REQUEST_TIMEOUT)
                        zone_responses.extend(responses)
                        
                        for raw in responses:
                            try:
                                data = json.loads(raw)
                                zone = safe_get_nested(data, "Body", "Zone")
                                if zone and isinstance(zone, dict):
                                    zone_data = zone
                                    break
                            except (json.JSONDecodeError, TypeError) as e:
                                driver_alert_logger.debug(
                                    f"JSON parse error in zone response: {str(e)}",
                                    extra=log_context
                                )
                                continue
                        
                        if zone_data:
                            break
                            
                    except Exception as e:
                        driver_alert_logger.warning(
                            f"Zone API call failed (attempt {attempt + 1}/{MAX_RETRIES}): {str(e)}",
                            extra=log_context
                        )
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_DELAY_BASE * (2 ** attempt))
                        else:
                            driver_alert_logger.error(
                                f"Zone API call failed after {MAX_RETRIES} attempts. "
                                f"Responses received: {zone_responses}",
                                extra=log_context
                            )
                            break
                
                # Parse zone data safely
                if zone_data:
                    # Extract area_code from zone's AssociatedArea
                    assoc_area = zone_data.get("AssociatedArea")
                    area_href = safe_get_href(assoc_area)
                    area_code = safe_extract_code(area_href)
                    
                    # Log if area_code is missing
                    if not area_code or area_code == 0:
                        driver_alert_logger.warning(
                            f"Missing area_code from Zone response. "
                            f"AssociatedArea: {assoc_area}, "
                            f"Full Zone data: {json.dumps(zone_data, default=str)}",
                            extra=log_context
                        )
                else:
                    driver_alert_logger.error(
                        f"No Zone data found after {MAX_RETRIES} attempts for zone_code {zone_code}. "
                        f"All responses: {zone_responses}",
                        extra=log_context
                    )
            else:
                driver_alert_logger.warning(
                    f"Cannot query Zone API - zone_code is missing or invalid: {zone_code}",
                    extra=log_context
                )
            
            # Step 2b: /device/{device_code} with retry (only if we got device_code) - for device_type and device_name
            if device_code and device_code > 0:
                device_data = None
                device_responses = []
                
                for attempt in range(MAX_RETRIES):
                    try:
                        await _send_json(writer, {
                            "CommuniqueType": "ReadRequest",
                            "Header": {"Url": f"/device/{device_code}"}
                        })
                        responses = await _recv_raw(reader, timeout=REQUEST_TIMEOUT)
                        device_responses.extend(responses)
                        
                        for raw in responses:
                            try:
                                data = json.loads(raw)
                                dev = safe_get_nested(data, "Body", "Device")
                                if dev and isinstance(dev, dict):
                                    device_data = dev
                                    break
                            except (json.JSONDecodeError, TypeError) as e:
                                driver_alert_logger.debug(
                                    f"JSON parse error in device response: {str(e)}",
                                    extra=log_context
                                )
                                continue
                        
                        if device_data:
                            break
                            
                    except Exception as e:
                        driver_alert_logger.warning(
                            f"Device API call failed (attempt {attempt + 1}/{MAX_RETRIES}): {str(e)}",
                            extra=log_context
                        )
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_DELAY_BASE * (2 ** attempt))
                        else:
                            driver_alert_logger.error(
                                f"Device API call failed after {MAX_RETRIES} attempts. "
                                f"Responses received: {device_responses}",
                                extra=log_context
                            )
                            break
                
                # Parse device data safely
                if device_data:
                    # Extract device_type
                    device_type = device_data.get("DeviceType")
                    if device_type and not isinstance(device_type, str):
                        device_type = None
                    
                    # Fallback: if device_name not found earlier, use Device.Name
                    if not device_name:
                        device_name = device_data.get("Name")
                        if device_name and not isinstance(device_name, str):
                            device_name = None
                else:
                    driver_alert_logger.error(
                        f"No Device data found after {MAX_RETRIES} attempts for device_code {device_code}. "
                        f"All responses: {device_responses}",
                        extra=log_context
                    )
            else:
                driver_alert_logger.warning(
                    f"Cannot query Device API - device_code is missing or invalid: {device_code}",
                    extra=log_context
                )
            
            # Step 3: Map area_code -> area_id with processor context
            if area_code and area_code > 0:
                try:
                    area_obj = db.query(Area).filter_by(code=str(area_code), processor_id=processor_id).first()
                    if area_obj:
                        area_id = area_obj.id
                    else:
                        driver_alert_logger.warning(
                            f"Area not found in database for area_code={area_code}, processor_id={processor_id}",
                            extra=log_context
                        )
                except Exception as e:
                    driver_alert_logger.error(
                        f"Database error while looking up area_id: {str(e)}",
                        extra=log_context
                    )
            
            # Check if we have all critical data - if not, retry full job
            missing_critical = []
            if not device_code or device_code == 0:
                missing_critical.append("device_code")
            if not zone_code or zone_code == 0:
                missing_critical.append("zone_code")
            if not area_code or area_code == 0:
                missing_critical.append("area_code")
            
            # If we have all critical data, return success
            if not missing_critical:
                # Final summary log if any non-critical data is missing
                missing_fields = []
                if not area_id:
                    missing_fields.append("area_id")
                if not device_name:
                    missing_fields.append("device_name")
                
                if missing_fields:
                    driver_alert_logger.warning(
                        f"MISSING DATA SUMMARY (non-critical) - Missing fields: {', '.join(missing_fields)}. "
                        f"Resolved values: area_id={area_id}, area_code={area_code}, "
                        f"zone_code={zone_code}, device_code={device_code}, device_type={device_type}, "
                        f"device_name={device_name}",
                        extra=log_context
                    )
                
                return area_id, area_code, zone_code, device_code, device_type, device_name
            
            # If critical data is missing, log and retry full job
            if missing_critical:
                driver_alert_logger.warning(
                    f"Full job retry {full_attempt + 1}/{MAX_RETRIES} - Missing critical data: {', '.join(missing_critical)}. "
                    f"Will retry ALL read requests from scratch.",
                    extra=log_context
                )
                
                if full_attempt < MAX_RETRIES - 1:
                    # Wait before retrying full job
                    await asyncio.sleep(RETRY_DELAY_BASE * (2 ** full_attempt))
                    # Continue to next full attempt - will retry all read requests
                    continue
                else:
                    # Final attempt failed - log and return partial data
                    driver_alert_logger.error(
                        f"Full job retry failed after {MAX_RETRIES} attempts. "
                        f"Still missing critical data: {', '.join(missing_critical)}. "
                        f"Resolved values: area_id={area_id}, area_code={area_code}, "
                        f"zone_code={zone_code}, device_code={device_code}, device_type={device_type}, "
                        f"device_name={device_name}",
                        extra=log_context
                    )
                    return area_id, area_code, zone_code, device_code, device_type, device_name
                    
        except Exception as e:
            driver_alert_logger.error(
                f"EXCEPTION in resolve_loadcontroller_mapping (full attempt {full_attempt + 1}/{MAX_RETRIES}): {str(e)}",
                extra=log_context,
                exc_info=True
            )
            
            if full_attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY_BASE * (2 ** full_attempt))
                continue
            else:
                # Return partial data if available, None otherwise
                return area_id, area_code, zone_code, device_code, device_type, device_name
    
    # Should never reach here, but return None values if we do (safety fallback)
    driver_alert_logger.error(
        f"Unexpected exit from resolve_loadcontroller_mapping - returning None values",
        extra=log_context
    )
    return None, None, None, None, None, None

# ---------------------- Raw Data Logging ---------------------- #
def log_raw_loadcontroller_status_data(processor_id: int, statuses: list):
    """Log raw loadcontroller status data before database processing"""
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
    #         "status_count": len(statuses),
    #         "raw_statuses": statuses
    #     }
    #     
    #     log_file = os.path.join(logs_dir, f"raw_drivers_{date_str}.log")
    #     with open(log_file, "a", encoding="utf-8") as f:
    #         f.write(json.dumps(log_data, indent=2, ensure_ascii=False))
    #         f.write("\n" + "="*80 + "\n")
    # except Exception as e:
    #     print(f"Failed to log raw loadcontroller status data: {e}")
    pass

def log_driver_alert_error(processor_id: int, loadcontroller_code: int, error_type: str, error_data: dict):
    """Log driver alert error data to driver_alert_error_logs file"""
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
    #         "loadcontroller_code": loadcontroller_code,
    #         "error_type": error_type,
    #         "error_data": error_data
    #     }
    #     
    #     log_file = os.path.join(logs_dir, f"driver_alert_error_logs_{date_str}.log")
    #     with open(log_file, "a", encoding="utf-8") as f:
    #         f.write(json.dumps(log_data, indent=2, ensure_ascii=False))
    #         f.write("\n" + "="*80 + "\n")
    # except Exception as e:
    #     print(f"Failed to log driver alert error data: {e}")
    pass


# ---------------------- Handle LoadController Status ---------------------- #
async def handle_loadcontroller_status(statuses, processor_id, writer, reader):
    """Handle loadcontroller status updates with robust error handling"""
    
    # Log raw status data before processing
    # log_raw_loadcontroller_status_data(processor_id, statuses)
    
    for status in statuses:
        db = SessionLocal()
        try:
            lc_href = status.get("href")
            if not lc_href:
                continue
                
            # Safely extract loadcontroller_code
            try:
                loadcontroller_code = int(lc_href.strip("/").split("/")[-2]) if lc_href else None
            except (ValueError, IndexError, AttributeError, TypeError):
                continue
            
            if not loadcontroller_code:
                continue

            log_context = {'processor_id': processor_id, 'loadcontroller_code': loadcontroller_code}

            error_info = status.get("ErrorStatus", {})
            if not isinstance(error_info, dict):
                error_info = {}
                
            code = error_info.get("ErrorCode")
            desc = error_info.get("Description")

            # Check if this is an error resolution (empty error code and description)
            is_error_resolved = (not code or (isinstance(code, str) and code.strip() == "")) and \
                               (not desc or (isinstance(desc, str) and desc.strip() == ""))

            # Skip if error code/description is "Unknown"
            if code == "Unknown" or desc == "Unknown":
                continue

            # Find existing alert for this loadcontroller with processor context
            alert = db.query(Driver).filter_by(
                loadcontroller_code=loadcontroller_code, 
                processor_id=processor_id
            ).first()

            if is_error_resolved:
                # Error has been resolved - update existing alert to "okay" status
                if alert:
                    try:
                        from app.crud.alert import update_alert_timestamps
                        update_alert_timestamps(alert, "okay")
                        
                        alert.error_code = None
                        alert.description = None
                        # alert_status is already set by update_alert_timestamps
                        db.commit()
                    except Exception:
                        db.rollback()
            else:
                # There's an active error - create or update alert
                if code in ERROR_MAP:
                    desc = ERROR_MAP[code]

                if alert:
                    # Update existing alert
                    try:
                        from app.crud.alert import update_alert_timestamps
                        update_alert_timestamps(alert, "not_okay")
                        
                        alert.error_code = code
                        alert.description = desc
                        # alert_status is already set by update_alert_timestamps
                        
                        # Update missing data if needed
                        needs_update = not alert.device_name or not alert.area_code or not alert.zone_code or not alert.device_code
                        if needs_update:
                            driver_alert_logger.warning(
                                f"Updating existing alert with missing data. "
                                f"Current missing: device_name={not alert.device_name}, "
                                f"area_code={not alert.area_code}, zone_code={not alert.zone_code}, "
                                f"device_code={not alert.device_code}",
                                extra=log_context
                            )
                            try:
                                area_id, area_code, zone_code, device_code, device_type, device_name = \
                                    await resolve_loadcontroller_mapping(
                                        writer, reader, loadcontroller_code, db, processor_id
                                    )
                                
                                if device_name and not alert.device_name:
                                    alert.device_name = device_name
                                if area_code and not alert.area_code:
                                    alert.area_code = area_code
                                if zone_code and not alert.zone_code:
                                    alert.zone_code = zone_code
                                if alert.zone_code and not alert.zone_id:
                                    alert.zone_id = _resolve_driver_zone_id(
                                        db, alert.processor_id, alert.zone_code
                                    )
                                if device_code and not alert.device_code:
                                    alert.device_code = device_code
                                if device_type and not alert.device_type:
                                    alert.device_type = device_type
                                if area_id and not alert.area_id:
                                    alert.area_id = area_id
                                
                                # Log if still missing after update
                                still_missing = []
                                if not alert.area_id:
                                    still_missing.append("area_id")
                                if not alert.area_code:
                                    still_missing.append("area_code")
                                if not alert.zone_code:
                                    still_missing.append("zone_code")
                                if not alert.device_code:
                                    still_missing.append("device_code")
                                if not alert.device_name:
                                    still_missing.append("device_name")
                                
                                if still_missing:
                                    driver_alert_logger.error(
                                        f"Alert still missing data after update attempt: {', '.join(still_missing)}",
                                        extra=log_context
                                    )
                            except Exception as e:
                                driver_alert_logger.error(
                                    f"Failed to update missing data: {str(e)}",
                                    extra=log_context,
                                    exc_info=True
                                )
                        
                        db.commit()
                    except Exception:
                        db.rollback()
                else:
                    # Create new alert - even with partial data
                    try:
                        area_id, area_code, zone_code, device_code, device_type, device_name = \
                            await resolve_loadcontroller_mapping(
                                writer, reader, loadcontroller_code, db, processor_id
                            )
                        
                        # # Log missing data before creating alert
                        # missing_fields = []
                        # if not area_id:
                        #     missing_fields.append("area_id")
                        # if not area_code:
                        #     missing_fields.append("area_code")
                        # if not zone_code:
                        #     missing_fields.append("zone_code")
                        # if not device_code:
                        #     missing_fields.append("device_code")
                        # if not device_name:
                        #     missing_fields.append("device_name")
                        
                        # if missing_fields:
                        #     driver_alert_logger.warning(
                        #         f"CREATING ALERT WITH MISSING DATA - Missing fields: {', '.join(missing_fields)}. "
                        #         f"Error code: {code}, Description: {desc}",
                        #         extra=log_context
                        #     )
                        if area_id:

                            new_alert = Driver(
                                processor_id=processor_id,
                                area_id=area_id,
                                area_code=area_code,
                                zone_code=zone_code,
                                zone_id=_resolve_driver_zone_id(db, processor_id, zone_code),
                                device_code=device_code,
                                device_type=device_type,
                                device_name=device_name,
                                loadcontroller_code=loadcontroller_code,
                                error_code=code,
                                description=desc,
                                alert_status="not_ok"
                            )
                            
                            # Set initial timestamps for new alert
                            current_time = datetime.utcnow()
                            new_alert.reported_time = current_time
                            new_alert.solved_time = None
                            new_alert.created_at = current_time
                            
                            db.add(new_alert)
                            db.commit()
                        else: 
                            error_data = {
                                "area_id": area_id,
                                "area_code": area_code,
                                "zone_code": zone_code,
                                "device_code": device_code,
                                "device_type": device_type,
                                "device_name": device_name,
                                "error_code": code,
                                "description": desc,
                                "reason": "Alert not created - area_id is missing"
                            }
                            # log_driver_alert_error(processor_id, loadcontroller_code, "missing_area_id", error_data)

                        # Log alert creation with final status
                        # if missing_fields:
                        #     driver_alert_logger.warning(
                        #         f"Alert created with missing data. Alert ID: {new_alert.id}, "
                        #         f"Missing: {', '.join(missing_fields)}",
                        #         extra=log_context
                        #     )
                    except Exception as e:
                        error_data = {}
                        try:
                            error_data["area_id"] = area_id
                        except NameError:
                            error_data["area_id"] = None
                        try:
                            error_data["area_code"] = area_code
                        except NameError:
                            error_data["area_code"] = None
                        try:
                            error_data["zone_code"] = zone_code
                        except NameError:
                            error_data["zone_code"] = None
                        try:
                            error_data["device_code"] = device_code
                        except NameError:
                            error_data["device_code"] = None
                        try:
                            error_data["device_type"] = device_type
                        except NameError:
                            error_data["device_type"] = None
                        try:
                            error_data["device_name"] = device_name
                        except NameError:
                            error_data["device_name"] = None
                        
                        error_data["error_code"] = code
                        error_data["description"] = desc
                        error_data["exception_type"] = type(e).__name__
                        error_data["exception_message"] = str(e)
                        error_data["reason"] = "Exception occurred while creating alert"
                        
                        # log_driver_alert_error(processor_id, loadcontroller_code, "exception", error_data)
                        driver_alert_logger.error(
                            f"Failed to create alert: {str(e)}",
                            extra=log_context,
                            exc_info=True
                        )
                        db.rollback()
                        # Continue processing other alerts even if one fails
                        continue
                        
        except Exception as e:
            driver_alert_logger.error(
                f"Exception in handle_loadcontroller_status: {str(e)}",
                extra={'processor_id': processor_id, 'loadcontroller_code': 'unknown'},
                exc_info=True
            )
            # Continue processing other statuses even if one fails
            continue
        finally:
            try:
                db.close()
            except Exception:
                pass

# ---------------------- Unified Listener ---------------------- #
async def loadcontroller_listener(reader, writer, processor_id):
    async def send_ping():
        while not shutdown_event.is_set():
            await asyncio.sleep(30)
            await _send_json(writer, {
                "CommuniqueType": "ReadRequest",
                "Header": {"URL": "/server/status/ping"}
            })

    asyncio.create_task(send_ping())

    while not shutdown_event.is_set():
        try:
            raw_msgs = await _recv_raw(reader)
            for raw in raw_msgs:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                ctype = msg.get("CommuniqueType")
                header = msg.get("Header", {})
                body = msg.get("Body", {})
                url = header.get("Url", "")

                if url == "/server/status/ping":
                    continue
                elif ctype == "SubscribeResponse" and "LoadControllerStatuses" in body:
                    await handle_loadcontroller_status(body["LoadControllerStatuses"], processor_id, writer, reader)
                elif url == "/loadcontroller/status":
                    await handle_loadcontroller_status(body.get("LoadControllerStatuses", []), processor_id, writer, reader)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(1)

# ---------------------- Processor Handling ---------------------- #
async def handle_connected_processor(processor, reader, writer):
    await _send_json(writer, {
        "CommuniqueType": "SubscribeRequest",
        "Header": {"Url": "/loadcontroller/status"}
    })
    await loadcontroller_listener(reader, writer, processor.id)

async def monitor_loadcontroller(processor):
    """Monitor loadcontroller for a single processor - safe for multiple processes"""
    while not shutdown_event.is_set():
        try:
            # Get processor-specific certificate paths
            cert_paths = get_processor_cert_paths(processor.ipv4)
            
            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ctx.load_verify_locations(cafile=cert_paths['lap_root'])
            ctx.load_cert_chain(certfile=cert_paths['leap_signed_csr'], keyfile=cert_paths['leap_private_key'])
            ctx.check_hostname = False

            reader, writer = await asyncio.open_connection(
                host=processor.ipv4,
                port=8081,
                ssl=ctx,
                server_hostname=get_proc_hostname(processor.system, processor.mac)
            )
            await handle_connected_processor(processor, reader, writer)
        except asyncio.CancelledError:
            break
        except Exception:
            db = SessionLocal()
            try:
                db.add(ProcessorConnectionError(processor_id=processor.id, message="LoadController connection failed"))
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
            await asyncio.sleep(5)

# ---------------------- Entrypoint ---------------------- #
async def main_async():
    """Main async entrypoint - handles multiple processors concurrently"""
    db = SessionLocal()
    try:
        processors = db.query(Processor).filter_by(handshake_status=True).all()
    except Exception:
        return
        
    if not processors:
        return
        
    # Create tasks for each processor - each runs independently
    tasks = [asyncio.create_task(monitor_loadcontroller(p)) for p in processors]
    
    try:
        # Wait for all tasks - if one fails, others continue
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        db.close()

def loadcontroller_listener_entrypoint():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
