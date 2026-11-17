"""Réglages, chemins et comptes.

Les identifiants viennent de config/accounts.env (jamais versionné) ; seuls les
noms affichés et les fournisseurs sont dans le code.
"""

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

RACINE = Path(__file__).resolve().parents[2]
FICHIER_COMPTES = RACINE / "config" / "accounts.env"
DOSSIER_DONNEES = RACINE / "data"
FUSEAU = ZoneInfo("Europe/Paris")

# clé -> (nom affiché, fournisseur), dans l'ordre d'ajout prévu au jalon 9
COMPTES = {
    "perso1": ("Principale", "gmail"),
    "perso2": ("Secondaire", "gmail"),
    "voile": ("Cap Efrei", "gmail"),
    "cours": ("Cours", "outlook"),
}

HOTES_EXEMPLE = {"", "imap.example.com"}


@dataclass(frozen=True)
class Compte:
    cle: str
    nom: str
    fournisseur: str
    hote: str
    utilisateur: str
    mot_de_passe: str = ""
    port: int = 993

    @property
    def configure(self) -> bool:
        return self.hote not in HOTES_EXEMPLE and bool(self.utilisateur)

    def __repr__(self) -> str:  # jamais le mot de passe
        return f"Compte({self.cle!r}, {self.hote!r})"


def charger_comptes(fichier: Path = FICHIER_COMPTES) -> dict[str, Compte]:
    """Lit accounts.env (variables PERSO1_HOST, PERSO1_USER, PERSO1_PASS...)."""
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
        )
    return comptes
