"""Éléments : En attente. Chaque mail envoyé ouvre un cycle, clos par une réponse.

Une réponse se rattache par le fil Gmail, par In-Reply-To ou par References. Un élément par fil,
pour le dernier mail envoyé. Passé le délai « Avant Sans réponse » (échéance), le front propose
Relancer ou Archiver. Un mail envoyé à soi-même n'ouvre pas de cycle.
"""

import json
from datetime import datetime, timedelta

from postroom.elements.commun import adresse, element, iso, instant, rattacher, retirer
from postroom.elements.reponse import comptes_lies


def a_soi_meme(destinataires: list[str], adresses: set[str]) -> bool:
    return bool(destinataires) and all(adresse(d) in adresses for d in destinataires)


def _reponse(con, comptes: list[str], envoi, adresses: set[str]) -> str | None:
    """Date de la première réponse reçue après ce mail envoyé, ou None."""
    marques = ",".join("?" * len(comptes))
    conditions, params = ["in_reply_to = ?"], [envoi["message_id"] or "\0"]
    if envoi["message_id"]:
        conditions.append("instr(COALESCE(refs, ''), ?) > 0")
        params.append(envoi["message_id"])
    if envoi["thread_id"]:
        conditions.append("thread_id = ?")
        params.append(envoi["thread_id"])
    lignes = con.execute(
        f"""SELECT date, expediteur FROM mails WHERE dossier = 'principal' AND compte IN ({marques})
            AND ({' OR '.join(conditions)}) ORDER BY date""", (*comptes, *params)).fetchall()
    depart = instant(envoi["date"])
    for l in lignes:
        d = instant(l["date"])
        if d and depart and d > depart and adresse(l["expediteur"]) not in adresses:
            return l["date"]
    return None


def actualiser(con, compte: str, adresses: set[str], reglages: dict, quand: datetime) -> dict[str, int]:
    """Enregistre les envois de `compte`, ferme les cycles qui ont reçu une réponse et tient à jour
    un élément En attente par fil."""
    comptes = comptes_lies(con, compte)
    envois = con.execute(
        """SELECT id, thread_id, message_id, destinataires, objet, date FROM mails
           WHERE compte = ? AND dossier = 'envoyes' ORDER BY date""", (compte,)).fetchall()
    r = {"envoyes": len(envois), "a_soi": 0, "ouverts": 0, "sans_reponse": 0, "repondus": 0, "archives": 0}
    fils: dict[str, list] = {}
    for e in envois:
        destinataires = json.loads(e["destinataires"] or "[]")
        if a_soi_meme(destinataires, adresses):
            r["a_soi"] += 1
            continue
        autres = [d for d in destinataires if adresse(d) not in adresses]
        con.execute(
            """INSERT INTO envoyes (mail_id, destinataire, envoye_le, cycle_depuis) VALUES (?, ?, ?, ?)
               ON CONFLICT (mail_id) DO NOTHING""", (e["id"], (autres or destinataires or [None])[0], e["date"], e["date"]))
        fils.setdefault(e["thread_id"] or f"m{e['id']}", []).append(e)

    for fil, liste in fils.items():
        dernier = liste[-1]
        suivi = con.execute("SELECT * FROM envoyes WHERE mail_id = ?", (dernier["id"],)).fetchone()
        existant = con.execute(
            "SELECT id, mail_id, relance FROM elements WHERE compte = ? AND type = 'attente' AND cle = ?",
            (compte, f"fil:{fil}")).fetchone()
        repondu_le = suivi["repondu_le"] or _reponse(con, comptes, dernier, adresses)
        if repondu_le:
            con.execute("UPDATE envoyes SET repondu_le = ? WHERE id = ?", (repondu_le, suivi["id"]))
            if existant:
                retirer(con, existant["id"], "repondu", quand)
            for (rid,) in con.execute("SELECT id FROM elements WHERE compte = ? AND type = 'reponse' AND cle LIKE ?",
                                      (compte, f"relance:{suivi['id']}:%")).fetchall():
                retirer(con, rid, "repondu", quand)
            r["repondus"] += 1
            continue
        if suivi["archive_le"]:
            r["archives"] += 1
            continue
        nouveau_cycle = not existant or existant["mail_id"] != dernier["id"]
        relance = 0 if nouveau_cycle else existant["relance"]
        jours = reglages["relance_jours"] if relance else reglages["attente_jours"]
        echeance = iso(instant(suivi["cycle_depuis"]) + timedelta(days=jours))
        element_id = element(
            con, compte, "attente", f"fil:{fil}", titre=dernier["objet"], sous_titre=suivi["destinataire"],
            mail_id=dernier["id"], date=dernier["date"], echeance=echeance, relance=relance,
            **({"retire_le": None, "retire_motif": None, "supprime_le": None, "lu": 0} if nouveau_cycle else {}),
        )
        for e in liste:
            rattacher(con, element_id, e["id"])
        r["ouverts"] += 1
        r["sans_reponse"] += quand >= instant(echeance)
    return r


def _suivi(con, element_id: int):
    el = con.execute("SELECT * FROM elements WHERE id = ? AND type = 'attente'", (element_id,)).fetchone()
    if el is None:
        raise ValueError(f"élément En attente {element_id} introuvable")
    return el, con.execute("SELECT * FROM envoyes WHERE mail_id = ?", (el["mail_id"],)).fetchone()


def relancer(con, element_id: int, reglages: dict, quand: datetime) -> int:
    """Relancer : un élément À répondre avec le tag Relance, et un nouveau cycle de X jours.
    Renvoie l'id de l'élément À répondre."""
    el, suivi = _suivi(con, element_id)
    n = el["relance"] + 1
    con.execute("UPDATE envoyes SET cycle_depuis = ? WHERE id = ?", (iso(quand), suivi["id"]))
    con.execute("UPDATE elements SET relance = ?, echeance = ?, maj_le = ? WHERE id = ?",
                (n, iso(quand + timedelta(days=reglages["relance_jours"])), iso(quand), element_id))
    reponse_id = element(con, el["compte"], "reponse", f"relance:{suivi['id']}:{n}", sous_type="relance",
                         titre=el["titre"], sous_titre=el["sous_titre"], priorite="normale", relance=1,
                         mail_id=el["mail_id"], date=iso(quand))
    rattacher(con, reponse_id, el["mail_id"])
    con.commit()
    return reponse_id


def archiver(con, element_id: int, quand: datetime) -> None:
    """Archiver : le cycle est clos, l'élément disparaît, le mail reste dans Envoyés."""
    el, suivi = _suivi(con, element_id)
    con.execute("UPDATE envoyes SET archive_le = ? WHERE id = ?", (iso(quand), suivi["id"]))
    retirer(con, element_id, "archive", quand)
    con.commit()
