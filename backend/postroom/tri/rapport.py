"""Rapport sur le tri d'une boîte : répartition, confiance, règles proposées, coût, échantillon à relire."""

import json
import random
import sqlite3
from email.utils import parseaddr

from postroom.tri import ia

TRANCHES = ((90, 101, "90–100"), (70, 90, "70–89"), (50, 70, "50–69"), (0, 50, "0–49"))


def _ligne(titre: str, n: int, total: int) -> str:
    return f"    {titre:22} {n:5}  {100 * n / total:5.1f} %" if total else f"    {titre:22} {n:5}"


def rapport(con: sqlite3.Connection, compte: str, echantillon: int = 40, graine: int = 17) -> tuple[str, str]:
    """Renvoie (résumé, échantillon) en texte ; aucun corps de mail."""
    base = """FROM decisions d JOIN mails m ON m.id = d.mail_id
              WHERE m.compte = ? AND m.dossier = 'principal'"""
    total = con.execute(f"SELECT COUNT(*) {base}", (compte,)).fetchone()[0]
    recus = con.execute("SELECT COUNT(*) FROM mails WHERE compte = ? AND dossier = 'principal'", (compte,)).fetchone()[0]
    out = [f"Tri de {compte} : {total} décisions pour {recus} mails reçus", "", "  Catégories (après seuil) :"]
    for cat, n in con.execute(f"SELECT d.categorie_auto, COUNT(*) {base} GROUP BY 1 ORDER BY 2 DESC", (compte,)):
        out.append(_ligne(cat, n, total))

    out += ["", "  Source :"]
    for src, n in con.execute(f"SELECT d.source, COUNT(*) {base} GROUP BY 1 ORDER BY 2 DESC", (compte,)):
        out.append(_ligne(src, n, total))
    out += ["", "  Règles qui ont décidé :"]
    for groupe, cat, n in con.execute(
        """SELECT r.groupe, r.categorie, COUNT(*)
           FROM decisions d JOIN mails m ON m.id = d.mail_id JOIN regles r ON r.id = d.regle_id
           WHERE m.compte = ? AND m.dossier = 'principal' AND d.source = 'regle'
           GROUP BY 1, 2 ORDER BY 3 DESC""",
        (compte,),
    ):
        out.append(f"    {groupe:22} → {cat:16} {n:5}")

    ia_ok = con.execute(f"SELECT d.confiance {base} AND d.source = 'ia' AND d.erreur IS NULL", (compte,)).fetchall()
    out += ["", f"  Confiance de l'IA ({len(ia_ok)} mails) :"]
    for bas, haut, titre in TRANCHES:
        out.append(_ligne(titre, sum(bas <= r[0] < haut for r in ia_ok), len(ia_ok)))
    sous = con.execute(
        f"SELECT d.categorie_ia, COUNT(*) {base} AND d.categorie_auto = 'a_trier' AND d.erreur IS NULL GROUP BY 1 ORDER BY 2 DESC",
        (compte,),
    ).fetchall()
    if sous:
        out.append("    sous le seuil, proposition de l'IA : " + ", ".join(f"{c} {n}" for c, n in sous))
    erreurs = con.execute(f"SELECT d.erreur, COUNT(*) {base} AND d.erreur IS NOT NULL GROUP BY 1", (compte,)).fetchall()
    out.append("    erreurs : " + (", ".join(f"{e} ×{n}" for e, n in erreurs) if erreurs else "aucune"))

    out += ["", "  Actions prévues (rien n'est fait sur la boîte) :"]
    for action, n in con.execute(f"SELECT d.action, COUNT(*) {base} GROUP BY 1 ORDER BY 2 DESC", (compte,)):
        out.append(_ligne(action, n, total))

    regles = {r[0]: r for r in con.execute("SELECT id, groupe, categorie FROM regles")}
    proposees: dict[str, list[int]] = {}
    for ids, categorie_ia, categorie in con.execute(
        f"SELECT d.regles_proposees, d.categorie_ia, d.categorie_auto {base} AND d.regles_proposees IS NOT NULL",
        (compte,),
    ):
        for i in json.loads(ids):
            _, groupe, cat_regle = regles[i]
            compte_ = proposees.setdefault(f"{groupe} → {cat_regle}", [0, 0])
            compte_[0] += 1
            compte_[1] += (categorie_ia or categorie) == cat_regle
    out += ["", "  Règles proposées (non appliquées) : mails attrapés, dont l'IA d'accord"]
    for nom, (n, accord) in sorted(proposees.items(), key=lambda x: -x[1][0]):
        out.append(f"    {nom:36} {n:5}  {accord:5}")
    if not proposees:
        out.append("    aucune")

    out += ["", f"  Consommation ({ia.MODELE}, tous passages de {compte}) :"]
    for modele, appels, *jetons in con.execute(
        """SELECT modele, SUM(appels_ia), SUM(jetons_entree), SUM(jetons_sortie), SUM(jetons_cache_ecrits),
                  SUM(jetons_cache_lus)
           FROM passages WHERE compte = ? AND modele LIKE ? AND appels_ia > 0 GROUP BY modele""",
        (compte, f"{ia.MODELE}%"),
    ):
        j = ia.Jetons(*jetons)
        abonnement = modele.endswith("/abonnement")
        out += [f"    {'abonnement Claude (claude -p)' if abonnement else 'API'} : {appels} appels",
                f"      jetons : {j.entree} entrée, {j.sortie} sortie, {j.cache_ecrits} cache écrits, {j.cache_lus} cache lus",
                f"      {'équivalent API' if abonnement else 'coût'} : {j.cout():.4f} $, soit {1000 * j.cout() / appels:.3f} $ "
                f"pour 1 000 mails" + (" (rien de facturé : quota de l'abonnement)" if abonnement else "")]
    return "\n".join(out), _echantillon(con, compte, base, echantillon, graine)


def _echantillon(con, compte: str, base: str, taille: int, graine: int) -> str:
    """Échantillon stratifié par catégorie : au moins un mail par catégorie, le reste au prorata."""
    lignes = [dict(r) for r in con.execute(
        f"""SELECT m.expediteur, m.objet, m.date, m.a_pj, d.categorie_auto, d.categorie_ia, d.source, d.confiance,
                   d.priorite, d.extraction, d.action, d.erreur {base} ORDER BY m.date DESC""", (compte,))]
    par_cat: dict[str, list[dict]] = {}
    for l in lignes:
        par_cat.setdefault(l["categorie_auto"], []).append(l)
    rnd = random.Random(graine)
    choix = []
    for cat, ls in sorted(par_cat.items()):
        n = max(1, round(taille * len(ls) / max(len(lignes), 1)))
        choix += rnd.sample(ls, min(n, len(ls)))
    out = [f"Échantillon de {len(choix)} décisions ({compte})", ""]
    for l in sorted(choix, key=lambda l: (l["categorie_auto"], l["date"] or "")):
        ext = json.loads(l["extraction"] or "{}")
        detail = [f"{l['source']}", f"conf {l['confiance']:.0f}" if l["confiance"] is not None else "conf —"]
        if l["categorie_ia"] and l["categorie_ia"] != l["categorie_auto"]:
            detail.append(f"IA : {l['categorie_ia']}")
        if l["priorite"]:
            detail.append(f"priorité {l['priorite']}")
        for cle in ("boutique", "numero", "etape", "date", "peremption"):
            if ext.get(cle):
                detail.append(f"{cle} {ext[cle]}")
        detail.append(f"→ {l['action']}" + (" (PJ)" if l["a_pj"] else ""))
        out.append(f"[{l['categorie_auto']}] {(l['date'] or '')[:10]}  {parseaddr(l['expediteur'] or '')[1]}")
        out.append(f"    objet : {(l['objet'] or '')[:100]}")
        if ext.get("resume"):
            out.append(f"    résumé : {ext['resume']}")
        if l["erreur"]:
            out.append(f"    erreur : {l['erreur']}")
        out.append(f"    {' · '.join(detail)}")
    return "\n".join(out)
