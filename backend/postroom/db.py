"""Connexion SQLite (WAL, timeout 30 s) et migrations numérotées (migrations/NNN_nom.sql)."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from postroom import config

DOSSIER_MIGRATIONS = Path(__file__).parent / "migrations"


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connecter(chemin: Path | str | None = None) -> sqlite3.Connection:
    chemin = Path(chemin or config.fichier_base())
    if str(chemin) != ":memory:":
        chemin.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(chemin, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def migrer(con: sqlite3.Connection, dossier: Path = DOSSIER_MIGRATIONS) -> list[int]:
    """Applique dans l'ordre les migrations absentes de schema_version ; renvoie celles appliquées."""
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applique_le TEXT NOT NULL)")
    con.commit()
    faites = {r[0] for r in con.execute("SELECT version FROM schema_version")}
    appliquees = []
    for fichier in sorted(dossier.glob("[0-9][0-9][0-9]_*.sql")):
        version = int(fichier.name[:3])
        if version in faites:
            continue
        sql = fichier.read_text(encoding="utf-8")
        try:
            # executescript valide toute transaction ouverte : BEGIN/COMMIT explicites
            con.executescript(
                f"BEGIN;\n{sql}\n;INSERT INTO schema_version VALUES ({version}, '{maintenant()}');\nCOMMIT;"
            )
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        appliquees.append(version)
    return appliquees


def version(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
