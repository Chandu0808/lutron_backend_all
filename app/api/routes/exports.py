from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from app.database.session import get_db
from app.models.user_model import User
from app.models.email_settings import EmailServerSettings
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_operator_permission_for_scope
from app.crud.exports import (
    generate_energy_consumption_csv, 
    generate_energy_savings_csv, 
    generate_occupancy_count_csv, 
    generate_total_consumption_by_group_csv, 
    generate_space_utilization_per_csv, 
    generate_occupancy_by_group_csv,
    generate_instant_occupancy_count_csv,
    generate_occupancy_by_group_from_logs_csv,
    generate_space_utilization_per_from_logs_csv,
    generate_peak_min_occupancy_from_logs_csv
)
from cryptography.fernet import Fernet

# Fernet encryption key (same as in email_settings CRUD)
FERNET_SECRET = b'D_5uU3ImkAl7O58-Lb1v4jU2Pf8Aq5PYs9Lx6Nj66tU='
fernet = Fernet(FERNET_SECRET)

def decrypt_key(encrypted_text: str) -> str:
    return fernet.decrypt(encrypted_text.encode()).decode()


router = APIRouter()


@router.get("/energy_consumption/download")
def download_energy_consumption(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Download energy consumption data as CSV file.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Generate CSV content
    try:
        csv_content = generate_energy_consumption_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"energy_consumption_{time_range}_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Return CSV as downloadable file
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/energy_consumption/email")
def email_energy_consumption(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Email energy consumption data as CSV attachment.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Fetch email settings from database
    email_settings = db.query(EmailServerSettings).first()
    if not email_settings:
        raise HTTPException(status_code=404, detail="Email server settings not configured")
    
    # Generate CSV content
    try:
        csv_content = generate_energy_consumption_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"energy_consumption_{time_range}_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{email_settings.sender_name} <{email_settings.server_email}>"
        msg['To'] = current_user.email
        msg['Subject'] = f"Energy Consumption Report - {time_range}"
        
        # Email body
        body = f"Please find attached the Energy Consumption report for {time_range}.\n\nGenerated on: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach CSV file
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Decrypt the app password
        decrypted_password = decrypt_key(email_settings.app_password)
        
        # Send email via SMTP
        # Use SSL for port 465, TLS for port 587
        if email_settings.port == 465:
            server = smtplib.SMTP_SSL(email_settings.server_name, email_settings.port)
        else:
            server = smtplib.SMTP(email_settings.server_name, email_settings.port)
            server.starttls()
        server.login(email_settings.server_email, decrypted_password)
        server.send_message(msg)
        server.quit()
        
        return {"status": "success", "message": f"Email sent successfully to {current_user.email}"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")


@router.get("/space_utilization_per/download")
def download_space_utilization_per(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Download space utilization per area data as CSV file.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Generate CSV content
    try:
        csv_content = generate_space_utilization_per_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Utilization By Area_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Return CSV as downloadable file
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/space_utilization_per/email")
def email_space_utilization_per(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Email space utilization per area data as CSV attachment.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Fetch email settings from database
    email_settings = db.query(EmailServerSettings).first()
    if not email_settings:
        raise HTTPException(status_code=404, detail="Email server settings not configured")
    
    # Generate CSV content
    try:
        csv_content = generate_space_utilization_per_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Utilization By Area_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{email_settings.sender_name} <{email_settings.server_email}>"
        msg['To'] = current_user.email
        msg['Subject'] = f"Space Utilization Per Area Report - {time_range}"
        
        # Email body
        body = f"Please find attached the Space Utilization Per Area report for {time_range}.\n\nGenerated on: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach CSV file
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Decrypt the app password
        decrypted_password = decrypt_key(email_settings.app_password)
        
        # Send email via SMTP
        # Use SSL for port 465, TLS for port 587
        if email_settings.port == 465:
            server = smtplib.SMTP_SSL(email_settings.server_name, email_settings.port)
        else:
            server = smtplib.SMTP(email_settings.server_name, email_settings.port)
            server.starttls()
        server.login(email_settings.server_email, decrypted_password)
        server.send_message(msg)
        server.quit()
        
        return {"status": "success", "message": f"Email sent successfully to {current_user.email}"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")


@router.get("/occupancy_by_group/download")
def download_occupancy_by_group(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Download occupancy by group data as CSV file.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Generate CSV content
    try:
        csv_content = generate_occupancy_by_group_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Utilization By Area Group_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Return CSV as downloadable file
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/occupancy_by_group/email")
def email_occupancy_by_group(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Email occupancy by group data as CSV attachment.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Fetch email settings from database
    email_settings = db.query(EmailServerSettings).first()
    if not email_settings:
        raise HTTPException(status_code=404, detail="Email server settings not configured")
    
    # Generate CSV content
    try:
        csv_content = generate_occupancy_by_group_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Utilization By Area Group_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{email_settings.sender_name} <{email_settings.server_email}>"
        msg['To'] = current_user.email
        msg['Subject'] = f"Occupancy by Group Report - {time_range}"
        
        # Email body
        body = f"Please find attached the Occupancy by Group report for {time_range}.\n\nGenerated on: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach CSV file
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Decrypt the app password
        decrypted_password = decrypt_key(email_settings.app_password)
        
        # Send email via SMTP
        # Use SSL for port 465, TLS for port 587
        if email_settings.port == 465:
            server = smtplib.SMTP_SSL(email_settings.server_name, email_settings.port)
        else:
            server = smtplib.SMTP(email_settings.server_name, email_settings.port)
            server.starttls()
        server.login(email_settings.server_email, decrypted_password)
        server.send_message(msg)
        server.quit()
        
        return {"status": "success", "message": f"Email sent successfully to {current_user.email}"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")


@router.get("/total_consumption_by_group/download")
def download_total_consumption_by_group(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Download total consumption by group data as CSV file.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Generate CSV content
    try:
        csv_content = generate_total_consumption_by_group_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"total_consumption_by_group_{time_range}_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Return CSV as downloadable file
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/total_consumption_by_group/email")
def email_total_consumption_by_group(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Email total consumption by group data as CSV attachment.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Fetch email settings from database
    email_settings = db.query(EmailServerSettings).first()
    if not email_settings:
        raise HTTPException(status_code=404, detail="Email server settings not configured")
    
    # Generate CSV content
    try:
        csv_content = generate_total_consumption_by_group_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"total_consumption_by_group_{time_range}_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{email_settings.sender_name} <{email_settings.server_email}>"
        msg['To'] = current_user.email
        msg['Subject'] = f"Total Consumption by Group Report - {time_range}"
        
        # Email body
        body = f"Please find attached the Total Consumption by Group report for {time_range}.\n\nGenerated on: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach CSV file
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Decrypt the app password
        decrypted_password = decrypt_key(email_settings.app_password)
        
        # Send email via SMTP
        # Use SSL for port 465, TLS for port 587
        if email_settings.port == 465:
            server = smtplib.SMTP_SSL(email_settings.server_name, email_settings.port)
        else:
            server = smtplib.SMTP(email_settings.server_name, email_settings.port)
            server.starttls()
        server.login(email_settings.server_email, decrypted_password)
        server.send_message(msg)
        server.quit()
        
        return {"status": "success", "message": f"Email sent successfully to {current_user.email}"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")


@router.get("/occupancy_count/download")
def download_occupancy_count(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Download occupancy count data as CSV file.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Generate CSV content
    try:
        csv_content = generate_occupancy_count_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Occupancy_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Return CSV as downloadable file
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/occupancy_count/email")
def email_occupancy_count(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Email occupancy count data as CSV attachment.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Fetch email settings from database
    email_settings = db.query(EmailServerSettings).first()
    if not email_settings:
        raise HTTPException(status_code=404, detail="Email server settings not configured")
    
    # Generate CSV content
    try:
        csv_content = generate_occupancy_count_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Occupancy_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{email_settings.sender_name} <{email_settings.server_email}>"
        msg['To'] = current_user.email
        msg['Subject'] = f"Occupancy Count Report - {time_range}"
        
        # Email body
        body = f"Please find attached the Occupancy Count report for {time_range}.\n\nGenerated on: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach CSV file
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Decrypt the app password
        decrypted_password = decrypt_key(email_settings.app_password)
        
        # Send email via SMTP
        # Use SSL for port 465, TLS for port 587
        if email_settings.port == 465:
            server = smtplib.SMTP_SSL(email_settings.server_name, email_settings.port)
        else:
            server = smtplib.SMTP(email_settings.server_name, email_settings.port)
            server.starttls()
        server.login(email_settings.server_email, decrypted_password)
        server.send_message(msg)
        server.quit()
        
        return {"status": "success", "message": f"Email sent successfully to {current_user.email}"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")


@router.get("/energy_savings/download")
def download_energy_savings(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Download energy savings data as CSV file.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Generate CSV content
    try:
        csv_content = generate_energy_savings_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"energy_savings_{time_range}_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Return CSV as downloadable file
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/energy_savings/email")
def email_energy_savings(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Email energy savings data as CSV attachment.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Fetch email settings from database
    email_settings = db.query(EmailServerSettings).first()
    if not email_settings:
        raise HTTPException(status_code=404, detail="Email server settings not configured")
    
    # Generate CSV content
    try:
        csv_content = generate_energy_savings_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"energy_savings_{time_range}_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{email_settings.sender_name} <{email_settings.server_email}>"
        msg['To'] = current_user.email
        msg['Subject'] = f"Energy Savings Report - {time_range}"
        
        # Email body
        body = f"Please find attached the Energy Savings report for {time_range}.\n\nGenerated on: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach CSV file
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Decrypt the app password
        decrypted_password = decrypt_key(email_settings.app_password)
        
        # Send email via SMTP
        # Use SSL for port 465, TLS for port 587
        if email_settings.port == 465:
            server = smtplib.SMTP_SSL(email_settings.server_name, email_settings.port)
        else:
            server = smtplib.SMTP(email_settings.server_name, email_settings.port)
            server.starttls()
        server.login(email_settings.server_email, decrypted_password)
        server.send_message(msg)
        server.quit()
        
        return {"status": "success", "message": f"Email sent successfully to {current_user.email}"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")


@router.get("/instant_occupancy_count/download")
def download_instant_occupancy_count(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Download instant occupancy count data as CSV file.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Generate CSV content
    try:
        csv_content = generate_instant_occupancy_count_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Occupancy_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Return CSV as downloadable file
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/instant_occupancy_count/email")
def email_instant_occupancy_count(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Email instant occupancy count data as CSV attachment.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Fetch email settings from database
    email_settings = db.query(EmailServerSettings).first()
    if not email_settings:
        raise HTTPException(status_code=404, detail="Email server settings not configured")
    
    # Generate CSV content
    try:
        csv_content = generate_instant_occupancy_count_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Occupancy_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{email_settings.sender_name} <{email_settings.server_email}>"
        msg['To'] = current_user.email
        msg['Subject'] = f"Instant Occupancy Count Report - {time_range}"
        
        # Email body
        body = f"Please find attached the Instant Occupancy Count report for {time_range}.\n\nGenerated on: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach CSV file
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Decrypt the app password
        decrypted_password = decrypt_key(email_settings.app_password)
        
        # Send email via SMTP
        # Use SSL for port 465, TLS for port 587
        if email_settings.port == 465:
            server = smtplib.SMTP_SSL(email_settings.server_name, email_settings.port)
        else:
            server = smtplib.SMTP(email_settings.server_name, email_settings.port)
            server.starttls()
        server.login(email_settings.server_email, decrypted_password)
        server.send_message(msg)
        server.quit()
        
        return {"status": "success", "message": f"Email sent successfully to {current_user.email}"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")


@router.get("/occupancy_by_group_from_logs/download")
def download_occupancy_by_group_from_logs(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Download occupancy by group from logs data as CSV file.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Generate CSV content
    try:
        csv_content = generate_occupancy_by_group_from_logs_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Utilization By Area Group_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Return CSV as downloadable file
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/occupancy_by_group_from_logs/email")
def email_occupancy_by_group_from_logs(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Email occupancy by group from logs data as CSV attachment.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Fetch email settings from database
    email_settings = db.query(EmailServerSettings).first()
    if not email_settings:
        raise HTTPException(status_code=404, detail="Email server settings not configured")
    
    # Generate CSV content
    try:
        csv_content = generate_occupancy_by_group_from_logs_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Utilization By Area Group_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{email_settings.sender_name} <{email_settings.server_email}>"
        msg['To'] = current_user.email
        msg['Subject'] = f"Occupancy by Group from Logs Report - {time_range}"
        
        # Email body
        body = f"Please find attached the Occupancy by Group from Logs report for {time_range}.\n\nGenerated on: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach CSV file
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Decrypt the app password
        decrypted_password = decrypt_key(email_settings.app_password)
        
        # Send email via SMTP
        # Use SSL for port 465, TLS for port 587
        if email_settings.port == 465:
            server = smtplib.SMTP_SSL(email_settings.server_name, email_settings.port)
        else:
            server = smtplib.SMTP(email_settings.server_name, email_settings.port)
            server.starttls()
        server.login(email_settings.server_email, decrypted_password)
        server.send_message(msg)
        server.quit()
        
        return {"status": "success", "message": f"Email sent successfully to {current_user.email}"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")


@router.get("/space_utilization_per_from_logs/download")
def download_space_utilization_per_from_logs(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Download space utilization per area from logs data as CSV file.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Generate CSV content
    try:
        csv_content = generate_space_utilization_per_from_logs_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Utilization By Area_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Return CSV as downloadable file
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/space_utilization_per_from_logs/email")
def email_space_utilization_per_from_logs(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Email space utilization per area from logs data as CSV attachment.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Fetch email settings from database
    email_settings = db.query(EmailServerSettings).first()
    if not email_settings:
        raise HTTPException(status_code=404, detail="Email server settings not configured")
    
    # Generate CSV content
    try:
        csv_content = generate_space_utilization_per_from_logs_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"Utilization By Area_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{email_settings.sender_name} <{email_settings.server_email}>"
        msg['To'] = current_user.email
        msg['Subject'] = f"Space Utilization Per Area from Logs Report - {time_range}"
        
        # Email body
        body = f"Please find attached the Space Utilization Per Area from Logs report for {time_range}.\n\nGenerated on: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach CSV file
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Decrypt the app password
        decrypted_password = decrypt_key(email_settings.app_password)
        
        # Send email via SMTP
        # Use SSL for port 465, TLS for port 587
        if email_settings.port == 465:
            server = smtplib.SMTP_SSL(email_settings.server_name, email_settings.port)
        else:
            server = smtplib.SMTP(email_settings.server_name, email_settings.port)
            server.starttls()
        server.login(email_settings.server_email, decrypted_password)
        server.send_message(msg)
        server.quit()
        
        return {"status": "success", "message": f"Email sent successfully to {current_user.email}"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")


@router.get("/peak_min_occupancy_from_logs/download")
def download_peak_min_occupancy_from_logs(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Download peak/min occupancy from logs data as CSV file.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Generate CSV content
    try:
        csv_content = generate_peak_min_occupancy_from_logs_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"peak_min_occupancy_from_logs_{time_range}_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Return CSV as downloadable file
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/peak_min_occupancy_from_logs/email")
def email_peak_min_occupancy_from_logs(
    area_ids: Optional[List[int]] = Query(None),
    floor_ids: Optional[List[int]] = Query(None),
    time_range: str = Query(..., enum=["this_day", "this_week", "this_month", "this_year", "custom"]),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Email peak/min occupancy from logs data as CSV attachment.
    """
    # Permission enforcement
    try:
        require_operator_permission_for_scope(
            required_level=1,  # monitor level
            area_ids=area_ids,
            floor_ids=floor_ids,
            enforce_on_empty_scope=bool(
                (area_ids and len(area_ids) > 0) or (floor_ids and len(floor_ids) > 0)
            ),
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        raise e
    
    # Fetch email settings from database
    email_settings = db.query(EmailServerSettings).first()
    if not email_settings:
        raise HTTPException(status_code=404, detail="Email server settings not configured")
    
    # Generate CSV content
    try:
        csv_content = generate_peak_min_occupancy_from_logs_csv(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating CSV: {str(e)}")
    
    # Generate filename
    now = datetime.now()
    filename = f"peak_min_occupancy_from_logs_{time_range}_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{email_settings.sender_name} <{email_settings.server_email}>"
        msg['To'] = current_user.email
        msg['Subject'] = f"Peak/Min Occupancy from Logs Report - {time_range}"
        
        # Email body
        body = f"Please find attached the Peak/Min Occupancy from Logs report for {time_range}.\n\nGenerated on: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach CSV file
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(csv_content.encode('utf-8'))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Decrypt the app password
        decrypted_password = decrypt_key(email_settings.app_password)
        
        # Send email via SMTP
        # Use SSL for port 465, TLS for port 587
        if email_settings.port == 465:
            server = smtplib.SMTP_SSL(email_settings.server_name, email_settings.port)
        else:
            server = smtplib.SMTP(email_settings.server_name, email_settings.port)
            server.starttls()
        server.login(email_settings.server_email, decrypted_password)
        server.send_message(msg)
        server.quit()
        
        return {"status": "success", "message": f"Email sent successfully to {current_user.email}"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")
