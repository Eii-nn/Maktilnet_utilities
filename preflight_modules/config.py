import os
import sys

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION & CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

CONFIG_FILE = os.path.expanduser("~/.mikrotik_tool_config.json")
USERNAME = "admin"
STATIC_IP_ADD = "192.168.88.100"
SCAN_TIMEOUT = 60
HOTSPOT_DIR = "Download/hotspot"
PROMPT_REGEX = r"\[.*@.*\]\s+>\s*"

RouterOSDesiredVersion = "7.23.1"
NPKDIR = "Download/"
NPKFILNAME = "routeros-7.21.2-mipsbe.npk"

# Wireless constants
WIRELESS_PACKAGE = "Download/wireless-7.23.1-mipsbe.npk"
WIRELESS_PACKAGE_NAME = "wireless-7.23.1-mipsbe.npk"

FORCE_WIRELESS_UPLOAD = "--force-wireless-upload" in sys.argv or os.getenv(
    "FORCE_WIRELESS_UPLOAD", ""
).strip().lower() in {"1", "true", "yes", "on"}

WIRELESS_REBOOT_TIMEOUT = 300

DEVICE_TYPE = "mikrotik_routeros"

PREFLIGHT_RSC = "Download/preflight_config.rsc"


def get_mikrotik_device(password: str) -> dict:
    """Returns the Netmiko device connection dictionary."""
    return {
        "device_type": DEVICE_TYPE,
        "host": STATIC_IP_ADD,
        "username": USERNAME,
        "password": password,
    }
