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
    """Parser planlagt økt fra current_plan.md."""
    if not PLAN_PATH.exists():
        return None

    with open(PLAN_PATH, 'r') as f:
        content = f.read()

    # Parse dato
    try:
        dt = datetime.strptime(dato, '%Y-%m-%d')
        dag_maned = dt.strftime('%d.%m')
        ukedag = ['Mandag', 'Tirsdag', 'Onsdag', 'Torsdag', 'Fredag', 'Lørdag', 'Søndag'][dt.weekday()]
    except:
        return None

    # Finn økt-header
    pattern = rf'###\s+{ukedag}\s+{dag_maned}\s+[–-]\s+(.+?)\s*[🔴🟢🟡⚪]'
    match = re.search(pattern, content)

    if not match:
        return None

    okt_type = match.group(1).strip()

    # Finn seksjonen
    start_idx = match.start()
    next_header = re.search(r'\n###\s+', content[start_idx + 10:])
    end_idx = start_idx + 10 + next_header.start() if next_header else len(content)
    okt_section = content[start_idx:end_idx]

    # Parse distanse
    dist_match = re.search(r'\*\*Distanse\*\*\s*\|\s*(\d+(?:\.\d+)?)\s*km', okt_section)
    distanse = float(dist_match.group(1)) if dist_match else 0

    # Parse pace
    pace_match = re.search(r'\*\*Pace\*\*\s*\|\s*([\d:]+(?:-[\d:]+)?/km)', okt_section)
    pace_range = pace_match.group(1) if pace_match else ''

    # Parse terskelarbeid
    terskel_match = re.search(r'\*\*Terskelarbeid\*\*\s*\|\s*(\d+)\s*min', okt_section)
    terskelarbeid = int(terskel_match.group(1)) if terskel_match else 0

    # Parse pace til sekunder
    if not pace_range and 'terskel 1' in okt_type.lower():
        pace_min_s, pace_max_s = PACE_ZONES['terskel_1']
    elif not pace_range and 'terskel 2' in okt_type.lower():
        pace_min_s, pace_max_s = PACE_ZONES['terskel_2']
    elif not pace_range and any(x in okt_type.lower() for x in ['rolig', 'restitusjon', 'lang tur']):
        pace_min_s, pace_max_s = PACE_ZONES['rolig_sone2']
    else:
        pace_min_s, pace_max_s = parse_pace_til_sekunder(pace_range)

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


def vis_evaluering(dato: str = None):
    """Hovedfunksjon som viser økt-evaluering."""

    console.print(f"\n[bold]🏃 ØKT-EVALUERING[/bold]\n")

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
    args = parser.parse_args()

    vis_evaluering(args.dato)


if __name__ == '__main__':
    main()
