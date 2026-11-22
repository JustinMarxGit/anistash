"""Discover (genres, sorting, filters), Surprise me and anime pages, with AniList faked."""

import asyncio
import random

from fastapi.testclient import TestClient

from app import anilist, db, locations, main

HEADERS = {"X-Requested-With": "anistash"}


def run(coro):
    return asyncio.run(coro)


def card(i, **extra):
    return {"id": i, "title": {"romaji": f"Show {i}", "english": None}, "coverImage": {"large": "x"},
            "bannerImage": None, "format": "TV", "episodes": 12, "seasonYear": 2020, "averageScore": 80,
            "genres": ["Drama"], **extra}


def fake_anilist(monkeypatch, real_pages=3):
    calls = []

    async def fetch(query, variables, cache_key, ttl):
        calls.append(variables)
        assert None not in variables.values()  # AniList answers explicit nulls with a server error
        if "GenreCollection" in query:
            return {"GenreCollection": ["Action", "Drama", "Hentai"]}
        if "Media(id:" in query:
            if variables["id"] == 404:
                return {"Media": None}
            return {"Media": {**card(1), "description": "A<br>story", "status": "FINISHED", "season": "FALL",
                              "duration": 24, "popularity": 1000, "favourites": 10, "idMal": 5, "synonyms": [],
                              "title": {"romaji": "Show 1", "english": "Show One", "native": "ショー"},
                              "startDate": {"year": 2020, "month": 10, "day": 1}, "endDate": {},
                              "studios": {"nodes": [{"name": "Studio A"}]}, "nextAiringEpisode": None,
                              "tags": [{"name": "Twist", "rank": 90, "isMediaSpoiler": True},
                                       {"name": "Travel", "rank": 85, "isMediaSpoiler": False},
                                       {"name": "Minor", "rank": 20, "isMediaSpoiler": False}],
                              "relations": {"edges": [
                                  {"relationType": "SEQUEL", "node": {**card(3), "type": "ANIME"}},
                                  {"relationType": "PREQUEL", "node": {**card(2), "type": "ANIME"}},
                                  {"relationType": "ADAPTATION", "node": {**card(9), "type": "MANGA"}},
                                  {"relationType": "CHARACTER", "node": {**card(8), "type": "ANIME"}}]},
                              "recommendations": {"nodes": [
                                  {"rating": 30, "mediaRecommendation": card(20)},
                                  {"rating": -2, "mediaRecommendation": card(21)},
                                  {"rating": 5, "mediaRecommendation": None}]}}}
        page = variables["page"]
        per = variables["perPage"]
        media = [card(page * 100 + n) for n in range(per)] if page <= real_pages else []
        return {"Page": {"pageInfo": {"hasNextPage": page < real_pages, "total": 5000}, "media": media}}

    monkeypatch.setattr(anilist, "_fetch", fetch)
    return calls


def test_genres_leave_out_adult(isolated, monkeypatch):
    fake_anilist(monkeypatch)
    assert run(anilist.genres()) == ["Action", "Drama"]


def test_discover_filters(isolated, monkeypatch):
    calls = fake_anilist(monkeypatch)
    result = run(anilist.discover(["Action", "Hentai", "Drama"], "rated", "movie", "airing", 2016, page=2))
    sent = calls[-1]
    assert sent["genre"] == ["Action", "Drama"] and sent["sort"] == ["SCORE_DESC"] and sent["format"] == ["MOVIE"]
    assert sent["status"] == "RELEASING" and sent["year"] == 2016 and sent["page"] == 2
    assert sent["minPopularity"] == 2000  # top rated skips obscure entries
    assert result["has_next"] is True and len(result["items"]) == 30
    run(anilist.discover([], "nonsense", "bogus", "bogus", 1850))
    sent = calls[-1]
    assert sent["sort"] == ["POPULARITY_DESC"] and not {"genre", "format", "status", "year"} & set(sent)
    run(anilist.discover([], "newest"))
    assert calls[-1]["statusNot"] == "NOT_YET_RELEASED"  # newest means out already


def test_random_finds_the_real_end(isolated, monkeypatch):
    calls = fake_anilist(monkeypatch, real_pages=2)
    random.seed(4)
    picks = {run(anilist.random_pick(["Drama"]))["id"] for _ in range(6)}
    assert picks and all(100 <= p < 300 for p in picks)  # only real pages (1 and 2)
    assert all(c["minPopularity"] == 3000 for c in calls if "page" in c)


def test_random_with_nothing_matching(isolated, monkeypatch):
    fake_anilist(monkeypatch, real_pages=0)
    assert run(anilist.random_pick(["Drama"])) is None


def test_anime_page_details(isolated, monkeypatch):
    fake_anilist(monkeypatch)
    info = run(anilist.details(1))
    assert info["title"] == "Show One" and info["native"] == "ショー" and info["studio"] == "Studio A"
    assert info["description"] == "A\nstory"
    assert info["tags"] == ["Travel"]  # spoilers and minor tags left out
    assert [(r["relation"], r["id"]) for r in info["relations"]] == [("Prequel", 2), ("Sequel", 3)]
    assert [m["id"] for m in info["similar"]] == [20]
    assert run(anilist.details(404)) is None


def test_endpoints(isolated, monkeypatch):
    fake_anilist(monkeypatch)
    run(locations.create({"name": "Media", "type": "local", "path": str(isolated)}))
    with TestClient(main.app, headers=HEADERS) as c:
        c.post("/api/setup/admin", json={"username": "admin", "password": "longenough"})
        c.post("/api/setup/complete")
        assert c.get("/api/genres").json() == {"genres": ["Action", "Drama"]}
        assert len(c.get("/api/discover", params={"genre": "Action,Drama", "sort": "trending"}).json()["items"]) == 30
        assert c.get("/api/random", params={"genre": "Drama"}).json()["id"] >= 100
        assert c.get("/api/anime/1").json()["similar"][0]["id"] == 20
        assert c.get("/api/anime/404").status_code == 404
    db.execute("DELETE FROM cache")
