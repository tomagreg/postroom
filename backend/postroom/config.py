"""Réglages, chemins et comptes.

Les identifiants viennent de config/accounts.env (jamais versionné) ; seuls les
noms affichés et les fournisseurs sont dans le code.
"""

import os
from dataclasses import dataclass, replace
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

RACINE = Path(__file__).resolve().parents[2]
FICHIER_COMPTES = RACINE / "config" / "accounts.env"
DOSSIER_DONNEES = RACINE / "data"
FICHIER_REGLES = RACINE / "config" / "rules.yaml"
FICHIER_LISTE_BLANCHE = RACINE / "config" / "whitelist.yaml"
FUSEAU = ZoneInfo("Europe/Paris")
EXECUTABLE_CLAUDE = os.environ.get("POSTROOM_CLAUDE") or "/root/.local/bin/claude"

# clé -> (nom affiché, fournisseur), dans l'ordre d'ajout prévu au jalon 9.
# cours (Microsoft 365 d'Efrei) est transférée vers perso1 et lue dans un libellé Gmail (jalon 0).
COMPTES = {
    "perso1": ("Principale", "gmail"),
    "perso2": ("Secondaire", "gmail"),
    "voile": ("Cap Efrei", "gmail"),
    "cours": ("Cours", "gmail"),
}

HOTES_EXEMPLE = {"", "imap.example.com"}


def fichier_base() -> Path:
    """Base SQLite : POSTROOM_DB, sinon data/postroom.db."""
    return Path(os.environ.get("POSTROOM_DB") or DOSSIER_DONNEES / "postroom.db")


@dataclass(frozen=True)
class Compte:
    cle: str
    nom: str
    fournisseur: str
    hote: str
    utilisateur: str
    mot_de_passe: str = ""
    port: int = 993
    auth: str = "motdepasse"  # motdepasse · oauth2
    client_id: str = ""
    tenant: str = ""
    via: str = ""  # boîte qui reçoit les mails transférés (cours -> perso1)
    dossier: str = "INBOX"  # dossier ou libellé lu

    @property
    def configure(self) -> bool:
        if self.hote in HOTES_EXEMPLE or not self.utilisateur:
            return False
        return bool(self.client_id) if self.auth == "oauth2" else bool(self.mot_de_passe)

    def __repr__(self) -> str:  # jamais le mot de passe
        return f"Compte({self.cle!r}, {self.hote!r})"


def charger_comptes(fichier: Path = FICHIER_COMPTES) -> dict[str, Compte]:
    """Lit accounts.env (PERSO1_HOST, PERSO1_USER, PERSO1_PASS ; pour OAuth2 :
    COURS_AUTH=oauth2, COURS_CLIENT_ID, COURS_TENANT ; pour une boîte transférée :
    COURS_VIA=perso1, COURS_DOSSIER=cours, qui reprend les identifiants de perso1)."""
    valeurs = dotenv_values(fichier) if fichier.exists() else {}
    comptes = {}
    for cle, (nom, fournisseur) in COMPTES.items():
        p = cle.upper()
        comptes[cle] = Compte(
            cle=cle,
            nom=nom,
            fournisseur=fournisseur,
            hote=(valeurs.get(f"{p}_HOST") or "").strip(),
            utilisateur=(valeurs.get(f"{p}_USER") or "").strip(),
            mot_de_passe=valeurs.get(f"{p}_PASS") or "",
            port=int(valeurs.get(f"{p}_PORT") or 993),
            auth=(valeurs.get(f"{p}_AUTH") or "motdepasse").strip(),
            client_id=(valeurs.get(f"{p}_CLIENT_ID") or "").strip(),
            tenant=(valeurs.get(f"{p}_TENANT") or "").strip(),
            via=(valeurs.get(f"{p}_VIA") or "").strip(),
            dossier=(valeurs.get(f"{p}_DOSSIER") or "INBOX").strip(),
        )
    for cle, c in comptes.items():
        source = comptes.get(c.via)
        if source and not source.via:
            comptes[cle] = replace(
                source, cle=cle, nom=c.nom, fournisseur=source.fournisseur, via=c.via, dossier=c.dossier
            )
    return comptes


def cle_anthropic(fichier: Path = FICHIER_COMPTES) -> str:
    """Clé de l'API Anthropic (ANTHROPIC_API_KEY de accounts.env, sinon de l'environnement)."""
    valeurs = dotenv_values(fichier) if fichier.exists() else {}
    return (valeurs.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def jeton_abonnement(fichier: Path = FICHIER_COMPTES) -> str:
    """Jeton de l'abonnement Claude (CLAUDE_CODE_OAUTH_TOKEN de accounts.env, sinon de l'environnement)."""
    valeurs = dotenv_values(fichier) if fichier.exists() else {}
    return (valeurs.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()


def moteur_ia(fichier: Path = FICHIER_COMPTES) -> str:
    """IA_MOTEUR de accounts.env : abonnement (claude -p, par défaut) ou api (clé, facturée au jeton)."""
    valeurs = dotenv_values(fichier) if fichier.exists() else {}
    return "api" if (valeurs.get("IA_MOTEUR") or "").strip().lower() == "api" else "abonnement"


def libelles_exclus(cle: str, comptes: dict[str, Compte]) -> set[str]:
    """Libellés de cette boîte qui appartiennent à une boîte transférée (perso1 : celui de cours)."""
    return {c.dossier for c in comptes.values() if c.via == cle and c.dossier != "INBOX"}
