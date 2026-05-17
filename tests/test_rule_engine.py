"""Tests Phase 2 — rule_engine (aucun réseau, aucune DB requis)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import rule_engine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WHITELIST = {
    "domains": ["efrei.fr", "gouv.fr", "ffvoile.fr", "ameli.fr"],
    "senders": [],
    "keywords_subject": [],
}

RULES = [
    {
        "id": "otp_codes", "type": "auth",
        "keywords": ["code de vérification", "OTP", "magic link", "verify your", "connexion à"],
        "action": "delete", "delay_hours": 1,
    },
    {
        "id": "notifications_auto", "type": "notification",
        "senders": ["noreply@", "no-reply@", "notifications@", "github.com"],
        "no_action_required": True, "action": "delete", "delay_hours": 0,
    },
    {
        "id": "tracking_delivered", "type": "tracking",
        "keywords": ["livré", "delivered", "remis", "out for delivery"],
        "action": "delete", "delay_days": 2,
    },
    {
        "id": "daily_digests", "type": "digest",
        "senders": ["duolingo", "strava"],
        "action": "delete", "delay_days": 1,
    },
]

BLACKLIST_SENDERS: list[str] = []
BLACKLIST_DOMAINS: list[str] = []


def make_email(sender: str = "someone@example.com", subject: str = "",
               body_preview: str = "") -> dict[str, str]:
    return {"from": sender, "subject": subject, "body_preview": body_preview, "uid": "1"}


# ---------------------------------------------------------------------------
# Couche 1 — Liste blanche
# ---------------------------------------------------------------------------

class TestWhitelist:
    def test_domain_exact_match(self):
        email = make_email("prof@efrei.fr", "Notes de partiel")
        assert rule_engine.check_whitelist(email, WHITELIST) is True

    def test_subdomain_match(self):
        email = make_email("service@api.gouv.fr", "Mise à jour")
        assert rule_engine.check_whitelist(email, WHITELIST) is True

    def test_non_whitelisted_domain(self):
        email = make_email("promo@shop.com", "Super offre")
        assert rule_engine.check_whitelist(email, WHITELIST) is False

    def test_display_name_with_angle_brackets(self):
        email = make_email("FFVoile <contact@ffvoile.fr>", "Résultats régate")
        assert rule_engine.check_whitelist(email, WHITELIST) is True

    def test_sender_exact(self):
        wl = {**WHITELIST, "senders": ["alice@example.com"]}
        email = make_email("Alice <alice@example.com>", "Bonjour")
        assert rule_engine.check_whitelist(email, wl) is True

    def test_keyword_in_subject(self):
        wl = {**WHITELIST, "keywords_subject": ["urgent"]}
        email = make_email("unknown@example.com", "URGENT : réunion demain")
        assert rule_engine.check_whitelist(email, wl) is True


# ---------------------------------------------------------------------------
# Couche 2 — Règles déterministes
# ---------------------------------------------------------------------------

class TestApplyRules:
    def test_otp_in_subject(self):
        email = make_email("bank@example.com", "Votre code de vérification : 123456")
        result = rule_engine.apply_rules(email, RULES, BLACKLIST_SENDERS, BLACKLIST_DOMAINS)
        assert result is not None
        assert result["rule_id"] == "otp_codes"
        assert result["action"] == "delete"

    def test_otp_in_body(self):
        email = make_email("auth@example.com", "Connexion", "Votre OTP est 9876")
        result = rule_engine.apply_rules(email, RULES, BLACKLIST_SENDERS, BLACKLIST_DOMAINS)
        assert result is not None
        assert result["rule_id"] == "otp_codes"

    def test_noreply_sender(self):
        email = make_email("noreply@github.com", "PR merged")
        result = rule_engine.apply_rules(email, RULES, BLACKLIST_SENDERS, BLACKLIST_DOMAINS)
        assert result is not None
        assert result["rule_id"] == "notifications_auto"

    def test_tracking_delivered(self):
        email = make_email("suivi@laposte.fr", "Votre colis a été livré")
        result = rule_engine.apply_rules(email, RULES, BLACKLIST_SENDERS, BLACKLIST_DOMAINS)
        assert result is not None
        assert result["rule_id"] == "tracking_delivered"
        assert result["delay_hours"] == 48  # delay_days=2 → 48h

    def test_digest_sender(self):
        # weekly@ n'est pas dans notifications_auto, mais duolingo est dans daily_digests
        email = make_email("weekly@duolingo.com", "Ton récap de la semaine")
        result = rule_engine.apply_rules(email, RULES, BLACKLIST_SENDERS, BLACKLIST_DOMAINS)
        assert result is not None
        assert result["rule_id"] == "daily_digests"

    def test_no_match_returns_none(self):
        email = make_email("alice@gmail.com", "On se voit samedi ?", "Tu es dispo ?")
        result = rule_engine.apply_rules(email, RULES, BLACKLIST_SENDERS, BLACKLIST_DOMAINS)
        assert result is None

    def test_blacklist_sender(self):
        email = make_email("spam@badactor.com", "Gagnez un iPhone")
        result = rule_engine.apply_rules(
            email, RULES, ["spam@badactor.com"], []
        )
        assert result is not None
        assert result["rule_id"] == "blacklist"

    def test_blacklist_domain(self):
        email = make_email("contact@badactor.com", "Offre exclusive")
        result = rule_engine.apply_rules(
            email, RULES, [], ["badactor.com"]
        )
        assert result is not None
        assert result["rule_id"] == "blacklist"


# ---------------------------------------------------------------------------
# process_email — pipeline complet
# ---------------------------------------------------------------------------

class TestProcessEmail:
    def test_whitelist_short_circuits(self):
        email = make_email("noreply@efrei.fr", "OTP 123456")
        # Même si l'email contient OTP, il est protégé par la whitelist
        decision = rule_engine.process_email(
            email, WHITELIST, RULES, BLACKLIST_SENDERS, BLACKLIST_DOMAINS
        )
        assert decision["action"] == "keep"
        assert decision["rule_id"] == "whitelist"
        assert decision["score"] == 3

    def test_rule_match_returns_delete(self):
        email = make_email("noreply@github.com", "Build succeeded")
        decision = rule_engine.process_email(
            email, WHITELIST, RULES, BLACKLIST_SENDERS, BLACKLIST_DOMAINS
        )
        assert decision["action"] == "delete"
        assert decision["score"] == 1

    def test_no_match_sends_to_llm(self):
        email = make_email("alice@gmail.com", "On se voit samedi ?", "Tu es dispo ?")
        decision = rule_engine.process_email(
            email, WHITELIST, RULES, BLACKLIST_SENDERS, BLACKLIST_DOMAINS
        )
        assert decision["action"] == "llm_required"
        assert decision["score"] is None
