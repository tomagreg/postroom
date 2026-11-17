"""Règles déterministes : catégories v2, reprise des règles v1 (rules.yaml, whitelist.yaml)
et application aux mails.
"""

import sqlite3
from dataclasses import dataclass, field
from email.utils import parseaddr
from pathlib import Path

import yaml

from postroom import config
from postroom.db import maintenant

# Liste fermée des catégories (plan v2, § 5). L'IA ne peut choisir que parmi elles.
CATEGORIES = (
    "a_repondre",
    "commande",
    "evenement",
    "alerte",
    "offre",
    "social",
    "recu_paiement",
    "mouvement_bancaire",
    "code_unique",
    "cgu",
    "bienvenue",
    "suivi_obsolete",
    "autre_supprime",
    "a_trier",
)
# Liste blanche : jamais envoyé en Suppression par une règle, toujours examiné par l'IA.
LISTE_BLANCHE = "liste_blanche"

# règle v1 -> (catégorie v2, état). Les règles trop larges, qui enverraient en Suppression
# des commandes ou des événements, sont seulement proposées (tranché au jalon 8).
CORRESPONDANCE_V1 = {
    "otp_codes": ("code_unique", "active"),
    "notifications_auto": ("autre_supprime", "proposee"),
    "tracking_delivered": ("suivi_obsolete", "proposee"),
    "newsletters_inactive": ("autre_supprime", "proposee"),
    "promo_pending": ("offre", "active"),
    "booking_past": ("evenement", "proposee"),
    "daily_digests": ("autre_supprime", "active"),
    "review_delete": ("autre_supprime", "active"),
    "social_pending": ("social", "active"),
}


@dataclass
class RapportImport:
    ajoutees: int = 0
    deja_presentes: int = 0
    ignorees: list[str] = field(default_factory=list)


def regles_v1(regles: dict, liste_blanche: dict) -> tuple[list[tuple], list[str]]:
    """YAML v1 -> lignes (type, valeur, catégorie, groupe, état), et règles non reprises."""
    lignes, ignorees = [], []
    for r in regles.get("rules") or []:
        groupe = r.get("id", "")
        categorie, etat = CORRESPONDANCE_V1.get(groupe, ("a_trier", "proposee"))
        avant = len(lignes)
        # la v1 cherchait les mots-clés dans l'objet et le début du corps
        lignes += [("mot_cle", v, categorie, groupe, etat) for v in r.get("keywords") or []]
        lignes += [("expediteur", v, categorie, groupe, etat) for v in r.get("senders") or []]
        lignes += [("exception_domaine", v, categorie, groupe, etat) for v in r.get("except_domains") or []]
        if len(lignes) == avant:
            ignorees.append(f"{groupe} (conditions sans valeur, non reprises)")
    noire = regles.get("blacklist") or {}
    lignes += [("expediteur", v, "autre_supprime", "blacklist", "active") for v in noire.get("senders") or []]
    lignes += [("domaine", v, "autre_supprime", "blacklist", "active") for v in noire.get("domains") or []]
    blanche = liste_blanche.get("whitelist") or {}
    lignes += [("domaine", v, LISTE_BLANCHE, "whitelist", "active") for v in blanche.get("domains") or []]
    lignes += [("expediteur", v, LISTE_BLANCHE, "whitelist", "active") for v in blanche.get("senders") or []]
    lignes += [("mot_cle_objet", v, LISTE_BLANCHE, "whitelist", "active") for v in blanche.get("keywords_subject") or []]
    return [l for l in lignes if str(l[1]).strip()], ignorees


def importer(
    con: sqlite3.Connection,
    fichier_regles: Path = config.FICHIER_REGLES,
    fichier_liste_blanche: Path = config.FICHIER_LISTE_BLANCHE,
) -> RapportImport:
    """Charge les règles v1 dans `regles` (origine migrée) ; relançable sans doublon."""
    regles = yaml.safe_load(fichier_regles.read_text(encoding="utf-8")) or {}
    blanche = yaml.safe_load(fichier_liste_blanche.read_text(encoding="utf-8")) or {}
    lignes, ignorees = regles_v1(regles, blanche)
    rapport = RapportImport(ignorees=ignorees)
    quand = maintenant()
    for type_, valeur, categorie, groupe, etat in lignes:
        cur = con.execute(
            """INSERT INTO regles (type, valeur, categorie, groupe, origine, etat, cree_le)
               VALUES (?, ?, ?, ?, 'migree', ?, ?)
               ON CONFLICT (type, valeur, categorie, groupe) DO NOTHING""",
            (type_, str(valeur), categorie, groupe, etat, quand),
        )
        if cur.rowcount:
            rapport.ajoutees += 1
        else:
            rapport.deja_presentes += 1
    con.commit()
    return rapport


# --- application aux mails ---------------------------------------------------

# du plus précis au plus large ; à type égal, ordre de création
ORDRE_TYPES = ("expediteur", "domaine", "mot_cle_objet", "mot_cle")
# la v1 cherchait les mots-clés dans les 300 premiers mots du corps
APERCU_CORPS = 2000


@dataclass(frozen=True)
class Regle:
    id: int
    type: str
    valeur: str
    categorie: str
    groupe: str
    etat: str


@dataclass
class Correspondance:
    liste_blanche: bool = False
    regle: Regle | None = None  # règle active qui décide
    proposees: list[Regle] = field(default_factory=list)  # règles proposées qui auraient décidé


def charger(con: sqlite3.Connection) -> list[Regle]:
    """Règles actives et proposées (les refusées sont ignorées)."""
    lignes = con.execute(
        "SELECT id, type, valeur, categorie, groupe, etat FROM regles WHERE etat IN ('active', 'proposee')"
    ).fetchall()
    return [Regle(*l) for l in lignes]


def domaine(expediteur: str | None) -> str:
    adresse = parseaddr(expediteur or "")[1]
    return adresse.rpartition("@")[2].lower() if "@" in adresse else ""


def _dans_domaine(dom: str, valeur: str) -> bool:
    valeur = valeur.lower().lstrip("@.")
    return bool(dom) and (dom == valeur or dom.endswith("." + valeur))


def correspond(r: Regle, expediteur: str, dom: str, objet: str, texte: str) -> bool:
    v = r.valeur.lower()
    if r.type == "expediteur":
        return v in expediteur
    if r.type == "domaine":
        return _dans_domaine(dom, v)
    if r.type == "mot_cle_objet":
        return v in objet
    if r.type == "mot_cle":
        return v in texte
    return False


def appliquer(regles: list[Regle], expediteur: str | None, objet: str | None, corps: str | None) -> Correspondance:
    """Liste blanche d'abord (le mail ira toujours à l'IA), puis la première règle active qui
    correspond ; une exception de domaine neutralise les règles de son groupe."""
    exp = (expediteur or "").lower()
    dom = domaine(expediteur)
    obj = (objet or "").lower()
    texte = f"{obj} {(corps or '')[:APERCU_CORPS].lower()}"
    exemptes = {r.groupe for r in regles if r.type == "exception_domaine" and _dans_domaine(dom, r.valeur)}
    resultat = Correspondance()
    candidates = sorted(
        (r for r in regles if r.type in ORDRE_TYPES and r.groupe not in exemptes),
        key=lambda r: (ORDRE_TYPES.index(r.type), r.id),
    )
    for r in candidates:
        if not correspond(r, exp, dom, obj, texte):
            continue
        if r.categorie == LISTE_BLANCHE:
            resultat.liste_blanche = True
        elif r.etat == "proposee":
            if all(p.groupe != r.groupe for p in resultat.proposees):
                resultat.proposees.append(r)
        elif resultat.regle is None:
            resultat.regle = r
    if resultat.liste_blanche:
        resultat.regle = None
    return resultat
