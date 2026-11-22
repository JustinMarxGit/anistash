"""Encryption at rest for stored secrets (the Jellyfin/Plex token).

Secrets in the database are Fernet tokens (AES-128-CBC + HMAC-SHA256). The key lives in
its own file outside the data directory: /etc/anistash/secret.key, created by the
installer, owned by root and readable only by the anistash group. A copied database or
backup therefore holds no usable token.

SMB passwords never reach the database: anistash-helper writes them to root-only
(0600) credential files, because the kernel's CIFS mount has to read them in plain form.
"""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import config

PREFIX = "enc:v1:"


def _key_path() -> Path:
    return Path(os.environ.get("ANISTASH_SECRET_KEY", "/etc/anistash/secret.key"))


def _fernet() -> Fernet:
    path = _key_path()
    if not path.exists():
        # Development and tests: keep a key next to the data instead
        path = config.DATA_DIR / "secret.key"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def encrypt(value: str) -> str:
    if not value:
        return ""
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Plain values (from before encryption existed) are returned as they are."""
    if not value or not value.startswith(PREFIX):
        return value or ""
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        raise ValueError("A stored secret can't be decrypted: the key in /etc/anistash/secret.key changed. "
                         "Enter the media server token again under Settings.")
