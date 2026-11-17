-- Revue du tri du 2026-11-17 : un mail écrit par une personne qui attend une réponse est À répondre,
-- même quand il parle d'une candidature (sinon il partirait en Suppression avec les accusés de réception).
UPDATE categories SET description = description || ' Cela vaut aussi pour un mail écrit par une personne au sujet d''un stage ou d''une candidature, si elle attend une réponse de Toma.'
WHERE cle = 'a_repondre';
UPDATE categories SET description = description || ' Seulement les messages sans réponse attendue de Toma ; une personne qui lui écrit et attend une réponse relève de a_repondre.'
WHERE cle = 'candidature';
