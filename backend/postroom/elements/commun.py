"""Outils communs aux éléments : réglages, dates, mail décidé, création et mise à jour d'un élément."""

import html
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parseaddr

from postroom.config import FUSEAU
from postroom.db import maintenant
from postroom.tri.categories import Categorie

# délais par défaut, remplacés par la table reglages
DEFAUTS = {
    "attente_jours": 5,
    "relance_jours": 5,
    "offre_jours": 14,
    "alerte_jours": 7,
    "commande_livree_jours": 7,
    "flux_jours": 90,
    "suggestion_jours": 14,
    "candidature_close_jours": 14,
    "simple_jours": 7,
    "evenement_sans_date_jours": 7,
    "expire_visible_jours": 1,
    "effacer_corps": 0,
}


def reglages(con: sqlite3.Connection) -> dict[str, int]:
    """Réglages numériques (délais en jours, interrupteurs)."""
    r = dict(DEFAUTS)
    for cle, valeur in con.execute("SELECT cle, valeur FROM reglages"):
        try:
            r[cle] = int(valeur)
        except ValueError:
            pass
    return r


def duree(categorie: Categorie, reglages_: dict[str, int], defaut: str) -> int:
    """Durée de vie d'un élément : celle de la catégorie, sinon le réglage « <cle>_jours »
    (social_jours), sinon le réglage du comportement."""
    if categorie.duree_jours is not None:
        return categorie.duree_jours
    return reglages_.get(f"{categorie.cle}_jours", reglages_[defaut])


# --- dates (ISO UTC en base) -------------------------------------------------


def instant(valeur: str | datetime | None) -> datetime | None:
    if valeur is None or isinstance(valeur, datetime):
        return valeur
    try:
        d = datetime.fromisoformat(valeur)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def apres(depart: str | None, jours: int) -> str | None:
    d = instant(depart)
    return iso(d + timedelta(days=jours)) if d else None


def fin_du_jour(jour: str | None) -> str | None:
    """Fin d'une journée de Paris (AAAA-MM-JJ) : minuit le lendemain, en UTC."""
    try:
        j = date.fromisoformat(jour or "")
    except ValueError:
        return None
    return iso(datetime.combine(j + timedelta(days=1), time(0), FUSEAU))


# --- textes ------------------------------------------------------------------


def nom_expediteur(expediteur: str | None) -> str:
    nom, adresse = parseaddr(expediteur or "")
    return (nom or adresse or "").strip()


def adresse(valeur: str | None) -> str:
    """Adresse normalisée : minuscules, sans le « +étiquette » (tomagreg2+cours@ = tomagreg2@)."""
    a = parseaddr(valeur or "")[1].lower().strip()
    local, _, dom = a.partition("@")
    return f"{local.split('+')[0]}@{dom}" if dom else a


def normaliser(texte: str | None) -> str:
    """Minuscules, sans accents ni ponctuation, entités HTML décodées (pour comparer)."""
    t = unicodedata.normalize("NFKD", html.unescape(texte or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def propre(valeur) -> str | None:
    """Champ texte de l'IA : vide, « "" » ou « null » -> None."""
    v = html.unescape(str(valeur) if valeur is not None else "").strip().strip('"').strip()
    return v if v and v.lower() not in ("null", "none", "-") else None


# --- mail décidé -------------------------------------------------------------


@dataclass
class Mail:
    """Un mail reçu et sa décision de tri, prêt à devenir un élément."""
    id: int
    compte: str
    thread_id: str | None
    message_id: str | None
    expediteur: str | None
    objet: str | None
    date: str | None
    corps_texte: str | None
    liens: list
    a_pj: bool
    emplacement: str
    categorie: Categorie
    priorite: str | None
    extraction: dict = field(default_factory=dict)

    @property
    def fil(self) -> str:
        return self.thread_id or f"m{self.id}"

    @property
    def resume(self) -> str:
        return (self.extraction.get("resume") or "").strip()

    def lien(self) -> tuple[str | None, str | None]:
        """(url, domaine) du lien choisi par l'IA parmi les liens extraits."""
        i = self.extraction.get("lien")
        if isinstance(i, int) and 0 <= i < len(self.liens):
            l = self.liens[i]
            return l.get("url"), l.get("domaine")
        return None, None


def mail_depuis(ligne: sqlite3.Row, categorie: Categorie) -> Mail:
    try:
        extraction = json.loads(ligne["extraction"] or "{}") or {}
    except ValueError:
        extraction = {}
    return Mail(
        id=ligne["id"], compte=ligne["compte"], thread_id=ligne["thread_id"], message_id=ligne["message_id"],
        expediteur=ligne["expediteur"], objet=ligne["objet"], date=ligne["date"], corps_texte=ligne["corps_texte"],
        liens=json.loads(ligne["liens"] or "[]"), a_pj=bool(ligne["a_pj"]), emplacement=ligne["emplacement"],
        categorie=categorie, priorite=ligne["priorite"], extraction=extraction,
    )


# --- éléments ----------------------------------------------------------------


def element(con: sqlite3.Connection, compte: str, type_: str, cle: str, **champs) -> int:
    """Crée l'élément (compte, type, cle) ou met à jour les champs donnés ; renvoie son id."""
    ligne = con.execute("SELECT id FROM elements WHERE compte = ? AND type = ? AND cle = ?",
                        (compte, type_, cle)).fetchone()
    if ligne:
        if champs:
            colonnes = ", ".join(f"{c} = :{c}" for c in champs)
            con.execute(f"UPDATE elements SET {colonnes}, maj_le = :maj_le WHERE id = :id",
                        dict(champs, maj_le=maintenant(), id=ligne[0]))
        return ligne[0]
    champs = dict(champs, compte=compte, type=type_, cle=cle, cree_le=maintenant())
    cur = con.execute(f"INSERT INTO elements ({', '.join(champs)}) VALUES ({', '.join(':' + c for c in champs)})",
                      champs)
    return cur.lastrowid


def rattacher(con: sqlite3.Connection, element_id: int, mail_id: int) -> None:
    con.execute("INSERT OR IGNORE INTO element_mails (element_id, mail_id) VALUES (?, ?)", (element_id, mail_id))


def retirer(con: sqlite3.Connection, element_id: int, motif: str, quand: datetime) -> None:
    con.execute("UPDATE elements SET retire_le = ?, retire_motif = ? WHERE id = ? AND retire_le IS NULL",
                (iso(quand), motif, element_id))


def plus_recent(ancien: str | None, nouveau: str | None) -> bool:
    """Le mail `nouveau` est-il au moins aussi récent que celui qui a rempli l'élément ?"""
    a, n = instant(ancien), instant(nouveau)
    return a is None or (n is not None and n >= a)
