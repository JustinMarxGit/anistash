"""Paths and runtime settings, all overridable through the environment.

In the LXC these come from /etc/anistash/anistash.env (loaded by the systemd unit).
For local development, set ANISTASH_DATA_DIR to somewhere writable.
"""

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
VERSION = (APP_DIR.parent / "VERSION").read_text().strip()
REPO = os.environ.get("ANISTASH_REPO", "JustinMarxGit/anistash")

DATA_DIR = Path(os.environ.get("ANISTASH_DATA_DIR", "/var/lib/anistash"))
DB_PATH = DATA_DIR / "anistash.db"
TMP_DIR = DATA_DIR / "tmp"

MOUNT_ROOT = Path(os.environ.get("ANISTASH_MOUNT_ROOT", "/mnt/anistash"))
HELPER = os.environ.get("ANISTASH_HELPER", "/usr/local/lib/anistash/anistash-helper")
# How the helper is run as root; development can set ANISTASH_SUDO="" to call a stand-in directly
SUDO = os.environ.get("ANISTASH_SUDO", "sudo -n").split()

ANI_CLI = os.environ.get("ANISTASH_ANI_CLI", "ani-cli")
MENU_CAPTURE = str(APP_DIR / "menu_capture.sh")

# Allowed prefixes for "local path" locations. Kept in sync with helper/anistash-helper.
LOCAL_PATH_PREFIXES = ("/mnt/", "/media/", "/srv/", "/data/", "/downloads/", "/storage/", "/home/")


def helper_available() -> bool:
    return os.path.exists(HELPER)
