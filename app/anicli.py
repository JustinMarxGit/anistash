"""The only module that runs ani-cli.

ani-cli is interactive, so search results and episode lists are read by pointing
ANI_CLI_MENU at menu_capture.sh: ani-cli pipes each menu's options into it and the
shim records them and selects nothing, which makes ani-cli exit. Downloads use the
normal non-interactive flags (-S, -e, -d). Scraping stays in ani-cli, so a site
change is fixed by `ani-cli -U` rather than by changing this code.
"""

import asyncio
import os
import re
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass

from . import config

SEARCH_TIMEOUT = 60
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")
_cache: dict[tuple, tuple[float, object]] = {}
_CACHE_TTL = 600


class AniCliError(Exception):
    pass


@dataclass
class Result:
    index: int
    title: str
    title_known: bool = True


def _env(capture: str | None = None, **extra) -> dict:
    env = dict(os.environ)
    env.update(
        ANI_CLI_MENU=config.MENU_CAPTURE,
        ANI_CLI_PLAYER="debug",
        ANI_CLI_LOG="0",
        ANI_CLI_EXACT="0",
        TERM="dumb",
    )
    if capture:
        env["ANIDL_CAPTURE"] = capture
    env.update(extra)
    return env


def _friendly_error(output: str) -> str:
    text = _ANSI.sub("", output)
    if "cloudflare" in text.lower():
        return "The anime source is blocking requests (Cloudflare). Try again later or update ani-cli."
    if "No results found" in text:
        return "No results found."
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line and not line.startswith("Checking dependencies"):
            return line
    return "ani-cli failed without an error message."


def parse_capture(text: str) -> dict[str, list[str]]:
    """Splits menu_capture.sh output into {prompt: [option lines]}."""
    menus: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            menus[current] = []
        elif current is not None and line.strip():
            menus[current].append(line.rstrip())
    return menus


async def _run_capture(args: list[str], timeout: int = SEARCH_TIMEOUT) -> tuple[dict[str, list[str]], str]:
    with tempfile.NamedTemporaryFile("r", prefix="anidl-capture-", delete=True) as capture:
        proc = await asyncio.create_subprocess_exec(
            config.ANI_CLI, *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_env(capture.name),
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            _kill(proc)
            raise AniCliError("ani-cli took too long to answer. The source may be down.")
        except FileNotFoundError:
            raise AniCliError("ani-cli is not installed.")
        capture.seek(0)
        return parse_capture(capture.read()), out.decode(errors="replace")


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _cached(key):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _store(key, value):
    _cache[key] = (time.time() + _CACHE_TTL, value)
    return value


def _mode_args(dub: bool) -> list[str]:
    return ["--dub"] if dub else []


async def search(query: str, dub: bool = False) -> list[Result]:
    query = query.strip()
    if not query:
        return []
    key = ("search", query.lower(), dub)
    if (hit := _cached(key)) is not None:
        return hit
    menus, output = await _run_capture([*_mode_args(dub), query])
    if "Select anime:" in menus:
        results = []
        for line in menus["Select anime:"]:
            number, _, title = line.partition(" ")
            if number.isdigit():
                results.append(Result(int(number), title.strip()))
        return _store(key, results)
    if "Select episode:" in menus or "Invalid episode selection" in output or "All links:" in output:
        # A single result skips the anime menu, so ani-cli never prints its title.
        # With no episodes out yet, ani-cli also skips the episode menu.
        return _store(key, [Result(1, query, title_known=False)])
    if "No results found" in output:
        return _store(key, [])
    raise AniCliError(_friendly_error(output))


async def episodes(query: str, index: int, dub: bool = False) -> list[str]:
    key = ("episodes", query.lower(), index, dub)
    if (hit := _cached(key)) is not None:
        return hit
    menus, output = await _run_capture([*_mode_args(dub), "-S", str(index), query])
    if "Select episode:" in menus:
        return _store(key, [line.split()[0] for line in menus["Select episode:"] if line.split()])
    # A single episode (e.g. a movie) skips the menu and goes straight to "playing" with the debug player.
    if "All links:" in output or "Selected link:" in output:
        return _store(key, ["1"])
    if "Invalid episode selection" in output:
        return _store(key, [])
    raise AniCliError(_friendly_error(output))


async def qualities(query: str, index: int, episode: str, dub: bool) -> list[str]:
    """Stream qualities offered for one episode, best first (e.g. ['1080', '720', '360'])."""
    _, output = await _run_capture([*_mode_args(dub), "-S", str(index), "-e", episode, query])
    text = _ANSI.sub("", output)
    if "All links:" not in text:
        raise AniCliError(_friendly_error(output))
    section = text.split("All links:", 1)[1].split("Selected link:", 1)[0]
    found = []
    for line in section.splitlines():
        label = line.split(">", 1)[0].strip()
        if ">" in line and label and label not in found:
            found.append(label)
    return found


def quality_order(available: list[str], preferred: str) -> list[str]:
    """The qualities to try, in order: the preferred one (or the best below it), then each lower one.

    Qualities above a numeric preference are kept as a last resort, so a download
    still happens when nothing at or below it works.
    """
    numeric = [q for q in available if q.isdigit()]
    other = [q for q in available if not q.isdigit()]
    numeric.sort(key=int, reverse=True)
    if not available:
        return ["best"]
    if preferred == "worst":
        return list(reversed(numeric)) + other
    if preferred.isdigit():
        cap = int(preferred)
        at_or_below = [q for q in numeric if int(q) <= cap]
        above = [q for q in reversed(numeric) if int(q) > cap]
        return at_or_below + above + other
    return numeric + other


async def resolve_index(query: str, index: int, title: str, dub: bool) -> int:
    """The current index of `title` in the results for `query`.

    Search order can change between queueing and downloading, so the stored index is
    only trusted when the title at that index still matches.
    """
    results = await search(query, dub)
    if not results:
        raise AniCliError(f'"{query}" no longer returns any results.')
    if len(results) == 1 and not results[0].title_known:
        return 1
    for result in results:
        if result.index == index and result.title == title:
            return index
    for result in results:
        if result.title == title:
            return result.index
    raise AniCliError(f'"{title}" is no longer in the results for "{query}".')


_PERCENT = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%(.*)")
_FFMPEG_TIME = re.compile(r"time=(\d+:\d+:\d+)")


def parse_progress(line: str) -> tuple[float | None, str | None]:
    """Reads (percent, detail) from a yt-dlp or ffmpeg progress line."""
    match = _PERCENT.search(line)
    if match:
        detail = re.sub(r"\s+", " ", match.group(2)).strip()
        detail = re.sub(r"\(frag \d+/\d+\)", "", detail).strip()
        return float(match.group(1)), detail
    match = _FFMPEG_TIME.search(line)
    if match:
        return None, f"{match.group(1)} downloaded"
    return None, None


async def download(query: str, index: int, episode: str, dub: bool, quality: str, out_dir: str,
                   on_progress, on_start=None) -> list[str]:
    """Downloads one episode into out_dir and returns the files it produced.

    on_progress(percent | None, detail | None, line) is called for every output line.
    on_start(proc) receives the process so the caller can cancel it.
    """
    if not shutil.which(config.ANI_CLI):
        raise AniCliError("ani-cli is not installed.")
    args = [*_mode_args(dub), "-d", "-q", quality or "best", "-S", str(index), "-e", episode, query]
    proc = await asyncio.create_subprocess_exec(
        config.ANI_CLI, *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_env(ANI_CLI_PLAYER="download", ANI_CLI_DOWNLOAD_DIR=out_dir),
        start_new_session=True,
    )
    if on_start:
        on_start(proc)
    tail: list[str] = []
    buffer = b""
    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        buffer += chunk
        # yt-dlp redraws its progress line with \r, so treat \r like a newline
        *lines, buffer = re.split(rb"[\r\n]", buffer)
        for raw in lines:
            line = _ANSI.sub("", raw.decode(errors="replace")).strip()
            if not line:
                continue
            tail = (tail + [line])[-40:]
            percent, detail = parse_progress(line)
            on_progress(percent, detail, line)
    code = await proc.wait()
    files = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir))
    videos = [f for f in files if f.endswith(".mp4") and os.path.getsize(f) > 0]
    if code != 0 or not videos:
        if code < 0:
            raise asyncio.CancelledError()
        raise AniCliError(_friendly_error("\n".join(tail)) if tail else f"ani-cli exited with code {code}")
    return files


async def version(binary: str, *args: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=_env()
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 20)
    except (FileNotFoundError, asyncio.TimeoutError):
        return ""
    text = _ANSI.sub("", out.decode(errors="replace")).strip()
    return text.splitlines()[0] if text else ""
