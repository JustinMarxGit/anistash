from fastapi.testclient import TestClient

from app import main, setup


def client():
    return TestClient(main.app)


def test_setup_gate_login_and_completion(isolated):
    with client() as c:
        assert c.get("/api/queue").status_code == 409
        assert c.get("/").status_code == 200  # the SPA itself always loads
        assert c.post("/api/setup/admin", json={"username": "admin", "password": "short"}).status_code == 400
        assert c.post("/api/setup/admin", json={"username": "admin", "password": "longenough"}).status_code == 200

        # A second admin can't be created without logging in
        anon = TestClient(main.app)
        assert anon.post("/api/setup/admin", json={"username": "x", "password": "longenough"}).status_code == 401

        assert c.get("/api/queue").status_code == 409  # setup not finished
        assert c.post("/api/setup/complete").status_code == 400  # no location yet
        r = c.post("/api/locations", json={"name": "Media", "type": "local", "path": str(isolated / "media")})
        assert r.status_code == 200, r.text
        assert c.put("/api/settings", json={"quality": "720", "concurrency": 2}).json()["quality"] == "720"
        assert c.post("/api/setup/complete").json()["setup_complete"] is True
        assert c.get("/api/queue").status_code == 200

        c.post("/api/logout")
        assert c.get("/api/queue").status_code == 401
        assert c.post("/api/login", json={"username": "admin", "password": "longenough"}).status_code == 200
        assert c.get("/api/queue").status_code == 200


def test_settings_reject_bad_values(isolated):
    for bad in ({"mode": "raw"}, {"quality": "4k"}, {"concurrency": 9}, {"naming": "x"}):
        try:
            setup.save_defaults(bad)
        except setup.SetupError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_setup_cli_reset_password(isolated, monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("firstpassword\n"))
    assert setup.main(["set-admin", "--username", "admin"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("secondpassword\n"))
    assert setup.main(["reset-password"]) == 0
    from app import auth
    assert auth.login("admin", "secondpassword")
    assert not auth.login("admin", "firstpassword")
