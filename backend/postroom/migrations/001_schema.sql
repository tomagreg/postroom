-- Schéma initial de Postroom v2 (plan v2, § 4), avec ce que demande la lecture des boîtes (jalon 1).
-- Dates en ISO 8601 UTC ; listes en JSON.

CREATE TABLE comptes (
    cle               TEXT PRIMARY KEY,
    nom               TEXT NOT NULL,
    fournisseur       TEXT NOT NULL,
    adresse           TEXT,
    via               TEXT,                         -- boîte qui reçoit les mails transférés (cours -> perso1)
    dossier           TEXT NOT NULL DEFAULT 'INBOX', -- dossier principal : INBOX ou libellé
    mode              TEXT NOT NULL DEFAULT 'inactif' CHECK (mode IN ('inactif', 'simulation', 'reel')),
    ajoutee_le        TEXT,
    simulation_depuis TEXT,
    derniere_synchro  TEXT,
    erreur            TEXT
);

CREATE TABLE mails (
    id           INTEGER PRIMARY KEY,
    compte       TEXT NOT NULL REFERENCES comptes (cle),
    dossier      TEXT NOT NULL CHECK (dossier IN ('principal', 'envoyes')),
    gmail_msgid  INTEGER NOT NULL,
    thread_id    TEXT,                               -- X-GM-THRID
    uid          INTEGER,
    uidvalidity  INTEGER,
    message_id   TEXT,
    in_reply_to  TEXT,
    refs         TEXT,                               -- en-tête References
    expediteur   TEXT,
    destinataires TEXT,                              -- JSON : adresses To et Cc
    objet        TEXT,
    date         TEXT,
    taille       INTEGER,
    libelles     TEXT,                               -- JSON : X-GM-LABELS
    corps_texte  TEXT,                               -- effacé avec l'élément
    liens        TEXT,                               -- JSON : [{url, domaine}]
    a_pj         INTEGER NOT NULL DEFAULT 0,
    emplacement  TEXT NOT NULL DEFAULT 'inbox'
                 CHECK (emplacement IN ('inbox', 'envoyes', 'conserves', 'suppression', 'corbeille', 'ailleurs')),
    vu_le        TEXT NOT NULL,
    UNIQUE (compte, gmail_msgid)
);
CREATE INDEX mails_compte_date ON mails (compte, date);
CREATE INDEX mails_message_id ON mails (message_id);
CREATE INDEX mails_thread ON mails (compte, thread_id);

CREATE TABLE pieces_jointes (
    id      INTEGER PRIMARY KEY,
    mail_id INTEGER NOT NULL REFERENCES mails (id) ON DELETE CASCADE,
    nom     TEXT,
    taille  INTEGER,
    type    TEXT
);
CREATE INDEX pieces_jointes_mail ON pieces_jointes (mail_id);

CREATE TABLE regles (
    id      INTEGER PRIMARY KEY,
    type    TEXT NOT NULL CHECK (type IN ('domaine', 'expediteur', 'mot_cle', 'mot_cle_objet', 'exception_domaine')),
    valeur  TEXT NOT NULL,
    categorie TEXT NOT NULL,
    groupe  TEXT NOT NULL DEFAULT '',                -- règle v1 d'origine, ou règle manuelle
    origine TEXT NOT NULL CHECK (origine IN ('migree', 'manuelle', 'proposee')),
    etat    TEXT NOT NULL CHECK (etat IN ('active', 'proposee', 'refusee')),
    cree_le TEXT NOT NULL,
    UNIQUE (type, valeur, categorie, groupe)
);

CREATE TABLE decisions (
    id             INTEGER PRIMARY KEY,
    mail_id        INTEGER NOT NULL UNIQUE REFERENCES mails (id) ON DELETE CASCADE,
    categorie_auto TEXT NOT NULL,
    categorie      TEXT,                             -- corrigée par Toma
    source         TEXT NOT NULL CHECK (source IN ('regle', 'ia')),
    regle_id       INTEGER REFERENCES regles (id),
    confiance      REAL,
    priorite       TEXT,
    action         TEXT,
    decide_le      TEXT NOT NULL,
    corrige_le     TEXT
);

CREATE TABLE elements (
    id         INTEGER PRIMARY KEY,
    type       TEXT NOT NULL CHECK (type IN ('reponse', 'attente', 'commande', 'evenement', 'alerte', 'offre')),
    sous_type  TEXT,
    compte     TEXT NOT NULL REFERENCES comptes (cle),
    titre      TEXT,
    sous_titre TEXT,
    resume     TEXT,
    lien       TEXT,
    domaine    TEXT,
    priorite   TEXT,
    relance    INTEGER NOT NULL DEFAULT 0,
    echeance   TEXT,
    expire_le  TEXT,
    lu         INTEGER NOT NULL DEFAULT 0,
    supprime_le TEXT,
    mail_id    INTEGER REFERENCES mails (id),
    cree_le    TEXT NOT NULL
);
CREATE INDEX elements_type ON elements (type, compte);

CREATE TABLE commandes (
    element_id INTEGER PRIMARY KEY REFERENCES elements (id) ON DELETE CASCADE,
    boutique   TEXT,
    numero     TEXT,
    etape      INTEGER CHECK (etape BETWEEN 1 AND 4),
    dates      TEXT,                                 -- JSON
    alerte     TEXT
);

CREATE TABLE envoyes (
    id           INTEGER PRIMARY KEY,
    mail_id      INTEGER NOT NULL UNIQUE REFERENCES mails (id),
    destinataire TEXT,
    envoye_le    TEXT,
    cycle_depuis TEXT,
    repondu_le   TEXT,
    archive_le   TEXT
);

CREATE TABLE flux (
    id        INTEGER PRIMARY KEY,
    mail_id   INTEGER REFERENCES mails (id),
    compte    TEXT NOT NULL REFERENCES comptes (cle),
    heure     TEXT NOT NULL,
    type      TEXT NOT NULL CHECK (type IN ('Paiements', 'Sécurité', 'Livraisons', 'Promos', 'Autres')),
    icone     TEXT,
    texte     TEXT,
    lu        INTEGER NOT NULL DEFAULT 0,
    masque_le TEXT
);
CREATE INDEX flux_heure ON flux (heure);

CREATE TABLE actions (
    id         INTEGER PRIMARY KEY,
    mail_id    INTEGER NOT NULL REFERENCES mails (id),
    type       TEXT NOT NULL CHECK (type IN ('etiqueter', 'deplacer', 'corbeille', 'restaurer')),
    cible      TEXT,
    cree_le    TEXT NOT NULL,
    execute_le TEXT,
    annule     INTEGER NOT NULL DEFAULT 0,
    simulee    INTEGER NOT NULL DEFAULT 0,
    erreur     TEXT
);

CREATE TABLE reglages (
    cle    TEXT PRIMARY KEY,
    valeur TEXT NOT NULL
);
INSERT INTO reglages (cle, valeur) VALUES
    ('attente_jours', '5'),            -- « Avant Sans réponse »
    ('relance_jours', '5'),            -- délai X après « Relancer »
    ('offre_jours', '14'),
    ('social_jours', '7'),
    ('purge_jours', '30'),
    ('alerte_jours', '7'),
    ('commande_livree_jours', '7'),
    ('flux_jours', '90'),
    ('seuil_confiance', '70'),
    ('libelle_suppression', 'Suppression'),
    ('libelle_conserves', 'Conservés');

CREATE TABLE passages (
    id            INTEGER PRIMARY KEY,
    debut         TEXT NOT NULL,
    fin           TEXT,
    compte        TEXT,
    mails_lus     INTEGER NOT NULL DEFAULT 0,
    appels_ia     INTEGER NOT NULL DEFAULT 0,
    jetons_entree INTEGER NOT NULL DEFAULT 0,
    jetons_sortie INTEGER NOT NULL DEFAULT 0,
    erreur        TEXT
);

-- état de lecture de chaque dossier IMAP (reprise d'un scan, changement d'UIDVALIDITY)
CREATE TABLE synchro (
    compte       TEXT NOT NULL REFERENCES comptes (cle),
    dossier_imap TEXT NOT NULL,
    uidvalidity  INTEGER,
    dernier_uid  INTEGER,
    mis_a_jour_le TEXT NOT NULL,
    PRIMARY KEY (compte, dossier_imap)
);
