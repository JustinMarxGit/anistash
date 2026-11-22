import sqlite3

from fastapi.testclient import TestClient

from app import auth, changelog, config, db, main

HEADERS = {"X-Requested-With": "anistash"}


def test_changelog_since():
    versions = lambda entries: [e["version"] for e in entries]  # noqa: E731
    assert versions(changelog.since("0.4.0", "0.6.0")) == ["0.6.0", "0.5.0"]
    assert versions(changelog.since("0.6.0", "0.6.0")) == []
    assert versions(changelog.since("", "0.6.0")) == ["0.6.0"]       # accounts from before the changelog existed
    assert versions(changelog.since("0.4.0", "0.5.0")) == ["0.5.0"]  # nothing newer than what's installed


def test_every_release_has_a_changelog_entry():
    assert changelog.CHANGELOG[0]["version"] == config.VERSION, "add a changelog entry for this version"
    for entry in changelog.CHANGELOG:
        assert entry["title"] and entry["items"]


def _ready_site(isolated):
    db.execute("INSERT INTO locations (name, type, config, path, is_default) VALUES ('M', 'local', '{}', ?, 1)",
               (str(isolated / "media"),))
    db.set_settings(setup_complete=True)


def _login(name, password):
    c = TestClient(main.app, headers=HEADERS)
    assert c.post("/api/login", json={"username": name, "password": password}).status_code == 200
    return c


def test_new_people_get_the_tour_not_the_changelog(isolated):
    _ready_site(isolated)
    auth.create_user("boss", "admin of the house", "admin")
    auth.create_user("newbie", "fresh install pass", "member")
    c = _login("newbie", "fresh install pass")
    user = c.get("/api/status").json()["user"]
    assert user["tour_done"] is False and user["whats_new"] is False
    assert c.post("/api/account/tour", json={"done": True}).status_code == 200
    assert c.get("/api/status").json()["user"]["tour_done"] is True


def test_people_from_before_an_update_get_the_changelog_once(isolated):
    _ready_site(isolated)
    auth.create_user("old", "long time user", "admin")
    db.execute("UPDATE users SET last_seen_version = '0.4.0', tour_done = 1")
    c = _login("old", "long time user")
    assert c.get("/api/status").json()["user"]["whats_new"] is True
    entries = c.get("/api/account/whats-new").json()["entries"]
    assert entries and all(changelog._parts(e["version"]) > (0, 4, 0) for e in entries)
    assert c.post("/api/account/whats-new/seen").status_code == 200
    assert c.get("/api/status").json()["user"]["whats_new"] is False
    assert len(c.get("/api/account/whats-new?all=1").json()["entries"]) == len(changelog.CHANGELOG)


def test_upgrading_database_skips_the_tour_for_existing_users(isolated):
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("CREATE TABLE users_old AS SELECT id, username, password_hash, role, disabled, must_change_password, "
                 "created_at, last_login_at FROM users")
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_old RENAME TO users")
    conn.execute("INSERT INTO users (id, username, password_hash, role, disabled, must_change_password, created_at, "
                 "last_login_at) VALUES (1, 'kai', 'x', 'admin', 0, 0, 0, 0)")
    conn.commit()
    conn.close()
    db.init()
    row = db.query_one("SELECT tour_done, last_seen_version FROM users WHERE username = 'kai'")
    assert row["tour_done"] == 1 and row["last_seen_version"] == ""
