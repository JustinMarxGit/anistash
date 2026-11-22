"""AniList GraphQL (public, no key): posters, synopses, trending lists and season chains.

Responses are cached in SQLite so browsing doesn't hit AniList's rate limit (90/min).
"""

import datetime
import json
import time

from . import db, net
from .naming import base_title, normalize

URL = "https://graphql.anilist.co"

_CARD = ("id title { romaji english } coverImage { large extraLarge } bannerImage format episodes seasonYear "
         "averageScore genres")

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

_NODE = "id type format title { romaji english } synonyms seasonYear"
_RELATIONS_DEPTH = 3


def _nested(depth: int) -> str:
    fields = _NODE
    for _ in range(depth):
        fields = f"{_NODE} relations {{ edges {{ relationType node {{ {fields} }} }} }}"
    return fields


# Relations of relations, three steps deep: one call covers three seasons of a prequel/sequel chain
_BY_ID = "query ($id: Int) { Media(id: $id, type: ANIME) { %s } }" % _nested(_RELATIONS_DEPTH)

_LIST = """
query ($sort: [MediaSort], $season: MediaSeason, $year: Int) {
  Page(perPage: 30) {
    media(type: ANIME, isAdult: false, sort: $sort, season: $season, seasonYear: $year) { %s }
  }
}""" % _CARD

SEASON_FORMATS = {"TV", "TV_SHORT", "ONA"}


async def _gql(query: str, variables: dict, cache_key: str, ttl: int):
    row = db.query_one("SELECT value FROM cache WHERE key = ? AND expires_at > ?", (cache_key, int(time.time())))
    if row:
        return json.loads(row["value"])
    return await net.once(("anilist", cache_key), lambda: _fetch(query, variables, cache_key, ttl))


async def _fetch(query: str, variables: dict, cache_key: str, ttl: int):
    now = int(time.time())
    resp = await net.client("anilist").post(URL, json={"query": query, "variables": variables})
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
        "english": media["title"].get("english") or "",
        "romaji": media["title"]["romaji"],
        "cover": (media.get("coverImage") or {}).get("large"),
        "banner": media.get("bannerImage"),
        "genres": (media.get("genres") or [])[:3],
        "format": media.get("format"),
        "episodes": media.get("episodes"),
        "year": media.get("seasonYear"),
        "score": media.get("averageScore"),
    }


async def trending() -> list[dict]:
    data = await _gql(_LIST, {"sort": ["TRENDING_DESC"]}, "anilist:trending:v2", 3600)
    return [_card(m) for m in (data or {}).get("Page", {}).get("media", [])]


async def seasonal() -> list[dict]:
    today = datetime.date.today()
    season = ["WINTER", "SPRING", "SUMMER", "FALL"][(today.month - 1) // 3]
    data = await _gql(_LIST, {"sort": ["POPULARITY_DESC"], "season": season, "year": today.year},
                      f"anilist:seasonal2:{season}:{today.year}", 3600)
    return [_card(m) for m in (data or {}).get("Page", {}).get("media", [])]


def _next_id(media: dict, relation: str) -> int | None:
    """The id of the TV/ONA entry related by `relation` (PREQUEL or SEQUEL), if any."""
    for edge in (media.get("relations") or {}).get("edges", []):
        node = edge["node"]
        if edge["relationType"] == relation and node["type"] == "ANIME" and node.get("format") in SEASON_FORMATS:
            return node["id"]
    return None


def is_first_season(media: dict) -> bool:
    return _next_id(media, "PREQUEL") is None


async def best_match(title: str) -> dict | None:
    return await _best_match(title)


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
        "thumb": (media.get("coverImage") or {}).get("large"),  # small enough for list thumbnails
        "banner": media.get("bannerImage"),
        "description": description,
        "genres": media.get("genres") or [],
        "status": media.get("status"),
        "names": _names(media),
    }


def names(media: dict) -> list[str]:
    """Every title AniList knows for an entry: English, romaji and synonyms."""
    return _names(media)


def _names(media: dict) -> list[str]:
    titles = media.get("title") or {}
    return [t for t in (titles.get("english"), titles.get("romaji"), *(media.get("synonyms") or [])) if t]


async def _media(media_id: int) -> dict | None:
    data = await _gql(_BY_ID, {"id": media_id}, f"anilist:id{_RELATIONS_DEPTH}:{media_id}", 86400)
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

    # Every entry seen so far that came with its own relations, by id. A by-id fetch brings
    # three steps of the chain at once, so most steps need no call of their own.
    known: dict[int, dict] = {}

    def absorb(media: dict) -> None:
        if "relations" not in media:
            return
        known.setdefault(media["id"], media)
        for edge in media["relations"].get("edges", []):
            absorb(edge["node"])

    async def full(media_id: int) -> dict | None:
        if media_id not in known:
            media = await _media(media_id)
            if not media:
                return None
            absorb(media)
            known.setdefault(media_id, media)
        return known[media_id]

    absorb(start)
    first, seen = start, {start["id"]}
    while len(seen) < limit and (prev := _next_id(first, "PREQUEL")) and prev not in seen:
        seen.add(prev)
        first = await full(prev) or first

    chain, current, seen = [], first, set()
    while current and current["id"] not in seen and len(chain) < limit:
        seen.add(current["id"])
        if current.get("format") in SEASON_FORMATS or current["id"] == start["id"]:
            chain.append({"id": current["id"], "title": current["title"].get("english") or current["title"]["romaji"],
                          "names": _names(current), "year": current.get("seasonYear")})
        nid = _next_id(current, "SEQUEL")
        current = await full(nid) if nid else None
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


async def other_names(query: str) -> list[str]:
    """The show's English and Japanese (romaji) titles when `query` is one of its names, so
    a search works whichever language it was typed in. Empty when AniList has no good match."""
    media = await _best_match(query)
    if not media:
        return []
    titles = media.get("title") or {}
    names = [t for t in (titles.get("english"), titles.get("romaji")) if t]
    wanted = normalize(query)
    known = {normalize(n) for n in _names(media)}
    # Only trust the match when the query is (part of) one of the show's names
    if wanted not in known and not any(wanted and wanted in k for k in known):
        return []
    return [n for n in names if normalize(n) != wanted]
