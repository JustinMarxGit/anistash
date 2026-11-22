import asyncio

import pytest
from fastapi.testclient import TestClient

from app import auth, config, db, main, updates

HEADERS = {"X-Requested-With": "anistash"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def newer_release(isolated, monkeypatch):
    started = []

    async def latest():
        return "v9.9.9"

    async def start(actor, ip=""):
        started.append(actor)

    monkeypatch.setattr(updates.setup, "latest_release", latest)
    monkeypatch.setattr(updates, "start_update", start)
    monkeypatch.setattr(config, "helper_available", lambda: True)
    return started


def test_auto_update_off_does_nothing(newer_release):
    assert run(updates.maybe_update()) == "off"
    assert newer_release == []


def test_auto_update_installs_a_newer_release_once(newer_release):
    db.set_settings(auto_update=True)
    assert run(updates.maybe_update()) == "started"
    assert newer_release == ["AniStash (automatic)"]
    # If that version didn't come up (rolled back), don't retry it in a loop
    assert run(updates.maybe_update()) == "already tried"
    assert len(newer_release) == 1


def test_auto_update_waits_for_downloads(newer_release, isolated):
    db.set_settings(auto_update=True)
    db.execute("INSERT INTO locations (name, type, config, path) VALUES ('M', 'local', '{}', ?)", (str(isolated),))
    db.execute("INSERT INTO batches (series_name, season, query, result_index, title, mode, quality, location_id, "
               "position, created_at) VALUES ('S', 1, 'q', 1, 'S', 'sub', 'best', 1, 1, 0)")
    db.execute("INSERT INTO items (batch_id, episode, status, position, updated_at) VALUES (1, '1', 'running', 0, 0)")
    assert run(updates.maybe_update()) == "waiting for downloads"
    assert newer_release == []


def test_up_to_date(isolated, monkeypatch):
    async def latest():
        return f"v{config.VERSION}"
    monkeypatch.setattr(updates.setup, "latest_release", latest)
    monkeypatch.setattr(config, "helper_available", lambda: True)
    db.set_settings(auto_update=True)
    assert run(updates.maybe_update()) == "up to date"


def test_only_admins_see_or_run_updates(newer_release, isolated):
    db.execute("INSERT INTO locations (name, type, config, path, is_default) VALUES ('M', 'local', '{}', ?, 1)",
               (str(isolated),))
    db.set_settings(setup_complete=True)
    auth.create_user("admin", "boss of the house", "admin")
    auth.create_user("member", "just visiting here", "member")

    def login(name, pw):
        c = TestClient(main.app, headers=HEADERS)
        assert c.post("/api/login", json={"username": name, "password": pw}).status_code == 200
        return c

    member = login("member", "just visiting here")
    assert member.get("/api/system/update-status").status_code == 403
    assert member.post("/api/system/update-app").status_code == 403
    assert member.put("/api/settings", json={"auto_update": True}).status_code == 403
    admin = login("admin", "boss of the house")
    info = admin.get("/api/system/update-status").json()
    assert info["update_available"] is True and info["latest"] == "v9.9.9" and info["auto_update"] is False
    assert admin.put("/api/settings", json={"auto_update": True}).json()["auto_update"] is True
    assert admin.post("/api/system/update-app").status_code == 200
    assert newer_release == ["admin"]
