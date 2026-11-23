"""Caching, request merging and the cheap paths added for speed."""

import asyncio
import os
import stat

from fastapi.testclient import TestClient

from app import anicli, anilist, config, db, jobs, library, locations, main

HEADERS = {"X-Requested-With": "anistash"}


def run(coro):
    return asyncio.run(coro)


def counting_ani_cli(tmp_path, monkeypatch):
    """Wraps the fake ani-cli so each run leaves a line in a file."""
    calls = tmp_path / "calls"
    wrapper = tmp_path / "counting-ani-cli"
    wrapper.write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\nsleep 0.3\nexec "{config.ANI_CLI}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(config, "ANI_CLI", str(wrapper))
    return lambda: len(calls.read_text().splitlines()) if calls.exists() else 0


def test_lookups_survive_a_restart(isolated, monkeypatch):
    count = counting_ani_cli(isolated, monkeypatch)
    first = run(anicli.search("show"))
    run(anicli.episodes("show", 2))
    anicli._cache.clear()  # what a restart forgets
    assert run(anicli.search("show")) == first
    assert run(anicli.episodes("show", 2)) == ["1", "2", "3", "12.5"]
    assert count() == 2


def test_identical_lookups_at_the_same_time_run_ani_cli_once(isolated, monkeypatch):
    count = counting_ani_cli(isolated, monkeypatch)

    async def both():
        return await asyncio.gather(anicli.search("show"), anicli.search("show"), anicli.search("Show"))

    a, b, c = run(both())
    assert a == b == c and len(a) == 3
    assert count() == 1


def test_qualities_are_cached(isolated, monkeypatch):
    count = counting_ani_cli(isolated, monkeypatch)
    assert run(anicli.qualities("show", 2, "1", False)) == run(anicli.qualities("show", 2, "1", False))
    assert count() == 1


def test_season_chain_needs_few_calls(isolated, monkeypatch):
    """Five seasons in a row: the lookup, then one call per three steps of the chain."""
    def node(n, depth):
        media = {"id": n, "type": "ANIME", "format": "TV", "title": {"romaji": f"Show {n}", "english": None},
                 "synonyms": [], "seasonYear": 2000 + n}
        if depth and n < 5:
            media["relations"] = {"edges": [{"relationType": "SEQUEL", "node": node(n + 1, depth - 1)}]}
        elif depth:
            media["relations"] = {"edges": []}
        return media

    calls = []

    async def fake_fetch(query, variables, cache_key, ttl):
        calls.append(cache_key)
        if "search" in variables:
            return {"Page": {"media": [node(1, 1)]}}
        return {"Media": node(variables["id"], anilist._RELATIONS_DEPTH)}  # as deep as the real query

    monkeypatch.setattr(anilist, "_fetch", fake_fetch)
    chain = run(anilist.season_chain("Show 1"))
    assert [e["id"] for e in chain] == [1, 2, 3, 4, 5]
    assert len(calls) == 3


def test_queue_poll_answers_unchanged_and_badge_counts(isolated):
    loc = run(locations.create({"name": "Media", "type": "local", "path": str(isolated / "media")}))
    jobs.enqueue("Show", loc["id"], "sub", "best",
                 [{"query": "show", "index": 2, "title": "Show Season 2", "season": 2, "episodes": ["1", "2"]}])
    with TestClient(main.app, headers=HEADERS) as c:
        c.post("/api/setup/admin", json={"username": "admin", "password": "longenough"})
        c.post("/api/setup/complete")
        db.execute("UPDATE items SET status = 'failed'")  # keep the worker from picking them up
        db.execute("UPDATE items SET status = 'queued' WHERE episode = '1'")
        first = c.get("/api/queue").json()
        assert first["counts"] == {"queued": 1, "running": 0, "done": 0} and len(first["batches"]) == 1
        again = c.get("/api/queue", params={"since": first["rev"]}).json()
        assert again == {"rev": first["rev"], "unchanged": True}
        db.execute("UPDATE items SET status = 'failed'")
        changed = c.get("/api/queue", params={"since": first["rev"]}).json()
        assert "batches" in changed and changed["counts"] == {"queued": 0, "running": 0, "done": 0}
        assert c.get("/api/queue/summary").json() == {"queued": 0, "running": 0, "done": 0}


def test_finished_history_is_capped(isolated, monkeypatch):
    loc = run(locations.create({"name": "Media", "type": "local", "path": str(isolated / "media")}))
    monkeypatch.setattr(jobs, "FINISHED_SHOWN", 3)
    for n in range(6):
        jobs.enqueue(f"Show {n}", loc["id"], "sub", "best",
                     [{"query": "show", "index": 2, "title": "T", "season": 1, "episodes": ["1"]}])
    db.execute("UPDATE items SET status = 'done' WHERE batch_id <= 5")
    names = sorted(b["series_name"] for b in jobs.snapshot())
    assert names == ["Show 2", "Show 3", "Show 4", "Show 5"]  # 3 newest finished + the active one


def test_static_files_are_compressed_and_cached(isolated):
    with TestClient(main.app, headers=HEADERS) as c:
        page = c.get("/")
        assert f'/static/app.js?v={config.VERSION}"' in page.text and page.headers["cache-control"] == "no-cache"
        js = c.get(f"/static/app.js?v={config.VERSION}", headers={"Accept-Encoding": "gzip"})
        assert js.headers["content-encoding"] == "gzip"
        assert "immutable" in js.headers["cache-control"]
        assert js.headers["x-content-type-options"] == "nosniff"
        assert c.get("/static/app.js").headers["cache-control"] == "no-cache"  # unversioned: always revalidate
        assert c.get("/static/fonts/onest-latin.woff2").headers["cache-control"] == "public, max-age=604800"
        assert c.get("/api/status").headers["cache-control"] == "no-store"


def test_indexes_exist(isolated):
    names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"items_batch", "items_status", "batches_position", "sessions_user", "follows_due", "cache_expiry"} <= names


def test_expired_cache_rows_are_pruned(isolated):
    db.execute("INSERT INTO cache (key, value, expires_at) VALUES ('old', '1', 1), ('new', '1', 99999999999)")
    assert db.prune_cache() == 1
    assert [r["key"] for r in db.query("SELECT key FROM cache")] == ["new"]


def test_existing_episodes_reads_each_folder_once(isolated, monkeypatch):
    loc = run(locations.create({"name": "Media", "type": "local", "path": str(isolated / "media")}))
    season = isolated / "media" / "Show" / "Season 02"
    season.mkdir(parents=True)
    for n in ("1", "3"):
        (season / f"Show - S02E{int(n):02d}.mp4").write_text("x")
    listed = []
    real = os.listdir
    monkeypatch.setattr(os, "listdir", lambda p: listed.append(p) or real(p))
    found = jobs.existing_episodes(loc["id"], "Show", 2, "Show Season 2", ["1", "2", "3", "4"])
    assert found == {"1", "3"} and len(listed) == 1


def test_finished_download_updates_library_without_rescan(isolated, monkeypatch):
    run(locations.create({"name": "Media", "type": "local", "path": str(isolated / "media")}))
    season = isolated / "media" / "Blue Box (2024)" / "Season 01"
    season.mkdir(parents=True)
    (season / "Blue Box (2024) - S01E01.mp4").write_text("x")
    library.invalidate()
    assert run(library.match([{"id": 1, "titles": ["Blue Box"]}]))["1"]["episodes"] == 1

    def no_rescan():
        raise AssertionError("full rescan")

    monkeypatch.setattr(library, "_scan_disk", no_rescan)
    video = season / "Blue Box (2024) - S01E02.mp4"
    video.write_text("x")
    library.add_episode(str(video), "Media")
    new_show = isolated / "media" / "Frieren (2023)" / "Season 01" / "Frieren (2023) - S01E01.mp4"
    new_show.parent.mkdir(parents=True)
    new_show.write_text("x")
    library.add_episode(str(new_show), "Media")
    matches = run(library.match([{"id": 1, "titles": ["Blue Box"]}, {"id": 2, "titles": ["Frieren"]}]))
    assert matches["1"]["episodes"] == 2 and matches["2"]["seasons"] == {"1": 1}
