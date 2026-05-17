"""Tests Phase 3 — llm_agent (aucun réseau requis, SDK mocké)."""
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import llm_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client(score: int, category: str = "test", confidence: float = 0.9,
                 action_required: bool = False, reply_suggested: str | None = None):
    """Retourne un client Anthropic mocké qui répond avec le JSON donné."""
    payload = {
        "score": score,
        "category": category,
        "reason": f"Test reason for score {score}",
        "confidence": confidence,
        "action_required": action_required,
        "reply_suggested": reply_suggested,
    }
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(payload))]

    client = MagicMock()
    client.messages.create.return_value = msg
    return client


def make_email(sender="someone@example.com", subject="Test", body="Hello"):
    return {"from": sender, "subject": subject, "body_preview": body, "uid": "1"}


# ---------------------------------------------------------------------------
# classify — parsing et appel SDK
# ---------------------------------------------------------------------------

class TestClassify:
    def test_returns_score_and_action_required(self):
        client = _mock_client(score=3, confidence=0.95)
        result = llm_agent.classify(make_email(), "perso1", client)
        assert result["score"] == 3
        assert result["action_required"] is False

    def test_uses_fast_model_by_default(self):
        client = _mock_client(score=1)
        llm_agent.classify(make_email(), "perso1", client)
        call_kwargs = client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == llm_agent.MODEL_FAST

    def test_uses_specified_model(self):
        client = _mock_client(score=1)
        llm_agent.classify(make_email(), "perso1", client, model=llm_agent.MODEL_SMART)
        call_kwargs = client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == llm_agent.MODEL_SMART

    def test_system_prompt_uses_account_context(self):
        client = _mock_client(score=3)
        llm_agent.classify(make_email(), "cours", client)
        call_kwargs = client.messages.create.call_args
        system_text = call_kwargs.kwargs["system"][0]["text"]
        assert "Efrei" in system_text

    def test_system_prompt_cached(self):
        client = _mock_client(score=3)
        llm_agent.classify(make_email(), "cours", client)
        call_kwargs = client.messages.create.call_args
        cache_ctrl = call_kwargs.kwargs["system"][0]["cache_control"]
        assert cache_ctrl == {"type": "ephemeral"}

    def test_invalid_json_returns_score_2(self):
        msg = MagicMock()
        msg.content = [MagicMock(text="NOT JSON AT ALL")]
        client = MagicMock()
        client.messages.create.return_value = msg
        result = llm_agent.classify(make_email(), "perso1", client)
        assert result["score"] == 2
        assert result["confidence"] == 0.0


# ---------------------------------------------------------------------------
# classify_with_escalation
# ---------------------------------------------------------------------------

class TestEscalation:
    def test_no_escalation_when_confident(self):
        client = _mock_client(score=3, confidence=0.9)
        llm_agent.classify_with_escalation(make_email(), "perso1", client, threshold=0.75)
        assert client.messages.create.call_count == 1

    def test_escalates_on_score_2(self):
        """Le premier appel (haiku) retourne score=2 → escalade vers sonnet."""
        smart_payload = json.dumps({
            "score": 3, "category": "info", "reason": "After review, keep",
            "confidence": 0.88, "action_required": False, "reply_suggested": None,
        })
        haiku_msg = MagicMock()
        haiku_msg.content = [MagicMock(text=json.dumps({
            "score": 2, "category": "ambiguous", "reason": "unclear",
            "confidence": 0.6, "action_required": False, "reply_suggested": None,
        }))]
        sonnet_msg = MagicMock()
        sonnet_msg.content = [MagicMock(text=smart_payload)]

        client = MagicMock()
        client.messages.create.side_effect = [haiku_msg, sonnet_msg]

        result = llm_agent.classify_with_escalation(make_email(), "perso1", client)
        assert client.messages.create.call_count == 2
        # Second call must use smart model
        second_call = client.messages.create.call_args_list[1]
        assert second_call.kwargs["model"] == llm_agent.MODEL_SMART
        assert result["score"] == 3

    def test_escalates_on_low_confidence(self):
        """confidence=0.5 < threshold=0.75 → escalade."""
        low_conf_payload = json.dumps({
            "score": 1, "category": "promo", "reason": "probably promo",
            "confidence": 0.5, "action_required": False, "reply_suggested": None,
        })
        high_conf_payload = json.dumps({
            "score": 1, "category": "promo", "reason": "definitely promo",
            "confidence": 0.92, "action_required": False, "reply_suggested": None,
        })
        haiku_msg = MagicMock()
        haiku_msg.content = [MagicMock(text=low_conf_payload)]
        sonnet_msg = MagicMock()
        sonnet_msg.content = [MagicMock(text=high_conf_payload)]

        client = MagicMock()
        client.messages.create.side_effect = [haiku_msg, sonnet_msg]

        result = llm_agent.classify_with_escalation(make_email(), "perso1", client, threshold=0.75)
        assert client.messages.create.call_count == 2
        assert result["confidence"] == 0.92


# ---------------------------------------------------------------------------
# result_to_decision
# ---------------------------------------------------------------------------

class TestResultToDecision:
    def test_score_1_gives_delete(self):
        d = llm_agent.result_to_decision({"score": 1, "reason": "old promo", "confidence": 0.9,
                                           "action_required": False, "reply_suggested": None, "category": "promo"})
        assert d["action"] == "delete"
        assert d["score"] == 1
        assert d["rule_id"] == "llm"

    def test_score_3_gives_keep(self):
        d = llm_agent.result_to_decision({"score": 3, "reason": "invoice", "confidence": 0.9,
                                           "action_required": False, "reply_suggested": None, "category": "admin"})
        assert d["action"] == "keep"

    def test_score_4_gives_keep(self):
        d = llm_agent.result_to_decision({"score": 4, "reason": "needs reply", "confidence": 0.85,
                                           "action_required": True, "reply_suggested": "Please reply", "category": "request"})
        assert d["action"] == "keep"
        assert d["llm_meta"]["action_required"] is True

    def test_score_2_gives_review(self):
        d = llm_agent.result_to_decision({"score": 2, "reason": "unclear", "confidence": 0.4,
                                           "action_required": False, "reply_suggested": None, "category": "ambiguous"})
        assert d["action"] == "review"


# ---------------------------------------------------------------------------
# add_to_reply_queue
# ---------------------------------------------------------------------------

class TestReplyQueue:
    def test_inserts_pending_row(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE emails (uid TEXT PRIMARY KEY, account TEXT, sender TEXT,
                                 subject TEXT, date TEXT, processed_at TEXT)
        """)
        conn.execute("""
            CREATE TABLE reply_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_uid TEXT, summary TEXT, status TEXT NOT NULL DEFAULT 'pending',
                snoozed_until TEXT, added_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute("INSERT INTO emails VALUES ('uid1','perso1','a@b.com','Hi','2026-01-01','2026-01-01')")
        conn.commit()

        llm_agent.add_to_reply_queue(conn, "uid1", "Please reply about X", "2026-01-01T10:00:00+00:00")
        conn.commit()

        row = conn.execute("SELECT * FROM reply_queue WHERE email_uid='uid1'").fetchone()
        assert row is not None
        assert row[3] == "pending"  # status column
        conn.close()
