"""Catégories et rubriques, lues en base (migration 003).

Ajouter une catégorie ou une rubrique = ajouter une ligne. Le code ne fixe que les comportements,
c'est-à-dire ce qu'un élément sait faire (automate de commande, compte à rebours…).
"""

import sqlite3
from dataclasses import dataclass

COMPORTEMENTS = ("aucun", "reponse", "commande", "evenement", "alerte", "offre", "simple", "candidature")
A_TRIER = "a_trier"
LISTE_BLANCHE = "liste_blanche"


@dataclass(frozen=True)
class Categorie:
    cle: str
    nom: str
    description: str
    sort: str  # garder · suppression
    comportement: str
    rubrique: str | None
    flux: str | None
    choix_ia: bool
    duree_jours: int | None = None  # durée de vie des éléments (vide : réglage)


def charger(con: sqlite3.Connection) -> dict[str, Categorie]:
    """Catégories actives, dans l'ordre d'affichage."""
    lignes = con.execute(
        """SELECT cle, nom, description, sort, comportement, rubrique, flux, choix_ia, duree_jours
           FROM categories WHERE active = 1 ORDER BY ordre, cle"""
    ).fetchall()
    return {l[0]: Categorie(*l[:7], bool(l[7]), l[8]) for l in lignes}


def pour_ia(categories: dict[str, Categorie]) -> list[Categorie]:
    return [c for c in categories.values() if c.choix_ia]
