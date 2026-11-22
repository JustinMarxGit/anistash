import pytest

from app.naming import (base_title, detect_season, episode_path, number_seasons, parse_episode_selection,
                        safe_name)

EPISODES = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "12.5"]


@pytest.mark.parametrize("text, expected", [
    ("1-5, 8, 12-", ["1", "2", "3", "4", "5", "8", "12", "12.5"]),
    ("-2", ["1", "2"]),
    ("7", ["7"]),
    ("12.5", ["12.5"]),
    ("3 1", ["1", "3"]),
    ("all", EPISODES),
])
def test_episode_selection(text, expected):
    assert parse_episode_selection(text, EPISODES) == expected


@pytest.mark.parametrize("text", ["5-1", "40", "abc"])
def test_episode_selection_errors(text):
    with pytest.raises(ValueError):
        parse_episode_selection(text, EPISODES)


@pytest.mark.parametrize("title, base", [
    ("Attack on Titan Season 3 Part 2", "Attack on Titan"),
    ("Attack on Titan: Final Season, Part 1", "Attack on Titan"),
    ("Attack on Titan: The Final Season Part 3", "Attack on Titan"),
    ("Sousou no Frieren 3rd Season", "Sousou no Frieren"),
    ("Mob Psycho 100 II", "Mob Psycho 100"),
    ("Mobile Suit Gundam X", "Mobile Suit Gundam X"),
    ("Part 2", "Part 2"),
])
def test_base_title(title, base):
    assert base_title(title) == base


@pytest.mark.parametrize("title, season", [
    ("Show", 1), ("Show Season 2", 2), ("Show 3rd Season", 3), ("Show II", 2),
    ("Show Season 3 Part 2", 3), ("Show Part 2", 1), ("Show Movie: Something", None),
])
def test_detect_season(title, season):
    assert detect_season(title) == season


def test_number_seasons_groups_parts_and_final_season():
    titles = ["AoT", "AoT Season 2", "AoT Season 3", "AoT Season 3 Part 2",
              "AoT: Final Season, Part 1", "AoT: Final Season, Part 2"]
    numbered = [(e["season"], e["part"]) for e in number_seasons(titles)]
    assert numbered == [(1, 1), (2, 1), (3, 1), (3, 2), (4, 1), (4, 2)]


def test_safe_name():
    assert safe_name("Frieren: Beyond Journey's End") == "Frieren - Beyond Journey's End"
    assert safe_name('a/b?"c') == "a_b__c"
    assert safe_name("...") == "Untitled"


def test_episode_path_server_naming_and_offset():
    path = episode_path("/mnt/x", "Show: One", 3, "1", "Show Season 3 Part 2", "server", offset=22)
    assert str(path) == "/mnt/x/Show - One/Season 03/Show - One - S03E23"
    assert str(episode_path("/mnt/x", "Show", 1, "12.5", "Show", "server", offset=5)).endswith("S01E12.5")
    assert str(episode_path("/mnt/x", "Show", 1, "4", "Show", "original")) == "/mnt/x/Show/Show Episode 4"
