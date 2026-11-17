"""Interface commune aux adaptateurs de boîtes, et connexion IMAP.

Jusqu'au jalon 4, toute boîte s'ouvre en lecture seule (EXAMINE).
"""

import base64
import imaplib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

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


@dataclass
class EtatBoite:
    messages: int
    uidvalidity: int


class Boite(ABC):
    """Ce que le reste du code attend d'une boîte, quel que soit le fournisseur."""

    def __init__(self, compte: Compte):
        self.compte = compte

    @abstractmethod
    def dossiers_speciaux(self) -> dict[str, str]: ...

    @abstractmethod
    def examiner(self, dossier: str = "INBOX") -> EtatBoite: ...

    @abstractmethod
    def fermer(self) -> None: ...
