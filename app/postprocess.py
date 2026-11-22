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


LANGUAGES = {"sub": ("jpn", "Japanese"), "dub": ("eng", "English")}


def finalize(files: list[str], workdir: str, title: str, mode: str = "sub") -> list[tuple[str, str]]:
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
    # Tag the audio language so Jellyfin/Plex show "Japanese" or "English" in their audio menu
    lang, label = LANGUAGES.get(mode, LANGUAGES["sub"])
    if _ffmpeg("-i", video, "-map", "0:v:0", "-map", "0:a?", "-c:v", "copy", *audio, "-sn",
               "-metadata:s:a:0", f"language={lang}", "-metadata:s:a:0", f"handler_name={label}",
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


def _fingerprint(path: str) -> str:
    """Hash of the first audio stream's packets, to spot a "dub" that is really the sub again."""
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-c", "copy", "-t", "120",
                             "-f", "md5", "-"], capture_output=True, text=True, timeout=600)
    return result.stdout.strip()


class SameAudio(Exception):
    pass


def merge_dual_audio(sub_video: str, dub_video: str, out: str, default: str = "sub", title: str = "") -> str:
    """One MP4 with the sub's video, Japanese audio and English audio, both labelled, so the
    player's audio menu can switch between them. Returns a note for the user, or "".

    Raises SameAudio when the "dub" is actually the same audio as the sub, and
    BrokenDownload when the two versions are too different in length to line up.
    """
    sub_info, dub_info = probe(sub_video), probe(dub_video)
    if _fingerprint(sub_video) == _fingerprint(dub_video):
        raise SameAudio("the site's dub has the same Japanese audio as the sub")
    drift = abs(sub_info["duration"] - dub_info["duration"])
    if drift > 3:
        raise BrokenDownload(f"the sub and dub differ in length by {drift:.0f}s, so their audio wouldn't line up")
    dub_audio = ["-c:a:1", "copy"] if dub_info["audio"] in MP4_AUDIO else ["-c:a:1", "aac", "-b:a:1", "192k"]
    first, second = ("0", "1") if default == "sub" else ("1", "0")
    ok = _ffmpeg(
        "-i", sub_video, "-i", dub_video,
        "-map", "0:v:0", "-map", "0:a:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a:0", "copy", *dub_audio, "-sn",
        # MP4 keeps a track's name in handler_name; players show it in the audio menu
        "-metadata:s:a:0", "language=jpn", "-metadata:s:a:0", "handler_name=Japanese",
        "-metadata:s:a:1", "language=eng", "-metadata:s:a:1", "handler_name=English",
        f"-disposition:a:{first}", "default", f"-disposition:a:{second}", "0",
        "-movflags", "+faststart", "-metadata", f"title={title}", out)
    if not ok:
        raise BrokenDownload("couldn't combine the sub and dub audio")
    probe(out)
    return ""
