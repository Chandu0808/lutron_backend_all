from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, time, timedelta
from sqlalchemy.orm import Session
from app.database.session import SessionLocal
from app.models.schedule import Schedule
from app.utils.activity_logger import log_activity
from app.models.quick_controls import QuickControl
from app.crud import alert
import threading

scheduler = BackgroundScheduler()

# Lock to prevent concurrent reconciliation execution
reconciliation_lock = threading.Lock()

# ------------------- Quick Control Scheduling ------------------- #
def schedule_quick_control_trigger(run_time: datetime, quick_control_id: int):
    def job():
        from app.api.routes.quick_controls import trigger_quick_control
        db = SessionLocal()
        try:
            trigger_quick_control(db=db, quick_control_id=quick_control_id)
            qc = db.query(QuickControl).filter(QuickControl.id == quick_control_id).first()
            if qc:
                log_activity(
                    db=db,
                    user_id=1,
                    area_id=None,
                    floor_id=None,
                    activity_type="Schedule",
                    activity_description=f" schedule triggered {qc.name} (ID: {quick_control_id})"
                )
        finally:
            db.close()

    scheduler.add_job(
        job,
        trigger="date",
        run_date=run_time,
        id=f"qc_trigger_{quick_control_id}_{run_time.timestamp()}",
        replace_existing=True
    )


def run_internal_schedule(schedule_id: int):
    db = SessionLocal()
    try:
        schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if schedule and schedule.quick_control_id:
            from app.trigger import trigger_quick_control_event
            # System trigger → pass a dummy user (id=1 or system user) or None
            trigger_quick_control_event(schedule.quick_control_id, db, user=None)

            qc = db.query(QuickControl).filter(QuickControl.id == schedule.quick_control_id).first()
            if qc:
                log_activity(
                    db=db,
                    user_id=1,   # system user
                    area_id=None,
                    floor_id=None,
                    activity_type="Schedule",
                    activity_description=f" schedule triggered (ID: {schedule_id}) {qc.name}"
                )
    finally:
        db.close()

def parse_time_dict(time_dict: dict) -> time:
    return time(
        hour=time_dict.get("hour", 0),
        minute=time_dict.get("minute", 0),
        second=time_dict.get("second", 0)
    )


def schedule_job_for_schedule_id(db: Session, schedule_id: int):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id, Schedule.is_active == True).first()
    if not schedule or not schedule.time_of_day:
        return

    try:
        t = parse_time_dict(schedule.time_of_day)
        hour, minute, second = t.hour, t.minute, t.second

        if schedule.schedule_type == "DayOfWeek" and schedule.days:
            day_map = {
                "Monday": "mon", "Tuesday": "tue", "Wednesday": "wed",
                "Thursday": "thu", "Friday": "fri", "Saturday": "sat", "Sunday": "sun"
            }
            cron_days = [day_map[d] for d, active in schedule.days.items() if active and d in day_map]
            if cron_days:
                scheduler.add_job(
                    run_internal_schedule,
                    CronTrigger(day_of_week=",".join(cron_days), hour=hour, minute=minute, second=second),
                    args=[schedule.id],
                    id=f"schedule_{schedule.id}",
                    replace_existing=True
                )

        if schedule.schedule_type == "SpecificDates" and schedule.specific_dates:
            for d in schedule.specific_dates:
                try:
                    run_date = datetime(d["year"], d["month"], d["day"], hour, minute, second)
                    if run_date > datetime.now():
                        scheduler.add_job(
                            run_internal_schedule,
                            "date",
                            run_date=run_date,
                            args=[schedule.id],
                            id=f"schedule_{schedule.id}_{d['year']}_{d['month']}_{d['day']}",
                            replace_existing=True
                        )
                except:
                    pass
    except:
        pass


def load_all_schedules():
    db = SessionLocal()
    try:
        schedules = db.query(Schedule).filter(Schedule.is_active == True).all()
        for sched in schedules:
            schedule_job_for_schedule_id(db, sched.id)
    finally:
        db.close()

# ------------------- Device Refresh Task ------------------- #
def schedule_device_refresh():
    try:
        scheduler.add_job(
            alert.refresh_all_devices,          # unified refresh for sensors + modules
            CronTrigger(minute="*/15"),         # every 15 minutes
            id="device_refresh",
            replace_existing=True
        )
        print("[Scheduler] Device refresh scheduled every 15 min")
    except Exception as e:
        print(f"[Scheduler Error] Could not schedule device refresh: {e}")


# ------------------- Daily Data Backfill Task ------------------- #
def daily_data_backfill():
    """
    Daily data backfill job that runs at 23:58 every day.
    Checks all day's data and backfills missing intervals.
    Runs in a separate thread to avoid blocking other scheduled jobs.
    """
    def run_backfill_in_thread():
        """
        Execute backfill in separate thread to ensure it doesn't block scheduler.
        This ensures the 00:00 logging job runs on time even if backfill takes longer.
        """
        from app.energy_logger import check_and_fill_missing_data_simple
        
        db = SessionLocal()
        backfill_start_time = datetime.now()
        
        try:
            print(f"[Daily Backfill] Starting at {backfill_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("[Daily Backfill] Checking all day's data (last 24 hours)")
            
            # Use the existing function with 24 hours lookback to check entire day
            energy_filled, occupancy_filled = check_and_fill_missing_data_simple(
                db, 
                backfill_start_time, 
                lookback_hours=24
            )
            
            # Commit the changes
            db.commit()
            
            backfill_end_time = datetime.now()
            duration = (backfill_end_time - backfill_start_time).total_seconds()
            
            print(
                f"[Daily Backfill] Completed at {backfill_end_time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(took {duration:.2f}s) - "
                f"Energy filled: {energy_filled} | Occupancy filled: {occupancy_filled}"
            )
            
        except Exception as e:
            print(f"[Daily Backfill Error] Fatal error: {e}")
            import traceback
            traceback.print_exc()
            db.rollback()
        finally:
            db.close()
    
    # Start backfill in daemon thread (won't block scheduler shutdown)
    backfill_thread = threading.Thread(target=run_backfill_in_thread, daemon=True)
    backfill_thread.start()
    print("[Daily Backfill] Backfill thread started")


def schedule_daily_backfill():
    """
    Schedule the daily data backfill job to run at 23:58 every day.
    Runs 2 minutes before 00:00 logging to ensure it doesn't interfere.
    """
    try:
        scheduler.add_job(
            daily_data_backfill,
            CronTrigger(hour=23, minute=58, second=0),
            id="daily_data_backfill",
            name="Daily data backfill at 23:58",
            replace_existing=True,
            max_instances=1  # Prevent multiple instances if previous one is still running
        )
        print("[Scheduler] Daily data backfill scheduled at 23:58")
    except Exception as e:
        print(f"[Scheduler Error] Could not schedule daily backfill: {e}")


# ------------------- Occupancy Logs Reconciliation Task ------------------- #
def occupancy_logs_reconciliation():
    """
    Hourly reconciliation job that compares occupancy logs with actual area occupancy status.
    If there are any mismatches, updates the logs and sets reconcile=True.
    Runs in a separate thread to avoid blocking other scheduled jobs.
    """
    def run_reconciliation_in_thread():
        """
        Execute reconciliation in separate thread to ensure it doesn't block scheduler.
        """
        from app.crud.occupancy_logs import reconcile_occupancy_logs
        
        # Acquire lock to prevent concurrent execution
        if not reconciliation_lock.acquire(blocking=False):
            print("[Occupancy Reconciliation] Skipped - reconciliation already in progress")
            return
        
        db = SessionLocal()
        reconciliation_start_time = datetime.now()
        
        try:
            print(f"[Occupancy Reconciliation] Starting at {reconciliation_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Run the reconciliation function
            result = reconcile_occupancy_logs(db)
            
            # Commit the changes
            db.commit()
            
            reconciliation_end_time = datetime.now()
            duration = (reconciliation_end_time - reconciliation_start_time).total_seconds()
            
            print(
                f"[Occupancy Reconciliation] Completed at {reconciliation_end_time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(took {duration:.2f}s) - "
                f"Total areas: {result.get('total_areas', 0)} | "
                f"Matched: {result.get('matched', 0)} | "
                f"Mismatched: {result.get('mismatched', 0)} | "
                f"Skipped: {result.get('skipped', 0)} | "
                f"Errors: {result.get('errors', 0)}"
            )
            
        except Exception as e:
            print(f"[Occupancy Reconciliation Error] Fatal error: {e}")
            import traceback
            traceback.print_exc()
            db.rollback()
        finally:
            db.close()
            # Release lock when done
            reconciliation_lock.release()
    
    # Start reconciliation in daemon thread (won't block scheduler shutdown)
    reconciliation_thread = threading.Thread(target=run_reconciliation_in_thread, daemon=True)
    reconciliation_thread.start()
    print("[Occupancy Reconciliation] Reconciliation thread started")


def schedule_occupancy_reconciliation():
    """
    Schedule the occupancy logs reconciliation job to run every 10 minutes.
    """
    try:
        scheduler.add_job(
            occupancy_logs_reconciliation,
            CronTrigger(minute=0),  # Run every hour
            id="occupancy_reconciliation",
            name="Occupancy logs reconciliation every hour",
            replace_existing=True,
            max_instances=1  # Prevent multiple instances if previous one is still running
        )
        print("[Scheduler] Occupancy logs reconciliation scheduled every hour")
    except Exception as e:
        print(f"[Scheduler Error] Could not schedule occupancy reconciliation: {e}")


# Start scheduler
scheduler.start()
schedule_device_refresh()
schedule_daily_backfill()
schedule_occupancy_reconciliation()

