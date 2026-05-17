# postroom

Agent de tri automatique multi-boîtes. Classe, nettoie et résume les emails chaque nuit.

**Stack** : Python 3.11+ · imaplib · sqlite3 · anthropic SDK · Flask · PyYAML · python-dotenv

---

## Installation

```bash
pip install -r requirements.txt
cp config/accounts.env.example config/accounts.env
# remplir accounts.env avec les credentials
```

---

## Configuration

### `config/accounts.env`

```env
COURS_HOST=outlook.office365.com
COURS_USER=prenom.nom@efrei.fr
COURS_PASS=motdepasse

VOILE_HOST=imap.example.com
VOILE_USER=voile@example.com
VOILE_PASS=motdepasse

PERSO1_HOST=imap.gmail.com
PERSO1_USER=adresse@gmail.com
PERSO1_PASS=app_password_16_chars

PERSO2_HOST=imap.gmail.com
PERSO2_USER=adresse2@gmail.com
PERSO2_PASS=app_password_16_chars

ANTHROPIC_API_KEY=sk-ant-...
```

> Gmail : utiliser un **App Password** (compte Google → Sécurité → Mots de passe d'application).

### `config/whitelist.yaml`

Domaines et expéditeurs toujours conservés (score 3, court-circuit couche 1).

### `config/rules.yaml`

Règles déterministes (couche 2) : mots-clés, expéditeurs, actions, délais de suppression.  
`confidence_threshold` : seuil en dessous duquel haiku escalade vers sonnet (défaut 0.75).

---

## Commandes

### Pipeline principal

```bash
# Fetch + classification (obligatoire --dry-run, ne modifie pas les serveurs mail)
python src/main.py --dry-run

# Limiter le nombre de mails fetchés par boîte (défaut 50)
python src/main.py --dry-run --limit 20
```

### Purge IMAP

```bash
# Voir ce qui serait supprimé sans toucher les serveurs
python src/purge.py --dry-run

# Appliquer les suppressions (flag \Deleted, sans EXPUNGE immédiat)
python src/purge.py
```

### Dashboard

```bash
# Mode lecture seule (défaut port 5002)
python src/dashboard.py

# Port personnalisé
python src/dashboard.py --port 8080

# Base de données alternative
python src/dashboard.py --db /chemin/vers/postroom.db

# Mode calibration — active les boutons de revue humaine dans /log
python src/dashboard.py --calibrate
```

**Routes disponibles :**

| Route | Description |
|-------|-------------|
| `/log` | Journal de toutes les décisions (action, score, règle, raison) |
| `/inbox` | Emails score=4 nécessitant une action (reply_queue) |
| `/promos` | Promotions en attente de décision (expiration J+14) |
| `/social` | Notifications sociales en attente (expiration J+7) |
| `/stats` | Répartition des actions, top règles, totaux |
| `/calibration` | Désaccords pipeline/humain *(mode --calibrate uniquement)* |

### Tests

```bash
python -m pytest
python -m pytest tests/test_rule_engine.py -v   # règles uniquement
python -m pytest tests/test_purge.py -v         # purge uniquement
```

---

## Pipeline de décision

```
Email
  │
  ├─ Couche 1 — Whitelist (domaine / expéditeur / mot-clé objet)
  │     → keep (score 3)
  │
  ├─ Couche 2 — Règles déterministes (rules.yaml)
  │     → delete (score 1)
  │     → promo_queue (J+14)
  │     → social_queue (J+7)
  │
  └─ Couche 3 — LLM haiku (dual-pass)
        Passe 1 : classification rapide
        Passe 2 : disambiguation si score=2 ou confidence < 0.75
        → keep (score 3-4) / delete (score 1) / review (score 2)
```

**Scores LLM :** 1=supprimer · 2=ambigu · 3=conserver · 4=action requise

---

## Structure

```
src/
  main.py          orchestrateur (fetch + classify + store)
  rule_engine.py   couches 1 et 2
  llm_agent.py     couche 3 (haiku dual-pass)
  db.py            SQLite helpers + schéma 7 tables
  imap_client.py   connexion IMAP multi-comptes
  dashboard.py     Flask dashboard (lecture seule + calibration)
  purge.py         flag \Deleted sur IMAP après délai
config/
  accounts.env          credentials (non versionné)
  accounts.env.example  template
  rules.yaml            règles déterministes
  whitelist.yaml        domaines/expéditeurs protégés
tests/              pytest, données synthétiques uniquement
logs/               .gitkeep
attachments/        .gitkeep
postroom.db         base SQLite (non versionnée)
```

---

## Cron (Raspberry Pi)

```cron
0 2 * * *  cd /home/pi/postroom && python src/main.py --dry-run >> logs/triage.log 2>&1
0 3 * * *  cd /home/pi/postroom && python src/purge.py >> logs/purge.log 2>&1
```
