from datetime import date

import pytest

from postroom.boites.base import ErreurEcriture, LectureSeule, decoder_utf7, encoder_utf7, nom_imap
from postroom.boites.gmail import ITEMS_COMPLETS, ITEMS_LEGERS, BoiteGmail, analyser_fetch, date_imap


def test_encoder_utf7_aller_retour():
    for nom in ("INBOX", "[Gmail]/Messages envoyés", "Éléments envoyés", "A&B", "Cours - POSTROOM", "日本"):
        assert decoder_utf7(encoder_utf7(nom)) == nom
    assert encoder_utf7("[Gmail]/Messages envoyés") == "[Gmail]/Messages envoy&AOk-s"


def test_nom_imap():
    assert nom_imap("INBOX") == "INBOX"
    assert nom_imap("Cours - POSTROOM") == '"Cours - POSTROOM"'
    assert nom_imap('a"b') == '"a\\"b"'


def test_date_imap_independante_de_la_locale():
    assert date_imap(date(2026, 10, 3)) == "03-Oct-2026"


def test_analyser_fetch_leger():
    donnees = [
        b'1 (X-GM-MSGID 1801 X-GM-LABELS ("\\\\Important" "Cours - POSTROOM") UID 10)',
        b"2 (X-GM-MSGID 1802 X-GM-LABELS () UID 11)",
        b"3 (X-GM-MSGID 1803 X-GM-LABELS (\\Inbox Perso) UID 12)",
    ]
    r = analyser_fetch(donnees)
    assert [(b.uid, b.gmail_msgid) for b in r] == [(10, 1801), (11, 1802), (12, 1803)]
    assert r[0].libelles == ["\\Important", "Cours - POSTROOM"]
    assert r[1].libelles == []
    assert r[2].libelles == ["\\Inbox", "Perso"]
    assert r[0].contenu is None


def test_analyser_fetch_complet_et_libelle_en_litteral():
    donnees = [
        (
            b'1 (UID 10 X-GM-MSGID 1801 X-GM-THRID 900 X-GM-LABELS ("\\\\Inbox") RFC822.SIZE 42 '
            b'INTERNALDATE "16-Nov-2026 10:30:00 +0100" BODY[] {5}',
            b"abcde",
        ),
        b")",
        (b"2 (UID 11 X-GM-MSGID 1802 X-GM-LABELS ({4}", b"Caf&AOk-"),
        (b' "Re&AOc-us") X-GM-THRID 901 RFC822.SIZE 3 INTERNALDATE "1-Nov-2026 08:00:00 +0000" BODY[] {3}', b"xyz"),
        b")",
    ]
    r = analyser_fetch(donnees)
    assert len(r) == 2
    assert (r[0].uid, r[0].thread_id, r[0].taille, r[0].contenu) == (10, "900", 42, b"abcde")
    assert r[0].date_interne == "2026-11-16T09:30:00+00:00"
    assert r[1].libelles == ["Café", "Reçus"]
    assert (r[1].contenu, r[1].date_interne) == (b"xyz", "2026-11-01T08:00:00+00:00")


class ImapEnregistreuse:
    def __init__(self):
        self.commandes = []

    def select(self, dossier, readonly=False):
        self.commandes.append(("select", dossier, readonly))
        return "OK", [b"3"]

    def response(self, code):
        return code, [b"77"]

    def uid(self, commande, *args):
        self.commandes.append(("uid", commande, *args))
        if commande == "SEARCH":
            return "OK", [b"5 3 4"]
        return "OK", [b"1 (X-GM-MSGID 1 X-GM-LABELS () UID 3)"]

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Messages envoy&AOk-s"']

    def store(self, *a):
        raise AssertionError("ne doit jamais être appelée")

    def logout(self):
        self.commandes.append(("logout",))


def test_boite_gmail_lecture_seule():
    imap = ImapEnregistreuse()
    b = BoiteGmail(object(), ouvrir=lambda c: imap)
    assert b.dossiers_speciaux() == {"\\Sent": "[Gmail]/Messages envoyés"}
    etat = b.examiner("[Gmail]/Messages envoyés")
    assert (etat.messages, etat.uidvalidity) == (3, 77)
    assert b.uids_depuis(date(2026, 10, 18)) == [3, 4, 5]
    b.identifier([3])
    b.lire([3, 4])
    b.fermer()
    assert imap.commandes == [
        ("select", '"[Gmail]/Messages envoy&AOk-s"', True),
        ("uid", "SEARCH", None, "SINCE", "18-Oct-2026"),
        ("uid", "FETCH", "3", ITEMS_LEGERS),
        ("uid", "FETCH", "3,4", ITEMS_COMPLETS),
        ("logout",),
    ]


@pytest.mark.parametrize(
    "appel",
    [
        lambda g: g.select("INBOX"),
        lambda g: g.uid("STORE", "1", "+FLAGS", "(\\Deleted)"),
        lambda g: g.uid("COPY", "1", "x"),
        lambda g: g.uid("MOVE", "1", "x"),
        lambda g: g.uid("FETCH", "1", "(BODY[])"),
        lambda g: g.uid("FETCH", "1", "(RFC822)"),
        lambda g: g.store("1", "+FLAGS", "(\\Seen)"),
        lambda g: g.expunge(),
        lambda g: g.append("INBOX", None, None, b"x"),
        lambda g: g.create("x"),
        lambda g: g.delete("x"),
    ],
)
def test_garde_lecture_seule_refuse_les_ecritures(appel):
    imap = ImapEnregistreuse()
    with pytest.raises(ErreurEcriture):
        appel(LectureSeule(imap))
    assert imap.commandes == []


def test_garde_lecture_seule_laisse_lire():
    imap = ImapEnregistreuse()
    g = LectureSeule(imap)
    g.select("INBOX", readonly=True)
    g.uid("FETCH", "1", ITEMS_COMPLETS)
    g.uid("search", None, "ALL")
    assert len(imap.commandes) == 3
