"""Purge job — flag \Deleted sur IMAP les mails dont le délai est écoulé.

Règles :
- Seuls les mails avec action='delete' et decided_at + delay_hours <= now sont traités.
- On flag \Deleted sans EXPUNGE immédiat (purge physique déléguée au serveur mail).
- En cas d'erreur IMAP sur un mail, on logue et on continue.

Usage :
    python src/purge.py --dry-run     # affiche ce qui serait purgé, sans toucher IMAP
    python src/purge.py               # flag réellement \Deleted
"""
import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
import db as db_module
import imap_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("purge")

ENV_PATH = ROOT / "config" / "accounts.env"
DB_PATH = ROOT / "postroom.db"


def get_pending_purges(conn: sqlite3.Connection, account_name: str) -> list[sqlite3.Row]:
    """Retourne les décisions delete dont le délai est écoulé et non encore purgées."""
    return conn.execute("""
        SELECT d.id, d.email_uid, d.delay_hours, d.decided_at, e.sender, e.subject
        FROM decisions d
        JOIN emails e ON e.uid = d.email_uid
        WHERE d.action = 'delete'
          AND d.purged_at IS NULL
          AND e.account = ?
          AND datetime(d.decided_at, '+' || d.delay_hours || ' hours') <= datetime('now')
    """, (account_name,)).fetchall()


def flag_deleted(conn_imap, imap_uid: str) -> bool:
    """Flag \Deleted sur IMAP. Retourne True si succès."""
    try:
        status, _ = conn_imap.uid("STORE", imap_uid, "+FLAGS", r"(\Deleted)")
        return status == "OK"
    except Exception as exc:
        logger.warning("Erreur flag \\Deleted uid=%s : %s", imap_uid, exc)
        return False


def mark_purged(conn: sqlite3.Connection, decision_id: int, now: str) -> None:
    conn.execute(
        "UPDATE decisions SET purged_at = ? WHERE id = ?",
        (now, decision_id),
    )


def run_purge(account: dict, conn_db: sqlite3.Connection,
              dry_run: bool = True) -> dict[str, int]:
    name = account["name"]
    counters = {"pending": 0, "flagged": 0, "errors": 0}
    now = datetime.now(timezone.utc).isoformat()

    rows = get_pending_purges(conn_db, name)
    counters["pending"] = len(rows)

    if not rows:
        logger.info("[%s] Aucun mail à purger.", name)
        return counters

    logger.info("[%s] %d mail(s) à purger.", name, len(rows))

    if dry_run:
        for r in rows:
            logger.info(
                "[%s] DRY-RUN — flaguerait \\Deleted : %s | %s",
                name, r["sender"][:40], r["subject"][:60],
            )
        return counters

    conn_imap = None
    try:
        conn_imap = imap_client.connect(account)
        conn_imap.select("INBOX")

        for r in rows:
            # uid format : "account:imap_uid"
            imap_uid = r["email_uid"].split(":", 1)[1]
            ok = flag_deleted(conn_imap, imap_uid)
            if ok:
                mark_purged(conn_db, r["id"], now)
                counters["flagged"] += 1
                logger.info(
                    "[%s] \\Deleted flagué : %s | %s",
                    name, r["sender"][:40], r["subject"][:60],
                )
            else:
                counters["errors"] += 1

        conn_db.commit()
    except Exception as exc:
        logger.error("[%s] Erreur connexion IMAP : %s", name, exc)
        counters["errors"] += counters["pending"]
    finally:
        if conn_imap:
            imap_client.close(conn_imap)

    return counters


def main() -> None:
    parser = argparse.ArgumentParser(description="postroom purge job")
    parser.add_argument("--dry-run", action="store_true",
                        help="Affiche ce qui serait purgé sans modifier IMAP")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    logger.info("=== purge démarré (dry-run=%s) ===", args.dry_run)

    accounts = imap_client.load_accounts(ENV_PATH)
    if not accounts:
        logger.error("Aucun compte chargé — abandon.")
        sys.exit(1)

    conn_db = db_module.get_conn(DB_PATH)
    total = {"pending": 0, "flagged": 0, "errors": 0}
    try:
        for account in accounts:
            try:
                c = run_purge(account, conn_db, dry_run=args.dry_run)
                for k in total:
                    total[k] += c.get(k, 0)
            except Exception as exc:
                logger.exception("[%s] Erreur inattendue : %s", account["name"], exc)
    finally:
        conn_db.close()

    logger.info(
        "=== Terminé — %d en attente | %d flagués | %d erreurs ===",
        total["pending"], total["flagged"], total["errors"],
    )


if __name__ == "__main__":
    main()
