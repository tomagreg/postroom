"""Couche 3 du pipeline — classification LLM.

Stratégie dual-pass tout-haiku (coût minimal) :
- Passe 1 : classification rapide (haiku)
- Passe 2 : disambiguation ciblée si score==2 ou confidence < threshold (haiku, prompt différent)

Pour activer sonnet sur la passe 2 en production, changer MODEL_SMART.

Contrat JSON retourné par le LLM :
{
  "score": 1|2|3|4,
  "category": str,
  "reason": str,
  "confidence": float (0-1),
  "action_required": bool,
  "reply_suggested": str | null
}
"""
import json
import logging
import sqlite3
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

MODEL_FAST = "claude-haiku-4-5"
MODEL_SMART = "claude-haiku-4-5"  # passer à claude-sonnet-4-6 en production

_SCORE_DEFINITIONS = """\
Score definitions (pick exactly one):
  1 = Delete — no value, safe to remove (OTP expired, promo, auto-notification)
  2 = Ambiguous — unclear value; report for human review
  3 = Keep — informational value, no action needed (invoice, schedule, receipt)
  4 = Action required — needs a reply, a task, or a decision

Return ONLY valid JSON on a single line, no markdown:
{"score": <int>, "category": "<str>", "reason": "<str (≤120 chars)>",
 "confidence": <float 0-1>, "action_required": <bool>, "reply_suggested": <str|null>}
"""

_DISAMBIGUATION_SUFFIX = """\

SECOND PASS — this email was ambiguous on first classification.
Focus: is there any concrete action, deadline, or personal relevance?
If yes → score 3 or 4. If genuinely unclear → score 2. Avoid score 1 unless certain.
"""

_ACCOUNT_CONTEXT = {
    "cours": "This is the school inbox for an engineering student at Efrei Paris (M1/M2 level). Prioritise academic deadlines, exam results, administrative messages from professors or the school administration.",
    "voile": "This is the sailing club inbox (CAP Efrei sailing team). Prioritise regattas, race results, committee decisions, and messages from coaches or members.",
    "perso1": "Personal inbox. Prioritise messages from real people, bank alerts, health (ameli.fr, doctor appointments), and any message requiring a personal reply.",
    "perso2": "Secondary personal inbox. Same heuristics as perso1 but lower priority.",
}


def _system_prompt(account_name: str, disambiguation: bool = False) -> str:
    context = _ACCOUNT_CONTEXT.get(account_name, "General inbox.")
    base = (
        f"You are an email triage assistant. Account context: {context}\n\n"
        + _SCORE_DEFINITIONS
    )
    return base + _DISAMBIGUATION_SUFFIX if disambiguation else base


def _user_message(email: dict[str, str]) -> str:
    return (
        f"From: {email.get('from', '')}\n"
        f"Subject: {email.get('subject', '')}\n"
        f"Body preview: {email.get('body_preview', '')[:1500]}"
    )


def _parse_response(text: str) -> dict[str, Any]:
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def classify(
    email: dict[str, str],
    account_name: str,
    client: anthropic.Anthropic,
    model: str = MODEL_FAST,
    disambiguation: bool = False,
) -> dict[str, Any]:
    """Classifie un email via LLM. Retourne le dict de décision LLM."""
    system = _system_prompt(account_name, disambiguation=disambiguation)
    response = client.messages.create(
        model=model,
        max_tokens=256,
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": _user_message(email)}],
    )
    raw = response.content[0].text
    try:
        result = _parse_response(raw)
    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        logger.warning("LLM parse error (%s): %s — raw: %s", model, exc, raw[:200])
        result = {
            "score": 2,
            "category": "parse_error",
            "reason": f"LLM response unparseable: {raw[:80]}",
            "confidence": 0.0,
            "action_required": False,
            "reply_suggested": None,
        }
    logger.debug(
        "[%s] %s score=%s cat=%s conf=%.2f",
        account_name, model, result.get("score"), result.get("category"), result.get("confidence", 0),
    )
    return result


def classify_with_escalation(
    email: dict[str, str],
    account_name: str,
    client: anthropic.Anthropic,
    threshold: float = 0.75,
) -> dict[str, Any]:
    """Haiku en premier ; escalade vers Sonnet si score==2 ou confidence < threshold."""
    result = classify(email, account_name, client, model=MODEL_FAST)
    score = result.get("score", 2)
    confidence = result.get("confidence", 0.0)

    if score == 2 or confidence < threshold:
        logger.info(
            "[%s] Passe 2 haiku (score=%s, conf=%.2f) — %s",
            account_name, score, confidence, email.get("subject", "")[:60],
        )
        result = classify(email, account_name, client, model=MODEL_SMART, disambiguation=True)

    return result


def result_to_decision(llm_result: dict[str, Any]) -> dict[str, Any]:
    """Convertit le résultat LLM en décision pipeline."""
    score = llm_result.get("score", 2)
    if score == 1:
        action = "delete"
    elif score in (3, 4):
        action = "keep"
    else:
        action = "review"

    return {
        "action": action,
        "rule_id": "llm",
        "reason": llm_result.get("reason", ""),
        "score": score,
        "delay_hours": 0,
        "llm_meta": {
            "category": llm_result.get("category"),
            "confidence": llm_result.get("confidence"),
            "action_required": llm_result.get("action_required", False),
            "reply_suggested": llm_result.get("reply_suggested"),
        },
    }


def add_to_reply_queue(conn: sqlite3.Connection, uid: str, summary: str, now: str) -> None:
    conn.execute(
        "INSERT INTO reply_queue (email_uid, summary, status, added_at, updated_at) "
        "VALUES (?, ?, 'pending', ?, ?)",
        (uid, summary, now, now),
    )
