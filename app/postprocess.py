"""Turns what ani-cli downloaded into files Jellyfin and Plex both play directly.

- The video is checked with ffprobe; a file with no video stream or a near-zero
  duration counts as a failed download, so the caller can try a lower quality.
- It is remuxed (no re-encode) into a clean MP4 with +faststart. HLS downloads are
  MPEG-TS segments in an MP4 wrapper, which some clients choke on; the remux also
  moves the index to the front so playback starts without reading the whole file.
  Audio that MP4 players don't all support is converted to AAC.
- WebVTT subtitles become SubRip (.en.srt), the sidecar format both servers handle best.
"""

import json
import os
import subprocess

MIN_DURATION = 10  # seconds; anything shorter is a broken download
MP4_AUDIO = {"aac", "mp3", "ac3", "eac3"}
MP4_VIDEO = {"h264", "hevc", "av1"}


class BrokenDownload(Exception):
    pass


def probe(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name", "-of", "json", path],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise BrokenDownload(f"ffprobe can't read the file: {result.stderr.strip()[:200]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    info = {
        "video": next((s["codec_name"] for s in streams if s.get("codec_type") == "video"), None),
        "audio": next((s["codec_name"] for s in streams if s.get("codec_type") == "audio"), None),
        "duration": float((data.get("format") or {}).get("duration") or 0),
    }
    if not info["video"]:
        raise BrokenDownload("the file has no video stream")
    if info["duration"] < MIN_DURATION:
        raise BrokenDownload(f"the file is only {info['duration']:.0f}s long")
    return info


def _ffmpeg(*args: str) -> bool:
    return subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, timeout=3600).returncode == 0


def finalize(files: list[str], workdir: str, title: str) -> list[tuple[str, str]]:
    """Validates and converts the downloaded files inside workdir.

    Returns [(path, suffix)] where suffix is what follows the episode's base name
    (".mp4", ".en.srt"). Raises BrokenDownload if the video is unusable.
    """
    video = next((f for f in files if f.endswith(".mp4")), None)
    if not video:
        raise BrokenDownload("no video file was produced")
    info = probe(video)
    if info["video"] not in MP4_VIDEO:
        raise BrokenDownload(f"unexpected video codec {info['video']}")

    out = []
    final_video = os.path.join(workdir, "final.mp4")
    audio = ["-c:a", "copy"] if info["audio"] in MP4_AUDIO or not info["audio"] else ["-c:a", "aac", "-b:a", "192k"]
    if _ffmpeg("-i", video, "-map", "0:v:0", "-map", "0:a?", "-c:v", "copy", *audio, "-sn",
               "-movflags", "+faststart", "-metadata", f"title={title}", final_video):
        probe(final_video)
        os.remove(video)
        out.append((final_video, ".mp4"))
    else:
        out.append((video, ".mp4"))  # the original already played in ffprobe, so keep it

    for sub in (f for f in files if f.endswith(".vtt") and os.path.getsize(f) > 0):
        srt = os.path.join(workdir, "final.en.srt")
        if _ffmpeg("-i", sub, "-f", "srt", srt) and os.path.getsize(srt) > 0:
            out.append((srt, ".en.srt"))
        else:
            out.append((sub, ".en.vtt"))
    return out
