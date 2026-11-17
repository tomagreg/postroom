import pytest

from postroom.__main__ import main
from postroom.boites import base
from postroom.boites.base import analyser_list, decoder_utf7, ouvrir_imap
from postroom.config import charger_comptes
from postroom.verification import verifier


@pytest.fixture
def fichier_comptes(tmp_path):
    f = tmp_path / "accounts.env"
    f.write_text(
        "PERSO1_HOST=imap.gmail.com\nPERSO1_USER=a@example.org\nPERSO1_PASS=abcd efgh ijkl mnop\n"
        "VOILE_HOST=imap.example.com\nVOILE_USER=\nVOILE_PASS=\n"
        "COURS_HOST=outlook.office365.com\nCOURS_USER=b@example.org\nCOURS_AUTH=oauth2\nCOURS_CLIENT_ID=abc\n"
    )
    return f


def test_charger_comptes(fichier_comptes):
    comptes = charger_comptes(fichier_comptes)
    assert list(comptes) == ["perso1", "perso2", "voile", "cours"]
    assert comptes["perso1"].configure
    assert comptes["perso1"].mot_de_passe == "abcd efgh ijkl mnop"
    assert not comptes["voile"].configure
    assert comptes["cours"].configure and comptes["cours"].auth == "oauth2"
    assert "abcd" not in repr(comptes["perso1"])


def test_decoder_utf7():
    assert decoder_utf7("[Gmail]/Messages envoy&AOk-s") == "[Gmail]/Messages envoyés"
    assert decoder_utf7("&AMk-l&AOk-ments envoy&AOk-s") == "Éléments envoyés"
    assert decoder_utf7("A&-B") == "A&B"


def test_analyser_list():
    lignes = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Messages envoy&AOk-s"',
        b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Corbeille"',
        b'(\\All \\HasNoChildren) "/" "[Gmail]/Tous les messages"',
    ]
    assert analyser_list(lignes) == {
        "\\Sent": "[Gmail]/Messages envoyés",
        "\\Trash": "[Gmail]/Corbeille",
        "\\All": "[Gmail]/Tous les messages",
    }


class FausseImap:
    def capability(self):
        return "OK", [b"IMAP4rev1 X-GM-EXT-1 MOVE UIDPLUS"]

    def __init__(self):
        self.selections = []
        self.deconnecte = False

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"']

    def select(self, dossier, readonly=False):
        self.selections.append((dossier, readonly))
        return "OK", [b"42"]

    def response(self, code):
        return code, [b"7"]

    def logout(self):
        self.deconnecte = True


def test_verifier_en_lecture_seule(fichier_comptes):
    imap = FausseImap()
    r = verifier(charger_comptes(fichier_comptes)["perso1"], ouvrir=lambda c: imap)
    assert r.statut == "ok" and r.messages == 42 and r.uidvalidity == 7
    assert r.speciaux == {"\\Sent": "[Gmail]/Sent Mail"}
    assert r.capacites == ["X-GM-EXT-1", "MOVE", "UIDPLUS"]
    assert imap.selections == [("INBOX", True)]  # EXAMINE, jamais SELECT
    assert imap.deconnecte


def test_verifier_erreur_et_non_configure(fichier_comptes):
    comptes = charger_comptes(fichier_comptes)

    def refuse(c):
        raise OSError("[AUTHENTICATIONFAILED] Invalid credentials")

    assert verifier(comptes["perso1"], ouvrir=refuse).statut == "erreur"
    assert verifier(comptes["voile"]).statut == "non configuré"


def test_cli_aide(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "verifier-boites" in capsys.readouterr().out


def test_ouvrir_imap_oauth2(fichier_comptes, monkeypatch):
    appels = {}

    class FausseConnexion:
        def __init__(self, hote, port, timeout):
            appels["hote"] = hote

        def authenticate(self, mecanisme, rappel):
            appels["mecanisme"] = mecanisme
            appels["chaine"] = rappel(b"")

        def login(self, *a):
            raise AssertionError("pas de LOGIN en OAuth2")

    import postroom.boites.oauth as oauth

    monkeypatch.setattr(base.imaplib, "IMAP4_SSL", FausseConnexion)
    monkeypatch.setattr(oauth, "jeton", lambda c: "JETON")
    ouvrir_imap(charger_comptes(fichier_comptes)["cours"])
    assert appels == {
        "hote": "outlook.office365.com",
        "mecanisme": "XOAUTH2",
        "chaine": b"user=b@example.org\x01auth=Bearer JETON\x01\x01",
    }
