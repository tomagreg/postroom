"""Couches 1 et 2 du pipeline de décision.

Couche 1 — Liste blanche : domaine, expéditeur exact, mot-clé dans l'objet.
Couche 2 — Règles déterministes depuis rules.yaml : keywords, senders, types.

Retourne une décision ou None (→ couche 3, LLM, phase 3).
"""
import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chargement de la configuration
# ---------------------------------------------------------------------------

def load_whitelist(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("whitelist", {})


def load_rules(path: Path) -> tuple[list[dict], list[str], list[str], float]:
    """Retourne (rules, blacklist_senders, blacklist_domains, confidence_threshold)."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rules = data.get("rules", [])
    blacklist = data.get("blacklist", {})
    threshold = float(data.get("confidence_threshold", 0.75))
    return rules, blacklist.get("senders", []), blacklist.get("domains", []), threshold


# ---------------------------------------------------------------------------
# Couche 1 — Liste blanche
# ---------------------------------------------------------------------------

def _extract_domain(address: str) -> str:
    """Extrait le domaine depuis une adresse mail (ex: 'Foo <foo@bar.com>' → 'bar.com')."""
    match = re.search(r"@([\w.\-]+)", address)
    return match.group(1).lower() if match else ""


def _extract_local(address: str) -> str:
    match = re.search(r"([\w.\-+]+)@", address)
    return match.group(1).lower() if match else ""


def check_whitelist(email: dict[str, str], whitelist: dict[str, Any]) -> bool:
    """Retourne True si le mail est protégé par la liste blanche."""
    sender = email.get("from", "")
    subject = email.get("subject", "")
    domain = _extract_domain(sender)

    # Domaines protégés (correspondance suffixe pour *.gouv.fr etc.)
    for protected_domain in whitelist.get("domains", []):
        if domain == protected_domain or domain.endswith("." + protected_domain):
            logger.debug("Whitelist domaine : %s (%s)", domain, protected_domain)
            return True

    # Expéditeurs exacts
    sender_lower = sender.lower()
    for protected_sender in whitelist.get("senders", []):
        if protected_sender.lower() in sender_lower:
            logger.debug("Whitelist expéditeur : %s", protected_sender)
            return True

    # Mots-clés dans l'objet
    subject_lower = subject.lower()
    for keyword in whitelist.get("keywords_subject", []):
        if keyword.lower() in subject_lower:
            logger.debug("Whitelist objet : %s", keyword)
            return True

    return False


# ---------------------------------------------------------------------------
# Couche 2 — Moteur de règles déterministes
# ---------------------------------------------------------------------------

def _match_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _match_senders(sender: str, patterns: list[str]) -> bool:
    sender_lower = sender.lower()
    return any(p.lower() in sender_lower for p in patterns)


def _sender_in_blacklist(sender: str, blacklist_senders: list[str],
                         blacklist_domains: list[str]) -> bool:
    domain = _extract_domain(sender)
    sender_lower = sender.lower()
    if any(bs.lower() in sender_lower for bs in blacklist_senders):
        return True
    if any(domain == bd.lower() or domain.endswith("." + bd.lower())
           for bd in blacklist_domains):
        return True
    return False


def apply_rules(email: dict[str, str], rules: list[dict],
                blacklist_senders: list[str],
                blacklist_domains: list[str]) -> dict[str, Any] | None:
    """Applique les règles déterministes. Retourne un dict décision ou None."""
    sender = email.get("from", "")
    subject = email.get("subject", "")
    body_preview = email.get("body_preview", "")
    full_text = f"{subject} {body_preview}"

    # Blacklist expéditeur/domaine (priorité absolue)
    if _sender_in_blacklist(sender, blacklist_senders, blacklist_domains):
        return {
            "action": "delete",
            "rule_id": "blacklist",
            "reason": f"Expéditeur/domaine blacklisté : {sender}",
            "delay_hours": 0,
        }

    for rule in rules:
        rule_id = rule.get("id", "unknown")
        rule_type = rule.get("type", "")
        action = rule.get("action", "delete")

        matched = False

        # Correspondance par mots-clés (dans objet ou body)
        if "keywords" in rule:
            matched = _match_keywords(full_text, rule["keywords"])

        # Correspondance par expéditeur
        if not matched and "senders" in rule:
            matched = _match_senders(sender, rule["senders"])

        if not matched:
            continue

        # Domaines exemptés de cette règle
        if "except_domains" in rule:
            domain = _extract_domain(sender)
            if any(domain == ed or domain.endswith("." + ed)
                   for ed in rule["except_domains"]):
                continue

        # Calcul du délai de suppression
        delay_hours = rule.get("delay_hours", 0)
        delay_days = rule.get("delay_days", 0)
        total_hours = delay_hours + delay_days * 24

        reason = _build_reason(rule_id, rule_type, sender, subject)
        logger.debug("Règle '%s' matchée pour : %s — %s", rule_id, sender, subject)

        return {
            "action": action,
            "rule_id": rule_id,
            "reason": reason,
            "delay_hours": total_hours,
        }

    return None  # pas de règle matchée → couche 3 (LLM)


def _build_reason(rule_id: str, rule_type: str, sender: str, subject: str) -> str:
    labels = {
        "auth": "Code d'authentification / OTP",
        "notification": "Notification automatique sans action",
        "tracking": "Suivi colis",
        "newsletter": "Newsletter inactive",
        "promo": "Promotion expirée",
        "booking": "Confirmation de réservation passée",
        "digest": "Résumé quotidien d'application",
    }
    label = labels.get(rule_type, f"Règle {rule_id}")
    return f"{label} — {subject[:80]}" if subject else label


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def process_email(email: dict[str, str], whitelist: dict[str, Any],
                  rules: list[dict], blacklist_senders: list[str],
                  blacklist_domains: list[str]) -> dict[str, Any]:
    """Pipeline couches 1+2. Retourne une décision complète.

    action: 'keep' | 'delete' | 'llm_required'
    """
    # Couche 1
    if check_whitelist(email, whitelist):
        return {
            "action": "keep",
            "rule_id": "whitelist",
            "reason": "Expéditeur ou domaine en liste blanche",
            "score": 3,
            "delay_hours": 0,
        }

    # Couche 2
    decision = apply_rules(email, rules, blacklist_senders, blacklist_domains)
    if decision:
        if decision["action"] not in ("promo_queue", "social_queue"):
            decision["score"] = 1
        return decision

    # Couche 3 (phase 3 — LLM)
    return {
        "action": "llm_required",
        "rule_id": None,
        "reason": "Aucune règle déterministe — à classifier par LLM",
        "score": None,
        "delay_hours": 0,
    }


PASSTHROUGH_ACTIONS = {"keep", "delete", "llm_required", "promo_queue"}
