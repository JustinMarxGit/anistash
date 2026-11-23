"""Looks (per person, cleaned on the server, site default) and uploaded pictures."""

import asyncio
import json

from fastapi.testclient import TestClient

from app import auth, db, files, locations, look, main

HEADERS = {"X-Requested-With": "anistash"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def setup_people(isolated):
    asyncio.run(locations.create({"name": "Media", "type": "local", "path": str(isolated)}))
    db.set_settings(setup_complete=True)
    auth.create_user("aiko", "correct horse battery", "admin")
    auth.create_user("ben", "just visiting here", "member")


def login(name, pw):
    c = TestClient(main.app, headers=HEADERS)
    assert c.post("/api/login", json={"username": name, "password": pw}).status_code == 200
    return c


def test_clean_keeps_only_safe_values():
    raw = {"mode": "dark", "palette": {"combo": 126, "colors": ["#1C4286", "#ebd3a2", "red", "#e2b540", "#000000", "#111111"],
                                       "accent": 9, "tint": 1},
           "tint": 400, "font": {"heading": "url(x)", "body": "serif", "size": 5}, "corners": "round",
           "motion": {"toggle": "instant", "ambient": "petals", "celebrate": "yes"}, "evil": "<script>",
           "bg_image": {"on": True, "dim": -10, "blur": 99, "fit": "stretch"}}
    out = look.clean(raw)
    assert out["mode"] == "dark" and out["corners"] == "round"
    assert out["palette"] == {"combo": 126, "colors": ["#1c4286", "#ebd3a2", "#e2b540", "#000000"], "accent": 0, "tint": 1}
    assert out["tint"] == 100 and out["font"] == {"heading": "bricolage", "body": "serif", "size": 90}
    assert out["motion"]["toggle"] == "instant" and out["motion"]["ambient"] == "petals" and out["motion"]["celebrate"] is True
    assert out["bg_image"] == {"on": True, "dim": 0, "blur": 24, "fit": "cover"}
    assert "evil" not in out
    assert look.clean("not a look") == look.DEFAULT


def test_looks_are_per_person_with_a_site_default(isolated):
    setup_people(isolated)
    aiko, ben = login("aiko", "correct horse battery"), login("ben", "just visiting here")
    assert ben.get("/api/status").json()["look"]["own"] is False
    mine = {"mode": "dark", "palette": {"combo": 135, "colors": ["#437742", "#f5ecc2", "#97acc8"], "accent": 0, "tint": 2}}
    saved = aiko.put("/api/account/look", json={"look": mine}).json()["look"]
    assert saved["own"] and saved["palette"]["colors"][0] == "#437742"
    assert ben.get("/api/status").json()["look"]["palette"]["colors"] == []  # aiko's look is hers
    # an admin makes it the default: people without their own look, and the login page, get it
    assert ben.put("/api/look/default", json={"look": mine}).status_code == 403
    assert aiko.put("/api/look/default", json={"look": mine}).status_code == 200
    assert ben.get("/api/status").json()["look"]["palette"]["combo"] == 135
    assert TestClient(main.app, headers=HEADERS).get("/api/status").json()["look"]["mode"] == "dark"
    ben.put("/api/account/look", json={"look": {"corners": "sharp"}})
    assert ben.get("/api/status").json()["look"]["corners"] == "sharp"
    ben.delete("/api/account/look")
    assert ben.get("/api/status").json()["look"]["own"] is False


def test_old_choices_become_a_look(isolated):
    setup_people(isolated)
    db.execute("UPDATE users SET theme_mode = 'dark', accent = '#3f9b5a' WHERE username = 'ben'")
    assert look.migrate() == 1
    stored = json.loads(auth.find_user("ben")["look"])
    assert stored["mode"] == "dark" and stored["palette"]["colors"] == ["#3f9b5a"]
    assert look.migrate() == 0


def test_uploads_are_checked_and_private(isolated):
    setup_people(isolated)
    aiko, ben = login("aiko", "correct horse battery"), login("ben", "just visiting here")
    r = aiko.put("/api/account/files/background", content=PNG)
    assert r.status_code == 200 and r.json()["files"]["background"].startswith("/api/account/files/background?v=")
    assert aiko.get("/api/account/files/background").content == PNG
    assert ben.get("/api/account/files/background").status_code == 404  # her picture isn't his
    assert aiko.put("/api/account/files/logo", content=SVG).status_code == 400
    assert aiko.put("/api/account/files/logo", content=b"<html>" + PNG).status_code == 400
    assert aiko.put("/api/account/files/logo", content=PNG + b"\x00" * (1024 * 1024)).status_code == 413
    assert aiko.put("/api/account/files/script", content=PNG).status_code == 404
    # without the app's own header (a cross-site upload), it's refused
    assert TestClient(main.app).put("/api/account/files/avatar", content=PNG).status_code == 403
    aiko.put("/api/account/files/avatar", content=PNG)
    assert aiko.get("/api/account/avatar").content == PNG
    aiko.delete("/api/account/files/background")
    assert aiko.get("/api/account/files/background").status_code == 404


def test_default_look_pictures_show_on_the_login_page(isolated):
    setup_people(isolated)
    aiko = login("aiko", "correct horse battery")
    aiko.put("/api/account/files/background", content=PNG)
    aiko.put("/api/account/files/avatar", content=PNG)
    aiko.put("/api/look/default", json={"look": {"bg_image": {"on": True}}})
    anon = TestClient(main.app, headers=HEADERS)
    shown = anon.get("/api/status").json()["look"]
    assert shown["bg_image"]["on"] and shown["files"]["background"].startswith("/api/look/files/background")
    assert shown["files"]["avatar"] == ""  # nobody's avatar on the login page
    assert anon.get(shown["files"]["background"]).content == PNG


def test_deleting_a_user_removes_their_pictures(isolated):
    setup_people(isolated)
    ben_id = auth.find_user("ben")["id"]
    files.save(ben_id, "avatar", PNG)
    aiko = login("aiko", "correct horse battery")
    assert aiko.delete(f"/api/users/{ben_id}").status_code == 200
    assert files.find(ben_id, "avatar") is None
