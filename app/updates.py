"""Automatic AniStash updates.

When enabled (an admin setting), a new GitHub release is installed by itself through
anistash-helper → anistash-update, the same path as the Update button, which rolls back
if the new version doesn't start. It waits until nothing is downloading, and won't retry
a version that already failed for 12 hours.
"""

import asyncio
import logging
import time

from . import auth, config, db, locations, setup

log = logging.getLogger("anistash.updates")

CHECK_EVERY = 30 * 60
RETRY_AFTER = 12 * 3600


def status() -> dict:
    return {"current": config.VERSION, "auto_update": bool(db.get_setting("auto_update"))}


async def info() -> dict:
    latest = await setup.latest_release()
    return {**status(), "latest": latest, "update_available": setup._newer(latest, config.VERSION)}


def _busy() -> bool:
    return db.query_one("SELECT 1 FROM items WHERE status = 'running' LIMIT 1") is not None


async def start_update(actor: str, ip: str = "") -> None:
    """Starts anistash-update in the background (it restarts this service)."""
    await locations._helper("update-app")
    auth.audit(actor, "update_started", "", ip)


async def maybe_update() -> str:
    """One automatic-update check. Returns what happened (for logs and tests)."""
    if not db.get_setting("auto_update") or not config.helper_available():
        return "off"
    details = await info()
    if not details["update_available"]:
        return "up to date"
    if _busy():
        return "waiting for downloads"
    last_version, last_at = db.get_setting("auto_update_tried") or ["", 0]
    if last_version == details["latest"] and time.time() - last_at < RETRY_AFTER:
        return "already tried"
    db.set_settings(auto_update_tried=[details["latest"], int(time.time())])
    log.info("Installing AniStash %s automatically", details["latest"])
    await start_update("AniStash (automatic)")
    return "started"


async def scheduler() -> None:
    await asyncio.sleep(120)  # let the service settle after a start (or after an update)
    while True:
        try:
            await maybe_update()
        except Exception as exc:
            log.warning("Automatic update check failed: %s", exc)
        await asyncio.sleep(CHECK_EVERY)
