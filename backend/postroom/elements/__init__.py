"""Éléments affichés par le front, construits à partir des décisions de tri.

Chaque catégorie a un comportement (table categories) ; le code ne connaît que les comportements.
Tout se passe en base : rien ne touche une boîte.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from postroom.elements import alertes, attente, candidatures, commandes, evenements, expirations, flux, offres, reponse
from postroom.elements.commun import Mail, mail_depuis, reglages
from postroom.tri import categories as cats

# comportement -> création ou mise à jour de l'élément d'un mail ; « reponse » est tenu à jour fil
# par fil (reponse.actualiser), « aucun » n'a qu'une ligne dans le Flux
COMPORTEMENTS = {
    "commande": commandes.traiter,
    "evenement": evenements.traiter,
    "alerte": alertes.traiter,
    "offre": offres.traiter,
    "simple": offres.traiter_simple,
    "candidature": candidatures.traiter,
}


@dataclass
class RapportElements:
    traites: int = 0
    par_comportement: dict = field(default_factory=dict)
    reponses: dict = field(default_factory=dict)
    attentes: dict = field(default_factory=dict)
    expirations: dict = field(default_factory=dict)


def a_traiter(con: sqlite3.Connection, compte: str) -> list[sqlite3.Row]:
    """Mails reçus dont la décision n'a pas encore donné d'élément (À trier et erreurs exclus :
    Toma valide d'abord dans Debug), du plus ancien au plus récent pour faire avancer les suivis."""
    return con.execute(
        """SELECT m.id, m.compte, m.thread_id, m.message_id, m.expediteur, m.objet, m.date, m.corps_texte,
                  m.liens, m.a_pj, m.emplacement, d.id AS decision_id, d.priorite, d.extraction,
                  COALESCE(d.categorie, d.categorie_auto) AS categorie
           FROM mails m JOIN decisions d ON d.mail_id = m.id
           WHERE m.compte = ? AND m.dossier = 'principal' AND d.traite_le IS NULL AND d.erreur IS NULL
             AND COALESCE(d.categorie, d.categorie_auto) <> ?
           ORDER BY m.date, m.id""",
        (compte, cats.A_TRIER),
    ).fetchall()


def effacer(con: sqlite3.Connection, compte: str) -> None:
    """Pour --refaire : repart de zéro pour ce compte (éléments, Flux, envois suivis)."""
    con.execute("DELETE FROM elements WHERE compte = ?", (compte,))
    con.execute("DELETE FROM flux WHERE compte = ?", (compte,))
    con.execute("DELETE FROM envoyes WHERE mail_id IN (SELECT id FROM mails WHERE compte = ?)", (compte,))
    con.execute("UPDATE decisions SET traite_le = NULL WHERE mail_id IN (SELECT id FROM mails WHERE compte = ?)",
                (compte,))


def construire(con: sqlite3.Connection, compte: str, adresses: set[str] = frozenset(),
               quand: datetime | None = None, refaire: bool = False) -> RapportElements:
    """Transforme les nouvelles décisions de `compte` en éléments et lignes de Flux, tient à jour
    À répondre et En attente, puis applique les expirations. `adresses` : adresses de Toma."""
    quand = quand or datetime.now(timezone.utc)
    if refaire:
        effacer(con, compte)
    categories = cats.charger(con)
    reglages_ = reglages(con)
    r = RapportElements()
    for ligne in a_traiter(con, compte):
        categorie = categories.get(ligne["categorie"])
        if categorie is None:  # catégorie désactivée : attend qu'elle revienne ou une correction
            continue
        m: Mail = mail_depuis(ligne, categorie)
        traiter = COMPORTEMENTS.get(categorie.comportement)
        if traiter:
            traiter(con, m, reglages_)
        flux.ajouter(con, m)
        con.execute("UPDATE decisions SET traite_le = ? WHERE id = ?",
                    (quand.isoformat(timespec="seconds"), ligne["decision_id"]))
        r.traites += 1
        r.par_comportement[categorie.comportement] = r.par_comportement.get(categorie.comportement, 0) + 1
    r.reponses = reponse.actualiser(con, compte, quand)
    r.attentes = attente.actualiser(con, compte, adresses, reglages_, quand)
    r.expirations = expirations.appliquer(con, compte, reglages_, quand)
    con.commit()
    return r
