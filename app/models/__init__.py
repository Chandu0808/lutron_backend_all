# app/models/__init__.py

from .area import Area
from .area_group import AreaGroup, AreaGroupMapping
from .coordinate import Coordinate
from .events import (
    ProcessorAreaEvent,
    ProcessorZoneEvent,
    ProcessorConnectionError,
    ProcessorEvent,
    CurrentAreaEvent,
    CurrentZoneEvent
)

from .floor import Floor
from .floor_proc_mapping import FloorProcMapping

from .processor import Processor
from .quick_controls import QuickControl, QuickControlArea, QuickControlAreaAction
from .schedule import Schedule, ScheduleGroups
from .theme_model import Theme
from .user_model import User, UserPermission
from .zone import Zone
from .email_settings import EmailServerSettings
from .area_energy_stats import AreaEnergyStat
from .help import HelpUpload
from .area_occupancy_stats import AreaOccupancyStat
from .activity_logs import ActivityLog 
from .energy_saving import AreaEnergySavingByStrategy
from .drivers import Driver
from .sensors_and_modules import SensorAndModule
from .occupancy_logs import OccupancyLog
from .activity_report import ActivityReport
from .home import HomePageContent
from .widget_title import WidgetTitle
from .dashboard_chart_order import DashboardChartOrder
from .installation_settings import InstallationSettings
from .widget_configuration import WidgetConfiguration
from .dashboard_layout import DashboardLayout
from .alert_type_display_settings import AlertTypeDisplaySetting
from .fofp import FOFPShape, ZoneFloorplanPosition
from .fofp_settings import FOFPSettings
