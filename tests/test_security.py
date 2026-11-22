"""Security behaviour of accounts, sessions and the API."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import auth, config, db, jobs, main, setup

HEADERS = {"X-Requested-With": "anistash"}
ADMIN_PW = "correct horse battery"
MEMBER_PW = "staple paper river"
VIEWER_PW = "window chair lamp"


@pytest.fixture
def site(isolated):
    """A finished setup with an admin, a member and a viewer."""
    auth.create_user("admin", ADMIN_PW, "admin")
    auth.create_user("member", MEMBER_PW, "member")
    auth.create_user("viewer", VIEWER_PW, "viewer")
    db.execute("INSERT INTO locations (name, type, config, path, is_default) VALUES ('M', 'local', '{}', ?, 1)",
               (str(isolated / "media"),))
    db.set_settings(setup_complete=True)
    return isolated


def login(username, password):
    c = TestClient(main.app, headers=HEADERS)
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def raw_db() -> bytes:
    return open(config.DB_PATH, "rb").read() + (open(str(config.DB_PATH) + "-wal", "rb").read()
                                                 if (config.DB_PATH.parent / (config.DB_PATH.name + "-wal")).exists() else b"")


def test_passwords_and_session_tokens_are_never_stored_in_plain_text(site):
    c = login("member", MEMBER_PW)
    token = c.cookies.get(auth.COOKIE)
    data = raw_db()
    assert MEMBER_PW.encode() not in data and ADMIN_PW.encode() not in data
    assert token.encode() not in data
    stored = db.query_one("SELECT password_hash FROM users WHERE username = 'member'")["password_hash"]
    assert stored.startswith("scrypt$65536$8$2$")
    assert db.query_one("SELECT 1 FROM sessions WHERE token_hash = ?", (auth.token_hash(token),))


def test_database_and_key_files_are_private(site):
    from app import vault
    vault.encrypt("x")
    assert (config.DB_PATH.stat().st_mode & 0o777) == 0o600
    assert (config.DATA_DIR.stat().st_mode & 0o777) == 0o700
    assert ((config.DATA_DIR / "secret.key").stat().st_mode & 0o777) == 0o600


def test_media_server_token_is_encrypted_at_rest(site):
    setup.save_media_server("jellyfin", "http://jf:8096", "SUPERSECRETTOKEN")
    assert b"SUPERSECRETTOKEN" not in raw_db()
    from app import vault
    assert vault.decrypt(db.get_setting("media_token")) == "SUPERSECRETTOKEN"


def test_cookie_flags(site):
    c = TestClient(main.app, headers=HEADERS)
    r = c.post("/api/login", json={"username": "admin", "password": ADMIN_PW})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie


def test_cross_site_requests_are_blocked(site):
    c = login("admin", ADMIN_PW)
    body = {"username": "eve", "password": "evil password!", "role": "admin"}
    # No custom header (a plain HTML form or simple cross-site request)
    plain = TestClient(main.app)
    plain.cookies = c.cookies
    assert plain.post("/api/users", json=body).status_code == 403
    # Header present but the request comes from another site
    assert c.post("/api/users", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/users", json=body, headers={"Origin": "http://testserver"}).status_code == 200


def test_security_headers(site):
    r = TestClient(main.app).get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert login("admin", ADMIN_PW).get("/api/status").headers["cache-control"] == "no-store"


def test_failed_logins_lock_the_account_and_give_one_message(site):
    c = TestClient(main.app, headers=HEADERS)
    wrong_user = c.post("/api/login", json={"username": "nobody", "password": "x" * 12}).json()["detail"]
    wrong_pw = c.post("/api/login", json={"username": "member", "password": "x" * 12}).json()["detail"]
    assert wrong_user == wrong_pw == "Wrong username or password."
    for _ in range(4):
        c.post("/api/login", json={"username": "member", "password": "x" * 12})
    r = c.post("/api/login", json={"username": "member", "password": MEMBER_PW})
    assert r.status_code == 401 and "Too many failed logins" in r.json()["detail"]
    actions = [e["action"] for e in auth.audit_entries()]
    assert "login_locked" in actions and actions.count("login_failed") >= 5


def test_roles_are_enforced_by_the_server(site):
    admin, member, viewer = login("admin", ADMIN_PW), login("member", MEMBER_PW), login("viewer", VIEWER_PW)
    queue = {"series_name": "Show", "location_id": 1, "mode": "sub", "quality": "best",
             "entries": [{"query": "show", "index": 1, "title": "Show", "season": 1, "episodes": ["1"]}]}
    assert viewer.get("/api/search", params={"q": "show"}).status_code == 200
    assert viewer.post("/api/queue", json=queue).status_code == 403
    assert member.post("/api/queue", json=queue).status_code == 200
    for c in (member, viewer):
        assert c.get("/api/users").status_code == 403
        assert c.get("/api/audit").status_code == 403
        assert c.put("/api/settings", json={"quality": "720"}).status_code == 403
        assert c.post("/api/locations", json={"type": "local", "path": "/tmp/x"}).status_code == 403
        assert c.get("/api/system").status_code == 403
    assert "config" not in member.get("/api/locations").json()["locations"][0]
    assert "config" in admin.get("/api/locations").json()["locations"][0]


def test_members_only_manage_their_own_downloads(site):
    admin, member = login("admin", ADMIN_PW), login("member", MEMBER_PW)
    admin_id = db.query_one("SELECT id FROM users WHERE username = 'admin'")["id"]
    entry = [{"query": "show", "index": 1, "title": "Show", "season": 1, "episodes": ["1"]}]
    jobs.enqueue("Admins show", 1, "sub", "best", entry, created_by=admin_id)
    batch = jobs.snapshot()[0]
    assert batch["owner"] == "admin"
    assert member.post(f"/api/batches/{batch['id']}/cancel").status_code == 403
    assert member.post(f"/api/items/{batch['items'][0]['id']}/cancel").status_code == 403
    assert member.delete(f"/api/batches/{batch['id']}").status_code == 403
    assert admin.post(f"/api/batches/{batch['id']}/cancel").status_code == 200


def test_password_rules(site):
    for bad in ("short", "aaaaaaaaaaaa", "password123", "membermember1"):
        with pytest.raises(auth.AuthError):
            auth.validate_password(bad, "member")
    auth.validate_password("blue kettle river", "member")


def test_disabling_demoting_and_password_changes_end_sessions(site):
    admin, member = login("admin", ADMIN_PW), login("member", MEMBER_PW)
    member_id = db.query_one("SELECT id FROM users WHERE username = 'member'")["id"]
    assert admin.put(f"/api/users/{member_id}", json={"role": "viewer"}).status_code == 200
    assert member.get("/api/queue").status_code == 401  # session ended with the role change

    member = login("member", MEMBER_PW)
    assert admin.put(f"/api/users/{member_id}", json={"disabled": True}).status_code == 200
    assert member.get("/api/queue").status_code == 401
    r = TestClient(main.app, headers=HEADERS).post("/api/login", json={"username": "member", "password": MEMBER_PW})
    assert r.status_code == 401

    # Changing your own password ends your other sessions but keeps you logged in
    other_device, this_device = login("admin", ADMIN_PW), login("admin", ADMIN_PW)
    r = this_device.post("/api/account/password", json={"current": ADMIN_PW, "new": "a brand new phrase"})
    assert r.status_code == 200
    assert other_device.get("/api/queue").status_code == 401
    assert this_device.get("/api/queue").status_code == 200


def test_admin_reset_forces_password_change(site):
    admin = login("admin", ADMIN_PW)
    member_id = db.query_one("SELECT id FROM users WHERE username = 'member'")["id"]
    assert admin.post(f"/api/users/{member_id}/password", json={"password": "temporary pass 42"}).status_code == 200
    member = login("member", "temporary pass 42")
    r = member.get("/api/queue")
    assert r.status_code == 403 and r.json()["detail"] == "password change required"
    assert member.get("/api/status").json()["user"]["must_change_password"] is True
    assert member.post("/api/account/password", json={"current": "temporary pass 42", "new": "my own new phrase"}).status_code == 200
    assert member.get("/api/queue").status_code == 200


def test_last_admin_is_protected(site):
    admin = login("admin", ADMIN_PW)
    admin_id = db.query_one("SELECT id FROM users WHERE username = 'admin'")["id"]
    assert admin.put(f"/api/users/{admin_id}", json={"role": "member"}).status_code == 400
    assert admin.delete(f"/api/users/{admin_id}").status_code == 400
    with pytest.raises(auth.AuthError):
        auth.update_user(admin_id, role="viewer")
    with pytest.raises(auth.AuthError):
        auth.delete_user(admin_id)


def test_usernames_are_unique_ignoring_case(site):
    with pytest.raises(auth.AuthError, match="taken"):
        auth.create_user("Member", "another good phrase", "member")


def test_old_password_hashes_are_upgraded_on_login(site):
    import hashlib
    import secrets
    salt = secrets.token_bytes(16)
    legacy = f"scrypt${salt.hex()}${hashlib.scrypt(b'legacy password!', salt=salt, n=2**14, r=8, p=1).hex()}"
    db.execute("UPDATE users SET password_hash = ? WHERE username = 'viewer'", (legacy,))
    login("viewer", "legacy password!")
    assert db.query_one("SELECT password_hash FROM users WHERE username = 'viewer'")["password_hash"].startswith("scrypt$65536$")


def test_sessions_from_old_schema_are_dropped(isolated):
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("DROP TABLE sessions")
    conn.execute("CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id INTEGER, expires_at INTEGER)")
    conn.execute("INSERT INTO sessions VALUES ('plaintext-token', 1, 99999999999)")
    conn.commit()
    conn.close()
    db.init()
    columns = {r["name"] for r in db.query("PRAGMA table_info(sessions)")}
    assert "token_hash" in columns and "token" not in columns
