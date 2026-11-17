"""Ingestion : analyse d'un message brut et lecture d'une boîte vers la base.

Le HTML des mails n'est jamais stocké : on en extrait du texte brut et des liens.
Seules les métadonnées des pièces jointes sont gardées.
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta, timezone
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

from postroom.boites.base import Boite, Brut
from postroom.config import Compte
from postroom.db import maintenant

CORPS_MAX = 100_000
LIENS_MAX = 50
URL_MAX = 2000
LOT_LEGER = 500

_URL_TEXTE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_BLOCS = {"p", "div", "br", "tr", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "hr"}
_MASQUES = {"script", "style", "head", "title", "noscript", "template"}
_BALISES = re.compile(r"<(?:html|body|div|table|td|br|p|span|a)\b[^>]*>", re.IGNORECASE)


@dataclass
class Analyse:
    message_id: str | None = None
    in_reply_to: str | None = None
    refs: str | None = None
    expediteur: str | None = None
    destinataires: list[str] = field(default_factory=list)
    objet: str | None = None
    date: str | None = None
    corps_texte: str = ""
    liens: list[dict] = field(default_factory=list)
    pieces_jointes: list[dict] = field(default_factory=list)


class _Texte(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.morceaux: list[str] = []
        self.liens: list[str] = []
        self._masque = 0

    def handle_starttag(self, tag, attrs):
        if tag == "body":  # un <head> mal refermé ne doit pas masquer tout le corps
            self._masque = 0
        elif tag in _MASQUES:
            self._masque += 1
        elif tag in _BLOCS:
            self.morceaux.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.liens.append(href.strip())

    def handle_endtag(self, tag):
        if tag in _MASQUES:
            self._masque = max(0, self._masque - 1)
        elif tag in _BLOCS:
            self.morceaux.append("\n")

    def handle_data(self, data):
        if not self._masque:
            self.morceaux.append(data)


def html_vers_texte(html: str) -> tuple[str, list[str]]:
    p = _Texte()
    try:
        p.feed(html)
        p.close()
    except Exception:  # HTML très abîmé : on garde ce qui a été lu
        pass
    return nettoyer("".join(p.morceaux)), p.liens


def nettoyer(texte: str) -> str:
    lignes = [re.sub(r"[ \t ​‌͏]+", " ", l).strip() for l in texte.replace("\r", "").split("\n")]
    texte = "\n".join(lignes)
    return re.sub(r"\n{3,}", "\n\n", texte).strip()


def _en_tete(msg, nom: str) -> str | None:
    try:
        valeur = msg.get(nom)
    except Exception:
        valeur = None
        brut = msg.get_all(nom, failobj=[]) if hasattr(msg, "get_all") else []
        if brut:
            try:
                valeur = str(make_header(decode_header(str(brut[0]))))
            except Exception:
                valeur = str(brut[0])
    if valeur is None:
        return None
    return " ".join(str(valeur).split()) or None


def _date(msg, repli: str | None) -> str | None:
    brut = _en_tete(msg, "Date")
    if brut:
        try:
            d = parsedate_to_datetime(brut)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError, IndexError, OverflowError):
            pass
    return repli


def _adresses(msg, *noms: str) -> list[str]:
    valeurs = []
    for nom in noms:
        try:
            valeurs += [str(v) for v in msg.get_all(nom, failobj=[])]
        except Exception:
            continue
    return [a.lower() for _, a in getaddresses(valeurs) if a]


def _contenu_texte(part) -> str:
    try:
        return part.get_content()
    except Exception:
        donnees = part.get_payload(decode=True) or b""
        for charset in (part.get_content_charset() or "utf-8", "utf-8", "latin-1"):
            try:
                return donnees.decode(charset, errors="replace")
            except LookupError:
                continue
        return donnees.decode("latin-1")


def _est_integree(part) -> bool:
    """Image affichée dans le corps (logo, signature), pas une vraie pièce jointe."""
    return part.get_content_maintype() == "image" and (
        bool(part.get("Content-ID")) or part.get_content_disposition() == "inline"
    )


def _parcourir(part, textes, htmls, pjs):
    ctype = part.get_content_type()
    disposition = part.get_content_disposition()
    nom = None
    try:
        nom = part.get_filename()
    except Exception:
        pass
    if ctype == "message/rfc822":  # mail transféré en pièce jointe
        pjs.append({"nom": nom or "message.eml", "taille": len(part.as_bytes()), "type": ctype})
        return
    if part.is_multipart():
        for sous in part.iter_parts():
            _parcourir(sous, textes, htmls, pjs)
        return
    if disposition == "attachment" or nom:
        if not _est_integree(part):
            donnees = part.get_payload(decode=True) or b""
            pjs.append({"nom": nom or "", "taille": len(donnees), "type": ctype})
        return
    if ctype == "text/plain":
        textes.append(_contenu_texte(part))
    elif ctype == "text/html":
        htmls.append(_contenu_texte(part))


def _liens(hrefs: list[str], texte: str) -> list[dict]:
    candidats = list(hrefs) + _URL_TEXTE.findall(texte)
    vus, liens = set(), []
    for url in candidats:
        url = url.strip().rstrip(".,;:!?)]}>\"'")
        if not url.lower().startswith(("http://", "https://")) or len(url) > URL_MAX or url in vus:
            continue
        try:
            hote = (urlsplit(url).hostname or "").lower()
        except ValueError:
            continue
        if not hote:
            continue
        vus.add(url)
        liens.append({"url": url, "domaine": hote.removeprefix("www.")})
        if len(liens) >= LIENS_MAX:
            break
    return liens


def analyser(contenu: bytes, date_repli: str | None = None) -> Analyse:
    msg = message_from_bytes(contenu, policy=policy.default)
    textes, htmls, pjs = [], [], []
    _parcourir(msg, textes, htmls, pjs)

    texte_brut = nettoyer("\n\n".join(textes))
    texte_html, hrefs = "", []
    if len(_BALISES.findall(texte_brut)) >= 3:  # expéditeur qui met du HTML dans text/plain
        texte_brut, hrefs = html_vers_texte(texte_brut)
    for h in htmls:
        t, l = html_vers_texte(h)
        texte_html = f"{texte_html}\n\n{t}".strip()
        hrefs += l
    # le texte brut sert, sauf s'il n'est qu'un bouchon (« affichez ce mail dans un navigateur »)
    if texte_brut and not (len(texte_brut) < 200 and len(texte_html) > len(texte_brut)):
        corps = texte_brut
    else:
        corps = texte_html or texte_brut
    if len(corps) > CORPS_MAX:
        corps = corps[:CORPS_MAX] + "\n[…tronqué]"

    return Analyse(
        message_id=_en_tete(msg, "Message-ID"),
        in_reply_to=_en_tete(msg, "In-Reply-To"),
        refs=_en_tete(msg, "References"),
        expediteur=_en_tete(msg, "From"),
        destinataires=_adresses(msg, "To", "Cc"),
        objet=_en_tete(msg, "Subject"),
        date=_date(msg, date_repli),
        corps_texte=corps,
        liens=_liens(hrefs, texte_brut),
        pieces_jointes=pjs,
    )


# --- écriture en base -------------------------------------------------------


def enregistrer_compte(con: sqlite3.Connection, compte: Compte) -> None:
    con.execute(
        """INSERT INTO comptes (cle, nom, fournisseur, adresse, via, dossier, ajoutee_le)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (cle) DO UPDATE SET nom = excluded.nom, fournisseur = excluded.fournisseur,
             adresse = excluded.adresse, via = excluded.via, dossier = excluded.dossier""",
        (
            compte.cle,
            compte.nom,
            compte.fournisseur,
            None if compte.via else compte.utilisateur.lower() or None,
            compte.via or None,
            compte.dossier,
            maintenant(),
        ),
    )


def enregistrer_mail(con, compte: str, dossier: str, uidvalidity: int, brut: Brut, a: Analyse) -> int:
    cur = con.execute(
        """INSERT INTO mails (compte, dossier, gmail_msgid, thread_id, uid, uidvalidity, message_id,
             in_reply_to, refs, expediteur, destinataires, objet, date, taille, libelles, corps_texte,
             liens, a_pj, emplacement, vu_le)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (compte, gmail_msgid) DO NOTHING""",
        (
            compte,
            dossier,
            brut.gmail_msgid,
            brut.thread_id,
            brut.uid,
            uidvalidity,
            a.message_id,
            a.in_reply_to,
            a.refs,
            a.expediteur,
            json.dumps(a.destinataires, ensure_ascii=False),
            a.objet,
            a.date,
            brut.taille,
            json.dumps(brut.libelles, ensure_ascii=False),
            a.corps_texte,
            json.dumps(a.liens, ensure_ascii=False),
            int(bool(a.pieces_jointes)),
            "inbox" if dossier == "principal" else "envoyes",
            maintenant(),
        ),
    )
    if not cur.rowcount:
        return 0
    mail_id = cur.lastrowid
    con.executemany(
        "INSERT INTO pieces_jointes (mail_id, nom, taille, type) VALUES (?, ?, ?, ?)",
        [(mail_id, p["nom"], p["taille"], p["type"]) for p in a.pieces_jointes],
    )
    return mail_id


# --- scan d'une boîte -------------------------------------------------------


@dataclass
class RapportDossier:
    dossier: str  # principal · envoyes
    nom_imap: str
    trouves: int = 0
    deja_en_base: int = 0
    exclus: int = 0
    ajoutes: int = 0
    erreurs: int = 0


def dossiers_a_lire(compte: Compte, speciaux: dict[str, str]) -> list[tuple[str, str]]:
    """Dossier principal, plus les Envoyés (sauf pour une boîte transférée : ses réponses partent d'ailleurs)."""
    dossiers = [("principal", compte.dossier)]
    if not compte.via and "\\Sent" in speciaux:
        dossiers.append(("envoyes", speciaux["\\Sent"]))
    return dossiers


def scanner(
    con: sqlite3.Connection,
    compte: Compte,
    boite: Boite,
    jours: int,
    *,
    exclus: set[str] = frozenset(),
    lot: int = 50,
    limite: int | None = None,
    afficher=print,
    aujourdhui: date | None = None,
) -> list[RapportDossier]:
    """Lit les mails des `jours` derniers jours et enregistre ceux qui manquent.

    Reprise : les mails déjà en base (même X-GM-MSGID) ne sont pas relus, ce qui couvre
    aussi un changement d'UIDVALIDITY. Validation par lot : un scan interrompu repart
    là où il s'est arrêté.
    """
    enregistrer_compte(con, compte)
    con.commit()
    depuis = (aujourdhui or date.today()) - timedelta(days=jours)
    connus = {r[0] for r in con.execute("SELECT gmail_msgid FROM mails WHERE compte = ?", (compte.cle,))}
    debut = maintenant()
    rapports = []
    for dossier, nom in dossiers_a_lire(compte, boite.dossiers_speciaux()):
        r = RapportDossier(dossier, nom)
        rapports.append(r)
        etat = boite.examiner(nom)
        uids = boite.uids_depuis(depuis)
        if limite:
            uids = uids[-limite:]
        r.trouves = len(uids)

        a_lire = []
        for i in range(0, len(uids), LOT_LEGER):
            for b in boite.identifier(uids[i : i + LOT_LEGER]):
                if b.gmail_msgid in connus:
                    r.deja_en_base += 1
                elif exclus & set(b.libelles):
                    r.exclus += 1
                else:
                    a_lire.append(b.uid)

        for i in range(0, len(a_lire), lot):
            for b in boite.lire(a_lire[i : i + lot]):
                if b.gmail_msgid in connus:
                    continue
                try:
                    a = analyser(b.contenu or b"", b.date_interne)
                    if enregistrer_mail(con, compte.cle, dossier, etat.uidvalidity, b, a):
                        r.ajoutes += 1
                        connus.add(b.gmail_msgid)
                except Exception as e:
                    r.erreurs += 1
                    afficher(f"    erreur sur l'UID {b.uid} : {type(e).__name__}")
            con.commit()
            afficher(f"  {compte.cle} {dossier} : {min(i + lot, len(a_lire))}/{len(a_lire)}")

        con.execute(
            """INSERT INTO synchro (compte, dossier_imap, uidvalidity, dernier_uid, mis_a_jour_le)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (compte, dossier_imap) DO UPDATE SET uidvalidity = excluded.uidvalidity,
                 dernier_uid = excluded.dernier_uid, mis_a_jour_le = excluded.mis_a_jour_le""",
            (compte.cle, nom, etat.uidvalidity, max(uids) if uids else None, maintenant()),
        )
        con.commit()

    fin = maintenant()
    con.execute("UPDATE comptes SET derniere_synchro = ?, erreur = NULL WHERE cle = ?", (fin, compte.cle))
    con.execute(
        "INSERT INTO passages (debut, fin, compte, mails_lus) VALUES (?, ?, ?, ?)",
        (debut, fin, compte.cle, sum(r.ajoutes for r in rapports)),
    )
    con.commit()
    return rapports
