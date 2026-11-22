"""AniList GraphQL (public, no key): posters, synopses, trending lists and season chains.

Responses are cached in SQLite so browsing doesn't hit AniList's rate limit (90/min).
"""

import datetime
import json
import time

import httpx

from . import db
from .naming import base_title, normalize

URL = "https://graphql.anilist.co"

_CARD = "id title { romaji english } coverImage { large } format episodes seasonYear averageScore"

_LOOKUP = """
query ($search: String) {
  Page(perPage: 10) {
    media(search: $search, type: ANIME, isAdult: false, sort: SEARCH_MATCH) {
      id title { romaji english native } synonyms
      coverImage { large extraLarge } bannerImage
      description(asHtml: false) episodes format status seasonYear genres averageScore
      relations { edges { relationType node { id type format title { romaji english } synonyms seasonYear } } }
    }
  }
}"""

_BY_ID = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id title { romaji english } synonyms format seasonYear
    relations { edges { relationType node { id type format title { romaji english } synonyms seasonYear } } }
  }
}"""

_LIST = """
query ($sort: [MediaSort], $season: MediaSeason, $year: Int) {
  Page(perPage: 30) {
    media(type: ANIME, isAdult: false, sort: $sort, season: $season, seasonYear: $year) { %s }
  }
}""" % _CARD

SEASON_FORMATS = {"TV", "TV_SHORT", "ONA"}


async def _gql(query: str, variables: dict, cache_key: str, ttl: int):
    now = int(time.time())
    row = db.query_one("SELECT value FROM cache WHERE key = ? AND expires_at > ?", (cache_key, now))
    if row:
        return json.loads(row["value"])
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(URL, json={"query": query, "variables": variables})
    if resp.status_code == 404:
        data = None
    else:
        resp.raise_for_status()
        data = resp.json().get("data")
    db.execute(
        "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
        (cache_key, json.dumps(data), now + ttl),
    )
    return data


def _card(media: dict) -> dict:
    return {
        "id": media["id"],
        "title": media["title"].get("english") or media["title"]["romaji"],
        "romaji": media["title"]["romaji"],
        "cover": (media.get("coverImage") or {}).get("large"),
        "format": media.get("format"),
        "episodes": media.get("episodes"),
        "year": media.get("seasonYear"),
        "score": media.get("averageScore"),
    }


async def trending() -> list[dict]:
    data = await _gql(_LIST, {"sort": ["TRENDING_DESC"]}, "anilist:trending", 3600)
    return [_card(m) for m in (data or {}).get("Page", {}).get("media", [])]


async def seasonal() -> list[dict]:
    today = datetime.date.today()
    season = ["WINTER", "SPRING", "SUMMER", "FALL"][(today.month - 1) // 3]
    data = await _gql(_LIST, {"sort": ["POPULARITY_DESC"], "season": season, "year": today.year},
                      f"anilist:seasonal:{season}:{today.year}", 3600)
    return [_card(m) for m in (data or {}).get("Page", {}).get("media", [])]


async def _best_match(title: str) -> dict | None:
    """AniList's search ranks spin-offs above the show itself, so prefer an exact title
    match, then a TV/ONA entry of the same series, then whatever ranked first."""
    data = await _gql(_LOOKUP, {"search": title}, f"anilist:lookup:{normalize(title)}", 86400)
    candidates = ((data or {}).get("Page") or {}).get("media") or []
    if not candidates:
        return None
    wanted, wanted_base = normalize(title), normalize(base_title(title))
    for media in candidates:
        if wanted in {normalize(n) for n in _names(media)}:
            return media
    for media in candidates:
        if media.get("format") in SEASON_FORMATS and wanted_base in {normalize(base_title(n)) for n in _names(media)}:
            return media
    return candidates[0]


async def lookup(title: str) -> dict | None:
    """Best AniList match for a title, with poster and synopsis. None if nothing matches."""
    media = await _best_match(title)
    if not media:
        return None
    description = (media.get("description") or "").replace("<br>", "\n")
    return {
        **_card(media),
        "cover": (media.get("coverImage") or {}).get("extraLarge") or (media.get("coverImage") or {}).get("large"),
        "banner": media.get("bannerImage"),
        "description": description,
        "genres": media.get("genres") or [],
        "status": media.get("status"),
        "names": _names(media),
    }


def _names(media: dict) -> list[str]:
    titles = media.get("title") or {}
    return [t for t in (titles.get("english"), titles.get("romaji"), *(media.get("synonyms") or [])) if t]


async def _media(media_id: int) -> dict | None:
    data = await _gql(_BY_ID, {"id": media_id}, f"anilist:id:{media_id}", 86400)
    return (data or {}).get("Media")


async def season_chain(title: str, limit: int = 12) -> list[dict]:
    """The TV seasons of the series `title` belongs to, first season first.

    Walks PREQUEL edges back to the start, then SEQUEL edges forward, keeping only
    TV/ONA entries (movies and OVAs are left out). Each entry has `names`, every
    title variant AniList knows, used to match ani-cli results.
    """
    start = await _best_match(title)
    if not start:
        return []

    def next_id(media: dict, relation: str) -> int | None:
        for edge in (media.get("relations") or {}).get("edges", []):
            node = edge["node"]
            if edge["relationType"] == relation and node["type"] == "ANIME" and node.get("format") in SEASON_FORMATS:
                return node["id"]
        return None

    first, seen = start, {start["id"]}
    while len(seen) < limit and (prev := next_id(first, "PREQUEL")) and prev not in seen:
        seen.add(prev)
        first = await _media(prev) or first

    chain, current, seen = [], first, set()
    while current and current["id"] not in seen and len(chain) < limit:
        seen.add(current["id"])
        if current.get("format") in SEASON_FORMATS or current is start:
            chain.append({"id": current["id"], "title": current["title"].get("english") or current["title"]["romaji"],
                          "names": _names(current), "year": current.get("seasonYear")})
        nid = next_id(current, "SEQUEL")
        current = await _media(nid) if nid else None
    return chain


def series_names(chain: list[dict]) -> set[str]:
    """Normalized base titles across a season chain, for matching ani-cli results."""
    return {normalize(base_title(name)) for entry in chain for name in entry["names"]}


_AIRING = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id status episodes
    nextAiringEpisode { airingAt episode timeUntilAiring }
  }
}"""


async def media_id(title: str) -> int | None:
    media = await _best_match(title)
    return media["id"] if media else None


async def airing(media_id_: int) -> dict:
    """Airing status of a show: {status, episodes, next_episode, next_airing_at}. Cached for an hour."""
    data = await _gql(_AIRING, {"id": media_id_}, f"anilist:airing:{media_id_}", 3600)
    media = (data or {}).get("Media") or {}
    upcoming = media.get("nextAiringEpisode") or {}
    return {
        "status": media.get("status"),            # RELEASING, FINISHED, NOT_YET_RELEASED, HIATUS, CANCELLED
        "episodes": media.get("episodes"),
        "next_episode": upcoming.get("episode"),
        "next_airing_at": upcoming.get("airingAt"),
    }
