"""Pure helpers: episode ranges, season detection and output file names."""

import re
import unicodedata
from pathlib import Path

ROMAN = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}

# Suffixes that mark a later season of the same series. Order matters: longest forms first.
# base_title() strips these repeatedly, so "Show: The Final Season, Part 2" loses both.
_SEASON_SUFFIX = re.compile(
    r"""[\s,:\-–—]*(
        (the\s+)?final\s+season
      | season\s*\d+
      | \d+(st|nd|rd|th)\s+season
      | s\d+
      | (cour|part)\s*\d+
      | (ii|iii|iv|vi|vii|viii|ix)
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_PART = re.compile(r"\b(?:part|cour)\s*(\d+)\s*$", re.IGNORECASE)
_FINAL = re.compile(r"\bfinal\s+season\b", re.IGNORECASE)

_SPECIAL = re.compile(r"\b(movie|film|ova|oad|special|specials|recap|mini anime|chibi)\b", re.IGNORECASE)


def parse_episode_selection(text: str, available: list[str]) -> list[str]:
    """Turns '1-5, 8, 12-' into the matching episodes from `available`, in list order.

    An open-ended range ('12-') runs to the last episode, '-3' starts at the first.
    Decimal episodes (e.g. '12.5') can be named directly or fall inside a range.
    """
    def num(value: str) -> float:
        return float(value)

    numeric = [e for e in available if _is_number(e)]
    chosen: set[str] = set()
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        if part.lower() in ("all", "*"):
            chosen.update(available)
            continue
        match = re.fullmatch(r"(\d+(?:\.\d+)?)?-(\d+(?:\.\d+)?)?", part)
        if match and (match.group(1) or match.group(2)):
            lo = num(match.group(1)) if match.group(1) else float("-inf")
            hi = num(match.group(2)) if match.group(2) else float("inf")
            if lo > hi:
                raise ValueError(f"Range {part} runs backwards")
            chosen.update(e for e in numeric if lo <= num(e) <= hi)
        elif part in available:
            chosen.add(part)
        elif _is_number(part) and any(num(e) == num(part) for e in numeric):
            chosen.update(e for e in numeric if num(e) == num(part))
        else:
            raise ValueError(f"Episode {part} doesn't exist")
    return [e for e in available if e in chosen]


def _is_number(value: str) -> bool:
    return re.fullmatch(r"\d+(\.\d+)?", value) is not None


def normalize(title: str) -> str:
    """Lowercase, accents removed, punctuation collapsed, for comparing titles."""
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    text = re.sub(r"['’`]", "", text.lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def base_title(title: str) -> str:
    """The series title without a season suffix: 'Show Season 2' -> 'Show'."""
    previous = None
    text = title.strip()
    while text != previous:
        previous = text
        stripped = _SEASON_SUFFIX.sub("", text).strip(" :-–—")
        # Never strip a title down to nothing (e.g. a show literally called "Part 2")
        text = stripped or text
    return text


def is_special(title: str) -> bool:
    return bool(_SPECIAL.search(title))


def detect_season(title: str) -> int | None:
    """The season number written in the title, or None if it doesn't say."""
    title = _PART.sub("", title.strip()).strip(" ,:-–—")
    t = title.lower()
    for pattern in (r"season\s*(\d+)", r"(\d+)(?:st|nd|rd|th)\s+season", r"\bs(\d+)\s*$"):
        match = re.search(pattern, t)
        if match:
            return int(match.group(1))
    match = re.search(r"\s(ii|iii|iv|vi|vii|viii|ix)\s*$", t)
    if match:
        return ROMAN[match.group(1)]
    if base_title(title) == title.strip() and not is_special(title):
        return 1
    return None


def detect_part(title: str) -> int:
    """'Show Season 3 Part 2' -> 2. Titles without a part are part 1."""
    match = _PART.search(title.strip())
    return int(match.group(1)) if match else 1


def number_seasons(titles: list[str]) -> list[dict]:
    """Season number, part and episode-offset order for each title of one series.

    Parts of the same season share its number (hianime restarts episode numbers per
    part, so the caller offsets later parts' episodes). "Final Season" comes after
    the highest explicit season. Specials go to season 0, unknowns to the next free number.
    """
    entries = []
    for title in titles:
        special = is_special(title)
        season = 0 if special else (None if _FINAL.search(title) else detect_season(title))
        entries.append({"title": title, "season": season, "part": detect_part(title),
                        "special": special, "final": bool(_FINAL.search(title))})
    highest = max([e["season"] for e in entries if e["season"]] or [0])
    for e in entries:
        if e["final"] and not e["special"]:
            e["season"] = highest + 1
    next_free = max([e["season"] for e in entries if e["season"] is not None] or [0]) + 1
    for e in entries:
        if e["season"] is None:
            e["season"] = next_free
            next_free += 1
        del e["final"]
    return entries


def safe_name(text: str) -> str:
    """A string usable as a single path component on SMB, NFS and ext4."""
    text = re.sub(r"\s*:\s*", " - ", text)
    text = re.sub(r'[<>"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return text[:180] or "Untitled"


def episode_label(episode: str) -> str:
    if re.fullmatch(r"\d+", episode):
        return f"{int(episode):02d}"
    return episode


def episode_path(root: str | Path, series: str, season: int, episode: str, title: str, naming: str,
                 offset: int = 0) -> Path:
    """Where a downloaded episode ends up, without extension.

    offset shifts numbered episodes, for later parts of a season whose numbering restarts at 1.
    """
    show = safe_name(series)
    if naming == "server":
        if offset and re.fullmatch(r"\d+", episode):
            episode = str(int(episode) + offset)
        return Path(root) / show / f"Season {season:02d}" / f"{show} - S{season:02d}E{episode_label(episode)}"
    return Path(root) / show / safe_name(f"{title} Episode {episode}")
