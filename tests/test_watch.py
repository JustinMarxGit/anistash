"""Watch history from Jellyfin and Plex, account linking, and how it shapes Picked for you."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import auth, db, locations, main, mediaserver, recommend, setup

HEADERS = {"X-Requested-With": "anistash"}
NOW = time.time()
DAY = 86400


def run(coro):
    return asyncio.run(coro)


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.1234567Z", time.gmtime(ts))


def fake_server(routes):
    """A tiny HTTP server answering GET path -> JSON (a dict, or a function of the query)."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            seen.append((url.path, query))
            answer = routes.get(url.path)
            if answer is None:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(answer(query) if callable(answer) else answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def jellyfin_items(query):
    if query.get("IncludeItemTypes") == "Series":
        return {"Items": [{"Name": "Blue Box", "Id": "s9"}]}
    return {"Items": [
        {"Id": "e1", "SeriesName": "Frieren", "UserData": {"LastPlayedDate": iso(NOW - 2 * DAY)}},
        {"Id": "e2", "SeriesName": "Frieren", "UserData": {"LastPlayedDate": iso(NOW - 3 * DAY)}},
        {"Id": "e3", "SeriesName": "Frieren", "UserData": {"LastPlayedDate": iso(NOW - 4 * DAY)}},
        {"Id": "e4", "SeriesName": "Old Thing", "UserData": {"LastPlayedDate": iso(NOW - 400 * DAY)}},
    ]}


@pytest.fixture
def jellyfin(isolated):
    server, seen = fake_server({
        "/Users": [{"Id": "u1", "Name": "Kai"}, {"Id": "u2", "Name": "Sam"}],
        "/Users/u1/Items": jellyfin_items,
    })
    db.set_settings(media_server="jellyfin", media_url=f"http://127.0.0.1:{server.server_port}",
                    media_token="KEY", media_library_id="lib1", media_library_name="Anime")
    yield seen
    server.shutdown()


@pytest.fixture
def plex(isolated):
    server, seen = fake_server({
        "/accounts": {"MediaContainer": {"Account": [{"id": 0, "name": ""}, {"id": 1, "name": "kai"},
                                                     {"id": 7, "name": "guest"}]}},
        "/status/sessions/history/all": {"MediaContainer": {"Metadata": [
            {"type": "episode", "grandparentTitle": "Frieren", "ratingKey": "100", "viewedAt": int(NOW - DAY)},
            {"type": "episode", "grandparentTitle": "Frieren", "ratingKey": "100", "viewedAt": int(NOW - 2 * DAY)},
            {"type": "episode", "grandparentTitle": "Frieren", "ratingKey": "101", "viewedAt": int(NOW - 3 * DAY)},
            {"type": "movie", "title": "Some Movie", "viewedAt": int(NOW)},
        ]}},
    })
    db.set_settings(media_server="plex", media_url=f"http://127.0.0.1:{server.server_port}",
                    media_token="TOKEN", media_library_id="4", media_library_name="Anime")
    yield seen
    server.shutdown()


def test_jellyfin_accounts_and_history(jellyfin):
    assert run(mediaserver.server_users()) == [{"id": "u1", "name": "Kai"}, {"id": "u2", "name": "Sam"}]
    history = run(mediaserver.watch_history("u1"))
    assert history["Frieren"]["episodes"] == 3 and abs(history["Frieren"]["last"] - (NOW - 2 * DAY)) < 2
    assert history["Blue Box"]["favorite"] is True and history["Blue Box"]["episodes"] == 0
    played = [q for path, q in jellyfin if path == "/Users/u1/Items" and q.get("Filters") == "IsPlayed"][0]
    assert played["ParentId"] == "lib1"  # only the anime library


def test_plex_accounts_and_history(plex):
    assert run(mediaserver.server_users()) == [{"id": "1", "name": "kai"}, {"id": "7", "name": "guest"}]
    history = run(mediaserver.watch_history("1"))
    assert history == {"Frieren": {"episodes": 2, "last": float(int(NOW - DAY)), "favorite": False}}
    query = [q for path, q in plex if path == "/status/sessions/history/all"][0]
    assert query["accountID"] == "1" and query["librarySectionID"] == "4"


def test_watching_outweighs_owning():
    history = {
        "Frieren": {"episodes": 20, "last": NOW - 5 * DAY, "favorite": False},
        "Old Thing": {"episodes": 20, "last": NOW - 400 * DAY, "favorite": False},
        "Dropped": {"episodes": 1, "last": NOW - 100 * DAY, "favorite": False},
        "Loved": {"episodes": 2, "last": NOW - 10 * DAY, "favorite": True},
    }
    have = [("Spy x Family", "mine"), ("Library Only (2020)", "library"), ("Frieren (2023)", "library")]
    profile = {e["title"]: e for e in recommend.taste_profile(have, history, now=NOW)}
    assert profile["Frieren"]["why"] == "watched" and profile["Frieren"]["weight"] > 2.5
    assert profile["Frieren"]["weight"] > profile["Old Thing"]["weight"] * 5  # watched long ago counts less
    assert profile["Dropped"]["weight"] < 0.2  # tried it and stopped
    assert profile["Loved"]["weight"] >= 1.5
    assert profile["Spy x Family"]["weight"] == 1.0 and profile["Spy x Family"]["why"] == "have"
    assert profile["Library Only"]["weight"] == 0.3
    without = {e["title"]: e for e in recommend.taste_profile(have, None, now=NOW)}
    assert without["Library Only"]["weight"] == 1.0  # no watch history: everything you have counts the same


def test_accounts_are_matched_by_username(jellyfin):
    user_id = auth.create_user("kai", "correct horse battery", "admin")
    account = run(recommend.linked_account(auth.get_user(user_id)))
    assert account == {"id": "u1", "name": "Kai"}
    assert auth.get_user(user_id)["media_user_id"] == "u1"  # remembered
    other = auth.create_user("nobody", "correct horse battery", "member")
    assert run(recommend.linked_account(auth.get_user(other))) is None


def test_linking_rules(jellyfin):
    run(locations.create({"name": "Media", "type": "local", "path": "/tmp"}))
    db.set_settings(setup_complete=True)
    admin_id = auth.create_user("boss", "correct horse battery", "admin")
    member_id = auth.create_user("member", "just visiting here", "member")

    def login(name, pw):
        c = TestClient(main.app, headers=HEADERS)
        assert c.post("/api/login", json={"username": name, "password": pw}).status_code == 200
        return c

    member = login("member", "just visiting here")
    accounts = member.get("/api/mediaserver/accounts").json()
    assert accounts["kind"] == "jellyfin" and len(accounts["accounts"]) == 2 and accounts["linked"] is None
    assert member.put("/api/account/media-user", json={"account_id": "u2"}).json()["media_user_name"] == "Sam"
    assert member.put("/api/account/media-user", json={"account_id": "nope"}).status_code == 400
    assert member.put(f"/api/users/{admin_id}/media-user", json={"account_id": "u2"}).status_code == 403
    assert member.put("/api/account/media-user", json={"account_id": "-"}).status_code == 200
    assert member.get("/api/mediaserver/accounts").json()["off"] is True
    admin = login("boss", "correct horse battery")
    assert admin.put(f"/api/users/{member_id}/media-user", json={"account_id": "u1"}).status_code == 200
    assert auth.get_user(member_id)["media_user_name"] == "Kai"

    setup.save_media_server("plex", "http://127.0.0.1:1", "T")  # another server: links no longer mean anything
    assert auth.get_user(member_id)["media_user_id"] == ""


def test_watched_shows_drive_picks_with_reason():
    candidates = [
        {"id": 10, "title": "Quiet Drama", "english": "Quiet Drama", "romaji": "Shizuka", "genres": ["Drama"],
         "year": 2026, "rating": {"combined": 80}},
        {"id": 11, "title": "Loud Action", "english": "Loud Action", "romaji": "Urusai", "genres": ["Action"],
         "year": 2026, "rating": {"combined": 80}},
    ]
    taste = [
        {"id": 1, "title": {"english": "Frieren", "romaji": "Sousou no Frieren"}, "synonyms": [], "genres": ["Drama"],
         "weight": 3.0, "why": "watched",
         "recommendations": {"nodes": [{"rating": 50, "mediaRecommendation": {"id": 10}}]}},
        {"id": 2, "title": {"english": "Shelf Show", "romaji": "Tana"}, "synonyms": [], "genres": ["Action"],
         "weight": 0.3, "why": "have",
         "recommendations": {"nodes": [{"rating": 50, "mediaRecommendation": {"id": 11}}]}},
    ]
    picks = recommend.pick(candidates, taste, {}, set())["items"]
    assert [p["id"] for p in picks] == [10, 11]
    assert picks[0]["reason"] == "Because you watched Frieren" and picks[1]["reason"] == "Because you have Shelf Show"


def test_picks_still_work_when_the_server_is_down(isolated, monkeypatch):
    db.set_settings(media_server="jellyfin", media_url="http://127.0.0.1:1", media_token="KEY", media_library_id="x")
    user_id = auth.create_user("kai", "correct horse battery", "admin")
    db.execute("UPDATE users SET media_user_id = 'u1', media_user_name = 'Kai' WHERE id = ?", (user_id,))

    async def scored():
        return {"items": [], "related": {}}

    async def taste(titles):
        return []

    monkeypatch.setattr(recommend, "scored", scored)
    monkeypatch.setattr(recommend, "_taste_media", taste)
    db.execute("INSERT INTO locations (name, type, config, path) VALUES ('M', 'local', '{}', ?)", (str(isolated),))
    db.execute("INSERT INTO follows (series_name, season, query, result_index, title, mode, quality, location_id, "
               "created_by, created_at) VALUES ('Blue Box', 1, 'q', 1, 'Blue Box', 'sub', 'best', 1, ?, 0)", (user_id,))
    result = run(recommend.for_you(auth.get_user(user_id)))
    assert result["based_on"] == {"shows": 1, "watched": 0, "server": "", "account": "Kai"}
