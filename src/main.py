"""Orchestrateur principal postroom.

Usage :
    python src/main.py --dry-run          # fetch + règles déterministes, aucune modification mail
    python src/main.py --dry-run --limit 20
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import anthropic
from dotenv import load_dotenv
import db
import imap_client
import llm_agent
import rule_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

ENV_PATH = ROOT / "config" / "accounts.env"
DB_PATH = ROOT / "postroom.db"
RULES_PATH = ROOT / "config" / "rules.yaml"
WHITELIST_PATH = ROOT / "config" / "whitelist.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="postroom — agent de tri mail")
    parser.add_argument(
        "--dry-run", action="store_true", required=True,
        help="Mode lecture seule obligatoire (aucune modification sur les serveurs mail)"
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Nombre de mails à fetcher par boîte (défaut : 50)"
    )
    return parser.parse_args()


def _record_decision(conn_db, uid: str, action: str, score: int | None,
                     rule_id: str | None, reason: str, delay_hours: int, now: str) -> None:
    row = conn_db.execute("SELECT reviewed FROM decisions WHERE email_uid = ?", (uid,)).fetchone()
    reviewed = row["reviewed"] if row else None
    conn_db.execute("DELETE FROM decisions WHERE email_uid = ?", (uid,))
    conn_db.execute("DELETE FROM rule_hits WHERE email_uid = ?", (uid,))
    conn_db.execute(
        "INSERT INTO decisions (email_uid, action, score, rule_id, reason, delay_hours, decided_at, reviewed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, action, score, rule_id, reason, delay_hours, now, reviewed),
    )
    if rule_id:
        conn_db.execute(
            "INSERT INTO rule_hits (rule_id, email_uid, hit_at) VALUES (?, ?, ?)",
            (rule_id, uid, now),
        )


def process_account(account: dict, conn_db, whitelist: dict, rules: list,
                    blacklist_senders: list, blacklist_domains: list,
                    limit: int, dry_run: bool,
                    llm_client: anthropic.Anthropic | None = None,
                    confidence_threshold: float = 0.75) -> dict[str, int]:
    """Connecte, fetch, classe, stocke. Retourne des compteurs par action."""
    name = account["name"]
    conn_imap = None
    counters: dict[str, int] = {"fetched": 0, "nouveaux": 0,
                                 "keep": 0, "delete": 0, "review": 0, "llm_required": 0}
    try:
        conn_imap = imap_client.connect(account)
        headers = imap_client.fetch_headers(conn_imap, limit=limit)
        counters["fetched"] = len(headers)
        now = datetime.now(timezone.utc).isoformat()

        for h in headers:
            uid = f"{name}:{h['uid']}"

            body_preview = imap_client.fetch_body_preview(conn_imap, h["uid"])
            email_data = {**h, "body_preview": body_preview}

            is_new = db.upsert_email(
                conn_db, uid=uid, account=name,
                sender=h["from"], subject=h["subject"],
                date=h["date"], processed_at=now,
            )
            if is_new:
                counters["nouveaux"] += 1

            # Couches 1 + 2
            decision = rule_engine.process_email(
                email_data, whitelist, rules, blacklist_senders, blacklist_domains
            )

            # Files d'attente dédiées
            if decision["action"] in ("promo_queue", "social_queue"):
                table = decision["action"].replace("_queue", "_queue")
                default_days = 14 if decision["action"] == "promo_queue" else 7
                delay_days = decision.get("delay_hours", 0) // 24 or default_days
                expires_at = (datetime.now(timezone.utc) + timedelta(days=delay_days)).isoformat()
                conn_db.execute(f"DELETE FROM {table} WHERE email_uid = ?", (uid,))
                conn_db.execute(
                    f"INSERT INTO {table} "
                    "(email_uid, summary, status, expires_at, added_at, updated_at) "
                    "VALUES (?, ?, 'pending', ?, ?, ?)",
                    (uid, h.get("subject", ""), expires_at, now, now),
                )

            # Couche 3 — LLM si nécessaire
            if decision["action"] == "llm_required" and llm_client is not None:
                counters["llm_required"] += 1
                llm_result = llm_agent.classify_with_escalation(
                    email_data, name, llm_client, threshold=confidence_threshold
                )
                decision = llm_agent.result_to_decision(llm_result)

                # Score 4 → file d'attente de réponse
                if llm_result.get("action_required"):
                    summary = llm_result.get("reply_suggested") or llm_result.get("reason", "")
                    llm_agent.add_to_reply_queue(conn_db, uid, summary, now)

            action = decision["action"]
            counters[action] = counters.get(action, 0) + 1

            _record_decision(
                conn_db, uid=uid,
                action=action,
                score=decision.get("score"),
                rule_id=decision.get("rule_id"),
                reason=decision.get("reason", ""),
                delay_hours=decision.get("delay_hours", 0),
                now=now,
            )

            logger.debug("[%s] %s | %s | %s", name, action,
                         h["from"][:40], h["subject"][:60])

        conn_db.commit()
        return counters
    finally:
        if conn_imap:
            imap_client.close(conn_imap)


def main() -> None:
    args = parse_args()

    load_dotenv(ENV_PATH)
    logger.info("=== postroom démarré (dry-run=%s, limit=%d) ===", args.dry_run, args.limit)

    db.init_db(DB_PATH)

    whitelist = rule_engine.load_whitelist(WHITELIST_PATH)
    rules, blacklist_senders, blacklist_domains, threshold = rule_engine.load_rules(RULES_PATH)
    logger.info("%d règle(s) chargée(s).", len(rules))

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    llm_client: anthropic.Anthropic | None = None
    if api_key:
        llm_client = anthropic.Anthropic(api_key=api_key)
        logger.info("Client LLM initialisé (haiku → sonnet, seuil=%.2f).", threshold)
    else:
        logger.warning("ANTHROPIC_API_KEY absent — couche LLM désactivée.")

    accounts = imap_client.load_accounts(ENV_PATH)
    if not accounts:
        logger.error("Aucun compte chargé depuis %s — abandon.", ENV_PATH)
        sys.exit(1)

    conn_db = db.get_conn(DB_PATH)
    try:
        total: dict[str, int] = {"fetched": 0, "nouveaux": 0,
                                  "keep": 0, "delete": 0, "review": 0, "llm_required": 0}
        for account in accounts:
            try:
                counters = process_account(
                    account, conn_db, whitelist, rules,
                    blacklist_senders, blacklist_domains,
                    args.limit, args.dry_run,
                    llm_client=llm_client,
                    confidence_threshold=threshold,
                )
                for k, v in counters.items():
                    total[k] = total.get(k, 0) + v
                logger.info(
                    "[%s] %d fetchés | %d nouveaux | %d keep | %d delete | %d review | %d → LLM",
                    account["name"],
                    counters["fetched"], counters["nouveaux"],
                    counters.get("keep", 0), counters.get("delete", 0),
                    counters.get("review", 0), counters.get("llm_required", 0),
                )
            except ConnectionError as exc:
                logger.error("%s", exc)
            except Exception as exc:
                logger.exception("[%s] Erreur inattendue : %s", account["name"], exc)
    finally:
        conn_db.close()

    logger.info(
        "=== Terminé — %d fetchés | %d nouveaux | %d keep | %d delete | %d review | %d → LLM ===",
        total["fetched"], total["nouveaux"],
        total.get("keep", 0), total.get("delete", 0),
        total.get("review", 0), total.get("llm_required", 0),
    )


if __name__ == "__main__":
    main()
