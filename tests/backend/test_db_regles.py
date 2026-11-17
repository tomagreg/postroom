import sqlite3

import pytest
import yaml

from postroom import config, db
from postroom.tri.categories import LISTE_BLANCHE
from postroom.tri.regles import importer, regles_v1

TABLES = {
    "comptes", "mails", "pieces_jointes", "decisions", "elements", "commandes", "envoyes", "flux",
    "actions", "regles", "reglages", "passages", "synchro", "schema_version",
}


@pytest.fixture
def con(tmp_path):
    c = db.connecter(tmp_path / "t.db")
    db.migrer(c)
    return c


def test_migrations_rejouables(tmp_path):
    c = db.connecter(tmp_path / "t.db")
    assert db.migrer(c) == [1, 2, 3, 4, 5]
    assert db.migrer(c) == []
    assert db.version(c) == 5
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert TABLES <= tables
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert dict(c.execute("SELECT cle, valeur FROM reglages"))["seuil_confiance"] == "70"


def test_migration_en_echec_annulee(tmp_path):
    dossier = tmp_path / "m"
    dossier.mkdir()
    (dossier / "001_a.sql").write_text("CREATE TABLE a (x);")
    (dossier / "002_b.sql").write_text("CREATE TABLE b (x); CREATE TABLE a (x);")
    c = db.connecter(tmp_path / "t.db")
    with pytest.raises(sqlite3.OperationalError):
        db.migrer(c, dossier)
    assert db.version(c) == 1
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'b'").fetchone()


def test_base_par_variable_d_environnement(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTROOM_DB", str(tmp_path / "x.db"))
    assert config.fichier_base() == tmp_path / "x.db"


def test_contraintes(con):
    con.execute("INSERT INTO comptes (cle, nom, fournisseur) VALUES ('perso1', 'Principale', 'gmail')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO comptes (cle, nom, fournisseur, mode) VALUES ('x', 'x', 'gmail', 'autre')")
    with pytest.raises(sqlite3.IntegrityError):  # clé étrangère
        con.execute("INSERT INTO mails (compte, dossier, gmail_msgid, vu_le) VALUES ('inconnu', 'principal', 1, 'x')")


def test_regles_v1_du_depot(con):
    """Les vrais rules.yaml et whitelist.yaml : chaque règle v1 a sa correspondance."""
    regles = yaml.safe_load(config.FICHIER_REGLES.read_text(encoding="utf-8"))
    blanche = yaml.safe_load(config.FICHIER_LISTE_BLANCHE.read_text(encoding="utf-8"))
    lignes, ignorees = regles_v1(regles, blanche)
    categories = {l[2] for l in lignes}
    assert categories <= {r[0] for r in con.execute("SELECT cle FROM categories")}
    assert not [l for l in lignes if l[3] == "promo_pending" and l[0] == "mot_cle"]  # objet seulement
    assert "a_trier" not in categories  # aucune règle v1 inconnue
    par_groupe = {}
    for type_, _, categorie, groupe, etat in lignes:
        par_groupe.setdefault(groupe, set()).add((categorie, etat))
    assert par_groupe["otp_codes"] == {("code_unique", "active")}
    assert par_groupe["notifications_auto"] == {("autre_supprime", "proposee")}
    assert par_groupe["booking_past"] == {("evenement", "proposee")}
    assert par_groupe["tracking_delivered"] == {("suivi_obsolete", "proposee")}
    assert par_groupe["social_pending"] == {("social", "active")}
    assert par_groupe["whitelist"] == {(LISTE_BLANCHE, "active")}
    assert ignorees == ["newsletters_inactive (conditions sans valeur, non reprises)"]
    assert ("exception_domaine", "sncf.fr", "autre_supprime", "notifications_auto", "proposee") in lignes
    assert ("mot_cle", " OTP ", "code_unique", "otp_codes", "active") in lignes  # espaces gardés


def test_importer_sans_doublon(con, tmp_path):
    r = tmp_path / "rules.yaml"
    w = tmp_path / "whitelist.yaml"
    r.write_text("rules:\n  - id: promo_pending\n    keywords: [promo, soldes]\n    action: promo_queue\n"
                 "  - id: inconnue\n    senders: [x@y]\nblacklist:\n  senders: []\n  domains: [spam.example]\n")
    w.write_text("whitelist:\n  domains: [gouv.fr]\n  senders: []\n  keywords_subject: [urgent]\n")
    premier = importer(con, r, w)
    assert (premier.ajoutees, premier.deja_presentes) == (6, 0)
    second = importer(con, r, w)
    assert (second.ajoutees, second.deja_presentes) == (0, 6)
    lignes = {tuple(l) for l in con.execute("SELECT type, valeur, categorie, origine, etat FROM regles")}
    assert ("expediteur", "x@y", "a_trier", "migree", "proposee") in lignes
    assert ("domaine", "spam.example", "autre_supprime", "migree", "active") in lignes
    assert ("mot_cle_objet", "urgent", LISTE_BLANCHE, "migree", "active") in lignes
