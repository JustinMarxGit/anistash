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


# Discover, anime pages and random picks ---------------------------------------------------------

GENRES_HIDDEN = {"Hentai"}
SORTS = {"popular": "POPULARITY_DESC", "rated": "SCORE_DESC", "trending": "TRENDING_DESC", "newest": "START_DATE_DESC"}
FORMATS = {"tv": ["TV", "TV_SHORT"], "movie": ["MOVIE"], "ona": ["ONA", "OVA", "SPECIAL"]}
STATUSES = {"airing": "RELEASING", "finished": "FINISHED", "upcoming": "NOT_YET_RELEASED"}


async def genres() -> list[str]:
    data = await _gql("query { GenreCollection }", {}, "anilist:genres", 7 * 86400)
    return [g for g in (data or {}).get("GenreCollection") or [] if g not in GENRES_HIDDEN]


def _filters(genre: list[str], fmt: str, status: str, year: int | None) -> tuple[dict, str]:
    """GraphQL variables and a cache-key fragment for the Discover filters (unknown values ignored)."""
    allowed = set(GENRES_HIDDEN)
    variables = {
        "genre": [g for g in genre if g not in allowed][:5] or None,
        "format": FORMATS.get(fmt),
        "status": STATUSES.get(status),
        "year": year if year and 1960 <= year <= datetime.date.today().year + 2 else None,
    }
    key = "|".join(f"{k}={','.join(v) if isinstance(v, list) else v}" for k, v in sorted(variables.items()) if v)
    return variables, key


_DISCOVER = """
query ($page: Int, $sort: [MediaSort], $genre: [String], $format: [MediaFormat], $status: MediaStatus,
       $statusNot: MediaStatus, $year: Int, $minPopularity: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage total }
    media(type: ANIME, isAdult: false, sort: $sort, genre_in: $genre, format_in: $format, status: $status,
          status_not: $statusNot, seasonYear: $year, popularity_greater: $minPopularity) { %s }
  }
}""" % _CARD


async def discover(genre: list[str], sort: str = "popular", fmt: str = "", status: str = "",
                   year: int | None = None, page: int = 1) -> dict:
    """One page (30) of anime matching the filters: {items, has_next}."""
    variables, key = _filters(genre, fmt, status, year)
    sort_key = sort if sort in SORTS else "popular"
    variables.update(page=max(1, min(page, 200)), sort=[SORTS[sort_key]], perPage=30,
                     # "Top rated" and "Newest" would otherwise lead with obscure entries
                     minPopularity=2000 if sort_key == "rated" else 1000 if sort_key == "newest" else None,
                     # "Newest" means out already, unless upcoming shows were asked for
                     statusNot="NOT_YET_RELEASED" if sort_key == "newest" and not variables["status"] else None)
    variables = {k: v for k, v in variables.items() if v is not None}  # AniList errors on explicit nulls
    data = await _gql(_DISCOVER, variables, f"anilist:discover:{sort_key}:{key}:{variables['page']}", 3 * 3600)
    page_data = (data or {}).get("Page") or {}
    return {"items": [_card(m) for m in page_data.get("media") or []],
            "has_next": bool((page_data.get("pageInfo") or {}).get("hasNextPage"))}


async def random_pick(genre: list[str], fmt: str = "", status: str = "") -> dict | None:
    """A random reasonably well-known anime (at least 3000 people track it) matching the filters.

    AniList's result count is capped, so the real number of pages is found on the way: a page
    past the end lowers the limit (remembered per filter for a day) and another page is tried."""
    import random
    variables, key = _filters(genre, fmt, status, None)
    variables.update(sort=["POPULARITY_DESC"], perPage=50, minPopularity=3000)
    variables = {k: v for k, v in variables.items() if v is not None}
    limit_key = f"anilist:random-pages:{key}"
    row = db.query_one("SELECT value FROM cache WHERE key = ? AND expires_at > ?", (limit_key, int(time.time())))
    pages = json.loads(row["value"]) if row else 100
    for _ in range(8):
        page = random.randint(1, max(1, pages))
        data = await _gql(_DISCOVER, {**variables, "page": page}, f"anilist:random:{key}:{page}", 86400)
        media = ((data or {}).get("Page") or {}).get("media") or []
        if media:
            return _card(random.choice(media))
        if page == 1:
            return None  # nothing matches at all
        pages = page - 1
        db.execute("INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                   (limit_key, json.dumps(pages), int(time.time()) + 86400))
    return None


_DETAILS = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    %s synonyms description(asHtml: false) status season duration popularity favourites idMal
    title { native }
    startDate { year month day } endDate { year month day }
    studios(isMain: true) { nodes { name } }
    nextAiringEpisode { episode airingAt }
    tags { name rank isMediaSpoiler }
    relations { edges { relationType node { %s type } } }
    recommendations(perPage: 16, sort: RATING_DESC) { nodes { rating mediaRecommendation { %s } } }
  }
}""" % (_CARD, _CARD, _CARD)

_RELATIONS_SHOWN = {"PREQUEL": "Prequel", "SEQUEL": "Sequel", "PARENT": "Main story", "SIDE_STORY": "Side story",
                    "SPIN_OFF": "Spin-off", "ALTERNATIVE": "Alternative version"}


async def details(media_id: int) -> dict | None:
    """Everything an anime page shows, with related entries and 'more like this'."""
    data = await _gql(_DETAILS, {"id": media_id}, f"anilist:details:{media_id}", 86400)
    media = (data or {}).get("Media")
    if not media:
        return None
    order = list(_RELATIONS_SHOWN)
    edges = [e for e in (media.get("relations") or {}).get("edges") or []
             if e["node"].get("type") == "ANIME" and e["relationType"] in _RELATIONS_SHOWN]
    edges.sort(key=lambda e: (order.index(e["relationType"]), e["node"].get("seasonYear") or 0))
    relations = [{**_card(e["node"]), "relation": _RELATIONS_SHOWN[e["relationType"]]} for e in edges]
    similar = [_card(n["mediaRecommendation"]) for n in (media.get("recommendations") or {}).get("nodes") or []
               if n.get("mediaRecommendation") and (n.get("rating") or 0) > 0]
    start = media.get("startDate") or {}
    return {
        **_card(media),
        "cover": (media.get("coverImage") or {}).get("extraLarge") or (media.get("coverImage") or {}).get("large"),
        "native": (media.get("title") or {}).get("native") or "",
        "names": _names(media),
        "description": (media.get("description") or "").replace("<br>", "\n"),
        "genres": media.get("genres") or [],
        "tags": [t["name"] for t in media.get("tags") or [] if not t.get("isMediaSpoiler") and (t.get("rank") or 0) >= 60][:8],
        "status": media.get("status"), "season": media.get("season"), "duration": media.get("duration"),
        "popularity": media.get("popularity"), "favourites": media.get("favourites"), "mal_id": media.get("idMal"),
        "start": [start.get("year"), start.get("month"), start.get("day")],
        "studio": next((s["name"] for s in (media.get("studios") or {}).get("nodes") or []), ""),
        "next_episode": media.get("nextAiringEpisode"),
        "relations": relations,
        "similar": similar,
    }
