-- Tri v2 (jalon 2) : ce que l'IA a extrait, proposition brute de l'IA, règles proposées
-- qui auraient attrapé le mail, erreur d'appel ; jetons détaillés par passage.

ALTER TABLE decisions ADD COLUMN categorie_ia TEXT;          -- proposition de l'IA, même sous le seuil
ALTER TABLE decisions ADD COLUMN extraction TEXT;            -- JSON : résumé, lien, péremption, commande, événement
ALTER TABLE decisions ADD COLUMN regles_proposees TEXT;      -- JSON : ids des règles proposées qui correspondent
ALTER TABLE decisions ADD COLUMN passage_id INTEGER REFERENCES passages (id);
ALTER TABLE decisions ADD COLUMN erreur TEXT;                -- échec de l'IA : le mail sera retrié

ALTER TABLE passages ADD COLUMN modele TEXT;
ALTER TABLE passages ADD COLUMN mails_tries INTEGER NOT NULL DEFAULT 0;
ALTER TABLE passages ADD COLUMN jetons_cache_ecrits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE passages ADD COLUMN jetons_cache_lus INTEGER NOT NULL DEFAULT 0;
