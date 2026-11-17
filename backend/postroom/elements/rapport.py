"""Rapport sur les éléments d'une boîte : ce que le front affichera, sans aucun corps de mail."""

import json
import sqlite3
from datetime import datetime

from postroom.config import FUSEAU
from postroom.elements.commun import instant

ETAPES = {1: "Commandé", 2: "Expédié", 3: "En livraison", 4: "Livré"}


def _jour(valeur) -> str:
    d = instant(valeur)
    return d.astimezone(FUSEAU).strftime("%d/%m") if d else "  ?  "


def _court(texte, n=70) -> str:
    texte = " ".join((texte or "").split())
    return texte if len(texte) <= n else texte[: n - 1] + "…"


def _etat(e, quand: datetime) -> str:
    if e["supprime_le"]:
        return "supprimé"
    if e["retire_le"]:
        return f"retiré ({e['retire_motif']})"
    fin = instant(e["expire_le"])
    return "Expiré" if fin and quand >= fin else "actif"


def rapport(con: sqlite3.Connection, compte: str, quand: datetime) -> str:
    els = con.execute("SELECT * FROM elements WHERE compte = ? ORDER BY type, date DESC", (compte,)).fetchall()
    out = [f"Éléments de {compte} au {quand.astimezone(FUSEAU):%d/%m/%Y %H:%M} : {len(els)} au total", "",
           "  Par type (actifs · « Expiré » affichés · retirés) :"]
    types: dict[str, list[int]] = {}
    for e in els:
        etat = _etat(e, quand)
        t = types.setdefault(e["type"], [0, 0, 0])
        t[0 if etat == "actif" else 1 if etat == "Expiré" else 2] += 1
    for t, (a, x, r) in sorted(types.items()):
        out.append(f"    {t:12} {a:4} · {x:3} · {r:4}")
    motifs = con.execute("SELECT retire_motif, COUNT(*) FROM elements WHERE compte = ? AND retire_le IS NOT NULL "
                         "GROUP BY 1 ORDER BY 2 DESC", (compte,)).fetchall()
    if motifs:
        out.append("    motifs de retrait : " + ", ".join(f"{m} {n}" for m, n in motifs))

    actifs = [e for e in els if _etat(e, quand) in ("actif", "Expiré")]

    def section(titre, type_, ligne):
        lignes = [e for e in actifs if e["type"] == type_]
        out.extend(["", f"  {titre} ({len(lignes)}) :"])
        out.extend(ligne(e) for e in lignes) if lignes else out.append("    (aucun)")

    section("À répondre", "reponse", lambda e: f"    {_jour(e['date'])} {e['priorite'] or '':8}"
            f"{' RELANCE' if e['relance'] else ''} {_court(e['titre'], 55)} — {_court(e['sous_titre'], 30)}")
    section("En attente", "attente", lambda e: f"    envoyé {_jour(e['date'])} à {_court(e['sous_titre'], 30):30} "
            f"{'Sans réponse' if quand >= instant(e['echeance']) else 'en attente':12} {_court(e['titre'], 45)}")

    out.extend(["", "  Commandes :"])
    for e in [e for e in els if e["type"] == "commande"]:
        c = con.execute("SELECT * FROM commandes WHERE element_id = ?", (e["id"],)).fetchone()
        dates = json.loads(c["dates"] or "{}")
        etapes = " → ".join(f"{ETAPES[int(k)]} {_jour(v)}" for k, v in sorted(dates.items()))
        out.append(f"    [{_etat(e, quand)}] {_court(e['titre'], 45)} — {c['boutique'] or '?'} n° {c['numero'] or '—'}"
                   f" : {ETAPES.get(c['etape'], '?')} ({etapes})")

    section("Événements", "evenement", lambda e: f"    {e['echeance'] or 'sans date':10} [{_etat(e, quand)}] "
            f"{_court(e['titre'], 60)}")
    out.extend(["", "  Candidatures suivies :"])
    for e in [e for e in els if e["type"] == "candidature" and e["sous_type"] != "suggestion"]:
        c = con.execute("SELECT * FROM candidatures WHERE element_id = ?", (e["id"],)).fetchone()
        if c is None:
            out.append(f"    [{_etat(e, quand)}] {_court(e['titre'], 50)} — sans entreprise ({e['sous_type']})")
            continue
        hist = " → ".join(f"{h['statut']} {_jour(h['date'])}" for h in json.loads(c["historique"] or "[]"))
        out.append(f"    [{_etat(e, quand)}] {_court(c['entreprise'], 18):18} {_court(c['poste'], 45):45} "
                   f"{c['statut']:10} ({hist})")
    sugg = [e for e in els if e["type"] == "candidature" and e["sous_type"] == "suggestion"]
    out.append(f"    suggestions de stage : {len(sugg)} cartes, dont {sum(_etat(e, quand) == 'actif' for e in sugg)}"
               " actives")
    section("Alertes", "alerte", lambda e: f"    {_jour(e['date'])} expire {_jour(e['expire_le'])} "
            f"{(e['domaine'] or '—'):22} {_court(e['titre'], 50)}")
    section("Offres", "offre", lambda e: f"    {_jour(e['date'])} expire {_jour(e['expire_le'])} {e['sous_type']:7} "
            f"{_court(e['sous_titre'], 20):20} {_court(e['titre'], 45)}")

    out.extend(["", "  Flux (lignes par type) :"])
    for t, n in con.execute("SELECT type, COUNT(*) FROM flux WHERE compte = ? GROUP BY 1 ORDER BY 2 DESC", (compte,)):
        out.append(f"    {t:12} {n:4}")
    out.extend(["", "  Flux, 12 lignes les plus récentes :"])
    for f in con.execute("SELECT heure, type, texte FROM flux WHERE compte = ? ORDER BY heure DESC LIMIT 12", (compte,)):
        out.append(f"    {_jour(f['heure'])} {f['type']:11} {_court(f['texte'], 80)}")
    return "\n".join(out)
