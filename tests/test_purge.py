"""Tests Phase 5 — purge.py (aucun réseau requis)."""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import db as db_module
import purge


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def _past(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _future(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db_module.init_db(db_path)
    c = db_module.get_conn(db_path)

    c.executescript(f"""
        INSERT INTO emails VALUES ('perso1:10','perso1','spam@x.com','Promo','{_past(5)}','{_past(5)}');
        INSERT INTO emails VALUES ('perso1:11','perso1','noreply@x.com','OTP','{_past(2)}','{_past(2)}');
        INSERT INTO emails VALUES ('perso1:12','perso1','news@x.com','Newsletter','{_past(0)}','{_past(0)}');

        INSERT INTO decisions (email_uid,action,score,rule_id,reason,delay_hours,decided_at)
        VALUES
          ('perso1:10','delete',1,'daily_digests','digest',0,'{_past(5)}'),
          ('perso1:11','delete',1,'otp_codes','OTP',1,'{_past(2)}'),
          ('perso1:12','delete',1,'notifications_auto','notif',0,'{_past(0)}');
    """)
    c.commit()
    yield c
    c.close()


# ---------------------------------------------------------------------------
# get_pending_purges
# ---------------------------------------------------------------------------

class TestGetPendingPurges:
    def test_returns_elapsed_decisions(self, conn):
        rows = purge.get_pending_purges(conn, "perso1")
        uids = [r["email_uid"] for r in rows]
        assert "perso1:10" in uids  # delay=0, decided 5h ago → ok
        assert "perso1:12" in uids  # delay=0, decided now → ok

    def test_excludes_not_elapsed(self, conn):
        # OTP : delay=1h, decided 2h ago → elapsed → included
        # Si delay=48h décidé il y a 5h → pas encore écoulé
        conn.execute("""
            INSERT INTO emails VALUES ('perso1:13','perso1','t@x.com','Track','2026-01-01','2026-01-01')
        """)
        conn.execute(f"""
            INSERT INTO decisions (email_uid,action,score,rule_id,reason,delay_hours,decided_at)
            VALUES ('perso1:13','delete',1,'tracking_delivered','track',48,'{_past(5)}')
        """)
        conn.commit()
        rows = purge.get_pending_purges(conn, "perso1")
        uids = [r["email_uid"] for r in rows]
        assert "perso1:13" not in uids

    def test_excludes_already_purged(self, conn):
        conn.execute(
            "UPDATE decisions SET purged_at='2026-01-01T00:00:00+00:00' WHERE email_uid='perso1:10'"
        )
        conn.commit()
        rows = purge.get_pending_purges(conn, "perso1")
        uids = [r["email_uid"] for r in rows]
        assert "perso1:10" not in uids

    def test_filters_by_account(self, conn):
        rows = purge.get_pending_purges(conn, "perso2")
        assert rows == []


# ---------------------------------------------------------------------------
# flag_deleted
# ---------------------------------------------------------------------------

class TestFlagDeleted:
    def test_returns_true_on_ok(self):
        mock_imap = MagicMock()
        mock_imap.uid.return_value = ("OK", [b"1 (FLAGS (\\Deleted))"])
        assert purge.flag_deleted(mock_imap, "10") is True
        mock_imap.uid.assert_called_once_with("STORE", "10", "+FLAGS", r"(\Deleted)")

    def test_returns_false_on_no(self):
        mock_imap = MagicMock()
        mock_imap.uid.return_value = ("NO", [b"failed"])
        assert purge.flag_deleted(mock_imap, "10") is False

    def test_returns_false_on_exception(self):
        mock_imap = MagicMock()
        mock_imap.uid.side_effect = OSError("connection lost")
        assert purge.flag_deleted(mock_imap, "10") is False


# ---------------------------------------------------------------------------
# run_purge — dry-run
# ---------------------------------------------------------------------------

class TestRunPurgeDryRun:
    def test_dry_run_does_not_touch_imap(self, conn):
        account = {"name": "perso1", "host": "imap.example.com",
                   "user": "u", "password": "p"}
        with patch("imap_client.connect") as mock_connect:
            purge.run_purge(account, conn, dry_run=True)
            mock_connect.assert_not_called()

    def test_dry_run_returns_pending_count(self, conn):
        account = {"name": "perso1", "host": "x", "user": "u", "password": "p"}
        c = purge.run_purge(account, conn, dry_run=True)
        assert c["pending"] >= 2
        assert c["flagged"] == 0

    def test_dry_run_does_not_set_purged_at(self, conn):
        account = {"name": "perso1", "host": "x", "user": "u", "password": "p"}
        purge.run_purge(account, conn, dry_run=True)
        row = conn.execute(
            "SELECT purged_at FROM decisions WHERE email_uid='perso1:10'"
        ).fetchone()
        assert row["purged_at"] is None


# ---------------------------------------------------------------------------
# run_purge — live (IMAP mocké)
# ---------------------------------------------------------------------------

class TestRunPurgeLive:
    def test_flags_deleted_and_marks_purged(self, conn):
        account = {"name": "perso1", "host": "x", "user": "u", "password": "p"}
        mock_imap = MagicMock()
        mock_imap.uid.return_value = ("OK", [b"flagged"])

        with patch("imap_client.connect", return_value=mock_imap), \
             patch("imap_client.close"):
            c = purge.run_purge(account, conn, dry_run=False)

        assert c["flagged"] >= 2
        assert c["errors"] == 0
        row = conn.execute(
            "SELECT purged_at FROM decisions WHERE email_uid='perso1:10'"
        ).fetchone()
        assert row["purged_at"] is not None

    def test_imap_error_counts_as_error(self, conn):
        account = {"name": "perso1", "host": "x", "user": "u", "password": "p"}
        with patch("imap_client.connect", side_effect=ConnectionError("refused")):
            c = purge.run_purge(account, conn, dry_run=False)
        assert c["errors"] > 0
