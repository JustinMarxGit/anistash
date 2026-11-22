"""Download locations: SMB/NFS shares mounted through anidl-helper, or local folders.

The SMB password goes straight to the helper (which stores it root-only in
/etc/anidl/creds) and is never written to the app database.
"""

import asyncio
import json
import os
import shutil
import uuid

from . import config, db

SMB_FIELDS = ("server", "share", "subpath", "username", "domain", "vers")
NFS_FIELDS = ("server", "export", "options")


class LocationError(Exception):
    pass


async def _helper(*args: str, payload: dict | None = None) -> dict:
    if not config.helper_available():
        raise LocationError("Network shares can only be added in the anidl LXC (anidl-helper isn't installed here).")
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", config.HELPER, *args,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(payload or {}).encode()), 120)
    except asyncio.TimeoutError:
        proc.kill()
        raise LocationError("Timed out talking to the share. Is the server reachable?")
    try:
        result = json.loads(out.decode() or "{}")
    except json.JSONDecodeError:
        raise LocationError(err.decode().strip() or "anidl-helper failed")
    if not result.get("ok"):
        raise LocationError(result.get("error") or err.decode().strip() or "anidl-helper failed")
    return result


def to_dict(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "type": row["type"], "path": row["path"],
        "server_path": row["server_path"], "is_default": bool(row["is_default"]),
        "config": json.loads(row["config"]),
    }


def get(location_id: int):
    row = db.query_one("SELECT * FROM locations WHERE id = ?", (location_id,))
    if not row:
        raise LocationError("That download location doesn't exist.")
    return row


def all_locations() -> list[dict]:
    return [to_dict(r) for r in db.query("SELECT * FROM locations ORDER BY is_default DESC, name")]


def default_id() -> int | None:
    row = db.query_one("SELECT id FROM locations ORDER BY is_default DESC, id LIMIT 1")
    return row["id"] if row else None


def status(row) -> dict:
    path = row["path"]
    info = {"mounted": True, "writable": False, "free_bytes": None, "total_bytes": None}
    if row["type"] != "local":
        info["mounted"] = os.path.ismount(path)
    if info["mounted"] and os.path.isdir(path):
        info["writable"] = os.access(path, os.W_OK)
        try:
            usage = shutil.disk_usage(path)
            info["free_bytes"], info["total_bytes"] = usage.free, usage.total
        except OSError:
            pass
    return info


def write_test(path: str) -> None:
    """Writes and deletes a file as the app user, so a read-only share fails here and not mid-download."""
    probe = os.path.join(path, f".anidl-write-test-{uuid.uuid4().hex[:8]}")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as exc:
        raise LocationError(f"Can't write to {path}: {exc.strerror or exc}")


def _clean(data: dict) -> tuple[str, str, dict, str | None, str]:
    name = (data.get("name") or "").strip()
    kind = data.get("type")
    if not name:
        raise LocationError("Give the location a name.")
    if kind == "smb":
        cfg = {k: str(data.get(k) or "").strip() for k in SMB_FIELDS}
        if not cfg["server"] or not cfg["share"]:
            raise LocationError("Server and share are required.")
        password = data.get("password")
        password = password if password not in (None, "") else None
    elif kind == "nfs":
        cfg = {k: str(data.get(k) or "").strip() for k in NFS_FIELDS}
        if not cfg["server"] or not cfg["export"]:
            raise LocationError("Server and export path are required.")
        password = None
    elif kind == "local":
        path = os.path.normpath((data.get("path") or "").strip())
        if not path.startswith("/"):
            raise LocationError("Enter an absolute path, like /mnt/media/anime.")
        cfg, password = {"path": path}, None
    else:
        raise LocationError("Pick SMB, NFS or local folder.")
    return name, kind, cfg, password, (data.get("server_path") or "").strip()


async def _activate(location_id: int, kind: str, cfg: dict, password: str | None) -> str:
    if kind == "local":
        if config.helper_available():
            await _helper("prepare-local", payload={"path": cfg["path"]})
        else:
            os.makedirs(cfg["path"], exist_ok=True)
        path = cfg["path"]
    else:
        payload = {"id": location_id, "type": kind, **cfg}
        if password is not None:
            payload["password"] = password
        path = (await _helper("mount", payload=payload))["path"]
    write_test(path)
    return path


async def create(data: dict) -> dict:
    name, kind, cfg, password, server_path = _clean(data)
    first = default_id() is None
    path = cfg.get("path") or str(config.MOUNT_ROOT / "pending")
    location_id = db.execute(
        "INSERT INTO locations (name, type, config, path, server_path, is_default) VALUES (?, ?, ?, ?, ?, ?)",
        (name, kind, json.dumps(cfg), path, server_path, int(first)),
    )
    try:
        path = await _activate(location_id, kind, cfg, password)
    except Exception:
        # A location that can't be written to is only clutter, so roll it back
        if kind != "local":
            try:
                await _helper("unmount", str(location_id))
            except LocationError:
                pass
        db.execute("DELETE FROM locations WHERE id = ?", (location_id,))
        raise
    db.execute("UPDATE locations SET path = ? WHERE id = ?", (path, location_id))
    return to_dict(get(location_id))


async def update(location_id: int, data: dict) -> dict:
    row = get(location_id)
    name, kind, cfg, password, server_path = _clean({**data, "type": row["type"]})
    path = await _activate(location_id, kind, cfg, password)
    db.execute(
        "UPDATE locations SET name = ?, config = ?, path = ?, server_path = ? WHERE id = ?",
        (name, json.dumps(cfg), path, server_path, location_id),
    )
    return to_dict(get(location_id))


async def test(location_id: int) -> dict:
    row = get(location_id)
    if row["type"] != "local" and not os.path.ismount(row["path"]):
        await _helper("remount", str(location_id))
    write_test(row["path"])
    return status(get(location_id))


async def remove(location_id: int) -> None:
    row = get(location_id)
    busy = db.query_one(
        "SELECT COUNT(*) AS n FROM items JOIN batches ON batches.id = items.batch_id "
        "WHERE batches.location_id = ? AND items.status IN ('queued', 'running')", (location_id,))
    if busy["n"]:
        raise LocationError(f"{busy['n']} queued download(s) still use this location. Cancel them first.")
    if row["type"] != "local":
        await _helper("unmount", str(location_id))
    with db.tx() as conn:
        conn.execute("DELETE FROM items WHERE batch_id IN (SELECT id FROM batches WHERE location_id = ?)", (location_id,))
        conn.execute("DELETE FROM batches WHERE location_id = ?", (location_id,))
        conn.execute("DELETE FROM locations WHERE id = ?", (location_id,))
        if row["is_default"]:
            conn.execute("UPDATE locations SET is_default = 1 WHERE id = (SELECT MIN(id) FROM locations)")


def set_default(location_id: int) -> None:
    get(location_id)
    with db.tx() as conn:
        conn.execute("UPDATE locations SET is_default = (id = ?)", (location_id,))
