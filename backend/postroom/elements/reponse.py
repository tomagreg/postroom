"""Éléments : À répondre. Un élément par fil Gmail tant qu'un mail du fil attend une réponse.

L'élément disparaît quand Toma répond dans le fil (mail dans Envoyés plus récent) ou quand le mail
n'est plus dans la boîte de réception (archivé ou supprimé dans Gmail) ; il revient si un nouveau
mail arrive dans le fil.
"""

import json
from datetime import datetime

from postroom.elements.commun import element, instant, nom_expediteur, rattacher, retirer

RANG = {"haute": 0, "normale": 1, "basse": 2}


def comptes_lies(con, compte: str) -> list[str]:
    """Comptes qui partagent la même boîte Gmail (cours est lu dans perso1) : mêmes fils, mêmes Envoyés."""
    via = con.execute("SELECT via FROM comptes WHERE cle = ?", (compte,)).fetchone()
    racine = (via[0] if via and via[0] else compte)
    return [racine] + [r[0] for r in con.execute("SELECT cle FROM comptes WHERE via = ?", (racine,))]


def dernier_envoi(con, comptes: list[str], fil: str, message_ids: list[str]) -> datetime | None:
    """Date du dernier mail envoyé par Toma dans ce fil (même X-GM-THRID, ou réponse à l'un de ses mails)."""
    marques = ",".join("?" * len(comptes))
    dates = [r[0] for r in con.execute(
        f"SELECT date FROM mails WHERE dossier = 'envoyes' AND compte IN ({marques}) AND thread_id = ?",
        (*comptes, fil))]
    for mid in filter(None, message_ids):
        dates += [r[0] for r in con.execute(
            f"""SELECT date FROM mails WHERE dossier = 'envoyes' AND compte IN ({marques})
                AND (in_reply_to = ? OR instr(COALESCE(refs, ''), ?) > 0)""", (*comptes, mid, mid))]
    instants = [d for d in map(instant, dates) if d]
    return max(instants) if instants else None


def actualiser(con, compte: str, quand: datetime) -> dict[str, int]:
    """Crée, met à jour, retire ou rouvre les éléments À répondre de `compte`, fil par fil."""
    comptes = comptes_lies(con, compte)
    lignes = con.execute(
        """SELECT m.id, m.thread_id, m.message_id, m.expediteur, m.objet, m.date, m.emplacement, d.priorite,
                  d.extraction, COALESCE(d.categorie, d.categorie_auto) AS categorie
           FROM mails m JOIN decisions d ON d.mail_id = m.id
           JOIN categories c ON c.cle = COALESCE(d.categorie, d.categorie_auto)
           WHERE m.compte = ? AND m.dossier = 'principal' AND c.comportement = 'reponse' AND c.active = 1""",
        (compte,),
    ).fetchall()
    fils: dict[str, list] = {}
    for l in lignes:
        fils.setdefault(l["thread_id"] or f"m{l['id']}", []).append(l)

    compte_r = {"ouverts": 0, "repondus": 0, "ailleurs": 0}
    for fil, mails in fils.items():
        mails.sort(key=lambda l: instant(l["date"]) or quand)
        envoi = dernier_envoi(con, comptes, fil, [l["message_id"] for l in mails])
        ouverts = [l for l in mails if l["emplacement"] == "inbox"
                   and (envoi is None or (instant(l["date"]) and instant(l["date"]) > envoi))]
        existant = con.execute(
            "SELECT id, mail_id, retire_le, supprime_le FROM elements WHERE compte = ? AND type = 'reponse' AND cle = ?",
            (compte, f"fil:{fil}")).fetchone()
        if not ouverts:
            if existant and existant["retire_le"] is None:
                repondu = envoi is not None and envoi >= (instant(mails[-1]["date"]) or envoi)
                retirer(con, existant["id"], "repondu" if repondu else "ailleurs", quand)
            compte_r["repondus" if envoi else "ailleurs"] += 1
            continue
        dernier = ouverts[-1]
        if existant and existant["supprime_le"] and existant["mail_id"] == dernier["id"]:
            continue  # ✕ de Toma : seul un nouveau mail le rouvre
        priorite = min((l["priorite"] for l in ouverts if l["priorite"] in RANG), key=RANG.get, default="normale")
        try:
            resume = (json.loads(dernier["extraction"] or "{}") or {}).get("resume") or None
        except ValueError:
            resume = None
        element_id = element(
            con, compte, "reponse", f"fil:{fil}", sous_type=dernier["categorie"], categorie=dernier["categorie"],
            titre=dernier["objet"], sous_titre=nom_expediteur(dernier["expediteur"]) or None, resume=resume,
            priorite=priorite, mail_id=dernier["id"], date=dernier["date"], retire_le=None, retire_motif=None,
            **({"supprime_le": None, "lu": 0} if existant and existant["mail_id"] != dernier["id"] else {}),
        )
        for l in mails:
            rattacher(con, element_id, l["id"])
        compte_r["ouverts"] += 1
    return compte_r
