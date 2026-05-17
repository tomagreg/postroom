import imaplib
import email
import logging
import os
from email.header import decode_header
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

ACCOUNT_PREFIXES = ("COURS", "VOILE", "PERSO1", "PERSO2")


def _decode_header_value(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def load_accounts(env_path: Path) -> list[dict[str, str]]:
    """Charge les configs IMAP des 4 comptes depuis accounts.env."""
    values = dotenv_values(env_path)
    accounts = []
    for prefix in ACCOUNT_PREFIXES:
        host = values.get(f"{prefix}_HOST")
        user = values.get(f"{prefix}_USER")
        password = values.get(f"{prefix}_PASS")
        if not (host and user and password):
            logger.warning("Compte %s incomplet dans %s — ignoré.", prefix, env_path)
            continue
        accounts.append({
            "name": prefix.lower(),
            "host": host,
            "user": user,
            "password": password,
        })
    logger.info("%d compte(s) chargé(s) depuis %s.", len(accounts), env_path)
    return accounts


def connect(account: dict[str, str]) -> imaplib.IMAP4_SSL:
    """Ouvre une connexion IMAP SSL et s'authentifie."""
    name = account["name"]
    logger.info("[%s] Connexion à %s…", name, account["host"])
    try:
        conn = imaplib.IMAP4_SSL(account["host"])
        conn.login(account["user"], account["password"])
        logger.info("[%s] Authentifié.", name)
        return conn
    except imaplib.IMAP4.error as exc:
        raise ConnectionError(f"[{name}] Échec de connexion IMAP : {exc}") from exc


def fetch_headers(conn: imaplib.IMAP4_SSL, mailbox: str = "INBOX",
                  limit: int = 50) -> list[dict[str, str]]:
    """Retourne les `limit` mails les plus récents avec leurs headers."""
    conn.select(mailbox, readonly=True)
    status, data = conn.search(None, "ALL")
    if status != "OK":
        logger.warning("Recherche IMAP échouée : %s", data)
        return []

    all_uids = data[0].split()
    uids_to_fetch = all_uids[-limit:] if len(all_uids) > limit else all_uids

    if not uids_to_fetch:
        return []

    uid_list = b",".join(uids_to_fetch)
    status, messages = conn.fetch(uid_list, "(BODY[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
    if status != "OK":
        logger.warning("Fetch headers échoué.")
        return []

    results = []
    for i, raw_uid in enumerate(uids_to_fetch):
        raw_data = messages[i * 2]
        if not isinstance(raw_data, tuple):
            continue
        msg = email.message_from_bytes(raw_data[1])
        results.append({
            "uid": raw_uid.decode(),
            "from": _decode_header_value(msg.get("From")),
            "subject": _decode_header_value(msg.get("Subject")),
            "date": msg.get("Date", ""),
            "message_id": msg.get("Message-ID", ""),
        })

    logger.debug("Fetched %d headers depuis %s.", len(results), mailbox)
    return results


def fetch_body_preview(conn: imaplib.IMAP4_SSL, uid: str,
                       max_words: int = 300) -> str:
    """Retourne les `max_words` premiers mots du body text/plain."""
    status, data = conn.fetch(uid.encode(), "(BODY.PEEK[TEXT])")
    if status != "OK" or not data or data[0] is None:
        return ""

    raw = data[0][1] if isinstance(data[0], tuple) else b""
    msg = email.message_from_bytes(raw)

    text = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")

    words = text.split()
    return " ".join(words[:max_words])


def close(conn: imaplib.IMAP4_SSL) -> None:
    try:
        conn.logout()
    except Exception:
        pass
