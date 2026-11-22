from fastapi.testclient import TestClient

from app import anicli, anilist, auth, db, main

HEADERS = {"X-Requested-With": "anistash"}


def _site(isolated):
    db.execute("INSERT INTO locations (name, type, config, path, is_default) VALUES ('M', 'local', '{}', ?, 1)",
               (str(isolated),))
    db.set_settings(setup_complete=True)
    auth.create_user("boss", "keeper of the house", "admin")
    c = TestClient(main.app, headers=HEADERS)
    assert c.post("/api/login", json={"username": "boss", "password": "keeper of the house"}).status_code == 200
    return c


def test_search_also_uses_the_other_language_name(isolated, monkeypatch):
    c = _site(isolated)

    async def other_names(query):
        return {"ao no hako": ["Blue Box"], "blue box": ["Ao no Hako"]}.get(query.lower(), [])

    async def search(query, dub=False, fresh=False):
        # The anime site only knows the English title
        if query == "Blue Box":
            return [anicli.Result(1, "Blue Box"), anicli.Result(2, "Blue Box Season 2")]
        return []

    monkeypatch.setattr(anilist, "other_names", other_names)
    monkeypatch.setattr(anicli, "search", search)
    data = c.get("/api/search", params={"q": "Ao no Hako"}).json()
    assert [(r["title"], r["query"], r["index"]) for r in data["results"]] == [
        ("Blue Box", "Blue Box", 1), ("Blue Box Season 2", "Blue Box", 2)]
    assert data["also_searched"] == ["Blue Box"]


def test_search_merges_without_duplicates(isolated, monkeypatch):
    c = _site(isolated)

    async def other_names(query):
        return ["Sousou no Frieren"]

    async def search(query, dub=False, fresh=False):
        return [anicli.Result(1, "Frieren"), anicli.Result(2, "Frieren Season 2")] if query == "Frieren" \
            else [anicli.Result(1, "Frieren Season 2"), anicli.Result(2, "Frieren Mini Anime")]

    monkeypatch.setattr(anilist, "other_names", other_names)
    monkeypatch.setattr(anicli, "search", search)
    titles = [r["title"] for r in c.get("/api/search", params={"q": "Frieren"}).json()["results"]]
    assert titles == ["Frieren", "Frieren Season 2", "Frieren Mini Anime"]


def test_search_still_works_when_anilist_is_down(isolated, monkeypatch):
    c = _site(isolated)

    async def broken(query):
        raise RuntimeError("AniList unreachable")
    monkeypatch.setattr(anilist, "other_names", broken)
    assert [r["title"] for r in c.get("/api/search", params={"q": "show"}).json()["results"]][:1] == ["Show"]


def test_title_language_is_per_person(isolated):
    c = _site(isolated)
    assert c.get("/api/status").json()["user"]["title_lang"] == "en"
    assert c.put("/api/account/preferences", json={"title_lang": "ja"}).json() == {"title_lang": "ja"}
    assert c.get("/api/status").json()["user"]["title_lang"] == "ja"
    assert c.put("/api/account/preferences", json={"title_lang": "fr"}).status_code == 400
