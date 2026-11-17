"""Vérification des boîtes en lecture seule (jalon 0) : connexion, dossiers spéciaux, EXAMINE."""

from dataclasses import dataclass, field

from postroom.boites.base import analyser_list, ouvrir_imap
from postroom.config import Compte

CAPACITES_UTILES = ("X-GM-EXT-1", "MOVE", "UIDPLUS", "SPECIAL-USE", "IDLE", "CONDSTORE")


@dataclass
class Resultat:
    cle: str
    statut: str  # ok · erreur · non configuré
    detail: str = ""
    messages: int | None = None
    uidvalidity: int | None = None
    speciaux: dict[str, str] = field(default_factory=dict)
    capacites: list[str] = field(default_factory=list)


def verifier(compte: Compte, ouvrir=ouvrir_imap) -> Resultat:
    if not compte.configure:
        return Resultat(compte.cle, "non configuré", "identifiants absents de accounts.env")
    try:
        imap = ouvrir(compte)
    except Exception as e:  # message du serveur, sans identifiant
        return Resultat(compte.cle, "erreur", f"connexion : {_message(e)}")
    try:
        # les capacités annoncées après LOGIN sont plus complètes (MOVE, UIDPLUS chez Gmail)
        typ, donnees = imap.capability()
        brut = donnees[0] if typ == "OK" and donnees else b""
        capacites = (brut.decode() if isinstance(brut, bytes) else str(brut)).split()
        typ, lignes = imap.list()
        speciaux = analyser_list(lignes) if typ == "OK" else {}
        dossier = compte.dossier if compte.dossier == "INBOX" else f'"{compte.dossier}"'
        typ, donnees = imap.select(dossier, readonly=True)  # EXAMINE
        if typ != "OK":
            return Resultat(compte.cle, "erreur", f"EXAMINE {compte.dossier} : {_message(donnees)}")
        _, uv = imap.response("UIDVALIDITY")
        return Resultat(
            compte.cle,
            "ok",
            messages=int(donnees[0]),
            uidvalidity=int(uv[0]) if uv and uv[0] else None,
            speciaux=speciaux,
            capacites=[c for c in CAPACITES_UTILES if c in capacites],
        )
    except Exception as e:
        return Resultat(compte.cle, "erreur", _message(e))
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _message(e) -> str:
    texte = e.args[0] if isinstance(e, Exception) and e.args else e
    if isinstance(texte, list) and texte:
        texte = texte[0]
    if isinstance(texte, bytes):
        texte = texte.decode(errors="replace")
    return str(texte)
