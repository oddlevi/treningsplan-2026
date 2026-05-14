#!/usr/bin/env python3
"""
Adaptiv planjustering.

Analyserer faktisk trening vs plan og foreslår justeringer.
Endrer IKKE planen automatisk – venter på brukerbekreftelse.

Bruk:
    python plan/juster.py              # Analyser og foreslå
    python plan/juster.py --oppdater   # Bruk etter godkjenning
"""

import sqlite3
import re
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Confirm

# Prosjektstier
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
PLAN_PATH = PROJECT_ROOT / "current_plan.md"

console = Console()

# Pace-soner (sekunder per km)
PACE_ZONES = {
    'terskel_1': (250, 260),        # 4:10-4:20
    'terskel_2': (240, 250),        # 4:00-4:10
}


@dataclass
class TerskelAnalyse:
    """Analyse av terskeløkter."""
    type: str
    antall_okter: int
    snitt_pace_s: float
    planlagt_pace_min_s: int
    planlagt_pace_max_s: int
    avvik_sek: float


@dataclass
class VolumAnalyse:
    """Analyse av volum."""
    faktisk_km: float
    planlagt_km: float
    avvik_pct: float
    uker_analysert: int


@dataclass
class BelastningAnalyse:
    """Analyse av belastning."""
    acwr: float
    acwr_trend: str  # 'stigende', 'stabil', 'synkende'
    uker_over_terskel: int


@dataclass
class Forslag:
    """Et justeringsforslag."""
    kategori: str
    beskrivelse: str
    begrunnelse: str
    prioritet: int  # 1=høy, 2=middels, 3=lav


def format_pace(sek: float) -> str:
    """Formaterer sekunder til pace-streng."""
    if sek <= 0:
        return '-'
    min_del = int(sek // 60)
    sek_del = int(sek % 60)
    return f"{min_del}:{sek_del:02d}/km"


def analyser_terskel_okter(uker: int = 3) -> list[TerskelAnalyse]:
    """Analyserer terskeløkter de siste N ukene."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    dato_fra = (datetime.now() - timedelta(weeks=uker)).strftime('%Y-%m-%d')

    # Hent alle løpeøkter
    cursor.execute("""
        SELECT
            date(CASE
                WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                ELSE start_date
            END) as activity_date,
            name,
            avg_pace_s_per_km,
            distance_km
        FROM activities
        WHERE activity_date >= ?
        AND sport IN ('running', 'Run', 'trail_running')
        AND avg_pace_s_per_km > 0
        ORDER BY start_date
    """, (dato_fra,))

    aktiviteter = cursor.fetchall()
    conn.close()

    # Klassifiser økter basert på pace
    terskel_1_okter = []
    terskel_2_okter = []

    for dato, navn, pace_s, dist in aktiviteter:
        # Terskel 2 (raskere): 4:00-4:10 (240-250s)
        if 235 <= pace_s <= 255:
            terskel_2_okter.append(pace_s)
        # Terskel 1 (saktere): 4:10-4:20 (250-260s)
        elif 245 <= pace_s <= 270:
            terskel_1_okter.append(pace_s)

    analyser = []

    if terskel_1_okter:
        snitt = sum(terskel_1_okter) / len(terskel_1_okter)
        midt = (PACE_ZONES['terskel_1'][0] + PACE_ZONES['terskel_1'][1]) / 2
        analyser.append(TerskelAnalyse(
            type='Terskel 1',
            antall_okter=len(terskel_1_okter),
            snitt_pace_s=snitt,
            planlagt_pace_min_s=PACE_ZONES['terskel_1'][0],
            planlagt_pace_max_s=PACE_ZONES['terskel_1'][1],
            avvik_sek=snitt - midt
        ))

    if terskel_2_okter:
        snitt = sum(terskel_2_okter) / len(terskel_2_okter)
        midt = (PACE_ZONES['terskel_2'][0] + PACE_ZONES['terskel_2'][1]) / 2
        analyser.append(TerskelAnalyse(
            type='Terskel 2',
            antall_okter=len(terskel_2_okter),
            snitt_pace_s=snitt,
            planlagt_pace_min_s=PACE_ZONES['terskel_2'][0],
            planlagt_pace_max_s=PACE_ZONES['terskel_2'][1],
            avvik_sek=snitt - midt
        ))

    return analyser


def analyser_volum(uker: int = 2) -> VolumAnalyse:
    """Analyserer faktisk vs planlagt volum."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    dato_fra = (datetime.now() - timedelta(weeks=uker)).strftime('%Y-%m-%d')
    dato_til = datetime.now().strftime('%Y-%m-%d')

    # Faktisk volum
    cursor.execute("""
        SELECT COALESCE(SUM(distance_km), 0)
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= ?
        AND date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) <= ?
        AND sport IN ('running', 'Run', 'trail_running')
    """, (dato_fra, dato_til))

    faktisk_km = cursor.fetchone()[0] or 0
    conn.close()

    # Planlagt volum (fra plan)
    # For nå, bruk estimat basert på fase 1A: ~60 km/uke
    planlagt_km = 60 * uker

    avvik_pct = ((faktisk_km - planlagt_km) / planlagt_km * 100) if planlagt_km > 0 else 0

    return VolumAnalyse(
        faktisk_km=faktisk_km,
        planlagt_km=planlagt_km,
        avvik_pct=avvik_pct,
        uker_analysert=uker
    )


def analyser_belastning() -> BelastningAnalyse:
    """Analyserer ACWR og belastningstrend."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Siste 7 dager (akutt)
    cursor.execute("""
        SELECT COALESCE(SUM(distance_km), 0)
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= date('now', '-7 days')
        AND sport IN ('running', 'Run', 'trail_running')
    """)
    akutt = cursor.fetchone()[0] or 0

    # Siste 28 dager (kronisk)
    cursor.execute("""
        SELECT COALESCE(SUM(distance_km), 0)
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= date('now', '-28 days')
        AND sport IN ('running', 'Run', 'trail_running')
    """)
    kronisk = cursor.fetchone()[0] or 0

    # ACWR for forrige uke
    cursor.execute("""
        SELECT COALESCE(SUM(distance_km), 0)
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= date('now', '-14 days')
        AND date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) < date('now', '-7 days')
        AND sport IN ('running', 'Run', 'trail_running')
    """)
    forrige_uke = cursor.fetchone()[0] or 0

    conn.close()

    # ACWR beregning
    kronisk_per_uke = kronisk / 4 if kronisk > 0 else 1
    acwr = akutt / kronisk_per_uke if kronisk_per_uke > 0 else 0

    # Trend
    if akutt > forrige_uke * 1.1:
        trend = 'stigende'
    elif akutt < forrige_uke * 0.9:
        trend = 'synkende'
    else:
        trend = 'stabil'

    # Tell uker over terskel
    uker_over = 0
    if acwr > 1.3:
        uker_over = 1
        # Sjekk forrige uke
        forrige_kronisk = (kronisk - akutt + forrige_uke) / 4
        forrige_acwr = forrige_uke / forrige_kronisk if forrige_kronisk > 0 else 0
        if forrige_acwr > 1.3:
            uker_over = 2

    return BelastningAnalyse(
        acwr=round(acwr, 2),
        acwr_trend=trend,
        uker_over_terskel=uker_over
    )


def generer_forslag(
    terskel: list[TerskelAnalyse],
    volum: VolumAnalyse,
    belastning: BelastningAnalyse
) -> list[Forslag]:
    """Genererer justeringsforslag basert på analyser."""
    forslag = []

    # ACWR-baserte forslag
    if belastning.uker_over_terskel >= 2:
        forslag.append(Forslag(
            kategori='BELASTNING',
            beskrivelse='Sett inn nedtrappingsuke',
            begrunnelse=f'ACWR har vært over 1,3 i {belastning.uker_over_terskel} uker. '
                       f'Skaderisiko øker. Anbefaler 40% reduksjon neste uke.',
            prioritet=1
        ))
    elif belastning.acwr > 1.3:
        forslag.append(Forslag(
            kategori='BELASTNING',
            beskrivelse='Redusér volum 20% neste uke',
            begrunnelse=f'ACWR {belastning.acwr} er over 1,3. Ikke kritisk ennå, '
                       f'men vær forsiktig med ytterligere økning.',
            prioritet=2
        ))

    # Terskel-pace forslag
    for t in terskel:
        if t.avvik_sek < -5:  # Raskere enn plan
            nye_pace_min = int(t.snitt_pace_s - 5)
            nye_pace_max = int(t.snitt_pace_s + 5)
            forslag.append(Forslag(
                kategori='PACE',
                beskrivelse=f'Oppdater {t.type}-pace til {format_pace(nye_pace_min)}-{format_pace(nye_pace_max)}',
                begrunnelse=f'Du har løpt {t.type} {abs(t.avvik_sek):.0f} sek/km raskere enn planen '
                           f'i {t.antall_okter} økter. Formen har forbedret seg.',
                prioritet=2
            ))
        elif t.avvik_sek > 5:  # Saktere enn plan
            forslag.append(Forslag(
                kategori='PACE',
                beskrivelse=f'Vurder lettere {t.type}-økter eller mer restitusjon',
                begrunnelse=f'Du har løpt {t.type} {t.avvik_sek:.0f} sek/km saktere enn planen. '
                           f'Dette kan skyldes akkumulert tretthet eller at pace-målene er for ambisiøse.',
                prioritet=2
            ))

    # Volum-forslag
    if volum.avvik_pct < -10:
        nytt_volum = int(volum.faktisk_km / volum.uker_analysert)
        forslag.append(Forslag(
            kategori='VOLUM',
            beskrivelse=f'Juster planlagt volum til ~{nytt_volum} km/uke',
            begrunnelse=f'Faktisk volum ({volum.faktisk_km:.0f} km) var {abs(volum.avvik_pct):.0f}% '
                       f'under planlagt ({volum.planlagt_km:.0f} km) de siste {volum.uker_analysert} ukene. '
                       f'Det er bedre å justere ned enn å stadig underprestere.',
            prioritet=3
        ))

    # Sorter etter prioritet
    forslag.sort(key=lambda x: x.prioritet)

    return forslag


def vis_analyse():
    """Viser analyse og forslag."""

    console.print(f"\n[bold]📊 ADAPTIV PLANJUSTERING[/bold]\n")

    # Kjør analyser
    terskel = analyser_terskel_okter(uker=3)
    volum = analyser_volum(uker=2)
    belastning = analyser_belastning()

    # Vis terskel-analyse
    if terskel:
        console.print("[bold]Terskel-analyse (siste 3 uker):[/bold]")
        table = Table(show_header=True)
        table.add_column("Type", style="cyan")
        table.add_column("Antall økter")
        table.add_column("Snitt-pace")
        table.add_column("Planlagt")
        table.add_column("Avvik")

        for t in terskel:
            avvik_str = f"{t.avvik_sek:+.0f} sek"
            avvik_color = "green" if t.avvik_sek < 0 else ("yellow" if t.avvik_sek < 10 else "red")
            table.add_row(
                t.type,
                str(t.antall_okter),
                format_pace(t.snitt_pace_s),
                f"{format_pace(t.planlagt_pace_min_s)}-{format_pace(t.planlagt_pace_max_s)}",
                f"[{avvik_color}]{avvik_str}[/{avvik_color}]"
            )

        console.print(table)
    else:
        console.print("[dim]Ingen terskeløkter funnet de siste 3 ukene[/dim]")

    # Vis volum-analyse
    console.print(f"\n[bold]Volum-analyse (siste {volum.uker_analysert} uker):[/bold]")
    console.print(f"  Faktisk: {volum.faktisk_km:.1f} km")
    console.print(f"  Planlagt: ~{volum.planlagt_km:.0f} km")
    avvik_color = "green" if abs(volum.avvik_pct) < 10 else ("yellow" if abs(volum.avvik_pct) < 20 else "red")
    console.print(f"  Avvik: [{avvik_color}]{volum.avvik_pct:+.0f}%[/{avvik_color}]")

    # Vis belastning
    console.print(f"\n[bold]Belastning:[/bold]")
    acwr_color = "green" if belastning.acwr < 1.0 else ("yellow" if belastning.acwr < 1.3 else "red")
    console.print(f"  ACWR: [{acwr_color}]{belastning.acwr}[/{acwr_color}] ({belastning.acwr_trend})")
    if belastning.uker_over_terskel > 0:
        console.print(f"  [red]⚠️ {belastning.uker_over_terskel} uke(r) over ACWR 1,3[/red]")

    # Generer og vis forslag
    forslag = generer_forslag(terskel, volum, belastning)

    console.print()
    if forslag:
        console.print(Panel(
            "\n".join([
                f"[bold]{f.kategori}:[/bold] {f.beskrivelse}\n"
                f"[dim]{f.begrunnelse}[/dim]\n"
                for f in forslag
            ]),
            title="[bold yellow]FORSLAG TIL JUSTERING[/bold yellow]",
            border_style="yellow"
        ))

        console.print("\n[dim]Kjør 'python plan/juster.py --oppdater' etter godkjenning for å oppdatere planen.[/dim]")
    else:
        console.print(Panel(
            "✅ Ingen justeringer nødvendig.\n\n"
            "Faktisk trening matcher planen godt. Fortsett som før!",
            title="[bold green]STATUS[/bold green]",
            border_style="green"
        ))

    return forslag


def oppdater_plan(forslag: list[Forslag]):
    """Oppdaterer planen basert på godkjente forslag."""

    if not forslag:
        console.print("[yellow]Ingen forslag å implementere[/yellow]")
        return

    console.print("\n[bold]Følgende justeringer vil bli implementert:[/bold]\n")
    for f in forslag:
        console.print(f"  • {f.beskrivelse}")

    if not Confirm.ask("\nGodkjenn disse endringene?"):
        console.print("[yellow]Avbrutt[/yellow]")
        return

    # TODO: Implementer faktisk oppdatering av plan/current_plan.md
    # For nå, vis bare instruksjoner

    console.print("\n[bold green]Forslag godkjent![/bold green]")
    console.print("\n[dim]Manuell oppdatering kreves i plan/current_plan.md:[/dim]")

    for f in forslag:
        if f.kategori == 'PACE':
            console.print(f"\n  PACE: Oppdater pace-soner i planen")
            console.print(f"  {f.beskrivelse}")
        elif f.kategori == 'VOLUM':
            console.print(f"\n  VOLUM: Juster ukentlige distanser")
            console.print(f"  {f.beskrivelse}")
        elif f.kategori == 'BELASTNING':
            console.print(f"\n  BELASTNING: Legg inn nedtrapping")
            console.print(f"  {f.beskrivelse}")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Adaptiv planjustering')
    parser.add_argument('--oppdater', action='store_true', help='Oppdater planen etter godkjenning')
    args = parser.parse_args()

    forslag = vis_analyse()

    if args.oppdater and forslag:
        oppdater_plan(forslag)


if __name__ == '__main__':
    main()
