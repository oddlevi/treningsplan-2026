#!/usr/bin/env python3
"""
Diagnostiserer årsaker til lav Training Readiness.

Henter detaljerte data fra Garmin og analyserer korrelasjoner
mellom Readiness og søvn, stress, belastning og andre faktorer.
"""

import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from statistics import mean, stdev

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

load_dotenv()

console = Console()

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"


@dataclass
class DayDiagnostics:
    """Diagnostikkdata for én dag."""
    date: str
    weekday: str

    # Training Readiness
    readiness: int
    readiness_factors: Dict[str, Any]

    # Søvn
    sleep_score: Optional[int]
    sleep_hours: float
    sleep_deep_hours: float
    sleep_light_hours: float
    sleep_rem_hours: float
    sleep_awake_hours: float

    # Stress
    stress_avg: int
    stress_max: int
    stress_rest: int  # Stressnivå i hvile

    # Fysisk
    resting_hr: int
    body_battery_morning: int
    body_battery_evening: int
    body_battery_drain: int

    # Belastning
    training_load: float
    activity_minutes: int
    steps: int


def get_garmin_client():
    """Oppretter Garmin-klient med innlogging."""
    try:
        from garminconnect import Garmin
    except ImportError:
        console.print("[red]Feil: garminconnect ikke installert[/red]")
        console.print("Kjør: pip3 install garminconnect")
        sys.exit(1)

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    if not email or not password:
        console.print("[red]Feil: GARMIN_EMAIL og GARMIN_PASSWORD må settes i .env[/red]")
        sys.exit(1)

    client = Garmin(email, password)
    client.login()
    return client


def fetch_sleep_data(garmin, date_str: str) -> Dict[str, Any]:
    """Henter detaljert søvndata for en dato."""
    try:
        sleep = garmin.get_sleep_data(date_str)
        if sleep and 'dailySleepDTO' in sleep:
            dto = sleep['dailySleepDTO']
            return {
                'score': dto.get('sleepScores', {}).get('overall', {}).get('value'),
                'hours': dto.get('sleepTimeSeconds', 0) / 3600,
                'deep_hours': dto.get('deepSleepSeconds', 0) / 3600,
                'light_hours': dto.get('lightSleepSeconds', 0) / 3600,
                'rem_hours': dto.get('remSleepSeconds', 0) / 3600,
                'awake_hours': dto.get('awakeSleepSeconds', 0) / 3600,
            }
    except Exception as e:
        console.print(f"[dim]Søvn {date_str}: {e}[/dim]")

    return {
        'score': None,
        'hours': 0,
        'deep_hours': 0,
        'light_hours': 0,
        'rem_hours': 0,
        'awake_hours': 0,
    }


def fetch_stress_data(garmin, date_str: str) -> Dict[str, int]:
    """Henter stressdata for en dato."""
    try:
        stress = garmin.get_stress_data(date_str)
        if stress:
            values = [
                v.get('stressLevel', -1)
                for v in stress.get('stressValuesArray', [])
                if v.get('stressLevel', -1) >= 0
            ]
            rest_values = [
                v.get('stressLevel', -1)
                for v in stress.get('stressValuesArray', [])
                if v.get('stressLevel', -1) >= 0 and v.get('stressLevel', 100) < 25
            ]
            if values:
                return {
                    'avg': int(mean(values)),
                    'max': max(values),
                    'rest': int(mean(rest_values)) if rest_values else 0,
                }
    except Exception as e:
        console.print(f"[dim]Stress {date_str}: {e}[/dim]")

    return {'avg': 0, 'max': 0, 'rest': 0}


def fetch_body_battery(garmin, date_str: str) -> Dict[str, int]:
    """Henter Body Battery-data for en dato."""
    try:
        bb = garmin.get_body_battery(date_str)
        if bb:
            values = [
                v[1] for v in bb
                if isinstance(v, list) and len(v) >= 2 and v[1] is not None
            ]
            if values:
                # Morgen = første verdi, kveld = siste verdi
                return {
                    'morning': values[0] if values else 0,
                    'evening': values[-1] if values else 0,
                    'max': max(values),
                    'min': min(values),
                    'drain': values[0] - values[-1] if len(values) >= 2 else 0,
                }
    except Exception as e:
        console.print(f"[dim]Body Battery {date_str}: {e}[/dim]")

    return {'morning': 0, 'evening': 0, 'max': 0, 'min': 0, 'drain': 0}


def fetch_readiness_factors(garmin, date_str: str) -> Dict[str, Any]:
    """Henter Training Readiness-faktorer."""
    try:
        readiness = garmin.get_training_readiness(date_str)
        if readiness and len(readiness) > 0:
            r = readiness[0]
            return {
                'score': r.get('score', 0),
                'sleep_score': r.get('sleepScore'),
                'recovery_score': r.get('recoveryScore'),
                'hrv_score': r.get('hrvScore'),
                'stress_score': r.get('acuteStressScore'),
                'training_load_score': r.get('trainingLoadScore'),
                'level': r.get('level', 'UNKNOWN'),
            }
    except Exception as e:
        console.print(f"[dim]Readiness factors {date_str}: {e}[/dim]")

    return {}


def fetch_activities(garmin, date_str: str) -> Dict[str, Any]:
    """Henter aktivitetsdata for en dato."""
    try:
        activities = garmin.get_activities_by_date(date_str, date_str)
        total_load = 0
        total_minutes = 0
        for a in activities:
            total_load += a.get('activityTrainingLoad', 0) or 0
            total_minutes += (a.get('duration', 0) or 0) / 60
        return {
            'load': total_load,
            'minutes': int(total_minutes),
            'count': len(activities),
        }
    except Exception as e:
        console.print(f"[dim]Activities {date_str}: {e}[/dim]")

    return {'load': 0, 'minutes': 0, 'count': 0}


def fetch_steps(garmin, date_str: str) -> int:
    """Henter skrittdata for en dato."""
    try:
        steps = garmin.get_steps_data(date_str)
        if steps and 'totalSteps' in steps:
            return steps['totalSteps']
    except Exception:
        pass
    return 0


def get_db_metrics(date_str: str) -> Dict[str, Any]:
    """Henter eksisterende metrikker fra databasen."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT training_readiness, sleep_score, sleep_hours,
               resting_hr, stress_avg, body_battery_max, body_battery_min,
               acute_load, steps
        FROM daily_metrics
        WHERE date = ?
    """, (date_str,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            'readiness': row[0] or 0,
            'sleep_score': row[1],
            'sleep_hours': row[2] or 0,
            'resting_hr': row[3] or 0,
            'stress_avg': row[4] or 0,
            'body_battery_max': row[5] or 0,
            'body_battery_min': row[6] or 0,
            'acute_load': row[7] or 0,
            'steps': row[8] or 0,
        }
    return {}


def diagnose_day(garmin, date_str: str) -> Optional[DayDiagnostics]:
    """Samler all diagnostikkdata for én dag."""
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    weekdays = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn']
    weekday = weekdays[date_obj.weekday()]

    # Hent fra database først
    db = get_db_metrics(date_str)

    # Hent ferske data fra Garmin
    console.print(f"[dim]Henter data for {date_str}...[/dim]")

    sleep = fetch_sleep_data(garmin, date_str)
    stress = fetch_stress_data(garmin, date_str)
    bb = fetch_body_battery(garmin, date_str)
    factors = fetch_readiness_factors(garmin, date_str)
    activities = fetch_activities(garmin, date_str)
    steps = fetch_steps(garmin, date_str)

    return DayDiagnostics(
        date=date_str,
        weekday=weekday,
        readiness=factors.get('score', db.get('readiness', 0)),
        readiness_factors=factors,
        sleep_score=sleep['score'] or db.get('sleep_score'),
        sleep_hours=sleep['hours'] or db.get('sleep_hours', 0),
        sleep_deep_hours=sleep['deep_hours'],
        sleep_light_hours=sleep['light_hours'],
        sleep_rem_hours=sleep['rem_hours'],
        sleep_awake_hours=sleep['awake_hours'],
        stress_avg=stress['avg'] or db.get('stress_avg', 0),
        stress_max=stress['max'],
        stress_rest=stress['rest'],
        resting_hr=db.get('resting_hr', 0),
        body_battery_morning=bb['morning'],
        body_battery_evening=bb['evening'],
        body_battery_drain=bb['drain'],
        training_load=activities['load'] or db.get('acute_load', 0),
        activity_minutes=activities['minutes'],
        steps=steps or db.get('steps', 0),
    )


def analyze_correlations(days: List[DayDiagnostics]) -> Dict[str, Any]:
    """Analyserer korrelasjoner mellom Readiness og andre faktorer."""

    low_days = [d for d in days if d.readiness < 20]
    high_days = [d for d in days if d.readiness >= 40]

    analysis = {
        'low_days_count': len(low_days),
        'high_days_count': len(high_days),
        'comparisons': {},
        'likely_causes': [],
    }

    def safe_mean(values):
        return mean(values) if values else 0

    # Sammenlign faktorer mellom lave og høye dager
    metrics = [
        ('Søvn (timer)', 'sleep_hours'),
        ('Søvn-score', 'sleep_score'),
        ('Dyp søvn (timer)', 'sleep_deep_hours'),
        ('REM søvn (timer)', 'sleep_rem_hours'),
        ('Stress snitt', 'stress_avg'),
        ('Stress maks', 'stress_max'),
        ('Body Battery morgen', 'body_battery_morning'),
        ('Body Battery drain', 'body_battery_drain'),
        ('Treningsbelastning', 'training_load'),
        ('Hvile-HR', 'resting_hr'),
    ]

    for name, attr in metrics:
        low_vals = [getattr(d, attr) for d in low_days if getattr(d, attr)]
        high_vals = [getattr(d, attr) for d in high_days if getattr(d, attr)]

        low_avg = safe_mean(low_vals) if low_vals else None
        high_avg = safe_mean(high_vals) if high_vals else None

        if low_avg is not None and high_avg is not None and high_avg != 0:
            diff_pct = ((low_avg - high_avg) / high_avg) * 100
            analysis['comparisons'][name] = {
                'low_avg': low_avg,
                'high_avg': high_avg,
                'diff_pct': diff_pct,
            }

    # Identifiser sannsynlige årsaker
    comps = analysis['comparisons']

    if 'Søvn (timer)' in comps and comps['Søvn (timer)']['diff_pct'] < -15:
        analysis['likely_causes'].append(
            f"🔴 SØVN: {comps['Søvn (timer)']['low_avg']:.1f}t på lave dager vs "
            f"{comps['Søvn (timer)']['high_avg']:.1f}t på gode dager "
            f"({comps['Søvn (timer)']['diff_pct']:.0f}%)"
        )

    if 'Dyp søvn (timer)' in comps and comps['Dyp søvn (timer)']['diff_pct'] < -20:
        analysis['likely_causes'].append(
            f"🔴 DYP SØVN: {comps['Dyp søvn (timer)']['low_avg']:.1f}t vs "
            f"{comps['Dyp søvn (timer)']['high_avg']:.1f}t "
            f"({comps['Dyp søvn (timer)']['diff_pct']:.0f}%)"
        )

    if 'Stress snitt' in comps and comps['Stress snitt']['diff_pct'] > 20:
        analysis['likely_causes'].append(
            f"🔴 STRESS: Snitt {comps['Stress snitt']['low_avg']:.0f} på lave dager vs "
            f"{comps['Stress snitt']['high_avg']:.0f} på gode dager "
            f"(+{comps['Stress snitt']['diff_pct']:.0f}%)"
        )

    if 'Body Battery morgen' in comps and comps['Body Battery morgen']['diff_pct'] < -20:
        analysis['likely_causes'].append(
            f"🔴 BODY BATTERY: Starter dagen på {comps['Body Battery morgen']['low_avg']:.0f} vs "
            f"{comps['Body Battery morgen']['high_avg']:.0f} "
            f"({comps['Body Battery morgen']['diff_pct']:.0f}%)"
        )

    if 'Treningsbelastning' in comps and comps['Treningsbelastning']['diff_pct'] > 30:
        analysis['likely_causes'].append(
            f"🟡 OVERBELASTNING: {comps['Treningsbelastning']['low_avg']:.0f} load på lave dager vs "
            f"{comps['Treningsbelastning']['high_avg']:.0f} på gode dager"
        )

    return analysis


def print_diagnostics(days: List[DayDiagnostics], analysis: Dict[str, Any]):
    """Skriver ut diagnostikkrapport."""

    console.print("\n")
    console.print(Panel.fit(
        "[bold]TRAINING READINESS DIAGNOSE[/bold]\n"
        f"Periode: {days[-1].date} – {days[0].date}",
        border_style="blue"
    ))

    # Hovedtabell
    table = Table(
        title="\nDaglig oversikt",
        box=box.ROUNDED,
        show_lines=True,
    )

    table.add_column("Dag", style="bold")
    table.add_column("Dato")
    table.add_column("Readiness", justify="center")
    table.add_column("Søvn", justify="center")
    table.add_column("Dyp/REM", justify="center")
    table.add_column("Stress", justify="center")
    table.add_column("BB start", justify="center")
    table.add_column("Load", justify="center")

    for d in days:
        # Fargekoding for Readiness
        if d.readiness < 30:
            r_style = "[red]"
        elif d.readiness < 60:
            r_style = "[yellow]"
        else:
            r_style = "[green]"

        readiness_str = f"{r_style}{d.readiness}[/]"

        # Søvn
        sleep_str = f"{d.sleep_hours:.1f}t" if d.sleep_hours > 0 else "-"
        if d.sleep_score:
            sleep_str += f" ({d.sleep_score})"

        # Dyp/REM
        deep_rem = f"{d.sleep_deep_hours:.1f}/{d.sleep_rem_hours:.1f}" if d.sleep_deep_hours > 0 else "-"

        # Stress
        stress_str = f"{d.stress_avg}" if d.stress_avg > 0 else "-"
        if d.stress_max > 0:
            stress_str += f" (max {d.stress_max})"

        # Body Battery
        bb_str = str(d.body_battery_morning) if d.body_battery_morning > 0 else "-"

        # Load
        load_str = f"{d.training_load:.0f}" if d.training_load > 0 else "-"

        table.add_row(
            d.weekday,
            d.date,
            readiness_str,
            sleep_str,
            deep_rem,
            stress_str,
            bb_str,
            load_str,
        )

    console.print(table)

    # Readiness-faktorer fra Garmin (hvis tilgjengelig)
    factors_found = [d for d in days if d.readiness_factors]
    if factors_found:
        console.print("\n[bold]Garmin Readiness-faktorer (nyeste dag):[/bold]")
        latest = factors_found[0]
        f = latest.readiness_factors

        factor_table = Table(box=box.SIMPLE)
        factor_table.add_column("Komponent")
        factor_table.add_column("Score")
        factor_table.add_column("Vurdering")

        components = [
            ('Søvn', f.get('sleep_score')),
            ('Restitusjon', f.get('recovery_score')),
            ('HRV', f.get('hrv_score')),
            ('Stress', f.get('stress_score')),
            ('Treningsbelastning', f.get('training_load_score')),
        ]

        for name, score in components:
            if score is not None:
                if score >= 70:
                    status = "[green]Bra[/green]"
                elif score >= 40:
                    status = "[yellow]Moderat[/yellow]"
                else:
                    status = "[red]Lav[/red]"
                factor_table.add_row(name, str(score), status)

        console.print(factor_table)

    # Korrelasjonsanalyse
    console.print(f"\n[bold]Korrelasjonsanalyse[/bold]")
    console.print(f"Dager med Readiness <20: {analysis['low_days_count']}")
    console.print(f"Dager med Readiness ≥40: {analysis['high_days_count']}")

    if analysis['comparisons']:
        comp_table = Table(title="\nForskjell: Lave vs gode dager", box=box.SIMPLE)
        comp_table.add_column("Faktor")
        comp_table.add_column("Lave dager", justify="right")
        comp_table.add_column("Gode dager", justify="right")
        comp_table.add_column("Forskjell", justify="right")

        for name, data in analysis['comparisons'].items():
            diff = data['diff_pct']
            diff_str = f"{diff:+.0f}%"
            if abs(diff) > 20:
                diff_str = f"[bold red]{diff_str}[/bold red]"
            elif abs(diff) > 10:
                diff_str = f"[yellow]{diff_str}[/yellow]"

            comp_table.add_row(
                name,
                f"{data['low_avg']:.1f}",
                f"{data['high_avg']:.1f}",
                diff_str,
            )

        console.print(comp_table)

    # Sannsynlige årsaker
    if analysis['likely_causes']:
        console.print("\n[bold]Identifiserte årsaker til lav Readiness:[/bold]")
        for cause in analysis['likely_causes']:
            console.print(f"  {cause}")
    else:
        console.print("\n[yellow]Ingen tydelig årsak identifisert fra dataene.[/yellow]")
        console.print("  Mulige skjulte faktorer: alkohol, sykdom, mental stress, overtrening.")

    # Anbefaling
    console.print("\n")

    # Sjekk om det er søvn-problem
    avg_sleep = mean([d.sleep_hours for d in days if d.sleep_hours > 0]) if any(d.sleep_hours > 0 for d in days) else 0
    avg_deep = mean([d.sleep_deep_hours for d in days if d.sleep_deep_hours > 0]) if any(d.sleep_deep_hours > 0 for d in days) else 0

    recommendations = []

    if avg_sleep < 7:
        recommendations.append("🛏️ Prioriter 7-8 timer søvn per natt")

    if avg_deep < 1.0:
        recommendations.append("🌙 For lite dyp søvn – unngå alkohol, sen trening og skjermtid før leggetid")

    low_readiness_streak = sum(1 for d in days if d.readiness < 30)
    if low_readiness_streak >= 5:
        recommendations.append("⚠️ 5+ dager med lav Readiness – vurder å utsette terskeløkten 1-2 dager")

    avg_stress = mean([d.stress_avg for d in days if d.stress_avg > 0]) if any(d.stress_avg > 0 for d in days) else 0
    if avg_stress > 40:
        recommendations.append("🧘 Høyt stressnivå – vurder avslapningsteknikker")

    if not recommendations:
        recommendations.append("✅ Dataene viser ingen åpenbar årsak til lav Readiness")
        recommendations.append("💡 Vurder: alkohol siste uke? Sykdom? Uvanlig mental belastning?")

    console.print(Panel(
        "[bold]ANBEFALING FØR TERSKELØKT[/bold]\n\n" +
        "\n".join(recommendations) +
        "\n\n[dim]Hvis Readiness i morgen er >40, kjør 4×6 min som planlagt.[/dim]\n"
        "[dim]Hvis Readiness <30, vurder å gjøre om til rolig langtur i stedet.[/dim]",
        border_style="green" if days[0].readiness >= 40 else "yellow",
    ))


def main():
    """Hovedfunksjon."""
    console.print("[bold blue]Henter diagnostikkdata fra Garmin...[/bold blue]\n")

    garmin = get_garmin_client()

    # Hent data for siste 14 dager (eller 8 dager som spesifisert)
    end_date = datetime(2026, 5, 11)  # I dag
    start_date = datetime(2026, 5, 4)  # 8 dager tilbake

    days = []
    current = end_date

    while current >= start_date:
        date_str = current.strftime('%Y-%m-%d')
        try:
            day_data = diagnose_day(garmin, date_str)
            if day_data:
                days.append(day_data)
        except Exception as e:
            console.print(f"[red]Feil for {date_str}: {e}[/red]")

        current -= timedelta(days=1)

    if not days:
        console.print("[red]Ingen data hentet[/red]")
        sys.exit(1)

    # Analyser korrelasjoner
    analysis = analyze_correlations(days)

    # Skriv ut rapport
    print_diagnostics(days, analysis)


if __name__ == "__main__":
    main()
