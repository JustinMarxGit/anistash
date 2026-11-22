"""User accounts, password hashing, sessions, login throttling and the audit log.

Security notes (OWASP Password Storage / Session Management / Authentication cheat sheets):
- Passwords are hashed with scrypt (N=2^16, r=8, p=2, 16-byte salt). Hashes made with
  older, weaker parameters are upgraded the next time that user logs in.
- Plain passwords are never stored or logged. Logins always run a hash check, even for
  unknown users, so response time doesn't reveal which usernames exist.
- Session tokens are 256-bit random values. Only their SHA-256 is stored, so a copy of
  the database can't be used to log in. Sessions expire after 7 idle days and 30 days
  at most, and are revoked on logout, password change, role change and account disable.
- Failed logins lock a username after 5 tries and an IP after 20 tries in 15 minutes.
"""

import hashlib
import hmac
import re
import secrets
import time

from . import db

ROLES = ("admin", "member", "viewer")
ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2}
COOKIE = "anistash_session"

IDLE_SECONDS = 7 * 86400
ABSOLUTE_SECONDS = 30 * 86400
COOKIE_MAX_AGE = ABSOLUTE_SECONDS

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**16, 8, 2
SCRYPT_MAXMEM = 256 * 1024 * 1024

LOCK_WINDOW = 15 * 60
USER_LIMIT = 5
IP_LIMIT = 20

USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
COMMON_PASSWORDS = {
    "password", "password1", "password123", "123456789", "1234567890", "qwertyuiop", "iloveyou1",
    "letmein123", "welcome123", "admin12345", "administrator", "changeme123", "anistash123",
}


class AuthError(Exception):
    pass


# Passwords -------------------------------------------------------------------

def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def _parse(stored: str):
    parts = stored.split("$")
    if len(parts) == 6 and parts[0] == "scrypt":
        return int(parts[1]), int(parts[2]), int(parts[3]), bytes.fromhex(parts[4]), parts[5]
    if len(parts) == 3 and parts[0] == "scrypt":  # v0.1/v0.2 format: N=2^14, r=8, p=1
        return 2**14, 8, 1, bytes.fromhex(parts[1]), parts[2]
    raise ValueError("unknown hash format")


def verify_password(password: str, stored: str) -> bool:
    try:
        n, r, p, salt, digest = _parse(stored)
    except ValueError:
        return False
    check = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM,
                           dklen=len(bytes.fromhex(digest)))
    return hmac.compare_digest(check.hex(), digest)


def needs_rehash(stored: str) -> bool:
    try:
        n, r, p, *_ = _parse(stored)
    except ValueError:
        return True
    return (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)


# Checked against when a username doesn't exist, so failures take the same time either way
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME.match(username):
        raise AuthError("Usernames are 3-32 characters: letters, numbers, dots, dashes and underscores.")
    return username


def validate_password(password: str, username: str = "") -> None:
    if not isinstance(password, str) or len(password) < 10:
        raise AuthError("Use a password of at least 10 characters. A few random words works well.")
    if len(password) > 256:
        raise AuthError("Passwords can be at most 256 characters.")
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or len(set(password)) < 4:
        raise AuthError("That password is too easy to guess. Pick something less common.")
    # Short names like "sam" would block ordinary words ("same", "sample"), so only check 4+ characters
    if username and len(username) >= 4 and username.lower() in lowered:
        raise AuthError("The password can't contain the username.")


# Audit log -------------------------------------------------------------------

def audit(actor: str, action: str, detail: str = "", ip: str = "") -> None:
    row_id = db.execute("INSERT INTO audit_log (at, actor, action, detail, ip) VALUES (?, ?, ?, ?, ?)",
                        (int(time.time()), actor or "-", action, detail[:300], ip[:64]))
    # Keep the log bounded (trimming every 100th entry is plenty)
    if row_id % 100 == 0:
        db.execute("DELETE FROM audit_log WHERE id <= ? - 5000", (row_id,))


def audit_entries(limit: int = 200) -> list[dict]:
    return [dict(r) for r in db.query("SELECT at, actor, action, detail, ip FROM audit_log ORDER BY id DESC LIMIT ?",
                                      (limit,))]


# Login throttling --------------------------------------------------------------

def _recent_failures(key: str, now: int) -> list[int]:
    return [r["at"] for r in db.query("SELECT at FROM login_attempts WHERE key = ? AND at > ? ORDER BY at",
                                      (key, now - LOCK_WINDOW))]


def locked_for(username: str, ip: str) -> int:
    """Seconds until another login attempt is allowed (0 = allowed now)."""
    now = int(time.time())
    wait = 0
    for key, limit in ((f"user:{username.lower()}", USER_LIMIT), (f"ip:{ip}", IP_LIMIT)):
        failures = _recent_failures(key, now)
        if len(failures) >= limit:
            wait = max(wait, failures[-limit] + LOCK_WINDOW - now)
    return max(wait, 0)


def _record_failure(username: str, ip: str) -> None:
    now = int(time.time())
    with db.tx() as conn:
        conn.execute("INSERT INTO login_attempts (key, at) VALUES (?, ?)", (f"user:{username.lower()}", now))
        conn.execute("INSERT INTO login_attempts (key, at) VALUES (?, ?)", (f"ip:{ip}", now))
        conn.execute("DELETE FROM login_attempts WHERE at < ?", (now - LOCK_WINDOW,))


# Users ------------------------------------------------------------------------

def public_user(row) -> dict:
    return {"id": row["id"], "username": row["username"], "role": row["role"], "disabled": bool(row["disabled"]),
            "must_change_password": bool(row["must_change_password"]), "created_at": row["created_at"],
            "last_login_at": row["last_login_at"]}


def has_admin() -> bool:
    return db.query_one("SELECT 1 FROM users WHERE role = 'admin' AND disabled = 0 LIMIT 1") is not None


def list_users() -> list[dict]:
    return [public_user(r) for r in db.query("SELECT * FROM users ORDER BY username")]


def get_user(user_id: int):
    row = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not row:
        raise AuthError("That user doesn't exist.")
    return row


def _active_admins(conn, excluding: int | None = None) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
                        (excluding or -1,)).fetchone()[0]


def create_user(username: str, password: str, role: str = "member", must_change: bool = False) -> int:
    username = validate_username(username)
    if role not in ROLES:
        raise AuthError("Role must be admin, member or viewer.")
    validate_password(password, username)
    with db.tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise AuthError(f"The username {username} is taken.")
        # New people get the tour, not a list of changes from before they joined
        from .config import VERSION
        return conn.execute(
            "INSERT INTO users (username, password_hash, role, must_change_password, created_at, last_seen_version, "
            "tour_done) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (username, hash_password(password), role, int(must_change), int(time.time()), VERSION)).lastrowid


def update_user(user_id: int, role: str | None = None, disabled: bool | None = None) -> None:
    get_user(user_id)
    if role is not None and role not in ROLES:
        raise AuthError("Role must be admin, member or viewer.")
    with db.tx() as conn:
        row = conn.execute("SELECT role, disabled FROM users WHERE id = ?", (user_id,)).fetchone()
        new_role = role or row["role"]
        new_disabled = row["disabled"] if disabled is None else int(disabled)
        if (new_role != "admin" or new_disabled) and row["role"] == "admin" and not _active_admins(conn, user_id):
            raise AuthError("There must always be at least one active admin.")
        conn.execute("UPDATE users SET role = ?, disabled = ? WHERE id = ?", (new_role, new_disabled, user_id))
        # A demoted or disabled user keeps no open session with the old rights
        if new_role != row["role"] or new_disabled:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def delete_user(user_id: int) -> None:
    get_user(user_id)
    with db.tx() as conn:
        row = conn.execute("SELECT role, disabled FROM users WHERE id = ?", (user_id,)).fetchone()
        if row["role"] == "admin" and not row["disabled"] and not _active_admins(conn, user_id):
            raise AuthError("You can't delete the last admin.")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def set_password(user_id: int, password: str, must_change: bool = False, keep_session: str | None = None) -> None:
    """Sets a new password and signs the user out everywhere (except keep_session, a token hash)."""
    row = get_user(user_id)
    validate_password(password, row["username"])
    with db.tx() as conn:
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = ? WHERE id = ?",
                     (hash_password(password), int(must_change), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, keep_session or ""))


def set_admin(username: str, password: str) -> int:
    """Creates the first admin, or resets an existing user's password and makes sure they can log in
    as admin (used by setup and `anistash-setup --reset-password`)."""
    row = db.query_one("SELECT id FROM users WHERE username = ?", (username.strip(),))
    if row:
        set_password(row["id"], password)
        with db.tx() as conn:
            conn.execute("UPDATE users SET role = 'admin', disabled = 0 WHERE id = ?", (row["id"],))
        return row["id"]
    return create_user(username, password, role="admin")


# Sessions ---------------------------------------------------------------------

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, ip: str = "", user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with db.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (token_hash(token), user_id, now, now, now + IDLE_SECONDS, ip[:64], user_agent[:200]))
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id))
    return token


def login(username: str, password: str, ip: str = "", user_agent: str = "") -> str:
    """Returns a new session token or raises AuthError with a message safe to show."""
    username = (username or "").strip()[:64]
    wait = locked_for(username, ip)
    if wait:
        audit(username or "-", "login_locked", "", ip)
        raise AuthError(f"Too many failed logins. Try again in {max(1, wait // 60)} minute(s).")
    row = db.query_one("SELECT * FROM users WHERE username = ?", (username,)) if username else None
    ok = verify_password(password or "", row["password_hash"] if row else _DUMMY_HASH)
    if not row or not ok or row["disabled"]:
        _record_failure(username, ip)
        audit(username or "-", "login_failed", "account disabled" if row and ok else "", ip)
        raise AuthError("Wrong username or password.")
    if needs_rehash(row["password_hash"]):
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), row["id"]))
    db.execute("DELETE FROM login_attempts WHERE key = ?", (f"user:{username.lower()}",))
    audit(row["username"], "login", "", ip)
    return create_session(row["id"], ip, user_agent)


def session_user(token: str | None):
    """The logged-in user for a session cookie, or None. Extends idle sessions on use."""
    if not token or len(token) > 100:
        return None
    digest = token_hash(token)
    now = int(time.time())
    row = db.query_one(
        "SELECT users.*, sessions.token_hash, sessions.created_at AS session_created, sessions.last_seen_at "
        "FROM sessions JOIN users ON users.id = sessions.user_id "
        "WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.disabled = 0", (digest, now))
    if row and now - row["last_seen_at"] > 300:
        expires = min(now + IDLE_SECONDS, row["session_created"] + ABSOLUTE_SECONDS)
        db.execute("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?", (now, expires, digest))
    return row


def logout(token: str | None) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


def user_sessions(user_id: int) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT created_at, last_seen_at, ip, user_agent FROM sessions WHERE user_id = ? AND expires_at > ? "
        "ORDER BY last_seen_at DESC", (user_id, int(time.time())))]


def allowed(user, role: str) -> bool:
    return user is not None and ROLE_RANK[user["role"]] >= ROLE_RANK[role]
