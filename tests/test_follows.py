import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import anicli, anilist, auth, db, follows, jobs, main

HOUR = 3600
ENTRY = {"query": "show", "index": 2, "title": "Show Season 2", "season": 2, "offset": 0}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def setup_done(isolated, monkeypatch):
    db.execute("INSERT INTO locations (name, type, config, path, is_default) VALUES ('M', 'local', '{}', ?, 1)",
               (str(isolated / "media"),))
    db.set_settings(setup_complete=True)

    # No network in tests: a show that airs episode 5 in two days
    async def media_id(title):
        return 42

    async def airing(media_id_):
        return {"status": "RELEASING", "episodes": 12, "next_episode": 5,
                "next_airing_at": follows.now() + 48 * HOUR}

    monkeypatch.setattr(anilist, "media_id", media_id)
    monkeypatch.setattr(anilist, "airing", airing)
    return isolated


def queued_episodes(follow_id):
    return sorted(r["episode"] for r in db.query(
        "SELECT items.episode FROM items JOIN batches ON batches.id = items.batch_id WHERE batches.follow_id = ?",
        (follow_id,)))


def release(monkeypatch, episodes):
    monkeypatch.setenv("FAKE_EPISODES", episodes)
    anicli._cache.clear()


def test_only_new_episodes_are_queued_as_they_come_out(setup_done, monkeypatch):
    follow = run(follows.create(ENTRY, "Show", 1, "sub", "best", None, new_seasons=False, only_new=True))
    assert json.loads(follows.get(follow["id"])["skip_episodes"]) == ["1", "2", "3", "12.5"]
    assert run(follows.check(follow["id"])) == "Up to date"
    assert queued_episodes(follow["id"]) == []

    release(monkeypatch, "1 2 3 4 12.5")
    assert run(follows.check(follow["id"])) == "Queued 1 new episode"
    assert queued_episodes(follow["id"]) == ["4"]
    assert run(follows.check(follow["id"])) == "Up to date"  # no duplicates
    batch = jobs.snapshot()[0]
    assert batch["follow_id"] == follow["id"] and batch["season"] == 2


def test_catch_up_queues_missing_but_skips_files_on_disk(setup_done):
    season = setup_done / "media" / "Show" / "Season 02"
    season.mkdir(parents=True)
    (season / "Show - S02E01.mp4").write_text("x")
    follow = run(follows.create(ENTRY, "Show", 1, "sub", "best", None, new_seasons=False))
    assert run(follows.check(follow["id"])) == "Queued 3 new episodes"
    assert queued_episodes(follow["id"]) == ["12.5", "2", "3"]


def test_failed_episodes_are_retried_but_cancelled_ones_are_not(setup_done, monkeypatch):
    follow = run(follows.create(ENTRY, "Show", 1, "dub", "best", None, new_seasons=False))
    run(follows.check(follow["id"]))
    items = {i["episode"]: i["id"] for b in jobs.snapshot() for i in b["items"]}
    db.execute("UPDATE items SET status = 'failed', updated_at = ? WHERE id = ?", (follows.now() - 2 * HOUR, items["1"]))
    db.execute("UPDATE items SET status = 'cancelled', updated_at = ? WHERE id = ?", (follows.now() - 2 * HOUR, items["2"]))
    assert run(follows.check(follow["id"])) == "Retrying 1 episode"
    statuses = {i["episode"]: i["status"] for b in jobs.snapshot() for i in b["items"]}
    assert statuses["1"] == "queued" and statuses["2"] == "cancelled"
    assert queued_episodes(follow["id"]).count("1") == 1


def test_schedule_follows_the_airing_times():
    t = 1_000_000
    airs = t + 48 * HOUR
    # Up to date with a known next broadcast: look 20 minutes after it, but at least twice a day
    assert follows.next_check_at(t, "RELEASING", airs, 5, 4, t, "sub") == t + 12 * HOUR
    assert follows.next_check_at(t, "RELEASING", t + 2 * HOUR, 5, 4, t, "sub") == t + 2 * HOUR + 20 * 60
    # Episode 4 aired but the site only has 3: hourly until it shows up
    assert follows.next_check_at(t, "RELEASING", airs, 5, 3, t, "sub") == t + HOUR
    # Finished: every 6h while recent, then daily, then weekly
    assert follows.next_check_at(t, "FINISHED", None, None, 12, t - 2 * 86400, "sub") == t + 6 * HOUR
    assert follows.next_check_at(t, "FINISHED", None, None, 12, t - 30 * 86400, "sub") == t + 86400
    assert follows.next_check_at(t, "FINISHED", None, None, 12, t - 90 * 86400, "sub") == t + 7 * 86400


def test_check_saves_airing_info_and_next_check(setup_done):
    follow = run(follows.create(ENTRY, "Show", 1, "sub", "best", None, new_seasons=False, only_new=True))
    run(follows.check(follow["id"]))
    row = follows.to_dict(follows.get(follow["id"]))
    assert row["airing_status"] == "RELEASING" and row["next_episode"] == 5
    assert row["next_check"] == row["last_checked"] + HOUR  # episode 4 aired but isn't on the site yet


def test_new_seasons_are_followed_automatically(setup_done, monkeypatch):
    async def seasons(query, index, title, dub):
        return {"series_name": "Show", "seasons": [
            {"query": "show", "index": 1, "title": "Show", "season": 1, "part": 1, "special": False, "offset": 0},
            {"query": "show", "index": 2, "title": "Show Season 2", "season": 2, "part": 1, "special": False, "offset": 0},
            {"query": "show", "index": 3, "title": "Show Season 3", "season": 3, "part": 1, "special": False, "offset": 0},
        ]}
    monkeypatch.setattr(follows.shows, "seasons", seasons)
    follow = run(follows.create(ENTRY, "Show", 1, "sub", "best", None, new_seasons=True, only_new=True))
    run(follows.check(follow["id"]))
    titles = sorted(f["title"] for f in follows.all_follows())
    assert titles == ["Show Season 2", "Show Season 3"]  # season 1 is older, so not followed
    run(follows.check(follow["id"]))  # the daily season check doesn't run twice in a row
    assert len(follows.all_follows()) == 2


def test_same_show_cant_be_followed_twice(setup_done):
    run(follows.create(ENTRY, "Show", 1, "sub", "best", None))
    with pytest.raises(follows.FollowError, match="already followed"):
        run(follows.create(ENTRY, "Show", 1, "sub", "best", None))
    run(follows.create(ENTRY, "Show", 1, "dub", "best", None))  # dub is a separate follow


def test_follow_permissions(setup_done):
    headers = {"X-Requested-With": "anistash"}
    for name, role in (("admin", "admin"), ("member", "member"), ("other", "member"), ("viewer", "viewer")):
        auth.create_user(name, "long enough phrase", role)

    def login(name):
        c = TestClient(main.app, headers=headers)
        assert c.post("/api/login", json={"username": name, "password": "long enough phrase"}).status_code == 200
        return c

    body = {"entry": ENTRY, "series_name": "Show", "location_id": 1, "mode": "sub", "only_new": True}
    assert login("viewer").post("/api/follows", json=body).status_code == 403
    member = login("member")
    r = member.post("/api/follows", json=body)
    assert r.status_code == 200, r.text
    follow_id = r.json()["id"]
    other = login("other")
    assert other.put(f"/api/follows/{follow_id}", json={"status": "paused"}).status_code == 403
    assert other.delete(f"/api/follows/{follow_id}").status_code == 403
    assert member.put(f"/api/follows/{follow_id}", json={"status": "paused"}).json()["status"] == "paused"
    assert login("admin").delete(f"/api/follows/{follow_id}").status_code == 200
