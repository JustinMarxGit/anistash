"""Public ratings and popularity from several sites, for recommendations.

- AniList: every show airing now, with its score, number of scorers, trending and genres.
- MyAnimeList: scores of airing shows, through the official API when an admin has saved a
  (free) Client ID, otherwise through Jikan, an unofficial mirror that needs no key.
- Kitsu: scores of airing shows, matched to MAL ids through Kitsu's own mappings.
- hianime (the site ani-cli downloads from): its home page lists what people are watching
  right now (Trending, Top Airing, Most Popular, Latest Episode) with sub and dub episode
  counts, which also says the show can be downloaded and whether a dub exists.

Every source is optional: one that fails is left out and the others still rank the shows.
Answers are cached in SQLite (hours for scores, an hour for hianime's lists).
"""

import asyncio
import datetime
import html
import json
import logging
import re
import shutil
import time

from . import anilist, config, db, net, vault

log = logging.getLogger("anistash.ratings")

SCORES_TTL = 6 * 3600
LISTS_TTL = 3600
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


RETRY_FAILED = 1800


class SourceDown(Exception):
    pass


async def _cached(key: str, ttl: int, fetch):
    """fetch()'s answer, kept for ttl seconds; identical requests at the same time share one fetch.
    A failure is remembered for half an hour, so a site that's down doesn't slow every page."""
    row = db.query_one("SELECT value FROM cache WHERE key = ? AND expires_at > ?", (key, int(time.time())))
    if row:
        value = json.loads(row["value"])
        if isinstance(value, dict) and "_failed" in value:
            raise SourceDown(value["_failed"])
        return value

    async def run():
        try:
            data = await fetch()
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            db.execute("INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                       (key, json.dumps({"_failed": reason}), int(time.time()) + RETRY_FAILED))
            raise SourceDown(reason) from exc
        db.execute("INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                   (key, json.dumps(data), int(time.time()) + ttl))
        return data
    return await net.once(("ratings", key), run)


# AniList --------------------------------------------------------------------------------

_AIRING = """
query ($page: Int) {
  Page(page: $page, perPage: 50) {
    media(type: ANIME, status: RELEASING, isAdult: false, sort: POPULARITY_DESC) {
      %s idMal synonyms popularity trending status
      stats { scoreDistribution { amount } }
      recommendations(perPage: 10, sort: RATING_DESC) { nodes { rating mediaRecommendation { id } } }
    }
  }
}""" % anilist._CARD


async def airing() -> list[dict]:
    """The 100 most popular shows airing now (AniList's own data, scores included)."""
    pages = await asyncio.gather(*(anilist._gql(_AIRING, {"page": page}, f"anilist:airing-list2:{page}", SCORES_TTL)
                                   for page in (1, 2)))
    seen, out = set(), []
    for data in pages:
        for media in ((data or {}).get("Page") or {}).get("media") or []:
            if media["id"] not in seen:
                seen.add(media["id"])
                out.append(media)
    return out


# MyAnimeList ---------------------------------------------------------------------------------

def mal_client_id() -> str:
    stored = db.get_setting("mal_client_id")
    return vault.decrypt(stored) if stored else ""


async def mal_scores() -> dict[str, dict]:
    """{mal id: {score (0-100), votes}} for shows airing now."""
    client_id = mal_client_id()
    if client_id:
        return await _cached("mal:airing:official", SCORES_TTL, lambda: _mal_official(client_id))
    return await _cached("mal:airing:jikan", SCORES_TTL, _jikan)


async def _mal_official(client_id: str) -> dict[str, dict]:
    resp = await net.client("mal").get(
        "https://api.myanimelist.net/v2/anime/ranking",
        params={"ranking_type": "airing", "limit": 500, "fields": "mean,num_scoring_users"},
        headers={"X-MAL-CLIENT-ID": client_id})
    if resp.status_code in (401, 403):
        raise RuntimeError("MyAnimeList rejected the Client ID.")
    resp.raise_for_status()
    out = {}
    for entry in resp.json().get("data", []):
        node = entry.get("node") or {}
        if node.get("mean"):
            out[str(node["id"])] = {"score": node["mean"] * 10, "votes": node.get("num_scoring_users") or 0}
    return out


async def _jikan() -> dict[str, dict]:
    out = {}
    for page in range(1, 5):  # Jikan allows about 3 requests a second; one at a time is polite
        resp = await net.client("jikan", timeout=6).get(
            "https://api.jikan.moe/v4/top/anime", params={"filter": "airing", "page": page, "limit": 25})
        resp.raise_for_status()
        body = resp.json()
        for entry in body.get("data", []):
            if entry.get("score"):
                out[str(entry["mal_id"])] = {"score": entry["score"] * 10, "votes": entry.get("scored_by") or 0}
        if not (body.get("pagination") or {}).get("has_next_page"):
            break
        await asyncio.sleep(0.5)
    return out


# Kitsu -------------------------------------------------------------------------------------

async def kitsu_scores() -> dict[str, dict]:
    """{mal id: {score (0-100), votes}} for the 100 most watched shows airing now."""
    return await _cached("kitsu:airing", SCORES_TTL, _kitsu)


async def _kitsu() -> dict[str, dict]:
    async def page(offset: int) -> dict:
        resp = await net.client("kitsu").get("https://kitsu.app/api/edge/anime", params={
            "filter[status]": "current", "sort": "-userCount", "page[limit]": 20, "page[offset]": offset,
            "include": "mappings", "fields[anime]": "averageRating,ratingFrequencies,userCount,mappings",
            "fields[mappings]": "externalSite,externalId"})
        resp.raise_for_status()
        return resp.json()

    out = {}
    for body in await asyncio.gather(*(page(offset) for offset in range(0, 100, 20))):
        mal_ids = {m["id"]: m["attributes"]["externalId"] for m in body.get("included", [])
                   if m.get("type") == "mappings" and m["attributes"].get("externalSite") == "myanimelist/anime"}
        for anime in body.get("data", []):
            attrs = anime["attributes"]
            mapped = [mal_ids[m["id"]] for m in anime["relationships"]["mappings"]["data"] if m["id"] in mal_ids]
            if mapped and attrs.get("averageRating"):
                votes = sum(int(v) for v in (attrs.get("ratingFrequencies") or {}).values())
                out[str(mapped[0])] = {"score": float(attrs["averageRating"]), "votes": votes}
    return out


# hianime ------------------------------------------------------------------------------------

def site_url() -> str:
    """The anime site's address, read from the installed ani-cli so it follows `ani-cli -U`."""
    path = shutil.which(config.ANI_CLI)
    try:
        match = re.search(r'^base_api="(https?://[^"]+)"', open(path).read(), re.M) if path else None
    except OSError:
        match = None
    return match.group(1) if match else "https://hianime.at"


# Lists on the home page, by where each starts
_SECTIONS = [
    ("trending", 'id="trending-home"'),
    ("latest", "Latest Episode</h2>"),
    ("new", "New On HiAnime</h2>"),
    ("upcoming", "Top Upcoming</h2>"),
    ("airing", 'anif-block-header">Top Airing'),
    ("popular", 'anif-block-header">Most Popular'),
    ("favorite", 'anif-block-header">Most Favorite'),
    ("completed", 'anif-block-header">Latest Completed'),
    ("top10", "Top 10</h2>"),
]
KEEP = ("trending", "airing", "popular", "latest", "top10")
_ITEM = re.compile(r'(?=<li[ >]|<div class="flw-item|<div class="swiper-slide)')
_TITLE = re.compile(r'(?:title|data-title)="([^"]+)"')
_JNAME = re.compile(r'data-jname="([^"]*)"')
_SUB = re.compile(r'tick-sub">\s*(?:<i[^>]*></i>)?\s*(\d+)')
_DUB = re.compile(r'tick-dub">\s*(?:<i[^>]*></i>)?\s*(\d+)')


def parse_home(page: str) -> list[dict]:
    """[{list, rank, title, jname, sub, dub}] from the site's home page."""
    starts = sorted((page.find(marker), name) for name, marker in _SECTIONS if page.find(marker) >= 0)
    out = []
    for i, (start, name) in enumerate(starts):
        if name not in KEEP:
            continue
        end = starts[i + 1][0] if i + 1 < len(starts) else len(page)
        rank, seen = 0, set()
        for chunk in _ITEM.split(page[start:end])[1:]:
            title, jname = _TITLE.search(chunk), _JNAME.search(chunk)
            if not title:
                continue
            title = html.unescape(title.group(1)).strip()
            if title in seen:  # the Top 10 block repeats itself for today / week / month
                continue
            seen.add(title)
            rank += 1
            sub, dub = _SUB.search(chunk), _DUB.search(chunk)
            out.append({"list": name, "rank": rank, "title": title,
                        "jname": html.unescape(jname.group(1)).strip() if jname else "",
                        "sub": int(sub.group(1)) if sub else 0, "dub": int(dub.group(1)) if dub else 0})
    return out


async def hianime_lists() -> list[dict]:
    async def fetch():
        resp = await net.client("hianime").get(site_url() + "/home", headers={"User-Agent": BROWSER},
                                               follow_redirects=True)
        resp.raise_for_status()
        return parse_home(resp.text)
    return await _cached("hianime:home", LISTS_TTL, fetch)


def season_now() -> tuple[str, int]:
    today = datetime.date.today()
    return ["WINTER", "SPRING", "SUMMER", "FALL"][(today.month - 1) // 3], today.year
