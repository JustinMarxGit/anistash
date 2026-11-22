"""SQLite storage: schema, connections and the key/value settings table."""

import json
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL CHECK (type IN ('smb', 'nfs', 'local')),
    config        TEXT NOT NULL DEFAULT '{}',   -- type-specific fields, never the SMB password
    path          TEXT NOT NULL,                -- where files go inside the container
    server_path TEXT NOT NULL DEFAULT '',     -- the same folder as the media server sees it
    is_default    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS batches (
    id             INTEGER PRIMARY KEY,
    series_name    TEXT NOT NULL,
    season         INTEGER NOT NULL,
    episode_offset INTEGER NOT NULL DEFAULT 0,  -- added to episode numbers (later parts of a season)
    query          TEXT NOT NULL,
    result_index   INTEGER NOT NULL,
    title          TEXT NOT NULL,
    mode           TEXT NOT NULL,
    quality        TEXT NOT NULL,
    location_id    INTEGER NOT NULL REFERENCES locations(id),
    position       REAL NOT NULL,
    warning        TEXT NOT NULL DEFAULT '',
    notified       INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY,
    batch_id   INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    episode    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'queued',  -- queued, running, done, failed, cancelled, skipped
    progress   REAL NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT '',
    error      TEXT NOT NULL DEFAULT '',
    log        TEXT NOT NULL DEFAULT '',
    dest       TEXT NOT NULL DEFAULT '',
    quality    TEXT NOT NULL DEFAULT '',          -- the quality actually downloaded
    position   REAL NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "setup_complete": False,
    "setup_step": 0,
    "mode": "sub",
    "quality": "best",
    "concurrency": 1,
    "naming": "server",
    "skip_existing": True,
    "media_server": "none",
    "media_url": "",
    "media_token": "",
    "media_refresh": "season",
    "media_library_id": "",
    "media_library_name": "",
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx():
    """A connection wrapped in a transaction that commits on success."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def query(sql: str, params=()) -> list[sqlite3.Row]:
    conn = connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def query_one(sql: str, params=()) -> sqlite3.Row | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=()) -> int:
    """Runs one statement and returns lastrowid."""
    conn = connect()
    try:
        return conn.execute(sql, params).lastrowid
    finally:
        conn.close()


def get_setting(key: str):
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else DEFAULT_SETTINGS.get(key)


def get_settings() -> dict:
    values = dict(DEFAULT_SETTINGS)
    for row in query("SELECT key, value FROM settings"):
        values[row["key"]] = json.loads(row["value"])
    return values


def set_settings(**values) -> None:
    with tx() as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
