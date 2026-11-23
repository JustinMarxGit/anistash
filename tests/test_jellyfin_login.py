"""Signing in with Jellyfin accounts: off until an admin enables them, checked by Jellyfin,
never storing the password, and following Jellyfin when someone is disabled there."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import auth, db, jellyfin_auth, locations, main

HEADERS = {"X-Requested-With": "anistash"}
PASSWORDS = {"Kai": "kai's jellyfin pw", "Mio Tanaka": "mio's jellyfin pw", "boss": "boss jellyfin pw"}


def run(coro):
    return asyncio.run(coro)


class FakeJellyfin:
    def __init__(self):
        self.users = {"j-kai": {"Name": "Kai"}, "j-mio": {"Name": "Mio Tanaka"}, "j-boss": {"Name": "boss"}}
        self.disabled = set()
        self.logouts = []
        self.bodies = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, body=b"", kind="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/Users":
                    return self._send(200, json.dumps([
                        {"Id": i, "Name": u["Name"], "PrimaryImageTag": "t1" if i == "j-kai" else None,
                         "Policy": {"IsDisabled": i in fake.disabled, "IsAdministrator": i == "j-boss"}}
                        for i, u in fake.users.items()]).encode())
                if path == "/Users/j-kai/Images/Primary":
                    return self._send(200, b"\x89PNG\r\n\x1a\nfake", "image/png")
                self._send(404)

            def do_POST(self):
                path = urlsplit(self.path).path
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.bodies.append(body)
                if path == "/Users/AuthenticateByName":
                    data = json.loads(body)
                    found = next((i for i, u in fake.users.items() if u["Name"] == data["Username"]), None)
                    if not found or PASSWORDS.get(data["Username"]) != data["Pw"] or found in fake.disabled:
                        return self._send(401)
                    user = {"Id": found, "Name": fake.users[found]["Name"], "Policy": {"IsDisabled": False}}
                    return self._send(200, json.dumps({"User": user, "AccessToken": "tok-" + found}).encode())
                if path == "/Sessions/Logout":
                    fake.logouts.append(self.headers.get("Authorization", ""))
                    return self._send(204)
                self._send(404)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def jellyfin(isolated):
    fake = FakeJellyfin()
    run(locations.create({"name": "Media", "type": "local", "path": str(isolated)}))
    db.set_settings(setup_complete=True, media_server="jellyfin", media_url=f"http://127.0.0.1:{fake.server.server_port}",
                    media_token="KEY", media_library_id="lib")
    auth.create_user("admin", "correct horse battery", "admin")
    yield fake
    fake.server.shutdown()


def client():
    return TestClient(main.app, headers=HEADERS)


def sign_in(name, pw):
    c = client()
    return c, c.post("/api/login", json={"username": name, "password": pw})


def admin():
    c, r = sign_in("admin", "correct horse battery")
    assert r.status_code == 200
    return c


def test_no_access_until_an_admin_enables_it(jellyfin):
    _, r = sign_in("Kai", PASSWORDS["Kai"])
    assert r.status_code == 401 and "isn't enabled" in r.json()["detail"]
    a = admin()
    overview = a.get("/api/jellyfin/users").json()
    assert overview["requests"] == 1
    kai = next(u for u in overview["users"] if u["id"] == "j-kai")
    assert kai["state"] == "none" and kai["requested_at"]
    assert a.post("/api/jellyfin/users/j-kai/enable", json={}).status_code == 200
    c, r = sign_in("Kai", PASSWORDS["Kai"])
    assert r.status_code == 200
    me = c.get("/api/status").json()["user"]
    assert me["username"] == "Kai" and me["role"] == "member" and me["auth_source"] == "jellyfin"
    assert jellyfin.logouts and all('Token="tok-j-kai"' in h for h in jellyfin.logouts)  # no Jellyfin session left behind
    assert a.get("/api/jellyfin/users").json()["requests"] == 0
    row = auth.find_user("Kai")
    assert row["media_user_id"] == "j-kai"  # Picked for you uses their own watch history


def test_wrong_password_looks_like_any_wrong_password_and_counts(jellyfin):
    _, r = sign_in("Kai", "not it")
    assert r.status_code == 401 and r.json()["detail"] == "Wrong username or password."
    _, r = sign_in("nobody", "not it either")
    assert r.json()["detail"] == "Wrong username or password."
    for _ in range(5):
        sign_in("Kai", "still not it")
    _, r = sign_in("Kai", PASSWORDS["Kai"])
    assert "Too many failed logins" in r.json()["detail"]


def test_password_is_never_stored(jellyfin):
    admin().post("/api/jellyfin/users/j-mio/enable", json={"role": "viewer"})
    _, r = sign_in("Mio Tanaka", PASSWORDS["Mio Tanaka"])
    assert r.status_code == 200
    raw = open(db.config.DB_PATH, "rb").read()
    wal = db.config.DB_PATH.with_name(db.config.DB_PATH.name + "-wal")
    raw += wal.read_bytes() if wal.exists() else b""
    assert PASSWORDS["Mio Tanaka"].encode() not in raw
    assert all(PASSWORDS["Mio Tanaka"] not in e["detail"] for e in auth.audit_entries())
    assert auth.find_user("Mio Tanaka")["role"] == "viewer"


def test_disabled_in_jellyfin_loses_access(jellyfin):
    admin().post("/api/jellyfin/users/j-kai/enable", json={})
    c, r = sign_in("Kai", PASSWORDS["Kai"])
    assert r.status_code == 200
    jellyfin.disabled.add("j-kai")
    assert run(jellyfin_auth.sync()) == "synced"
    assert c.get("/api/status").json()["logged_in"] is False  # session ended
    assert auth.find_user("Kai")["disabled"] == 1


def test_renamed_in_jellyfin_still_signs_in(jellyfin):
    admin().post("/api/jellyfin/users/j-kai/enable", json={})
    jellyfin.users["j-kai"]["Name"] = "Kai Sato"
    PASSWORDS["Kai Sato"] = PASSWORDS["Kai"]
    try:
        _, r = sign_in("Kai Sato", PASSWORDS["Kai"])
        assert r.status_code == 200
        assert auth.find_user("Kai Sato")["external_id"] == "j-kai"
    finally:
        del PASSWORDS["Kai Sato"]


def test_linking_a_same_name_account_keeps_its_data(jellyfin):
    user_id = auth.create_user("Kai", "kai's old anistash pw", "member")
    db.execute("UPDATE users SET accent = '#3f9b5a' WHERE id = ?", (user_id,))
    a = admin()
    kai = next(u for u in a.get("/api/jellyfin/users").json()["users"] if u["id"] == "j-kai")
    assert kai["state"] == "name_taken" and kai["user_id"] == user_id
    assert a.post("/api/jellyfin/users/j-kai/enable", json={}).status_code == 400  # must link, not duplicate
    assert a.post("/api/jellyfin/users/j-kai/link", json={}).status_code == 200
    _, r = sign_in("Kai", "kai's old anistash pw")
    assert r.status_code == 401  # the old password no longer works
    c, r = sign_in("Kai", PASSWORDS["Kai"])
    assert r.status_code == 200
    assert c.get("/api/status").json()["user"]["accent"] == "#3f9b5a" and auth.find_user("Kai")["id"] == user_id


def test_only_admins_manage_jellyfin_access(jellyfin):
    admin().post("/api/jellyfin/users/j-kai/enable", json={})
    member, _ = sign_in("Kai", PASSWORDS["Kai"])
    assert member.get("/api/jellyfin/users").status_code == 403
    assert member.post("/api/jellyfin/users/j-mio/enable", json={"role": "admin"}).status_code == 403
    assert member.post("/api/account/password", json={"current": "x", "new": "something long enough"}).status_code == 400


def test_avatars_come_through_anistash(jellyfin):
    a = admin()
    assert a.get("/api/jellyfin/users/j-kai/avatar").content.startswith(b"\x89PNG")
    a.post("/api/jellyfin/users/j-kai/enable", json={})
    kai, _ = sign_in("Kai", PASSWORDS["Kai"])
    assert kai.get("/api/account/avatar").headers["content-type"] == "image/png"


def test_console_reset_makes_it_a_password_account_again(jellyfin):
    admin().post("/api/jellyfin/users/j-kai/enable", json={})
    auth.set_admin("Kai", "a fresh console password")
    _, r = sign_in("Kai", "a fresh console password")
    assert r.status_code == 200 and auth.find_user("Kai")["auth_source"] == "local"
