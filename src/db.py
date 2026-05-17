import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "postroom.db"


def get_conn(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    logger.info("Initialisation de la base de données : %s", db_path)
    with get_conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS emails (
                uid          TEXT PRIMARY KEY,
                account      TEXT NOT NULL,
                sender       TEXT,
                subject      TEXT,
                date         TEXT,
                processed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS decisions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email_uid   TEXT REFERENCES emails(uid),
                action      TEXT NOT NULL,
                score       INTEGER,
                rule_id     TEXT,
                reason      TEXT,
                delay_hours INTEGER NOT NULL DEFAULT 0,
                decided_at  TEXT NOT NULL,
                purged_at   TEXT,
                reviewed    TEXT CHECK(reviewed IN ('keep','delete'))
            );

            CREATE TABLE IF NOT EXISTS rule_hits (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id  TEXT NOT NULL,
                email_uid TEXT,
                hit_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reply_queue (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email_uid     TEXT REFERENCES emails(uid),
                summary       TEXT,
                status        TEXT NOT NULL DEFAULT 'pending',
                snoozed_until TEXT,
                added_at      TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attachments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email_uid  TEXT REFERENCES emails(uid),
                filename   TEXT,
                path       TEXT,
                size_bytes INTEGER,
                category   TEXT,
                saved_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS social_queue (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email_uid  TEXT REFERENCES emails(uid),
                summary    TEXT,
                status     TEXT NOT NULL DEFAULT 'pending',
                expires_at TEXT NOT NULL,
                added_at   TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_queue (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email_uid  TEXT REFERENCES emails(uid),
                summary    TEXT,
                status     TEXT NOT NULL DEFAULT 'pending',
                expires_at TEXT NOT NULL,
                added_at   TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
    logger.info("Schéma SQLite prêt.")


def upsert_email(conn: sqlite3.Connection, uid: str, account: str, sender: str,
                 subject: str, date: str, processed_at: str) -> bool:
    """Insère le mail s'il n'existe pas. Retourne True si nouvel enregistrement."""
    cursor = conn.execute(
        "SELECT 1 FROM emails WHERE uid = ?", (uid,)
    )
    if cursor.fetchone():
        return False
    conn.execute(
        "INSERT INTO emails (uid, account, sender, subject, date, processed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, account, sender, subject, date, processed_at),
    )
    return True
