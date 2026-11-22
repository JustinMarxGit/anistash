"""anidl web app: JSON API plus the single-page frontend in app/static."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import anicli, anilist, auth, config, db, mediaserver, jobs, locations, setup, shows
from .naming import base_title, number_seasons

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("anidl")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    worker = asyncio.create_task(jobs.worker())
    yield
    worker.cancel()
    await mediaserver.flush()


app = FastAPI(title="anidl", version=config.VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)

# Routes reachable before setup is finished (once logged in)
SETUP_ROUTES = ("/api/setup", "/api/locations", "/api/mediaserver", "/api/settings", "/api/system", "/api/account")
PUBLIC_ROUTES = ("/api/status", "/api/login", "/api/logout")
USER_ERRORS = (anicli.AniCliError, locations.LocationError, mediaserver.MediaServerError, jobs.QueueError,
               setup.SetupError, ValueError)


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    request.state.user = auth.session_user(request.cookies.get(auth.COOKIE))
    if not path.startswith("/api/") or path.startswith(PUBLIC_ROUTES):
        return await call_next(request)
    if not auth.has_admin():
        # Fresh install: only the first wizard steps work, without a login
        if path in ("/api/setup/system", "/api/setup/admin", "/api/setup/step"):
            return await call_next(request)
        return JSONResponse({"detail": "setup required"}, status_code=409)
    if request.state.user is None:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    if not db.get_setting("setup_complete") and not path.startswith(SETUP_ROUTES):
        return JSONResponse({"detail": "setup required"}, status_code=409)
    return await call_next(request)


async def user_error(request: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=400)


for _error in USER_ERRORS:
    app.add_exception_handler(_error, user_error)


def _session_response(data: dict, token: str, request: Request) -> JSONResponse:
    resp = JSONResponse(data)
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https")
    return resp


# Session and setup -------------------------------------------------------------

@app.get("/api/status")
async def status(request: Request):
    user = request.state.user
    return {**setup.state(), "logged_in": user is not None, "username": user["username"] if user else None}


@app.post("/api/login")
async def login(request: Request, username: str = Body(...), password: str = Body(...)):
    token = auth.login(username, password)
    if not token:
        await asyncio.sleep(1)  # slows down password guessing
        raise HTTPException(401, "Wrong username or password.")
    return _session_response({"ok": True}, token, request)


@app.post("/api/logout")
async def logout(request: Request):
    auth.logout(request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/api/setup/system")
async def setup_system(search: bool = True):
    return await setup.system_check(test_search=search)


@app.post("/api/setup/admin")
async def setup_admin(request: Request, username: str = Body(...), password: str = Body(...)):
    if auth.has_admin() and request.state.user is None:
        raise HTTPException(403, "An admin account already exists. Log in first.")
    user_id = auth.set_admin(username, password)
    setup.save_step(2)
    return _session_response({"ok": True}, auth.create_session(user_id), request)


@app.post("/api/setup/step")
async def setup_step(step: int = Body(..., embed=True)):
    setup.save_step(step)
    return setup.state()


@app.post("/api/setup/complete")
async def setup_complete():
    setup.complete()
    return setup.state()


@app.post("/api/account/password")
async def change_password(request: Request, current: str = Body(...), new: str = Body(...)):
    row = db.query_one("SELECT password_hash FROM users WHERE id = ?", (request.state.user["id"],))
    if not auth.verify_password(current, row["password_hash"]):
        raise HTTPException(400, "Current password is wrong.")
    user_id = auth.set_admin(request.state.user["username"], new)
    return _session_response({"ok": True}, auth.create_session(user_id), request)


# Settings, Jellyfin, system ----------------------------------------------------

@app.get("/api/settings")
async def get_settings():
    return setup.public_settings()


@app.put("/api/settings")
async def put_settings(data: dict = Body(...)):
    return setup.save_defaults(data)


@app.put("/api/mediaserver")
async def put_mediaserver(kind: str = Body("none"), url: str = Body(""), token: str | None = Body(None),
                          library_id: str | None = Body(None), library_name: str = Body("")):
    setup.save_media_server(kind, url, token or None, library_id, library_name)
    return setup.public_settings()


@app.post("/api/mediaserver/libraries")
async def mediaserver_libraries(kind: str | None = Body(None), url: str | None = Body(None),
                                token: str | None = Body(None)):
    return {"libraries": await mediaserver.libraries(kind or None, url or None, token or None)}


@app.post("/api/mediaserver/test")
async def test_mediaserver(kind: str | None = Body(None), url: str | None = Body(None), token: str | None = Body(None)):
    return await mediaserver.test(kind or None, url or None, token or None)


@app.get("/api/mediaserver/series")
async def mediaserver_series(name: str):
    if not mediaserver.configured():
        return {"configured": False, "exists": False}
    try:
        return {"configured": True, "exists": await mediaserver.find_series(name)}
    except mediaserver.MediaServerError as exc:
        return {"configured": True, "exists": False, "error": str(exc)}


@app.get("/api/system")
async def system():
    info = await setup.system_check(test_search=False)
    latest = await setup.latest_release()
    return {**info, "latest": latest, "update_available": setup._newer(latest, config.VERSION)}


@app.post("/api/system/update-app")
async def update_app():
    await locations._helper("update-app")
    return {"ok": True}


@app.post("/api/system/update-tools")
async def update_tools():
    result = await locations._helper("update-tools")
    return {"output": result.get("output", "")}


# Locations -----------------------------------------------------------------------

@app.get("/api/locations")
async def list_locations():
    out = []
    for loc in locations.all_locations():
        out.append({**loc, "status": await asyncio.to_thread(locations.status, locations.get(loc["id"]))})
    return {"locations": out, "network_shares": config.helper_available()}


@app.post("/api/locations")
async def add_location(data: dict = Body(...)):
    return await locations.create(data)


@app.post("/api/locations/browse")
async def browse_shares(data: dict = Body(...)):
    return await locations.browse(data)


@app.post("/api/locations/folders")
async def browse_folders(data: dict = Body(...)):
    return {"folders": await locations.folders(data)}


@app.put("/api/locations/{location_id}")
async def edit_location(location_id: int, data: dict = Body(...)):
    return await locations.update(location_id, data)


@app.put("/api/locations/{location_id}/server-path")
async def location_server_path(location_id: int, server_path: str = Body(..., embed=True)):
    locations.get(location_id)
    db.execute("UPDATE locations SET server_path = ? WHERE id = ?", (server_path.strip(), location_id))
    return {"ok": True}


@app.post("/api/locations/{location_id}/test")
async def test_location(location_id: int):
    return await locations.test(location_id)


@app.post("/api/locations/{location_id}/default")
async def default_location(location_id: int):
    locations.set_default(location_id)
    return {"ok": True}


@app.delete("/api/locations/{location_id}")
async def delete_location(location_id: int):
    await locations.remove(location_id)
    return {"ok": True}


# Browse and search -----------------------------------------------------------------

@app.get("/api/browse/{kind}")
async def browse(kind: str):
    if kind not in ("trending", "seasonal"):
        raise HTTPException(404)
    try:
        return {"items": await (anilist.trending() if kind == "trending" else anilist.seasonal())}
    except Exception as exc:
        log.warning("AniList %s failed: %s", kind, exc)
        return {"items": [], "error": "Couldn't reach AniList."}


@app.get("/api/search")
async def search(q: str, dub: bool = False):
    results = await anicli.search(q, dub)
    return {"results": [r.__dict__ for r in results]}


@app.get("/api/meta")
async def meta(title: str):
    try:
        return {"meta": await anilist.lookup(title)}
    except Exception:
        return {"meta": None}


@app.get("/api/show")
async def show(q: str, i: int, t: str, dub: bool = False):
    data = await shows.seasons(q, i, t, dub)
    data["meta"] = await meta(t)
    return data


@app.post("/api/plan")
async def plan(entries: list[dict] = Body(...), dub: bool = Body(False)):
    """Season numbers, offsets and episode lists for results picked together on the search page."""
    entries = [{"query": e["query"], "index": int(e["index"]), "title": e["title"]} for e in entries]
    for entry, numbered in zip(entries, number_seasons([e["title"] for e in entries])):
        entry.update(season=numbered["season"], part=numbered["part"], special=numbered["special"])
    entries.sort(key=lambda e: (e["special"], e["season"], e["part"], e["title"]))
    await shows.episodes_for(entries, dub)
    shows.set_offsets(entries)
    titles = [e["title"] for e in entries if not e["special"]] or [entries[0]["title"]]
    return {"series_name": base_title(titles[0]), "entries": entries}


@app.post("/api/existing")
async def existing(location_id: int = Body(...), series_name: str = Body(...), entries: list[dict] = Body(...)):
    """Which episodes of each season are already at the location, for the ✓ marks."""
    return {"existing": {
        e["title"]: sorted(jobs.existing_episodes(location_id, series_name, int(e["season"]), e["title"],
                                                  [str(x) for x in e.get("episodes", [])], int(e.get("offset") or 0)))
        for e in entries
    }}


# Queue ---------------------------------------------------------------------------------

@app.post("/api/queue")
async def enqueue(series_name: str = Body(...), location_id: int = Body(...), mode: str = Body("sub"),
                  quality: str = Body("best"), entries: list[dict] = Body(...)):
    for entry in entries:
        if entry.get("episodes") == "all":
            entry["episodes"] = await anicli.episodes(entry["query"], int(entry["index"]), mode == "dub")
    return jobs.enqueue(series_name, location_id, mode, quality, entries)


@app.get("/api/queue")
async def queue():
    return {"batches": jobs.snapshot()}


@app.post("/api/queue/clear-finished")
async def clear_finished():
    return {"removed": jobs.clear_finished()}


@app.post("/api/batches/{batch_id}/{action}")
async def batch_action(batch_id: int, action: str):
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
async def delete_batch(batch_id: int):
    jobs.remove_batch(batch_id)
    return {"ok": True}


@app.post("/api/items/{item_id}/{action}")
async def item_action(item_id: int, action: str):
    if action == "cancel":
        jobs.cancel_item(item_id)
    elif action == "retry":
        jobs.retry_item(item_id)
    elif action in ("up", "down"):
        jobs.move("items", item_id, action)
    else:
        raise HTTPException(404)
    return {"ok": True}


# Frontend --------------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=config.APP_DIR / "static"), name="static")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(config.APP_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/{path:path}")
async def spa(path: str):
    if path.startswith("api/"):
        return Response(status_code=404)
    return FileResponse(config.APP_DIR / "static" / "index.html", headers={"Cache-Control": "no-cache"})
