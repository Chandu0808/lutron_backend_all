import logging
import sys

# File logging disabled - console output only
# Database logging (via log_activity and activity_report_log) is unaffected

formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

# -------------------------------
# General App Logger
# -------------------------------
logger = logging.getLogger("lutron_app")
logger.setLevel(logging.ERROR)
logger.propagate = False
logger.handlers.clear()

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# -------------------------------
# Listener Logger
# -------------------------------
listener_logger = logging.getLogger("lutron_listener")
listener_logger.setLevel(logging.INFO)
listener_logger.propagate = False
listener_logger.handlers.clear()

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)
listener_logger.addHandler(console_handler)

# -------------------------------
# Connection Logger
# -------------------------------
connection_logger = logging.getLogger("lutron_connection")
connection_logger.setLevel(logging.ERROR)
connection_logger.propagate = False
connection_logger.handlers.clear()

# -------------------------------
# Main Process Logger
# -------------------------------
main_logger = logging.getLogger("lutron_main")
main_logger.setLevel(logging.ERROR)
main_logger.propagate = False
main_logger.handlers.clear()

