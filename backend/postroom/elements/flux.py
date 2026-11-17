"""Flux : une ligne par mail traité, journal de tout ce que Postroom a fait.

Le type vient de la catégorie (Paiements, Sécurité, Livraisons, Promos, Autres) ; une catégorie
sans type (code à usage unique, CGU…) donne une ligne « supprimé ». Lignes gardées 90 jours.
"""

import re

from postroom.elements.commun import Mail, nom_expediteur

ICONES = {"Paiements": "payments", "Sécurité": "shield", "Livraisons": "local_shipping", "Promos": "sell",
          "Autres": "mail"}


def texte(m: Mail) -> str:
    if m.categorie.flux is None:
        expediteur = nom_expediteur(m.expediteur)
        nom = re.sub(r"\s*\(.*\)\s*$", "", m.categorie.nom)  # « Autre (supprimé) » -> « Autre »
        return f"{nom}{f' — {expediteur}' if expediteur else ''} — supprimé"
    return m.resume or m.objet or m.categorie.nom


def ajouter(con, m: Mail) -> None:
    type_ = m.categorie.flux or "Autres"
    con.execute(
        """INSERT INTO flux (mail_id, compte, heure, type, icone, texte) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (mail_id) DO UPDATE SET type = excluded.type, icone = excluded.icone, texte = excluded.texte""",
        (m.id, m.compte, m.date, type_, "delete" if m.categorie.flux is None else ICONES[type_], texte(m)),
    )
