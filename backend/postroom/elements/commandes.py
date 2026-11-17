"""Éléments : commandes. Un élément par commande, dont l'étape avance mail après mail.

Clé de la commande : numéro Amazon lu dans le mail, sinon numéro et boutique donnés par l'IA,
sinon le fil Gmail. L'étape ne recule jamais ; l'élément est retiré quelques jours après « Livré ».
"""

import json
import re

from postroom.elements.commun import Mail, apres, duree, element, normaliser, plus_recent, propre, rattacher

NUMERO_AMAZON = re.compile(r"\b(\d{3}-\d{7}-\d{7})\b")
LIVRE = 4
# « Commandés : », « Expédié : », « Livré : »… en tête d'objet (Amazon et la plupart des boutiques)
_PREFIXE = re.compile(
    r"^\s*(?:commandée?s?|expédiée?s?|livrée?s?|en cours de livraison|arrive aujourd'hui|livraison prévue"
    r"|ordered|shipped|delivered|out for delivery)\s*:\s*",
    re.IGNORECASE,
)


def numero_amazon(m: Mail) -> str | None:
    for texte in (m.objet or "", (m.corps_texte or "")[:5000]):
        trouve = NUMERO_AMAZON.search(texte)
        if trouve:
            return trouve[1]
    return None


def cle_commande(m: Mail) -> tuple[str, str | None, str | None]:
    """(clé, boutique, numéro)."""
    boutique = propre(m.extraction.get("boutique"))
    amazon = numero_amazon(m)
    if amazon:
        return f"amazon:{amazon}", boutique or "Amazon", amazon
    numero = propre(m.extraction.get("numero"))
    if numero:
        return f"{normaliser(boutique).split(' ')[0] if boutique else ''}:{normaliser(numero)}", boutique, numero
    return f"fil:{m.fil}", boutique, None


def titre(objet: str | None) -> str:
    return _PREFIXE.sub("", objet or "").strip() or (objet or "")


def traiter(con, m: Mail, reglages: dict) -> int:
    cle, boutique, numero = cle_commande(m)
    etape = m.extraction.get("etape") if m.extraction.get("etape") in (1, 2, 3, 4) else 1
    ancien = con.execute(
        """SELECT e.id, e.titre, e.date, c.etape, c.dates, c.boutique, c.numero FROM elements e
           LEFT JOIN commandes c ON c.element_id = e.id WHERE e.compte = ? AND e.type = 'commande' AND e.cle = ?""",
        (m.compte, cle),
    ).fetchone()
    dates = json.loads(ancien["dates"] or "{}") if ancien else {}
    etape_finale = max(etape, (ancien["etape"] or 1) if ancien else 1)
    dates.setdefault(str(etape), m.date)
    url, domaine = m.lien()
    champs = dict(sous_type=m.categorie.cle, categorie=m.categorie.cle, titre=(ancien["titre"] if ancien else None)
                  or titre(m.objet), sous_titre=boutique or (ancien["boutique"] if ancien else None))
    if not ancien or plus_recent(ancien["date"], m.date):
        champs.update(resume=m.resume or None, mail_id=m.id, date=m.date)
        if url:
            champs.update(lien=url, domaine=domaine)
    champs["expire_le"] = (apres(dates.get(str(LIVRE)), duree(m.categorie, reglages, "commande_livree_jours"))
                           if etape_finale == LIVRE else None)
    element_id = element(con, m.compte, "commande", cle, **champs)
    rattacher(con, element_id, m.id)
    con.execute(
        """INSERT INTO commandes (element_id, boutique, numero, etape, dates) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (element_id) DO UPDATE SET boutique = COALESCE(excluded.boutique, boutique),
             numero = COALESCE(excluded.numero, numero), etape = excluded.etape, dates = excluded.dates""",
        (element_id, boutique, numero, etape_finale, json.dumps(dates)),
    )
    return element_id
