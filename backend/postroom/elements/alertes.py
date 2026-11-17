"""Éléments : alertes (sécurité, compte), avec résumé, lien conservé et péremption du lien.

Sans péremption indiquée dans le mail, l'alerte expire quelques jours après sa réception.
"""

from postroom.elements.commun import Mail, apres, duree, element, fin_du_jour, propre, rattacher


def traiter(con, m: Mail, reglages: dict) -> int:
    url, domaine = m.lien()
    peremption = fin_du_jour(propre(m.extraction.get("peremption")))
    element_id = element(
        con, m.compte, "alerte", f"mail:{m.id}", sous_type=m.categorie.cle, categorie=m.categorie.cle,
        titre=m.objet, resume=m.resume or None, lien=url, domaine=domaine,
        expire_le=peremption or apres(m.date, duree(m.categorie, reglages, "alerte_jours")), mail_id=m.id,
        date=m.date,
    )
    rattacher(con, element_id, m.id)
    return element_id
