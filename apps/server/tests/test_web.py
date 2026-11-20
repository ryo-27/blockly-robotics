from fastapi.testclient import TestClient
from test_executions import settings

from app.main import create_app


def test_built_ui_is_same_origin_without_shadowing_api(tmp_path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text('<html lang="ja">ロボットのアトリエ</html>')
    monkeypatch.setenv("WORKSHOP_WEB_DIST", str(web))
    with TestClient(create_app(settings(), tmp_path / "test.sqlite3")) as client:
        assert "ロボットのアトリエ" in client.get("/").text
        assert client.get("/api/health").json()["phase"] == 5
        assert client.get("/docs").status_code == 200
        assert client.get("/api/robots").status_code == 401
        assert client.get("/api/unknown").status_code == 404
        assert client.get("/config/local.json").status_code == 404


def test_api_starts_without_web_build(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_WEB_DIST", str(tmp_path / "missing"))
    with TestClient(create_app(settings(), tmp_path / "test.sqlite3")) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 404
