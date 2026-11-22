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
    id                   INTEGER PRIMARY KEY,
    username             TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash        TEXT NOT NULL,          -- scrypt; plain passwords are never stored
    role                 TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('admin', 'member', 'viewer')),
    disabled             INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at           INTEGER NOT NULL DEFAULT 0,
    last_login_at        INTEGER NOT NULL DEFAULT 0
);

-- Only a SHA-256 of each session token is stored, so a copy of the database can't log anyone in
CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    ip           TEXT NOT NULL DEFAULT '',
    user_agent   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS login_attempts (
    key        TEXT NOT NULL,      -- 'user:<name>' or 'ip:<address>'
    at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS login_attempts_key ON login_attempts (key, at);

CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY,
    at       INTEGER NOT NULL,
    actor    TEXT NOT NULL,        -- username, or '-' when nobody is logged in
    action   TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT '',
    ip       TEXT NOT NULL DEFAULT ''
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
    created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    follow_id      INTEGER REFERENCES follows(id) ON DELETE SET NULL,
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

-- Shows kept up to date automatically: new episodes are queued as they come out
CREATE TABLE IF NOT EXISTS follows (
    id                  INTEGER PRIMARY KEY,
    series_name         TEXT NOT NULL,
    season              INTEGER NOT NULL,
    episode_offset      INTEGER NOT NULL DEFAULT 0,
    query               TEXT NOT NULL,
    result_index        INTEGER NOT NULL,
    title               TEXT NOT NULL,
    mode                TEXT NOT NULL,
    quality             TEXT NOT NULL,
    location_id         INTEGER NOT NULL REFERENCES locations(id),
    created_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
    new_seasons         INTEGER NOT NULL DEFAULT 1,   -- also follow the next season when it appears
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    skip_episodes       TEXT NOT NULL DEFAULT '[]',   -- episodes that existed when "only new" was chosen
    anilist_id          INTEGER,
    airing_status       TEXT NOT NULL DEFAULT '',
    next_episode        INTEGER,
    next_airing_at      INTEGER,
    last_checked        INTEGER NOT NULL DEFAULT 0,
    next_check          INTEGER NOT NULL DEFAULT 0,
    last_new_at         INTEGER NOT NULL DEFAULT 0,
    last_seasons_check  INTEGER NOT NULL DEFAULT 0,
    last_result         TEXT NOT NULL DEFAULT '',
    created_at          INTEGER NOT NULL,
    UNIQUE (series_name, title, mode)
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
    "both_default": "sub",   # with sub+dub files: which audio plays first
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


# Columns added after the first release: (table, column, definition)
MIGRATIONS = [
    ("users", "role", "TEXT NOT NULL DEFAULT 'admin'"),
    ("users", "disabled", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "must_change_password", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "created_at", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "last_login_at", "INTEGER NOT NULL DEFAULT 0"),
    ("batches", "created_by", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    ("batches", "follow_id", "INTEGER REFERENCES follows(id) ON DELETE SET NULL"),
]


def init() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    config.DATA_DIR.chmod(0o700)
    conn = connect()
    try:
        # Sessions from before token hashing stored raw tokens: drop them (everyone logs in again)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
        if columns and "token_hash" not in columns:
            conn.execute("DROP TABLE sessions")
        conn.executescript(SCHEMA)
        for table, column, definition in MIGRATIONS:
            if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    finally:
        conn.close()
    for path in (config.DB_PATH, config.DB_PATH.with_name(config.DB_PATH.name + "-wal"),
                 config.DB_PATH.with_name(config.DB_PATH.name + "-shm")):
        if path.exists():
            path.chmod(0o600)  # password hashes and settings: readable by the service only


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
