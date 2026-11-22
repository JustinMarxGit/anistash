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

log = logging.getLogger("anidl.shows")


async def seasons(query: str, index: int, title: str, dub: bool) -> dict:
    """Seasons related to the chosen result. Returns {series_name, seasons: [...]}."""
    try:
        chain = await anilist.season_chain(title)
    except Exception as exc:  # AniList being down shouldn't break the show page
        log.warning("AniList season lookup failed: %s", exc)
        chain = []

    names = anilist.series_names(chain) | {normalize(base_title(title))}
    queries = [query]
    for candidate in [base_title(title), *(base_title(n) for e in chain[:1] for n in e["names"][:2])]:
        if normalize(candidate) not in {normalize(q) for q in queries}:
            queries.append(candidate)

    found: dict[str, dict] = {}
    for q in queries[:3]:
        try:
            results = await anicli.search(q, dub)
        except anicli.AniCliError:
            continue
        for r in results:
            if not r.title_known:
                continue
            if normalize(base_title(r.title)) in names and r.title not in found:
                found[r.title] = {"query": q, "index": r.index, "title": r.title}
    # The chosen result is always in, even if no other result matches it
    found[title] = {"query": query, "index": index, "title": title}

    entries = list(found.values())
    for entry, numbered in zip(entries, number_seasons([e["title"] for e in entries])):
        entry.update(season=numbered["season"], part=numbered["part"], special=numbered["special"])
    entries.sort(key=lambda e: (e["special"], e["season"], e["part"], e["title"]))
    await episodes_for(entries, dub)
    set_offsets(entries)

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
