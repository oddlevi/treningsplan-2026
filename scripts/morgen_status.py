#!/usr/bin/env python3
"""
Daglig morgensjekk før trening.

Henter HRV, Training Readiness, søvn og hvile-HR fra Garmin,
sammenligner med baseline, og gir konkret treningsanbefaling.

Bruk:
    python scripts/morgen_status.py
    python scripts/morgen_status.py --dato 2026-05-13
"""

import sqlite3
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Prosjektstier
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
PLAN_PATH = PROJECT_ROOT / "plan" / "current_plan.md"

console = Console()


@dataclass
class DagensMetrikker:
    """Dagens helsemetrikker."""
    dato: str
    hrv_value: float
    hrv_status: str
    training_readiness: int
    sleep_score: int
    sleep_hours: float
    resting_hr: int


@dataclass
class Baseline:
    """Baseline-verdier for sammenligning."""
    hrv_7d: float
    hrv_28d: float
    readiness_7d: float
    sleep_7d: float
    resting_hr_7d: float


@dataclass
class VolumeStats:
    """Volumstatistikk."""
    km_7d: float
    km_28d: float
    acwr: float  # Volum-basert (legacy)
    acute_load: float  # Garmin acute load (7d vektet)
    chronic_load: float  # Garmin chronic load (28d vektet)
    load_acwr: float  # Load-basert ACWR (mer presis)


@dataclass
class PlanlagtOkt:
    """Planlagt økt for dagen."""
    dag: str
    dato: str
    type: str
    distanse_km: float
    pace: str
    terskelarbeid_min: int
    beskrivelse: str


def hent_dagens_metrikker(dato: str) -> Optional[DagensMetrikker]:
    """Henter dagens metrikker fra databasen."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            date,
            hrv_weekly_avg,
            hrv_status,
            training_readiness,
            sleep_score,
            sleep_hours,
            resting_hr
        FROM daily_metrics
        WHERE date = ?
    """, (dato,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return DagensMetrikker(
        dato=row[0],
        hrv_value=row[1] or 0,
        hrv_status=row[2] or 'UNKNOWN',
        training_readiness=row[3] or 0,
        sleep_score=row[4] or 0,
        sleep_hours=row[5] or 0,
        resting_hr=row[6] or 0
    )


def hent_baseline(dato: str) -> Baseline:
    """Beregner 7-dagers og 28-dagers baseline."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 7-dagers baseline
    cursor.execute("""
        SELECT
            AVG(hrv_weekly_avg),
            AVG(training_readiness),
            AVG(sleep_score),
            AVG(resting_hr)
        FROM daily_metrics
        WHERE date >= date(?, '-7 days') AND date < ?
    """, (dato, dato))
    row_7d = cursor.fetchone()

    # 28-dagers baseline
    cursor.execute("""
        SELECT AVG(hrv_weekly_avg)
        FROM daily_metrics
        WHERE date >= date(?, '-28 days') AND date < ?
    """, (dato, dato))
    row_28d = cursor.fetchone()

    conn.close()

    return Baseline(
        hrv_7d=row_7d[0] or 0,
        hrv_28d=row_28d[0] or 0,
        readiness_7d=row_7d[1] or 0,
        sleep_7d=row_7d[2] or 0,
        resting_hr_7d=row_7d[3] or 0
    )


def hent_hrv_historie(dato: str, dager: int = 3) -> List[Tuple[str, str]]:
    """Henter HRV-status for de siste N dagene."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT date, hrv_status
        FROM daily_metrics
        WHERE date >= date(?, ?) AND date <= ?
        ORDER BY date DESC
    """, (dato, f'-{dager} days', dato))

    rows = cursor.fetchall()
    conn.close()

    return [(row[0], row[1] or 'UNKNOWN') for row in rows]


def hent_volume_stats(dato: str) -> VolumeStats:
    """Beregner volumstatistikk og ACWR (både volum- og load-basert)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Siste 7 dager - km
    cursor.execute("""
        SELECT COALESCE(SUM(distance_km), 0)
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= date(?, '-7 days')
        AND date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) <= ?
        AND sport IN ('running', 'Run', 'trail_running')
    """, (dato, dato))
    km_7d = cursor.fetchone()[0] or 0

    # Siste 28 dager - km
    cursor.execute("""
        SELECT COALESCE(SUM(distance_km), 0)
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= date(?, '-28 days')
        AND date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) <= ?
        AND sport IN ('running', 'Run', 'trail_running')
    """, (dato, dato))
    km_28d = cursor.fetchone()[0] or 0

    # Garmin Training Load - siste 7 dager (akutt)
    cursor.execute("""
        SELECT COALESCE(SUM(training_load), 0)
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= date(?, '-7 days')
        AND date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) <= ?
        AND sport IN ('running', 'Run', 'trail_running')
    """, (dato, dato))
    acute_load = cursor.fetchone()[0] or 0

    # Garmin Training Load - siste 28 dager (kronisk)
    cursor.execute("""
        SELECT COALESCE(SUM(training_load), 0)
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= date(?, '-28 days')
        AND date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) <= ?
        AND sport IN ('running', 'Run', 'trail_running')
    """, (dato, dato))
    chronic_load_total = cursor.fetchone()[0] or 0

    conn.close()

    # Volum-ACWR (legacy)
    kronisk_per_uke = km_28d / 4 if km_28d > 0 else 1
    acwr = km_7d / kronisk_per_uke if kronisk_per_uke > 0 else 0

    # Load-ACWR (Garmin-basert)
    chronic_load_per_week = chronic_load_total / 4 if chronic_load_total > 0 else 1
    load_acwr = acute_load / chronic_load_per_week if chronic_load_per_week > 0 else 0

    return VolumeStats(
        km_7d=km_7d,
        km_28d=km_28d,
        acwr=round(acwr, 2),
        acute_load=round(acute_load, 0),
        chronic_load=round(chronic_load_total, 0),
        load_acwr=round(load_acwr, 2)
    )


def parse_planlagt_okt(dato: str) -> Optional[PlanlagtOkt]:
    """Parser dagens planlagte økt fra current_plan.md.

    Støtter tabellformat:
    | Dag DD.MM | Økttype 🔴 | | | *Km* | *Detaljer* | 2024 |
    """
    if not PLAN_PATH.exists():
        return None

    with open(PLAN_PATH, 'r') as f:
        content = f.read()

    # Parse dato til dag.måned format
    try:
        dt = datetime.strptime(dato, '%Y-%m-%d')
        dag_maned = dt.strftime('%d.%m')
        ukedag = ['Mandag', 'Tirsdag', 'Onsdag', 'Torsdag', 'Fredag', 'Lørdag', 'Søndag'][dt.weekday()]
        ukedag_kort = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn'][dt.weekday()]
    except:
        return None

    # Søk etter tabellrad med denne datoen
    # Format: | Man 25.05 | Hvile ⚪ | | | *-* | *-* | ... |
    # eller: | Tir 26.05 | Terskel 1 🔴 | | | *14* | *6×6 min @ 3:50-4:05/km* | ... |
    table_pattern = rf'\|\s*(?:✅\s*|⏭️\s*)?{ukedag_kort}\s+{dag_maned}\s*\|([^|]+)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|'
    table_match = re.search(table_pattern, content)

    if table_match:
        # Parse økttype fra andre kolonne (f.eks. "Terskel 1 🔴")
        okt_kolonne = table_match.group(1).strip()
        # Fjern emoji og hent økttype
        okt_type = re.sub(r'\s*[🔴🟢🟡⚪]\s*$', '', okt_kolonne).strip()

        # Parse distanse fra femte kolonne (*14* eller *-*)
        dist_kolonne = table_match.group(4).strip()
        dist_match = re.search(r'\*(\d+(?:\.\d+)?)\*', dist_kolonne)
        distanse = float(dist_match.group(1)) if dist_match else 0

        # Parse detaljer fra sjette kolonne (*6×6 min @ 3:50-4:05/km (36 min)*)
        detaljer_kolonne = table_match.group(5).strip()
        beskrivelse = re.sub(r'^\*|\*$', '', detaljer_kolonne).strip()

        # Parse pace fra detaljer (f.eks. "3:50-4:05/km")
        pace_match = re.search(r'(\d+:\d+(?:-\d+:\d+)?)/km', beskrivelse)
        pace = pace_match.group(1) + '/km' if pace_match else ''

        # Parse terskelarbeid fra detaljer (f.eks. "(36 min)")
        terskel_match = re.search(r'\((\d+)\s*min\)', beskrivelse)
        terskelarbeid = int(terskel_match.group(1)) if terskel_match else 0

        return PlanlagtOkt(
            dag=ukedag,
            dato=dato,
            type=okt_type,
            distanse_km=distanse,
            pace=pace,
            terskelarbeid_min=terskelarbeid,
            beskrivelse=beskrivelse
        )

    # Fallback: prøv gammelt header-format for bakoverkompatibilitet
    # Format: ### Dag DD.MM – Økttype 🔴/🟢/⚪
    pattern = rf'###\s+{ukedag}\s+{dag_maned}\s+[–-]\s+(.+?)\s*[🔴🟢🟡⚪]'
    match = re.search(pattern, content)

    if not match:
        return None

    okt_type = match.group(1).strip()

    # Finn seksjonen for denne økten
    start_idx = match.start()
    next_header = re.search(r'\n###\s+', content[start_idx + 10:])
    end_idx = start_idx + 10 + next_header.start() if next_header else len(content)
    okt_section = content[start_idx:end_idx]

    # Parse distanse
    dist_match = re.search(r'\*\*Distanse\*\*\s*\|\s*(\d+(?:\.\d+)?)\s*km', okt_section)
    distanse = float(dist_match.group(1)) if dist_match else 0

    # Parse pace
    pace_match = re.search(r'\*\*Pace\*\*\s*\|\s*([\d:]+(?:-[\d:]+)?/km)', okt_section)
    pace = pace_match.group(1) if pace_match else ''

    # Parse terskelarbeid
    terskel_match = re.search(r'\*\*Terskelarbeid\*\*\s*\|\s*(\d+)\s*min', okt_section)
    terskelarbeid = int(terskel_match.group(1)) if terskel_match else 0

    # Hent beskrivelse (første linje etter tabellen)
    besk_match = re.search(r'\*\*Beskrivelse:\*\*\s*\n(.+)', okt_section)
    beskrivelse = besk_match.group(1).strip() if besk_match else ''

    return PlanlagtOkt(
        dag=ukedag,
        dato=dato,
        type=okt_type,
        distanse_km=distanse,
        pace=pace,
        terskelarbeid_min=terskelarbeid,
        beskrivelse=beskrivelse
    )


def er_hard_okt(okt: PlanlagtOkt) -> bool:
    """Sjekker om økten er en hard økt (terskel, intervall, etc)."""
    harde_typer = ['terskel', 'intervall', 'tempo', 'fartlek', 'vo2']
    return any(t in okt.type.lower() for t in harde_typer)


def generer_anbefaling(
    metrikker: Optional[DagensMetrikker],
    baseline: Baseline,
    volume: VolumeStats,
    okt: Optional[PlanlagtOkt],
    hrv_historie: List[Tuple[str, str]]
) -> tuple[str, str, str]:
    """
    Genererer treningsanbefaling basert på data.

    Returnerer (signal, anbefaling, begrunnelse)
    """

    # Hvis ingen data
    if not metrikker:
        return ('GULT',
                'Ingen data for i dag – kjør etter følelse',
                'Mangler dagens metrikker fra Garmin')

    # Hvis ingen planlagt økt
    if not okt:
        return ('GRØNT',
                'Ingen planlagt økt i dag – hviledag eller valgfri aktivitet',
                '')

    # Hvis hviledag
    if 'hvile' in okt.type.lower():
        return ('GRØNT',
                'Hviledag som planlagt – nyt hvilen!',
                '')

    # Hent nøkkelverdier
    readiness = metrikker.training_readiness
    sleep_hours = metrikker.sleep_hours
    hrv_status = metrikker.hrv_status.upper()
    acwr = volume.load_acwr  # Bruker load-basert ACWR (Garmin)
    hard_okt = er_hard_okt(okt)

    # Tell HRV unbalanced/low de siste 3 dagene
    dårlig_hrv_dager = sum(1 for _, status in hrv_historie
                           if status.upper() in ['LOW', 'POOR', 'UNBALANCED'])

    # =========================================================================
    # JUSTERINGSREGLER (fra plan/current_plan.md)
    # Basert på personlig Readiness-fordeling (snitt ~29)
    # =========================================================================

    # UNNTAK: Bytt til rolig ved ekstremt lave verdier
    if hard_okt and (readiness < 10 or sleep_hours < 4):
        grunner = []
        if readiness < 10:
            grunner.append(f'Readiness {readiness}')
        if sleep_hours < 4:
            grunner.append(f'søvn {sleep_hours:.1f}t')
        return ('RØDT',
                'Bytt til rolig 8 km @ 5:15-5:30/km',
                f'{" + ".join(grunner)} – kroppen trenger hvile.')

    # RØDT: ACWR > 1.5 (alltid høyeste prioritet - skaderisiko)
    if acwr > 1.5:
        return ('RØDT',
                'Høy skaderisiko – senk til Terskel 1-pace (4:00-4:10 / maks HR <170)',
                f'ACWR {acwr} er over 1,5. Du har økt belastningen for raskt.')

    # RØDT: Readiness <15 ELLER søvn <4.5t (på hard økt)
    if hard_okt and (readiness < 15 or sleep_hours < 4.5):
        grunner = []
        if readiness < 15:
            grunner.append(f'Readiness {readiness}')
        if sleep_hours < 4.5:
            grunner.append(f'søvn {sleep_hours:.1f}t')

        return ('RØDT',
                'Senk til Terskel 1-pace (4:00-4:10 / maks HR <170)',
                f'{" + ".join(grunner)} – kjør økten, men lavere intensitet.')

    # RØDT: HRV dårlig 2+ dager + hard økt
    if dårlig_hrv_dager >= 2 and hard_okt:
        return ('RØDT',
                'Senk til Terskel 1-pace (4:00-4:10 / maks HR <170)',
                f'HRV har vært {hrv_status.lower()} i {dårlig_hrv_dager} dager.')

    # GULT: Readiness 15-35 ELLER søvn 4.5-6t (på hard økt)
    if hard_okt and (15 <= readiness <= 35 or 4.5 <= sleep_hours <= 6):
        grunner = []
        if 15 <= readiness <= 35:
            grunner.append(f'Readiness {readiness}')
        if 4.5 <= sleep_hours <= 6:
            grunner.append(f'søvn {sleep_hours:.1f}t')

        # Spesifikke justeringer basert på økttype
        if 'terskel 1' in okt.type.lower() or 'lange drag' in okt.type.lower():
            justering = 'Kutt 1 drag (f.eks. 4×6 → 3×6 min)'
        elif 'terskel 2' in okt.type.lower() or 'korte drag' in okt.type.lower():
            justering = 'Kutt 2 drag (f.eks. 8×3 → 6×3 min)'
        elif 'langtur' in okt.type.lower() or 'lang' in okt.type.lower():
            justering = 'Hold sone 2 strengt (HR <135)'
        else:
            justering = 'Kutt 1-2 drag, hold pace'

        return ('GULT',
                f'Kjør {okt.type} med justering: {justering}',
                f'{" + ".join(grunner)} – spar litt til neste økt.')

    # GULT: ACWR > 1.3
    if acwr > 1.3:
        justering = 'Redusér drag/intensitet 20-30%' if hard_okt else 'Kjør litt kortere'
        return ('GULT',
                f'Kjør planlagt økt med justering: {justering}',
                f'ACWR {acwr} nærmer seg risikosonen (>1,3).')

    # GULT: HRV unbalanced 1 dag + hard økt
    if dårlig_hrv_dager == 1 and hard_okt:
        return ('GULT',
                f'Kjør {okt.type} med 20% lettere intensitet',
                f'HRV var {hrv_status.lower()} i går – lytt til kroppen.')

    # GRØNT: Readiness >35 OG søvn >6t
    if readiness > 35 and sleep_hours > 6:
        return ('GRØNT',
                f'Kjør planlagt økt som spesifisert',
                f'Readiness {readiness}, søvn {sleep_hours:.1f}t – kroppen er klar!')

    # GRØNT: Default for rolige økter
    if not hard_okt:
        return ('GRØNT',
                f'Kjør {okt.type} som planlagt',
                f'Rolig økt – ingen spesiell justering nødvendig.')

    # GRØNT: Ingen varselstegn, men ikke optimal
    if hrv_status == 'BALANCED' and readiness >= 35:
        return ('GRØNT',
                f'Kjør planlagt økt',
                f'Readiness {readiness}, HRV balanced. God dag for trening.')

    # Default: GULT med forsiktighet
    return ('GULT',
            f'Kjør {okt.type}, men vær oppmerksom på signalene',
            f'Readiness {readiness}, søvn {sleep_hours:.1f}t – ikke optimalt.')


def vis_morgen_status(dato: str):
    """Hovedfunksjon som viser morgenstatus."""

    console.print(f"\n[bold]☀️  MORGEN STATUS – {dato}[/bold]\n")

    # Hent alle data
    metrikker = hent_dagens_metrikker(dato)
    baseline = hent_baseline(dato)
    volume = hent_volume_stats(dato)
    okt = parse_planlagt_okt(dato)
    hrv_historie = hent_hrv_historie(dato, dager=3)

    # Vis dagens metrikker
    if metrikker:
        table = Table(title="Dagens metrikker", show_header=True)
        table.add_column("Metrikk", style="cyan")
        table.add_column("Verdi", style="white")
        table.add_column("vs 7d snitt", style="dim")

        # HRV
        hrv_diff = ((metrikker.hrv_value - baseline.hrv_7d) / baseline.hrv_7d * 100) if baseline.hrv_7d > 0 else 0
        hrv_color = "green" if hrv_diff >= 0 else "red"
        table.add_row(
            "HRV",
            f"{metrikker.hrv_value:.0f} ({metrikker.hrv_status})",
            f"[{hrv_color}]{hrv_diff:+.0f}%[/{hrv_color}]"
        )

        # Training Readiness
        ready_diff = metrikker.training_readiness - baseline.readiness_7d
        ready_color = "green" if ready_diff >= 0 else "red"
        table.add_row(
            "Training Readiness",
            f"{metrikker.training_readiness}",
            f"[{ready_color}]{ready_diff:+.0f}[/{ready_color}]"
        )

        # Søvn-timer (viktigst for justeringsregler)
        if metrikker.sleep_hours > 0:
            sleep_color = "green" if metrikker.sleep_hours >= 6 else ("yellow" if metrikker.sleep_hours >= 4.5 else "red")
            table.add_row(
                "Søvn",
                f"[{sleep_color}]{metrikker.sleep_hours:.1f}t[/{sleep_color}]",
                f"[dim]{'✓ OK' if metrikker.sleep_hours >= 6 else '⚠️ Lite' if metrikker.sleep_hours >= 4.5 else '🔴 For lite'}[/dim]"
            )

        # Søvn-score
        if metrikker.sleep_score > 0:
            sleep_diff = metrikker.sleep_score - baseline.sleep_7d
            sleep_color = "green" if sleep_diff >= 0 else "red"
            table.add_row(
                "Søvn-score",
                f"{metrikker.sleep_score}",
                f"[{sleep_color}]{sleep_diff:+.0f}[/{sleep_color}]"
            )

        # Hvile-HR
        if metrikker.resting_hr > 0:
            hr_diff = metrikker.resting_hr - baseline.resting_hr_7d
            hr_color = "green" if hr_diff <= 0 else "red"  # Lavere er bedre
            table.add_row(
                "Hvile-HR",
                f"{metrikker.resting_hr} bpm",
                f"[{hr_color}]{hr_diff:+.0f}[/{hr_color}]"
            )

        console.print(table)
    else:
        console.print("[yellow]⚠️ Ingen metrikker funnet for i dag[/yellow]")

    # Vis volumstatistikk
    console.print(f"\n[bold]📊 Volum:[/bold] {volume.km_7d:.1f} km siste 7 dager / {volume.km_28d:.1f} km siste 28 dager")

    # Vis Load-basert ACWR (Garmin)
    load_acwr = volume.load_acwr
    acwr_color = "green" if load_acwr < 1.0 else ("yellow" if load_acwr < 1.3 else "red")
    console.print(f"[bold]⚖️  ACWR:[/bold] [{acwr_color}]{load_acwr}[/{acwr_color}] (load-basert)", end="")
    console.print(f"  [dim]Akutt: {volume.acute_load:.0f} / Kronisk: {volume.chronic_load/4:.0f} per uke[/dim]")

    if load_acwr < 0.8:
        console.print("   → Lav belastning (risiko for detrain)")
    elif load_acwr < 1.0:
        console.print("   → Vedlikehold")
    elif load_acwr < 1.3:
        console.print("   → God progresjon ✓")
    elif load_acwr < 1.5:
        console.print("   → Høy progresjon ⚠️")
    else:
        console.print("   → Skaderisiko! 🔴")

    # Vis planlagt økt
    console.print()
    if okt:
        console.print(f"[bold]📋 Planlagt økt:[/bold] {okt.type}")
        if okt.distanse_km > 0:
            console.print(f"   Distanse: {okt.distanse_km} km")
        if okt.pace:
            console.print(f"   Pace: {okt.pace}")
        if okt.terskelarbeid_min > 0:
            console.print(f"   Terskelarbeid: {okt.terskelarbeid_min} min")
    else:
        console.print("[dim]Ingen planlagt økt funnet for i dag[/dim]")

    # Generer og vis anbefaling
    signal, anbefaling, begrunnelse = generer_anbefaling(
        metrikker, baseline, volume, okt, hrv_historie
    )

    console.print()

    if signal == 'GRØNT':
        panel_style = "green"
        emoji = "✅"
    elif signal == 'GULT':
        panel_style = "yellow"
        emoji = "⚠️"
    else:
        panel_style = "red"
        emoji = "🛑"

    panel_content = f"{emoji} {anbefaling}"
    if begrunnelse:
        panel_content += f"\n\n[dim]{begrunnelse}[/dim]"

    console.print(Panel(
        panel_content,
        title=f"[bold]ANBEFALING: {signal}[/bold]",
        border_style=panel_style,
        padding=(1, 2)
    ))

    # 3-dagers regel: sjekk om vi har hatt 3 dager på rad med lav Readiness/søvn
    if metrikker:
        sjekk_3_dagers_regel(dato)


def sjekk_3_dagers_regel(dato: str):
    """Sjekker om siste 3 dager har hatt dårlige signaler."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Hent siste 3 dagers metrikker
    cursor.execute("""
        SELECT date, training_readiness, sleep_hours
        FROM daily_metrics
        WHERE date <= ?
        ORDER BY date DESC
        LIMIT 3
    """, (dato,))
    rows = cursor.fetchall()
    conn.close()

    if len(rows) < 3:
        return

    # Tell dager med RØDT signal (kun RØDT teller for 3-dagers regel)
    røde_dager = 0
    for row in rows:
        readiness = row[1] or 0
        sleep = row[2] or 0
        # RØDT: readiness <15 eller søvn <4.5
        if readiness < 15 or (sleep > 0 and sleep < 4.5):
            røde_dager += 1

    if røde_dager >= 3:
        console.print()
        console.print(Panel(
            "[bold red]⚠️ 3-DAGERS REGEL UTLØST[/bold red]\n\n"
            "Siste 3 dager har gitt rødt signal (Readiness <15 eller søvn <4.5t).\n"
            "Vurder en ekstra hviledag.\n\n"
            "[bold]Anbefaling:[/bold] Kjør plan/juster.py for automatisk justering\n"
            "[dim]python3 plan/juster.py[/dim]",
            border_style="red",
            title="[bold]VURDER EKSTRA HVILEDAG[/bold]"
        ))


def sync_garmin(quiet: bool = False):
    """Syncer fra Garmin før analyse."""
    import subprocess

    if not quiet:
        console.print("[bold]Syncer fra Garmin...[/bold]\n")

    fetch_script = PROJECT_ROOT / "scripts" / "fetch_garmin.py"
    result = subprocess.run(
        [sys.executable, str(fetch_script), "--days", "2"],
        cwd=PROJECT_ROOT,
        capture_output=quiet
    )

    if result.returncode != 0 and not quiet:
        console.print("[yellow]⚠️ Garmin-sync feilet. Bruker cached data.[/yellow]\n")
    elif not quiet:
        console.print()


def vis_telegram_status(dato: str):
    """Kompakt output for Telegram - kun nøkkeltall, ingen daglige justeringer."""
    metrikker = hent_dagens_metrikker(dato)
    volume = hent_volume_stats(dato)

    lines = [f"☀️ MORGEN {dato}"]

    if metrikker:
        # Nøkkeltall på én linje
        hrv_str = f"HRV {metrikker.hrv_value:.0f}"
        ready_str = f"Ready {metrikker.training_readiness}"
        sleep_str = f"Søvn {metrikker.sleep_hours:.1f}t" if metrikker.sleep_hours > 0 else ""

        metrics = [hrv_str, ready_str]
        if sleep_str:
            metrics.append(sleep_str)
        lines.append(" | ".join(metrics))

        # ACWR alltid
        lines.append(f"ACWR {volume.load_acwr} (load)")

    print("\n".join(lines))


def generer_briefing(dato: str):
    """Genererer morgenbriefing og returnerer filsti."""
    try:
        from morgen_briefing import lagre_briefing
        md_path, _ = lagre_briefing(dato)
        return md_path
    except Exception as e:
        console.print(f"[yellow]⚠️ Kunne ikke generere briefing: {e}[/yellow]")
        return None


def hent_kommende_okter(dato: str, dager: int = 3) -> List[PlanlagtOkt]:
    """Henter planlagte økter for de neste N dagene."""
    okter = []
    for i in range(dager):
        d = datetime.strptime(dato, '%Y-%m-%d') + timedelta(days=i)
        okt = parse_planlagt_okt(d.strftime('%Y-%m-%d'))
        if okt:
            okter.append(okt)
    return okter


def generer_kosthold_tips(okter: List[PlanlagtOkt]) -> str:
    """Genererer kort kostholdsanbefaling basert på kommende økter."""
    if not okter:
        return "Spis variert og drikk nok vann."

    # Sjekk om det er race/konkurranse
    for okt in okter:
        if 'race' in okt.type.lower() or 'konkurranse' in okt.type.lower() or '10k' in okt.type.lower() or 'halvmaraton' in okt.type.lower():
            return "Karbo-fokus i dag! Pasta, ris, brød. Drikk mye vann. Sportsdrikk før sengetid."

    # Sjekk om det er hard økt i dag eller i morgen
    harde_typer = ['terskel', 'intervall', 'tempo', 'vo2', 'fartlek']
    hard_i_dag = okter[0] if okter and any(t in okter[0].type.lower() for t in harde_typer) else None
    hard_i_morgen = okter[1] if len(okter) > 1 and any(t in okter[1].type.lower() for t in harde_typer) else None

    if hard_i_dag:
        return "Karbohydrater til frokost/lunsj før økta. Protein + karbo etter. Drikk godt."
    elif hard_i_morgen:
        return "Fyll opp karbo-lagrene i dag. Pasta/ris til middag. God søvn prioriteres."
    elif okter[0] and 'lang' in okter[0].type.lower():
        return "Langtur i dag – lett frokost, ta med energi på tur. Restituér godt etterpå."
    elif okter[0] and 'hvile' in okter[0].type.lower():
        return "Hviledag – spis normalt, fokuser på restitusjon og hydrering."
    else:
        return "Normal dag – balansert kosthold, nok protein, hold deg hydrert."


def send_morgen_telegram(dato: str):
    """Sender dagens økt og kostholdsinfo til Telegram."""
    # Legg til project root i path for å importere utils
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    try:
        from utils.telegram import send_message
    except ImportError as e:
        console.print(f"[yellow]⚠️ Kunne ikke importere telegram-modul: {e}[/yellow]")
        return

    metrikker = hent_dagens_metrikker(dato)
    okt = parse_planlagt_okt(dato)
    okter = hent_kommende_okter(dato, dager=3)
    baseline = hent_baseline(dato)
    volume = hent_volume_stats(dato)
    hrv_historie = hent_hrv_historie(dato, dager=3)

    # Generer anbefaling
    signal, anbefaling, _ = generer_anbefaling(metrikker, baseline, volume, okt, hrv_historie)
    signal_emoji = "🟢" if signal == "GRØNT" else ("🟡" if signal == "GULT" else "🔴")

    # Bygg melding
    dt = datetime.strptime(dato, '%Y-%m-%d')
    ukedag = ['Mandag', 'Tirsdag', 'Onsdag', 'Torsdag', 'Fredag', 'Lørdag', 'Søndag'][dt.weekday()]

    lines = [f"☀️ *{ukedag} {dt.strftime('%d.%m')}*"]

    # Status
    if metrikker:
        status_parts = []
        if metrikker.training_readiness > 0:
            status_parts.append(f"Ready {metrikker.training_readiness}")
        if metrikker.sleep_hours > 0:
            status_parts.append(f"Søvn {metrikker.sleep_hours:.1f}t")
        if status_parts:
            lines.append(" | ".join(status_parts))

    lines.append("")

    # Dagens økt
    if okt:
        lines.append(f"*Dagens økt: {okt.type}*")
        if okt.distanse_km > 0:
            lines.append(f"📏 {okt.distanse_km} km")
        if okt.pace:
            lines.append(f"⏱️ {okt.pace}")
        if okt.beskrivelse and okt.beskrivelse != '-':
            lines.append(f"📝 {okt.beskrivelse}")
    else:
        lines.append("*Ingen planlagt økt i dag*")

    lines.append("")
    lines.append(f"{signal_emoji} {anbefaling}")

    # Kostholdsinfo
    lines.append("")
    kosthold = generer_kosthold_tips(okter)
    lines.append(f"🍽️ {kosthold}")

    # Send
    melding = "\n".join(lines)
    if send_message(melding):
        console.print("[green]✓ Sendt til Telegram[/green]")
    else:
        console.print("[yellow]⚠️ Kunne ikke sende til Telegram[/yellow]")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Daglig morgensjekk før trening')
    parser.add_argument('--dato', type=str, help='Dato å sjekke (YYYY-MM-DD)')
    parser.add_argument('--sync', action='store_true', help='Sync fra Garmin først')
    parser.add_argument('--telegram', action='store_true', help='Kompakt output for Telegram')
    parser.add_argument('--no-briefing', action='store_true', help='Ikke generer briefing')
    args = parser.parse_args()

    if args.sync:
        sync_garmin(quiet=args.telegram)

    if args.dato:
        dato = args.dato
    else:
        dato = datetime.now().strftime('%Y-%m-%d')

    if args.telegram:
        vis_telegram_status(dato)
    else:
        vis_morgen_status(dato)

        # Generer briefing automatisk (med mindre --no-briefing)
        if not args.no_briefing:
            briefing_path = generer_briefing(dato)
            if briefing_path:
                console.print(f"\n[bold]📄 Dagens briefing:[/bold] {briefing_path}")
                console.print(f"[dim]   Les med: cat rapport/briefing_siste.md[/dim]")

        # Send til Telegram automatisk
        console.print()
        send_morgen_telegram(dato)


if __name__ == '__main__':
    main()
