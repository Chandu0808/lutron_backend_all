from sqlalchemy import Column, Integer, String, Boolean, UniqueConstraint

from app.database.session import Base


class AlertTypeDisplaySetting(Base):
    """
    Global (system-wide) UI visibility setting per alert type.

    This is used so disabling an alert type keeps it hidden even for alerts
    created after the disable action.
    """

    __tablename__ = "alert_type_display_settings"

    __table_args__ = (
        UniqueConstraint("alert_type", name="uq_alert_type_display_settings_alert_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    alert_type = Column(String(64), nullable=False)
    display = Column(Boolean, nullable=False, default=True)

