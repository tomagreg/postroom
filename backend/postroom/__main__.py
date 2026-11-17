"""Ligne de commande : python -m postroom <commande>."""

import argparse
import sys

from postroom.config import COMPTES, charger_comptes


def cmd_verifier_boites(args) -> int:
    from postroom.verification import verifier

    comptes = charger_comptes()
    cles = [args.compte] if args.compte else list(COMPTES)
    echec = False
    for cle in cles:
        c = comptes[cle]
        r = verifier(c)
        via = f", via {c.via}" if c.via else ""
        print(f"{cle} ({c.nom}, {c.fournisseur}{via}) : {r.statut}" + (f" — {r.detail}" if r.detail else ""))
        if r.statut == "ok":
            print(f"    {c.dossier} : {r.messages} messages, UIDVALIDITY {r.uidvalidity}")
            for usage, nom in sorted(r.speciaux.items()):
                print(f"    {usage:8} {nom}")
            print(f"    capacités : {' '.join(r.capacites) or '—'}")
        echec |= r.statut == "erreur"
    return 1 if echec else 0


def cmd_connecter_oauth(args) -> int:
    from postroom.boites.oauth import ErreurOAuth, connecter

    compte = charger_comptes()[args.compte]
    if compte.auth != "oauth2" or not compte.client_id:
        print(f"{args.compte} : AUTH=oauth2 et CLIENT_ID absents de accounts.env")
        return 1
    try:
        connecter(compte)
    except ErreurOAuth as e:
        print(f"échec : {e}")
        return 1
    print("jeton enregistré")
    return 0


def cmd_migrer(args) -> int:
    from postroom import config, db

    con = db.connecter()
    appliquees = db.migrer(con)
    print(f"base {config.fichier_base()} : schéma v{db.version(con)}"
          + (f" (migrations appliquées : {', '.join(map(str, appliquees))})" if appliquees else " (à jour)"))
    return 0


def cmd_importer_regles(args) -> int:
    from postroom import db
    from postroom.tri.regles import importer

    con = db.connecter()
    db.migrer(con)
    r = importer(con)
    print(f"règles : {r.ajoutees} ajoutées, {r.deja_presentes} déjà présentes")
    for nom in r.ignorees:
        print(f"    ignorée : {nom}")
    for etat, n in con.execute("SELECT etat, COUNT(*) FROM regles GROUP BY etat ORDER BY etat"):
        print(f"    {etat} : {n}")
    return 0


def cmd_scan(args) -> int:
    import time

    from postroom import db
    from postroom.boites.gmail import BoiteGmail
    from postroom.config import libelles_exclus
    from postroom.ingestion import scanner

    comptes = charger_comptes()
    compte = comptes[args.compte]
    if not compte.configure:
        print(f"{args.compte} : identifiants absents de accounts.env")
        return 1
    con = db.connecter()
    db.migrer(con)
    exclus = libelles_exclus(compte.cle, comptes)
    debut = time.monotonic()
    boite = BoiteGmail(compte)
    try:
        rapports = scanner(con, compte, boite, args.jours, exclus=exclus, lot=args.lot, limite=args.limite)
    finally:
        boite.fermer()
    print(f"{compte.cle} ({compte.nom}), {args.jours} jours, {time.monotonic() - debut:.0f} s :")
    for r in rapports:
        print(f"    {r.dossier} ({r.nom_imap}) : {r.trouves} trouvés, {r.ajoutes} ajoutés, "
              f"{r.deja_en_base} déjà en base, {r.exclus} exclus, {r.erreurs} erreurs")
    if exclus:
        print(f"    libellés exclus : {', '.join(sorted(exclus))}")
    return 1 if any(r.erreurs for r in rapports) else 0


def cmd_etat(args) -> int:
    from postroom import config, db

    chemin = config.fichier_base()
    con = db.connecter()
    db.migrer(con)
    taille = chemin.stat().st_size / 1e6 if chemin.exists() else 0
    print(f"base {chemin} : schéma v{db.version(con)}, {taille:.1f} Mo")
    lignes = con.execute(
        """SELECT m.compte, m.dossier, COUNT(*) AS n, SUM(m.corps_texte <> '') AS corps,
                  SUM(m.a_pj) AS pj, SUM(LENGTH(m.corps_texte)) AS octets, MIN(m.date) AS du, MAX(m.date) AS au
           FROM mails m GROUP BY m.compte, m.dossier ORDER BY m.compte, m.dossier"""
    ).fetchall()
    if not lignes:
        print("    aucun mail")
    for l in lignes:
        print(f"    {l['compte']:7} {l['dossier']:9} {l['n']:5} mails, {l['corps']} avec corps, {l['pj']} avec PJ, "
              f"{(l['octets'] or 0) / 1e6:.1f} Mo de texte, du {(l['du'] or '?')[:10]} au {(l['au'] or '?')[:10]}")
    n_pj = con.execute("SELECT COUNT(*) FROM pieces_jointes").fetchone()[0]
    n_regles = con.execute("SELECT COUNT(*) FROM regles").fetchone()[0]
    print(f"    pièces jointes : {n_pj} ; règles : {n_regles}")
    for c in con.execute("SELECT cle, derniere_synchro FROM comptes ORDER BY cle"):
        print(f"    dernière lecture de {c['cle']} : {c['derniere_synchro'] or 'jamais'}")
    return 0


def cmd_trier(args) -> int:
    import time

    from postroom import db
    from postroom.config import cle_anthropic, jeton_abonnement, moteur_ia
    from postroom.tri import ia
    from postroom.tri.decision import trier

    if moteur_ia() == "api" and not cle_anthropic():
        print("IA_MOTEUR=api mais ANTHROPIC_API_KEY absente de accounts.env")
        return 1
    if moteur_ia() == "abonnement" and not jeton_abonnement():
        print("CLAUDE_CODE_OAUTH_TOKEN (jeton de l'abonnement, comme pour RogerLeJardinier) absent de accounts.env")
        return 1
    con = db.connecter()
    db.migrer(con)
    debut = time.monotonic()
    r = trier(con, args.compte, refaire=args.refaire, limite=args.limite, categorie=args.categorie)
    j = r.jetons
    print(f"{args.compte} : {r.a_trier} mails à trier, {r.tries} triés en {time.monotonic() - debut:.0f} s "
          f"(passage {r.passage_id})")
    print(f"    par règle {r.par_regle}, par l'IA {r.par_ia} (dont {r.sous_seuil} sous le seuil), erreurs {r.erreurs}")
    for erreur, n in r.detail_erreurs.items():
        print(f"    erreur : {erreur} ×{n}")
    if r.limite:
        print(f"    limite de l'abonnement atteinte, {r.non_tries} mails laissés pour le passage suivant : {r.limite}")
    print(f"    jetons : {j.entree} entrée, {j.sortie} sortie, {j.cache_ecrits} cache écrits, {j.cache_lus} cache lus"
          f" ; {'équivalent API' if r.moteur == 'abonnement' else 'coût'} {j.cout():.4f} $ ({ia.MODELE}, {r.moteur})")
    return 1 if r.erreurs or r.limite else 0


def cmd_rapport_tri(args) -> int:
    from datetime import datetime

    from postroom import config, db
    from postroom.tri.rapport import rapport

    con = db.connecter()
    db.migrer(con)
    resume, echantillon = rapport(con, args.compte, args.echantillon)
    print(resume)
    print()
    print(echantillon)
    dossier = config.DOSSIER_DONNEES / "rapports"
    dossier.mkdir(parents=True, exist_ok=True)
    fichier = dossier / f"tri-{args.compte}-{datetime.now(config.FUSEAU):%Y-%m-%d-%H%M}.txt"
    fichier.write_text(f"{resume}\n\n{echantillon}\n", encoding="utf-8")
    print(f"\nrapport écrit dans {fichier}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="postroom")
    sous = parser.add_subparsers(dest="commande", required=True)
    p = sous.add_parser("verifier-boites", help="teste la connexion des boîtes en lecture seule")
    p.add_argument("--compte", choices=list(COMPTES))
    p.set_defaults(func=cmd_verifier_boites)
    p = sous.add_parser("connecter-oauth", help="connexion OAuth2 Microsoft (device code)")
    p.add_argument("--compte", choices=list(COMPTES), required=True)
    p.set_defaults(func=cmd_connecter_oauth)
    p = sous.add_parser("migrer", help="crée ou met à jour le schéma de la base")
    p.set_defaults(func=cmd_migrer)
    p = sous.add_parser("importer-regles", help="reprend rules.yaml et whitelist.yaml dans la base")
    p.set_defaults(func=cmd_importer_regles)
    p = sous.add_parser("scan", help="lit une boîte en lecture seule et enregistre les mails")
    p.add_argument("--compte", choices=list(COMPTES), required=True)
    p.add_argument("--jours", type=int, default=30)
    p.add_argument("--lot", type=int, default=50)
    p.add_argument("--limite", type=int, help="au plus N mails par dossier (les plus récents)")
    p.set_defaults(func=cmd_scan)
    p = sous.add_parser("etat", help="résumé de la base")
    p.set_defaults(func=cmd_etat)
    p = sous.add_parser("trier", help="trie les mails reçus (règles, puis IA) ; rien ne touche la boîte")
    p.add_argument("--compte", choices=list(COMPTES), required=True)
    p.add_argument("--limite", type=int, help="au plus N mails (les plus récents)")
    p.add_argument("--refaire", action="store_true", help="retrie aussi les mails déjà triés (sauf corrigés)")
    p.add_argument("--categorie", help="retrie seulement les mails de cette catégorie (retenue ou proposée par l'IA)")
    p.set_defaults(func=cmd_trier)
    p = sous.add_parser("rapport-tri", help="répartition, coût et échantillon à relire")
    p.add_argument("--compte", choices=list(COMPTES), required=True)
    p.add_argument("--echantillon", type=int, default=40)
    p.set_defaults(func=cmd_rapport_tri)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
