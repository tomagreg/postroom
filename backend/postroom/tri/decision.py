"""Décision de tri d'un mail reçu : règles, puis IA ; seuil de confiance et action prévue.

L'action prévue est seulement enregistrée : rien ne touche la boîte avant le jalon 4.
"""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from postroom.db import maintenant
from postroom.tri import ia
from postroom.tri.regles import Correspondance, appliquer, charger

GARDER = ("a_repondre", "a_trier")
APPELS_SIMULTANES = 4


def action_prevue(categorie: str, a_pj: bool) -> str:
    """garder (reste dans la boîte) · conserves (vraie pièce jointe) · suppression (§ 5 du plan)."""
    if categorie in GARDER:
        return "garder"
    return "conserves" if a_pj else "suppression"


def seuil(con: sqlite3.Connection) -> int:
    ligne = con.execute("SELECT valeur FROM reglages WHERE cle = 'seuil_confiance'").fetchone()
    return int(ligne[0]) if ligne else 70


@dataclass
class RapportTri:
    passage_id: int = 0
    a_trier: int = 0  # mails à traiter
    tries: int = 0
    par_regle: int = 0
    par_ia: int = 0
    sous_seuil: int = 0
    erreurs: int = 0
    non_tries: int = 0  # laissés pour le passage suivant (limite de l'abonnement)
    limite: str | None = None
    moteur: str = ""
    jetons: ia.Jetons = field(default_factory=ia.Jetons)
    detail_erreurs: dict = field(default_factory=dict)


def mails_a_trier(con, compte: str, refaire: bool = False, limite: int | None = None) -> list[dict]:
    """Mails reçus encore dans la boîte, sans décision ou dont l'IA a échoué
    (avec `refaire` : toutes les décisions non corrigées par Toma)."""
    condition = "d.id IS NULL OR d.erreur IS NOT NULL" + (" OR d.corrige_le IS NULL" if refaire else "")
    lignes = con.execute(
        f"""SELECT m.id, m.compte, m.expediteur, m.objet, m.date, m.corps_texte, m.liens, m.a_pj
            FROM mails m LEFT JOIN decisions d ON d.mail_id = m.id
            WHERE m.compte = ? AND m.dossier = 'principal' AND m.emplacement = 'inbox' AND ({condition})
            ORDER BY m.date DESC""" + (" LIMIT ?" if limite else ""),
        (compte, limite) if limite else (compte,),
    ).fetchall()
    mails = [dict(l) for l in lignes]
    for m in mails:
        m["pieces_jointes"] = [
            r[0] or "sans nom" for r in con.execute("SELECT nom FROM pieces_jointes WHERE mail_id = ?", (m["id"],))
        ]
    return mails


def _enregistrer(con, mail: dict, c: Correspondance, r: ia.ResultatIA | None, seuil_: int, passage_id: int) -> str:
    proposees = json.dumps([p.id for p in c.proposees]) if c.proposees else None
    if r is None:  # règle active
        valeurs = dict(categorie_auto=c.regle.categorie, categorie_ia=None, source="regle", regle_id=c.regle.id,
                       confiance=100, priorite=None, extraction=None, erreur=None)
    elif r.erreur:
        valeurs = dict(categorie_auto="a_trier", categorie_ia=None, source="ia", regle_id=None,
                       confiance=None, priorite=None, extraction=None, erreur=r.erreur)
    else:
        auto = r.categorie if r.confiance >= seuil_ else "a_trier"
        valeurs = dict(categorie_auto=auto, categorie_ia=r.categorie, source="ia", regle_id=None,
                       confiance=r.confiance, priorite=r.priorite if auto == "a_repondre" else None,
                       extraction=json.dumps(r.extraction, ensure_ascii=False), erreur=None)
    valeurs["action"] = action_prevue(valeurs["categorie_auto"], bool(mail["a_pj"]))
    con.execute(
        """INSERT INTO decisions (mail_id, categorie_auto, categorie_ia, source, regle_id, confiance, priorite,
             action, extraction, regles_proposees, passage_id, erreur, decide_le)
           VALUES (:mail_id, :categorie_auto, :categorie_ia, :source, :regle_id, :confiance, :priorite,
             :action, :extraction, :regles_proposees, :passage_id, :erreur, :decide_le)
           ON CONFLICT (mail_id) DO UPDATE SET categorie_auto = excluded.categorie_auto,
             categorie_ia = excluded.categorie_ia, source = excluded.source, regle_id = excluded.regle_id,
             confiance = excluded.confiance, priorite = excluded.priorite, action = excluded.action,
             extraction = excluded.extraction, regles_proposees = excluded.regles_proposees,
             passage_id = excluded.passage_id, erreur = excluded.erreur, decide_le = excluded.decide_le""",
        dict(valeurs, mail_id=mail["id"], regles_proposees=proposees, passage_id=passage_id, decide_le=maintenant()),
    )
    con.commit()
    return valeurs["categorie_auto"]


def trier(con: sqlite3.Connection, compte: str, moteur=None, refaire: bool = False,
          limite: int | None = None) -> RapportTri:
    """Trie les mails reçus de `compte` et note le passage (jetons compris). Si la limite de
    l'abonnement est atteinte, les mails restants attendent le passage suivant."""
    regles = charger(con)
    seuil_ = seuil(con)
    mails = mails_a_trier(con, compte, refaire, limite)
    rapport = RapportTri(a_trier=len(mails))
    analyses = [(m, appliquer(regles, m["expediteur"], m["objet"], m["corps_texte"])) for m in mails]
    pour_ia = [(m, c) for m, c in analyses if c.regle is None]
    if pour_ia:
        moteur = moteur or ia.moteur_par_defaut()
    rapport.moteur = getattr(moteur, "nom", "")
    modele = f"{ia.MODELE}/{rapport.moteur}" if rapport.moteur else ia.MODELE
    cur = con.execute("INSERT INTO passages (debut, compte, modele) VALUES (?, ?, ?)", (maintenant(), compte, modele))
    rapport.passage_id = cur.lastrowid
    con.commit()

    for m, c in analyses:
        if c.regle is not None:
            _enregistrer(con, m, c, None, seuil_, rapport.passage_id)
            rapport.par_regle += 1
            rapport.tries += 1

    arret = threading.Event()

    def classer(mc):
        if arret.is_set():
            return None
        try:
            return ia.classer(moteur, mc[0], mc[1].liste_blanche)
        except ia.LimiteAtteinte as e:
            arret.set()
            return e

    if pour_ia:
        with ThreadPoolExecutor(APPELS_SIMULTANES) as pool:
            for (m, c), r in zip(pour_ia, pool.map(classer, pour_ia)):
                if r is None or isinstance(r, ia.LimiteAtteinte):
                    rapport.non_tries += 1
                    if r is not None:
                        rapport.limite = str(r) or "limite atteinte"
                    continue
                rapport.jetons.ajouter(r.jetons)
                categorie = _enregistrer(con, m, c, r, seuil_, rapport.passage_id)
                if r.erreur:
                    rapport.erreurs += 1
                    rapport.detail_erreurs[r.erreur] = rapport.detail_erreurs.get(r.erreur, 0) + 1
                    continue
                rapport.par_ia += 1
                rapport.tries += 1
                rapport.sous_seuil += categorie == "a_trier"

    j = rapport.jetons
    con.execute(
        """UPDATE passages SET fin = ?, mails_lus = ?, mails_tries = ?, appels_ia = ?, jetons_entree = ?,
             jetons_sortie = ?, jetons_cache_ecrits = ?, jetons_cache_lus = ?, erreur = ? WHERE id = ?""",
        (maintenant(), len(mails), rapport.tries, len(pour_ia) - rapport.non_tries, j.entree, j.sortie, j.cache_ecrits,
         j.cache_lus, "; ".join([f"limite : {rapport.limite}"] * bool(rapport.limite)
                                + [f"{k} ×{n}" for k, n in rapport.detail_erreurs.items()]) or None,
         rapport.passage_id),
    )
    con.commit()
    return rapport
