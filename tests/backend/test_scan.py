"""Scan d'une boîte avec une fausse boîte : lots, reprise, exclusion de cours, UIDVALIDITY."""

import json
from datetime import date
from email.message import EmailMessage

import pytest

from postroom import db
from postroom.boites.base import Boite, Brut, EtatBoite
from postroom.config import Compte, charger_comptes, libelles_exclus
from postroom.ingestion import dossiers_a_lire, scanner

AUJOURDHUI = date(2026, 11, 17)
PERSO1 = Compte("perso1", "Principale", "gmail", "imap.gmail.com", "moi@example.org", "secret")
COURS = Compte("cours", "Cours", "gmail", "imap.gmail.com", "moi@example.org", "secret", via="perso1",
               dossier="Cours - POSTROOM")


def _contenu(n: int, pj: bool = False) -> bytes:
    m = EmailMessage()
    m["From"] = f"exp{n}@example.org"
    m["Subject"] = f"mail {n}"
    m["Date"] = "Mon, 16 Nov 2026 10:00:00 +0000"
    m.set_content(f"corps {n}")
    if pj:
        m.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="f.pdf")
    return m.as_bytes()


class FausseBoite(Boite):
    """Dossiers : nom -> (uidvalidity, {uid: (msgid, libellés, jour)})."""

    def __init__(self, dossiers, sent="[Gmail]/Messages envoyés", panne_apres=None):
        self.dossiers = dossiers
        self.sent = sent
        self.courant = None
        self.lectures = []
        self.panne_apres = panne_apres

    def dossiers_speciaux(self):
        return {"\\Sent": self.sent, "\\Trash": "[Gmail]/Corbeille"}

    def examiner(self, dossier="INBOX"):
        self.courant = dossier
        uv, msgs = self.dossiers[dossier]
        return EtatBoite(len(msgs), uv)

    def uids_depuis(self, jour):
        return sorted(u for u, (_, _, j) in self.dossiers[self.courant][1].items() if j >= jour)

    def _brut(self, uid, complet):
        msgid, libelles, _ = self.dossiers[self.courant][1][uid]
        return Brut(uid, msgid, "t1", libelles, 100, "2026-11-16T10:00:00+00:00",
                    _contenu(msgid, pj=msgid % 2 == 0) if complet else None)

    def identifier(self, uids):
        return [self._brut(u, False) for u in uids]

    def lire(self, uids):
        if self.panne_apres is not None and len(self.lectures) >= self.panne_apres:
            raise ConnectionError("connexion coupée")
        self.lectures.append(list(uids))
        return [self._brut(u, True) for u in uids]

    def fermer(self):
        pass


@pytest.fixture
def con(tmp_path):
    c = db.connecter(tmp_path / "t.db")
    db.migrer(c)
    return c


def _boite_perso1(**kw):
    recent, ancien = date(2026, 11, 10), date(2026, 9, 1)
    inbox = {u: (1000 + u, ["\\Inbox"], recent) for u in range(1, 8)}
    inbox[8] = (1008, ["\\Inbox"], ancien)  # hors fenêtre
    inbox[9] = (1009, ["Cours - POSTROOM"], recent)  # mail de cours : exclu de perso1
    sent = {1: (2001, ["\\Sent"], recent), 2: (1003, ["\\Sent", "\\Inbox"], recent)}  # 1003 : déjà vu dans INBOX
    return FausseBoite({"INBOX": (5, inbox), "[Gmail]/Messages envoyés": (6, sent)}, **kw)


def _scan(con, compte, boite, **kw):
    return scanner(con, compte, boite, 30, exclus={"Cours - POSTROOM"} if compte.cle == "perso1" else set(),
                   lot=3, afficher=lambda *_: None, aujourdhui=AUJOURDHUI, **kw)


def test_scan_complet(con):
    boite = _boite_perso1()
    principal, envoyes = _scan(con, PERSO1, boite)
    assert (principal.trouves, principal.ajoutes, principal.exclus, principal.erreurs) == (8, 7, 1, 0)
    assert (envoyes.trouves, envoyes.ajoutes, envoyes.deja_en_base) == (2, 1, 1)
    assert boite.lectures == [[1, 2, 3], [4, 5, 6], [7], [1]]  # lots de 3
    lignes = con.execute("SELECT * FROM mails ORDER BY gmail_msgid").fetchall()
    assert [l["gmail_msgid"] for l in lignes] == [1001, 1002, 1003, 1004, 1005, 1006, 1007, 2001]
    m = lignes[0]
    assert (m["compte"], m["dossier"], m["uid"], m["uidvalidity"], m["emplacement"]) == ("perso1", "principal", 1, 5, "inbox")
    assert m["objet"] == "mail 1001" and m["corps_texte"] == "corps 1001"
    assert json.loads(m["libelles"]) == ["\\Inbox"]
    assert lignes[-1]["emplacement"] == "envoyes"
    assert con.execute("SELECT a_pj FROM mails WHERE gmail_msgid = 1002").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM pieces_jointes").fetchone()[0] == 3  # 1002, 1004, 1006
    compte = con.execute("SELECT * FROM comptes").fetchone()
    assert (compte["cle"], compte["dossier"], compte["adresse"]) == ("perso1", "INBOX", "moi@example.org")
    assert compte["derniere_synchro"]
    assert con.execute("SELECT mails_lus FROM passages").fetchone()[0] == 8
    assert {tuple(s) for s in con.execute("SELECT dossier_imap, uidvalidity, dernier_uid FROM synchro")} == {
        ("INBOX", 5, 9), ("[Gmail]/Messages envoyés", 6, 2)}


def test_second_passage_ne_relit_rien(con):
    _scan(con, PERSO1, _boite_perso1())
    boite = _boite_perso1()
    principal, envoyes = _scan(con, PERSO1, boite)
    assert (principal.ajoutes, principal.deja_en_base, envoyes.ajoutes) == (0, 7, 0)
    assert boite.lectures == []


def test_reprise_apres_coupure(con):
    with pytest.raises(ConnectionError):
        _scan(con, PERSO1, _boite_perso1(panne_apres=1))
    assert con.execute("SELECT COUNT(*) FROM mails").fetchone()[0] == 3  # premier lot validé
    boite = _boite_perso1()
    principal, _ = _scan(con, PERSO1, boite)
    assert (principal.ajoutes, principal.deja_en_base) == (4, 3)
    assert boite.lectures[0] == [4, 5, 6]


def test_changement_d_uidvalidity(con):
    _scan(con, PERSO1, _boite_perso1())
    renumerote = _boite_perso1()
    uv, msgs = renumerote.dossiers["INBOX"]
    renumerote.dossiers["INBOX"] = (99, {u + 100: v for u, v in msgs.items()})
    principal, _ = _scan(con, PERSO1, renumerote)
    assert (principal.ajoutes, principal.deja_en_base) == (0, 7)  # reconnus par X-GM-MSGID
    assert con.execute("SELECT uidvalidity FROM synchro WHERE dossier_imap = 'INBOX'").fetchone()[0] == 99


def test_scan_cours_libelle_sans_envoyes(con):
    boite = FausseBoite({"Cours - POSTROOM": (7, {1: (1009, ["Cours - POSTROOM"], date(2026, 11, 10))})})
    rapports = _scan(con, COURS, boite)
    assert [(r.dossier, r.nom_imap, r.ajoutes) for r in rapports] == [("principal", "Cours - POSTROOM", 1)]
    compte = con.execute("SELECT * FROM comptes").fetchone()
    assert (compte["via"], compte["dossier"], compte["adresse"]) == ("perso1", "Cours - POSTROOM", None)


def test_limite_garde_les_plus_recents(con):
    boite = _boite_perso1()
    principal, envoyes = _scan(con, PERSO1, boite, limite=2)
    assert principal.trouves == 2 and boite.lectures[0] == [7]  # 9 exclu, 7 lu


def test_message_illisible_compte_comme_erreur(con, monkeypatch):
    import postroom.ingestion as ingestion

    def casse(contenu, repli=None):
        raise ValueError("illisible")

    monkeypatch.setattr(ingestion, "analyser", casse)
    principal, _ = _scan(con, PERSO1, _boite_perso1())
    assert principal.erreurs == 7 and principal.ajoutes == 0


def test_dossiers_a_lire():
    assert dossiers_a_lire(PERSO1, {"\\Sent": "S"}) == [("principal", "INBOX"), ("envoyes", "S")]
    assert dossiers_a_lire(PERSO1, {}) == [("principal", "INBOX")]
    assert dossiers_a_lire(COURS, {"\\Sent": "S"}) == [("principal", "Cours - POSTROOM")]


def test_libelles_exclus(tmp_path):
    f = tmp_path / "accounts.env"
    f.write_text("PERSO1_HOST=imap.gmail.com\nPERSO1_USER=a@example.org\nPERSO1_PASS=s\n"
                 "COURS_VIA=perso1\nCOURS_DOSSIER=Cours - POSTROOM\n")
    comptes = charger_comptes(f)
    assert libelles_exclus("perso1", comptes) == {"Cours - POSTROOM"}
    assert libelles_exclus("perso2", comptes) == set()
    assert comptes["cours"].fournisseur == "gmail"
