"""Expirations : un élément expiré reste affiché « Expiré » un jour (expire_visible_jours), puis il
est retiré. Lignes du Flux purgées après 90 jours. Corps d'un mail effacé quand tous ses éléments
sont retirés, seulement si le réglage effacer_corps vaut 1 (jalon 9)."""

from datetime import datetime, timedelta

from postroom.elements.commun import iso, instant, retirer


def appliquer(con, compte: str, reglages: dict, quand: datetime) -> dict[str, int]:
    r = {"expires_affiches": 0, "retires": 0, "flux_purges": 0, "corps_effaces": 0}
    grace = timedelta(days=reglages["expire_visible_jours"])
    for l in con.execute(
        """SELECT id, expire_le FROM elements
           WHERE compte = ? AND expire_le IS NOT NULL AND retire_le IS NULL AND supprime_le IS NULL""", (compte,)
    ).fetchall():
        fin = instant(l["expire_le"])
        if fin is None or quand < fin:
            continue
        if quand >= fin + grace:
            retirer(con, l["id"], "expire", quand)
            r["retires"] += 1
        else:
            r["expires_affiches"] += 1

    limite = iso(quand - timedelta(days=reglages["flux_jours"]))
    r["flux_purges"] = con.execute("DELETE FROM flux WHERE compte = ? AND heure < ?", (compte, limite)).rowcount

    if reglages["effacer_corps"]:
        r["corps_effaces"] = con.execute(
            """UPDATE mails SET corps_texte = NULL
               WHERE compte = ? AND corps_texte IS NOT NULL
                 AND id IN (SELECT mail_id FROM element_mails)
                 AND id NOT IN (SELECT em.mail_id FROM element_mails em JOIN elements e ON e.id = em.element_id
                                WHERE e.retire_le IS NULL AND e.supprime_le IS NULL)""", (compte,)).rowcount
    return r
