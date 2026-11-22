"""AniStash web app: JSON API plus the single-page frontend in app/static.

Access control: every API route declares the lowest role allowed to use it
(viewer < member < admin) through the `need()` dependency. The middleware loads the
session, enforces setup and password-change gates, blocks cross-site requests and adds
security headers to every response.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware

from . import (anicli, anilist, auth, changelog, config, db, follows, jobs, library, locations, mediaserver, net,
               recommend, setup, shows, updates)
from .naming import base_title, number_seasons

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("anistash")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    tasks = [asyncio.create_task(job()) for job in (jobs.worker, follows.scheduler, updates.scheduler, housekeeping,
                                                       recommend.keep_fresh)]
    yield
    for task in tasks:
        task.cancel()
    await mediaserver.flush()
    await net.close()


async def housekeeping() -> None:
    """Hourly: forget expired cached answers."""
    while True:
        try:
            db.prune_cache()
            anicli.prune_cache()
        except Exception:
            log.exception("Housekeeping failed")
        await asyncio.sleep(3600)


app = FastAPI(title="AniStash", version=config.VERSION, lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url=None)

# Reachable before setup is finished (by an admin)
SETUP_ROUTES = ("/api/setup", "/api/locations", "/api/mediaserver", "/api/settings", "/api/system", "/api/account")
PUBLIC_ROUTES = ("/api/status", "/api/login", "/api/logout")
# Still reachable while the user must change their password
PASSWORD_CHANGE_ROUTES = ("/api/status", "/api/logout", "/api/account/password")
USER_ERRORS = (anicli.AniCliError, locations.LocationError, mediaserver.MediaServerError, jobs.QueueError,
               setup.SetupError, auth.AuthError, follows.FollowError, ValueError)
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: https://s4.anilist.co https://img.anili.st; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _same_origin(request: Request) -> bool:
    """Unsafe requests must come from this site. Browsers always send Origin (or at least
    Referer) on cross-site POSTs, and can't add our custom header without a CORS preflight,
    which this server never approves."""
    if request.headers.get("x-requested-with") != "anistash":
        return False
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return urlsplit(source).netloc == host


class Gate:
    """Loads the session and enforces the setup, login, password-change and same-origin rules
    for the API, and adds security and caching headers to every response.

    A plain ASGI middleware: Starlette's @app.middleware("http") wraps every response in
    extra tasks and streams, which cost more than the checks themselves.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        path = scope["path"]
        api = path.startswith("/api/")
        request.state.user = auth.session_user(request.cookies.get(auth.COOKIE)) if api else None
        extra = dict(SECURITY_HEADERS)
        # Open pages compare this with the version they were loaded with, and reload after an update
        extra["X-AniStash-Version"] = config.VERSION
        if api:
            extra["Cache-Control"] = "no-store"
        elif path.startswith("/static/"):
            extra["Cache-Control"] = _static_cache(path, scope.get("query_string", b""))

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in extra.items():
                    if name == "Cache-Control" and api:
                        headers[name] = value
                    elif name not in headers:
                        headers[name] = value
            await send(message)

        blocked = _gate(request) if api else None
        if blocked is not None:
            return await blocked(scope, receive, send_with_headers)
        await self.app(scope, receive, send_with_headers)


def _static_cache(path: str, query: bytes) -> str:
    # index.html links app.js/app.css with ?v=<version>, so those can be kept until the next update
    if query.startswith(b"v="):
        return "public, max-age=31536000, immutable"
    if path.startswith(("/static/fonts/", "/static/icons/")):
        return "public, max-age=604800"
    return "no-cache"


def _gate(request: Request) -> JSONResponse | None:
    """The response that blocks this API request, or None to let it through."""
    path = request.url.path
    if request.method in UNSAFE_METHODS and not _same_origin(request):
        return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
    if path.startswith(PUBLIC_ROUTES):
        return None
    user = request.state.user
    if not auth.has_admin():
        # Fresh install: only the first wizard steps work, without a login
        if path in ("/api/setup/system", "/api/setup/admin", "/api/setup/step"):
            return None
        return JSONResponse({"detail": "setup required"}, status_code=409)
    if user is None:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    if user["must_change_password"] and not path.startswith(PASSWORD_CHANGE_ROUTES):
        return JSONResponse({"detail": "password change required"}, status_code=403)
    if not db.get_setting("setup_complete") and not path.startswith(SETUP_ROUTES):
        return JSONResponse({"detail": "setup required"}, status_code=409)
    return None


app.add_middleware(Gate)
app.add_middleware(GZipMiddleware, minimum_size=1024)


def need(role: str):
    def dependency(request: Request):
        user = request.state.user
        if user is None:
            raise HTTPException(401, "Not logged in")
        if not auth.allowed(user, role):
            raise HTTPException(403, f"Your account can't do that ({role} only).")
        return user
    return dependency


VIEWER, MEMBER, ADMIN = Depends(need("viewer")), Depends(need("member")), Depends(need("admin"))


async def user_error(request: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=400)


for _error in USER_ERRORS:
    app.add_exception_handler(_error, user_error)


def _session_response(data: dict, token: str, request: Request) -> JSONResponse:
    resp = JSONResponse(data)
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(auth.COOKIE, token, max_age=auth.COOKIE_MAX_AGE, httponly=True, samesite="strict",
                    secure=https, path="/")
    return resp


def _me(user) -> dict | None:
    if not user:
        return None
    return {"id": user["id"], "username": user["username"], "role": user["role"],
            "must_change_password": bool(user["must_change_password"]), "tour_done": bool(user["tour_done"]),
            "title_lang": user["title_lang"],
            "whats_new": bool(changelog.since(user["last_seen_version"], config.VERSION))}


# Session and setup -------------------------------------------------------------

@app.get("/api/status")
async def status(request: Request):
    user = request.state.user
    return {**setup.state(), "logged_in": user is not None, "user": _me(user),
            "username": user["username"] if user else None}


@app.post("/api/login")
async def login(request: Request, username: str = Body(...), password: str = Body(...)):
    try:
        token = await asyncio.to_thread(auth.login, username, password, client_ip(request),
                                        request.headers.get("user-agent", ""))
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc))
    return _session_response({"ok": True}, token, request)


@app.post("/api/logout")
async def logout(request: Request):
    user = request.state.user
    auth.logout(request.cookies.get(auth.COOKIE))
    if user:
        auth.audit(user["username"], "logout", "", client_ip(request))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/setup/system")
async def setup_system(request: Request, search: bool = True):
    if auth.has_admin():
        need("admin")(request)
    return await setup.system_check(test_search=search)


@app.post("/api/setup/admin")
async def setup_admin(request: Request, username: str = Body(...), password: str = Body(...)):
    # Only the very first account can be created without logging in
    if auth.has_admin():
        raise HTTPException(403, "An admin account already exists. Log in, then add users under Settings → Users.")
    user_id = await asyncio.to_thread(auth.create_user, username, password, "admin")
    auth.audit(username, "user_created", "first admin (setup)", client_ip(request))
    setup.save_step(2)
    return _session_response({"ok": True}, auth.create_session(user_id, client_ip(request),
                                                               request.headers.get("user-agent", "")), request)


@app.post("/api/setup/step")
async def setup_step(request: Request, step: int = Body(..., embed=True)):
    if auth.has_admin():
        need("admin")(request)
    setup.save_step(step)
    return setup.state()


@app.post("/api/setup/complete")
async def setup_complete(user=ADMIN):
    setup.complete()
    return setup.state()


# Own account -------------------------------------------------------------------

@app.post("/api/account/password")
async def change_password(request: Request, current: str = Body(...), new: str = Body(...), user=VIEWER):
    if not await asyncio.to_thread(auth.verify_password, current, user["password_hash"]):
        auth.audit(user["username"], "password_change_failed", "wrong current password", client_ip(request))
        raise HTTPException(400, "Current password is wrong.")
    if current == new:
        raise HTTPException(400, "Choose a password different from the current one.")
    await asyncio.to_thread(auth.set_password, user["id"], new)
    auth.audit(user["username"], "password_changed", "", client_ip(request))
    # All sessions were revoked; continue with a fresh one
    return _session_response({"ok": True}, auth.create_session(user["id"], client_ip(request),
                                                               request.headers.get("user-agent", "")), request)


@app.get("/api/account/whats-new")
async def whats_new(all: bool = False, user=VIEWER):
    entries = changelog.CHANGELOG if all else changelog.since(user["last_seen_version"], config.VERSION)
    return {"version": config.VERSION, "entries": entries}


@app.post("/api/account/whats-new/seen")
async def whats_new_seen(user=VIEWER):
    db.execute("UPDATE users SET last_seen_version = ? WHERE id = ?", (config.VERSION, user["id"]))
    return {"ok": True}


@app.put("/api/account/preferences")
async def preferences(title_lang: str = Body(..., embed=True), user=VIEWER):
    if title_lang not in ("en", "ja"):
        raise HTTPException(400, "Title language must be en (English) or ja (Japanese).")
    db.execute("UPDATE users SET title_lang = ? WHERE id = ?", (title_lang, user["id"]))
    return {"title_lang": title_lang}


@app.post("/api/account/tour")
async def tour_state(done: bool = Body(True, embed=True), user=VIEWER):
    db.execute("UPDATE users SET tour_done = ? WHERE id = ?", (int(done), user["id"]))
    return {"ok": True}


@app.get("/api/account/sessions")
async def my_sessions(user=VIEWER):
    return {"sessions": auth.user_sessions(user["id"])}


@app.post("/api/account/logout-everywhere")
async def logout_everywhere(request: Request, user=VIEWER):
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    auth.audit(user["username"], "logout_everywhere", "", client_ip(request))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


# Users (admin) -------------------------------------------------------------------

@app.get("/api/users")
async def users(user=ADMIN):
    return {"users": auth.list_users(), "roles": list(auth.ROLES)}


@app.post("/api/users")
async def add_user(request: Request, username: str = Body(...), password: str = Body(...),
                   role: str = Body("member"), must_change: bool = Body(True), user=ADMIN):
    user_id = await asyncio.to_thread(auth.create_user, username, password, role, must_change)
    auth.audit(user["username"], "user_created", f"{username} as {role}", client_ip(request))
    return auth.public_user(auth.get_user(user_id))


@app.put("/api/users/{user_id}")
async def edit_user(request: Request, user_id: int, role: str | None = Body(None),
                    disabled: bool | None = Body(None), user=ADMIN):
    target = auth.get_user(user_id)
    if user_id == user["id"] and (disabled or (role and role != "admin")):
        raise HTTPException(400, "You can't disable or demote your own account. Ask another admin.")
    auth.update_user(user_id, role, disabled)
    changes = ", ".join(x for x in (f"role {role}" if role else "",
                                    {True: "disabled", False: "enabled"}.get(disabled, "")) if x)
    auth.audit(user["username"], "user_changed", f"{target['username']}: {changes}", client_ip(request))
    return auth.public_user(auth.get_user(user_id))


@app.post("/api/users/{user_id}/password")
async def reset_user_password(request: Request, user_id: int, password: str = Body(...),
                              must_change: bool = Body(True), user=ADMIN):
    target = auth.get_user(user_id)
    await asyncio.to_thread(auth.set_password, user_id, password, must_change)
    auth.audit(user["username"], "password_reset", target["username"], client_ip(request))
    return {"ok": True}


@app.delete("/api/users/{user_id}")
async def remove_user(request: Request, user_id: int, user=ADMIN):
    target = auth.get_user(user_id)
    if user_id == user["id"]:
        raise HTTPException(400, "You can't delete your own account.")
    auth.delete_user(user_id)
    auth.audit(user["username"], "user_deleted", target["username"], client_ip(request))
    return {"ok": True}


@app.get("/api/audit")
async def audit_log(user=ADMIN):
    return {"entries": auth.audit_entries()}


# Settings, media server, system ----------------------------------------------------

@app.get("/api/settings")
async def get_settings(user=VIEWER):
    return setup.public_settings()


@app.put("/api/settings")
async def put_settings(request: Request, data: dict = Body(...), user=ADMIN):
    result = setup.save_defaults(data)
    if "mal_client_id" in data:
        recommend.forget()
    auth.audit(user["username"], "settings_changed", ", ".join(sorted(data)), client_ip(request))
    return result


@app.put("/api/mediaserver")
async def put_mediaserver(request: Request, kind: str = Body("none"), url: str = Body(""),
                          token: str | None = Body(None), library_id: str | None = Body(None),
                          library_name: str = Body(""), user=ADMIN):
    setup.save_media_server(kind, url, token or None, library_id, library_name)
    auth.audit(user["username"], "media_server_changed", kind, client_ip(request))
    return setup.public_settings()


@app.post("/api/mediaserver/libraries")
async def mediaserver_libraries(kind: str | None = Body(None), url: str | None = Body(None),
                                token: str | None = Body(None), user=ADMIN):
    return {"libraries": await mediaserver.libraries(kind or None, url or None, token or None)}


@app.post("/api/mediaserver/test")
async def test_mediaserver(kind: str | None = Body(None), url: str | None = Body(None),
                           token: str | None = Body(None), user=ADMIN):
    return await mediaserver.test(kind or None, url or None, token or None)


@app.get("/api/mediaserver/series")
async def mediaserver_series(name: str, user=VIEWER):
    if not mediaserver.configured():
        return {"configured": False, "exists": False}
    try:
        return {"configured": True, "exists": await mediaserver.find_series(name)}
    except mediaserver.MediaServerError as exc:
        return {"configured": True, "exists": False, "error": str(exc)}


@app.get("/api/system")
async def system(user=ADMIN):
    info = await setup.system_check(test_search=False)
    latest = await setup.latest_release()
    return {**info, "latest": latest, "update_available": setup._newer(latest, config.VERSION)}


@app.post("/api/system/update-app")
async def update_app(request: Request, user=ADMIN):
    await updates.start_update(user["username"], client_ip(request))
    return {"ok": True}


@app.get("/api/system/update-status")
async def update_status(fresh: bool = False, user=ADMIN):
    """For the "update available" banner: admins only, cheap (the release lookup is cached;
    fresh=1 asks GitHub now)."""
    return {**await updates.info(fresh), "can_update": config.helper_available()}


@app.get("/api/system/update-progress")
async def update_progress(user=ADMIN):
    """Where a running update is (download, install, restart) and how the last one ended."""
    return updates.progress()


@app.post("/api/system/update-tools")
async def update_tools(user=ADMIN):
    result = await locations._helper("update-tools")
    return {"output": result.get("output", "")}


# Locations -----------------------------------------------------------------------

@app.get("/api/locations")
async def list_locations(user=VIEWER):
    rows = locations.all_locations()
    statuses = await asyncio.gather(*(asyncio.to_thread(locations.status, locations.get(loc["id"])) for loc in rows))
    out = []
    for loc, state in zip(rows, statuses):
        entry = {**loc, "status": state}
        if user["role"] != "admin":
            # Members only need the name to pick a download location
            entry = {k: entry[k] for k in ("id", "name", "type", "is_default", "status")}
        out.append(entry)
    return {"locations": out, "network_shares": config.helper_available()}


@app.post("/api/locations")
async def add_location(request: Request, data: dict = Body(...), user=ADMIN):
    loc = await locations.create(data)
    auth.audit(user["username"], "location_added", loc["name"], client_ip(request))
    return loc


@app.post("/api/locations/browse")
async def browse_shares(data: dict = Body(...), user=ADMIN):
    return await locations.browse(data)


@app.post("/api/locations/folders")
async def browse_folders(data: dict = Body(...), user=ADMIN):
    return {"folders": await locations.folders(data)}


@app.put("/api/locations/{location_id}")
async def edit_location(request: Request, location_id: int, data: dict = Body(...), user=ADMIN):
    loc = await locations.update(location_id, data)
    auth.audit(user["username"], "location_changed", loc["name"], client_ip(request))
    return loc


@app.put("/api/locations/{location_id}/server-path")
async def location_server_path(location_id: int, server_path: str = Body(..., embed=True), user=ADMIN):
    locations.get(location_id)
    db.execute("UPDATE locations SET server_path = ? WHERE id = ?", (server_path.strip(), location_id))
    return {"ok": True}


@app.post("/api/locations/{location_id}/test")
async def test_location(location_id: int, user=ADMIN):
    return await locations.test(location_id)


@app.post("/api/locations/{location_id}/default")
async def default_location(location_id: int, user=ADMIN):
    locations.set_default(location_id)
    return {"ok": True}


@app.delete("/api/locations/{location_id}")
async def delete_location(request: Request, location_id: int, user=ADMIN):
    name = locations.get(location_id)["name"]
    await locations.remove(location_id)
    auth.audit(user["username"], "location_removed", name, client_ip(request))
    return {"ok": True}


# Browse and search -----------------------------------------------------------------

@app.get("/api/browse/{kind}")
async def browse(kind: str, user=VIEWER):
    if kind not in ("trending", "seasonal"):
        raise HTTPException(404)
    try:
        return {"items": await (anilist.trending() if kind == "trending" else anilist.seasonal())}
    except Exception as exc:
        log.warning("AniList %s failed: %s", kind, exc)
        return {"items": [], "error": "Couldn't reach AniList."}


@app.get("/api/recommend/season")
async def recommend_season(user=VIEWER):
    """The best shows airing now: MAL, AniList and Kitsu ratings combined, plus what's popular on hianime."""
    return await recommend.season()


@app.get("/api/recommend/for-you")
async def recommend_for_you(user=VIEWER):
    """Airing shows picked from what this person follows, queued and has in the library."""
    return await recommend.for_you(user)


@app.get("/api/mediaserver/accounts")
async def media_accounts(user=VIEWER):
    """The media server's accounts, for linking watch history, and which one is linked to you."""
    kind = db.get_setting("media_server") if mediaserver.configured() else "none"
    if kind == "none":
        return {"kind": "none", "accounts": [], "linked": None}
    try:
        accounts = await recommend.server_accounts()
    except Exception as exc:
        return {"kind": kind, "accounts": [], "linked": None, "error": str(exc)}
    me = auth.get_user(user["id"])
    linked = await recommend.linked_account(me)
    return {"kind": kind, "accounts": accounts, "linked": linked,
            "off": me["media_user_id"] == mediaserver.NO_HISTORY}


async def _link_media_user(user_id: int, account_id: str) -> dict:
    """Links an AniStash user to a media server account ('-' = don't use watch history,
    '' = match by name again)."""
    name = ""
    if account_id not in ("", mediaserver.NO_HISTORY):
        accounts = await recommend.server_accounts()
        match = next((a for a in accounts if a["id"] == account_id), None)
        if not match:
            raise HTTPException(400, "That account isn't on the media server.")
        name = match["name"]
    db.execute("UPDATE users SET media_user_id = ?, media_user_name = ? WHERE id = ?", (account_id, name, user_id))
    recommend.forget()
    return {"media_user_id": account_id, "media_user_name": name}


@app.put("/api/account/media-user")
async def link_my_media_user(account_id: str = Body(..., embed=True), user=VIEWER):
    return await _link_media_user(user["id"], account_id)


@app.put("/api/users/{user_id}/media-user")
async def link_media_user(request: Request, user_id: int, account_id: str = Body(..., embed=True), user=ADMIN):
    target = auth.get_user(user_id)
    result = await _link_media_user(user_id, account_id)
    auth.audit(user["username"], "media_account_linked", f"{target['username']} → {result['media_user_name'] or account_id or 'auto'}",
               client_ip(request))
    return result


@app.get("/api/search")
async def search(q: str, dub: bool = False, user=VIEWER):
    """Searches by what was typed and by the show's name in the other language (English ↔
    Japanese), so either name finds it. Each result remembers which query found it."""
    async def other_names():
        try:
            return (await anilist.other_names(q))[:2]
        except Exception:  # AniList being down only means no second-language search
            return []

    async def search_others():
        names = await other_names()
        return names, await asyncio.gather(*(anicli.search(x, dub) for x in names), return_exceptions=True)

    # The search for what was typed doesn't wait for AniList to answer
    first, (others, rest) = await asyncio.gather(_attempt(anicli.search(q, dub)), search_others())
    queries, found = [q, *others], [first, *rest]
    if isinstance(found[0], Exception) and all(isinstance(f, Exception) or not f for f in found[1:]):
        raise found[0]
    results, seen = [], set()
    for query, batch in zip(queries, found):
        if isinstance(batch, Exception):
            continue
        for r in batch:
            if r.title.lower() in seen:
                continue
            seen.add(r.title.lower())
            results.append({**r.__dict__, "query": query, "key": f"{len(results)}"})
    return {"results": results, "also_searched": others}


async def _attempt(coro):
    """The result, or the exception it raised (like gather(return_exceptions=True) for one)."""
    try:
        return await coro
    except Exception as exc:
        return exc


@app.get("/api/meta")
async def meta(title: str, user=VIEWER):
    try:
        return {"meta": await anilist.lookup(title)}
    except Exception:
        return {"meta": None}


@app.get("/api/show")
async def show(q: str, i: int, t: str, dub: bool = False, user=VIEWER):
    data, info = await asyncio.gather(shows.seasons(q, i, t, dub), meta(t, user))
    data["meta"] = info
    return data


@app.post("/api/plan")
async def plan(entries: list[dict] = Body(...), dub: bool = Body(False), user=MEMBER):
    """Season numbers, offsets and episode lists for results picked together on the search page."""
    entries = [{"query": e["query"], "index": int(e["index"]), "title": e["title"]} for e in entries]
    for entry, numbered in zip(entries, number_seasons([e["title"] for e in entries])):
        entry.update(season=numbered["season"], part=numbered["part"], special=numbered["special"])
    entries.sort(key=lambda e: (e["special"], e["season"], e["part"], e["title"]))
    await shows.episodes_for(entries, dub)
    shows.set_offsets(entries)
    titles = [e["title"] for e in entries if not e["special"]] or [entries[0]["title"]]
    return {"series_name": base_title(titles[0]), "entries": entries}


@app.post("/api/library/match")
async def library_match(items: list[dict] = Body(..., embed=True), user=VIEWER):
    """Which of these shows ({id, titles}) are already in your folders or media server library."""
    return {"matches": await library.match(items[:200])}


@app.post("/api/existing")
async def existing(location_id: int = Body(...), series_name: str = Body(...), entries: list[dict] = Body(...),
                   user=VIEWER):
    """Which episodes of each season are already at the location, for the ✓ marks."""
    found = await asyncio.gather(*(asyncio.to_thread(
        jobs.existing_episodes, location_id, series_name, int(e["season"]), e["title"],
        [str(x) for x in e.get("episodes", [])], int(e.get("offset") or 0)) for e in entries))
    return {"existing": {e["title"]: sorted(eps) for e, eps in zip(entries, found)}}


# Queue ---------------------------------------------------------------------------------

def _own_batch(user, batch_id: int) -> None:
    """Members manage only their own downloads; admins manage all."""
    row = db.query_one("SELECT created_by FROM batches WHERE id = ?", (batch_id,))
    if not row:
        raise HTTPException(404, "That download doesn't exist.")
    if user["role"] != "admin" and row["created_by"] != user["id"]:
        raise HTTPException(403, "You can only change your own downloads.")


@app.post("/api/queue")
async def enqueue(request: Request, series_name: str = Body(...), location_id: int = Body(...),
                  mode: str = Body("sub"), quality: str = Body("best"), entries: list[dict] = Body(...), user=MEMBER):
    async def all_episodes(entry):
        entry["episodes"] = await anicli.episodes(entry["query"], int(entry["index"]), mode == "dub")
    await asyncio.gather(*(all_episodes(e) for e in entries if e.get("episodes") == "all"))
    result = jobs.enqueue(series_name, location_id, mode, quality, entries, created_by=user["id"])
    auth.audit(user["username"], "queued", f"{series_name}: {result['added']} episode(s), {mode}", client_ip(request))
    return result


@app.get("/api/queue")
async def queue(since: str = "", user=VIEWER):
    """The queue, or just {unchanged: true} when nothing changed since revision `since`."""
    rev = jobs.revision()
    if since and since == rev:
        return {"rev": rev, "unchanged": True}
    return {"rev": rev, "batches": jobs.snapshot(), "counts": jobs.counts(), "me": user["id"],
            "admin": user["role"] == "admin"}


@app.get("/api/queue/summary")
async def queue_summary(user=VIEWER):
    """Queued and running episode counts, for the Downloads badge."""
    return jobs.counts()


@app.post("/api/queue/clear-finished")
async def clear_finished(user=MEMBER):
    return {"removed": jobs.clear_finished(None if user["role"] == "admin" else user["id"])}


@app.post("/api/batches/{batch_id}/rescan")
async def rescan_batch(batch_id: int, user=MEMBER):
    _own_batch(user, batch_id)
    count = jobs.rescan(batch_id)
    await mediaserver.flush()  # now, not after the usual 30 second wait
    warning = db.query_one("SELECT warning FROM batches WHERE id = ?", (batch_id,))["warning"]
    if warning:
        raise HTTPException(400, warning)
    return {"ok": True, "folders": count}


@app.post("/api/batches/{batch_id}/{action}")
async def batch_action(batch_id: int, action: str, user=MEMBER):
    _own_batch(user, batch_id)
    if action == "cancel":
        jobs.cancel_batch(batch_id)
    elif action == "retry":
        jobs.retry_batch(batch_id)
    elif action in ("up", "down"):
        jobs.move("batches", batch_id, action)
    else:
        raise HTTPException(404)
    return {"ok": True}


@app.delete("/api/batches/{batch_id}")
async def delete_batch(batch_id: int, user=MEMBER):
    _own_batch(user, batch_id)
    jobs.remove_batch(batch_id)
    return {"ok": True}


@app.post("/api/items/{item_id}/{action}")
async def item_action(item_id: int, action: str, user=MEMBER):
    row = db.query_one("SELECT batch_id FROM items WHERE id = ?", (item_id,))
    if not row:
        raise HTTPException(404, "That episode isn't in the queue.")
    _own_batch(user, row["batch_id"])
    if action == "cancel":
        jobs.cancel_item(item_id)
    elif action == "retry":
        jobs.retry_item(item_id)
    elif action in ("up", "down"):
        jobs.move("items", item_id, action)
    else:
        raise HTTPException(404)
    return {"ok": True}


# Followed shows -----------------------------------------------------------------------------

def _own_follow(user, follow_id: int) -> None:
    row = follows.get(follow_id)
    if user["role"] != "admin" and row["created_by"] != user["id"]:
        raise HTTPException(403, "You can only change shows you follow.")


@app.get("/api/follows")
async def list_follows(user=VIEWER):
    return {"follows": follows.all_follows(), "me": user["id"], "admin": user["role"] == "admin"}


@app.post("/api/follows")
async def add_follow(request: Request, entry: dict = Body(...), series_name: str = Body(...),
                     location_id: int = Body(...), mode: str = Body("sub"), quality: str = Body("best"),
                     new_seasons: bool = Body(True), only_new: bool = Body(False), user=MEMBER):
    follow = await follows.create(entry, series_name, location_id, mode, quality, user["id"], new_seasons, only_new)
    auth.audit(user["username"], "follow_added", f"{entry.get('title')} ({mode})", client_ip(request))
    return follow


@app.put("/api/follows/{follow_id}")
async def edit_follow(follow_id: int, data: dict = Body(...), user=MEMBER):
    _own_follow(user, follow_id)
    return follows.update(follow_id, data)


@app.post("/api/follows/{follow_id}/check")
async def check_follow(follow_id: int, user=MEMBER):
    _own_follow(user, follow_id)
    follows.check_soon(follow_id)
    return {"ok": True}


@app.delete("/api/follows/{follow_id}")
async def remove_follow(request: Request, follow_id: int, user=MEMBER):
    _own_follow(user, follow_id)
    title = follows.get(follow_id)["title"]
    follows.remove(follow_id)
    auth.audit(user["username"], "follow_removed", title, client_ip(request))
    return {"ok": True}


# Frontend --------------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=config.APP_DIR / "static"), name="static")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(config.APP_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")


def _index_html() -> bytes:
    """index.html with versioned app.js/app.css links, so browsers can keep them until an update."""
    html = (config.APP_DIR / "static" / "index.html").read_text()
    for asset in ("/static/app.css", "/static/app.js"):
        html = html.replace(f'"{asset}"', f'"{asset}?v={config.VERSION}"')
    html = html.replace("<title>", f'<meta name="anistash-version" content="{config.VERSION}">\n  <title>', 1)
    return html.encode()


INDEX_HTML = _index_html()


@app.get("/{path:path}")
async def spa(path: str):
    if path.startswith("api/"):
        return Response(status_code=404)
    return Response(INDEX_HTML, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})
