-- Catégories et rubriques en base (jalon 2) : ajouter une catégorie ou une rubrique = ajouter une ligne.
-- Le code ne fixe que les comportements (ce qu'un élément sait faire) ; l'IA reçoit la liste des
-- catégories actives et leurs descriptions à chaque passage.

-- Regroupements affichés dans le front (onglets des pages Boîte de réception et Notifications).
CREATE TABLE rubriques (
    cle    TEXT PRIMARY KEY,
    nom    TEXT NOT NULL,
    page   TEXT NOT NULL CHECK (page IN ('inbox', 'notifs')),
    icone  TEXT,                                     -- nom Material Symbols
    ordre  INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);
INSERT INTO rubriques (cle, nom, page, icone, ordre) VALUES
    ('reply', 'À répondre', 'inbox', 'reply', 1),
    ('waiting', 'En attente', 'inbox', 'schedule', 2),
    ('orders', 'Commandes', 'notifs', 'package_2', 1),
    ('events', 'Événements', 'notifs', 'event', 2),
    ('alerts', 'Alertes', 'notifs', 'shield', 3),
    ('offers', 'Offres', 'notifs', 'sell', 4),
    ('applications', 'Candidatures', 'notifs', 'work', 5);

CREATE TABLE categories (
    cle          TEXT PRIMARY KEY,
    nom          TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',          -- définition donnée à l'IA
    sort         TEXT NOT NULL CHECK (sort IN ('garder', 'suppression')),  -- pièce jointe : conserves
    comportement TEXT NOT NULL
                 CHECK (comportement IN ('aucun', 'reponse', 'commande', 'evenement', 'alerte', 'offre', 'simple', 'candidature')),
    rubrique     TEXT REFERENCES rubriques (cle),
    flux         TEXT CHECK (flux IN ('Paiements', 'Sécurité', 'Livraisons', 'Promos', 'Autres')),
    choix_ia     INTEGER NOT NULL DEFAULT 1,         -- l'IA peut la choisir
    systeme      INTEGER NOT NULL DEFAULT 0,         -- indispensable au code : ni supprimée ni renommée
    ordre        INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1
);
INSERT INTO categories (cle, nom, sort, comportement, rubrique, flux, choix_ia, systeme, ordre, description) VALUES
    ('a_repondre', 'À répondre', 'garder', 'reponse', 'reply', 'Autres', 1, 1, 1,
     'une personne ou un organisme attend une réponse, une action ou une décision de Toma (question directe, document à fournir, invitation à confirmer, démarche administrative, école, santé, banque qui demande une action).'),
    ('commande', 'Commande', 'suppression', 'commande', 'orders', 'Livraisons', 1, 0, 2,
     'achat en ligne d''un objet (confirmation, préparation, expédition, livraison, retard, retour). Un billet de transport ou une réservation n''est pas une commande : c''est un événement.'),
    ('evenement', 'Événement', 'suppression', 'evenement', 'events', 'Autres', 1, 0, 3,
     'rendez-vous, réservation, billet de train ou d''avion, spectacle, inscription, échéance datée à venir.'),
    ('alerte', 'Alerte', 'suppression', 'alerte', 'alerts', 'Sécurité', 1, 0, 4,
     'sécurité d''un compte (nouvelle connexion, mot de passe changé, appareil ou application ajoutés), fraude, paiement bloqué, incident ou avertissement important à lire.'),
    ('candidature', 'Candidature', 'suppression', 'candidature', 'applications', 'Autres', 1, 0, 5,
     'recherche de stage ou d''emploi : candidature de Toma (accusé de réception, suivi, invitation à un entretien ou à un test, refus, proposition) et offres de stage ou d''emploi suggérées par un site ou un réseau (Indeed, Glassdoor, LinkedIn, Welcome to the Jungle, JobTeaser…).'),
    ('offre', 'Offre', 'suppression', 'offre', 'offers', 'Promos', 1, 0, 6,
     'promotion, publicité, newsletter, contenu de veille.'),
    ('social', 'Réseau social', 'suppression', 'offre', 'offers', 'Autres', 1, 0, 7,
     'notification d''un réseau social (LinkedIn, Instagram, X, Facebook, TikTok…), sauf les offres de stage ou d''emploi, qui sont des candidatures.'),
    ('recu_paiement', 'Reçu ou paiement', 'suppression', 'aucun', NULL, 'Paiements', 1, 0, 8,
     'reçu, facture, confirmation de paiement ou de renouvellement d''abonnement.'),
    ('mouvement_bancaire', 'Mouvement bancaire', 'suppression', 'aucun', NULL, 'Paiements', 1, 0, 9,
     'virement, prélèvement, opération ou relevé bancaire.'),
    ('code_unique', 'Code à usage unique', 'suppression', 'aucun', NULL, NULL, 1, 0, 10,
     'code de vérification, mot de passe à usage unique, lien de connexion.'),
    ('cgu', 'Conditions d''utilisation', 'suppression', 'aucun', NULL, NULL, 1, 0, 11,
     'changement de conditions d''utilisation ou de politique de confidentialité.'),
    ('bienvenue', 'Bienvenue', 'suppression', 'aucun', NULL, NULL, 1, 0, 12,
     'bienvenue, création ou activation de compte, prise en main.'),
    ('suivi_obsolete', 'Suivi obsolète', 'suppression', 'aucun', NULL, NULL, 1, 0, 13,
     'mise à jour de suivi déjà dépassée (colis livré, ticket clos, statut final sans autre information utile).'),
    ('autre_supprime', 'Autre (supprimé)', 'suppression', 'aucun', NULL, NULL, 1, 0, 14,
     'notification automatique sans valeur durable ni action attendue (rappel d''application, résumé d''activité, demande d''avis, enquête de satisfaction).'),
    ('a_trier', 'À trier', 'garder', 'aucun', NULL, NULL, 0, 1, 90,
     'confiance de l''IA sous le seuil, ou règle inconnue : Toma décide dans Debug.'),
    ('liste_blanche', 'Liste blanche', 'garder', 'aucun', NULL, NULL, 0, 1, 91,
     'marque des règles de la liste blanche : le mail n''est jamais supprimé par une règle et passe toujours par l''IA.');

-- Mots-clés des promotions cherchés dans l'objet seulement : dans le corps, « promo » ou « réduction »
-- attrapaient des reçus et des certifications (rapport de tri du 2026-11-17).
UPDATE regles SET type = 'mot_cle_objet' WHERE groupe = 'promo_pending' AND type = 'mot_cle' AND origine = 'migree';
