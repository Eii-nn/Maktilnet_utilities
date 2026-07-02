"""
User-facing installation steps for the preflight GUI.

Technical logs stay in the hidden details panel; these labels are what
non-technical operators see (Windows-installer style).
"""

INSTALL_STEPS = [
    {
        "id": "find_router",
        "label": "Finding your router",
        "subs": [
            {"id": "scan", "label": "Searching for router"},
            {"id": "connect", "label": "Connecting to router"},
        ],
    },
    {
        "id": "prepare",
        "label": "Preparing the router",
        "subs": [
            {"id": "network", "label": "Setting up network"},
            {"id": "firmware", "label": "Updating software"},
            {"id": "wireless_check", "label": "Checking wireless package"},
            {"id": "wireless_upload", "label": "Uploading wireless package"},
            {"id": "wireless_install", "label": "Installing wireless package"},
            {"id": "wireless_reboot", "label": "Waiting for router to restart"},
        ],
    },
    {
        "id": "clean",
        "label": "Cleaning old settings",
        "subs": [
            {"id": "remove_files", "label": "Removing old files"},
        ],
    },
    {
        "id": "install_packages",
        "label": "Installing packages",
        "subs": [
            {"id": "certs", "label": "Uploading certificates"},
            {"id": "hotspot", "label": "Uploading hotspot"},
            {"id": "config", "label": "Preparing configuration"},
            {"id": "script", "label": "Uploading setup script"},
        ],
    },
    {
        "id": "apply",
        "label": "Applying configuration",
        "subs": [
            {"id": "reset", "label": "Restarting router — do not unplug"},
        ],
    },
    {
        "id": "finish",
        "label": "Finishing up",
        "subs": [],
    },
]

STEP_ORDER = [step["id"] for step in INSTALL_STEPS]

SUB_LABELS = {
    (step["id"], sub["id"]): sub["label"]
    for step in INSTALL_STEPS
    for sub in step.get("subs", [])
}

FRIENDLY_ERRORS = {
    "authentication": "Could not connect. Please check the password and try again.",
    "connection": "Could not reach the router. Make sure it is powered on and connected.",
    "upload": "A file could not be uploaded. Please try again or contact support.",
    "default": "Something went wrong. Expand technical details below or contact support.",
}


def friendly_error_message(exc: Exception) -> str:
    message = str(exc).lower()
    if "auth" in message or "password" in message or "login failed" in message:
        return FRIENDLY_ERRORS["authentication"]
    if "connect" in message or "timeout" in message or "mactelnet" in message:
        return FRIENDLY_ERRORS["connection"]
    if "upload" in message or "scp" in message:
        return FRIENDLY_ERRORS["upload"]
    return FRIENDLY_ERRORS["default"]
