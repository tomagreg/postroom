"""Interface commune aux adaptateurs de boîtes, et connexion IMAP.

Jusqu'au jalon 4, toute boîte s'ouvre en lecture seule (EXAMINE).
"""

import base64
import imaplib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from postroom.config import Compte

USAGES_SPECIAUX = ("\\Sent", "\\Trash", "\\All", "\\Junk", "\\Drafts")

_LIGNE_LIST = re.compile(rb'\((?P<attributs>[^)]*)\) (?P<sep>"[^"]*"|NIL) (?P<nom>.+)')


def decoder_utf7(nom: str) -> str:
    """Décode un nom de dossier IMAP en UTF-7 modifié (RFC 3501, § 5.1.3)."""
    resultat, i = [], 0
    while i < len(nom):
        if nom[i] == "&":
            fin = nom.index("-", i)
            if fin == i + 1:
                resultat.append("&")
            else:
                b64 = nom[i + 1 : fin].replace(",", "/")
                b64 += "=" * (-len(b64) % 4)
                resultat.append(base64.b64decode(b64).decode("utf-16-be"))
            i = fin + 1
        else:
            resultat.append(nom[i])
            i += 1
    return "".join(resultat)


def encoder_utf7(nom: str) -> str:
    """Encode un nom de dossier en UTF-7 modifié (inverse de decoder_utf7)."""
    resultat, tampon = [], []

    def vider():
        if tampon:
            b64 = base64.b64encode("".join(tampon).encode("utf-16-be")).decode().rstrip("=")
            resultat.append("&" + b64.replace("/", ",") + "-")
            tampon.clear()

    for car in nom:
        if 0x20 <= ord(car) <= 0x7E:
            vider()
            resultat.append("&-" if car == "&" else car)
        else:
            tampon.append(car)
    vider()
    return "".join(resultat)


def nom_imap(dossier: str) -> str:
    """Nom de dossier prêt pour une commande IMAP : UTF-7 modifié, entre guillemets si besoin."""
    if dossier == "INBOX":
        return dossier
    encode = encoder_utf7(dossier).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{encode}"'


def analyser_list(lignes: list[bytes]) -> dict[str, str]:
    """Réponse LIST -> {attribut SPECIAL-USE: nom du dossier décodé}."""
    speciaux = {}
    for ligne in lignes:
        if not isinstance(ligne, bytes):
            continue
        m = _LIGNE_LIST.match(ligne)
        if not m:
            continue
        attributs = m["attributs"].decode().split()
        nom = m["nom"].decode().strip()
        if nom.startswith('"') and nom.endswith('"'):
            nom = nom[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        for a in attributs:
            if a in USAGES_SPECIAUX:
                speciaux[a] = decoder_utf7(nom)
    return speciaux


def ouvrir_imap(compte: Compte, *, delai: float = 30) -> imaplib.IMAP4_SSL:
    """Connexion TLS authentifiée : mot de passe d'application, ou XOAUTH2 (Microsoft 365)."""
    if compte.auth == "oauth2":
        from postroom.boites.oauth import jeton

        acces = jeton(compte)  # avant d'ouvrir la connexion : erreur claire si pas de jeton
        imap = imaplib.IMAP4_SSL(compte.hote, compte.port, timeout=delai)
        chaine = f"user={compte.utilisateur}\x01auth=Bearer {acces}\x01\x01".encode()
        imap.authenticate("XOAUTH2", lambda _: chaine)
        return imap
    imap = imaplib.IMAP4_SSL(compte.hote, compte.port, timeout=delai)
    imap.login(compte.utilisateur, compte.mot_de_passe)
    return imap


class ErreurEcriture(RuntimeError):
    """Commande IMAP qui modifierait la boîte, refusée en lecture seule."""


class LectureSeule:
    """Enveloppe d'une connexion imaplib qui ne laisse passer que la lecture.

    Jusqu'au jalon 4, aucune commande ne peut modifier une boîte : SELECT est refusé
    (EXAMINE seulement), UID n'accepte que SEARCH et FETCH, et FETCH n'accepte que
    BODY.PEEK (qui ne pose pas \\Seen).
    """

    PERMISES = {"capability", "list", "response", "noop", "logout"}

    def __init__(self, imap):
        self._imap = imap

    def select(self, dossier="INBOX", readonly=False):
        if not readonly:
            raise ErreurEcriture("SELECT refusé : EXAMINE uniquement")
        return self._imap.select(dossier, readonly=True)

    def uid(self, commande, *args):
        commande = commande.upper()
        if commande not in ("SEARCH", "FETCH"):
            raise ErreurEcriture(f"UID {commande} refusé en lecture seule")
        if commande == "FETCH":
            elements = " ".join(str(a) for a in args[1:]).upper()
            if re.search(r"BODY\[|RFC822(?![.]SIZE)", elements.replace("BODY.PEEK[", "")):
                raise ErreurEcriture("FETCH sans PEEK refusé (poserait \\Seen)")
        return self._imap.uid(commande, *args)

    def __getattr__(self, nom):
        if nom in self.PERMISES:
            return getattr(self._imap, nom)
        raise ErreurEcriture(f"{nom} refusé en lecture seule")


@dataclass
class EtatBoite:
    messages: int
    uidvalidity: int


@dataclass
class Brut:
    """Un message tel que lu sur la boîte, avant analyse."""

    uid: int
    gmail_msgid: int
    thread_id: str | None = None
    libelles: list[str] = field(default_factory=list)
    taille: int | None = None
    date_interne: str | None = None  # INTERNALDATE, ISO UTC
    contenu: bytes | None = None


class Boite(ABC):
    """Ce que le reste du code attend d'une boîte, quel que soit le fournisseur."""

    def __init__(self, compte: Compte):
        self.compte = compte

    @abstractmethod
    def dossiers_speciaux(self) -> dict[str, str]: ...

    @abstractmethod
    def examiner(self, dossier: str = "INBOX") -> EtatBoite: ...

    @abstractmethod
    def uids_depuis(self, jour) -> list[int]:
        """UID du dossier examiné reçus depuis ce jour (datetime.date)."""

    @abstractmethod
    def identifier(self, uids: list[int]) -> list[Brut]:
        """Identifiants et libellés seulement (lecture légère, sans contenu)."""

    @abstractmethod
    def lire(self, uids: list[int]) -> list[Brut]:
        """Messages complets, sans changer leur état (BODY.PEEK)."""

    @abstractmethod
    def fermer(self) -> None: ...
