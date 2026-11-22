"""What you already have: series in the download folders and in the media server's library.

Both are indexed by a normalized series name (year and season suffix removed, case and
punctuation ignored), so "Blue Box Season 2", "Blue Box (2024)" and "BLUE BOX" are the
same show. The index is cached briefly and refreshed after each finished download.
"""

import asyncio
import logging
import os
import re
import time

from . import db, locations, mediaserver
from .naming import base_title, detect_season, normalize

log = logging.getLogger("anistash.library")

TTL = 300
_YEAR = re.compile(r"\s*\((19|20)\d{2}\)\s*$")
_SEASON_DIR = re.compile(r"^season\s*(\d+)$|^specials?$", re.IGNORECASE)
_cache: dict[str, tuple[float, object]] = {}


def series_key(name: str) -> str:
    return normalize(base_title(_YEAR.sub("", name or "")))


def invalidate() -> None:
    _cache.clear()


def _count_videos(path: str) -> int:
    try:
        return sum(1 for f in os.scandir(path) if f.is_file() and f.name.lower().endswith((".mp4", ".mkv")))
    except OSError:
        return 0


def _scan_disk() -> dict[str, dict]:
    """{series key: {name, location, seasons: {n: episodes}}} for every location's series folders."""
    index: dict[str, dict] = {}
    for loc in locations.all_locations():
        row = locations.get(loc["id"])
        if not locations.is_mounted(row) or not os.path.isdir(row["path"]):
            continue
        try:
            folders = [e for e in os.scandir(row["path"]) if e.is_dir() and not e.name.startswith(".")]
        except OSError:
            continue
        for folder in folders:
            seasons: dict[int, int] = {}
            try:
                for sub in os.scandir(folder.path):
                    match = _SEASON_DIR.match(sub.name) if sub.is_dir() else None
                    if match:
                        count = _count_videos(sub.path)
                        if count:
                            seasons[int(match.group(1) or 0)] = count
            except OSError:
                continue
            loose = _count_videos(folder.path)  # "original" naming keeps episodes in the series folder
            if loose:
                seasons[1] = seasons.get(1, 0) + loose
            if not seasons:
                continue
            entry = index.setdefault(series_key(folder.name), {"name": folder.name, "locations": [], "seasons": {}})
            entry["locations"].append(loc["name"])
            for number, count in seasons.items():
                entry["seasons"][number] = entry["seasons"].get(number, 0) + count
    return index


async def _scan_server() -> dict[str, dict]:
    """{series key: {name, episodes}} from the media server's anime library (empty when not set up)."""
    settings = db.get_settings()
    if not mediaserver.configured(settings) or not settings["media_library_id"]:
        return {}
    try:
        series = await mediaserver.library_series(settings["media_library_id"])
    except mediaserver.MediaServerError as exc:
        log.warning("Couldn't list the media server library: %s", exc)
        return {}
    index = {}
    for s in series:
        for name in filter(None, (s["name"], s.get("original_name"))):
            index.setdefault(series_key(name), {"name": s["name"], "episodes": s.get("episodes")})
    return index


async def _index() -> tuple[dict, dict]:
    hit = _cache.get("index")
    if hit and hit[0] > time.time():
        return hit[1]
    disk, server = await asyncio.gather(asyncio.to_thread(_scan_disk), _scan_server())
    _cache["index"] = (time.time() + TTL, (disk, server))
    return disk, server


async def match(items: list[dict]) -> dict:
    """For each {id, titles} item: what's already in the library, or nothing.

    A match looks like {"seasons": {"1": 25, "2": 7}, "episodes": 32, "locations": [...],
    "server": "Jellyfin", "server_episodes": 32, "season": 2, "season_episodes": 7}
    where season/season_episodes describe the season the title itself names.
    """
    disk, server = await _index()
    server_label = {"jellyfin": "Jellyfin", "plex": "Plex"}.get(db.get_setting("media_server"), "")
    out = {}
    for item in items:
        titles = [t for t in item.get("titles") or [] if t][:8]
        keys = {series_key(t) for t in titles} - {""}
        on_disk = next((disk[k] for k in keys if k in disk), None)
        on_server = next((server[k] for k in keys if k in server), None)
        if not on_disk and not on_server:
            continue
        result = {"seasons": {}, "episodes": 0, "locations": [], "server": "", "server_episodes": None}
        if on_disk:
            result.update(seasons={str(k): v for k, v in sorted(on_disk["seasons"].items())},
                          episodes=sum(on_disk["seasons"].values()), locations=sorted(set(on_disk["locations"])))
        if on_server:
            result.update(server=server_label, server_episodes=on_server.get("episodes"))
        # A title that names its season ("… Season 3") wins over a plain one, which means season 1
        named = [detect_season(t) for t in titles if base_title(t) != t.strip() and detect_season(t)]
        plain = [detect_season(t) for t in titles if detect_season(t)]
        season = named[0] if named else (plain[0] if plain else None)
        if season is not None:
            result["season"] = season
            result["season_episodes"] = (on_disk or {}).get("seasons", {}).get(season, 0) if on_disk else 0
        out[str(item.get("id"))] = result
    return out
