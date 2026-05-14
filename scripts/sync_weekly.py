#!/usr/bin/env python3
"""
Ukentlig synkronisering og rapportgenerering.

Kjøres hver søndag kveld for å:
1. Synce nye aktiviteter fra Strava og Garmin
2. Generere ukerapport (planlagt vs faktisk)
3. Analysere restitusjon og belastning
4. Foreslå justeringer for kommende uke

Bruk:
    python scripts/sync_weekly.py              # Full kjøring
    python scripts/sync_weekly.py --no-sync    # Kun rapport (skip sync)
    python scripts/sync_weekly.py --week 20    # Rapport for spesifikk uke
"""

import sqlite3
import subprocess
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

# Prosjektstier
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
PLAN_PATH = PROJECT_ROOT / "plan" / "current_plan.md"
RAPPORT_DIR = PROJECT_ROOT / "rapport"

console = Console()

# Bakken pace-soner (sekunder per km)
PACE_ZONES = {
    'restitusjon': (320, 345),      # 5:20-5:45
    'rolig_sone2': (300, 315),      # 5:00-5:15
    'terskel_1': (250, 260),        # 4:10-4:20
    'terskel_2': (240, 250),        # 4:00-4:10
    'vo2max': (210, 219),           # 3:30-3:39
}

# HR-soner
HR_ZONES = {
    'lett': (0, 145),
    'moderat': (145, 160),
    'hard': (160, 200),
}


@dataclass
class PlannedWorkout:
    """Planlagt økt fra current_plan.md."""
    date: str
    day_name: str
    workout_type: str
    distance_km: float
    pace_range: str
    description: str


@dataclass
class ActualWorkout:
    """Faktisk gjennomført økt fra databasen."""
    date: str
    name: str
    distance_km: float
    moving_time_s: int
    avg_pace_s: float
    avg_hr: int
    max_hr: int
    source: str


@dataclass
class DailyMetrics:
    """Daglige metrikker fra Garmin."""
    date: str
    hrv_value: float
    hrv_status: str
    training_readiness: int
    sleep_score: int
    sleep_hours: float
    resting_hr: int
    acute_load: float
    chronic_load: float


def run_sync():
    """Kjører inkrementell sync fra Strava og Garmin."""
    console.print("\n[bold blue]1. INKREMENTELL SYNC[/bold blue]\n")

    results = {'strava': 0, 'garmin': 0}

    # Strava sync
    with console.status("[bold green]Syncer fra Strava..."):
        try:
            # Tell aktiviteter før
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM activities WHERE source='strava'")
            before_strava = cursor.fetchone()[0]
            conn.close()

            # Kjør fetch_strava.py
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "fetch_strava.py")],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT)
            )

            # Tell aktiviteter etter
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM activities WHERE source='strava'")
            after_strava = cursor.fetchone()[0]
            conn.close()

            results['strava'] = after_strava - before_strava
            console.print(f"  Strava: [green]+{results['strava']} nye aktiviteter[/green]")

        except Exception as e:
            console.print(f"  [red]Strava sync feilet: {e}[/red]")

    # Garmin sync (siste 7 dager)
    with console.status("[bold green]Syncer fra Garmin (siste 7 dager)..."):
        try:
            # Tell aktiviteter før
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM activities WHERE source='garmin'")
            before_garmin = cursor.fetchone()[0]
            conn.close()

            # Kjør fetch_garmin.py --days 7
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "fetch_garmin.py"), "--days", "7"],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT)
            )

            # Tell aktiviteter etter
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM activities WHERE source='garmin'")
            after_garmin = cursor.fetchone()[0]
            conn.close()

            results['garmin'] = after_garmin - before_garmin
            console.print(f"  Garmin: [green]+{results['garmin']} nye aktiviteter[/green]")

        except Exception as e:
            console.print(f"  [red]Garmin sync feilet: {e}[/red]")

    return results


def parse_plan(week_start: datetime, week_end: datetime) -> list[PlannedWorkout]:
    """Parser current_plan.md for å finne planlagte økter i gitt uke."""

    if not PLAN_PATH.exists():
        console.print("[yellow]Ingen plan funnet i plan/current_plan.md[/yellow]")
        return []

    with open(PLAN_PATH, 'r') as f:
        content = f.read()

    planned = []

    # Finn alle daglige økter med regex
    # Format: ### Dag DD.MM – Økttype 🔴/🟢/⚪
    # Støtter både en-dash (–) og bindestrek (-)
    day_pattern = r'###\s+(\w+)\s+(\d{2})\.(\d{2})\s+[–-]\s+(.+?)\s*[🔴🟢🟡⚪]'

    # Finn distanse og pace
    dist_pattern = r'\*\*Distanse\*\*\s*\|\s*(\d+(?:\.\d+)?)\s*km'
    pace_pattern = r'\*\*Pace\*\*\s*\|\s*([\d:]+(?:-[\d:]+)?/km)'

    lines = content.split('\n')
    current_date = None
    current_day = None
    current_type = None
    current_dist = None
    current_pace = None

    i = 0
    while i < len(lines):
        line = lines[i]

        # Sjekk for dag-header
        day_match = re.search(day_pattern, line)
        if day_match:
            # Lagre forrige økt hvis vi har en
            if current_date and current_type:
                # Sjekk om datoen er i vår uke
                try:
                    workout_date = datetime.strptime(current_date, '%Y-%m-%d')
                    if week_start <= workout_date <= week_end:
                        planned.append(PlannedWorkout(
                            date=current_date,
                            day_name=current_day,
                            workout_type=current_type,
                            distance_km=current_dist or 0,
                            pace_range=current_pace or '',
                            description=''
                        ))
                except:
                    pass

            # Parse ny dag
            day_name = day_match.group(1)
            day_num = int(day_match.group(2))
            month_num = int(day_match.group(3))
            workout_type = day_match.group(4).strip()

            # Bygg dato (anta 2026)
            year = 2026
            current_date = f"{year}-{month_num:02d}-{day_num:02d}"
            current_day = day_name
            current_type = workout_type
            current_dist = None
            current_pace = None

        # Sjekk for distanse
        dist_match = re.search(dist_pattern, line)
        if dist_match:
            current_dist = float(dist_match.group(1))

        # Sjekk for pace
        pace_match = re.search(pace_pattern, line)
        if pace_match:
            current_pace = pace_match.group(1)

        i += 1

    # Lagre siste økt
    if current_date and current_type:
        try:
            workout_date = datetime.strptime(current_date, '%Y-%m-%d')
            if week_start <= workout_date <= week_end:
                planned.append(PlannedWorkout(
                    date=current_date,
                    day_name=current_day,
                    workout_type=current_type,
                    distance_km=current_dist or 0,
                    pace_range=current_pace or '',
                    description=''
                ))
        except:
            pass

    return planned


def get_actual_workouts(week_start: datetime, week_end: datetime) -> list[ActualWorkout]:
    """Henter faktiske aktiviteter fra databasen for gitt uke."""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
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
        AND activity_date >= ?
        AND activity_date <= ?
        ORDER BY activity_date
    """, (week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))

    workouts = []
    for row in cursor.fetchall():
        workouts.append(ActualWorkout(
            date=row[0],
            name=row[1] or '',
            distance_km=row[2] or 0,
            moving_time_s=row[3] or 0,
            avg_pace_s=row[4] or 0,
            avg_hr=row[5] or 0,
            max_hr=row[6] or 0,
            source=row[7] or ''
        ))

    conn.close()
    return workouts


def get_daily_metrics(week_start: datetime, week_end: datetime) -> list[DailyMetrics]:
    """Henter daglige metrikker fra Garmin for gitt uke."""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            date,
            hrv_value,
            hrv_status,
            training_readiness,
            sleep_score,
            sleep_hours,
            resting_hr,
            acute_load,
            chronic_load
        FROM daily_metrics
        WHERE date >= ?
        AND date <= ?
        ORDER BY date
    """, (week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))

    metrics = []
    for row in cursor.fetchall():
        metrics.append(DailyMetrics(
            date=row[0],
            hrv_value=row[1] or 0,
            hrv_status=row[2] or '',
            training_readiness=row[3] or 0,
            sleep_score=row[4] or 0,
            sleep_hours=row[5] or 0,
            resting_hr=row[6] or 0,
            acute_load=row[7] or 0,
            chronic_load=row[8] or 0
        ))

    conn.close()
    return metrics


def get_hrv_baseline(end_date: datetime, days: int = 28) -> float:
    """Henter HRV-baseline (snitt over siste N dager)."""

    start_date = end_date - timedelta(days=days)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT AVG(hrv_value)
        FROM daily_metrics
        WHERE date >= ? AND date <= ?
        AND hrv_value IS NOT NULL
    """, (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))

    result = cursor.fetchone()[0]
    conn.close()

    return result or 0


def format_pace(seconds_per_km: float) -> str:
    """Konverterer sekunder per km til MM:SS format."""
    if seconds_per_km <= 0:
        return '-'
    minutes = int(seconds_per_km // 60)
    seconds = int(seconds_per_km % 60)
    return f"{minutes}:{seconds:02d}"


def format_time(total_seconds: int) -> str:
    """Konverterer sekunder til HH:MM:SS eller MM:SS format."""
    if total_seconds <= 0:
        return '-'
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def classify_workout(avg_pace_s: float, avg_hr: int) -> str:
    """Klassifiserer økt basert på pace og HR."""

    if avg_pace_s <= 0:
        return 'ukjent'

    # Primært pace-basert klassifisering
    if avg_pace_s >= 320:  # >= 5:20
        return 'restitusjon'
    elif avg_pace_s >= 300:  # >= 5:00
        return 'rolig'
    elif avg_pace_s >= 260:  # >= 4:20
        return 'grå_sone'  # Mellom rolig og terskel
    elif avg_pace_s >= 250:  # >= 4:10
        return 'terskel_1'
    elif avg_pace_s >= 240:  # >= 4:00
        return 'terskel_2'
    else:
        return 'hard'


def analyze_threshold_minutes(workouts: list[ActualWorkout]) -> dict:
    """Analyserer total tid i terskelsoner."""

    # Dette er en forenklet analyse - ideelt sett ville vi sett på splits
    # For nå estimerer vi basert på gjennomsnittspace

    terskel_1_min = 0
    terskel_2_min = 0

    for w in workouts:
        if w.avg_pace_s <= 0:
            continue

        classification = classify_workout(w.avg_pace_s, w.avg_hr)
        duration_min = w.moving_time_s / 60

        if classification == 'terskel_1':
            # Estimer at ~70% av økten er faktisk terskelarbeid (resten er oppvarming/nedjogg)
            terskel_1_min += duration_min * 0.5
        elif classification == 'terskel_2':
            terskel_2_min += duration_min * 0.5

    return {
        'terskel_1': round(terskel_1_min),
        'terskel_2': round(terskel_2_min),
        'total': round(terskel_1_min + terskel_2_min)
    }


def check_easy_days(workouts: list[ActualWorkout], day_names: list[str] = ['onsdag', 'søndag']) -> dict:
    """Sjekker om de rolige dagene faktisk var rolige."""

    results = {}
    day_map = {
        0: 'mandag', 1: 'tirsdag', 2: 'onsdag', 3: 'torsdag',
        4: 'fredag', 5: 'lørdag', 6: 'søndag'
    }

    for w in workouts:
        try:
            workout_date = datetime.strptime(w.date, '%Y-%m-%d')
            day_name = day_map[workout_date.weekday()]

            if day_name in day_names:
                classification = classify_workout(w.avg_pace_s, w.avg_hr)
                was_easy = classification in ['restitusjon', 'rolig']

                results[w.date] = {
                    'day': day_name,
                    'pace': format_pace(w.avg_pace_s),
                    'hr': w.avg_hr,
                    'was_easy': was_easy,
                    'classification': classification
                }
        except:
            continue

    return results


def compare_planned_vs_actual(planned: list[PlannedWorkout], actual: list[ActualWorkout]) -> list[dict]:
    """Sammenligner planlagte og faktiske økter."""

    comparisons = []
    actual_by_date = {w.date: w for w in actual}

    for p in planned:
        a = actual_by_date.get(p.date)

        if p.workout_type.lower() == 'hvile':
            # Hviledag
            if a:
                # Løp på hviledag
                comparisons.append({
                    'date': p.date,
                    'day': p.day_name,
                    'planned_type': 'Hvile',
                    'planned_km': 0,
                    'actual_km': a.distance_km,
                    'actual_pace': format_pace(a.avg_pace_s),
                    'actual_hr': a.avg_hr,
                    'status': '⚠️',
                    'note': f'Løp {a.distance_km:.1f} km på hviledag'
                })
            else:
                comparisons.append({
                    'date': p.date,
                    'day': p.day_name,
                    'planned_type': 'Hvile',
                    'planned_km': 0,
                    'actual_km': 0,
                    'actual_pace': '-',
                    'actual_hr': 0,
                    'status': '✓',
                    'note': 'Hvilt som planlagt'
                })
        else:
            # Treningsdag
            if a:
                km_diff = a.distance_km - p.distance_km
                km_diff_pct = (km_diff / p.distance_km * 100) if p.distance_km > 0 else 0

                if abs(km_diff_pct) <= 15:
                    status = '✓'
                    note = 'Truffet'
                elif km_diff_pct > 15:
                    status = '⚠️'
                    note = f'+{km_diff:.1f} km over plan'
                else:
                    status = '⚠️'
                    note = f'{km_diff:.1f} km under plan'

                comparisons.append({
                    'date': p.date,
                    'day': p.day_name,
                    'planned_type': p.workout_type,
                    'planned_km': p.distance_km,
                    'actual_km': a.distance_km,
                    'actual_pace': format_pace(a.avg_pace_s),
                    'actual_hr': a.avg_hr,
                    'status': status,
                    'note': note
                })
            else:
                comparisons.append({
                    'date': p.date,
                    'day': p.day_name,
                    'planned_type': p.workout_type,
                    'planned_km': p.distance_km,
                    'actual_km': 0,
                    'actual_pace': '-',
                    'actual_hr': 0,
                    'status': '❌',
                    'note': 'Ikke gjennomført'
                })

    return comparisons


def calculate_acwr(metrics: list[DailyMetrics]) -> float:
    """Beregner ACWR (Acute:Chronic Workload Ratio)."""

    if not metrics:
        return 0

    # Bruk siste metrikk som har load-data
    for m in reversed(metrics):
        if m.acute_load and m.chronic_load:
            return round(m.acute_load / m.chronic_load, 2)

    return 0


def generate_insights() -> list[str]:
    """
    Genererer innsikter fra dataene over tid.

    Analyserer:
    - Pace-drift på langturer
    - HR-respons på samme pace over uker
    - Hvilke dager har best HRV
    - Sammenheng kveldstrening og søvn
    - Pace-forskjell tredemølle vs ute
    - HR etter harde økter
    """
    insights = []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Pace-drift på langturer (siste 4 langturer)
    cursor.execute("""
        SELECT
            date(CASE
                WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                ELSE start_date
            END) as activity_date,
            distance_km,
            avg_pace_s_per_km,
            name
        FROM activities
        WHERE sport IN ('running', 'Run')
        AND distance_km >= 14
        ORDER BY start_date DESC
        LIMIT 4
    """)
    langturer = cursor.fetchall()

    if len(langturer) >= 2:
        paces = [l[2] for l in langturer if l[2] and l[2] > 0]
        if paces:
            newest = paces[0]
            oldest = paces[-1]
            drift = newest - oldest
            if drift > 10:
                insights.append(f"📈 Langturs-pace har økt {drift:.0f} sek/km over siste {len(paces)} langturer – du går ut for rolig eller mister form")
            elif drift < -10:
                insights.append(f"📉 Langturs-pace har sunket {abs(drift):.0f} sek/km – god fremgang!")

    # 2. HR-respons på samme pace (terskeløkter)
    cursor.execute("""
        SELECT
            date(CASE
                WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                ELSE start_date
            END) as activity_date,
            avg_pace_s_per_km,
            avg_hr,
            distance_km
        FROM activities
        WHERE sport IN ('running', 'Run')
        AND avg_pace_s_per_km BETWEEN 240 AND 270
        AND avg_hr > 0
        ORDER BY start_date DESC
        LIMIT 8
    """)
    terskel_okter = cursor.fetchall()

    if len(terskel_okter) >= 4:
        # Sammenlign HR ved lignende pace
        recent = terskel_okter[:len(terskel_okter)//2]
        older = terskel_okter[len(terskel_okter)//2:]

        recent_hr = sum(o[2] for o in recent) / len(recent)
        older_hr = sum(o[2] for o in older) / len(older)
        hr_diff = recent_hr - older_hr

        if hr_diff < -5:
            insights.append(f"💪 HR ved terskel-pace har sunket {abs(hr_diff):.0f} bpm – formen stiger!")
        elif hr_diff > 5:
            insights.append(f"⚠️ HR ved terskel-pace har økt {hr_diff:.0f} bpm – mulig tretthet eller dehydrering")

    # 3. Beste HRV-dager
    cursor.execute("""
        SELECT
            CASE strftime('%w', date)
                WHEN '0' THEN 'Søndag'
                WHEN '1' THEN 'Mandag'
                WHEN '2' THEN 'Tirsdag'
                WHEN '3' THEN 'Onsdag'
                WHEN '4' THEN 'Torsdag'
                WHEN '5' THEN 'Fredag'
                WHEN '6' THEN 'Lørdag'
            END as weekday,
            AVG(hrv_weekly_avg) as avg_hrv,
            COUNT(*) as n
        FROM daily_metrics
        WHERE hrv_weekly_avg > 0
        AND date >= date('now', '-28 days')
        GROUP BY strftime('%w', date)
        HAVING n >= 2
        ORDER BY avg_hrv DESC
    """)
    hrv_by_day = cursor.fetchall()

    if len(hrv_by_day) >= 3:
        best_day = hrv_by_day[0]
        worst_day = hrv_by_day[-1]
        if best_day[1] - worst_day[1] > 5:
            insights.append(f"📊 Høyest HRV på {best_day[0]} ({best_day[1]:.0f}), lavest på {worst_day[0]} ({worst_day[1]:.0f})")

    # 4. Kveldstrening og søvn-score
    # Finner økter som startet etter 18:00 og sjekker søvn dagen etter
    cursor.execute("""
        SELECT
            date(CASE
                WHEN a.start_date LIKE '%T%' THEN replace(replace(a.start_date, 'T', ' '), 'Z', '')
                ELSE a.start_date
            END) as activity_date,
            CAST(substr(a.start_date, 12, 2) AS INTEGER) as hour,
            d.sleep_score
        FROM activities a
        LEFT JOIN daily_metrics d ON date(a.start_date, '+1 day') = d.date
        WHERE a.sport IN ('running', 'Run')
        AND hour >= 18
        AND d.sleep_score > 0
        AND a.start_date >= date('now', '-28 days')
    """)
    kvelds_okter = cursor.fetchall()

    if len(kvelds_okter) >= 3:
        kvelds_sovn = sum(o[2] for o in kvelds_okter) / len(kvelds_okter)

        cursor.execute("""
            SELECT AVG(sleep_score)
            FROM daily_metrics
            WHERE sleep_score > 0
            AND date >= date('now', '-28 days')
        """)
        total_sovn = cursor.fetchone()[0] or 0

        if total_sovn > 0:
            diff = kvelds_sovn - total_sovn
            if diff < -5:
                insights.append(f"😴 Kveldstrening (etter 18:00) gir {abs(diff):.0f} poeng dårligere søvn-score")
            elif diff > 5:
                insights.append(f"✅ Kveldstrening ser ikke ut til å påvirke søvnen negativt")

    # 5. Tredemølle vs ute
    cursor.execute("""
        SELECT
            CASE
                WHEN name LIKE '%tredemølle%' OR name LIKE '%treadmill%' OR name LIKE '%indoor%' THEN 'inne'
                ELSE 'ute'
            END as location,
            AVG(avg_pace_s_per_km) as avg_pace,
            COUNT(*) as n
        FROM activities
        WHERE sport IN ('running', 'Run')
        AND avg_pace_s_per_km > 0
        AND start_date >= date('now', '-60 days')
        GROUP BY location
        HAVING n >= 3
    """)
    location_stats = cursor.fetchall()

    inne_pace = next((l[1] for l in location_stats if l[0] == 'inne'), None)
    ute_pace = next((l[1] for l in location_stats if l[0] == 'ute'), None)

    if inne_pace and ute_pace:
        diff = inne_pace - ute_pace
        if abs(diff) > 10:
            faster = "tredemølle" if diff < 0 else "ute"
            insights.append(f"🏃 Du løper {abs(diff):.0f} sek/km raskere på {faster}")

    # 6. HR etter harde økter (proxy for restitusjon)
    cursor.execute("""
        SELECT
            d.date,
            d.resting_hr,
            a.avg_pace_s_per_km
        FROM daily_metrics d
        LEFT JOIN activities a ON date(a.start_date, '-1 day') = d.date
        WHERE a.avg_pace_s_per_km BETWEEN 230 AND 280
        AND d.resting_hr > 0
        AND d.date >= date('now', '-28 days')
        ORDER BY d.date DESC
    """)
    hr_etter_hard = cursor.fetchall()

    if len(hr_etter_hard) >= 3:
        avg_hr_etter = sum(h[1] for h in hr_etter_hard) / len(hr_etter_hard)

        cursor.execute("""
            SELECT AVG(resting_hr)
            FROM daily_metrics
            WHERE resting_hr > 0
            AND date >= date('now', '-28 days')
        """)
        avg_hr_total = cursor.fetchone()[0] or 0

        if avg_hr_total > 0:
            diff = avg_hr_etter - avg_hr_total
            if diff > 3:
                insights.append(f"❤️ Hvile-HR er {diff:.0f} bpm høyere dagen etter harde økter – vurder bedre restitusjon")

    conn.close()

    if not insights:
        insights.append("📊 For lite data til å identifisere mønstre – fortsett å logge!")

    return insights


def generate_adjustments(
    comparisons: list[dict],
    metrics: list[DailyMetrics],
    hrv_baseline: float,
    acwr: float
) -> list[str]:
    """Genererer justeringsforslag basert på data."""

    suggestions = []

    # Sjekk ACWR
    if acwr > 1.3:
        suggestions.append(f"🔴 ACWR er {acwr} (over 1.3) – foreslår å redusere torsdagens terskel med 30%")
    elif acwr > 1.2:
        suggestions.append(f"🟡 ACWR er {acwr} – hold øye med belastning")

    # Sjekk HRV
    if metrics:
        hrv_values = [m.hrv_value for m in metrics if m.hrv_value > 0]
        if hrv_values:
            hrv_avg = sum(hrv_values) / len(hrv_values)
            if hrv_baseline > 0:
                hrv_diff_pct = ((hrv_avg - hrv_baseline) / hrv_baseline) * 100
                if hrv_diff_pct < -10:
                    suggestions.append(f"🔴 HRV ned {abs(hrv_diff_pct):.0f}% vs baseline – vurder å erstatte én terskeløkt med rolig")
                elif hrv_diff_pct < -5:
                    suggestions.append(f"🟡 HRV ned {abs(hrv_diff_pct):.0f}% – følg med på restitusjon")

    # Sjekk Training Readiness
    if metrics:
        readiness_values = [m.training_readiness for m in metrics if m.training_readiness > 0]
        if readiness_values:
            readiness_avg = sum(readiness_values) / len(readiness_values)
            readiness_low_days = sum(1 for m in metrics if 0 < m.training_readiness < 30)

            # Alvorlig underrestitusjon: 3+ dager under 30
            if readiness_low_days >= 3:
                suggestions.append(f"🔴 ALVORLIG: {readiness_low_days} dager med Readiness <30 – anbefaler ekstra hvileuke")
            elif readiness_avg < 40:
                suggestions.append(f"🔴 Training Readiness snitt {readiness_avg:.0f} – kroppen trenger hvile")
            elif readiness_avg < 50:
                suggestions.append(f"🟡 Training Readiness snitt {readiness_avg:.0f} – vurder lettere uke")

        # Sjekk HRV-stress
        hrv_bad_days = sum(1 for m in metrics if m.hrv_status in ('LOW', 'POOR', 'UNBALANCED'))
        if hrv_bad_days >= 3:
            suggestions.append(f"🔴 HRV-STRESS: {hrv_bad_days} dager med ubalansert HRV – prioriter restitusjon")

    # Sjekk gjennomføringsgrad
    missed = sum(1 for c in comparisons if c['status'] == '❌')
    if missed > 1:
        suggestions.append(f"⚠️ {missed} økter ikke gjennomført – vurder om volumet er for høyt")

    # Hvis ingen problemer
    if not suggestions:
        suggestions.append("✅ Alt ser bra ut – fortsett med planen som den er")

    return suggestions


def generate_report(
    week_num: int,
    year: int,
    week_start: datetime,
    week_end: datetime,
    planned: list[PlannedWorkout],
    actual: list[ActualWorkout],
    metrics: list[DailyMetrics],
    comparisons: list[dict],
    hrv_baseline: float,
    acwr: float,
    adjustments: list[str],
    insights: list[str] = None
) -> str:
    """Genererer markdown-rapport."""

    # Beregn totaler
    planned_km = sum(p.distance_km for p in planned)
    actual_km = sum(a.distance_km for a in actual)
    actual_time = sum(a.moving_time_s for a in actual)

    # Terskelanalyse
    threshold = analyze_threshold_minutes(actual)

    # Rolige dager
    easy_days = check_easy_days(actual)

    # HRV-analyse
    hrv_values = [m.hrv_value for m in metrics if m.hrv_value > 0]
    hrv_avg = sum(hrv_values) / len(hrv_values) if hrv_values else 0
    hrv_diff_pct = ((hrv_avg - hrv_baseline) / hrv_baseline * 100) if hrv_baseline > 0 else 0

    # Readiness
    readiness_values = [m.training_readiness for m in metrics if m.training_readiness > 0]
    readiness_avg = sum(readiness_values) / len(readiness_values) if readiness_values else 0

    # Søvn
    sleep_values = [m.sleep_hours for m in metrics if m.sleep_hours > 0]
    sleep_avg = sum(sleep_values) / len(sleep_values) if sleep_values else 0

    report = f"""# Ukerapport {year}-W{week_num:02d}

*Generert {datetime.now().strftime('%Y-%m-%d %H:%M')}*

**Periode:** {week_start.strftime('%d.%m')} – {week_end.strftime('%d.%m.%Y')}

---

## Sammendrag

| Metrikk | Planlagt | Faktisk | Avvik |
|---------|----------|---------|-------|
| Volum | {planned_km:.0f} km | {actual_km:.1f} km | {actual_km - planned_km:+.1f} km |
| Økter | {len([p for p in planned if p.workout_type.lower() != 'hvile'])} | {len(actual)} | {len(actual) - len([p for p in planned if p.workout_type.lower() != 'hvile']):+d} |
| Total tid | - | {format_time(actual_time)} | - |

---

## Dag-for-dag

| Dag | Dato | Planlagt | Faktisk km | Pace | HR | Status |
|-----|------|----------|------------|------|-----|--------|
"""

    for c in comparisons:
        report += f"| {c['day']} | {c['date']} | {c['planned_type']} ({c['planned_km']:.0f} km) | {c['actual_km']:.1f} km | {c['actual_pace']} | {c['actual_hr'] or '-'} | {c['status']} {c['note']} |\n"

    report += f"""
---

## Bakken-metrikker

### Terskelarbeid

| Sone | Estimert tid |
|------|--------------|
| Terskel 1 (4:10-4:20) | ~{threshold['terskel_1']} min |
| Terskel 2 (4:00-4:10) | ~{threshold['terskel_2']} min |
| **Total** | **~{threshold['total']} min** |

*Mål: 45-75 min/uke i base-fase*

### Var de rolige dagene faktisk rolige?

"""

    if easy_days:
        for date, info in easy_days.items():
            status = "✓ Ja" if info['was_easy'] else f"⚠️ Nei ({info['classification']})"
            report += f"- **{info['day'].capitalize()} ({date}):** {info['pace']}/km, HR {info['hr']} – {status}\n"
    else:
        report += "*Ingen data for rolige dager*\n"

    # Beregn Training Readiness-statistikk
    readiness_low_days = sum(1 for m in metrics if 0 < m.training_readiness < 30)
    readiness_mid_days = sum(1 for m in metrics if 30 <= m.training_readiness <= 60)
    readiness_high_days = sum(1 for m in metrics if m.training_readiness > 60)
    severe_underrecovery = readiness_low_days >= 3

    # Beregn HRV-statistikk
    hrv_unbalanced_days = sum(1 for m in metrics if m.hrv_status in ('LOW', 'POOR', 'UNBALANCED'))
    hrv_balanced_days = sum(1 for m in metrics if m.hrv_status == 'BALANCED')
    severe_hrv_stress = hrv_unbalanced_days >= 3

    report += f"""
---

## Restitusjon og belastning

| Metrikk | Verdi | Vurdering |
|---------|-------|-----------|
| ACWR | {acwr} | {'✓ OK' if acwr <= 1.3 else '🔴 Høy'} |
| Søvn snitt | {sleep_avg:.1f}t | {'✓ Bra' if sleep_avg >= 7 else '⚠️ Lite'} |

### Training Readiness (daglig)

| Dag | Dato | Readiness | Status |
|-----|------|-----------|--------|
"""

    # Daglig Training Readiness-tabell
    day_names = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn']
    for m in metrics:
        if m.training_readiness > 0:
            date_obj = datetime.strptime(m.date, '%Y-%m-%d')
            day_name = day_names[date_obj.weekday()]
            readiness = m.training_readiness
            if readiness < 30:
                status = "🔴 Lav"
            elif readiness <= 60:
                status = "🟡 Moderat"
            else:
                status = "🟢 God"
            report += f"| {day_name} | {m.date} | {readiness} | {status} |\n"

    report += f"""
**Ukeoversikt:**
- Snitt: **{readiness_avg:.0f}** {'✓ Bra' if readiness_avg >= 50 else '⚠️ Lav'}
- Dager med god restitusjon (>60): **{readiness_high_days}**
- Dager med lav restitusjon (<30): **{readiness_low_days}**
"""

    if severe_underrecovery:
        report += """
> ⚠️ **ALVORLIG UNDERRESTITUSJON:** 3+ dager med Readiness <30 denne uken.
> Anbefaling: Vurder ekstra hvileuke eller redusert intensitet neste uke.
"""

    report += """
### HRV-trend (daglig)

| Dag | Dato | HRV | Status |
|-----|------|-----|--------|
"""

    # Daglig HRV-tabell
    for m in metrics:
        if m.hrv_value > 0:
            date_obj = datetime.strptime(m.date, '%Y-%m-%d')
            day_name = day_names[date_obj.weekday()]
            status_emoji = {'BALANCED': '✓ Balansert', 'LOW': '⚠️ Lav', 'POOR': '🔴 Dårlig', 'UNBALANCED': '⚠️ Ubalansert'}.get(m.hrv_status, m.hrv_status)
            report += f"| {day_name} | {m.date} | {m.hrv_value:.0f} | {status_emoji} |\n"

    report += f"""
**Ukeoversikt:**
- Snitt: **{hrv_avg:.0f}** ({hrv_diff_pct:+.0f}% vs 28d baseline)
- Dager med balansert HRV: **{hrv_balanced_days}**
- Dager med ubalansert/lav HRV: **{hrv_unbalanced_days}**
"""

    if severe_hrv_stress:
        report += """
> ⚠️ **HRV-STRESS:** 3+ dager med ubalansert/lav HRV denne uken.
> Anbefaling: Prioriter søvn, reduser stress, vurder lettere treningsuke.
"""

    report += f"""
---

## Justeringsforslag for kommende uke

"""

    for adj in adjustments:
        report += f"- {adj}\n"

    # Innsikter-seksjon
    if insights:
        report += """
---

## Innsikter fra dataene

"""
        for insight in insights:
            report += f"- {insight}\n"

    report += """
---

*Vent på bekreftelse før plan/current_plan.md endres.*
"""

    return report


def print_terminal_summary(
    week_num: int,
    planned_km: float,
    actual_km: float,
    hrv_ok: bool,
    ready_for_next: bool
):
    """Skriver kort status til terminalen."""

    hrv_status = "[green]✓[/green]" if hrv_ok else "[yellow]⚠[/yellow]"
    ready_status = "[green]ja[/green]" if ready_for_next else "[yellow]nei[/yellow]"

    panel = Panel(
        f"Uke {week_num}: Planlagt {planned_km:.0f} km / Faktisk {actual_km:.1f} km\n"
        f"HRV {hrv_status} | Klar for uke {week_num + 1}: {ready_status}\n\n"
        f"[dim]Se full rapport: rapport/ukerapport_2026-W{week_num:02d}.md[/dim]",
        title="[bold]Ukestatus[/bold]",
        border_style="blue"
    )
    console.print(panel)


@click.command()
@click.option('--no-sync', is_flag=True, help='Skip synkronisering, kun rapport')
@click.option('--week', default=0, type=int, help='Generer rapport for spesifikk ukenummer')
def main(no_sync: bool, week: int):
    """Ukentlig synkronisering og rapportgenerering."""

    console.print("[bold blue]═══════════════════════════════════════════════════════════[/bold blue]")
    console.print("[bold blue]           UKENTLIG SYNC OG RAPPORT                        [/bold blue]")
    console.print("[bold blue]═══════════════════════════════════════════════════════════[/bold blue]")

    # Bestem hvilken uke vi rapporterer for
    today = datetime.now()

    if week > 0:
        # Spesifikk uke
        year = today.year
        # Finn første dag i spesifisert uke
        jan1 = datetime(year, 1, 1)
        # ISO week 1 starter på mandag
        days_to_week1_monday = (7 - jan1.weekday()) % 7
        if jan1.weekday() > 3:  # Torsdag eller senere
            days_to_week1_monday += 7
        week1_monday = jan1 + timedelta(days=days_to_week1_monday - 7)
        week_start = week1_monday + timedelta(weeks=week - 1)
    else:
        # Forrige uke (mandag til søndag)
        days_since_monday = today.weekday()
        week_start = today - timedelta(days=days_since_monday + 7)

    week_end = week_start + timedelta(days=6)
    week_num = week_start.isocalendar()[1]
    year = week_start.isocalendar()[0]

    console.print(f"\nRapport for uke {week_num} ({week_start.strftime('%d.%m')} – {week_end.strftime('%d.%m.%Y')})\n")

    # 1. Sync
    if not no_sync:
        sync_results = run_sync()
    else:
        console.print("[yellow]Synkronisering hoppet over (--no-sync)[/yellow]")

    # 2. Hent data
    console.print("\n[bold blue]2. HENTER DATA[/bold blue]\n")

    planned = parse_plan(week_start, week_end)
    console.print(f"  Planlagte økter: {len(planned)}")

    actual = get_actual_workouts(week_start, week_end)
    console.print(f"  Faktiske økter: {len(actual)}")

    metrics = get_daily_metrics(week_start, week_end)
    console.print(f"  Daglige metrikker: {len(metrics)} dager")

    hrv_baseline = get_hrv_baseline(week_end)
    console.print(f"  HRV baseline (28d): {hrv_baseline:.0f}")

    # 3. Analyser
    console.print("\n[bold blue]3. ANALYSERER[/bold blue]\n")

    comparisons = compare_planned_vs_actual(planned, actual)
    acwr = calculate_acwr(metrics)

    # HRV vurdering
    hrv_values = [m.hrv_value for m in metrics if m.hrv_value > 0]
    hrv_avg = sum(hrv_values) / len(hrv_values) if hrv_values else 0
    hrv_ok = hrv_avg >= hrv_baseline * 0.9 if hrv_baseline > 0 else True

    # Readiness vurdering
    readiness_values = [m.training_readiness for m in metrics if m.training_readiness > 0]
    readiness_avg = sum(readiness_values) / len(readiness_values) if readiness_values else 50
    ready_for_next = readiness_avg >= 45 and acwr <= 1.3

    # Generer justeringsforslag
    adjustments = generate_adjustments(comparisons, metrics, hrv_baseline, acwr)

    # Generer innsikter
    insights = generate_insights()

    # 4. Generer rapport
    console.print("\n[bold blue]4. GENERERER RAPPORT[/bold blue]\n")

    report = generate_report(
        week_num, year, week_start, week_end,
        planned, actual, metrics, comparisons,
        hrv_baseline, acwr, adjustments, insights
    )

    # Lagre rapport
    RAPPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RAPPORT_DIR / f"ukerapport_{year}-W{week_num:02d}.md"
    with open(report_path, 'w') as f:
        f.write(report)

    console.print(f"  Rapport lagret: [green]{report_path}[/green]")

    # 5. Terminal-status
    console.print("\n[bold blue]5. STATUS[/bold blue]\n")

    planned_km = sum(p.distance_km for p in planned)
    actual_km = sum(a.distance_km for a in actual)

    print_terminal_summary(week_num, planned_km, actual_km, hrv_ok, ready_for_next)

    # Vis justeringsforslag
    if adjustments:
        console.print("\n[bold]Justeringsforslag:[/bold]")
        for adj in adjustments:
            console.print(f"  {adj}")

    console.print("\n[dim]Vent på bekreftelse før plan endres.[/dim]")


if __name__ == "__main__":
    main()
