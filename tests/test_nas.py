"""
NAS-Tests gegen einen echten SMB-Server (z. B. lokaler Samba).
Aktivieren mit:  HOMIFY_TEST_SMB="host,freigabe,benutzer,passwort"
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

SMB = os.environ.get("HOMIFY_TEST_SMB", "")
pytestmark = pytest.mark.skipif(not SMB, reason="Kein Test-NAS (HOMIFY_TEST_SMB) gesetzt")


def _nas_values(folder="HomifyTest"):
    host, share, user, password = SMB.split(",")
    return {"storage_mode": "nas", "nas_host": host, "nas_share": share, "nas_folder": folder,
            "nas_user": user, "nas_password": password}


@pytest.fixture(scope="module")
def admin(scanned):
    from homify import auth
    from homify.server import app

    if not auth.authenticate("nasadmin", "nasadmin1"):
        auth.create_user("nasadmin", "nasadmin1", is_admin=True)
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": "nasadmin", "password": "nasadmin1"}).status_code == 200
    return client


def _wait(predicate, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def test_smb_storage_basics(tmp_path):
    from homify.storage import storage_from_settings

    st = storage_from_settings(_nas_values("HomifyBasics"))
    st.ensure_base()
    ok, msg = st.available()
    assert ok, msg
    assert st.check_writable()[0]
    src = tmp_path / "x.bin"
    src.write_bytes(b"0123456789" * 1000)
    st.put(str(src), "A/B/x.bin")
    assert st.stat("A/B/x.bin")[0] == 10000
    assert ("A/B/x.bin", 10000) in [(r, s) for r, s, _ in st.walk()]
    with st.open("A/B/x.bin") as fh:
        fh.seek(5000)
        assert fh.read(10) == b"0123456789"
    st.remove("A/B/x.bin")
    assert st.stat("A/B/x.bin") is None


def test_wrong_password_is_explained():
    from homify.storage import storage_from_settings

    values = _nas_values()
    values["nas_password"] = "falsch"
    ok, msg = storage_from_settings(values).available()
    assert not ok
    assert "Passwort" in msg or "Anmeldung" in msg


def test_move_library_to_nas_and_stream(admin, music_dir):
    from homify import db
    from homify.config import config
    from homify.migrate import migration
    from homify.scanner import scanner

    original = {k: config.get(k) for k in ("storage_mode", "storage_path")}
    before = db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"]
    track = db.query_one("SELECT id FROM tracks WHERE title = 'One More Time'")
    admin.put(f"/api/likes/{track['id']}")

    r = admin.post("/api/storage/test", json=_nas_values())
    assert r.json()["ok"], r.json()

    # Musik aufs NAS übertragen (Kopie behalten, damit andere Tests weiterlaufen)
    r = admin.post("/api/storage/apply", json={**_nas_values(), "transfer": True, "keep_copy": True})
    assert r.status_code == 200, r.text
    assert r.json()["started"]
    assert _wait(lambda: not migration.running)
    assert migration.status["failed"] == 0, migration.status
    assert config.get("storage_mode") == "nas"
    scanner.wait(30)
    assert _wait(lambda: not scanner.status["running"])

    try:
        rows = db.query("SELECT root FROM tracks")
        assert len(rows) == before
        assert all(r["root"].startswith("smb://") for r in rows)
        # Gleiche IDs -> Like ist noch da
        likes = admin.get("/api/likes").json()
        assert track["id"] in [t["id"] for t in likes]

        # Streaming direkt vom NAS mit Range
        res = admin.get(f"/api/tracks/{track['id']}/stream", headers={"Range": "bytes=0-99"})
        assert res.status_code == 206
        assert len(res.content) == 100
        assert res.headers["content-range"].startswith("bytes 0-99/")
        # Umwandlung einer WMA-Datei vom NAS
        wma = db.query_one("SELECT id FROM tracks WHERE codec = 'wma'")
        res = admin.get(f"/api/tracks/{wma['id']}/stream", params={"transcode": 1})
        assert res.status_code == 200 and res.headers["content-type"] == "audio/mpeg"
        # Cover aus dem NAS-Ordner
        assert db.query_one("SELECT cover_id FROM tracks WHERE title = 'Aerodynamic'")["cover_id"]
        info = admin.get("/api/storage").json()["storage"]
        assert info["online"] and info["tracks"] == before
    finally:
        # zurück auf den lokalen Ordner (ohne Übertragen) und NAS-Testordner aufräumen
        r = admin.post("/api/storage/apply", json={"storage_mode": "folder", "storage_path": original["storage_path"],
                                                   "transfer": False})
        assert r.status_code == 200, r.text
        scanner.wait(30)
        _wait(lambda: not scanner.status["running"])
        from homify.storage import storage_from_settings

        st = storage_from_settings(_nas_values())
        for rel, _s, _m in list(st.walk()):
            st.remove(rel)
    assert db.query_one("SELECT COUNT(*) AS n FROM tracks WHERE root LIKE 'local:%'")["n"] == before
