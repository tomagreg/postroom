-- Éléments (jalon 3) : types candidature et simple, regroupement (cle), retrait par expiration ou
-- par la boîte (retire_le, retire_motif), plusieurs mails par élément, suivi des candidatures,
-- durée de vie par catégorie, décisions déjà transformées en éléments.

-- SQLite ne modifie pas un CHECK : la table est recréée (vide jusqu'ici).
CREATE TABLE elements_v5 (
    id           INTEGER PRIMARY KEY,
    type         TEXT NOT NULL
                 CHECK (type IN ('reponse', 'attente', 'commande', 'evenement', 'alerte', 'offre', 'candidature', 'simple')),
    sous_type    TEXT,
    categorie    TEXT,                               -- catégorie du mail d'origine (rubrique du front)
    cle          TEXT NOT NULL,                      -- regroupement : fil, commande, candidature ou mail
    compte       TEXT NOT NULL REFERENCES comptes (cle),
    titre        TEXT,
    sous_titre   TEXT,
    resume       TEXT,
    lien         TEXT,
    domaine      TEXT,
    priorite     TEXT,
    relance      INTEGER NOT NULL DEFAULT 0,
    echeance     TEXT,                               -- date de l'événement, ou fin du délai « Sans réponse »
    expire_le    TEXT,
    lu           INTEGER NOT NULL DEFAULT 0,
    supprime_le  TEXT,                               -- ✕ de Toma
    retire_le    TEXT,                               -- retiré par Postroom
    retire_motif TEXT CHECK (retire_motif IN ('expire', 'repondu', 'ailleurs', 'archive')),
    mail_id      INTEGER REFERENCES mails (id),      -- mail principal (le plus récent)
    date         TEXT,                               -- date de ce mail
    cree_le      TEXT NOT NULL,
    maj_le       TEXT
);
INSERT INTO elements_v5 (id, type, sous_type, cle, compte, titre, sous_titre, resume, lien, domaine, priorite,
                         relance, echeance, expire_le, lu, supprime_le, mail_id, cree_le)
    SELECT id, type, sous_type, 'mail:' || COALESCE(mail_id, id), compte, titre, sous_titre, resume, lien, domaine,
           priorite, relance, echeance, expire_le, lu, supprime_le, mail_id, cree_le
    FROM elements;
DROP TABLE elements;
ALTER TABLE elements_v5 RENAME TO elements;
CREATE INDEX elements_type ON elements (type, compte);
CREATE UNIQUE INDEX elements_cle ON elements (compte, type, cle);

CREATE TABLE element_mails (
    element_id INTEGER NOT NULL REFERENCES elements (id) ON DELETE CASCADE,
    mail_id    INTEGER NOT NULL REFERENCES mails (id),
    PRIMARY KEY (element_id, mail_id)
);
CREATE INDEX element_mails_mail ON element_mails (mail_id);

CREATE TABLE candidatures (
    element_id INTEGER PRIMARY KEY REFERENCES elements (id) ON DELETE CASCADE,
    entreprise TEXT,
    poste      TEXT,
    reference  TEXT,
    statut     TEXT,
    historique TEXT                                  -- JSON : [{statut, date, mail_id}]
);

CREATE UNIQUE INDEX flux_mail ON flux (mail_id);

-- vide : réglage « <cle>_jours » s'il existe (social_jours), sinon le délai du comportement
ALTER TABLE categories ADD COLUMN duree_jours INTEGER;
ALTER TABLE decisions ADD COLUMN traite_le TEXT;

INSERT OR IGNORE INTO reglages (cle, valeur) VALUES
    ('suggestion_jours', '14'),          -- suggestion de stage
    ('candidature_close_jours', '14'),   -- candidature refusée
    ('simple_jours', '7'),
    ('evenement_sans_date_jours', '7'),
    ('expire_visible_jours', '1'),       -- un élément expiré reste affiché « Expiré » avant d'être retiré
    ('effacer_corps', '0');              -- 1 au jalon 9 : corps effacé quand ses éléments sont retirés
