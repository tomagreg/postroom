"""Éléments : candidatures (stage, emploi), suivies par statut.

Les mails d'une même candidature sont regroupés par entreprise, puis par poste proche (ou par
référence quand le poste manque). Le statut est celui du mail le plus récent ; l'historique garde
chaque étape. Un entretien ou un test daté crée aussi un événement. Une suggestion de stage
(Indeed, LinkedIn…) est une carte seule, sans suivi, qui expire après quelques jours.
"""

import json

from postroom.elements import evenements
from postroom.elements.commun import (Mail, apres, duree, element, instant, nom_expediteur, normaliser, propre,
                                      rattacher)

ORDRE = ("suggestion", "autre", "envoyee", "recue", "test", "entretien", "proposition", "refus")
CLOS = ("refus",)
PEU_INFORMATIFS = ("suggestion", "autre", "envoyee")
AVEC_EVENEMENT = {"entretien": "Entretien", "test": "Test"}
_SUFFIXES = {"group", "groupe", "sa", "sas", "se", "france", "inc", "ltd", "gmbh"}
_MOTS_VIDES = {
    "stage", "stagiaire", "stages", "pfe", "h", "f", "x", "hf", "fh", "m", "bac", "5", "de", "du", "des", "la",
    "le", "les", "et", "en", "l", "d", "a", "au", "aux", "pour", "un", "une", "4e", "annee", "intern", "internship",
    "e", "trtg", "as", "24",
}
SEUIL_POSTE = 0.5


def cle_entreprise(entreprise: str | None) -> str:
    mots = normaliser(entreprise).split()
    while len(mots) > 1 and mots[-1] in _SUFFIXES:
        mots.pop()
    return " ".join(mots)


def mots_poste(poste: str | None) -> set[str]:
    return {m for m in normaliser(poste).split() if m not in _MOTS_VIDES}


def meme_poste(a: str | None, b: str | None) -> bool:
    ma, mb = mots_poste(a), mots_poste(b)
    if not ma or not mb:
        return False
    return ma <= mb or mb <= ma or len(ma & mb) / len(ma | mb) >= SEUIL_POSTE


def _existante(con, compte: str, entreprise: str, poste: str | None, reference: str | None):
    """Candidature déjà suivie qui correspond à ce mail, ou None."""
    lignes = con.execute(
        """SELECT e.id, e.cle, e.date, c.entreprise, c.poste, c.reference, c.statut, c.historique
           FROM elements e JOIN candidatures c ON c.element_id = e.id
           WHERE e.compte = ? AND e.type = 'candidature' AND e.cle LIKE ?""",
        (compte, f"candidature:{entreprise}:%"),
    ).fetchall()
    for l in lignes:
        if poste and meme_poste(poste, l["poste"]):
            return l
    if reference:  # la référence ne sert que si un des deux postes manque (l'IA la recopie parfois mal)
        for l in lignes:
            if (not poste or not l["poste"]) and l["reference"] \
                    and normaliser(l["reference"]) == normaliser(reference):
                return l
    if not poste and len(lignes) == 1:
        return lignes[0]
    return None


def _statut_courant(historique: list[dict]) -> str:
    """Statut du mail le plus récent ; à date égale, l'étape la plus avancée. Un accusé d'envoi
    (« envoyee », « autre ») n'efface pas une étape plus avancée déjà reçue."""
    zero = instant("1970-01-01T00:00:00+00:00")
    courant = None
    for h in sorted(historique, key=lambda h: (instant(h["date"]) or zero, ORDRE.index(h["statut"]))):
        if courant is None or h["statut"] not in PEU_INFORMATIFS \
                or ORDRE.index(h["statut"]) >= ORDRE.index(courant):
            courant = h["statut"]
    return courant


def traiter(con, m: Mail, reglages: dict) -> int:
    statut = m.extraction.get("statut") if m.extraction.get("statut") in ORDRE else "autre"
    entreprise = propre(m.extraction.get("entreprise"))
    poste = propre(m.extraction.get("poste"))
    reference = propre(m.extraction.get("reference"))
    url, domaine = m.lien()

    if statut == "suggestion" or not entreprise:
        element_id = element(
            con, m.compte, "candidature", f"mail:{m.id}", sous_type=statut, categorie=m.categorie.cle,
            titre=poste or m.objet, sous_titre=entreprise or nom_expediteur(m.expediteur) or None,
            resume=m.resume or None, lien=url, domaine=domaine,
            expire_le=apres(m.date, reglages["suggestion_jours"]) if statut == "suggestion" else None,
            mail_id=m.id, date=m.date,
        )
        rattacher(con, element_id, m.id)
        return element_id

    cle_e = cle_entreprise(entreprise)
    ancienne = _existante(con, m.compte, cle_e, poste, reference)
    if ancienne:
        cle = ancienne["cle"]
        historique = json.loads(ancienne["historique"] or "[]")
    else:
        rang = con.execute("SELECT COUNT(*) FROM elements WHERE compte = ? AND type = 'candidature' AND cle LIKE ?",
                           (m.compte, f"candidature:{cle_e}:%")).fetchone()[0]
        cle, historique = f"candidature:{cle_e}:{rang + 1}", []
    if not any(h["mail_id"] == m.id for h in historique):
        historique.append({"statut": statut, "date": m.date, "mail_id": m.id})
    courant = _statut_courant(historique)
    dernier = max(historique, key=lambda h: instant(h["date"]) or instant("1970-01-01T00:00:00+00:00"))

    champs = dict(sous_type=courant, categorie=m.categorie.cle,
                  titre=(ancienne["poste"] if ancienne else None) or poste or m.objet,
                  sous_titre=(ancienne["entreprise"] if ancienne else None) or entreprise,
                  expire_le=apres(dernier["date"], duree(m.categorie, reglages, "candidature_close_jours"))
                  if courant in CLOS else None)
    if not ancienne or dernier["mail_id"] == m.id:
        champs.update(resume=m.resume or None, mail_id=m.id, date=m.date)
        if url:
            champs.update(lien=url, domaine=domaine)
    element_id = element(con, m.compte, "candidature", cle, **champs)
    rattacher(con, element_id, m.id)
    con.execute(
        """INSERT INTO candidatures (element_id, entreprise, poste, reference, statut, historique)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (element_id) DO UPDATE SET poste = COALESCE(poste, excluded.poste),
             reference = COALESCE(reference, excluded.reference), statut = excluded.statut,
             historique = excluded.historique""",
        (element_id, entreprise, poste, reference, courant, json.dumps(historique, ensure_ascii=False)),
    )

    jour = propre(m.extraction.get("date"))
    if statut in AVEC_EVENEMENT and jour:
        evenements.creer(con, m, f"candidature:{element_id}:{statut}:{jour}",
                         f"{AVEC_EVENEMENT[statut]} — {champs['sous_titre']}", jour, reglages,
                         sous_type="candidature", sous_titre=champs["titre"])
    return element_id
