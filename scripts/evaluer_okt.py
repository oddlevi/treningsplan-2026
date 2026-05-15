#!/usr/bin/env python3
"""
Økt-evaluering etter trening.

Analyserer nyeste aktivitet, sammenligner med planen,
og gir konkrete observasjoner og råd.

Bruk:
    python scripts/evaluer_okt.py              # Evaluér siste økt
    python scripts/evaluer_okt.py --dato 2026-05-13  # Evaluér økt på dato
"""

import sqlite3
import re
import json
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional, List, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Prosjektstier
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
EVAL_DB_PATH = PROJECT_ROOT / "data" / "processed" / "okt_evalueringer.db"
PLAN_PATH = PROJECT_ROOT / "plan" / "current_plan.md"

console = Console()

# Pace-soner (sekunder per km) - justert 14.05 basert på faktiske øktdata
PACE_ZONES = {
    'restitusjon': (320, 345),      # 5:20-5:45
    'rolig_sone2': (310, 325),      # 5:10-5:25
    'terskel_1': (235, 250),        # 3:55-4:10
    'terskel_2': (225, 240),        # 3:45-4:00
}


@dataclass
class FaktiskOkt:
    """Faktisk gjennomført økt."""
    id: int
    dato: str
    navn: str
    distanse_km: float
    tid_s: int
    snitt_pace_s: float
    snitt_hr: int
    maks_hr: int
    splits: list
    kilde: str


@dataclass
class PlanlagtOkt:
    """Planlagt økt fra planen."""
    dag: str
    dato: str
    type: str
    distanse_km: float
    pace_range: str
    pace_min_s: int
    pace_max_s: int
    terskelarbeid_min: int


@dataclass
class Evaluering:
    """Evalueringsresultat."""
    dato: str
    aktivitet_id: int
    klassifisering: str
    volum_avvik_pct: float
    pace_avvik_sek: float
    observasjoner: list
    raad: list
    detaljer: dict


def init_eval_db():
    """Initialiserer evalueringsdatabasen."""
    conn = sqlite3.connect(EVAL_DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS evalueringer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dato TEXT NOT NULL,
            aktivitet_id INTEGER,
            klassifisering TEXT,
            volum_avvik_pct REAL,
            pace_avvik_sek REAL,
            observasjoner TEXT,
            raad TEXT,
            detaljer TEXT,
            opprettet TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def hent_siste_okt(dato: str = None) -> Optional[FaktiskOkt]:
    """Henter siste aktivitet fra databasen."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if dato:
        cursor.execute("""
            SELECT
                id,
                date(CASE
                    WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                    ELSE start_date
                END) as activity_date,
                name,
                distance_km,
                moving_time_s,
                avg_pace_s_per_km,
                avg_hr,
                max_hr,
                source
            FROM activities
            WHERE activity_date = ?
            AND sport IN ('running', 'Run', 'trail_running')
            ORDER BY start_date DESC
            LIMIT 1
        """, (dato,))
    else:
        cursor.execute("""
            SELECT
                id,
                date(CASE
                    WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                    ELSE start_date
                END) as activity_date,
                name,
                distance_km,
                moving_time_s,
                avg_pace_s_per_km,
                avg_hr,
                max_hr,
                source
            FROM activities
            WHERE sport IN ('running', 'Run', 'trail_running')
            ORDER BY start_date DESC
            LIMIT 1
        """)

    row = cursor.fetchone()

    if not row:
        conn.close()
        return None

    aktivitet_id = row[0]

    # Hent splits hvis tilgjengelig
    cursor.execute("""
        SELECT split_km, pace_s_per_km, hr
        FROM splits
        WHERE activity_id = ?
        ORDER BY split_km
    """, (aktivitet_id,))

    splits = [{'km': r[0], 'pace_s': r[1], 'hr': r[2]} for r in cursor.fetchall()]

    conn.close()

    return FaktiskOkt(
        id=row[0],
        dato=row[1],
        navn=row[2] or '',
        distanse_km=row[3] or 0,
        tid_s=row[4] or 0,
        snitt_pace_s=row[5] or 0,
        snitt_hr=row[6] or 0,
        maks_hr=row[7] or 0,
        splits=splits,
        kilde=row[8] or ''
    )


def parse_pace_til_sekunder(pace_str: str) -> tuple[int, int]:
    """Parser pace-streng til (min_sek, max_sek)."""
    if not pace_str:
        return (0, 0)

    # Fjern /km
    pace_str = pace_str.replace('/km', '').strip()

    # Håndter range (f.eks. "4:10-4:20")
    if '-' in pace_str:
        parts = pace_str.split('-')
        min_pace = parse_single_pace(parts[0])
        max_pace = parse_single_pace(parts[1])
        return (min_pace, max_pace)
    else:
        pace = parse_single_pace(pace_str)
        return (pace - 5, pace + 5)  # ±5 sek margin


def parse_single_pace(pace: str) -> int:
    """Parser en enkelt pace til sekunder."""
    pace = pace.strip()
    if ':' in pace:
        parts = pace.split(':')
        return int(parts[0]) * 60 + int(parts[1])
    return 0


def format_pace(sek: float) -> str:
    """Formaterer sekunder til pace-streng."""
    if sek <= 0:
        return '-'
    min_del = int(sek // 60)
    sek_del = int(sek % 60)
    return f"{min_del}:{sek_del:02d}/km"


def hent_planlagt_okt(dato: str) -> Optional[PlanlagtOkt]:
    """Parser planlagt økt fra current_plan.md (tabellformat)."""
    if not PLAN_PATH.exists():
        return None

    with open(PLAN_PATH, 'r') as f:
        content = f.read()

    # Parse dato
    try:
        dt = datetime.strptime(dato, '%Y-%m-%d')
        dag_maned = dt.strftime('%d.%m')
        ukedag_kort = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn'][dt.weekday()]
        ukedag = ['Mandag', 'Tirsdag', 'Onsdag', 'Torsdag', 'Fredag', 'Lørdag', 'Søndag'][dt.weekday()]
    except:
        return None

    # Finn tabellrad for denne datoen
    # Format: | Fre 15.05 | Lang tur 🟢 | | | *14* | *5:10-5:25/km, HR 125-135* | ... |
    # eller: | ✅ Tor 14.05 | Terskel 2 | **12.2** | ... |
    pattern = rf'\|\s*(?:✅\s*)?{ukedag_kort}\s+{dag_maned}[^|]*\|\s*([^|]+)\|'
    match = re.search(pattern, content)

    if not match:
        return None

    okt_type = match.group(1).strip()
    # Fjern emoji-markører
    okt_type = re.sub(r'[🔴🟢🟡⚪]', '', okt_type).strip()

    # Finn hele raden
    line_start = content.rfind('\n', 0, match.start()) + 1
    line_end = content.find('\n', match.end())
    full_line = content[line_start:line_end] if line_end > 0 else content[line_start:]

    # Parse kolonner fra tabellrad
    columns = [c.strip() for c in full_line.split('|')]
    # Typisk: ['', 'Dag', 'Økt', 'Km gjennomført', 'Gjennomført', 'Km planlagt', 'Planlagt', '2024', '']

    # Finn planlagt km (kolonne med *tall*)
    distanse = 0
    pace_range = ''

    for col in columns:
        # Planlagt km: *14* eller *12*
        km_match = re.search(r'\*(\d+(?:\.\d+)?)\*', col)
        if km_match and not pace_range:  # Første tall er km
            distanse = float(km_match.group(1))

        # Planlagt pace: *5:10-5:25/km* eller i beskrivelse
        pace_match = re.search(r'(\d:\d{2}-\d:\d{2})/km', col)
        if pace_match:
            pace_range = pace_match.group(1) + '/km'

    # Parse pace til sekunder basert på økttype
    if not pace_range and 'terskel 1' in okt_type.lower():
        pace_min_s, pace_max_s = PACE_ZONES['terskel_1']
        pace_range = '3:55-4:10/km'
    elif not pace_range and 'terskel 2' in okt_type.lower():
        pace_min_s, pace_max_s = PACE_ZONES['terskel_2']
        pace_range = '3:45-4:00/km'
    elif not pace_range and any(x in okt_type.lower() for x in ['rolig', 'restitusjon', 'lang tur']):
        pace_min_s, pace_max_s = PACE_ZONES['rolig_sone2']
        pace_range = '5:10-5:25/km'
    else:
        pace_min_s, pace_max_s = parse_pace_til_sekunder(pace_range)

    # Beregn terskelarbeid fra økttype
    terskelarbeid = 0
    if 'terskel' in okt_type.lower():
        # Sjekk for intervall-beskrivelse i raden
        intervall_match = re.search(r'(\d+)[×x](\d+)\s*min', full_line)
        if intervall_match:
            terskelarbeid = int(intervall_match.group(1)) * int(intervall_match.group(2))

    return PlanlagtOkt(
        dag=ukedag,
        dato=dato,
        type=okt_type,
        distanse_km=distanse,
        pace_range=pace_range,
        pace_min_s=pace_min_s,
        pace_max_s=pace_max_s,
        terskelarbeid_min=terskelarbeid
    )


def klassifiser_okt(faktisk: FaktiskOkt, planlagt: Optional[PlanlagtOkt]) -> tuple[str, float, float]:
    """
    Klassifiserer økten vs plan.

    Returnerer (klassifisering, volum_avvik_pct, pace_avvik_sek)
    """
    if not planlagt:
        return ('Ikke planlagt aktivitet', 0, 0)

    if 'hvile' in planlagt.type.lower():
        return ('Trening på hviledag', 0, 0)

    # Beregn avvik
    if planlagt.distanse_km > 0:
        volum_avvik = ((faktisk.distanse_km - planlagt.distanse_km) / planlagt.distanse_km) * 100
    else:
        volum_avvik = 0

    # Pace-avvik fra målsone midtpunkt
    if planlagt.pace_min_s > 0 and planlagt.pace_max_s > 0:
        midt_pace = (planlagt.pace_min_s + planlagt.pace_max_s) / 2
        pace_avvik = faktisk.snitt_pace_s - midt_pace
    else:
        pace_avvik = 0

    # Klassifiser
    if abs(volum_avvik) <= 5 and abs(pace_avvik) <= 5:
        return ('Truffet plan ✅', volum_avvik, pace_avvik)
    elif abs(volum_avvik) <= 15 and abs(pace_avvik) <= 10:
        return ('Lett avvik ⚡', volum_avvik, pace_avvik)
    else:
        return ('Stort avvik ⚠️', volum_avvik, pace_avvik)


def analyser_splits(splits: list, planlagt: Optional[PlanlagtOkt]) -> list[str]:
    """Analyserer splits for terskeløkter."""
    observasjoner = []

    if not splits or len(splits) < 3:
        return observasjoner

    # Sjekk pacing: gikk du ut for fort?
    forste_halvdel = splits[:len(splits)//2]
    siste_halvdel = splits[len(splits)//2:]

    snitt_forste = sum(s['pace_s'] for s in forste_halvdel) / len(forste_halvdel)
    snitt_siste = sum(s['pace_s'] for s in siste_halvdel) / len(siste_halvdel)

    pace_drift = snitt_siste - snitt_forste

    if pace_drift > 10:
        observasjoner.append(f"⚠️ Positiv split: {pace_drift:.0f} sek/km saktere i andre halvdel. Du gikk ut for fort.")
    elif pace_drift < -10:
        observasjoner.append(f"✅ Negativ split: {abs(pace_drift):.0f} sek/km raskere mot slutten. God pacing!")
    else:
        observasjoner.append(f"✅ Jevn pacing gjennom økten (±{abs(pace_drift):.0f} sek/km)")

    # HR drift
    hr_splits = [s for s in splits if s.get('hr') and s['hr'] > 0]
    if len(hr_splits) >= 4:
        forste_hr = sum(s['hr'] for s in hr_splits[:len(hr_splits)//2]) / (len(hr_splits)//2)
        siste_hr = sum(s['hr'] for s in hr_splits[len(hr_splits)//2:]) / (len(hr_splits) - len(hr_splits)//2)
        hr_drift = siste_hr - forste_hr

        if hr_drift > 10:
            observasjoner.append(f"⚠️ HR-drift: +{hr_drift:.0f} bpm fra start til slutt. Kan tyde på dehydrering eller for høy intensitet.")
        elif hr_drift > 5:
            observasjoner.append(f"📊 Moderat HR-drift: +{hr_drift:.0f} bpm. Normalt for lengre økter.")

    # Sjekk pauseløping (hvis terskeløkt)
    if planlagt and 'terskel' in planlagt.type.lower():
        # Finn splits som sannsynligvis er pauser (mye saktere enn snitt)
        snitt = sum(s['pace_s'] for s in splits) / len(splits)
        langsomme = [s for s in splits if s['pace_s'] > snitt + 30]

        if langsomme:
            pause_snitt = sum(s['pace_s'] for s in langsomme) / len(langsomme)
            if pause_snitt > 320:  # Saktere enn 5:20
                observasjoner.append(f"✅ Pausene var rolige ({format_pace(pause_snitt)})")
            elif pause_snitt > 300:
                observasjoner.append(f"⚡ Pausene var litt raske ({format_pace(pause_snitt)}) – prøv å jogge roligere")

    return observasjoner


def generer_raad(faktisk: FaktiskOkt, planlagt: Optional[PlanlagtOkt], klassifisering: str, observasjoner: list) -> list[str]:
    """Genererer konkrete råd til neste tilsvarende økt."""
    raad = []

    if not planlagt:
        raad.append("Sjekk at denne økten passer inn i ukeplanen")
        return raad

    # Basert på klassifisering
    if 'Stort avvik' in klassifisering:
        if faktisk.distanse_km < planlagt.distanse_km * 0.8:
            raad.append("Neste gang: Prøv å fullføre hele planlagt distanse, eller juster planen ned")
        if faktisk.snitt_pace_s < planlagt.pace_min_s - 10:
            raad.append("Du løp raskere enn planlagt. Neste gang: Hold deg i målsonen selv om det føles lett")

    # Basert på observasjoner
    if any('gikk ut for fort' in o.lower() for o in observasjoner):
        raad.append("Neste terskeløkt: Start 5-10 sek/km saktere enn målpace de første 2 dragene")

    if any('hr-drift' in o.lower() and '⚠️' in o for o in observasjoner):
        raad.append("Vurder å ta med væske på økter over 60 min")

    # Generelle råd basert på økttype
    if planlagt and 'terskel' in planlagt.type.lower():
        if faktisk.snitt_pace_s < planlagt.pace_min_s:
            raad.append("Terskel-pace skal føles kontrollert. Hvis du kan gå raskere, betyr det at planen kan oppdateres")

    if not raad:
        raad.append("Fortsett med samme tilnærming neste gang!")

    return raad[:3]  # Max 3 råd


def oppdater_plan_med_faktisk(faktisk: FaktiskOkt):
    """Oppdaterer planen med gjennomført økt."""
    if not PLAN_PATH.exists():
        return False

    with open(PLAN_PATH, 'r') as f:
        content = f.read()

    # Parse dato
    try:
        dt = datetime.strptime(faktisk.dato, '%Y-%m-%d')
        dag_maned = dt.strftime('%d.%m')
        ukedag_kort = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn'][dt.weekday()]
    except:
        return False

    # Finn tabellrad for denne datoen
    # Format: | Fre 15.05 | Lang tur 🟢 | | | *14* | *5:10-5:25/km* | 2024 |
    pattern = rf'(\|\s*)({ukedag_kort}\s+{dag_maned}[^|]*\|[^|]*\|)\s*\|([^|]*)\|'
    match = re.search(pattern, content)

    if not match:
        # Prøv også med emoji-prefix (✅)
        pattern = rf'(\|\s*)(✅\s*{ukedag_kort}\s+{dag_maned}[^|]*\|[^|]*\|)\s*\|([^|]*)\|'
        match = re.search(pattern, content)

    if not match:
        console.print(f"[yellow]Kunne ikke finne rad for {dag_maned} i planen[/yellow]")
        return False

    # Hent planlagt økt for å sjekke type
    planlagt = hent_planlagt_okt(faktisk.dato)

    # Bruk snitt-HR for rolige økter, maks-HR for harde økter
    er_rolig_okt = False
    if planlagt:
        okt_type_lower = planlagt.type.lower()
        er_rolig_okt = any(x in okt_type_lower for x in ['rolig', 'restitusjon', 'lang tur', 'hvile'])

    # Formater gjennomført data
    pace_str = format_pace(faktisk.snitt_pace_s)
    if er_rolig_okt and faktisk.snitt_hr > 0:
        hr_str = f"snitt HR {faktisk.snitt_hr}"
    elif faktisk.maks_hr > 0:
        hr_str = f"maks HR {faktisk.maks_hr}"
    else:
        hr_str = ""

    gjennomfort = f"**{faktisk.distanse_km:.1f}**"
    beskrivelse = f"**{pace_str}"
    if hr_str:
        beskrivelse += f", {hr_str}"
    beskrivelse += "**"

    # Bygg ny rad med ✅ og gjennomført data
    prefix = match.group(1)
    dag_okt = match.group(2)

    # Fjern eksisterende ✅ hvis den finnes, legg til ny
    dag_okt_clean = re.sub(r'✅\s*', '', dag_okt)

    # Finn resten av raden
    rest_start = match.end()
    rest_end = content.find('\n', rest_start)
    rest_of_row = content[rest_start:rest_end] if rest_end > 0 else content[rest_start:]

    # Ny rad: | ✅ Dag | Økt | **km** | **beskrivelse** | resten...
    new_row = f"{prefix}✅ {dag_okt_clean} {gjennomfort} | {beskrivelse} |{rest_of_row}"

    # Erstatt i content
    full_match_end = rest_end if rest_end > 0 else len(content)
    new_content = content[:match.start()] + new_row + content[full_match_end:]

    with open(PLAN_PATH, 'w') as f:
        f.write(new_content)

    console.print(f"[green]✓ Plan oppdatert: {ukedag_kort} {dag_maned} → {faktisk.distanse_km:.1f} km @ {pace_str}[/green]")

    # Oppdater uketotal
    oppdater_uketotal(faktisk.dato)

    return True


def oppdater_uketotal(dato: str):
    """Oppdaterer total km for uken basert på gjennomførte økter."""
    if not PLAN_PATH.exists():
        return False

    with open(PLAN_PATH, 'r') as f:
        content = f.read()

    # Finn uke-seksjonen for denne datoen
    try:
        dt = datetime.strptime(dato, '%Y-%m-%d')
        # Finn mandagen i denne uka
        mandag = dt - timedelta(days=dt.weekday())
        mandag_str = mandag.strftime('%d.%m')
    except:
        return False

    # Finn UKE-header etterfulgt av dato-linje med mandagen
    # Format: # UKE 1\n\n**11.05 – 17.05.2026 | ...
    uke_pattern = rf'(# UKE \d+)\n+\*\*{mandag_str}'
    uke_match = re.search(uke_pattern, content)

    if not uke_match:
        return False

    uke_header = uke_match.group(1)  # "# UKE X"
    uke_start = uke_match.start()

    # Finn slutten av denne uka (neste # UKE eller # BLOKK eller slutten)
    neste_seksjon = re.search(r'\n# (?:UKE|BLOKK)', content[uke_start + 1:])
    if neste_seksjon:
        uke_end = uke_start + 1 + neste_seksjon.start()
    else:
        uke_end = len(content)

    uke_content = content[uke_start:uke_end]

    # Finn alle gjennomførte km (tall i **X.X** format i Km-kolonnen)
    # Tabellformat: | ✅ Dag | Økt | **km** | beskrivelse | ...
    km_pattern = r'\|\s*✅[^|]+\|[^|]+\|\s*\*\*(\d+\.?\d*)\*\*'
    km_matches = re.findall(km_pattern, uke_content)

    total_km = sum(float(km) for km in km_matches if km)

    # Finn og oppdater Total-raden
    # Format: | **Total** | | **XX.X** | | *planlagt* | ...
    total_pattern = r'(\|\s*\*\*Total\*\*\s*\|[^|]*\|)\s*\*\*[\d.]*\*\*'
    total_match = re.search(total_pattern, uke_content)

    if total_match:
        new_total_row = f"{total_match.group(1)} **{total_km:.1f}**"
        new_uke_content = uke_content[:total_match.start()] + new_total_row + uke_content[total_match.end():]
        new_content = content[:uke_start] + new_uke_content + content[uke_end:]

        with open(PLAN_PATH, 'w') as f:
            f.write(new_content)

        console.print(f"[green]✓ Uketotal oppdatert: {total_km:.1f} km[/green]")
        return True

    return False


def lagre_evaluering(evaluering: Evaluering):
    """Lagrer evalueringen til databasen."""
    init_eval_db()

    conn = sqlite3.connect(EVAL_DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO evalueringer
        (dato, aktivitet_id, klassifisering, volum_avvik_pct, pace_avvik_sek, observasjoner, raad, detaljer)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        evaluering.dato,
        evaluering.aktivitet_id,
        evaluering.klassifisering,
        evaluering.volum_avvik_pct,
        evaluering.pace_avvik_sek,
        json.dumps(evaluering.observasjoner, ensure_ascii=False),
        json.dumps(evaluering.raad, ensure_ascii=False),
        json.dumps(evaluering.detaljer, ensure_ascii=False)
    ))

    conn.commit()
    conn.close()


def sync_data():
    """Syncer data fra Garmin/Strava."""
    import subprocess

    console.print("[dim]Syncer data fra Garmin...[/dim]")
    try:
        result = subprocess.run(
            [str(PROJECT_ROOT / ".venv" / "bin" / "python"),
             str(PROJECT_ROOT / "scripts" / "fetch_garmin.py"),
             "--days", "3"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT
        )
        if result.returncode == 0:
            console.print("[dim]✓ Sync fullført[/dim]")
        else:
            console.print(f"[yellow]Sync feilet: {result.stderr[:100]}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Kunne ikke synce: {e}[/yellow]")


def vis_evaluering(dato: str = None, skip_sync: bool = False):
    """Hovedfunksjon som viser økt-evaluering."""

    console.print(f"\n[bold]🏃 ØKT-EVALUERING[/bold]\n")

    # Sync først
    if not skip_sync:
        sync_data()
        console.print()

    # Hent data
    faktisk = hent_siste_okt(dato)

    if not faktisk:
        console.print("[red]Ingen aktivitet funnet å evaluere[/red]")
        return

    planlagt = hent_planlagt_okt(faktisk.dato)

    # Klassifiser
    klassifisering, volum_avvik, pace_avvik = klassifiser_okt(faktisk, planlagt)

    # Vis faktisk økt
    console.print(f"[bold]Aktivitet:[/bold] {faktisk.navn}")
    console.print(f"[bold]Dato:[/bold] {faktisk.dato}")
    console.print(f"[bold]Distanse:[/bold] {faktisk.distanse_km:.1f} km")
    console.print(f"[bold]Tid:[/bold] {faktisk.tid_s // 60}:{faktisk.tid_s % 60:02d}")
    console.print(f"[bold]Snitt-pace:[/bold] {format_pace(faktisk.snitt_pace_s)}")
    if faktisk.maks_hr > 0:
        console.print(f"[bold]HR:[/bold] {faktisk.maks_hr} bpm")

    # Vis planlagt vs faktisk
    console.print()
    if planlagt:
        table = Table(title="Planlagt vs Faktisk", show_header=True)
        table.add_column("", style="cyan")
        table.add_column("Planlagt", style="dim")
        table.add_column("Faktisk", style="white")
        table.add_column("Avvik", style="yellow")

        # Distanse
        dist_diff = f"{volum_avvik:+.0f}%" if planlagt.distanse_km > 0 else "-"
        table.add_row(
            "Distanse",
            f"{planlagt.distanse_km} km",
            f"{faktisk.distanse_km:.1f} km",
            dist_diff
        )

        # Pace
        if planlagt.pace_range:
            pace_diff = f"{pace_avvik:+.0f} sek" if abs(pace_avvik) > 0 else "✓"
            table.add_row(
                "Pace",
                planlagt.pace_range,
                format_pace(faktisk.snitt_pace_s),
                pace_diff
            )

        # Økttype
        table.add_row("Type", planlagt.type, faktisk.navn, "")

        console.print(table)
    else:
        console.print("[yellow]⚠️ Ingen planlagt økt funnet for denne datoen[/yellow]")

    # Klassifisering
    console.print()
    if 'Truffet' in klassifisering:
        console.print(f"[green bold]{klassifisering}[/green bold]")
    elif 'Lett' in klassifisering:
        console.print(f"[yellow bold]{klassifisering}[/yellow bold]")
    else:
        console.print(f"[red bold]{klassifisering}[/red bold]")

    # Analyser splits
    observasjoner = analyser_splits(faktisk.splits, planlagt)

    # Vis observasjoner
    if observasjoner:
        console.print("\n[bold]📊 Observasjoner:[/bold]")
        for obs in observasjoner:
            console.print(f"  {obs}")

    # Generer og vis råd
    raad = generer_raad(faktisk, planlagt, klassifisering, observasjoner)

    console.print("\n[bold]💡 Råd til neste økt:[/bold]")
    for r in raad:
        console.print(f"  • {r}")

    # Lagre evaluering
    evaluering = Evaluering(
        dato=faktisk.dato,
        aktivitet_id=faktisk.id,
        klassifisering=klassifisering,
        volum_avvik_pct=volum_avvik,
        pace_avvik_sek=pace_avvik,
        observasjoner=observasjoner,
        raad=raad,
        detaljer={
            'faktisk_distanse': faktisk.distanse_km,
            'faktisk_pace': faktisk.snitt_pace_s,
            'faktisk_hr': faktisk.snitt_hr,
            'planlagt_type': planlagt.type if planlagt else None,
            'planlagt_distanse': planlagt.distanse_km if planlagt else None
        }
    )

    lagre_evaluering(evaluering)
    console.print(f"\n[dim]Evaluering lagret til {EVAL_DB_PATH.name}[/dim]")

    # Oppdater planen med gjennomført økt
    oppdater_plan_med_faktisk(faktisk)

    # Generer treningsbrief for neste dag
    try:
        from morgen_briefing import lagre_briefing
        aktivitet_dato = dato if dato else faktisk.dato
        neste_dag_dt = datetime.strptime(aktivitet_dato, '%Y-%m-%d') + timedelta(days=1)
        neste_dag = neste_dag_dt.strftime('%Y-%m-%d')
        neste_dag_lesbar = neste_dag_dt.strftime('%-d. %B %Y').replace('January', 'januar').replace('February', 'februar').replace('March', 'mars').replace('April', 'april').replace('May', 'mai').replace('June', 'juni').replace('July', 'juli').replace('August', 'august').replace('September', 'september').replace('October', 'oktober').replace('November', 'november').replace('December', 'desember')
        lagre_briefing(neste_dag)
        console.print(f"\n[green]📄 Treningsbrief generert for {neste_dag_lesbar}[/green]")
    except Exception as e:
        console.print(f"\n[yellow]Kunne ikke generere treningsbrief: {e}[/yellow]")

    # Generer ukesbrief på søndager
    aktivitet_dato = dato if dato else faktisk.dato
    dato_dt = datetime.strptime(aktivitet_dato, '%Y-%m-%d')
    if dato_dt.weekday() == 6:  # Søndag
        try:
            from ukesbrief import lagre_ukesbrief
            uke_nr = dato_dt.isocalendar()[1]
            lagre_ukesbrief(dato)
            console.print(f"[green]📊 Ukesbrief generert for uke {uke_nr}[/green]")
        except Exception as e:
            console.print(f"[yellow]Kunne ikke generere ukesbrief: {e}[/yellow]")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Evaluér siste økt')
    parser.add_argument('--dato', type=str, help='Dato å evaluere (YYYY-MM-DD)')
    parser.add_argument('--no-sync', action='store_true', help='Hopp over sync')
    args = parser.parse_args()

    vis_evaluering(args.dato, skip_sync=args.no_sync)


if __name__ == '__main__':
    main()
