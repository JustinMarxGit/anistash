"""Signing in with a Jellyfin account.

Jellyfin users can't use AniStash until an admin enables them (Settings → Users). Once
enabled they sign in with their Jellyfin username and password: AniStash asks Jellyfin to
check it, then ends the Jellyfin session that check created and starts its own. The password
is never stored or logged.

Someone whose Jellyfin password is right but who hasn't been enabled gets an access request
that admins see. A wrong password gets the same answer as a wrong AniStash password, and both
count toward the same lockout, so this can't be used to guess Jellyfin passwords.
"""

import asyncio
import logging
import secrets
import time

from . import auth, config, db, mediaserver, net

log = logging.getLogger("anistash.jellyfin")

NOT_ENABLED = "Your Jellyfin account isn't enabled for AniStash yet. An admin has been told."
DEFAULT_ROLE = "member"


def available() -> bool:
    s = db.get_settings()
    return s["media_server"] == "jellyfin" and mediaserver.configured(s)


def _device_id() -> str:
    device = db.get_setting("jellyfin_device_id")
    if not device:
        device = "anistash-" + secrets.token_hex(8)
        db.set_settings(jellyfin_device_id=device)
    return device


def _client_header(token: str = "") -> str:
    header = f'MediaBrowser Client="AniStash", Device="AniStash", DeviceId="{_device_id()}", Version="{config.VERSION}"'
    return header + (f', Token="{token}"' if token else "")


def _user(data: dict) -> dict:
    policy = data.get("Policy") or {}
    return {"id": data.get("Id", ""), "name": data.get("Name", ""), "disabled": bool(policy.get("IsDisabled")),
            "is_admin": bool(policy.get("IsAdministrator")), "image": data.get("PrimaryImageTag") or ""}


async def authenticate(username: str, password: str) -> dict | None:
    """The Jellyfin user for these credentials, or None when Jellyfin says no."""
    url = db.get_setting("media_url").rstrip("/")
    client = net.client("jellyfin-auth")
    try:
        resp = await client.post(url + "/Users/AuthenticateByName", json={"Username": username, "Pw": password},
                                 headers={"Authorization": _client_header(), "Accept": "application/json"})
    except Exception as exc:
        raise mediaserver.MediaServerError(f"Can't reach Jellyfin to check your password ({exc.__class__.__name__}).")
    if resp.status_code in (400, 401, 403, 404):
        return None
    if resp.status_code >= 300:
        raise mediaserver.MediaServerError(f"Jellyfin answered HTTP {resp.status_code}. Try again in a moment.")
    data = resp.json()
    token = data.get("AccessToken") or ""
    if token:  # don't leave an "AniStash" device behind in Jellyfin for every sign-in
        try:
            await client.post(url + "/Sessions/Logout", headers={"Authorization": _client_header(token)})
        except Exception as exc:
            log.info("Couldn't end the Jellyfin session: %s", exc)
    return _user(data.get("User") or {})


async def login(username: str, password: str, ip: str = "", user_agent: str = "") -> str:
    """Sign-in for everyone: AniStash accounts check their own password, Jellyfin accounts (and
    names AniStash doesn't know, when Jellyfin is set up) are checked with Jellyfin."""
    username = (username or "").strip()[:64]
    row = auth.find_user(username)
    if (row is not None and row["auth_source"] == "local") or not available() or not username or not password:
        return await asyncio.to_thread(auth.login, username, password, ip, user_agent)
    auth.check_lock(username, ip)
    found = await authenticate(username, password)
    if found is None or not found["id"]:
        auth.fail(username, ip, "Jellyfin password")
        raise auth.AuthError(auth.WRONG)
    if row is None:  # maybe renamed in Jellyfin since it was enabled
        row = db.query_one("SELECT * FROM users WHERE external_id = ?", (found["id"],))
    if row is not None and row["external_id"] == found["id"] and not row["disabled"] and not found["disabled"]:
        if row["username"] != found["name"]:  # renamed in Jellyfin (pictures are kept current by sync)
            db.execute("UPDATE users SET username = ? WHERE id = ?", (found["name"], row["id"]))
            row = auth.get_user(row["id"])
        return auth.succeed(row, ip, user_agent)
    if found["disabled"]:
        auth.fail(username, ip, "disabled in Jellyfin")
        raise auth.AuthError(auth.WRONG)
    _request_access(found, ip)
    raise auth.AuthError(NOT_ENABLED)


def _request_access(found: dict, ip: str) -> None:
    db.execute("INSERT INTO jellyfin_requests (external_id, username, requested_at, dismissed) VALUES (?, ?, ?, 0) "
               "ON CONFLICT(external_id) DO UPDATE SET username = excluded.username, "
               "requested_at = excluded.requested_at, dismissed = 0", (found["id"], found["name"], int(time.time())))
    auth.audit(found["name"], "access_requested", "Jellyfin", ip)


# Admin side -------------------------------------------------------------------------------------

async def server_users() -> list[dict]:
    resp = await mediaserver._request("GET", "/Users")
    return [_user(u) for u in resp.json() if u.get("Id")]


def requests() -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT external_id, username, requested_at FROM jellyfin_requests WHERE dismissed = 0 AND external_id NOT IN "
        "(SELECT external_id FROM users WHERE external_id != '' AND disabled = 0) ORDER BY requested_at DESC")]


async def overview() -> dict:
    """Every Jellyfin user with what they can do in AniStash."""
    people = await server_users()
    rows = {r["external_id"]: r for r in db.query("SELECT * FROM users WHERE external_id != ''")}
    local = {r["username"].lower(): r for r in db.query("SELECT * FROM users WHERE auth_source = 'local'")}
    pending = {r["external_id"]: r for r in requests()}
    out = []
    for person in people:
        row = rows.get(person["id"])
        entry = {**person, "requested_at": pending.get(person["id"], {}).get("requested_at")}
        if row is not None:
            entry.update(state="disabled" if row["disabled"] else "enabled", user_id=row["id"], role=row["role"])
        elif person["name"].lower() in local:
            other = local[person["name"].lower()]
            entry.update(state="name_taken", user_id=other["id"], role=other["role"])
        else:
            entry.update(state="none", user_id=None, role=None)
        out.append(entry)
    out.sort(key=lambda e: (e["requested_at"] is None, e["state"] != "enabled", e["name"].lower()))
    return {"users": out, "requests": len(pending)}


async def _find(external_id: str) -> dict:
    person = next((p for p in await server_users() if p["id"] == external_id), None)
    if person is None:
        raise auth.AuthError("That Jellyfin user doesn't exist any more.")
    return person


async def enable(external_id: str, role: str = DEFAULT_ROLE) -> int:
    """Gives a Jellyfin user access (as `role`). Returns their AniStash user id."""
    if role not in auth.ROLES:
        raise auth.AuthError("Role must be admin, member or viewer.")
    person = await _find(external_id)
    if person["disabled"]:
        raise auth.AuthError(f"{person['name']} is disabled in Jellyfin.")
    row = db.query_one("SELECT * FROM users WHERE external_id = ?", (external_id,))
    if row is not None:
        auth.update_user(row["id"], role=role, disabled=False)
        user_id = row["id"]
    else:
        if db.query_one("SELECT 1 FROM users WHERE username = ?", (person["name"],)):
            raise auth.AuthError(f"{person['name']} already has an AniStash account. Link it instead.")
        user_id = db.execute(
            "INSERT INTO users (username, password_hash, role, created_at, last_seen_version, tour_done, auth_source, "
            "external_id, avatar_tag, media_user_id, media_user_name) VALUES (?, '!', ?, ?, ?, 0, 'jellyfin', ?, ?, ?, ?)",
            (person["name"], role, int(time.time()), config.VERSION, external_id, person["image"], external_id, person["name"]))
    db.execute("UPDATE jellyfin_requests SET dismissed = 1 WHERE external_id = ?", (external_id,))
    return user_id


async def link(external_id: str) -> int:
    """Switches the AniStash account with the same name to Jellyfin sign-in. It keeps its role,
    follows, downloads and look; its AniStash password stops working and its sessions end."""
    person = await _find(external_id)
    row = db.query_one("SELECT * FROM users WHERE username = ? AND auth_source = 'local'", (person["name"],))
    if row is None:
        raise auth.AuthError(f"There's no AniStash account called {person['name']} to link.")
    if db.query_one("SELECT 1 FROM users WHERE external_id = ? AND id != ?", (external_id, row["id"])):
        raise auth.AuthError("That Jellyfin user is already linked to another account.")
    with db.tx() as conn:
        conn.execute("UPDATE users SET auth_source = 'jellyfin', external_id = ?, password_hash = '!', "
                     "must_change_password = 0, username = ?, avatar_tag = ?, media_user_id = ?, media_user_name = ? "
                     "WHERE id = ?", (external_id, person["name"], person["image"], external_id, person["name"], row["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
    db.execute("UPDATE jellyfin_requests SET dismissed = 1 WHERE external_id = ?", (external_id,))
    return row["id"]


def disable(external_id: str) -> None:
    row = db.query_one("SELECT id FROM users WHERE external_id = ?", (external_id,))
    if row is None:
        raise auth.AuthError("That Jellyfin user doesn't have access.")
    auth.update_user(row["id"], disabled=True)


def dismiss(external_id: str) -> None:
    db.execute("UPDATE jellyfin_requests SET dismissed = 1 WHERE external_id = ?", (external_id,))


async def sync() -> str:
    """Hourly: Jellyfin users disabled or deleted in Jellyfin lose access here too (except the
    last admin, who would lock everyone out); names and pictures are kept up to date."""
    if not available():
        return "off"
    rows = db.query("SELECT * FROM users WHERE auth_source = 'jellyfin' AND external_id != ''")
    if not rows:
        return "nothing to do"
    try:
        people = {p["id"]: p for p in await server_users()}
    except Exception as exc:
        log.info("Jellyfin user sync skipped: %s", exc)
        return "unreachable"
    for row in rows:
        person = people.get(row["external_id"])
        if person is None or person["disabled"]:
            if row["disabled"]:
                continue
            try:
                auth.update_user(row["id"], disabled=True)
                auth.audit("AniStash", "jellyfin_disabled_sync", row["username"])
            except auth.AuthError:
                auth.audit("AniStash", "jellyfin_disabled_sync", f"{row['username']} kept: the only admin")
            continue
        if person["name"] != row["username"] or person["image"] != row["avatar_tag"]:
            if not db.query_one("SELECT 1 FROM users WHERE username = ? AND id != ?", (person["name"], row["id"])):
                db.execute("UPDATE users SET username = ?, avatar_tag = ? WHERE id = ?",
                           (person["name"], person["image"], row["id"]))
    return "synced"


async def avatar(external_id: str) -> tuple[bytes, str] | None:
    """A Jellyfin user's picture (fetched through AniStash, so the browser never needs Jellyfin)."""
    try:
        resp = await mediaserver._request("GET", f"/Users/{external_id}/Images/Primary",
                                          params={"maxHeight": 160, "quality": 85})
    except mediaserver.MediaServerError:
        return None
    kind = resp.headers.get("content-type", "").split(";")[0]
    if not kind.startswith("image/") or kind == "image/svg+xml":
        return None
    return resp.content, kind
