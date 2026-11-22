"""Recommendations: the hianime parser, score blending, cross-site matching and personal picks."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, library, locations, main, ratings, recommend, vault

HEADERS = {"X-Requested-With": "anistash"}
HOME = (Path(__file__).parent / "data" / "hianime_home.html").read_text()


def run(coro):
    return asyncio.run(coro)


def media(id_, english, romaji, score, votes, mal=None, trending=0, genres=(), synonyms=()):
    return {"id": id_, "idMal": mal, "title": {"english": english, "romaji": romaji}, "synonyms": list(synonyms),
            "coverImage": {"large": f"https://s4.anilist.co/{id_}.jpg"}, "bannerImage": None, "format": "TV",
            "episodes": 12, "seasonYear": 2026, "averageScore": score, "genres": list(genres), "popularity": votes,
            "trending": trending, "status": "RELEASING", "stats": {"scoreDistribution": [{"amount": votes}]}}


AIRING = [
    media(1, "One Piece", "One Piece", 87, 290000, mal=21, trending=280, genres=["Action", "Adventure"]),
    media(2, "The Apothecary Diaries Season 3", "Kusuriya no Hitorigoto 3rd Season", 85, 6000, mal=61987,
          trending=290, genres=["Drama", "Mystery"]),
    media(3, "Tiny Gem", "Chiisana Houseki", 95, 12, mal=99, genres=["Drama"]),  # few votes
    media(4, "Hologra", "Holo no Graffiti", 78, 900, genres=["Comedy"], synonyms=["ホロぐら"]),
    media(5, "Blue Box Season 2", "Ao no Hako 2nd Season", 81, 5000, mal=500, genres=["Romance", "Sports", "Drama"]),
    media(6, "Space Duel", "Uchuu Kettou", 80, 4000, mal=600, genres=["Action", "Sci-Fi"]),
]


@pytest.fixture
def sources(monkeypatch):
    calls = {"mal": True}

    async def airing():
        return AIRING

    async def mal_scores():
        if not calls["mal"]:
            raise ratings.SourceDown("ConnectTimeout")
        return {"21": {"score": 89.0, "votes": 1200000}, "61987": {"score": 88.0, "votes": 40000}}

    async def kitsu_scores():
        return {"21": {"score": 84.0, "votes": 160000}}

    async def hianime_lists():
        return ratings.parse_home(HOME)

    for name, fn in (("airing", airing), ("mal_scores", mal_scores), ("kitsu_scores", kitsu_scores),
                     ("hianime_lists", hianime_lists)):
        monkeypatch.setattr(ratings, name, fn)
    recommend.forget()
    yield calls
    recommend.forget()


def test_parses_the_site_home_page():
    items = ratings.parse_home(HOME)
    by_list = {}
    for item in items:
        by_list.setdefault(item["list"], []).append(item)
    assert [(i["rank"], i["title"], i["jname"]) for i in by_list["trending"]] == [
        (1, "Black Clover Season 2", "Black Clover 2nd Season"),
        (2, "The Apothecary Diaries Season 3", "Kusuriya no Hitorigoto 3rd Season")]
    assert [(i["title"], i["sub"], i["dub"]) for i in by_list["airing"]] == [
        ("One Piece", 1180, 1160), ("Witch Hat Atelier", 13, 13)]
    assert [(i["title"], i["sub"], i["dub"]) for i in by_list["latest"]] == [
        ("The Apothecary Diaries Season 3", 8, 6), ("Kanata kara", 8, 0)]
    assert [i["title"] for i in by_list["popular"]] == ["Attack on Titan"]
    assert "upcoming" not in by_list and "completed" not in by_list
    assert ratings.parse_home("<html>changed layout</html>") == []


def test_site_address_follows_ani_cli(tmp_path, monkeypatch):
    script = tmp_path / "ani-cli"
    script.write_text('#!/bin/sh\nbase_api="https://hianime.example"\n')
    script.chmod(0o755)
    monkeypatch.setattr(config, "ANI_CLI", str(script))
    assert ratings.site_url() == "https://hianime.example"


def test_few_votes_are_pulled_toward_the_average():
    assert recommend.shrink(95, 12) < 72  # a dozen perfect-ish scores barely move it
    assert recommend.shrink(89, 1_000_000) == pytest.approx(89, abs=0.01)
    assert recommend.combined({"mal": None, "anilist": None}) is None
    only_anilist = recommend.combined({"anilist": {"score": 80, "votes": 10**6}, "mal": None})
    assert only_anilist == pytest.approx(80, abs=0.1)  # weights renormalize over what's present
    both = recommend.combined({"anilist": {"score": 80, "votes": 10**6}, "mal": {"score": 90, "votes": 10**6}})
    assert 84 < both < 86


def test_best_this_season_combines_sources(sources):
    result = run(recommend.season())
    titles = [c["title"] for c in result["items"]]
    assert titles[0] == "One Piece"
    assert titles.index("Tiny Gem") > titles.index("Blue Box Season 2")  # 95 from 12 votes isn't best
    one_piece = result["items"][0]
    assert one_piece["rating"]["mal"] == 89.0 and one_piece["rating"]["kitsu"] == 84.0
    assert one_piece["available"]["sub"] == 1180 and one_piece["available"]["ranks"]["airing"] == 1
    apothecary = next(c for c in result["items"] if c["id"] == 2)
    assert apothecary["available"]["ranks"]["trending"] == 2 and apothecary["available"]["dub"] == 6
    assert result["sources"] == {"anilist": True, "mal": True, "kitsu": True, "hianime": True}


def test_non_latin_names_dont_match_everything(sources):
    result = run(recommend.season())
    hologra = next(c for c in result["items"] if c["id"] == 4)
    assert hologra["available"] is None


def test_a_site_being_down_leaves_it_out(sources):
    sources["mal"] = False
    result = run(recommend.season())
    assert result["sources"]["mal"] is False and result["items"]
    assert "mal" not in result["items"][0]["rating"]


def test_failures_are_remembered(isolated):
    calls = []

    async def fetch():
        calls.append(1)
        raise ConnectionError("down")

    for _ in range(3):
        with pytest.raises(ratings.SourceDown):
            run(ratings._cached("test:down", 60, fetch))
    assert len(calls) == 1


def test_picks_follow_taste_and_skip_what_you_have(sources):
    candidates = run(recommend.scored())["items"]
    taste = [
        {"title": {"english": "Frieren: Beyond Journey's End", "romaji": "Sousou no Frieren"}, "synonyms": [],
         "genres": ["Adventure", "Drama", "Fantasy"],
         "recommendations": {"nodes": [{"rating": 900, "mediaRecommendation": {"id": 2}},
                                       {"rating": 5, "mediaRecommendation": {"id": 6}}]}},
        {"title": {"english": "Blue Box", "romaji": "Ao no Hako"}, "synonyms": [],
         "genres": ["Romance", "Sports", "Drama"], "recommendations": {"nodes": []}},
        {"title": {"english": "One Piece", "romaji": "One Piece"}, "synonyms": [],
         "genres": ["Action", "Adventure"], "recommendations": {"nodes": []}},
    ]
    disk = {library.series_key("Blue Box (2024)"): {"name": "Blue Box (2024)", "seasons": {1: 25}},
            library.series_key("One Piece"): {"name": "One Piece", "seasons": {1: 1100}}}
    picks = recommend.pick(candidates, taste, disk, followed=set())["items"]
    reasons = {p["title"]: p["reason"] for p in picks}
    assert reasons["The Apothecary Diaries Season 3"] == "Because you have Frieren: Beyond Journey's End"
    assert reasons["Blue Box Season 2"] == "New season of Blue Box"  # you have season 1 only
    assert "One Piece" not in reasons  # you have it
    assert picks[0]["title"] in ("The Apothecary Diaries Season 3", "Blue Box Season 2")
    followed = recommend.pick(candidates, taste, disk, followed={library.series_key("Blue Box")})["items"]
    assert "Blue Box Season 2" not in {p["title"] for p in followed}


def test_endpoints(sources, isolated, monkeypatch):
    async def taste(titles):
        return []

    monkeypatch.setattr(recommend, "_taste_media", taste)
    run(locations.create({"name": "Media", "type": "local", "path": str(isolated / "media")}))
    with TestClient(main.app, headers=HEADERS) as c:
        c.post("/api/setup/admin", json={"username": "admin", "password": "longenough"})
        c.post("/api/setup/complete")
        season = c.get("/api/recommend/season").json()
        assert season["items"][0]["title"] == "One Piece" and season["sources"]["kitsu"] is True
        assert c.get("/api/recommend/for-you").json() == {"items": [], "based_on": 0}


def test_mal_client_id_is_checked_and_stored_encrypted(isolated):
    with TestClient(main.app, headers=HEADERS) as c:
        c.post("/api/setup/admin", json={"username": "admin", "password": "longenough"})
        assert c.put("/api/settings", json={"mal_client_id": "not an id"}).status_code == 400
        good = "0123456789abcdef0123456789abcdef"
        assert c.put("/api/settings", json={"mal_client_id": good}).json()["mal_client_id_set"] is True
        stored = db.get_setting("mal_client_id")
        assert stored != good and vault.decrypt(stored) == good and ratings.mal_client_id() == good
        assert c.put("/api/settings", json={"mal_client_id": ""}).json()["mal_client_id_set"] is False


def test_picks_use_recommendations_from_the_airing_show_too(sources):
    candidates = run(recommend.scored())["items"]
    taste = [{"id": 777, "title": {"english": "SPY x FAMILY", "romaji": "SPY x FAMILY"}, "synonyms": [],
              "genres": ["Action", "Comedy"], "recommendations": {"nodes": []}}]
    picks = recommend.pick(candidates, taste, {}, set(), related={6: {777: 300}})["items"]
    assert picks[0]["title"] == "Space Duel" and picks[0]["reason"] == "Because you have SPY x FAMILY"


def test_promo_clips_are_left_out(sources, monkeypatch):
    async def airing():
        return AIRING + [media(9, None, "HOLOLIVE ALTERNATIVE Teaser PVs", 90, 5000)]

    monkeypatch.setattr(ratings, "airing", airing)
    assert 9 not in {c["id"] for c in run(recommend.scored())["items"]}
