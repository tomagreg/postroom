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
        print(f"{cle} ({c.nom}, {c.fournisseur}) : {r.statut}" + (f" — {r.detail}" if r.detail else ""))
        if r.statut == "ok":
            print(f"    INBOX : {r.messages} messages, UIDVALIDITY {r.uidvalidity}")
            for usage, nom in sorted(r.speciaux.items()):
                print(f"    {usage:8} {nom}")
            print(f"    capacités : {' '.join(r.capacites) or '—'}")
        echec |= r.statut == "erreur"
    return 1 if echec else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="postroom")
    sous = parser.add_subparsers(dest="commande", required=True)
    p = sous.add_parser("verifier-boites", help="teste la connexion des boîtes en lecture seule")
    p.add_argument("--compte", choices=list(COMPTES))
    p.set_defaults(func=cmd_verifier_boites)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
