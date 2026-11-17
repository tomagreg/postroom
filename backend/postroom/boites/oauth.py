"""OAuth2 Microsoft (flux « device code ») pour l'IMAP de Microsoft 365.

Les jetons sont mis en cache dans data/jetons-<compte>.json (droits 600, jamais affichés).
"""

import os

import msal

from postroom.config import DOSSIER_DONNEES, Compte

PORTEES = ["https://outlook.office.com/IMAP.AccessAsUser.All"]


class ErreurOAuth(Exception):
    pass


def _fichier_cache(compte: Compte):
    return DOSSIER_DONNEES / f"jetons-{compte.cle}.json"


def _application(compte: Compte):
    cache = msal.SerializableTokenCache()
    f = _fichier_cache(compte)
    if f.exists():
        cache.deserialize(f.read_text())
    app = msal.PublicClientApplication(
        compte.client_id,
        authority=f"https://login.microsoftonline.com/{compte.tenant or 'organizations'}",
        token_cache=cache,
    )
    return app, cache


def _enregistrer(compte: Compte, cache) -> None:
    if cache.has_state_changed:
        f = _fichier_cache(compte)
        f.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as sortie:
            sortie.write(cache.serialize())


def jeton(compte: Compte) -> str:
    """Jeton d'accès valide, renouvelé en silence grâce au cache."""
    app, cache = _application(compte)
    comptes = app.get_accounts(username=compte.utilisateur)
    resultat = app.acquire_token_silent(PORTEES, account=comptes[0]) if comptes else None
    if not resultat or "access_token" not in resultat:
        raise ErreurOAuth(f"aucun jeton : lancer python -m postroom connecter-oauth --compte {compte.cle}")
    _enregistrer(compte, cache)
    return resultat["access_token"]


def connecter(compte: Compte, afficher=print) -> None:
    """Connexion interactive : affiche l'adresse et le code à saisir, attend la validation."""
    app, cache = _application(compte)
    flux = app.initiate_device_flow(scopes=PORTEES)
    if "user_code" not in flux:
        raise ErreurOAuth(flux.get("error_description") or str(flux))
    afficher(flux["message"])
    resultat = app.acquire_token_by_device_flow(flux)
    if "access_token" not in resultat:
        raise ErreurOAuth(f"{resultat.get('error')} : {resultat.get('error_description')}")
    _enregistrer(compte, cache)
