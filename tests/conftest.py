import os
import stat
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture(scope="session")
def fake_video(tmp_path_factory):
    """A real 12 second H.264/AAC mp4, so ffprobe and the remux run for real."""
    path = tmp_path_factory.mktemp("video") / "sample.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=12:size=160x90:rate=5",
                    "-f", "lavfi", "-i", "sine=duration=12", "-c:v", "libx264", "-c:a", "aac", "-shortest",
                    "-f", "mpegts", str(path)], check=True)  # MPEG-TS bytes in a .mp4, like an HLS download
    return path


@pytest.fixture(scope="session")
def fake_dub_video(tmp_path_factory):
    """Same picture, different (higher) audio tone, like an English dub of the same episode."""
    path = tmp_path_factory.mktemp("dub") / "dub.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=12:size=160x90:rate=5",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=12", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", "-f", "mpegts", str(path)], check=True)
    return path


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, fake_video, fake_dub_video):
    """Each test gets its own data dir and the fake ani-cli."""
    import asyncio

    from app import anicli, config, db, jobs, library, recommend

    fake = HERE / "fake_ani_cli.sh"
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "anistash.db")
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "data" / "tmp")
    monkeypatch.setattr(config, "ANI_CLI", str(fake))
    monkeypatch.setattr(config, "HELPER", str(tmp_path / "no-helper"))
    monkeypatch.setenv("FAKE_VIDEO", str(fake_video))
    monkeypatch.setenv("FAKE_DUB_VIDEO", str(fake_dub_video))
    monkeypatch.delenv("FAKE_DUB_SAME", raising=False)
    monkeypatch.delenv("FAKE_NO_DUB", raising=False)
    monkeypatch.delenv("FAKE_FAIL", raising=False)
    monkeypatch.delenv("FAKE_BROKEN", raising=False)
    monkeypatch.delenv("FAKE_EPISODES", raising=False)
    anicli._cache.clear()
    library.invalidate()  # in-memory caches would carry one test's data into the next
    recommend.forget()
    monkeypatch.setattr(jobs, "_wake", asyncio.Event())  # each asyncio.run() is a new loop
    db.init()
    os.makedirs(tmp_path / "media", exist_ok=True)
    yield tmp_path
