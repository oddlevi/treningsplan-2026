# Treningsplan – Odd Levi

Dette prosjektet bygger en datadrevet, periodisert treningsplan for løping basert på data fra Garmin Epix Pro og Strava. Claude Code skal hjelpe med datauthenting, analyse, plangenerering og ukentlig oppfølging.

## Løperprofil

- **Nåværende PB-er**: 10k 36:39 / halvmaraton 1:24:00
- **Høstmål 2026**: Sub 37:00 på 10k (01.09), sub 1:25:00 på halvmaraton (22.09)
- **Tilnærming**: Konservative mål, la formen overraske. Fokus på prosess, ikke tider.
- **Nåværende volum**: 40-60 km/uke
- **Klokke**: Garmin Epix Pro (full tilgang til HRV, Training Readiness, VO2 Max, Race Predictor, Training Load, Body Battery)
- **Strava**: Abonnement aktivt

## Løpskalender 2026

| Dato | Løp | Strategi |
|------|-----|----------|
| 06.06 | Halvmaraton | Kontrollert langtur m/tempo |
| 16.06 | 10 km | Kontrollert |
| 18.07 | Halvmaraton (kupert) | Hard langtur, +3-5 min ift flat |
| **01.09** | **10 km** | **MÅL-LØP – sub 37:00** |
| **22.09** | **Halvmaraton** | **MÅL-LØP – sub 1:25:00** |

## Etter-økt rutine

Når brukeren kjører `okt` eller `tokt`, spør ALLTID om subjektiv følelse etter at data er vist:

**Spørsmål å stille:**
1. Hvordan føltes økta? (lett/moderat/tungt/veldig tungt)
2. Hvordan var pusten? (kontrollert/anstrengt/tung)
3. Noe spesielt å merke? (tunge bein, god flyt, smerter, energi, etc.)

**Hvorfor:** Subjektiv følelse er ofte mer verdifull enn tall – spesielt i comeback-fase. HR kan være misvisende pga varme, søvn, stress, dehydrering. Pust og RPE gir det sanne bildet.

**Lagring:** Noter følelsen sammen med økt-evalueringen for å bygge erfaringsbase over tid.

## Morgenbriefing

En personlig tekstbasert "morgenbriefing" genereres hver morgen – en 4-5 minutters lesning som føles som om en personlig trener snakker direkte til deg.

### Kjøring

```bash
./run.sh morgen      # Genererer briefing automatisk etter sync
./run.sh briefing    # Vis kun briefingen
```

Briefingen lagres til:
- `rapport/briefing_YYYY-MM-DD.md` – lesbar versjon
- `rapport/briefing_YYYY-MM-DD_speech.txt` – talemanus for TTS
- `rapport/briefing_siste.md` – alltid nyeste

### Struktur (alltid denne rekkefølgen)

1. **Åpning** – Hilsen, dato, uke i planen, trend siste dager
2. **Kroppens status** – Readiness, HRV, søvn, ACWR tolket i naturlig språk
3. **Dagens økt** – Hva planen sier, hvorfor, konkret utførelse, fokuspunkt
4. **Tilbakeblikk** – Hva du gjorde samme tid i 2024 (hoppes hvis ingen data)
5. **Ukens bilde** – Volum så langt, hva som kommer
6. **Avslutning** – Én konkret motivasjonssetning forankret i data

### Tone og stil

- Norsk, naturlig muntlig språk
- Som en trener som kjenner deg, ikke en app
- Aldri klisjeer ("Push through", "No pain no gain")
- Aldri generisk pep talk – alltid forankret i faktiske tall
- Maks 600 ord

### TTS-utvidelse (fremtidig)

`briefing_YYYY-MM-DD_speech.txt` er forberedt for tekst-til-tale:
- Ingen markdown
- `<pause>`-markører mellom seksjoner
- "kilometer" ikke "km", "minutter" ikke "min"
- Kan mates direkte til TTS-motor (ElevenLabs, OpenAI TTS, etc.)

## Historikk – "Lær meg å kjenne"-fasen

Før det lages noen treningsplan, skal Claude Code hente ALL tilgjengelig historikk fra både Strava og Garmin – så langt tilbake som kontoene rekker. Dette er fundamentet.

### To lag med historisk data

**Lag 1 – Lang historikk (alle år, alle aktiviteter):**
Strava og Garmin har grunnleggende data så langt tilbake som kontoen rekker: dato, distanse, varighet, pace, snitt-HR, maks-HR, høydemeter, GPS-spor. Dette gir det store bildet.

**Lag 2 – Avansert (siden Epix Pro-eierskap):**
HRV Status, Training Readiness, Training Load (akutt/kronisk), Race Predictor, Body Battery, søvn-score, hvile-HR. Disse styrer daglige beslutninger.

Datamodellen må flagge per aktivitet hvilke metrikker som er tilgjengelige (`has_hrv`, `has_readiness`, `has_training_load`), så analyser ikke mikser epler og pærer.

### Hva "lær meg å kjenne"-rapporten skal svare på

Når full historikk er hentet, generer `rapport/laer_meg_aa_kjenne.md` som dekker:

1. **Volumhistorikk per år og måned**
   - Total km per år, snitt km/uke per måned
   - Identifiser perioder med høyest volum og hva som skjedde rett etter
   - Sesongmønster: hvilke måneder topper, hvilke faller av

2. **Pace-utvikling over tid**
   - Pace ved sammenlignbar HR (sone 2) per kvartal/år → ren formtrend, isolert fra intensitet
   - Pace per distanse (5k, 10k, halv, lange turer) over tid
   - VO2 Max-trend hvis tilgjengelig

3. **Race-historikk**
   - Alle løp >5k registrert (Strava activity type "Race" eller manuelt taggede)
   - Tid, dato, pace, plassering i treningssyklus (uker siden forrige peak)
   - Hva karakteriserte treningen 6-12 uker FØR de beste racene?

4. **Skadeindikasjoner og opphold**
   - Hull i dataene >14 dager
   - Comeback-mønster: hvor raskt bygget du opp igjen, hvordan gikk det
   - Eventuelle perioder med plutselig fall i volum

5. **Mønstre å lære av**
   - Optimal ukestruktur historisk (hvor mange økter, hvor mye lett vs hardt)
   - Hvor sterk er korrelasjonen volum → form for DEG?
   - Tegn på overbelastning du har vist før (HR drift, pace-fall ved samme HR, dårlig HRV)
   - Hvilke økttyper gir best respons (hvis det kan utledes)?

6. **Sterk-svak-profil**
   - VO2 Max vs terskel-utholdenhet (10k vs halv-prestasjon over tid)
   - Pace-fall fra 5k → 10k → halv: er du en "speedløper" eller "utholdenhetsløper"?
   - Cadence-mønster, høydeprofil-preferanse

7. **Anbefalt utgangspunkt**
   - Foreslå starvolum for fase 1 basert på siste 12 ukers snitt, ikke generiske tall
   - Foreslå pace-soner basert på faktisk HR-pace-forhold, ikke teoretiske formler
   - Flagg eventuelle røde flagg (overtraining-tegn, urealistisk volumhopp)

Rapporten skal være ærlig – om dataene viser at 3-6 mnd til sub 35/1:20 er urealistisk gitt din historikk, skal det stå svart på hvitt.

## Strategi: Periodisert tilnærming (19 uker)

**Periode:** 11.05 – 22.09.2026

Bygg form gradvis mot høstens mål-løp. Vår/sommer-løp brukes som kontrollerte treningsløp, ikke konkurranser. Kruttet spares til september.

| Blokk | Uker | Periode | Fokus |
|-------|------|---------|-------|
| 1 – Base/Comeback | 1-4 | 11.05–07.06 | Etabler rytme, terskel |
| 2 – Aerob build | 5-8 | 08.06–05.07 | Volum, langtur |
| 3 – Terskel-build | 9-12 | 06.07–02.08 | Terskel + VO2 |
| 4 – 10k-spesifikk | 13-16 | 03.08–01.09 | Race-pace → **10K MÅL** |
| 5 – Halv-spesifikk | 17-19 | 08.09–22.09 | Terskel → **HALV MÅL** |

Begrunnelse: VO2 Max-løft fra 10k-blokken blir grunnmur for terskelarbeid i halv-blokken. Aerob base er løperens største flaskehals.

## Faseplan

**Kontekst:** Comeback-fase etter 79 tapte treningsdager (ferie/livshøydepunkter, ikke skade). Tidligere toppform: VO2 Max 59.8, 10k 36:35, 50-60 km/uke. Nåværende: VO2 Max ~56, 47% lette økter (bør være 80%).

### Fase 1: 10k-blokk (~uke 1-12)

#### Fase 1A – Comeback/gjenoppbygging (uke 1-4)

| Mål | Detaljer |
|-----|----------|
| Volum | 60 → 64 → 66 → 35 km/uke (uke 4 = nedtrapping) |
| Sone-fordeling | Fra 47% → 80% lett. Prioritet #1 |
| Harde økter | 2/uke (Bakken-modell): Terskel 1 (tir) + Terskel 2 (tor) |
| Terskelarbeid | 54 → 65 → 68 min/uke (innenfor 45-75 min ramme) |
| Lang tur | Opp til 18 km, rolig pace (5:10-5:25/km), ingen progresjon |
| VO2 Max-mål | Tilbake til 58+ |

**Ingen race-pace ennå.** Fokus er å reetablere aerob base via kontrollert terskeltrening.

#### Fase 1B – Build (uke 5-8)

| Uke | Volum | Kvalitet |
|-----|-------|----------|
| 5 | 65 km | 1× VO2 Max (5×1000m @ 3:45), 1× terskel (3×10 min @ 4:00) |
| 6 | 68 km | 1× VO2 Max (6×1000m), 1× terskel (4×8 min) |
| 7 | 70 km | 1× VO2 Max (5×1200m), 1× terskel (2×15 min) |
| 8 | 56 km | Nedtrapping – kun 1 lett kvalitetsøkt |

Lang tur: 18-22 km med siste 20-30 min i marathon-pace (4:10-4:20/km).

#### Fase 1C – Peak (uke 9-11)

| Uke | Volum | Kvalitet |
|-----|-------|----------|
| 9 | 65 km | 1× 10k-pace (6×1000m @ 3:35-3:40), 1× fartlek |
| 10 | 63 km | 1× 10k-pace (3×2000m @ 3:38), 1× korte reps (10×400m @ 3:20) |
| 11 | 60 km | 1× lett 10k-pace (4×1000m), lang tur 18 km med tempo-finish |

Race-spesifikk trening. Volum reduseres svakt for å absorbere intensitet.

#### Fase 1D – Taper + race (uke 12)

| Dag | Økt |
|-----|-----|
| Man | Hvile |
| Tir | 8 km lett + 4×200m strides |
| Ons | 6 km lett |
| Tor | 5 km lett + 3×400m @ 10k-pace |
| Fre | Hvile |
| Lør | 3 km oppvarming + 10k RACE |
| Søn | 5 km rolig restitusjon |

Volum: ~35 km. Hold litt intensitet for å holde systemet "våkent".

---

### Fase 2: Halv-blokk (~uke 14-25)

*Uke 13 er restitusjonsuke etter 10k-racet.*

| Underfase | Uker | Fokus | Volum |
|-----------|------|-------|-------|
| Aerob base | 14-17 | Volumøkning, lange turer opp til 25 km | 70-80 km/uke |
| Terskel-build | 18-21 | Lange terskelintervaller, marathon pace | 75-85 km/uke |
| Spesifikk | 22-24 | Halv race pace, lange progresjoner | 70-80 km/uke |
| Taper + race | 25 | Reduksjon | 45-55 km/uke |

**Volumjustering:** +5-10% vs. opprinnelig plan, basert på dokumentert kapasitet fra 2025 (60+ km/uke uten problemer).

---

**Progresjonsregler:**
- Maks +10% volum per uke
- Hver 4. uke er nedtrappingsuke (-20% volum)
- Ved HRV-status "low" 2+ dager → bytt hard økt med rolig
- Ved Training Readiness <50 på hard økt-dag → flytt økten

## Treningsprinsipper – Marius Bakken / Norsk modell

**Hovedfilosofi:** Høyt volum av kontrollert terskeltrening, ekte rolige dager, minimal VO2 Max-trening i base-fase.

### Ukestruktur

| Dag | Økttype | Intensitet |
|-----|---------|------------|
| Mandag | Hvile | - |
| Tirsdag | **Terskel 1** – lange drag | Laktat 2,5-3,0 mmol/l |
| Onsdag | Rolig restitusjon | Sone 2, ekte rolig |
| Torsdag | **Terskel 2** – korte drag | Laktat 3,0-4,0 mmol/l |
| Fredag | Hvile | - |
| Lørdag | Lang tur | Sone 2, INGEN progresjon |
| Søndag | Restitusjon (valgfri) | Sone 1-2 |

### Bakken-prinsippene

1. **To terskeløkter per uke** (tirsdag + torsdag)
   - Terskel 1: lengre drag (5-8 min), lavere intensitet (3:55-4:10/km)
   - Terskel 2: kortere drag (3-4 min), høyere intensitet (3:45-4:00/km)
   - Total terskelarbeid: 45-75 min/uke i base-fase

2. **Kontrollert terskel = nøkkelen**
   - Laktat 2,5-4,0 mmol/l (ikke høyere!)
   - Subjektivt: "komfortabelt hardt" – kunne snakket i korte setninger
   - Lavere variasjon i pace mellom øktene (~10 sek/km forskjell)
   - Aldri "all out" på terskeløkt

3. **Ekte rolige dager (onsdag + søndag)**
   - MÅ være i sone 2 (5:10-5:25/km), HR ~130
   - Unngå "grå sone" – ingen halv-hard løping
   - Disse dagene er like viktige som terskeløktene

4. **Lang tur = rolig**
   - Alltid i sone 2 (5:10-5:25/km), HR ~130
   - INGEN progresjon eller tempo-finish i base-fase
   - Bygg utholdenhet gjennom varighet, ikke intensitet

5. **VO2 Max-intervaller brukes sparsomt**
   - Kun i peak-fase (1C, 2C)
   - Ikke nødvendig for å bygge aerob kapasitet
   - Terskeltrening gir tilstrekkelig stimulus i base/build

### Generelle regler

- **Progresjon på pace, ikke HR**: Når pace ved samme HR forbedres → form stiger
- **HRV-styrt justering**: HRV-status "low" 2+ dager → bytt hard økt med rolig
- **Training Readiness < 50** på øktdag → flytt hard økt til neste dag
- **ACWR** holdes mellom 0,8 og 1,3. Over 1,5 = skaderisiko
- **Maks +10% volum per uke**, hver 4. uke er nedtrapping (-20%)

## Pace-soner – Bakken-terminologi

Basert på 10k-PB 36:35 = 3:39/km. Oppdatert 2026-05-14.

> **Justert 14.05:** Terskel-soner justert basert på faktiske øktdata. HR = maks HR på drag.

| Sone | Pace | Maks HR | Laktat | Bruk |
|------|------|---------|--------|------|
| **Restitusjon** | 5:20-5:45/km | <130 | <1,5 mmol/l | Oppvarming, nedjogg, dagen etter hard økt |
| **Rolig sone 2** | 5:10-5:25/km | <145 | 1,5-2,0 mmol/l | Langturer, onsdag/søndag, mesteparten av volumet |
| **Terskel 1** | 3:55-4:10/km | <170 | 2,5-3,0 mmol/l | Lange drag (5-8 min), tirsdag |
| **Terskel 2** | 3:45-4:00/km | 168-174 | 3,0-4,0 mmol/l | Korte drag (3-4 min), torsdag |
| **VO2 Max** | 3:30-3:40/km | 178+ | 5+ mmol/l | Kun peak-fase |
| **10k MÅL-PACE** | 3:40-3:45/km | 178+ | ~4,5 mmol/l | Race 01.09 (skal opp til) |
| **Halv MÅL-PACE** | 3:55-4:00/km | 170-178 | 3,5-4,0 mmol/l | Race 22.09 (skal opp til) |

### Terskel-detaljer

| Økttype | Intervaller | Pause | Total arbeid | Følelse |
|---------|-------------|-------|--------------|---------|
| Terskel 1 (tirsdag) | 5-7 min × 5-6 | 90 sek @ 5:30 | 25-40 min | "Kunne snakket i korte setninger" |
| Terskel 2 (torsdag) | 3-4 min × 8-10 | 60-75 sek @ 5:30 | 24-40 min | "Komfortabelt hardt" |

**Viktig:** Terskel-pace skal føles kontrollert. Hvis du ikke kan snakke korte setninger, går du for hardt. Laktat over 4,0 mmol/l = for intensivt for denne fasen.

## Script-struktur: Én sannhet per funksjon

**Prinsipp:** All logikk ligger i `scripts/`. Rot-filer er kun snarveier.

| Snarvei (rot) | Kanonisk versjon | Hva den gjør |
|---------------|------------------|--------------|
| `morgen.py` | `scripts/morgen_status.py --sync` | Syncer Garmin + viser morgen-anbefaling |

### Kanoniske scripts

| Script | Flagg | Beskrivelse |
|--------|-------|-------------|
| `scripts/morgen_status.py` | `--sync`, `--dato YYYY-MM-DD` | Morgenstatus med anbefaling |
| `scripts/evaluer_okt.py` | | Evaluerer siste økt mot plan |
| `scripts/fetch_garmin.py` | `--days N`, `--full-history` | Henter data fra Garmin |
| `scripts/fetch_strava.py` | `--days N`, `--full-history` | Henter data fra Strava |
| `scripts/sync_weekly.py` | `--no-sync`, `--week N` | Ukerapport med sync |
| `scripts/diagnose_readiness.py` | | Analyserer årsaker til lav Readiness |
| `scripts/morgen_briefing.py` | `--dato`, `--lagre`, `--vis` | Genererer personlig morgenbriefing |
| `plan/juster.py` | | Foreslår plan-justeringer |

**Regel:** Ikke dupliser logikk. Hvis du trenger ny funksjonalitet, legg den i `scripts/` og lag eventuelt en tynn snarvei i rot.

## Prosjektstruktur

```
treningsplan/
├── CLAUDE.md                # denne filen
├── morgen.py                # SNARVEI → scripts/morgen_status.py --sync
├── .env                     # API-nøkler (gitignored!)
├── requirements.txt
├── data/
│   ├── raw/                 # rå JSON fra Strava/Garmin
│   └── processed/           # SQLite/Parquet
├── scripts/
│   ├── morgen_status.py     # KANONISK: morgenstatus + anbefaling
│   ├── evaluer_okt.py       # KANONISK: evaluer økt mot plan
│   ├── fetch_strava.py      # OAuth2 + hent aktiviteter
│   ├── fetch_garmin.py      # garminconnect: HRV, readiness, VO2
│   ├── sync_weekly.py       # ukerapport + sync
│   └── diagnose_readiness.py # rotårsak-analyse
├── analyse/
│   ├── historikk.py         # bygger "lær meg å kjenne"-rapport
│   ├── baseline.py          # status nå: pace, HR-soner, volum
│   ├── trender.py           # VO2/LT/volum over tid
│   ├── race_analyse.py      # hva karakteriserte gode race-treningsblokker
│   └── soneanalyse.py       # % tid i hver sone
├── plan/
│   ├── juster.py            # KANONISK: foreslå plan-justeringer
│   ├── generator.py         # bygger uker basert på fase + data
│   └── current_plan.md      # aktiv plan (oppdateres)
└── rapport/
    └── ukerapport_YYYY-WW.md
```

## Vanlige kommandoer

### Kjøring via alias (etter oppsett)

| Alias | Hva den gjør |
|-------|--------------|
| `morgen` | Sync Garmin + morgenstatus med anbefaling |
| `okt` | Evaluer siste økt mot plan |
| `uke` | Generer ukerapport |
| `juster` | Foreslå plan-justeringer |
| `diagnose` | Analyser årsaker til lav Readiness |
| `briefing` | Vis dagens morgenbriefing |
| `trening` | Naviger til prosjektmappa |

### Kjøring via run.sh (fra prosjektmappa)

```bash
./run.sh morgen     # Morgenstatus med sync
./run.sh okt        # Evaluer siste økt
./run.sh uke        # Ukerapport
./run.sh juster     # Plan-justeringer
./run.sh diagnose   # Readiness-diagnose
./run.sh sync       # Bare sync (ingen analyse)
```

### Eksplisitt kjøring med venv

```bash
.venv/bin/python scripts/morgen_status.py --sync
.venv/bin/python scripts/evaluer_okt.py
.venv/bin/python scripts/sync_weekly.py
.venv/bin/python plan/juster.py
.venv/bin/python scripts/diagnose_readiness.py
```

### Oppsett av aliaser (én gang)

Legg til i `~/.zshrc`:
```bash
source "/Users/oddlevipaulsen/Odd Levi HD/Kodeprosjekter/Treningsplan Garmin Strava/setup_aliases.sh"
```

Deretter: `source ~/.zshrc` eller åpne ny terminal.

### Hva kommandoene gjør

- **morgen** – Syncer Garmin, viser HRV/Readiness/søvn, gir GRØNT/GULT/RØDT anbefaling
- **okt** – Analyserer siste aktivitet vs plan, klassifiserer avvik, gir råd
- **uke** – Syncer + genererer ukerapport til `rapport/ukerapport_YYYY-WW.md`
- **juster** – Foreslår plan-justeringer basert på faktisk prestasjon
- **diagnose** – Henter detaljert søvn/stress/belastning, finner rotårsak til lav Readiness

### Engangskjøring / analyse

- **"hent all historikk"** → kjør `scripts/fetch_historikk.py` (engangskjøring, kan ta tid)
- **"lær meg å kjenne"** → kjør `analyse/historikk.py`, generer `rapport/laer_meg_aa_kjenne.md`
- **"hent siste data"** → kjør `scripts/sync_weekly.py`, oppsummer hva som er nytt
- **"hvordan ligger jeg an"** → kjør `analyse/baseline.py` + `analyse/trender.py`, gi statusrapport mot målene
- **"vis race-historikk"** → kjør `analyse/race_analyse.py`, vis alle løp + kontekst
- **"lag neste ukes plan"** → les `plan/current_plan.md` + siste 14 dager data, generer neste 7 dager
- **"er jeg klar for hard økt"** → sjekk Training Readiness, HRV-status, søvn, ACWR – gi go/no-go

## Tekniske avhengigheter

```
stravalib>=2.0          # Strava OAuth + API
garminconnect>=0.2.20   # uoffisiell Garmin Connect-klient
pandas>=2.0
duckdb>=0.10            # eller sqlite3 (stdlib)
plotly>=5.0             # interaktive grafer
python-dotenv
rich                    # pen terminal-output
```

## API-tilgang

### Strava
1. Opprett app på https://www.strava.com/settings/api → `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`
2. Authorization callback domain: `localhost`
3. Scope som trengs: `read,activity:read_all`
4. Refresh token caches i `.env`

### Garmin Connect
`garminconnect` er uoffisielt. Bruker brukernavn/passord. Garmin kan kreve MFA – bibblioteket støtter `garth`-token caching i `~/.garminconnect/`. Bruk dedikert app-passord hvis MFA er på.

**Viktig**: aldri commit `.env` eller cached tokens til Git.

## Rate limits

- Strava: 100 req / 15 min, 1000 req / dag → cache aggressivt, bare hent nye aktiviteter
- Garmin: ingen offisiell grense, men respekter ~1 req/sek for å unngå blokk

## Datamodell (forslag)

SQLite-tabeller:

```sql
activities (
  id INTEGER PRIMARY KEY,        -- Strava activity ID
  garmin_id INTEGER,             -- kobling til Garmin
  start_date TIMESTAMP,
  sport TEXT,
  distance_km REAL,
  moving_time_s INTEGER,
  avg_pace_s_per_km REAL,
  avg_hr INTEGER,
  max_hr INTEGER,
  elevation_gain_m REAL,
  avg_cadence REAL,
  perceived_effort INTEGER,
  is_race BOOLEAN,               -- markert som race i Strava
  training_load REAL,            -- Garmin Training Load (nyere data)
  aerobic_te REAL,               -- Training Effect aerob
  anaerobic_te REAL,
  vo2max_estimate REAL,
  has_hrv BOOLEAN,               -- flagg: avansert data tilgjengelig?
  has_readiness BOOLEAN,
  has_training_load BOOLEAN,
  raw_json TEXT                  -- hele responsen for senere parsing
)

daily_metrics (
  date DATE PRIMARY KEY,
  hrv_status TEXT,               -- balanced/unbalanced/low/poor
  hrv_weekly_avg REAL,
  training_readiness INTEGER,    -- 0-100
  body_battery_max INTEGER,
  body_battery_min INTEGER,
  sleep_score INTEGER,
  resting_hr INTEGER,
  training_status TEXT,          -- productive/maintaining/peaking/overreaching/etc
  acute_load REAL,
  chronic_load REAL
)

splits (
  activity_id INTEGER,
  split_km INTEGER,
  pace_s_per_km REAL,
  hr INTEGER,
  elevation_change_m REAL,
  PRIMARY KEY (activity_id, split_km)
)
```

## Personlige observasjoner

- **Lav HRV hos meg er alltid knyttet til usunn livsstil (alkohol)** – dette sliter kroppen. Når HRV er lav, se på livsstil først, ikke trening.

## Notater for Claude Code

- Når du gjør analyse, **bruk faktisk data fra databasen** – ikke gjett pace-soner basert på generelle formler hvis Garmin har målt LT direkte.
- Generer treningsøkter med konkret pace-range, ikke "lett tempo" – f.eks. "4×1000m @ 3:20-3:25/km, p=400m jog".
- Skadeforebygging > prestasjon. Hvis dataene indikerer overbelastning, foreslå reduksjon selv om planen sier hard økt.
- Norsk språk i ukerapporter og planer.
- Når du tviler, spør brukeren før du gjør store endringer i `plan/current_plan.md`.
