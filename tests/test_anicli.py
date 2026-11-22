import asyncio

import pytest

from app import anicli


def run(coro):
    return asyncio.run(coro)


def test_search_parses_menu():
    results = run(anicli.search("show"))
    assert [(r.index, r.title) for r in results] == [(1, "Show"), (2, "Show Season 2"), (3, "Show: Final Season, Part 2")]


def test_qualities_and_fallback_order():
    assert run(anicli.qualities("show", 1, "2", False)) == ["1080", "720", "360"]
    available = ["1080", "720", "360"]
    assert anicli.quality_order(available, "best") == ["1080", "720", "360"]
    assert anicli.quality_order(available, "720") == ["720", "360", "1080"]
    assert anicli.quality_order(available, "worst") == ["360", "720", "1080"]
    assert anicli.quality_order([], "best") == ["best"]


def test_single_result_has_unknown_title():
    [result] = run(anicli.search("single"))
    assert result.index == 1 and not result.title_known


def test_no_results_and_blocked():
    assert run(anicli.search("nothing")) == []
    with pytest.raises(anicli.AniCliError, match="Cloudflare"):
        run(anicli.search("blocked"))


def test_episodes():
    assert run(anicli.episodes("show", 2)) == ["1", "2", "3", "12.5"]


def test_resolve_index_follows_moved_title():
    assert run(anicli.resolve_index("show", 1, "Show Season 2", False)) == 2
    with pytest.raises(anicli.AniCliError):
        run(anicli.resolve_index("show", 1, "Gone", False))


def test_download_reports_progress(tmp_path):
    seen = []
    out = tmp_path / "out"
    out.mkdir()
    files = run(anicli.download("show", 1, "2", False, "best", str(out), lambda p, d, line: seen.append(p)))
    assert [f.rsplit("/", 1)[1] for f in files] == ["Show Episode 2.mp4", "Show Episode 2.vtt"]
    assert 50.0 in seen and 100.0 in seen


def test_download_failure_is_readable(tmp_path):
    with pytest.raises(anicli.AniCliError, match="Invalid episode"):
        run(anicli.download("show", 1, "99", False, "best", str(tmp_path), lambda *a: None))


@pytest.mark.parametrize("line, expected", [
    ("[download]  42.3% of ~ 300.00MiB at 5.00MiB/s ETA 00:30 (frag 10/200)", (42.3, "of ~ 300.00MiB at 5.00MiB/s ETA 00:30")),
    ("frame= 100 fps=0.0 q=-1.0 size=1024kB time=00:01:23.45 bitrate=", (None, "00:01:23 downloaded")),
    ("random noise", (None, None)),
])
def test_parse_progress(line, expected):
    assert anicli.parse_progress(line) == expected
