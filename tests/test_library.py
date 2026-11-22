import asyncio

import pytest

from app import db, library, locations, mediaserver


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def media(isolated):
    run(locations.create({"name": "anime on media", "type": "local", "path": str(isolated / "media")}))
    root = isolated / "media"
    for folder, season, count in (("Blue Box (2024)", "Season 01", 25), ("Blue Box (2024)", "Season 02", 7),
                                  ("Frieren - Beyond Journey’s End (2023)", "Season 01", 3)):
        d = root / folder / season
        d.mkdir(parents=True, exist_ok=True)
        for n in range(count):
            (d / f"ep{n}.mp4").write_text("x")
    (root / "Empty Show").mkdir()
    library.invalidate()
    return root


def test_matches_folders_by_series_name(media):
    matches = run(library.match([
        {"id": 1, "titles": ["Blue Box"]},
        {"id": 2, "titles": ["Blue Box Season 2"]},
        {"id": 3, "titles": ["Ao no Hako", "Blue Box Season 3"]},
        {"id": 4, "titles": ["Frieren: Beyond Journey's End"]},
        {"id": 5, "titles": ["Empty Show"]},
        {"id": 6, "titles": ["Something Else"]},
    ]))
    assert matches["1"]["seasons"] == {"1": 25, "2": 7} and matches["1"]["episodes"] == 32
    assert matches["1"]["locations"] == ["anime on media"]
    assert matches["2"]["season"] == 2 and matches["2"]["season_episodes"] == 7
    assert matches["3"]["season"] == 3 and matches["3"]["season_episodes"] == 0  # the series, not that season
    assert matches["4"]["episodes"] == 3  # curly apostrophe, " - " and year don't matter
    assert "5" not in matches and "6" not in matches  # empty folders don't count


def test_media_server_library_counts(media, monkeypatch):
    async def series(library_id):
        return [{"name": "Dandadan", "original_name": "Dan Da Dan", "episodes": 12}]
    monkeypatch.setattr(mediaserver, "library_series", series)
    monkeypatch.setattr(mediaserver, "configured", lambda s=None: True)
    db.set_settings(media_server="plex", media_library_id="4")
    library.invalidate()
    matches = run(library.match([{"id": "d", "titles": ["Dan Da Dan"]}, {"id": "b", "titles": ["Blue Box"]}]))
    assert matches["d"]["server"] == "Plex" and matches["d"]["server_episodes"] == 12 and matches["d"]["episodes"] == 0
    assert matches["b"]["server"] == ""


def test_cache_refreshes_after_invalidate(media):
    assert run(library.match([{"id": 1, "titles": ["New Show"]}])) == {}
    d = media / "New Show" / "Season 01"
    d.mkdir(parents=True)
    (d / "a.mp4").write_text("x")
    assert run(library.match([{"id": 1, "titles": ["New Show"]}])) == {}  # still cached
    library.invalidate()
    assert run(library.match([{"id": 1, "titles": ["New Show"]}]))["1"]["episodes"] == 1
