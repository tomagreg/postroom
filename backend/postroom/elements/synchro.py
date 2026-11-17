"""Synchronisation Gmail -> Postroom, en lecture seule : quels mails sont encore dans la boîte ?

Un mail reçu qui n'est plus dans le dossier principal (archivé ou supprimé par Toma dans Gmail)
passe en « ailleurs » ; il redevient « inbox » s'il y revient. Les emplacements fixés par
Postroom lui-même (Conservés, Suppression, Corbeille) ne sont pas touchés.
"""

from dataclasses import dataclass
from datetime import timedelta

from postroom.boites.base import Boite
from postroom.config import Compte
from postroom.elements.commun import instant

MARGE_JOURS = 2  # SEARCH SINCE porte sur la date de réception du serveur, pas sur l'en-tête Date
LOT = 500


@dataclass
class RapportSynchro:
    verifies: int = 0
    presents: int = 0
    partis: int = 0
    revenus: int = 0


def presence(con, compte: Compte, boite: Boite) -> RapportSynchro:
    r = RapportSynchro()
    mails = con.execute(
        """SELECT id, gmail_msgid, date, emplacement FROM mails
           WHERE compte = ? AND dossier = 'principal' AND emplacement IN ('inbox', 'ailleurs')""", (compte.cle,)
    ).fetchall()
    dates = [d for d in (instant(m["date"]) for m in mails) if d]
    if not dates:
        return r
    boite.examiner(compte.dossier)
    uids = boite.uids_depuis((min(dates) - timedelta(days=MARGE_JOURS)).date())
    presents = set()
    for i in range(0, len(uids), LOT):
        presents |= {b.gmail_msgid for b in boite.identifier(uids[i:i + LOT])}
    for m in mails:
        r.verifies += 1
        ici = m["gmail_msgid"] in presents
        r.presents += ici
        if ici and m["emplacement"] == "ailleurs":
            con.execute("UPDATE mails SET emplacement = 'inbox' WHERE id = ?", (m["id"],))
            r.revenus += 1
        elif not ici and m["emplacement"] == "inbox":
            con.execute("UPDATE mails SET emplacement = 'ailleurs' WHERE id = ?", (m["id"],))
            r.partis += 1
    con.commit()
    return r
