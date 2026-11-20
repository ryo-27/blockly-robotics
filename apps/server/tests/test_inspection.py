import sqlite3

from fastapi.testclient import TestClient
from test_executions import auth, settings

from app.inspection import database_overview
from app.main import create_app


def test_database_browser_requires_station_and_redacts_credentials(tmp_path):
    path = tmp_path / "workshop.db"
    with TestClient(create_app(settings(), path)) as client:
        assert client.get("/api/station/database").status_code == 401
        assert client.get("/api/station/database", headers=auth("a" * 40)).status_code == 403
        assert (
            client.get(
                "/api/station/database/robots", headers=auth("invalid-participant-token")
            ).status_code
            == 403
        )
        overview = client.get("/api/station/database", headers=auth()).json()
        assert overview["available"] and overview["readOnly"]
        assert {t["name"]: t["rowCount"] for t in overview["tables"]}["robots"] == 2
        rows = client.get("/api/station/database/robots?limit=1", headers=auth()).json()
        assert len(rows["rows"]) == 1 and rows["rowCount"] == 2
        assert rows["rows"][0]["token_hash"] == "（非表示）"
        assert next(c for c in rows["columns"] if c["name"] == "token_hash")["redacted"]
        with sqlite3.connect(path) as conn:
            digest = conn.execute("SELECT token_hash FROM robots ORDER BY rowid DESC").fetchone()[0]
        assert digest not in str(rows)
        assert {t["name"] for t in overview["tables"]} == {
            "robots",
            "executions",
            "robot_events",
            "sqlite_sequence",
        }
        assert (
            client.get("/api/station/database/participant_sessions", headers=auth()).status_code
            == 404
        )


def test_inspection_paginates_and_never_writes_or_accepts_sql(tmp_path):
    path = tmp_path / "workshop.db"
    with TestClient(create_app(settings(), path)) as client:
        one = client.get("/api/station/database/robots?limit=1&offset=0", headers=auth()).json()
        two = client.get("/api/station/database/robots?limit=1&offset=1", headers=auth()).json()
        assert one["rows"][0]["id"] != two["rows"][0]["id"]
        assert (
            client.get(
                "/api/station/database/robots%22%3BDROP%20TABLE%20robots%3B--", headers=auth()
            ).status_code
            == 404
        )
        for query in ("limit=101", "offset=-1", "limit=0"):
            assert (
                client.get("/api/station/database/robots?" + query, headers=auth()).status_code
                == 422
            )
        assert client.post(
            "/api/station/database/robots", headers=auth(), json={"sql": "DELETE FROM robots"}
        ).status_code in (404, 405)
        assert client.get("/api/station/database/robots", headers=auth()).json()["rowCount"] == 2


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    assert database_overview(path)["available"] is False
    assert not path.exists()
