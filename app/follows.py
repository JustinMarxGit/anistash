"""Followed shows: new episodes are queued automatically as they come out.

Each follow is one season entry (like a queued batch) plus a schedule. A check asks
ani-cli for the current episode list and queues every episode that isn't on disk,
queued, or one the user chose to skip. AniList's airing schedule decides when to check
next: shortly after the next episode airs, then hourly until it shows up on the site
(uploads lag the broadcast by hours, dubs by more). Finished shows are still checked
daily for late dubs and new seasons, then weekly once they've been quiet for a while.
When "new seasons" is on, a daily check looks for the series' next season and follows it.
"""

import asyncio
import json
import logging
import time

from . import anicli, anilist, db, jobs, locations, shows

log = logging.getLogger("anistash.follows")

HOUR = 3600
DAY = 24 * HOUR
AIRED_GRACE = 20 * 60      # first look this long after an episode airs
RETRY_FAILED_AFTER = 50 * 60
_wake = asyncio.Event()


class FollowError(Exception):
    pass


def now() -> int:
    return int(time.time())


def get(follow_id: int):
    row = db.query_one("SELECT * FROM follows WHERE id = ?", (follow_id,))
    if not row:
        raise FollowError("That show isn't followed.")
    return row


def to_dict(row) -> dict:
    owner = db.query_one("SELECT username FROM users WHERE id = ?", (row["created_by"],)) if row["created_by"] else None
    loc = db.query_one("SELECT name FROM locations WHERE id = ?", (row["location_id"],))
    return {
        "id": row["id"], "series_name": row["series_name"], "season": row["season"], "title": row["title"],
        "mode": row["mode"], "quality": row["quality"], "location_id": row["location_id"],
        "location": loc["name"] if loc else "?", "new_seasons": bool(row["new_seasons"]), "status": row["status"],
        "airing_status": row["airing_status"], "next_episode": row["next_episode"],
        "next_airing_at": row["next_airing_at"], "last_checked": row["last_checked"],
        "next_check": row["next_check"], "last_new_at": row["last_new_at"], "last_result": row["last_result"],
        "created_by": row["created_by"], "owner": owner["username"] if owner else "",
    }


def all_follows() -> list[dict]:
    return [to_dict(r) for r in db.query("SELECT * FROM follows ORDER BY series_name, season, title")]


async def create(entry: dict, series_name: str, location_id: int, mode: str, quality: str, created_by: int | None,
                 new_seasons: bool = True, only_new: bool = False) -> dict:
    """Follows one season entry ({query, index, title, season, offset}).

    only_new: episodes that exist right now are skipped and only later ones download.
    Otherwise the first check also queues whatever is missing.
    """
    series_name = series_name.strip()
    if not series_name:
        raise FollowError("Series name is required.")
    if mode not in ("sub", "dub"):
        raise FollowError("Mode must be sub or dub.")
    locations.get(location_id)
    existing = db.query_one("SELECT id FROM follows WHERE series_name = ? AND title = ? AND mode = ?",
                            (series_name, entry["title"], mode))
    if existing:
        raise FollowError(f"{entry['title']} ({mode}) is already followed.")
    skip = []
    if only_new:
        index = await anicli.resolve_index(entry["query"], int(entry["index"]), entry["title"], mode == "dub")
        skip = await anicli.episodes(entry["query"], index, mode == "dub", fresh=True)
    follow_id = db.execute(
        "INSERT INTO follows (series_name, season, episode_offset, query, result_index, title, mode, quality, "
        "location_id, created_by, new_seasons, skip_episodes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (series_name, int(entry["season"]), int(entry.get("offset") or 0), entry["query"], int(entry["index"]),
         entry["title"], mode, quality or "best", location_id, created_by, int(new_seasons), json.dumps(skip), now()))
    _wake.set()
    return to_dict(get(follow_id))


def update(follow_id: int, data: dict) -> dict:
    get(follow_id)
    fields = {}
    if "status" in data:
        if data["status"] not in ("active", "paused"):
            raise FollowError("Status must be active or paused.")
        fields["status"] = data["status"]
        if data["status"] == "active":
            fields["next_check"] = 0
    if "new_seasons" in data:
        fields["new_seasons"] = int(bool(data["new_seasons"]))
    if "location_id" in data:
        locations.get(int(data["location_id"]))
        fields["location_id"] = int(data["location_id"])
    if "quality" in data:
        fields["quality"] = str(data["quality"])
    if fields:
        db.execute(f"UPDATE follows SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                   (*fields.values(), follow_id))
    _wake.set()
    return to_dict(get(follow_id))


def check_soon(follow_id: int) -> None:
    get(follow_id)
    db.execute("UPDATE follows SET next_check = 0 WHERE id = ?", (follow_id,))
    _wake.set()


def remove(follow_id: int) -> None:
    get(follow_id)
    db.execute("DELETE FROM follows WHERE id = ?", (follow_id,))


def next_check_at(t: int, airing_status: str, next_airing_at: int | None, next_episode: int | None,
                  newest_on_site: float | None, last_new_at: int, mode: str) -> int:
    """When to look again, from AniList's schedule and what the site already has."""
    if airing_status in ("RELEASING", "NOT_YET_RELEASED"):
        latest_aired = (next_episode - 1) if next_episode else None
        if latest_aired and (newest_on_site is None or newest_on_site < latest_aired):
            return t + HOUR  # aired but not on the site yet: keep looking
        if next_airing_at and next_airing_at > t:
            # Look shortly after the next broadcast, but at least twice a day
            return min(next_airing_at + AIRED_GRACE, t + 12 * HOUR)
        return t + HOUR if mode == "dub" else t + 3 * HOUR
    if airing_status in ("FINISHED", "CANCELLED", "HIATUS"):
        if t - last_new_at < 14 * DAY:
            return t + 6 * HOUR   # dubs and late uploads still trickle in
        if t - last_new_at < 60 * DAY:
            return t + DAY
        return t + 7 * DAY
    return t + 3 * HOUR           # not on AniList: check regularly


def _newest_regular(episodes: list[str]) -> int | None:
    """Highest whole-numbered episode. Recaps like 12.5 don't mean episode 12 is out."""
    numbers = [int(e) for e in episodes if e.isdigit()]
    return max(numbers) if numbers else None


async def check(follow_id: int) -> str:
    """Queues new episodes for one follow and schedules its next check. Returns a summary."""
    row = get(follow_id)
    dub = row["mode"] == "dub"
    t = now()
    try:
        index = await anicli.resolve_index(row["query"], row["result_index"], row["title"], dub, fresh=True)
        available = await anicli.episodes(row["query"], index, dub, fresh=True)
    except anicli.AniCliError as exc:
        db.execute("UPDATE follows SET last_checked = ?, next_check = ?, last_result = ? WHERE id = ?",
                   (t, t + HOUR, f"Couldn't check: {exc}", follow_id))
        return str(exc)

    skip = set(json.loads(row["skip_episodes"]))
    wanted = [e for e in available if e not in skip]
    on_disk = jobs.existing_episodes(row["location_id"], row["series_name"], row["season"], row["title"], wanted,
                                     row["episode_offset"])
    previous = {r["episode"]: r for r in db.query(
        "SELECT items.id, items.episode, items.status, items.updated_at FROM items "
        "JOIN batches ON batches.id = items.batch_id WHERE batches.follow_id = ? ORDER BY items.id", (follow_id,))}

    new, retried = [], 0
    for episode in wanted:
        if episode in on_disk:
            continue
        item = previous.get(episode)
        if item is None:
            new.append(episode)
        elif item["status"] == "failed" and t - item["updated_at"] > RETRY_FAILED_AFTER:
            # e.g. listed before the dub was uploaded: try again quietly
            jobs.retry_item(item["id"])
            retried += 1
        # queued/running/done/cancelled: nothing to do (cancelled means the user said no)

    added = 0
    if new:
        result = jobs.enqueue(row["series_name"], row["location_id"], row["mode"], row["quality"],
                              [{"query": row["query"], "index": index, "title": row["title"], "season": row["season"],
                                "offset": row["episode_offset"], "episodes": new}],
                              created_by=row["created_by"], follow_id=follow_id, always_skip_existing=True)
        added = result["added"]

    airing = {"status": row["airing_status"], "next_episode": row["next_episode"], "next_airing_at": row["next_airing_at"]}
    anilist_id = row["anilist_id"]
    try:
        anilist_id = anilist_id or await anilist.media_id(row["title"])
        if anilist_id:
            airing = await anilist.airing(anilist_id)
    except Exception as exc:  # AniList being down only makes the schedule less precise
        log.warning("AniList airing lookup failed for %s: %s", row["title"], exc)

    last_new = t if added else row["last_new_at"] or row["created_at"]
    next_check = next_check_at(t, airing.get("status") or "", airing.get("next_airing_at"),
                               airing.get("next_episode"), _newest_regular(available), last_new, row["mode"])
    summary = (f"Queued {added} new episode{'s' if added != 1 else ''}" if added
               else f"Retrying {retried} episode{'s' if retried != 1 else ''}" if retried else "Up to date")
    db.execute(
        "UPDATE follows SET result_index = ?, anilist_id = ?, airing_status = ?, next_episode = ?, next_airing_at = ?, "
        "last_checked = ?, next_check = ?, last_new_at = ?, last_result = ? WHERE id = ?",
        (index, anilist_id, airing.get("status") or "", airing.get("next_episode"), airing.get("next_airing_at"),
         t, next_check, last_new, summary, follow_id))
    if added:
        log.info("%s S%02d: queued %d new episode(s)", row["series_name"], row["season"], added)
    if row["new_seasons"] and t - row["last_seasons_check"] > DAY:
        await find_new_seasons(follow_id)
    return summary


async def find_new_seasons(follow_id: int) -> list[dict]:
    """Follows seasons of the series that came out after the followed one."""
    row = get(follow_id)
    db.execute("UPDATE follows SET last_seasons_check = ? WHERE id = ?", (now(), follow_id))
    try:
        data = await shows.seasons(row["query"], row["result_index"], row["title"], row["mode"] == "dub")
    except Exception as exc:
        log.warning("New-season lookup failed for %s: %s", row["title"], exc)
        return []
    followed = {r["title"] for r in db.query("SELECT title FROM follows WHERE series_name = ? AND mode = ?",
                                              (row["series_name"], row["mode"]))}
    added = []
    for entry in data["seasons"]:
        newer = entry["season"] > row["season"] or (entry["season"] == row["season"] and entry["part"] > 1
                                                     and entry["title"] != row["title"])
        if entry["special"] or not newer or entry["title"] in followed:
            continue
        try:
            follow = await create(entry, row["series_name"], row["location_id"], row["mode"], row["quality"],
                                  row["created_by"], new_seasons=bool(row["new_seasons"]))
        except FollowError:
            continue
        log.info("Found a new season of %s: %s", row["series_name"], entry["title"])
        added.append(follow)
    return added


async def scheduler() -> None:
    """Runs due checks one at a time, so ani-cli and the site aren't hammered."""
    while True:
        try:
            due = db.query("SELECT id FROM follows WHERE status = 'active' AND next_check <= ? ORDER BY next_check",
                           (now(),))
            for row in due:
                await check(row["id"])
        except Exception:
            log.exception("Follow scheduler error")
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), 60)
        except asyncio.TimeoutError:
            pass
