"""Pictures people upload for their look: a background, a logo and an avatar.

Only PNG, JPEG, WebP and GIF are accepted, recognised by their first bytes rather than by name
or the browser's word for it. SVG is refused because it can carry scripts. Files live in
DATA_DIR/user-files/<user id>/ (or default/ for the site's default look), readable only by the
service, and are served back with their real type and nosniff.
"""

import shutil

from . import config

KINDS = {"background": 8 * 1024 * 1024, "logo": 1024 * 1024, "avatar": 1024 * 1024}
TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}


class FileError(ValueError):
    pass


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def _folder(owner):
    return config.DATA_DIR / "user-files" / str(owner)


def find(owner, kind: str):
    """(path, media type) of a stored file, or None."""
    if kind not in KINDS:
        return None
    for ext, media in TYPES.items():
        path = _folder(owner) / f"{kind}.{ext}"
        if path.exists():
            return path, media
    return None


def save(owner, kind: str, data: bytes) -> str:
    """Stores a picture; returns its version (for cache-busting URLs)."""
    if kind not in KINDS:
        raise FileError("You can upload a background, a logo or an avatar.")
    if not data:
        raise FileError("That file is empty.")
    if len(data) > KINDS[kind]:
        raise FileError(f"That picture is too big (the most for a {kind} is {KINDS[kind] // (1024 * 1024)} MB).")
    ext = sniff(data)
    if ext is None:
        raise FileError("Use a PNG, JPEG, WebP or GIF picture.")
    folder = _folder(owner)
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    remove(owner, kind)
    path = folder / f"{kind}.{ext}"
    tmp = path.with_suffix(".partial")
    tmp.write_bytes(data)
    tmp.chmod(0o600)
    tmp.replace(path)
    return version(owner, kind)


def version(owner, kind: str) -> str:
    found = find(owner, kind)
    return str(int(found[0].stat().st_mtime_ns // 1000)) if found else ""


def remove(owner, kind: str) -> None:
    for ext in TYPES:
        (_folder(owner) / f"{kind}.{ext}").unlink(missing_ok=True)


def remove_all(owner) -> None:
    shutil.rmtree(_folder(owner), ignore_errors=True)


def copy_to_default(owner) -> None:
    """The site default look takes this person's pictures with it."""
    remove_all("default")
    for kind in KINDS:
        found = find(owner, kind)
        if found:
            target = _folder("default")
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(found[0], target / found[0].name)


def urls(owner) -> dict:
    """URLs of the pictures a look uses ('' = none), versioned so browsers can cache them."""
    base = "/api/look/files" if owner == "default" else "/api/account/files"
    out = {}
    for kind in KINDS:
        v = version(owner, kind)
        out[kind] = f"{base}/{kind}?v={v}" if v else ""
    return out

