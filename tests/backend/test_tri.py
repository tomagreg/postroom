"""Tri v2 : application des règles, sortie de l'IA, seuil, action prévue, passages (sans réseau)."""

import json
import threading
from types import SimpleNamespace

import pytest

from postroom import db
from postroom.tri import ia
from postroom.tri.decision import action_prevue, trier
from postroom.tri.rapport import rapport
from postroom.tri import categories as cats
from postroom.tri.categories import LISTE_BLANCHE
from postroom.tri.regles import Regle, appliquer, domaine


def R(id_, type_, valeur, categorie, groupe="g", etat="active"):
    return Regle(id_, type_, valeur, categorie, groupe, etat)


REGLES = [
    R(1, "mot_cle", "code de vérification", "code_unique", "otp_codes"),
    R(2, "expediteur", "noreply@", "autre_supprime", "notifications_auto", "proposee"),
    R(3, "exception_domaine", "sncf.fr", "autre_supprime", "notifications_auto", "proposee"),
    R(4, "expediteur", "notifications-noreply@linkedin.com", "social", "social_pending"),
    R(5, "domaine", "gouv.fr", LISTE_BLANCHE, "whitelist"),
    R(6, "mot_cle", "promo", "offre", "promo_pending"),
    R(7, "domaine", "spam.example", "autre_supprime", "blacklist"),
]


# --- règles ------------------------------------------------------------------


def test_domaine():
    assert domaine("Impots <ne-pas-repondre@dgfip.gouv.fr>") == "dgfip.gouv.fr"
    assert domaine("sans adresse") == ""


def test_expediteur_avant_mot_cle():
    c = appliquer(REGLES, "LinkedIn <notifications-noreply@linkedin.com>", "Une promo pour vous", "")
    assert c.regle.id == 4


def test_mot_cle_dans_objet_et_debut_du_corps():
    assert appliquer(REGLES, "a@b.fr", "Bonjour", "Votre code de vérification : 1234").regle.id == 1
    loin = "x" * 3000 + " code de vérification"
    assert appliquer(REGLES, "a@b.fr", "Bonjour", loin).regle is None


def test_domaine_et_sous_domaine():
    assert appliquer(REGLES, "x@mail.spam.example", "", "").regle.id == 7
    assert appliquer(REGLES, "x@notspam.example", "", "").regle is None


def test_liste_blanche_jamais_decidee_par_une_regle():
    c = appliquer(REGLES, "Impots <x@dgfip.gouv.fr>", "Code de vérification", "promo")
    assert c.liste_blanche and c.regle is None


def test_regle_proposee_notee_sans_decider():
    c = appliquer(REGLES, "Shop <noreply@shop.fr>", "Votre colis", "")
    assert c.regle is None and [p.id for p in c.proposees] == [2]


def test_exception_domaine_neutralise_son_groupe():
    c = appliquer(REGLES, "SNCF <noreply@sncf.fr>", "Votre billet", "")
    assert c.regle is None and c.proposees == []
    # l'exception ne vaut que pour son groupe
    assert appliquer(REGLES, "SNCF <noreply@sncf.fr>", "promo", "").regle.id == 6


# --- sortie de l'IA ----------------------------------------------------------


def _tri():
    c = db.connecter(":memory:")
    db.migrer(c)
    return ia.Tri.depuis(cats.charger(c))


TRI = _tri()


def _sortie(**kw):
    d = dict(categorie="commande", confiance=92, priorite="aucune", resume="Colis expédié.", lien=1,
             peremption="", boutique="Amazon", numero_commande="402-1", etape_commande=2, date_evenement="",
             entreprise="", poste="", reference="", statut_candidature="", expediteur_personne=False)
    d.update(kw)
    return d


def test_valider_commande():
    cat, conf, prio, ext = ia.valider(_sortie(), 2, TRI)
    assert (cat, conf, prio) == ("commande", 92, None)
    assert ext == {"resume": "Colis expédié.", "lien": 1, "personne": False, "boutique": "Amazon", "numero": "402-1",
                   "etape": 2}


def test_valider_categorie_hors_liste():
    with pytest.raises(ia.ErreurIA):
        ia.valider(_sortie(categorie="a_trier"), 0, TRI)
    with pytest.raises(ia.ErreurIA):
        ia.valider(_sortie(categorie="supprimer_tout"), 0, TRI)


def test_valider_lien_hors_liste_et_bornes():
    _, conf, _, ext = ia.valider(_sortie(lien=5, confiance=140), 2, TRI)
    assert ext["lien"] is None and conf == 100
    _, _, _, ext = ia.valider(_sortie(lien=-1), 2, TRI)
    assert ext["lien"] is None


def test_valider_priorite_et_dates():
    _, _, prio, _ = ia.valider(_sortie(categorie="a_repondre", priorite="haute"), 0, TRI)
    assert prio == "haute"
    _, _, prio, _ = ia.valider(_sortie(categorie="offre", priorite="haute"), 0, TRI)
    assert prio is None
    _, _, _, ext = ia.valider(_sortie(categorie="evenement", date_evenement="2026-12-03"), 0, TRI)
    assert ext["date"] == "2026-12-03"
    _, _, _, ext = ia.valider(_sortie(categorie="alerte", peremption="bientôt"), 0, TRI)
    assert ext["peremption"] is None


def test_message_balise_et_tronque():
    mail = {"compte": "perso1", "expediteur": "a@b.fr", "objet": "Salut", "date": "2026-11-16",
            "corps_texte": "y" * (ia.CORPS_MAX + 50),
            "liens": json.dumps([{"url": f"https://d{i}.fr/x", "domaine": f"d{i}.fr"} for i in range(30)]),
            "pieces_jointes": ["facture.pdf"]}
    m = ia.message(mail, liste_blanche=True)
    assert "<mail>" in m and m.rstrip().endswith("</mail>")
    assert "[…corps tronqué]" in m and "liste blanche : oui" in m and "facture.pdf" in m
    assert "[19]" in m and "[20]" not in m


def test_cout():
    assert ia.Jetons(1_000_000, 1_000_000).cout() == pytest.approx(0.60)


# --- tri complet avec un faux client ----------------------------------------


class FauxClient:
    def __init__(self, reponses):
        self.reponses = reponses  # objet -> dict de sortie, ou "refus" / "panne"
        self.appels = []
        self.verrou = threading.Lock()
        self.messages = self

    def create(self, **kw):
        texte = kw["messages"][0]["content"]
        with self.verrou:
            self.appels.append(kw)
        objet = next(o for o in self.reponses if f"Objet : {o}\n" in texte)
        r = self.reponses[objet]
        if r == "panne":
            raise ConnectionError("réseau")
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_creation_input_tokens=0,
                                cache_read_input_tokens=800)
        if r == "refus":
            return SimpleNamespace(stop_reason="refusal", content=[], usage=usage)
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(r))])


@pytest.fixture
def con(tmp_path):
    c = db.connecter(tmp_path / "t.db")
    db.migrer(c)
    c.execute("INSERT INTO comptes (cle, nom, fournisseur) VALUES ('perso1', 'Principale', 'gmail')")
    for r in REGLES:
        c.execute("INSERT INTO regles (id, type, valeur, categorie, groupe, origine, etat, cree_le) "
                  "VALUES (?, ?, ?, ?, ?, 'migree', ?, 'x')", (r.id, r.type, r.valeur, r.categorie, r.groupe, r.etat))
    c.commit()
    return c


def _mail(con, n, expediteur, objet, corps="", a_pj=0, dossier="principal"):
    cur = con.execute(
        """INSERT INTO mails (compte, dossier, gmail_msgid, expediteur, objet, date, corps_texte, liens, a_pj,
             emplacement, vu_le) VALUES ('perso1', ?, ?, ?, ?, ?, ?, '[]', ?, ?, 'x')""",
        (dossier, n, expediteur, objet, f"2026-11-{n:02d}T10:00:00+00:00", corps, a_pj,
         "inbox" if dossier == "principal" else "envoyes"),
    )
    con.commit()
    return cur.lastrowid


def _peupler(con):
    _mail(con, 1, "a@b.fr", "Code", "Votre code de vérification : 1")                  # règle
    _mail(con, 2, "Amazon <ship@amazon.fr>", "Expédié", a_pj=1)                         # IA, PJ
    _mail(con, 3, "Paul <paul@x.fr>", "Question")                                        # IA, À répondre
    _mail(con, 4, "Shop <noreply@shop.fr>", "Douteux")                                   # IA sous le seuil
    _mail(con, 5, "z@y.fr", "Bizarre")                                                   # refus
    _mail(con, 6, "moi@b.fr", "Envoyé", dossier="envoyes")                               # jamais trié
    return {
        "Expédié": _sortie(),
        "Question": _sortie(categorie="a_repondre", priorite="haute", confiance=88),
        "Douteux": _sortie(categorie="offre", confiance=55),
        "Bizarre": "refus",
    }


def _decisions(con):
    return {r["objet"]: dict(r) for r in con.execute(
        "SELECT m.objet, d.* FROM decisions d JOIN mails m ON m.id = d.mail_id")}


def test_trier(con):
    client = FauxClient(_peupler(con))
    r = trier(con, "perso1", moteur=ia.MoteurAPI(client))
    assert (r.a_trier, r.tries, r.par_regle, r.par_ia, r.sous_seuil, r.erreurs) == (5, 4, 1, 3, 1, 1)
    assert len(client.appels) == 4
    appel = client.appels[0]
    assert appel["model"] == "claude-haiku-5-5"
    assert appel["output_config"]["format"]["schema"]["properties"]["categorie"]["enum"] == list(TRI.categories)
    d = _decisions(con)
    assert "Envoyé" not in d
    assert (d["Code"]["source"], d["Code"]["categorie_auto"], d["Code"]["action"]) == ("regle", "code_unique", "suppression")
    assert (d["Expédié"]["categorie_auto"], d["Expédié"]["action"]) == ("commande", "conserves")
    assert json.loads(d["Expédié"]["extraction"])["numero"] == "402-1"
    assert (d["Question"]["priorite"], d["Question"]["action"]) == ("haute", "garder")
    assert (d["Douteux"]["categorie_auto"], d["Douteux"]["categorie_ia"], d["Douteux"]["action"]) == ("a_trier", "offre", "garder")
    assert json.loads(d["Douteux"]["regles_proposees"]) == [2]
    assert d["Bizarre"]["erreur"] == "arrêt : refusal" and d["Bizarre"]["categorie_auto"] == "a_trier"
    p = con.execute("SELECT * FROM passages WHERE id = ?", (r.passage_id,)).fetchone()
    assert (p["mails_lus"], p["mails_tries"], p["appels_ia"], p["jetons_entree"], p["jetons_cache_lus"]) == (5, 4, 4, 4000, 3200)
    assert p["modele"] == "claude-haiku-5-5/api" and p["fin"] and "refusal" in p["erreur"]


def test_trier_rejouable_et_reprise_des_erreurs(con):
    reponses = _peupler(con)
    trier(con, "perso1", moteur=ia.MoteurAPI(FauxClient(reponses)))
    reponses["Bizarre"] = _sortie(categorie="autre_supprime", confiance=95)
    client = FauxClient(reponses)
    r = trier(con, "perso1", moteur=ia.MoteurAPI(client))
    assert (r.a_trier, r.par_ia, r.erreurs) == (1, 1, 0)  # seul le refus est repris
    assert _decisions(con)["Bizarre"]["erreur"] is None
    assert con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 5
    assert trier(con, "perso1", moteur=ia.MoteurAPI(FauxClient(reponses))).a_trier == 0


def test_refaire_garde_les_corrections(con):
    reponses = _peupler(con)
    trier(con, "perso1", moteur=ia.MoteurAPI(FauxClient(reponses)))
    con.execute("UPDATE decisions SET categorie = 'offre', corrige_le = 'x' WHERE mail_id = 3")
    con.commit()
    r = trier(con, "perso1", moteur=ia.MoteurAPI(FauxClient(reponses)), refaire=True)
    assert r.a_trier == 4
    assert con.execute("SELECT categorie FROM decisions WHERE mail_id = 3").fetchone()[0] == "offre"


def test_panne_reseau(con):
    _mail(con, 1, "a@b.fr", "Panne")
    r = trier(con, "perso1", moteur=ia.MoteurAPI(FauxClient({"Panne": "panne"})))
    assert r.erreurs == 1 and _decisions(con)["Panne"]["erreur"] == "appel : ConnectionError"


def test_action_prevue():
    c = cats.charger(_con_memoire())
    assert action_prevue(c["a_repondre"], True) == "garder"
    assert action_prevue(c["a_trier"], False) == "garder"
    assert action_prevue(None, False) == "garder"
    assert action_prevue(c["recu_paiement"], True) == "conserves"
    assert action_prevue(c["offre"], False) == "suppression"
    assert action_prevue(c["candidature"], False, {"statut": "refus"}) == "suppression"
    for statut in ("entretien", "test", "proposition"):
        assert action_prevue(c["candidature"], False, {"statut": statut}) == "garder"
    assert action_prevue(c["candidature"], False, {"statut": "suggestion"}) == "suppression"
    # écrite par une personne (Toma en copie, par exemple) : gardée
    assert action_prevue(c["candidature"], False, {"statut": "envoyee", "personne": True}) == "garder"
    assert action_prevue(c["offre"], False, {"personne": True}) == "suppression"


def _con_memoire():
    c = db.connecter(":memory:")
    db.migrer(c)
    return c


def test_rapport(con):
    trier(con, "perso1", moteur=ia.MoteurAPI(FauxClient(_peupler(con))))
    resume, echantillon = rapport(con, "perso1", echantillon=10)
    assert "5 décisions pour 5 mails reçus" in resume
    assert "notifications_auto → autre_supprime" in resume
    assert "arrêt : refusal" in resume and "$" in resume
    assert "Votre code de vérification" not in resume + echantillon  # jamais de corps
    assert "[commande]" in echantillon and "402-1" in echantillon


# --- abonnement (claude -p, comme RogerLeJardinier) --------------------------


class FauxClaude:
    """Remplace subprocess.run : objet -> sortie JSON de `claude -p`, ou texte de limite."""

    def __init__(self, reponses):
        self.reponses = reponses
        self.appels = []
        self.verrou = threading.Lock()

    def __call__(self, commande, input, env, **kw):
        with self.verrou:
            self.appels.append((commande, env))
        objet = next(o for o in self.reponses if f"Objet : {o}\n" in input)
        r = self.reponses[objet]
        usage = {"input_tokens": 50, "output_tokens": 80, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 1200}
        if r == "limite":
            sortie = {"type": "result", "is_error": True, "result": "You've hit your session limit · resets 7pm"}
            return SimpleNamespace(returncode=1, stdout=json.dumps(sortie), stderr="")
        sortie = {"type": "result", "subtype": "success", "is_error": False, "result": "",
                  "structured_output": r, "usage": usage}
        return SimpleNamespace(returncode=0, stdout=json.dumps(sortie), stderr="")


def _abonnement(tmp_path, faux, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ne-doit-pas-passer")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    return ia.MoteurAbonnement(jeton="jeton-test", executable="claude", dossier=tmp_path / "claude", executer=faux)


def test_abonnement(con, tmp_path, monkeypatch):
    faux = FauxClaude(_peupler(con) | {"Bizarre": _sortie(categorie="cgu", confiance=99)})
    r = trier(con, "perso1", moteur=_abonnement(tmp_path, faux, monkeypatch))
    assert (r.par_ia, r.erreurs, r.moteur) == (4, 0, "abonnement")
    commande, env = faux.appels[0]
    assert commande[:2] == ["claude", "-p"] and "--restricted" in commande
    assert commande[commande.index("--tools") + 1] == "" and commande[commande.index("--model") + 1] == ia.MODELE
    assert json.loads(commande[commande.index("--json-schema") + 1]) == TRI.schema
    assert "--no-session-persistence" in commande
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_SIMPLE" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "jeton-test" and env["CLAUDE_CONFIG_DIR"].endswith("claude")
    assert _decisions(con)["Expédié"]["categorie_auto"] == "commande"
    p = con.execute("SELECT * FROM passages WHERE id = ?", (r.passage_id,)).fetchone()
    assert p["modele"] == "claude-haiku-5-5/abonnement" and p["jetons_cache_lus"] == 4800
    resume, _ = rapport(con, "perso1")
    assert "abonnement Claude" in resume and "équivalent API" in resume


def test_abonnement_limite_arrete_le_passage(con, tmp_path, monkeypatch):
    reponses = _peupler(con)
    reponses = {o: "limite" for o in reponses}
    r = trier(con, "perso1", moteur=_abonnement(tmp_path, FauxClaude(reponses), monkeypatch))
    assert r.limite and "session limit" in r.limite
    assert (r.par_ia, r.erreurs, r.non_tries) == (0, 0, 4)
    # rien d'enregistré pour ces mails : ils seront triés au passage suivant
    assert set(_decisions(con)) == {"Code"}
    assert "limite" in con.execute("SELECT erreur FROM passages WHERE id = ?", (r.passage_id,)).fetchone()[0]


def test_abonnement_sans_jeton(tmp_path):
    m = ia.MoteurAbonnement(jeton="", executable="claude", dossier=tmp_path, executer=None)
    assert m.appeler("x", TRI).erreur == "appel : CLAUDE_CODE_OAUTH_TOKEN absent"


# --- catégories en base -------------------------------------------------------


def test_categories_et_rubriques_en_base():
    c = _con_memoire()
    categories = cats.charger(c)
    assert {"candidature", "a_trier", "liste_blanche"} <= set(categories)
    assert categories["candidature"].rubrique == "applications" and categories["candidature"].sort == "suppression"
    assert "a_trier" not in TRI.categories and "liste_blanche" not in TRI.categories
    assert dict(c.execute("SELECT cle, nom FROM rubriques"))["applications"] == "Candidatures"
    assert "Indeed" in TRI.consigne and "- candidature :" in TRI.consigne


def test_nouvelle_categorie_sans_code(tmp_path):
    """Une ligne de plus dans categories : l'IA la reçoit (consigne et liste fermée)."""
    c = _con_memoire()
    c.execute("INSERT INTO rubriques (cle, nom, page) VALUES ('assos', 'Associations', 'notifs')")
    c.execute("""INSERT INTO categories (cle, nom, description, sort, comportement, rubrique)
                 VALUES ('association', 'Association', 'vie associative du club de voile', 'suppression', 'simple', 'assos')""")
    c.execute("UPDATE categories SET active = 0 WHERE cle = 'cgu'")
    tri = ia.Tri.depuis(cats.charger(c))
    assert "association" in tri.schema["properties"]["categorie"]["enum"]
    assert "cgu" not in tri.schema["properties"]["categorie"]["enum"]
    assert "- association : vie associative" in tri.consigne
    cat, _, _, ext = ia.valider(_sortie(categorie="association"), 2, tri)
    assert cat == "association" and set(ext) == {"resume", "lien", "personne"}


def test_candidature():
    cat, _, prio, ext = ia.valider(_sortie(categorie="candidature", entreprise="SPIE", poste="Stage Data",
                                           reference="R-12", statut_candidature="entretien",
                                           date_evenement="2026-11-20", expediteur_personne=True), 0, TRI)
    assert cat == "candidature" and prio is None
    assert ext == {"resume": "Colis expédié.", "lien": None, "personne": True, "entreprise": "SPIE", "poste": "Stage Data",
                   "reference": "R-12", "statut": "entretien", "date": "2026-11-20"}


def test_expediteur_adresse_exacte():
    regles = [R(1, "expediteur", "noreply@linkedin.com", "social"), R(2, "expediteur", "noreply@", "autre_supprime")]
    assert appliquer(regles, "LinkedIn <noreply@linkedin.com>", "", "").regle.id == 1
    # security-noreply@linkedin.com n'est plus pris pour noreply@linkedin.com
    assert appliquer(regles, "LinkedIn <security-noreply@linkedin.com>", "", "").regle.id == 2


def test_regle_vers_categorie_inactive_ignoree(con):
    con.execute("UPDATE categories SET active = 0 WHERE cle = 'code_unique'")
    con.commit()
    _mail(con, 1, "a@b.fr", "Code", "Votre code de vérification : 1")
    r = trier(con, "perso1", moteur=ia.MoteurAPI(FauxClient({"Code": _sortie(categorie="autre_supprime")})))
    assert (r.par_regle, r.par_ia) == (0, 1)


def test_retrier_une_categorie(con):
    reponses = _peupler(con)
    trier(con, "perso1", moteur=ia.MoteurAPI(FauxClient(reponses)))
    client = FauxClient(reponses)
    r = trier(con, "perso1", moteur=ia.MoteurAPI(client), categorie="offre")
    # « Douteux » : sous le seuil, proposé offre par l'IA ; les autres décisions ne bougent pas
    assert r.a_trier == 1 and len(client.appels) == 1


def test_a_repondre_prime_sur_candidature():
    assert "au sujet d'un stage ou d'une candidature" in TRI.categories["a_repondre"].description
