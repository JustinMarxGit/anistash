"""Grouping ani-cli results into the seasons of one series.

hianime lists each season as its own entry ("Show", "Show Season 2", "Sousou no
Show 3rd Season"), sometimes under different languages. AniList's prequel/sequel
chain supplies every title variant of the series, and any ani-cli result whose
base title (season suffix removed) matches one of them is treated as a season.
"""

import asyncio
import logging

from . import anicli, anilist
from .naming import base_title, normalize, number_seasons

log = logging.getLogger("anistash.shows")


async def seasons(query: str, index: int, title: str, dub: bool) -> dict:
    """Seasons related to the chosen result. Returns {series_name, seasons: [...]}.

    Every lookup starts as soon as what it needs is known, rather than step by step: the
    searches that don't depend on AniList start straight away, a result's episode list
    starts as soon as it's known to belong to the series, and when the chosen title is the
    first season its other names are searched before AniList has walked the whole chain.
    """
    names = {normalize(base_title(title))}  # the series' titles, growing as AniList answers
    searches: dict[str, tuple[str, asyncio.Future]] = {}  # normalized query -> (query, its search)
    # Each season's episode list is fetched once, through whichever search found it first
    chosen: dict[str, tuple[str, int]] = {title: (query, index)}
    started: list[asyncio.Future] = [asyncio.ensure_future(anicli.episodes(query, index, dub))]

    def prefetch(q: str, results: list) -> None:
        for r in results:
            if r.title_known and r.title not in chosen and normalize(base_title(r.title)) in names:
                chosen[r.title] = (q, r.index)
                started.append(asyncio.ensure_future(anicli.episodes(q, r.index, dub)))

    async def search_and_prefetch(q: str) -> list:
        results = await anicli.search(q, dub)
        prefetch(q, results)
        return results

    def search(q: str) -> asyncio.Future:
        if normalize(q) not in searches:
            searches[normalize(q)] = (q, asyncio.ensure_future(search_and_prefetch(q)))
        return searches[normalize(q)][1]

    def queries_for(first: dict | None) -> list[str]:
        queries = [query]
        for candidate in [base_title(title), *(base_title(n) for n in (first or {}).get("names", [])[:2])]:
            if normalize(candidate) not in {normalize(q) for q in queries}:
                queries.append(candidate)
        return queries[:3]

    def rescan_finished() -> None:
        for q, future in searches.values():
            if future.done() and not future.exception():
                prefetch(q, future.result())

    chain_task = asyncio.ensure_future(anilist.season_chain(title))
    try:
        for q in queries_for(None):
            search(q)
        try:
            start = await anilist.best_match(title)
        except Exception:
            start = None
        if start and anilist.is_first_season(start):
            names.update(normalize(base_title(n)) for n in anilist.names(start))
            for q in queries_for({"names": anilist.names(start)}):
                search(q)
            rescan_finished()
        try:
            chain = await chain_task
        except Exception as exc:  # AniList being down shouldn't break the show page
            log.warning("AniList season lookup failed: %s", exc)
            chain = []
        names.update(anilist.series_names(chain))
        rescan_finished()

        found: dict[str, dict] = {}
        for q in queries_for(chain[0] if chain else None):
            try:
                results = await search(q)
            except anicli.AniCliError:
                continue
            for r in results:
                if not r.title_known:
                    continue
                if normalize(base_title(r.title)) in names and r.title not in found:
                    found_q, found_index = chosen.get(r.title, (q, r.index))
                    found[r.title] = {"query": found_q, "index": found_index, "title": r.title}
        # The chosen result is always in, even if no other result matches it
        found[title] = {"query": query, "index": index, "title": title}

        entries = list(found.values())
        for entry, numbered in zip(entries, number_seasons([e["title"] for e in entries])):
            entry.update(season=numbered["season"], part=numbered["part"], special=numbered["special"])
        entries.sort(key=lambda e: (e["special"], e["season"], e["part"], e["title"]))
        await episodes_for(entries, dub)
        set_offsets(entries)
    finally:
        # Anything started on a guess that turned out unneeded is finished (or cached) by now
        await asyncio.gather(chain_task, *(f for _, f in searches.values()), *started, return_exceptions=True)

    series = base_title(chain[0]["title"]) if chain else base_title(title)
    # "Show (2013)" is how Jellyfin and Plex both prefer series folders; it makes matching reliable
    if chain and chain[0].get("year"):
        series = f"{series} ({chain[0]['year']})"
    return {"series_name": series, "seasons": entries}


async def episodes_for(entries: list[dict], dub: bool) -> None:
    async def one(entry):
        try:
            entry["episodes"] = await anicli.episodes(entry["query"], entry["index"], dub)
        except anicli.AniCliError as exc:
            entry["episodes"], entry["error"] = [], str(exc)
    await asyncio.gather(*(one(e) for e in entries))


def set_offsets(entries: list[dict]) -> None:
    """Later parts of a season continue the numbering of earlier ones (S03E13 onward)."""
    total: dict[int, int] = {}
    for entry in entries:
        numbers = [int(e) for e in entry.get("episodes", []) if e.isdigit()]
        restarts = bool(numbers) and min(numbers) == 1
        entry["offset"] = total.get(entry["season"], 0) if entry["part"] > 1 and restarts else 0
        total[entry["season"]] = entry["offset"] + (max(numbers) if numbers else 0)
