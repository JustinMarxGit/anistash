"""Download locations: SMB/NFS shares mounted through anistash-helper, or local folders.

The SMB password goes straight to the helper (which stores it root-only in
/etc/anistash/creds) and is never written to the app database.
"""

import asyncio
import json
import os
import re
from urllib.parse import unquote
import shutil
import uuid

from . import config, db

SMB_FIELDS = ("server", "share", "subpath", "username", "domain", "vers")
NFS_FIELDS = ("server", "export", "options")


class LocationError(Exception):
    pass


async def _helper(*args: str, payload: dict | None = None) -> dict:
    if not config.helper_available():
        raise LocationError("Network shares can only be added in the anistash LXC (anistash-helper isn't installed here).")
    proc = await asyncio.create_subprocess_exec(
        *config.SUDO, config.HELPER, *args,
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
        raise LocationError(err.decode().strip() or "anistash-helper failed")
    if not result.get("ok"):
        raise LocationError(result.get("error") or err.decode().strip() or "anistash-helper failed")
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


def is_mounted(row) -> bool:
    """Whether a share's mount is up. The location's path may be a folder inside the share,
    so check the mount point itself (MOUNT_ROOT/loc<id>), not the path."""
    if row["type"] == "local":
        return True
    return os.path.ismount(config.MOUNT_ROOT / f"loc{row['id']}")


def status(row) -> dict:
    path = row["path"]
    info = {"mounted": True, "writable": False, "free_bytes": None, "total_bytes": None}
    if row["type"] != "local":
        info["mounted"] = is_mounted(row)
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
    probe = os.path.join(path, f".anistash-write-test-{uuid.uuid4().hex[:8]}")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as exc:
        raise LocationError(f"Can't write to {path}: {exc.strerror or exc}")


_SMB_URL = re.compile(r"^(?:smb|cifs)://([^/\\]+)[/\\]?(.*)$", re.IGNORECASE)
_UNC = re.compile(r"^(?:\\\\|//)([^/\\]+)[/\\]?(.*)$")
_NFS_URL = re.compile(r"^nfs://([^/]+)(/.*)?$", re.IGNORECASE)
_NFS = re.compile(r"^([A-Za-z0-9.\-]+):(/.*)$")


def _split_path(text: str) -> list[str]:
    # Addresses copied from a browser or file manager encode spaces as %20
    parts = [unquote(p).strip() for p in re.split(r"[/\\]+", text.strip())]
    parts = [p for p in parts if p]
    if any(p in (".", "..") for p in parts):
        raise LocationError("Folder names can't be . or ..")
    return parts


def parse_address(text: str) -> dict:
    """Reads a share address the way people paste it.

    SMB: //server/share/folder, \\\\server\\share\\folder, smb://server/share/folder
    NFS: server:/export/path, nfs://server/export/path
    """
    text = text.strip()
    match = _SMB_URL.match(text) or _UNC.match(text)
    if match:
        parts = _split_path(match.group(2))
        if not parts:
            raise LocationError(f"Add the share name after the server: //{match.group(1)}/<share>. "
                                "Use Find shares to list them.")
        return {"type": "smb", "server": match.group(1), "share": parts[0], "subpath": "/".join(parts[1:])}
    match = _NFS_URL.match(text) or _NFS.match(text)
    if match:
        return {"type": "nfs", "server": match.group(1), "export": "/" + "/".join(_split_path(match.group(2) or "/"))}
    raise LocationError("Enter an address like //192.168.1.10/share/folder (SMB) or 192.168.1.10:/export/path (NFS).")


def _clean(data: dict) -> tuple[str, str, dict, str | None, str]:
    name = (data.get("name") or "").strip()
    kind = data.get("type")
    if kind in ("smb", "nfs") and (data.get("address") or "").strip():
        parsed = parse_address(data["address"])
        if parsed["type"] != kind:
            raise LocationError(f"That looks like an {parsed['type'].upper()} address. Pick {parsed['type'].upper()} as the type.")
        data = {**data, **parsed}
    if kind == "smb":
        cfg = {k: str(data.get(k) or "").strip() for k in SMB_FIELDS}
        # A path typed into the share box (/datastore/jellyfin/anime): first part is the share
        parts = _split_path(cfg["share"])
        cfg["share"] = parts[0] if parts else ""
        cfg["subpath"] = "/".join(parts[1:] + _split_path(cfg["subpath"]))
        cfg["server"] = cfg["server"].strip("/\\")
        if not cfg["server"] or not cfg["share"]:
            raise LocationError("Enter the share address, e.g. //192.168.1.10/share/folder.")
        password = data.get("password")
        password = password if password not in (None, "") else None
    elif kind == "nfs":
        cfg = {k: str(data.get(k) or "").strip() for k in NFS_FIELDS}
        if not cfg["server"] or not cfg["export"]:
            raise LocationError("Enter the export address, e.g. 192.168.1.10:/mnt/pool/anime.")
        password = None
    elif kind == "local":
        path = os.path.normpath((data.get("path") or "").strip())
        if not path.startswith("/"):
            raise LocationError("Enter an absolute path, like /mnt/media/anime.")
        cfg, password = {"path": path}, None
    else:
        raise LocationError("Pick SMB, NFS or local folder.")
    if not name:
        # Named after where it points, e.g. "anime on media"
        if kind == "smb":
            name = f"{cfg['subpath'].split('/')[-1]} on {cfg['share']}" if cfg["subpath"] else cfg["share"]
        else:
            name = os.path.basename((cfg.get("export") or cfg.get("path") or "").rstrip("/")) or cfg.get("server", "Downloads")
    return name, kind, cfg, password, (data.get("server_path") or "").strip()


async def _activate(location_id: int, kind: str, cfg: dict, password: str | None) -> str:
    if kind == "local":
        if config.helper_available():
            await _helper("prepare-local", payload={"path": cfg["path"]})
        else:
            os.makedirs(cfg["path"], exist_ok=True)
        path = cfg["path"]
    else:
        payload = {"id": location_id, "type": kind, **{k: v for k, v in cfg.items() if k != "subpath"}}
        if password is not None:
            payload["password"] = password
        path = (await _helper("mount", payload=payload))["path"]
        # The share itself is mounted; the folder inside it is created if it's new
        if cfg.get("subpath"):
            path = os.path.join(path, *cfg["subpath"].split("/"))
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as exc:
                raise LocationError(f"Mounted the share but can't create {cfg['subpath']} in it: {exc.strerror or exc}")
    write_test(path)
    return path


async def browse(data: dict) -> list[dict]:
    """Shares on an SMB server, or exports on an NFS server, for the Find shares button."""
    kind = data.get("type")
    text = (data.get("address") or data.get("server") or "").strip()
    # Only the server part matters here, whatever form the address is in
    server = re.sub(r"^(?:smb|cifs|nfs)://|^[/\\]+", "", text, flags=re.IGNORECASE)
    server = re.split(r"[/\\]|:/", server, maxsplit=1)[0]
    if not server:
        raise LocationError("Enter the server's address first (e.g. 192.168.1.10).")
    payload = {"type": kind, "server": server, "suggest": bool(data.get("suggest"))}
    if kind == "smb":
        payload.update(_smb_login(data))
    result = await _helper("list-shares", payload=payload)
    return {"server": server, "shares": result.get("shares", []), "suggestions": result.get("suggestions", [])}


def _smb_login(data: dict) -> dict:
    return {"username": (data.get("username") or "").strip(), "password": data.get("password") or "",
            "domain": (data.get("domain") or "").strip()}


async def folders(data: dict) -> list[str]:
    """Folders inside an SMB share, for browsing to the download folder."""
    payload = {"server": (data.get("server") or "").strip(), "share": data.get("share") or "",
               "path": [p for p in data.get("path") or [] if p], **_smb_login(data)}
    return (await _helper("list-folders", payload=payload)).get("folders", [])


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
    if not is_mounted(row):
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
