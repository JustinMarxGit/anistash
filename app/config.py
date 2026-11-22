"""Paths and runtime settings, all overridable through the environment.

In the LXC these come from /etc/anidl/anidl.env (loaded by the systemd unit).
For local development, set ANIDL_DATA_DIR to somewhere writable.
"""

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
VERSION = (APP_DIR.parent / "VERSION").read_text().strip()
REPO = os.environ.get("ANIDL_REPO", "JustinMarxGit/anidl")

DATA_DIR = Path(os.environ.get("ANIDL_DATA_DIR", "/var/lib/anidl"))
DB_PATH = DATA_DIR / "anidl.db"
TMP_DIR = DATA_DIR / "tmp"

MOUNT_ROOT = Path(os.environ.get("ANIDL_MOUNT_ROOT", "/mnt/anidl"))
HELPER = os.environ.get("ANIDL_HELPER", "/usr/local/lib/anidl/anidl-helper")

ANI_CLI = os.environ.get("ANIDL_ANI_CLI", "ani-cli")
MENU_CAPTURE = str(APP_DIR / "menu_capture.sh")

# Allowed prefixes for "local path" locations. Kept in sync with helper/anidl-helper.
LOCAL_PATH_PREFIXES = ("/mnt/", "/media/", "/srv/", "/data/", "/downloads/", "/storage/", "/home/")


def helper_available() -> bool:
    return os.path.exists(HELPER)
