"""Adaptateur Gmail (IMAP, extensions X-GM-*), en lecture seule jusqu'au jalon 4."""

import re
from datetime import datetime, timezone

from postroom.boites.base import (
    Boite,
    Brut,
    EtatBoite,
    LectureSeule,
    analyser_list,
    decoder_utf7,
    nom_imap,
    ouvrir_imap,
)
from postroom.config import Compte

MOIS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

ITEMS_LEGERS = "(UID X-GM-MSGID X-GM-LABELS)"
ITEMS_COMPLETS = "(UID X-GM-MSGID X-GM-THRID X-GM-LABELS RFC822.SIZE INTERNALDATE BODY.PEEK[])"

_DEBUT = re.compile(rb"^\d+ \(")
_LITTERAL_CORPS = re.compile(rb"BODY\[\] \{\d+\}$")
_LITTERAL = re.compile(rb"\{\d+\}$")
_NOMBRE = {
    "uid": re.compile(rb"\bUID (\d+)"),
    "gmail_msgid": re.compile(rb"\bX-GM-MSGID (\d+)"),
    "thread_id": re.compile(rb"\bX-GM-THRID (\d+)"),
    "taille": re.compile(rb"\bRFC822\.SIZE (\d+)"),
}
_DATE_INTERNE = re.compile(rb'INTERNALDATE "(\d{1,2})-(\w{3})-(\d{4}) (\d\d):(\d\d):(\d\d) ([+-]\d{4})"')


def date_imap(jour) -> str:
    """Date au format IMAP (SEARCH SINCE), indépendante de la locale."""
    return f"{jour.day:02d}-{MOIS[jour.month - 1]}-{jour.year}"


def _regrouper(donnees) -> list[tuple[bytes, bytes | None]]:
    """Réponse brute de imaplib -> [(en-tête FETCH aplati, contenu du message ou None)]."""
    messages: list[list] = []
    for item in donnees or []:
        if isinstance(item, tuple):
            tete, litteral = item
            if _DEBUT.match(tete) or not messages:
                messages.append([b"", None])
            if _LITTERAL_CORPS.search(tete):
                messages[-1][0] += tete
                messages[-1][1] = litteral
            else:  # littéral dans un libellé : réinjecté comme chaîne entre guillemets
                echappe = litteral.replace(b"\\", b"\\\\").replace(b'"', b'\\"')
                messages[-1][0] += _LITTERAL.sub(b"", tete) + b'"' + echappe + b'"'
        elif isinstance(item, bytes):
            if _DEBUT.match(item):
                messages.append([item, None])
            elif messages:
                messages[-1][0] += item
    return [(t, c) for t, c in messages]


def _libelles(tete: bytes) -> list[str]:
    debut = tete.find(b"X-GM-LABELS (")
    if debut < 0:
        return []
    texte = tete[debut + len(b"X-GM-LABELS (") :].decode("utf-8", errors="replace")
    libelles, i = [], 0
    while i < len(texte) and texte[i] != ")":
        if texte[i] == " ":
            i += 1
        elif texte[i] == '"':
            j, morceaux = i + 1, []
            while j < len(texte) and texte[j] != '"':
                if texte[j] == "\\" and j + 1 < len(texte):
                    j += 1
                morceaux.append(texte[j])
                j += 1
            libelles.append(decoder_utf7("".join(morceaux)))
            i = j + 1
        else:
            j = i
            while j < len(texte) and texte[j] not in " )":
                j += 1
            libelles.append(decoder_utf7(texte[i:j]))
            i = j
    return libelles


def _date_interne(tete: bytes) -> str | None:
    m = _DATE_INTERNE.search(tete)
    if not m:
        return None
    jour, mois, annee, h, mi, s, decalage = (g.decode() for g in m.groups())
    try:
        d = datetime.strptime(
            f"{annee}-{MOIS.index(mois) + 1:02d}-{int(jour):02d} {h}:{mi}:{s} {decalage}", "%Y-%m-%d %H:%M:%S %z"
        )
    except ValueError:
        return None
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def analyser_fetch(donnees) -> list[Brut]:
    resultat = []
    for tete, contenu in _regrouper(donnees):
        nombres = {}
        for cle, motif in _NOMBRE.items():
            m = motif.search(tete)
            nombres[cle] = int(m[1]) if m else None
        if nombres["uid"] is None or nombres["gmail_msgid"] is None:
            continue
        resultat.append(
            Brut(
                uid=nombres["uid"],
                gmail_msgid=nombres["gmail_msgid"],
                thread_id=str(nombres["thread_id"]) if nombres["thread_id"] is not None else None,
                libelles=_libelles(tete),
                taille=nombres["taille"],
                date_interne=_date_interne(tete),
                contenu=contenu,
            )
        )
    return resultat


class BoiteGmail(Boite):
    def __init__(self, compte: Compte, ouvrir=ouvrir_imap):
        super().__init__(compte)
        self.imap = LectureSeule(ouvrir(compte))

    def dossiers_speciaux(self) -> dict[str, str]:
        typ, lignes = self.imap.list()
        return analyser_list(lignes) if typ == "OK" else {}

    def examiner(self, dossier: str = "INBOX") -> EtatBoite:
        typ, donnees = self.imap.select(nom_imap(dossier), readonly=True)
        if typ != "OK":
            raise RuntimeError(f"EXAMINE {dossier} : {donnees!r}")
        _, uv = self.imap.response("UIDVALIDITY")
        return EtatBoite(messages=int(donnees[0]), uidvalidity=int(uv[0]) if uv and uv[0] else 0)

    def uids_depuis(self, jour) -> list[int]:
        typ, donnees = self.imap.uid("SEARCH", None, "SINCE", date_imap(jour))
        if typ != "OK":
            raise RuntimeError(f"SEARCH : {donnees!r}")
        return sorted(int(u) for u in (donnees[0] or b"").split())

    def _fetch(self, uids: list[int], items: str) -> list[Brut]:
        if not uids:
            return []
        typ, donnees = self.imap.uid("FETCH", ",".join(map(str, uids)), items)
        if typ != "OK":
            raise RuntimeError(f"FETCH : {donnees!r}")
        return analyser_fetch(donnees)

    def identifier(self, uids: list[int]) -> list[Brut]:
        return self._fetch(uids, ITEMS_LEGERS)

    def lire(self, uids: list[int]) -> list[Brut]:
        return self._fetch(uids, ITEMS_COMPLETS)

    def fermer(self) -> None:
        try:
            self.imap.logout()
        except Exception:
            pass
