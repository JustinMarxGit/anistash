import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import db, jobs, locations, mediaserver, postprocess


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def location(isolated):
    return run(locations.create({"name": "Media", "type": "local", "path": str(isolated / "media"),
                                 "server_path": "/jf/anime"}))


def entry(**overrides):
    return {"query": "show", "index": 2, "title": "Show Season 2", "season": 2, "offset": 0,
            "episodes": ["1", "2"], **overrides}


def test_enqueue_skips_duplicates_and_existing(location, isolated):
    first = jobs.enqueue("Show", location["id"], "sub", "best", [entry()])
    assert first["added"] == 2
    again = jobs.enqueue("Show", location["id"], "sub", "best", [entry()])
    assert again == {"added": 0, "skipped_queued": 2, "skipped_existing": 0, "batches": []}

    season = isolated / "media" / "Show" / "Season 03"
    season.mkdir(parents=True)
    (season / "Show - S03E01.mp4").write_text("x")
    result = jobs.enqueue("Show", location["id"], "sub", "best", [entry(season=3, title="Other")])
    assert result["added"] == 1 and result["skipped_existing"] == 1


def test_enqueue_validates(location):
    with pytest.raises(jobs.QueueError):
        jobs.enqueue(" ", location["id"], "sub", "best", [entry()])
    with pytest.raises(jobs.QueueError):
        jobs.enqueue("Show", location["id"], "raw", "best", [entry()])
    with pytest.raises(locations.LocationError):
        jobs.enqueue("Show", 999, "sub", "best", [entry()])


def test_worker_downloads_renames_and_notifies(location, isolated, monkeypatch):
    notified = []
    monkeypatch.setattr(mediaserver, "notify", lambda paths, loc, batch: notified.append(paths))
    db.set_settings(setup_complete=True, media_refresh="season")
    jobs.enqueue("Show", location["id"], "sub", "best", [entry(season=3, offset=10)])

    async def drive():
        worker = asyncio.create_task(jobs.worker())
        for _ in range(100):
            await asyncio.sleep(0.1)
            if all(i["status"] == "done" for b in jobs.snapshot() for i in b["items"]):
                break
        worker.cancel()

    run(drive())
    folder = isolated / "media" / "Show" / "Season 03"
    assert sorted(os.listdir(folder)) == ["Show - S03E11.en.srt", "Show - S03E11.mp4",
                                          "Show - S03E12.en.srt", "Show - S03E12.mp4"]
    info = postprocess.probe(str(folder / "Show - S03E11.mp4"))
    assert info["video"] == "h264" and info["audio"] == "aac"
    with open(folder / "Show - S03E11.mp4", "rb") as f:
        assert f.read(12)[4:8] == b"ftyp"  # a real MP4 now, not MPEG-TS in an .mp4 name
    assert "Hello" in (folder / "Show - S03E11.en.srt").read_text()
    assert {i["quality"] for b in jobs.snapshot() for i in b["items"]} == {"1080p"}
    assert notified == [[str(folder)]]  # once, when the season finished
    assert not os.listdir(isolated / "data" / "tmp")


def test_cancel_retry_and_move(location):
    jobs.enqueue("A", location["id"], "sub", "best", [entry(episodes=["1", "2", "3"])])
    jobs.enqueue("B", location["id"], "sub", "best", [entry(title="B", episodes=["1"])])
    batches = jobs.snapshot()
    first_item = batches[0]["items"][0]["id"]
    jobs.cancel_item(first_item)
    assert jobs.snapshot()[0]["items"][0]["status"] == "cancelled"
    jobs.retry_item(first_item)
    assert jobs.snapshot()[0]["items"][0]["status"] == "queued"

    jobs.move("batches", batches[1]["id"], "up")
    assert [b["series_name"] for b in jobs.snapshot()] == ["B", "A"]
    jobs.move("items", batches[1]["items"][0]["id"], "up")  # only item in its batch: no-op
    jobs.move("items", first_item, "down")
    assert [i["episode"] for i in jobs.snapshot()[1]["items"]] == ["2", "1", "3"]


def test_location_in_use_cannot_be_removed(location):
    jobs.enqueue("Show", location["id"], "sub", "best", [entry()])
    with pytest.raises(locations.LocationError, match="queued"):
        run(locations.remove(location["id"]))


def test_network_share_needs_helper():
    with pytest.raises(locations.LocationError, match="LXC"):
        run(locations.create({"name": "NAS", "type": "smb", "server": "nas", "share": "media"}))


def test_jellyfin_targeted_scan_and_debounce(location, monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            received.append((self.path, self.headers["Authorization"], json.loads(body) if body else None))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    db.set_settings(media_server="jellyfin", media_url=f"http://127.0.0.1:{server.server_port}", media_token="KEY")
    monkeypatch.setattr(mediaserver, "DEBOUNCE", 0.2)
    loc = locations.get(location["id"])
    media = location["path"]

    async def scenario():
        mediaserver.notify([f"{media}/Show/Season 01"], loc, 1)
        mediaserver.notify([f"{media}/Show/Season 02"], loc, 2)
        await asyncio.sleep(0.6)

    run(scenario())
    server.shutdown()
    assert len(received) == 1
    path, auth, body = received[0]
    assert path == "/Library/Media/Updated"
    assert auth == 'MediaBrowser Token="KEY"'
    assert [u["Path"] for u in body["Updates"]] == ["/jf/anime/Show/Season 01", "/jf/anime/Show/Season 02"]


def _jellyfin_server(received):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps([
                {"Name": "Movies", "ItemId": "m1", "CollectionType": "movies", "Locations": ["/jf/movies"]},
                {"Name": "Anime", "ItemId": "a1", "CollectionType": "tvshows", "Locations": ["/jf/anime"]},
            ]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            received.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_unmapped_location_scans_only_the_anime_library(isolated, monkeypatch):
    received = []
    server = _jellyfin_server(received)
    loc = run(locations.create({"name": "Media", "type": "local", "path": str(isolated / "media")}))
    db.set_settings(media_server="jellyfin", media_url=f"http://127.0.0.1:{server.server_port}", media_token="KEY",
                    media_library_id="a1", media_library_name="Anime")
    monkeypatch.setattr(mediaserver, "DEBOUNCE", 0)
    assert [l["name"] for l in run(mediaserver.libraries())] == ["Movies", "Anime"]

    async def scenario():
        mediaserver.notify([loc["path"] + "/Show/Season 01"], locations.get(loc["id"]), 1)
        await asyncio.sleep(0.4)

    run(scenario())
    server.shutdown()
    assert len(received) == 1
    assert received[0].startswith("/Items/a1/Refresh?Recursive=true")
    assert not any(p.startswith("/Library/Refresh") for p in received)


def test_unmapped_location_without_saved_library_picks_anime(isolated, monkeypatch):
    received = []
    server = _jellyfin_server(received)
    loc = run(locations.create({"name": "Media", "type": "local", "path": str(isolated / "media")}))
    db.set_settings(media_server="jellyfin", media_url=f"http://127.0.0.1:{server.server_port}", media_token="KEY")
    jobs.enqueue("Show", loc["id"], "sub", "best", [entry()])
    batch_id = jobs.snapshot()[0]["id"]
    monkeypatch.setattr(mediaserver, "DEBOUNCE", 0)

    async def scenario():
        mediaserver.notify([loc["path"] + "/Show"], locations.get(loc["id"]), batch_id)
        await asyncio.sleep(0.4)

    run(scenario())
    server.shutdown()
    assert received[-1].startswith("/Items/a1/Refresh")  # the Anime library, chosen automatically
    assert jobs.snapshot()[0]["warning"] == ""


def test_jellyfin_failure_becomes_batch_warning(location, monkeypatch):
    db.set_settings(media_server="jellyfin", media_url="http://127.0.0.1:9", media_token="KEY")
    jobs.enqueue("Show", location["id"], "sub", "best", [entry()])
    batch_id = jobs.snapshot()[0]["id"]
    monkeypatch.setattr(mediaserver, "DEBOUNCE", 0)

    async def scenario():
        mediaserver.notify([location["path"] + "/Show"], locations.get(location["id"]), batch_id)
        await asyncio.sleep(0.5)

    run(scenario())
    assert "Jellyfin scan failed" in jobs.snapshot()[0]["warning"]


def _drive_worker(timeout=15):
    async def drive():
        worker = asyncio.create_task(jobs.worker())
        for _ in range(timeout * 10):
            await asyncio.sleep(0.1)
            if not any(i["status"] in ("queued", "running") for b in jobs.snapshot() for i in b["items"]):
                break
        worker.cancel()
    run(drive())


def test_quality_falls_back_when_download_fails_or_is_broken(location, isolated, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "1080")
    monkeypatch.setenv("FAKE_BROKEN", "720")
    db.set_settings(setup_complete=True, media_refresh="off")
    jobs.enqueue("Show", location["id"], "sub", "best", [entry(episodes=["1"])])
    _drive_worker()
    [item] = jobs.snapshot()[0]["items"]
    assert item["status"] == "done", item["error"]
    assert item["quality"] == "360p"
    assert os.path.exists(isolated / "media" / "Show" / "Season 02" / "Show - S02E01.mp4")


def test_all_qualities_failing_reports_each(location, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "1080 720 360")
    db.set_settings(setup_complete=True, media_refresh="off")
    jobs.enqueue("Show", location["id"], "sub", "best", [entry(episodes=["1"])])
    _drive_worker()
    [item] = jobs.snapshot()[0]["items"]
    assert item["status"] == "failed"
    assert "1080p failed" in item["error"] and "360p failed" in item["error"]


def _plex_server(received):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get("X-Plex-Token")))
            if self.path.startswith("/library/sections?") or self.path == "/library/sections":
                body = {"MediaContainer": {"Directory": [
                    {"key": "1", "title": "Movies", "type": "movie", "Location": [{"path": "/data/movies"}]},
                    {"key": "4", "title": "Anime", "type": "show", "Location": [{"path": "/data/anime"}]}]}}
            elif self.path == "/":
                body = {"MediaContainer": {"friendlyName": "plexy", "version": "1.41"}}
            else:
                body = {}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_plex_folder_scan_and_library_scan(isolated, monkeypatch):
    received = []
    server = _plex_server(received)
    url = f"http://127.0.0.1:{server.server_port}"
    assert run(mediaserver.test("plex", url, "TOK")) == {"name": "plexy", "version": "1.41", "kind": "Plex"}
    libs = run(mediaserver.libraries("plex", url, "TOK"))
    assert [(l["id"], l["name"], l["paths"]) for l in libs][1] == ("4", "Anime", ["/data/anime"])

    mapped = run(locations.create({"name": "M", "type": "local", "path": str(isolated / "media"),
                                   "server_path": "/data/anime"}))
    plain = run(locations.create({"name": "P", "type": "local", "path": str(isolated / "media" / "p")}))
    db.set_settings(media_server="plex", media_url=url, media_token="TOK", media_library_id="4")
    monkeypatch.setattr(mediaserver, "DEBOUNCE", 0)
    received.clear()

    async def scenario(paths, loc):
        mediaserver.notify(paths, locations.get(loc["id"]), 1)
        await asyncio.sleep(0.4)

    run(scenario([mapped["path"] + "/Show (2013)/Season 01"], mapped))
    assert received == [("/library/sections/4/refresh?path=%2Fdata%2Fanime%2FShow+%282013%29%2FSeason+01", "TOK")]
    received.clear()
    run(scenario([plain["path"] + "/Show/Season 01"], plain))
    assert received == [("/library/sections/4/refresh", "TOK")]
    server.shutdown()


def test_plex_without_saved_library_picks_anime(location, monkeypatch):
    received = []
    server = _plex_server(received)
    db.set_settings(media_server="plex", media_url=f"http://127.0.0.1:{server.server_port}", media_token="TOK")
    jobs.enqueue("Show", location["id"], "sub", "best", [entry()])
    batch_id = jobs.snapshot()[0]["id"]
    monkeypatch.setattr(mediaserver, "DEBOUNCE", 0)

    async def scenario():
        mediaserver.notify([location["path"] + "/Show"], locations.get(location["id"]), batch_id)
        await asyncio.sleep(0.4)

    run(scenario())
    server.shutdown()
    # Plex scans the folder through the library it picked by itself
    assert ("/library/sections/4/refresh?path=%2Fjf%2Fanime%2FShow", "TOK") in received
    assert db.get_setting("media_library_id") == "4"


def test_ambiguous_libraries_still_scan_nothing(isolated, monkeypatch):
    # No path mapping, so this needs a library refresh, and there's no clear library to pick
    location = run(locations.create({"name": "Plain", "type": "local", "path": str(isolated / "media" / "plain")}))
    async def two_tv_libraries(*args):
        return [{"id": "1", "name": "TV", "type": "tvshows", "paths": []},
                {"id": "2", "name": "Cartoons", "type": "tvshows", "paths": []}]
    scans = []

    async def scan(*args):
        scans.append(args)
    monkeypatch.setattr(mediaserver, "libraries", two_tv_libraries)
    monkeypatch.setattr(mediaserver, "_scan", scan)
    db.set_settings(media_server="jellyfin", media_url="http://jf", media_token="KEY")
    jobs.enqueue("Show", location["id"], "sub", "best", [entry()])
    batch_id = jobs.snapshot()[0]["id"]
    monkeypatch.setattr(mediaserver, "DEBOUNCE", 0)

    async def scenario():
        mediaserver.notify([location["path"] + "/Show"], locations.get(location["id"]), batch_id)
        await asyncio.sleep(0.3)

    run(scenario())
    assert scans == []
    assert "more than one it could be" in jobs.snapshot()[0]["warning"]


@pytest.mark.parametrize("text, expected", [
    ("//10.10.1.3/datastore/jellyfin/anime",
     {"type": "smb", "server": "10.10.1.3", "share": "datastore", "subpath": "jellyfin/anime"}),
    ("\\\\nas\\media\\anime", {"type": "smb", "server": "nas", "share": "media", "subpath": "anime"}),
    ("smb://nas.lan/media/", {"type": "smb", "server": "nas.lan", "share": "media", "subpath": ""}),
    ("10.10.1.3:/mnt/pool/anime/", {"type": "nfs", "server": "10.10.1.3", "export": "/mnt/pool/anime"}),
    ("nfs://nas/export", {"type": "nfs", "server": "nas", "export": "/export"}),
])
def test_parse_address(text, expected):
    assert locations.parse_address(text) == expected


@pytest.mark.parametrize("text", ["//nas", "//nas/", "media/anime", "//nas/share/../etc"])
def test_parse_address_rejects(text):
    with pytest.raises(locations.LocationError):
        locations.parse_address(text)


def test_share_box_path_is_split_into_share_and_folder():
    # What was typed in v0.1.0's Share box: server 10.10.1.3, share "/datastore/jellyfin/anime"
    _, _, cfg, _, _ = locations._clean({"name": "x", "type": "smb", "server": "10.10.1.3",
                                         "share": "/datastore/jellyfin/anime"})
    assert (cfg["share"], cfg["subpath"]) == ("datastore", "jellyfin/anime")


def test_address_type_mismatch_is_explained():
    with pytest.raises(locations.LocationError, match="NFS"):
        locations._clean({"name": "x", "type": "smb", "address": "10.10.1.3:/mnt/anime"})


def _helper_module():
    import importlib.machinery
    import importlib.util
    from pathlib import Path
    path = str(Path(__file__).parent.parent / "helper" / "anistash-helper")
    loader = importlib.machinery.SourceFileLoader("anistash_helper", path)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("anistash_helper", loader))
    loader.exec_module(module)
    return module


def test_share_address_with_encoded_space():
    assert locations.parse_address("//10.10.1.3/Data%20Store/jellyfin/anime") == {
        "type": "smb", "server": "10.10.1.3", "share": "Data Store", "subpath": "jellyfin/anime"}


@pytest.mark.parametrize("name", ["Data Store", "media", "anime$", "Café-Media_2", "My.Share (old)"])
def test_helper_accepts_real_share_names(name):
    assert _helper_module().check_share(name) == name


@pytest.mark.parametrize("name, shown", [("data/store", "/"), ("a:b", ":"), ("x*?", "* ?"), ("", None)])
def test_helper_rejects_names_smb_forbids(name, shown):
    helper = _helper_module()
    with pytest.raises(helper.Invalid) as err:
        helper.check_share(name)
    if shown:
        assert all(c in str(err.value) for c in shown.split())


SMB_LS = {
    # one smbclient session per share; several ls commands print one block each
    ("//10.10.1.3/Data Store", 'ls "*"'):
        "  .                                   D        0  Sat Nov 22 10:00:00 2026\n"
        "  ..                                  D        0  Sat Nov 22 10:00:00 2026\n"
        "  jellyfin                            D        0  Sat Nov  2 10:00:00 2026\n"
        "  backups                             D        0  Sat Nov  2 10:00:00 2026\n"
        "  notes.txt                           A      120  Sat Nov  2 10:00:00 2026\n"
        "\n\t\t3907018584 blocks of size 1024. 1000 blocks available\n",
    ("//10.10.1.3/Data Store", 'ls "jellyfin\\*"; ls "backups\\*"'):
        "  movies                              D        0  Sat Nov  2 10:00:00 2026\n"
        "  anime                               D        0  Sat Nov  2 10:00:00 2026\n"
        "  Anime Movies                        D        0  Sat Nov  2 10:00:00 2026\n"
        "\n\t\t3907018584 blocks of size 1024. 1000 blocks available\n"
        "  2025                                D        0  Sat Nov  2 10:00:00 2026\n"
        "\n\t\t3907018584 blocks of size 1024. 1000 blocks available\n",
    ("//10.10.1.3/photos", 'ls "*"'):
        "  2024                                D        0  Sat Nov  2 10:00:00 2026\n"
        "\n\t\t3907018584 blocks of size 1024. 1000 blocks available\n",
    ("//10.10.1.3/photos", 'ls "2024\\*"'):
        "\n\t\t3907018584 blocks of size 1024. 1000 blocks available\n",
}


def test_helper_finds_anime_folder(monkeypatch):
    import subprocess
    helper = _helper_module()

    def fake_smbclient(spec, target, flag, commands, timeout=30):
        return subprocess.CompletedProcess([], 0, SMB_LS[(target, commands)], "")

    monkeypatch.setattr(helper, "_smbclient", fake_smbclient)
    found = helper._find_anime({}, "10.10.1.3", ["Data Store", "photos"])
    assert found == [{"share": "Data Store", "path": ["jellyfin", "anime"]},
                     {"share": "Data Store", "path": ["jellyfin", "Anime Movies"]}]
    listing, error = helper._list_dirs({}, "10.10.1.3", "Data Store", [[]])
    assert listing == [["jellyfin", "backups"]] and error is None


@pytest.mark.parametrize("parts", [["a;b"], ['say "hi"'], [".."], ["x/y"]])
def test_helper_rejects_unsafe_folder_names(parts):
    helper = _helper_module()
    with pytest.raises(helper.Invalid):
        helper._smb_path(parts)


def test_mount_check_uses_mount_point_not_subfolder(isolated, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MOUNT_ROOT", isolated / "mnt")
    checked = []
    monkeypatch.setattr(os.path, "ismount", lambda p: checked.append(str(p)) or True)
    row = {"id": 4, "type": "smb", "path": str(isolated / "mnt" / "loc4" / "jellyfin" / "anime")}
    assert locations.is_mounted(row)
    assert checked == [str(isolated / "mnt" / "loc4")]


def test_requested_quality_is_tried_first(location, isolated, monkeypatch):
    # Regression: items.quality (what was downloaded) shadowed batches.quality (what was asked for)
    db.set_settings(setup_complete=True, media_refresh="off")
    jobs.enqueue("Show", location["id"], "sub", "720", [entry(episodes=["1"])])
    _drive_worker()
    [item] = jobs.snapshot()[0]["items"]
    assert item["status"] == "done" and item["quality"] == "720p"


def test_scan_picks_the_anime_library_when_none_was_saved(isolated, monkeypatch):
    received = []
    server = _jellyfin_server(received)  # libraries: Movies (movies), Anime (tvshows)
    loc = run(locations.create({"name": "Media", "type": "local", "path": str(isolated / "media")}))
    db.set_settings(media_server="jellyfin", media_url=f"http://127.0.0.1:{server.server_port}", media_token="KEY")
    monkeypatch.setattr(mediaserver, "DEBOUNCE", 0)

    async def scenario():
        mediaserver.notify([loc["path"] + "/Show/Season 01"], locations.get(loc["id"]), 1)
        await asyncio.sleep(0.4)

    run(scenario())
    server.shutdown()
    assert received and received[0].startswith("/Items/a1/Refresh")
    assert db.get_setting("media_library_name") == "Anime"


def test_pick_library_only_guesses_when_clear():
    libs = [{"id": "1", "name": "Movies", "type": "movies", "paths": []},
            {"id": "2", "name": "TV", "type": "tvshows", "paths": []},
            {"id": "3", "name": "Anime", "type": "tvshows", "paths": []}]
    assert mediaserver.pick_library(libs)["name"] == "Anime"
    assert mediaserver.pick_library(libs[:2])["name"] == "TV"          # the only TV library
    assert mediaserver.pick_library([libs[1], {**libs[1], "id": "4", "name": "Cartoons"}]) is None  # ambiguous


def test_rescan_after_a_failed_scan(location, monkeypatch):
    notified = []
    monkeypatch.setattr(mediaserver, "notify", lambda folders, loc, batch: notified.append(folders))
    monkeypatch.setattr(mediaserver, "configured", lambda settings=None: True)
    jobs.enqueue("Show", location["id"], "sub", "best", [entry(episodes=["1"])])
    batch = jobs.snapshot()[0]
    db.execute("UPDATE items SET status = 'done', dest = ? WHERE batch_id = ?",
               (location["path"] + "/Show/Season 02/Show - S02E01.mp4", batch["id"]))
    db.execute("UPDATE batches SET warning = 'Jellyfin scan failed: x' WHERE id = ?", (batch["id"],))
    assert jobs.rescan(batch["id"]) == 1
    assert notified == [[location["path"] + "/Show/Season 02"]]
    assert jobs.snapshot()[0]["warning"] == ""


def _audio_tracks(path):
    import json as _json
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index:stream_tags=language,handler_name:stream_disposition=default", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    return [(s["tags"].get("language"), s["tags"].get("handler_name"), s["disposition"]["default"])
            for s in _json.loads(out)["streams"]]


def test_both_gives_one_file_with_japanese_and_english_audio(location, isolated):
    db.set_settings(setup_complete=True, media_refresh="off", both_default="sub")
    jobs.enqueue("Show", location["id"], "both", "best", [entry(episodes=["1"])])
    _drive_worker(30)
    [item] = jobs.snapshot()[0]["items"]
    assert item["status"] == "done" and item["quality"] == "1080p · sub+dub", item
    video = isolated / "media" / "Show" / "Season 02" / "Show - S02E01.mp4"
    assert _audio_tracks(video) == [("jpn", "Japanese", 1), ("eng", "English", 0)]
    assert (video.parent / "Show - S02E01.en.srt").exists()


def test_both_can_play_english_first(location, isolated):
    db.set_settings(setup_complete=True, media_refresh="off", both_default="dub")
    jobs.enqueue("Show", location["id"], "both", "best", [entry(episodes=["1"])])
    _drive_worker(30)
    video = isolated / "media" / "Show" / "Season 02" / "Show - S02E01.mp4"
    assert _audio_tracks(video) == [("jpn", "Japanese", 0), ("eng", "English", 1)]


@pytest.mark.parametrize("env, expected", [("FAKE_DUB_SAME", "No English dub on the site yet"),
                                           ("FAKE_NO_DUB", "the dub couldn't be added")])
def test_both_falls_back_to_japanese_only(location, isolated, monkeypatch, env, expected):
    monkeypatch.setenv(env, "1")
    db.set_settings(setup_complete=True, media_refresh="off")
    jobs.enqueue("Show", location["id"], "both", "best", [entry(episodes=["1"])])
    _drive_worker(30)
    [item] = jobs.snapshot()[0]["items"]
    assert item["status"] == "done" and item["quality"] == "1080p · sub only"
    assert expected in item["detail"]
    video = isolated / "media" / "Show" / "Season 02" / "Show - S02E01.mp4"
    assert _audio_tracks(video) == [("jpn", "Japanese", 1)]


def test_single_mode_files_are_language_tagged(location, isolated):
    db.set_settings(setup_complete=True, media_refresh="off")
    jobs.enqueue("Show", location["id"], "dub", "best", [entry(episodes=["1"])])
    _drive_worker(30)
    video = isolated / "media" / "Show" / "Season 02" / "Show - S02E01.mp4"
    assert _audio_tracks(video)[0][:2] == ("eng", "English")
