"""Download queue.

A batch is one show-season queued for download; it has one item per episode. Each
item is its own ani-cli run, so any set of episodes works, each can be retried on
its own, and one failure doesn't stop the rest of the season. Items download to
local disk first and are then moved to the location, so a slow share never breaks
HLS fragment writes and Jellyfin never sees half-written files.
"""

import asyncio
import logging
import os
import shutil
import time

from . import anicli, config, db, locations, mediaserver, postprocess
from .naming import episode_path

log = logging.getLogger("anidl.jobs")

ACTIVE = ("queued", "running")
_running: dict[int, asyncio.subprocess.Process | None] = {}
_tasks: set[asyncio.Task] = set()
_wake = asyncio.Event()


class QueueError(Exception):
    pass


def now() -> int:
    return int(time.time())


def existing_episodes(location_id: int | None, series: str, season: int, title: str, episodes: list[str],
                      offset: int = 0) -> set[str]:
    """Episodes already on disk at the location, under the configured naming."""
    if not location_id:
        return set()
    try:
        row = locations.get(location_id)
    except locations.LocationError:
        return set()
    naming = db.get_setting("naming")
    return {e for e in episodes
            if os.path.exists(str(episode_path(row["path"], series, season, e, title, naming, offset)) + ".mp4")}


def enqueue(series_name: str, location_id: int, mode: str, quality: str, entries: list[dict]) -> dict:
    """Queues seasons. Each entry: {query, index, title, season, offset, episodes: [...]}.

    Episodes that are already queued, or already downloaded when skip_existing is on,
    are left out and counted in the result.
    """
    series_name = series_name.strip()
    if not series_name:
        raise QueueError("Series name is required.")
    locations.get(location_id)
    if mode not in ("sub", "dub"):
        raise QueueError("Mode must be sub or dub.")
    skip_existing = db.get_setting("skip_existing")
    added, skipped_queued, skipped_existing, batch_ids = 0, 0, 0, []
    for entry in entries:
        episodes = [str(e) for e in entry.get("episodes") or []]
        season = int(entry["season"])
        offset = int(entry.get("offset") or 0)
        if season < 0 or season > 99:
            raise QueueError("Season must be between 0 and 99.")
        if offset < 0 or offset > 5000:
            raise QueueError("Episode offset must be between 0 and 5000.")
        queued = {r["episode"] for r in db.query(
            "SELECT items.episode FROM items JOIN batches ON batches.id = items.batch_id "
            "WHERE batches.series_name = ? AND batches.season = ? AND batches.title = ? AND items.status IN ('queued', 'running')",
            (series_name, season, entry["title"]))}
        on_disk = (existing_episodes(location_id, series_name, season, entry["title"], episodes, offset)
                   if skip_existing else set())
        todo = [e for e in episodes if e not in queued and e not in on_disk]
        skipped_queued += len([e for e in episodes if e in queued])
        skipped_existing += len([e for e in episodes if e in on_disk and e not in queued])
        if not todo:
            continue
        with db.tx() as conn:
            position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM batches").fetchone()[0]
            batch_id = conn.execute(
                "INSERT INTO batches (series_name, season, episode_offset, query, result_index, title, mode, quality, "
                "location_id, position, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (series_name, season, offset, entry["query"], int(entry["index"]), entry["title"], mode, quality or "best",
                 location_id, position, now()),
            ).lastrowid
            for i, episode in enumerate(todo):
                conn.execute(
                    "INSERT INTO items (batch_id, episode, position, updated_at) VALUES (?, ?, ?, ?)",
                    (batch_id, episode, i, now()),
                )
        added += len(todo)
        batch_ids.append(batch_id)
    _wake.set()
    return {"added": added, "skipped_queued": skipped_queued, "skipped_existing": skipped_existing, "batches": batch_ids}


def snapshot() -> list[dict]:
    batches = db.query("SELECT batches.*, locations.name AS location_name FROM batches "
                       "LEFT JOIN locations ON locations.id = batches.location_id ORDER BY position")
    items = db.query("SELECT id, batch_id, episode, status, progress, detail, error, dest, quality "
                     "FROM items ORDER BY position, id")
    by_batch: dict[int, list] = {}
    for item in items:
        by_batch.setdefault(item["batch_id"], []).append(dict(item))
    result = []
    for b in batches:
        its = by_batch.get(b["id"], [])
        counts = {s: sum(1 for i in its if i["status"] == s) for s in ("queued", "running", "done", "failed", "cancelled", "skipped")}
        result.append({
            "id": b["id"], "series_name": b["series_name"], "season": b["season"], "offset": b["episode_offset"],
            "title": b["title"],
            "mode": b["mode"], "quality": b["quality"], "location": b["location_name"], "warning": b["warning"],
            "created_at": b["created_at"], "items": its, "counts": counts,
            "active": counts["queued"] + counts["running"] > 0,
        })
    return result


def _set_item(item_id: int, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE items SET {cols} WHERE id = ?", (*fields.values(), item_id))


def cancel_item(item_id: int) -> None:
    proc = _running.get(item_id)
    db.execute("UPDATE items SET status = 'cancelled', detail = '' WHERE id = ? AND status IN ('queued', 'running')", (item_id,))
    if proc and proc.returncode is None:
        anicli._kill(proc)


def cancel_batch(batch_id: int) -> None:
    for row in db.query("SELECT id FROM items WHERE batch_id = ? AND status IN ('queued', 'running')", (batch_id,)):
        cancel_item(row["id"])


def retry_item(item_id: int) -> None:
    db.execute("UPDATE items SET status = 'queued', progress = 0, error = '', detail = '', updated_at = ? "
               "WHERE id = ? AND status IN ('failed', 'cancelled')", (now(), item_id))
    db.execute("UPDATE batches SET notified = 0, warning = '' WHERE id = (SELECT batch_id FROM items WHERE id = ?)", (item_id,))
    _wake.set()


def retry_batch(batch_id: int) -> None:
    for row in db.query("SELECT id FROM items WHERE batch_id = ? AND status IN ('failed', 'cancelled')", (batch_id,)):
        retry_item(row["id"])


def move(table: str, row_id: int, direction: str) -> None:
    """Swaps a batch (or an item within its batch) with its neighbour."""
    if table not in ("batches", "items") or direction not in ("up", "down"):
        raise QueueError("Bad move.")
    row = db.query_one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
    if not row:
        raise QueueError("Not found.")
    scope, params = ("", ()) if table == "batches" else ("AND batch_id = ?", (row["batch_id"],))
    cmp, order = ("<", "DESC") if direction == "up" else (">", "ASC")
    other = db.query_one(
        f"SELECT id, position FROM {table} WHERE (position {cmp} ? OR (position = ? AND id {cmp} ?)) {scope} "
        f"ORDER BY position {order}, id {order} LIMIT 1",
        (row["position"], row["position"], row_id, *params))
    if not other:
        return
    with db.tx() as conn:
        a, b = row["position"], other["position"]
        if a == b:
            b = a + (-0.5 if direction == "up" else 0.5)
        conn.execute(f"UPDATE {table} SET position = ? WHERE id = ?", (b, row_id))
        conn.execute(f"UPDATE {table} SET position = ? WHERE id = ?", (a, other["id"]))


def clear_finished() -> int:
    with db.tx() as conn:
        done = [r[0] for r in conn.execute(
            "SELECT id FROM batches WHERE NOT EXISTS (SELECT 1 FROM items WHERE items.batch_id = batches.id "
            "AND items.status IN ('queued', 'running'))")]
        conn.executemany("DELETE FROM items WHERE batch_id = ?", [(i,) for i in done])
        conn.executemany("DELETE FROM batches WHERE id = ?", [(i,) for i in done])
    return len(done)


def remove_batch(batch_id: int) -> None:
    cancel_batch(batch_id)
    with db.tx() as conn:
        conn.execute("DELETE FROM items WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))


def _next_item():
    return db.query_one(
        "SELECT items.*, batches.query, batches.result_index, batches.title, batches.mode, batches.quality, "
        "batches.series_name, batches.season, batches.episode_offset, batches.location_id FROM items JOIN batches ON batches.id = items.batch_id "
        "WHERE items.status = 'queued' ORDER BY batches.position, batches.id, items.position, items.id LIMIT 1")


def _move_into_place(outputs: list[tuple[str, str]], target_base: str) -> str:
    """Moves finished files to target_base + suffix. Returns the video's final path."""
    os.makedirs(os.path.dirname(target_base), exist_ok=True)
    video = ""
    for src, suffix in outputs:
        dest = target_base + suffix
        partial = dest + ".partial"
        shutil.copyfile(src, partial)
        os.replace(partial, dest)  # the media server never sees a half-copied file
        os.remove(src)
        if suffix == ".mp4":
            video = dest
    return video


async def _download_best(item, index: int, dub: bool, workdir, on_progress,
                         current: dict) -> tuple[list[tuple[str, str]], str]:
    """Downloads the highest quality that works, stepping down one quality at a time.

    A quality counts as failed when ani-cli/yt-dlp fails or the file doesn't pass
    postprocess checks. Returns the finished files and the quality used.
    """
    item_id = item["id"]
    try:
        offered = await anicli.qualities(item["query"], index, item["episode"], dub)
    except anicli.AniCliError:
        offered = []  # ani-cli picks the best on its own
    order = anicli.quality_order(offered, item["quality"])
    errors = []
    for attempt, quality in enumerate(order):
        if _cancelled(item_id):
            raise asyncio.CancelledError()
        label = f"{quality}p" if quality.isdigit() else quality
        current["label"] = label
        _set_item(item_id, progress=0, detail=f"Downloading {label}…" if not attempt
                  else f"{errors[-1]} Trying {label}…")
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True)
        try:
            files = await anicli.download(
                item["query"], index, item["episode"], dub, quality, str(workdir), on_progress,
                on_start=lambda proc: _running.__setitem__(item_id, proc),
            )
            if _cancelled(item_id):
                raise asyncio.CancelledError()
            _set_item(item_id, detail=f"Checking and remuxing {label}…", progress=100)
            outputs = await asyncio.to_thread(
                postprocess.finalize, files, str(workdir), f"{item['series_name']} S{item['season']:02d}E{item['episode']}")
            return outputs, label
        except (anicli.AniCliError, postprocess.BrokenDownload) as exc:
            if _cancelled(item_id):
                raise asyncio.CancelledError()
            errors.append(f"{label} failed ({exc}).")
            log.info("Episode %s of %s: %s", item["episode"], item["title"], errors[-1])
    raise anicli.AniCliError(" ".join(errors) or "No quality could be downloaded.")


def _cancelled(item_id: int) -> bool:
    row = db.query_one("SELECT status FROM items WHERE id = ?", (item_id,))
    return row is None or row["status"] == "cancelled"


async def _run_item(item) -> None:
    item_id = item["id"]
    workdir = config.TMP_DIR / f"item-{item_id}"
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    _set_item(item_id, status="running", progress=0, detail="Finding stream…", error="")
    last_write = 0.0
    log_lines: list[str] = []
    current = {"label": ""}  # the quality being downloaded, shown before yt-dlp's progress

    def on_progress(percent, detail, line):
        nonlocal last_write
        log_lines.append(line)
        del log_lines[:-30]
        if time.monotonic() - last_write < 1 and percent != 100:
            return
        last_write = time.monotonic()
        fields = {"log": "\n".join(log_lines)}
        if percent is not None:
            fields["progress"] = percent
        if detail:
            fields["detail"] = f"{current['label']} · {detail}" if current["label"] else detail
        _set_item(item_id, **fields)

    try:
        location = locations.get(item["location_id"])
        if location["type"] != "local" and not os.path.ismount(location["path"]):
            raise locations.LocationError(f"{location['name']} isn't mounted. Test it under Settings → Locations.")
        dub = item["mode"] == "dub"
        index = await anicli.resolve_index(item["query"], item["result_index"], item["title"], dub)
        outputs, quality_used = await _download_best(item, index, dub, workdir, on_progress, current)
        _set_item(item_id, detail="Moving to " + location["name"] + "…", progress=100)
        naming = db.get_setting("naming")
        target = episode_path(location["path"], item["series_name"], item["season"], item["episode"], item["title"],
                              naming, item["episode_offset"])
        video = await asyncio.to_thread(_move_into_place, outputs, str(target))
        _set_item(item_id, status="done", detail="", dest=video, quality=quality_used, log="\n".join(log_lines))
        _after_done(item, video, location)
    except asyncio.CancelledError:
        _set_item(item_id, status="cancelled", detail="")
    except (anicli.AniCliError, locations.LocationError, OSError) as exc:
        if _cancelled(item_id):
            _set_item(item_id, status="cancelled", detail="")
            return
        log.warning("Episode %s of %s failed: %s", item["episode"], item["title"], exc)
        _set_item(item_id, status="failed", detail="", error=str(exc), log="\n".join(log_lines))
    except Exception as exc:  # keep the worker alive whatever happens
        log.exception("Unexpected error downloading item %s", item_id)
        _set_item(item_id, status="failed", detail="", error=f"Unexpected error: {exc}")
    finally:
        _running.pop(item_id, None)
        shutil.rmtree(workdir, ignore_errors=True)
        _wake.set()


def _after_done(item, video: str | None, location) -> None:
    refresh = db.get_setting("media_refresh")
    if refresh == "off" or not video:
        return
    if refresh == "episode":
        mediaserver.notify([os.path.dirname(video)], location, item["batch_id"])
        return
    remaining = db.query_one("SELECT COUNT(*) AS n FROM items WHERE batch_id = ? AND status IN ('queued', 'running')",
                             (item["batch_id"],))["n"]
    if remaining == 0:
        dests = [r["dest"] for r in db.query("SELECT dest FROM items WHERE batch_id = ? AND status = 'done' AND dest != ''",
                                             (item["batch_id"],))]
        folders = sorted({os.path.dirname(d) for d in dests})
        mediaserver.notify(folders, location, item["batch_id"])


async def worker() -> None:
    """Starts queued items in order, up to the configured concurrency."""
    db.execute("UPDATE items SET status = 'queued', detail = '' WHERE status = 'running'")
    while True:
        try:
            concurrency = max(1, min(int(db.get_setting("concurrency") or 1), 5))
            while len(_running) < concurrency and db.get_setting("setup_complete"):
                item = _next_item()
                if not item:
                    break
                _running[item["id"]] = None
                db.execute("UPDATE items SET status = 'running' WHERE id = ?", (item["id"],))
                task = asyncio.create_task(_run_item(item))
                _tasks.add(task)
                task.add_done_callback(_tasks.discard)
        except Exception:
            log.exception("Queue worker error")
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), 5)
        except asyncio.TimeoutError:
            pass
