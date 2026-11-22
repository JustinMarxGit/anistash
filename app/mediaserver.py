"""Jellyfin and Plex: connection test, library list, targeted scans and series lookup.

anistash never asks the media server to scan everything. In order of preference it:
  1. scans just the new season folders, when the location has a "media server sees
     this as" path (Jellyfin: POST /Library/Media/Updated, Plex: refresh?path=...);
  2. otherwise refreshes only the chosen anime library;
  3. otherwise does nothing and warns that no library is chosen.
Scans are debounced: folders collect for DEBOUNCE seconds after the last download
and go out together, so a busy queue doesn't trigger a scan per file.
"""

import asyncio
import logging
import time

import httpx

from . import db, net, vault

DEBOUNCE = 30
KINDS = ("none", "jellyfin", "plex")
log = logging.getLogger("anistash.mediaserver")

_pending: set[str] = set()
_pending_batches: set[int] = set()
_library_refresh = False
_timer: asyncio.Task | None = None


class MediaServerError(Exception):
    pass


def configured(settings: dict | None = None) -> bool:
    s = settings or db.get_settings()
    return s["media_server"] in ("jellyfin", "plex") and bool(s["media_url"] and s["media_token"])


def _label(kind: str) -> str:
    return {"jellyfin": "Jellyfin", "plex": "Plex"}.get(kind, "Media server")


async def _request(method: str, path: str, *, kind: str | None = None, url: str | None = None,
                   token: str | None = None, **kwargs) -> httpx.Response:
    s = db.get_settings()
    kind = kind or s["media_server"]
    url = (url or s["media_url"]).rstrip("/")
    token = token or vault.decrypt(s["media_token"])
    if kind not in ("jellyfin", "plex") or not url or not token:
        raise MediaServerError("No media server is set up.")
    if kind == "jellyfin":
        headers = {"Authorization": f'MediaBrowser Token="{token}"', "Accept": "application/json"}
    else:
        headers = {"X-Plex-Token": token, "Accept": "application/json",
                   "X-Plex-Product": "anistash", "X-Plex-Client-Identifier": "anistash"}
    try:
        resp = await net.client("mediaserver").request(method, url + path, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise MediaServerError(f"Can't reach {_label(kind)} at {url}: {exc.__class__.__name__}") from exc
    if resp.status_code == 401:
        raise MediaServerError(f"{_label(kind)} rejected the {'API key' if kind == 'jellyfin' else 'token'}.")
    if resp.status_code >= 400:
        raise MediaServerError(f"{_label(kind)} returned HTTP {resp.status_code} for {path.split('?')[0]}.")
    return resp


async def test(kind: str | None = None, url: str | None = None, token: str | None = None) -> dict:
    kind = kind or db.get_setting("media_server")
    if kind == "jellyfin":
        info = (await _request("GET", "/System/Info", kind=kind, url=url, token=token)).json()
        return {"name": info.get("ServerName"), "version": info.get("Version"), "kind": "Jellyfin"}
    if kind == "plex":
        info = (await _request("GET", "/", kind=kind, url=url, token=token)).json().get("MediaContainer", {})
        return {"name": info.get("friendlyName"), "version": info.get("version"), "kind": "Plex"}
    raise MediaServerError("Choose Jellyfin or Plex first.")


async def libraries(kind: str | None = None, url: str | None = None, token: str | None = None) -> list[dict]:
    """[{id, name, type, paths}] for every library on the server."""
    kind = kind or db.get_setting("media_server")
    if kind == "jellyfin":
        folders = (await _request("GET", "/Library/VirtualFolders", kind=kind, url=url, token=token)).json()
        return [{"id": f["ItemId"], "name": f.get("Name"), "type": f.get("CollectionType") or "mixed",
                 "paths": f.get("Locations") or []} for f in folders if f.get("ItemId")]
    if kind == "plex":
        data = (await _request("GET", "/library/sections", kind=kind, url=url, token=token)).json()
        return [{"id": str(d["key"]), "name": d.get("title"), "type": d.get("type"),
                 "paths": [loc.get("path") for loc in d.get("Location") or [] if loc.get("path")]}
                for d in data.get("MediaContainer", {}).get("Directory") or []]
    raise MediaServerError("Choose Jellyfin or Plex first.")


async def library_series(library_id: str) -> list[dict]:
    """Every series in a library: [{name, original_name, episodes}]."""
    kind = db.get_setting("media_server")
    if kind == "jellyfin":
        resp = await _request("GET", "/Items", params={
            "ParentId": library_id, "IncludeItemTypes": "Series", "Recursive": "true",
            "Fields": "OriginalTitle,RecursiveItemCount", "EnableImages": "false", "Limit": 5000})
        return [{"name": i.get("Name", ""), "original_name": i.get("OriginalTitle", ""),
                 "episodes": i.get("RecursiveItemCount")} for i in resp.json().get("Items", [])]
    if kind == "plex":
        resp = await _request("GET", f"/library/sections/{library_id}/all", params={"type": 2})
        return [{"name": m.get("title", ""), "original_name": m.get("originalTitle", ""), "episodes": m.get("leafCount")}
                for m in resp.json().get("MediaContainer", {}).get("Metadata") or []]
    return []


async def find_series(name: str) -> bool:
    s = db.get_settings()
    wanted = name.lower()
    if s["media_server"] == "jellyfin":
        resp = await _request("GET", "/Items", params={
            "searchTerm": name, "IncludeItemTypes": "Series", "Recursive": "true", "Limit": 5})
        return any(i.get("Name", "").lower() == wanted for i in resp.json().get("Items", []))
    if s["media_server"] == "plex" and s["media_library_id"]:
        resp = await _request("GET", f"/library/sections/{s['media_library_id']}/all",
                              params={"type": 2, "title": name})
        return any(m.get("title", "").lower() == wanted for m in resp.json().get("MediaContainer", {}).get("Metadata") or [])
    return False


def map_path(path: str, location) -> str | None:
    """The path the media server sees for a folder under `location`, or None without a mapping."""
    if not location["server_path"]:
        return None
    root = location["path"].rstrip("/")
    if path != root and not path.startswith(root + "/"):
        return None
    return location["server_path"].rstrip("/") + path[len(root):]


def notify(paths: list[str], location, batch_id: int) -> None:
    """Queues a scan for these folders. Without a path mapping, the anime library is refreshed instead."""
    global _library_refresh, _timer
    if not configured():
        return
    _pending_batches.add(batch_id)
    mapped = [map_path(p, location) for p in paths]
    if any(m is None for m in mapped):
        _library_refresh = True
    _pending.update(m for m in mapped if m)
    if _timer and not _timer.done():
        _timer.cancel()
    _timer = asyncio.get_running_loop().create_task(_flush_later())


async def _flush_later() -> None:
    await asyncio.sleep(DEBOUNCE)
    await flush()


async def _scan(kind: str, library_id: str, folders: list[str], whole_library: bool) -> None:
    if kind == "jellyfin":
        if folders:
            await _request("POST", "/Library/Media/Updated", json={
                "Updates": [{"Path": p, "UpdateType": "Created"} for p in folders]})
        if whole_library:
            await _request("POST", f"/Items/{library_id}/Refresh", params={
                "Recursive": "true", "MetadataRefreshMode": "Default", "ImageRefreshMode": "Default",
                "ReplaceAllMetadata": "false", "ReplaceAllImages": "false"})
    else:
        if whole_library:
            await _request("GET", f"/library/sections/{library_id}/refresh")
        else:
            for folder in folders:
                await _request("GET", f"/library/sections/{library_id}/refresh", params={"path": folder})


def pick_library(libs: list[dict]) -> dict | None:
    """The library anime most likely lives in: one named like "anime" (a show library
    first), else the only TV library. None when it's a guess between several."""
    shows = [l for l in libs if l["type"] in ("tvshows", "show")]
    for group in (shows, libs):
        named = [l for l in group if "anime" in (l["name"] or "").lower()]
        if len(named) == 1:
            return named[0]
    return shows[0] if len(shows) == 1 else None


async def ensure_library() -> str:
    """The saved anime library id. If none was saved, choose one the same way the settings
    page suggests it, save it, and log that it happened."""
    s = db.get_settings()
    if s["media_library_id"]:
        return s["media_library_id"]
    choice = pick_library(await libraries())
    if not choice:
        raise MediaServerError("no anime library is chosen and there's more than one it could be. "
                               "Pick it under Settings → Media server and press Save")
    db.set_settings(media_library_id=choice["id"], media_library_name=choice["name"])
    from . import auth  # imported here: auth doesn't need the media server at import time
    auth.audit("AniStash", "media_library_auto", f"chose the {choice['name']} library")
    log.info("No anime library was saved; chose %s", choice["name"])
    return choice["id"]


async def flush() -> None:
    global _library_refresh
    folders, whole_library, batches = sorted(_pending), _library_refresh, sorted(_pending_batches)
    _pending.clear()
    _pending_batches.clear()
    _library_refresh = False
    if not folders and not whole_library:
        return
    kind = db.get_setting("media_server")
    try:
        library_id = db.get_setting("media_library_id")
        # Plex scans folders through a library too, so it always needs one
        if whole_library or kind == "plex":
            library_id = await ensure_library()
        await _scan(kind, library_id, folders, whole_library)
        library_name = db.get_setting("media_library_name") or library_id
        what = f"{_label(kind)} ({library_name} library)" if whole_library or kind == "plex" else f"{_label(kind)} (just the new folders)"
        log.info("Asked %s to scan", what)
        for batch_id in batches:
            db.execute("UPDATE batches SET scanned_at = ?, scanned_with = ?, "
                       "warning = CASE WHEN warning LIKE '%scan failed%' THEN '' ELSE warning END WHERE id = ?",
                       (int(time.time()), what, batch_id))
    except MediaServerError as exc:
        # Never fails a download: the batch just shows a warning
        log.warning("%s scan failed: %s", _label(kind), exc)
        for batch_id in batches:
            db.execute("UPDATE batches SET warning = ? WHERE id = ?", (f"{_label(kind)} scan failed: {exc}", batch_id))
