# postroom — conventions Claude Code

## Stack
- Python 3.11+ · imaplib stdlib · sqlite3 stdlib · Flask · anthropic SDK · python-dotenv · PyYAML

## Règles absolues
- `imaplib` natif uniquement — pas de bibliothèque IMAP tierce
- `sqlite3` stdlib uniquement — pas de SQLAlchemy ni ORM
- `logging` stdlib uniquement — jamais `print()`
- Mode `--dry-run` obligatoire sur tout script qui touche les mails IMAP
- Suppressions IMAP : flaguer `\Deleted` sans `EXPUNGE` immédiat (purge job séparé 48h)
- Dashboard Flask en lecture seule sur SQLite (pas d'écriture mail depuis Flask)
- Jamais committer `config/accounts.env`, `*.db`, `attachments/`, `logs/*.log`
- Jamais d'adresse mail réelle dans les tests — `user@example.com` uniquement

## Scores LLM
- 1 = supprimer · 2 = ambigu (rapport matinal) · 3 = conserver · 4 = action requise (/inbox)
- haiku-4-5 pour les scores clairs · sonnet-4-6 pour les ambigus (confiance < 0.75)

## Structure
```
src/           scripts Python autonomes (pas de blueprints Flask ici)
config/        YAML + accounts.env (non versionné)
logs/          .gitkeep uniquement
attachments/   .gitkeep uniquement
tests/         pytest, données synthétiques uniquement
```

## Type hints
Obligatoires sur toutes les fonctions publiques.
