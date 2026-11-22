"""Single admin account: scrypt password hashes and server-side session tokens."""

import hashlib
import hmac
import secrets
import time

from . import db

SESSION_DAYS = 30
COOKIE = "anidl_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    check = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(check.hex(), digest)


def validate(username: str, password: str) -> None:
    if not username.strip() or len(username) > 64:
        raise ValueError("Enter a username (up to 64 characters).")
    if len(password) < 8:
        raise ValueError("Use a password of at least 8 characters.")


def has_admin() -> bool:
    return db.query_one("SELECT 1 FROM users LIMIT 1") is not None


def set_admin(username: str, password: str) -> int:
    """Creates the admin account, or replaces its username and password."""
    validate(username, password)
    with db.tx() as conn:
        row = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if row:
            conn.execute("UPDATE users SET username = ?, password_hash = ? WHERE id = ?",
                         (username.strip(), hash_password(password), row[0]))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row[0],))
            return row[0]
        return conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                            (username.strip(), hash_password(password))).lastrowid


def login(username: str, password: str) -> str | None:
    row = db.query_one("SELECT id, password_hash FROM users WHERE username = ?", (username.strip(),))
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return create_session(row["id"])


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    db.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
               (token, user_id, int(time.time()) + SESSION_DAYS * 86400))
    return token


def session_user(token: str | None):
    if not token:
        return None
    return db.query_one(
        "SELECT users.id, users.username FROM sessions JOIN users ON users.id = sessions.user_id "
        "WHERE token = ? AND expires_at > ?", (token, int(time.time())))


def logout(token: str | None) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
