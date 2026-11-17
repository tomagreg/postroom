"""Éléments : offres (promos, veille, notifications sociales) et cartes simples.

Une offre expire 14 jours après le mail (7 pour une notification sociale, réglage social_jours),
ou à sa péremption si elle est plus proche.
"""

from postroom.elements.commun import (Mail, apres, duree, element, fin_du_jour, instant, nom_expediteur, propre,
                                      rattacher)


def _carte(con, m: Mail, type_: str, expire_le: str | None) -> int:
    url, domaine = m.lien()
    element_id = element(
        con, m.compte, type_, f"mail:{m.id}", sous_type=m.categorie.cle, categorie=m.categorie.cle, titre=m.objet,
        sous_titre=nom_expediteur(m.expediteur) or None, resume=m.resume or None, lien=url, domaine=domaine,
        expire_le=expire_le, mail_id=m.id, date=m.date,
    )
    rattacher(con, element_id, m.id)
    return element_id


def traiter(con, m: Mail, reglages: dict) -> int:
    expire_le = apres(m.date, duree(m.categorie, reglages, "offre_jours"))
    peremption = fin_du_jour(propre(m.extraction.get("peremption")))
    if peremption and expire_le and instant(peremption) < instant(expire_le):
        expire_le = peremption
    return _carte(con, m, "offre", expire_le)


def traiter_simple(con, m: Mail, reglages: dict) -> int:
    return _carte(con, m, "simple", apres(m.date, duree(m.categorie, reglages, "simple_jours")))
