"""Post-install setup, shared by the web wizard and the `anidl-setup` console tool.

The installer leaves the app unconfigured. Until `setup_complete` is set, the API
only answers setup-related routes. The same functions back both front ends, so
`python -m app.setup <command>` does exactly what the wizard's buttons do.
"""

import argparse
import asyncio
import json
import re
import sys
import time

import httpx

from . import anicli, auth, config, db, mediaserver, locations

STEPS = ["welcome", "admin", "locations", "mediaserver", "defaults", "finish"]
QUALITIES = ["best", "1080", "720", "480", "360", "worst"]
NAMINGS = ["server", "original"]
REFRESH = ["season", "episode", "off"]


class SetupError(Exception):
    pass


def state() -> dict:
    settings = db.get_settings()
    return {
        "setup_complete": bool(settings["setup_complete"]),
        "step": int(settings["setup_step"] or 0),
        "steps": STEPS,
        "has_admin": auth.has_admin(),
        "location_count": len(locations.all_locations()),
        "version": config.VERSION,
    }


def save_step(step: int) -> None:
    if 0 <= step < len(STEPS):
        db.set_settings(setup_step=step)


async def latest_release() -> str:
    """Newest release tag on GitHub, cached for 6 hours (the API allows 60 calls/hour)."""
    now = int(time.time())
    row = db.query_one("SELECT value FROM cache WHERE key = 'github:latest' AND expires_at > ?", (now,))
    if row:
        return json.loads(row["value"])
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.github.com/repos/{config.REPO}/releases/latest",
                                    headers={"Accept": "application/vnd.github+json"})
        tag = resp.json().get("tag_name", "") if resp.status_code == 200 else ""
    except httpx.HTTPError:
        return ""
    db.execute("INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES ('github:latest', ?, ?)",
               (json.dumps(tag), now + 6 * 3600))
    return tag


def _newer(tag: str, current: str) -> bool:
    def parts(v):
        return tuple(int(x) for x in re.findall(r"\d+", v)[:3])
    try:
        return bool(tag) and parts(tag) > parts(current)
    except ValueError:
        return False


async def system_check(test_search: bool = True) -> dict:
    ffmpeg = await anicli.version("ffmpeg", "-version")
    result = {
        "ani_cli": await anicli.version(config.ANI_CLI, "-V"),
        "yt_dlp": await anicli.version("yt-dlp", "--version"),
        "ffmpeg": " ".join(ffmpeg.split()[:3]) if ffmpeg else "",
        "helper": config.helper_available(),
        "version": config.VERSION,
        "search_ok": None,
        "search_error": "",
    }
    if test_search and result["ani_cli"]:
        try:
            result["search_ok"] = len(await anicli.search("one piece")) > 0
            if not result["search_ok"]:
                result["search_error"] = "The test search returned no results."
        except anicli.AniCliError as exc:
            result["search_ok"], result["search_error"] = False, str(exc)
    return result


def save_defaults(data: dict) -> dict:
    values = {}
    if "mode" in data:
        if data["mode"] not in ("sub", "dub"):
            raise SetupError("Mode must be sub or dub.")
        values["mode"] = data["mode"]
    if "quality" in data:
        if str(data["quality"]) not in QUALITIES:
            raise SetupError("Quality must be one of " + ", ".join(QUALITIES))
        values["quality"] = str(data["quality"])
    if "concurrency" in data:
        concurrency = int(data["concurrency"])
        if not 1 <= concurrency <= 5:
            raise SetupError("Concurrent downloads must be 1 to 5.")
        values["concurrency"] = concurrency
    if "naming" in data:
        if data["naming"] not in NAMINGS:
            raise SetupError("Naming must be server or original.")
        values["naming"] = data["naming"]
    if "skip_existing" in data:
        values["skip_existing"] = bool(data["skip_existing"])
    if "media_refresh" in data:
        if data["media_refresh"] not in REFRESH:
            raise SetupError("Scan timing must be season, episode or off.")
        values["media_refresh"] = data["media_refresh"]
    db.set_settings(**values)
    return public_settings()


def save_media_server(kind: str, url: str, token: str | None, library_id: str | None = None,
                      library_name: str = "") -> None:
    if kind not in mediaserver.KINDS:
        raise SetupError("Media server must be none, jellyfin or plex.")
    url = url.strip().rstrip("/")
    if kind != "none" and not url.startswith(("http://", "https://")):
        raise SetupError("The server URL must start with http:// or https://")
    if kind != db.get_setting("media_server"):
        # A library id from the other server type would be meaningless
        db.set_settings(media_library_id="", media_library_name="")
    values = {"media_server": kind, "media_url": url}
    if token is not None:
        values["media_token"] = token.strip()
    if library_id is not None:
        values["media_library_id"] = library_id.strip()
        values["media_library_name"] = library_name.strip()
    if kind == "none":
        values.update(media_url="", media_token="", media_library_id="", media_library_name="")
    db.set_settings(**values)


def public_settings() -> dict:
    """Settings safe to send to the browser (the Jellyfin key is reported as set/unset only)."""
    s = db.get_settings()
    return {
        "mode": s["mode"], "quality": s["quality"], "concurrency": s["concurrency"], "naming": s["naming"],
        "skip_existing": s["skip_existing"], "media_server": s["media_server"], "media_url": s["media_url"],
        "media_token_set": bool(s["media_token"]), "media_refresh": s["media_refresh"],
        "media_library_id": s["media_library_id"], "media_library_name": s["media_library_name"],
    }


def complete() -> None:
    if not auth.has_admin():
        raise SetupError("Create the admin account first.")
    if not locations.all_locations():
        raise SetupError("Add at least one download location first.")
    db.set_settings(setup_complete=True, setup_step=len(STEPS) - 1)


def reset() -> None:
    """Forgets all configuration. Downloaded files and mounts' data are left alone."""
    for loc in locations.all_locations():
        try:
            asyncio.run(locations.remove(loc["id"]))
        except locations.LocationError:
            db.execute("DELETE FROM locations WHERE id = ?", (loc["id"],))
    with db.tx() as conn:
        for table in ("items", "batches", "sessions", "users", "settings"):
            conn.execute(f"DELETE FROM {table}")


# Console front end -----------------------------------------------------------

def _read_secret(prompt: str) -> str:
    if sys.stdin.isatty():
        import getpass
        return getpass.getpass(prompt)
    return sys.stdin.readline().rstrip("\n")


def _print(data) -> None:
    print(json.dumps(data, indent=2) if not isinstance(data, str) else data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.setup", description="anidl setup (used by anidl-setup)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("check")
    p = sub.add_parser("set-admin", help="password is read from stdin")
    p.add_argument("--username", required=True)
    p = sub.add_parser("reset-password", help="password is read from stdin")
    p.add_argument("--username")
    sub.add_parser("add-location", help="JSON on stdin, same fields as the web form")
    sub.add_parser("list-locations")
    for name in ("test-location", "remove-location", "default-location"):
        sub.add_parser(name).add_argument("id", type=int)
    p = sub.add_parser("server-path", help="the folder as the media server sees it")
    p.add_argument("id", type=int)
    p.add_argument("path")
    p = sub.add_parser("set-media-server", help="API key / token is read from stdin")
    p.add_argument("--kind", choices=mediaserver.KINDS, required=True)
    p.add_argument("--url", default="")
    sub.add_parser("test-media-server")
    sub.add_parser("media-libraries")
    p = sub.add_parser("media-library", help="the only library anidl will ask the media server to scan")
    p.add_argument("library", help="library name or id")
    sub.add_parser("set-defaults", help="JSON on stdin")
    sub.add_parser("complete")
    p = sub.add_parser("reset")
    p.add_argument("--yes", action="store_true", required=True)
    args = parser.parse_args(argv)

    db.init()
    try:
        if args.cmd == "status":
            _print(state())
        elif args.cmd == "check":
            _print(asyncio.run(system_check()))
        elif args.cmd == "set-admin":
            auth.set_admin(args.username, _read_secret("Password: "))
            save_step(max(state()["step"], 2))
            _print(f"Admin account '{args.username}' saved.")
        elif args.cmd == "reset-password":
            row = db.query_one("SELECT username FROM users ORDER BY id LIMIT 1")
            username = args.username or (row["username"] if row else None)
            if not username:
                raise SetupError("No admin account yet. Use set-admin.")
            auth.set_admin(username, _read_secret("New password: "))
            _print(f"Password for '{username}' changed. All sessions were signed out.")
        elif args.cmd == "add-location":
            loc = asyncio.run(locations.create(json.load(sys.stdin)))
            _print(f"Added location {loc['id']}: {loc['name']} → {loc['path']}")
        elif args.cmd == "list-locations":
            for loc in locations.all_locations():
                info = locations.status(locations.get(loc["id"]))
                state_text = "ok" if info["mounted"] and info["writable"] else "NOT WRITABLE" if info["mounted"] else "NOT MOUNTED"
                default = " (default)" if loc["is_default"] else ""
                print(f"{loc['id']}\t{loc['name']}{default}\t{loc['type']}\t{loc['path']}\t{state_text}")
        elif args.cmd == "test-location":
            info = asyncio.run(locations.test(args.id))
            free = f"{info['free_bytes'] / 1e9:.1f} GB free" if info["free_bytes"] is not None else ""
            _print(f"Location {args.id} is mounted and writable. {free}")
        elif args.cmd == "remove-location":
            asyncio.run(locations.remove(args.id))
            _print(f"Removed location {args.id}. Files on it were not touched.")
        elif args.cmd == "default-location":
            locations.set_default(args.id)
            _print(f"Location {args.id} is now the default.")
        elif args.cmd == "server-path":
            locations.get(args.id)
            db.execute("UPDATE locations SET server_path = ? WHERE id = ?", (args.path.strip(), args.id))
            _print("Saved.")
        elif args.cmd == "set-media-server":
            token = _read_secret("API key / token: ") if args.kind != "none" else ""
            save_media_server(args.kind, args.url, token)
            _print("Media server settings saved.")
        elif args.cmd == "test-media-server":
            info = asyncio.run(mediaserver.test())
            _print(f"Connected to {info['name']} ({info['kind']} {info['version']}).")
        elif args.cmd == "media-libraries":
            for lib in asyncio.run(mediaserver.libraries()):
                print(f"{lib['id']}\t{lib['name']}\t{lib['type']}\t{', '.join(lib['paths'])}")
        elif args.cmd == "media-library":
            libs = asyncio.run(mediaserver.libraries())
            match = [l for l in libs if args.library in (l["id"], l["name"])]
            if not match:
                raise SetupError("No library with that name or id. See media-libraries.")
            db.set_settings(media_library_id=match[0]["id"], media_library_name=match[0]["name"])
            _print(f"anidl will only scan the '{match[0]['name']}' library.")
        elif args.cmd == "set-defaults":
            _print(save_defaults(json.load(sys.stdin)))
        elif args.cmd == "complete":
            complete()
            _print("Setup complete.")
        elif args.cmd == "reset":
            reset()
            _print("Configuration wiped. Open the web UI or run anidl-setup to set up again.")
    except (SetupError, locations.LocationError, mediaserver.MediaServerError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
