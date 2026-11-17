"""Éléments (jalon 3) : comportements, regroupements, En attente, synchronisation, expirations (sans réseau)."""

import json
from datetime import datetime, timezone

import pytest

from postroom import db
from postroom.boites.base import Boite, Brut, EtatBoite
from postroom.config import Compte
from postroom.elements import attente, construire
from postroom.elements.candidatures import cle_entreprise, meme_poste
from postroom.elements.commun import adresse, fin_du_jour, normaliser
from postroom.elements.synchro import presence

QUAND = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
MOI = {"moi@example.org"}


@pytest.fixture
def con(tmp_path):
    c = db.connecter(tmp_path / "t.db")
    db.migrer(c)
    c.execute("INSERT INTO comptes (cle, nom, fournisseur, adresse) VALUES ('perso1', 'Principale', 'gmail', "
              "'moi@example.org')")
    c.execute("INSERT INTO comptes (cle, nom, fournisseur, via, dossier) VALUES ('cours', 'Cours', 'gmail', "
              "'perso1', 'Cours - POSTROOM')")
    c.commit()
    return c


_n = [0]


def mail(con, categorie=None, *, objet="objet", expediteur="Exp <exp@site.fr>", date="2026-11-17T10:00:00+00:00",
         extraction=None, priorite=None, fil=None, dossier="principal", compte="perso1", message_id=None,
         in_reply_to=None, refs=None, destinataires=None, liens=None, corps="", emplacement=None, erreur=None):
    _n[0] += 1
    n = _n[0]
    cur = con.execute(
        """INSERT INTO mails (compte, dossier, gmail_msgid, thread_id, message_id, in_reply_to, refs, expediteur,
             destinataires, objet, date, corps_texte, liens, emplacement, vu_le)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (compte, dossier, 1000 + n, fil, message_id or f"<m{n}@x>", in_reply_to, refs, expediteur,
         json.dumps(destinataires or []), objet, date, corps, json.dumps(liens or []),
         emplacement or ("inbox" if dossier == "principal" else "envoyes"), "2026-11-18T00:00:00+00:00"))
    if categorie:
        con.execute(
            """INSERT INTO decisions (mail_id, categorie_auto, source, confiance, priorite, extraction, erreur,
                 decide_le) VALUES (?, ?, 'ia', 90, ?, ?, ?, '2026-11-18T00:00:00+00:00')""",
            (cur.lastrowid, categorie, priorite, json.dumps(extraction or {}), erreur))
    con.commit()
    return cur.lastrowid


def els(con, type_=None, actifs=True):
    sql = "SELECT * FROM elements WHERE 1=1" + (" AND type = ?" if type_ else "") + \
          (" AND retire_le IS NULL" if actifs else "")
    return con.execute(sql, (type_,) if type_ else ()).fetchall()


# --- outils ------------------------------------------------------------------


def test_normaliser_et_adresse():
    assert normaliser("STAGE &amp; Intelligence – 4ᵉ année") == "stage intelligence 4e annee"
    assert adresse("Moi <TomaGreg2+cours@Gmail.com>") == "tomagreg2@gmail.com"
    assert fin_du_jour("2026-11-20") == "2026-11-20T23:00:00+00:00"
    assert fin_du_jour("pas une date") is None


def test_rapprochement_candidatures():
    assert cle_entreprise("Thales Group") == cle_entreprise("THALES") == "thales"
    assert meme_poste("Management Consultant", "Consultant en management")
    assert meme_poste("Stagiaire IA Data – 4e année", "Stagiaire IA Data – 4ᵉ année")
    assert not meme_poste("Stage - Business Analyst Finance", "Modélisation de procédés & IA")


# --- comportements -----------------------------------------------------------


def test_commande_amazon_avance_sans_reculer(con):
    num = "402-2275066-0544366"
    mail(con, "commande", objet=f"Commandés : 2 « Caisse » {num}", date="2026-11-04T10:00:00+00:00",
         extraction={"boutique": "Amazon.fr", "numero": "", "etape": 1})
    mail(con, "commande", objet="Expédié : 2 « Caisse »", corps=f"Commande n° {num}",
         date="2026-11-05T10:00:00+00:00", extraction={"boutique": "Amazon", "numero": num, "etape": 2})
    mail(con, "commande", objet="Livré : 2 « Caisse »", corps=f"n° {num}", date="2026-11-07T10:00:00+00:00",
         extraction={"etape": 4})
    mail(con, "commande", objet="Rappel", corps=f"n° {num}", date="2026-11-08T10:00:00+00:00",
         extraction={"etape": 2})
    construire(con, "perso1", MOI, QUAND)
    (e,) = els(con, "commande", actifs=False)
    c = con.execute("SELECT * FROM commandes WHERE element_id = ?", (e["id"],)).fetchone()
    assert e["titre"].startswith("2 « Caisse »") and c["numero"] == num and c["etape"] == 4
    assert set(json.loads(c["dates"])) == {"1", "2", "4"}
    assert e["expire_le"] == "2026-11-14T10:00:00+00:00"  # 7 jours après Livré
    assert con.execute("SELECT COUNT(*) FROM element_mails WHERE element_id = ?", (e["id"],)).fetchone()[0] == 4


def test_commande_sans_numero_regroupee_par_fil(con):
    mail(con, "commande", fil="t9", extraction={"boutique": "Les 25h", "etape": 1})
    mail(con, "commande", fil="t9", date="2026-11-18T08:00:00+00:00", extraction={"boutique": "Les 25h", "etape": 2})
    construire(con, "perso1", MOI, QUAND)
    assert len(els(con, "commande")) == 1


def test_evenement_alerte_offre_social(con):
    mail(con, "evenement", objet="Billet", extraction={"date": "2026-11-27", "resume": "Train"})
    mail(con, "alerte", objet="Connexion", liens=[{"url": "https://a.fr/x", "domaine": "a.fr"}],
         extraction={"lien": 0, "peremption": None})
    mail(con, "offre", objet="Promo", extraction={"peremption": "2026-11-20"})
    mail(con, "social", objet="Profil consulté")
    construire(con, "perso1", MOI, QUAND)
    (ev,) = els(con, "evenement")
    assert ev["echeance"] == "2026-11-27" and ev["expire_le"] == fin_du_jour("2026-11-27")
    (al,) = els(con, "alerte")
    assert (al["lien"], al["domaine"], al["expire_le"]) == ("https://a.fr/x", "a.fr", "2026-11-24T10:00:00+00:00")
    offres = {e["sous_type"]: e for e in els(con, "offre")}
    assert offres["offre"]["expire_le"] == fin_du_jour("2026-11-20")  # péremption plus proche que 14 j
    assert offres["social"]["expire_le"] == "2026-11-24T10:00:00+00:00"  # social_jours = 7


def test_duree_par_categorie(con):
    con.execute("UPDATE categories SET duree_jours = 2 WHERE cle = 'offre'")
    mail(con, "offre", objet="Promo")
    construire(con, "perso1", MOI, QUAND)
    assert els(con, "offre")[0]["expire_le"] == "2026-11-19T10:00:00+00:00"


def test_simple(con):
    con.execute("UPDATE categories SET comportement = 'simple' WHERE cle = 'recu_paiement'")
    mail(con, "recu_paiement", objet="Reçu Discord")
    construire(con, "perso1", MOI, QUAND)
    (e,) = els(con, "simple")
    assert e["categorie"] == "recu_paiement" and e["expire_le"] == "2026-11-24T10:00:00+00:00"


def test_candidature_suivie_et_entretien(con):
    base = {"entreprise": "Thales Group", "poste": "STAGE - Data Analyst Opérations - F/H"}
    mail(con, "candidature", date="2026-10-29T08:00:00+00:00", extraction=dict(base, statut="envoyee"))
    mail(con, "candidature", date="2026-10-29T09:00:00+00:00",
         extraction={"entreprise": "THALES", "poste": "Data Analyst Opérations", "statut": "recue"})
    mail(con, "candidature", date="2026-11-02T09:00:00+00:00",
         extraction={"entreprise": "Thales", "poste": "", "statut": "entretien", "date": "2026-11-25"})
    mail(con, "candidature", extraction={"entreprise": "Thales", "poste": "Contrôleur de gestion", "statut": "envoyee"})
    construire(con, "perso1", MOI, QUAND)
    cands = {c["poste"]: c for c in con.execute("SELECT * FROM candidatures")}
    assert len(cands) == 2
    c = cands[base["poste"]]
    assert c["statut"] == "entretien"
    assert [h["statut"] for h in json.loads(c["historique"])] == ["envoyee", "recue", "entretien"]
    (ev,) = els(con, "evenement")
    assert ev["titre"] == "Entretien — Thales Group" and ev["echeance"] == "2026-11-25" and ev["sous_type"] == "candidature"


def test_candidature_statut_et_reference(con):
    # même jour : accusé de réception, invitation au test, puis accusé d'envoi -> le test reste le statut
    for heure, statut in ((8, "recue"), (9, "test"), (10, "envoyee")):
        mail(con, "candidature", date=f"2026-10-29T{heure:02d}:00:00+00:00",
             extraction={"entreprise": "Thales", "poste": "Data Analyst", "statut": statut})
    # test passé puis confirmation reçue -> recue
    for heure, statut in ((8, "test"), (9, "recue")):
        mail(con, "candidature", date=f"2026-10-26T{heure:02d}:00:00+00:00",
             extraction={"entreprise": "Wavestone", "poste": "Management Consultant", "statut": statut})
    # même référence recopiée par l'IA sur deux postes différents : deux candidatures
    mail(con, "candidature", extraction={"entreprise": "TotalEnergies", "poste": "Business Analyst Finance",
                                         "reference": "3069484", "statut": "recue"})
    mail(con, "candidature", extraction={"entreprise": "TotalEnergies", "reference": "3069484", "statut": "recue",
                                         "poste": "Modélisation de procédés &amp; IA"})
    construire(con, "perso1", MOI, QUAND)
    statuts = {(c["entreprise"], c["poste"]): c["statut"] for c in con.execute("SELECT * FROM candidatures")}
    assert statuts[("Thales", "Data Analyst")] == "test"
    assert statuts[("Wavestone", "Management Consultant")] == "recue"
    assert ("TotalEnergies", "Modélisation de procédés & IA") in statuts and len(statuts) == 4


def test_candidature_refusee_expire_et_suggestion(con):
    mail(con, "candidature", date="2026-10-25T08:00:00+00:00",
         extraction={"entreprise": "RTE", "poste": "STAGE PFE – Développeur et Data Analyst F/H", "statut": "envoyee"})
    mail(con, "candidature", date="2026-11-05T08:00:00+00:00",
         extraction={"entreprise": "RTE", "poste": "Développeur et Data Analyst", "statut": "refus"})
    mail(con, "candidature", objet="5 offres pour vous", extraction={"statut": "suggestion", "entreprise": ""})
    construire(con, "perso1", MOI, QUAND)
    (refus,) = [e for e in els(con, "candidature", actifs=False) if e["sous_type"] == "refus"]
    assert refus["expire_le"] == "2026-11-19T08:00:00+00:00"
    (sugg,) = [e for e in els(con, "candidature") if e["sous_type"] == "suggestion"]
    assert sugg["expire_le"] == "2026-12-01T10:00:00+00:00"
    assert con.execute("SELECT COUNT(*) FROM candidatures").fetchone()[0] == 1


def test_flux_une_ligne_par_mail_et_a_trier_ignore(con):
    mail(con, "recu_paiement", extraction={"resume": "Reçu Discord, 9,99 €"})
    mail(con, "code_unique", expediteur="Google <no-reply@google.com>")
    mail(con, "autre_supprime", expediteur="Strava <no-reply@strava.com>")
    mail(con, "a_trier")
    mail(con, "offre", erreur="délai dépassé")
    construire(con, "perso1", MOI, QUAND)
    lignes = {(l["type"], l["texte"], l["icone"]) for l in con.execute("SELECT * FROM flux")}
    assert lignes == {("Paiements", "Reçu Discord, 9,99 €", "payments"),
                      ("Autres", "Code à usage unique — Google — supprimé", "delete"),
                      ("Autres", "Autre — Strava — supprimé", "delete")}


def test_idempotent(con):
    mail(con, "offre")
    mail(con, "a_repondre", fil="f1")
    construire(con, "perso1", MOI, QUAND)
    def etat():
        return [(e["id"], e["type"], e["cle"], e["retire_le"]) for e in els(con, actifs=False)]

    avant = etat()
    r = construire(con, "perso1", MOI, QUAND)
    assert r.traites == 0 and etat() == avant
    assert con.execute("SELECT COUNT(*) FROM flux").fetchone()[0] == 2
    r = construire(con, "perso1", MOI, QUAND, refaire=True)
    assert r.traites == 2 and len(els(con, actifs=False)) == 2


# --- À répondre --------------------------------------------------------------


def test_reponse_par_fil_puis_repondu_puis_rouvert(con):
    mail(con, "a_repondre", fil="f1", priorite="normale", date="2026-11-13T09:00:00+00:00")
    mail(con, "a_repondre", fil="f1", priorite="haute", date="2026-11-14T09:00:00+00:00", objet="RE: Info")
    construire(con, "perso1", MOI, QUAND)
    (e,) = els(con, "reponse")
    assert e["priorite"] == "haute" and e["titre"] == "RE: Info"

    mail(con, None, fil="f1", dossier="envoyes", date="2026-11-15T09:00:00+00:00")
    construire(con, "perso1", MOI, QUAND)
    assert els(con, "reponse") == []
    assert els(con, "reponse", actifs=False)[0]["retire_motif"] == "repondu"

    mail(con, "a_repondre", fil="f1", date="2026-11-16T09:00:00+00:00", objet="RE: RE: Info")
    construire(con, "perso1", MOI, QUAND)
    (e,) = els(con, "reponse")
    assert e["titre"] == "RE: RE: Info"


def test_reponse_retiree_si_le_mail_quitte_la_boite(con):
    m = mail(con, "a_repondre", fil="f2")
    construire(con, "perso1", MOI, QUAND)
    con.execute("UPDATE mails SET emplacement = 'ailleurs' WHERE id = ?", (m,))
    construire(con, "perso1", MOI, QUAND)
    assert els(con, "reponse", actifs=False)[0]["retire_motif"] == "ailleurs"


def test_reponse_de_cours_vue_dans_les_envoyes_de_perso1(con):
    mail(con, "a_repondre", fil="f3", compte="cours")
    mail(con, None, fil="f3", dossier="envoyes", date="2026-11-18T09:00:00+00:00")
    construire(con, "cours", MOI, QUAND)
    assert els(con, "reponse") == []


# --- En attente --------------------------------------------------------------


def test_attente_sans_reponse_puis_reponse(con):
    mail(con, None, dossier="envoyes", fil="g1", message_id="<e1@moi>", objet="Candidature",
         destinataires=["rh@ent.fr"], date="2026-11-10T09:00:00+00:00")
    r = construire(con, "perso1", MOI, QUAND)
    (e,) = els(con, "attente")
    assert e["sous_titre"] == "rh@ent.fr" and e["echeance"] == "2026-11-15T09:00:00+00:00"
    assert r.attentes["sans_reponse"] == 1

    mail(con, "a_repondre", expediteur="RH <rh@ent.fr>", in_reply_to="<e1@moi>", date="2026-11-17T09:00:00+00:00")
    construire(con, "perso1", MOI, QUAND)
    assert els(con, "attente") == []
    assert con.execute("SELECT repondu_le FROM envoyes").fetchone()[0] == "2026-11-17T09:00:00+00:00"


def test_mail_a_soi_meme_sans_cycle(con):
    mail(con, None, dossier="envoyes", destinataires=["Moi+notes@example.org"])
    r = construire(con, "perso1", MOI, QUAND)
    assert r.attentes["a_soi"] == 1 and els(con, "attente") == []


def test_relancer_et_archiver(con):
    mail(con, None, dossier="envoyes", fil="g2", destinataires=["a@b.fr"], date="2026-11-10T09:00:00+00:00",
         objet="Devis")
    construire(con, "perso1", MOI, QUAND)
    (e,) = els(con, "attente")
    reglages = {"relance_jours": 5}
    rid = attente.relancer(con, e["id"], reglages, QUAND)
    rel = con.execute("SELECT * FROM elements WHERE id = ?", (rid,)).fetchone()
    assert rel["type"] == "reponse" and rel["relance"] == 1 and rel["titre"] == "Devis"
    e = con.execute("SELECT * FROM elements WHERE id = ?", (e["id"],)).fetchone()
    assert e["relance"] == 1 and e["echeance"] == "2026-11-23T12:00:00+00:00"
    construire(con, "perso1", MOI, QUAND)  # le cycle relancé survit à un nouveau passage
    assert els(con, "attente")[0]["relance"] == 1

    attente.archiver(con, e["id"], QUAND)
    construire(con, "perso1", MOI, QUAND)
    assert els(con, "attente") == []
    assert els(con, "attente", actifs=False)[0]["retire_motif"] == "archive"


# --- expirations -------------------------------------------------------------


def test_expire_affiche_puis_retire(con):
    mail(con, "offre", date="2026-11-04T00:00:00+00:00")  # expire le 18 à 0 h
    mail(con, "offre", date="2026-10-20T00:00:00+00:00")
    r = construire(con, "perso1", MOI, QUAND)
    assert r.expirations["expires_affiches"] == 1 and r.expirations["retires"] == 1
    assert len(els(con, "offre")) == 1


def test_effacement_des_corps_desactive_par_defaut(con):
    m = mail(con, "offre", date="2026-10-01T00:00:00+00:00", corps="texte")
    construire(con, "perso1", MOI, QUAND)
    assert con.execute("SELECT corps_texte FROM mails WHERE id = ?", (m,)).fetchone()[0] == "texte"
    con.execute("UPDATE reglages SET valeur = '1' WHERE cle = 'effacer_corps'")
    garde = mail(con, "offre", date="2026-11-17T00:00:00+00:00", corps="récent")
    construire(con, "perso1", MOI, QUAND)
    assert con.execute("SELECT corps_texte FROM mails WHERE id = ?", (m,)).fetchone()[0] is None
    assert con.execute("SELECT corps_texte FROM mails WHERE id = ?", (garde,)).fetchone()[0] == "récent"


def test_flux_purge_apres_90_jours(con):
    mail(con, "recu_paiement", date="2026-08-01T00:00:00+00:00")
    r = construire(con, "perso1", MOI, QUAND)
    assert r.expirations["flux_purges"] == 1


# --- synchronisation ---------------------------------------------------------


class Boite1(Boite):
    def __init__(self, presents):
        self.presents = presents
        self.examine = None

    def dossiers_speciaux(self):
        return {}

    def examiner(self, dossier="INBOX"):
        self.examine = dossier
        return EtatBoite(len(self.presents), 1)

    def uids_depuis(self, jour):
        return list(range(len(self.presents)))

    def identifier(self, uids):
        return [Brut(u, self.presents[u]) for u in uids]

    def lire(self, uids):
        raise AssertionError("pas de lecture complète")

    def fermer(self):
        pass


def test_presence(con):
    a = mail(con, "a_repondre")
    b = mail(con, "offre")
    c = mail(con, "offre", emplacement="ailleurs")
    d = mail(con, "offre", emplacement="suppression")
    msgid = {i: con.execute("SELECT gmail_msgid FROM mails WHERE id = ?", (i,)).fetchone()[0] for i in (a, b, c, d)}
    compte = Compte("perso1", "Principale", "gmail", "h", "u", "p")
    boite = Boite1([msgid[a], msgid[c]])
    r = presence(con, compte, boite)
    assert boite.examine == "INBOX"
    assert (r.verifies, r.presents, r.partis, r.revenus) == (3, 2, 1, 1)
    emplacements = dict(con.execute("SELECT id, emplacement FROM mails").fetchall())
    assert (emplacements[a], emplacements[b], emplacements[c], emplacements[d]) == \
        ("inbox", "ailleurs", "inbox", "suppression")


def test_migration_005(con):
    assert db.version(con) >= 5
    with pytest.raises(Exception):
        con.execute("INSERT INTO elements (type, cle, compte, cree_le) VALUES ('inconnu', 'x', 'perso1', 'x')")
    con.execute("INSERT INTO elements (type, cle, compte, cree_le) VALUES ('candidature', 'x', 'perso1', 'x')")
    with pytest.raises(Exception):
        con.execute("INSERT INTO elements (type, cle, compte, cree_le) VALUES ('candidature', 'x', 'perso1', 'y')")
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
