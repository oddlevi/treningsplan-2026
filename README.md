# Treningsplan

Personlig, datadrevet treningsplan-system for løping. Henter data fra Garmin Connect og Strava, analyserer form-trender, og genererer periodiserte planer.

## Mål

- 10k: 36:39 → sub 35:00 (~12 uker)
- Halvmaraton: 1:24:00 → sub 1:20:00 (~12 uker etter 10k-race)

## Oppstart

```bash
# 1. Virtuelt miljø
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Sett opp API-tilgang
cp .env.example .env
# Fyll inn STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, GARMIN_EMAIL, GARMIN_PASSWORD

# 3. Første gangs sync (henter ALL historikk – kan ta 30-60 min)
python scripts/fetch_historikk.py

# 4. "Lær meg å kjenne"-rapport (kjøres én gang)
python analyse/historikk.py

# 5. Baseline-analyse (status nå)
python analyse/baseline.py

# 6. Generer første plan
python plan/generator.py --fase 1 --uke 1
```

## Bruk i Claude Code

Åpne prosjektet i Claude Code (`claude` i terminal fra denne mappa). `CLAUDE.md` blir lastet automatisk. Skriv f.eks:

- "hent siste data"
- "hvordan ligger jeg an mot 10k-målet"
- "lag neste ukes plan"
- "er jeg klar for intervalløkt i dag?"

## Personvern

Alle data lagres lokalt. `.env`, `data/raw/`, `data/processed/` og `~/.garminconnect/` skal aldri pushes til Git. Se `.gitignore`.

## Status: skjelett – fyll inn scripts ved hjelp av Claude Code
