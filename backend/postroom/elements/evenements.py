"""Éléments : événements et échéances, avec compte à rebours jusqu'à la date.

L'élément expire à la fin du jour de l'événement ; sans date, quelques jours après le mail.
Un entretien ou un test de candidature crée aussi un événement (voir candidatures.py).
"""

from postroom.elements.commun import Mail, apres, element, fin_du_jour, propre, rattacher


def creer(con, m: Mail, cle: str, titre: str | None, jour: str | None, reglages: dict, *, sous_type: str,
          sous_titre: str | None = None) -> int:
    url, domaine = m.lien()
    element_id = element(
        con, m.compte, "evenement", cle, sous_type=sous_type, categorie=m.categorie.cle, titre=titre,
        sous_titre=sous_titre, resume=m.resume or None, lien=url, domaine=domaine, echeance=jour,
        expire_le=fin_du_jour(jour) if jour else apres(m.date, reglages["evenement_sans_date_jours"]),
        mail_id=m.id, date=m.date,
    )
    rattacher(con, element_id, m.id)
    return element_id


def traiter(con, m: Mail, reglages: dict) -> int:
    return creer(con, m, f"mail:{m.id}", m.objet, propre(m.extraction.get("date")), reglages,
                 sous_type=m.categorie.cle)
