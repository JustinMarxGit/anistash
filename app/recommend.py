"""Recommendations: the best shows airing now, and picks for each person.

Ratings from AniList, MyAnimeList and Kitsu are combined into one score. Each site's score
is first pulled toward an average (70) by how few people rated the show, so a handful of
perfect scores can't outrank a show thousands of people love. "Buzz" is how much people
are watching it right now: hianime's Trending and Top Airing lists and AniList's trending.

"Picked for you" starts from what that person has: their followed shows, what they queued
and the library. AniList's user recommendations for those shows ("people who liked X also
liked Y") and their genres pick airing shows to suggest, each with the reason.
"""

import asyncio
import hashlib
import json
import logging
import math
import re
import time

from . import anilist, db, library, mediaserver, net, ratings
from .naming import base_title, detect_season, normalize

log = logging.getLogger("anistash.recommend")

PRIOR, PRIOR_VOTES = 70.0, 300
WEIGHTS = {"mal": 0.45, "anilist": 0.40, "kitsu": 0.15}
SOURCE_NAMES = {"anilist": "AniList", "mal": "MyAnimeList", "kitsu": "Kitsu", "hianime": "hianime"}
SHOWN = 30
_memo: dict[str, tuple[float, object]] = {}


def shrink(score: float, votes: int) -> float:
    """A score pulled toward the average when few people rated it."""
    votes = max(votes or 0, 0)
    return (votes * score + PRIOR_VOTES * PRIOR) / (votes + PRIOR_VOTES)


def combined(scores: dict[str, dict]) -> float | None:
    """One 0-100 rating from {source: {score, votes}}, over whichever sources rated the show."""
    present = {k: v for k, v in scores.items() if v and v.get("score")}
    if not present:
        return None
    total = sum(WEIGHTS[k] for k in present)
    return sum(WEIGHTS[k] * shrink(v["score"], v["votes"]) for k, v in present.items()) / total


def _keys(*names: str) -> set[str]:
    """Exact normalized names, then the series key (season suffix and year removed)."""
    return ({normalize(n) for n in names if n} | {library.series_key(n) for n in names if n}) - {""}


def _site_index(entries: list[dict]) -> dict[str, dict]:
    """hianime entries merged by title: best rank in each list, and the episode counts."""
    merged: dict[str, dict] = {}
    for e in entries:
        m = merged.setdefault(e["title"], {"title": e["title"], "jname": e["jname"], "ranks": {}, "sub": 0, "dub": 0})
        m["ranks"][e["list"]] = min(e["rank"], m["ranks"].get(e["list"], 999))
        m["sub"], m["dub"] = max(m["sub"], e["sub"]), max(m["dub"], e["dub"])
    index: dict[str, dict] = {}
    for m in merged.values():
        for key in sorted({normalize(m["title"]), normalize(m["jname"])} - {""}):  # exact names first
            index.setdefault(key, m)
    for m in merged.values():
        for key in _keys(m["title"], m["jname"]):
            index.setdefault(key, m)
    return index


def _match_site(media: dict, index: dict) -> dict | None:
    names = anilist.names(media)
    for name in names:
        if normalize(name) and normalize(name) in index:
            return index[normalize(name)]
    for key in _keys(*names):
        if key in index:
            return index[key]
    return None


def _buzz(site: dict | None, trending: int, top_trending: int) -> float:
    """0-1: how much people are watching it right now."""
    ranks = (site or {}).get("ranks", {})
    hi = max([1 - (ranks[k] - 1) / 10 for k in ("trending", "top10", "airing") if ranks.get(k, 99) <= 10] +
             [0.35 if "latest" in ranks else 0, 0.5 if ranks.get("popular", 99) <= 10 else 0])
    al = min(1.0, trending / top_trending) if top_trending else 0
    return 0.6 * hi + 0.4 * al


_PROMO = re.compile(r"\b(PVs?|Teasers?|Trailers?|CMs?|Promotion(al)? Videos?)\b", re.IGNORECASE)


def _watchable(media: dict) -> bool:
    """Leaves out music videos and promo clips (teasers, PVs), which AniList lists as airing too."""
    if media.get("format") in ("MUSIC",):
        return False
    return not any(_PROMO.search(n) for n in anilist.names(media))


async def _gather_sources() -> tuple[dict, dict]:
    jobs = {"anilist": ratings.airing(), "mal": ratings.mal_scores(), "kitsu": ratings.kitsu_scores(),
            "hianime": ratings.hianime_lists()}
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    data, used = {}, {}
    for name, result in zip(jobs, results):
        if isinstance(result, Exception):
            log.warning("%s ratings unavailable: %r", SOURCE_NAMES[name], result)
            data[name], used[name] = ({} if name in ("mal", "kitsu") else []), False
        else:
            data[name], used[name] = result, True
    return data, used


async def scored() -> dict:
    """Every show airing now with its ratings, buzz and score, best first. Kept 10 minutes."""
    hit = _memo.get("season")
    if hit and hit[0] > time.time():
        return hit[1]
    data, used = await _gather_sources()
    site = _site_index(data["hianime"])
    top_trending = max((m.get("trending") or 0 for m in data["anilist"]), default=0)
    items, related = [], {}
    for media in data["anilist"]:
        if not _watchable(media):
            continue
        votes = sum(s.get("amount", 0) for s in ((media.get("stats") or {}).get("scoreDistribution") or []))
        mal_id = str(media.get("idMal") or "")
        sources = {"anilist": {"score": media.get("averageScore"), "votes": votes} if media.get("averageScore") else None,
                   "mal": data["mal"].get(mal_id), "kitsu": data["kitsu"].get(mal_id)}
        rating = combined(sources)
        if rating is None:
            continue
        on_site = _match_site(media, site)
        buzz = _buzz(on_site, media.get("trending") or 0, top_trending)
        card = anilist._card(media)
        related[media["id"]] = {n["mediaRecommendation"]["id"]: n.get("rating") or 0
                                for n in ((media.get("recommendations") or {}).get("nodes") or [])
                                if n.get("mediaRecommendation")}
        card.update(
            rating={"combined": round(rating, 1),
                    **{k: round(v["score"], 1) for k, v in sources.items() if v and v.get("score")}},
            buzz=round(buzz, 2), genres=media.get("genres") or [], mal_id=media.get("idMal"),
            available={"sub": on_site["sub"], "dub": on_site["dub"],
                       "ranks": on_site["ranks"]} if on_site else None,
            # Quality first; buzz breaks ties between similarly rated shows
            score=round(0.85 * rating + 15 * buzz + (2 if on_site else 0), 2))
        items.append(card)
    items.sort(key=lambda c: c["score"], reverse=True)
    # related: each show's own AniList recommendations ("fans of this also like ..."), for picks
    result = {"items": items, "sources": used, "built_at": int(time.time()), "related": related}
    if used["anilist"]:
        _memo["season"] = (time.time() + 600, result)
    return result


def forget() -> None:
    _memo.clear()


async def keep_fresh() -> None:
    """Rebuilds the ranking in the background every hour, so Browse never waits for the rating sites."""
    await asyncio.sleep(60)  # let the service settle after a start
    while True:
        if db.get_setting("setup_complete"):
            try:
                forget()
                await scored()
            except Exception as exc:
                log.warning("Couldn't refresh recommendations: %s", exc)
        await asyncio.sleep(3600)


async def season() -> dict:
    result = await scored()
    return {"items": result["items"][:SHOWN], "sources": result["sources"], "built_at": result["built_at"]}


# Picked for you -------------------------------------------------------------------------------

_TASTE = """
query (%s) {
  %s
}"""
_TASTE_FIELDS = ("Media(search: $s%d, type: ANIME, isAdult: false) { id title { romaji english } synonyms genres "
                 "recommendations(perPage: 10, sort: RATING_DESC) { nodes { rating mediaRecommendation { id } } } }")


WATCH_TTL = 1800
HALF_LIFE = 90 * 86400  # a show watched 90 days ago counts half as much as one watched today


def _have(user_id: int, library_names: list[str]) -> list[tuple[str, str]]:
    """What this person has: (name, 'mine') for their follows and what they queued, newest
    first, then (name, 'library') for the rest of the library."""
    names = [(r["series_name"], "mine") for r in db.query(
        "SELECT series_name FROM follows WHERE created_by = ? ORDER BY id DESC", (user_id,))]
    names += [(r["series_name"], "mine") for r in db.query(
        "SELECT series_name, MAX(id) AS last FROM batches WHERE created_by = ? GROUP BY series_name ORDER BY last DESC",
        (user_id,))]
    return names + [(name, "library") for name in library_names]


async def linked_account(user) -> dict | None:
    """The media server account whose watching shapes this person's picks: the one they (or an
    admin) chose, else the account with the same name. None without one."""
    if not mediaserver.configured() or user["media_user_id"] == mediaserver.NO_HISTORY:
        return None
    if user["media_user_id"]:
        return {"id": user["media_user_id"], "name": user["media_user_name"]}
    try:
        accounts = await server_accounts()
    except Exception:
        return None
    match = next((a for a in accounts if a["name"].lower() == user["username"].lower()), None)
    if match:
        db.execute("UPDATE users SET media_user_id = ?, media_user_name = ? WHERE id = ?",
                   (match["id"], match["name"], user["id"]))
    return match


async def server_accounts() -> list[dict]:
    kind = db.get_setting("media_server")
    return await ratings._cached(f"media-users:{kind}", WATCH_TTL, mediaserver.server_users)


async def watched(account: dict) -> dict[str, dict]:
    kind = db.get_setting("media_server")
    return await ratings._cached(f"watch:{kind}:{account['id']}", WATCH_TTL,
                                 lambda: mediaserver.watch_history(account["id"]))


def taste_profile(have: list[tuple[str, str]], history: dict[str, dict] | None, now: float | None = None,
                  limit: int = 40) -> list[dict]:
    """[{title, weight, why}] strongest first. With watch history, what you watched (more
    episodes, more recently, favorites) counts most; shows only sitting in the library count
    little. Without it, everything you have counts the same, as before."""
    now = now or time.time()
    profile: dict[str, dict] = {}

    def add(name: str, weight: float, why: str) -> None:
        key = library.series_key(name)
        if not key or weight <= 0:
            return
        entry = profile.get(key)
        if entry is None or weight > entry["weight"]:
            profile[key] = {"title": base_title(anilist_name(name)), "weight": weight,
                            "why": "watched" if why == "watched" or (entry and entry["why"] == "watched") else why}
        elif why == "watched":
            entry["why"] = "watched"

    for name, h in (history or {}).items():
        age = max(0.0, now - h["last"]) if h["last"] else HALF_LIFE * 4
        weight = math.log1p(h["episodes"]) * 0.5 ** (age / HALF_LIFE)
        if h["episodes"] <= 2 and age > 60 * 86400:
            weight *= 0.3  # tried it and stopped
        if h["favorite"]:
            weight = max(weight, 1.0) * 1.5
        add(name, weight, "watched")
    for name, source in have:
        add(name, 1.0 if source == "mine" or history is None else 0.3, "have")
    return sorted(profile.values(), key=lambda e: -e["weight"])[:limit]


def anilist_name(folder: str) -> str:
    """'Blue Box (2024)' -> 'Blue Box' (AniList searches better without the year)."""
    return library._YEAR.sub("", folder).strip()


async def _taste_media(titles: list[str]) -> list[dict]:
    """AniList entries (genres + recommendations) for each title, 10 per request, cached a day."""
    found: dict[str, dict | None] = {}
    missing = []
    for title in titles:
        row = db.query_one("SELECT value FROM cache WHERE key = ? AND expires_at > ?",
                           (f"anilist:taste:{normalize(title)}", int(time.time())))
        if row:
            found[title] = json.loads(row["value"])
        else:
            missing.append(title)
    for start in range(0, len(missing), 10):
        batch = missing[start:start + 10]
        query = _TASTE % (", ".join(f"$s{i}: String" for i in range(len(batch))),
                          "\n  ".join(f"a{i}: " + _TASTE_FIELDS % i for i in range(len(batch))))
        try:
            resp = await net.client("anilist").post(
                anilist.URL, json={"query": query, "variables": {f"s{i}": t for i, t in enumerate(batch)}})
            body = resp.json()
        except Exception as exc:
            log.warning("AniList taste lookup failed: %s", exc)
            continue
        answers = body.get("data") or {}
        for i, title in enumerate(batch):
            media = answers.get(f"a{i}")
            found[title] = media
            db.execute("INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                       (f"anilist:taste:{normalize(title)}", json.dumps(media), int(time.time()) + 86400))
    return [{**m, "query": t} for t, m in found.items() if m]


async def for_you(user) -> dict:
    disk, _ = await library._index()
    account = await linked_account(user)
    history = None
    if account:
        try:
            history = await watched(account)
        except Exception as exc:
            log.warning("Couldn't read watch history for %s: %s", user["username"], exc)
    profile = taste_profile(_have(user["id"], [entry["name"] for entry in disk.values()]), history)
    server = {"jellyfin": "Jellyfin", "plex": "Plex"}.get(db.get_setting("media_server"), "")
    based_on = {"shows": len(profile), "watched": sum(1 for e in profile if e["why"] == "watched"),
                "server": server if history is not None else "", "account": (account or {}).get("name", "")}
    if not profile:
        return {"items": [], "based_on": based_on}
    fingerprint = hashlib.sha1(json.dumps([(e["title"], round(e["weight"], 2)) for e in profile]).encode()).hexdigest()[:12]
    hit = _memo.get(f"you:{user['id']}")
    if hit and hit[0] > time.time() and hit[1][0] == fingerprint:
        return hit[1][1]

    season_data, taste = await asyncio.gather(scored(), _taste_media([e["title"] for e in profile]))
    weights = {e["title"]: e for e in profile}
    for media in taste:
        media.update(weight=weights[media["query"]]["weight"], why=weights[media["query"]]["why"])
    result = pick(season_data["items"], taste, disk, followed_keys(), season_data["related"])
    result["based_on"] = based_on
    _memo[f"you:{user['id']}"] = (time.time() + 3600, (fingerprint, result))
    return result


def followed_keys() -> set[str]:
    return {library.series_key(r["series_name"]) for r in db.query("SELECT series_name FROM follows")}


def pick(candidates: list[dict], taste: list[dict], disk: dict, followed: set[str], related: dict | None = None,
         shown: int = 20) -> dict:
    """Ranks airing shows for one person. Pure function of its inputs (tested directly).

    A show scores for being recommended by fans of your shows (either way round: from your
    show's AniList page or from its own), for sharing their genres, and for its rating.
    Decades-old long runners only show up when they're recommended directly."""
    related = related or {}
    recommended: dict[int, float] = {}
    because: dict[int, tuple[float, str, str]] = {}  # strongest (contribution, show, watched/have)
    genres: dict[str, float] = {}
    taste_keys: dict[str, str] = {}
    taste_ids: dict[int, str] = {}
    for media in taste:
        title = media.get("title") or {}
        label = title.get("english") or title.get("romaji") or ""
        strength, why = media.get("weight", 1.0), media.get("why", "have")
        if media.get("id"):
            taste_ids[media["id"]] = (label, strength, why)
        for key in _keys(*anilist.names(media)):
            taste_keys.setdefault(key, label)
        for g in media.get("genres") or []:
            genres[g] = genres.get(g, 0) + strength
        for node in ((media.get("recommendations") or {}).get("nodes") or []):
            rec, rating = node.get("mediaRecommendation"), node.get("rating") or 0
            if not rec or rating <= 0:
                continue
            weight = math.log1p(rating) * strength
            recommended[rec["id"]] = recommended.get(rec["id"], 0) + weight
            if weight > because.get(rec["id"], (0, "", ""))[0]:
                because[rec["id"]] = (weight, label, why)
    # The other way round: this airing show's fans recommend one of your shows
    for show_id, recs in related.items():
        for taste_id, rating in recs.items():
            if taste_id in taste_ids and rating > 0:
                label, strength, why = taste_ids[taste_id]
                weight = math.log1p(rating) * strength
                recommended[show_id] = recommended.get(show_id, 0) + weight
                if weight > because.get(show_id, (0, "", ""))[0]:
                    because[show_id] = (weight, label, why)
    top_rec = max(recommended.values(), default=0)
    taste_norm = math.sqrt(sum(v * v for v in genres.values())) or 1
    this_year = max((c.get("year") or 0 for c in candidates), default=0)
    picks = []
    for card in candidates:
        names = [card.get("english"), card.get("romaji"), card.get("title")]
        keys = _keys(*names)
        season = next((s for s in (detect_season(n) for n in names if n) if s), None)
        series = next((taste_keys[k] for k in keys if k in taste_keys), None)
        if any(k in followed for k in keys):
            continue
        on_disk = next((disk[k] for k in keys if k in disk), None)
        if series or on_disk:
            # A series you have: only a season you don't have yet is worth suggesting
            have = (on_disk or {}).get("seasons", {})
            if not season or season in have or season == 1:
                continue
            picks.append({**card, "reason": f"New season of {series or on_disk['name']}",
                          "match": 2.0 * card["rating"]["combined"] / 100})
            continue
        rec = recommended.get(card["id"], 0) / top_rec if top_rec else 0
        own = card.get("genres") or []
        # cosine similarity between this show's genres and the genres of your shows
        affinity = sum(genres.get(g, 0) for g in own) / (taste_norm * math.sqrt(len(own))) if own else 0
        if rec == 0 and affinity < 0.4:
            continue
        long_runner = (card.get("year") or this_year) < this_year - 3
        match = (0.65 * rec + 0.35 * affinity) * card["rating"]["combined"] / 100
        if long_runner and not rec:
            match *= 0.4
        if rec and card["id"] in because:
            _, show, why = because[card["id"]]
            reason = f"Because you {'watched' if why == 'watched' else 'have'} {show}"
        else:
            liked = sorted((g for g in card.get("genres", []) if g in genres), key=lambda g: -genres[g])[:2]
            reason = f"Because you like {' and '.join(liked)}" if liked else "Popular with fans of your shows"
        picks.append({**card, "reason": reason, "match": round(match, 3)})
    picks.sort(key=lambda c: c["match"], reverse=True)
    return {"items": picks[:shown]}
