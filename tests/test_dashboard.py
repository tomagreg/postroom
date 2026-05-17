"""Tests Phase 4 — dashboard Flask (aucun réseau requis)."""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import db as db_module
import dashboard


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "test.db"
    db_module.init_db(db_path)

    # Seed data
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        INSERT INTO emails VALUES ('u1','perso1','alice@example.com','Réunion vendredi','2026-05-17','2026-05-17T10:00:00+00:00');
        INSERT INTO emails VALUES ('u2','cours','prof@efrei.fr','Résultats partiels','2026-05-17','2026-05-17T10:01:00+00:00');
        INSERT INTO emails VALUES ('u3','voile','noreply@strava.com','Ton récap Strava','2026-05-17','2026-05-17T10:02:00+00:00');

        INSERT INTO decisions (email_uid, action, score, rule_id, reason, decided_at)
        VALUES
          ('u1','keep',3,'llm','Réunion personnelle','2026-05-17T10:00:00+00:00'),
          ('u2','keep',3,'whitelist','Domaine protégé','2026-05-17T10:01:00+00:00'),
          ('u3','delete',1,'daily_digests','Résumé quotidien','2026-05-17T10:02:00+00:00');

        INSERT INTO rule_hits (rule_id, email_uid, hit_at)
        VALUES ('daily_digests','u3','2026-05-17T10:02:00+00:00');

        INSERT INTO reply_queue (email_uid, summary, status, added_at, updated_at)
        VALUES ('u1','Répondre à Alice pour la réunion','pending','2026-05-17T10:00:00+00:00','2026-05-17T10:00:00+00:00');
    """)
    conn.commit()
    conn.close()

    dashboard.DB_PATH = db_path
    dashboard.app.config["TESTING"] = True
    with dashboard.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# /log
# ---------------------------------------------------------------------------

class TestLog:
    def test_returns_200(self, client):
        r = client.get("/log")
        assert r.status_code == 200

    def test_shows_all_actions(self, client):
        body = client.get("/log").data.decode()
        assert "keep" in body
        assert "delete" in body

    def test_shows_sender(self, client):
        body = client.get("/log").data.decode()
        assert "alice@example.com" in body

    def test_shows_rule_id(self, client):
        body = client.get("/log").data.decode()
        assert "daily_digests" in body


# ---------------------------------------------------------------------------
# /inbox
# ---------------------------------------------------------------------------

class TestInbox:
    def test_returns_200(self, client):
        r = client.get("/inbox")
        assert r.status_code == 200

    def test_shows_pending_item(self, client):
        body = client.get("/inbox").data.decode()
        assert "Alice" in body or "alice@example.com" in body

    def test_done_removes_from_inbox(self, client):
        # Récupérer l'id de l'item
        r = client.get("/inbox")
        body = r.data.decode()
        # L'item est présent avant
        assert "Répondre à Alice" in body

        # Simuler le POST /inbox/1/done
        r2 = client.post("/inbox/1/done")
        assert r2.status_code in (200, 302)

        body2 = client.get("/inbox").data.decode()
        assert "Répondre à Alice" not in body2

    def test_snooze_hides_item(self, client):
        r = client.post("/inbox/1/snooze", data={"until": "2099-12-31"})
        assert r.status_code in (200, 302)
        body = client.get("/inbox").data.decode()
        assert "Répondre à Alice" not in body

    def test_snooze_without_date_returns_400(self, client):
        r = client.post("/inbox/1/snooze", data={})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_returns_200(self, client):
        r = client.get("/stats")
        assert r.status_code == 200

    def test_shows_totals(self, client):
        body = client.get("/stats").data.decode()
        assert "3" in body  # total emails

    def test_shows_top_rules(self, client):
        body = client.get("/stats").data.decode()
        assert "daily_digests" in body


# ---------------------------------------------------------------------------
# Redirect
# ---------------------------------------------------------------------------

def test_root_redirects_to_log(client):
    r = client.get("/")
    assert r.status_code == 302
    assert "/log" in r.headers["Location"]
