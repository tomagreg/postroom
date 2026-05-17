"""Tests Phase 1 — aucun réseau requis."""
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import db
import imap_client


# ---------------------------------------------------------------------------
# db.py
# ---------------------------------------------------------------------------

def test_db_init(tmp_path):
    """init_db crée les 5 tables attendues."""
    db_path = tmp_path / "test.db"
    db.init_db(db_path)

    conn = db.get_conn(db_path)
    try:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # sqlite_sequence est créée automatiquement par SQLite pour AUTOINCREMENT
        tables.discard("sqlite_sequence")
    finally:
        conn.close()

    expected = {"emails", "decisions", "rule_hits", "reply_queue", "attachments", "promo_queue", "social_queue"}
    assert expected == tables, f"Tables manquantes : {expected - tables}"


def test_db_upsert_email(tmp_path):
    """Un même uid inséré deux fois ne crée qu'un seul enregistrement."""
    db_path = tmp_path / "test.db"

    db.init_db(db_path)

    conn = db.get_conn(db_path)
    try:
        is_new_1 = db.upsert_email(
            conn, uid="perso1:1", account="perso1",
            sender="alice@example.com", subject="Bonjour",
            date="2026-05-17", processed_at="2026-05-17T02:00:00+00:00",
        )
        conn.commit()

        is_new_2 = db.upsert_email(
            conn, uid="perso1:1", account="perso1",
            sender="alice@example.com", subject="Bonjour",
            date="2026-05-17", processed_at="2026-05-17T02:00:00+00:00",
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM emails WHERE uid='perso1:1'").fetchone()[0]
    finally:
        conn.close()

    assert is_new_1 is True
    assert is_new_2 is False
    assert count == 1


# ---------------------------------------------------------------------------
# imap_client.py
# ---------------------------------------------------------------------------

def test_load_accounts(tmp_path):
    """Avec un .env de test, retourne 4 comptes bien formés."""
    env_file = tmp_path / "accounts.env"
    env_file.write_text(
        "COURS_HOST=imap.cours.example.com\n"
        "COURS_USER=cours@example.com\n"
        "COURS_PASS=pass_cours\n"
        "VOILE_HOST=imap.voile.example.com\n"
        "VOILE_USER=voile@example.com\n"
        "VOILE_PASS=pass_voile\n"
        "PERSO1_HOST=imap.gmail.com\n"
        "PERSO1_USER=perso1@example.com\n"
        "PERSO1_PASS=pass_perso1\n"
        "PERSO2_HOST=imap.gmail.com\n"
        "PERSO2_USER=perso2@example.com\n"
        "PERSO2_PASS=pass_perso2\n"
    )

    accounts = imap_client.load_accounts(env_file)

    assert len(accounts) == 4
    names = [a["name"] for a in accounts]
    assert names == ["cours", "voile", "perso1", "perso2"]
    for a in accounts:
        assert "host" in a and a["host"]
        assert "user" in a and a["user"]
        assert "password" in a and a["password"]


def test_fetch_body_truncation():
    """fetch_body_preview tronque à max_words mots."""
    body_300_words = " ".join(f"mot{i}" for i in range(500))
    raw_email = (
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body_300_words}"
    ).encode()

    mock_conn = MagicMock()
    mock_conn.fetch.return_value = ("OK", [(None, raw_email)])

    result = imap_client.fetch_body_preview(mock_conn, uid="42", max_words=300)

    assert len(result.split()) == 300
