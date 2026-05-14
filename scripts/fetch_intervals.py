#!/usr/bin/env python3
"""
Henter intervall/lap-data fra Garmin Connect.

Identifiserer økter med manuelle laps og lagrer drag-detaljer
(pace, HR, distanse, varighet) for analyse og optimalisering.

Bruk:
    python scripts/fetch_intervals.py              # Siste 8 uker
    python scripts/fetch_intervals.py --weeks 4   # Siste 4 uker
"""

import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import time

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from dotenv import load_dotenv

# Last miljøvariabler
PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
load_dotenv(PROJECT_ROOT / ".env")

console = Console()

DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
INTERVALS_DIR = PROJECT_ROOT / "data" / "raw" / "garmin" / "intervals"
INTERVALS_DIR.mkdir(parents=True, exist_ok=True)


def init_intervals_table():
    """Oppretter tabell for intervalldata."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interval_laps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id INTEGER,
            garmin_id INTEGER,
            activity_date DATE,
            activity_name TEXT,
            lap_index INTEGER,
            lap_type TEXT,
            distance_m REAL,
            duration_s REAL,
            pace_s_per_km REAL,
            avg_hr INTEGER,
            max_hr INTEGER,
            avg_cadence REAL,
            elevation_gain REAL,
            elevation_loss REAL,
            rest_after_s REAL,
            UNIQUE(garmin_id, lap_index)
        )
    """)

    # Indeks for rask oppslag
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_interval_laps_date
        ON interval_laps(activity_date)
    """)

    conn.commit()
    conn.close()


def get_garmin_client():
    """Logger inn på Garmin Connect."""
    from garminconnect import Garmin, GarminConnectAuthenticationError

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    if not email or not password:
        console.print("[red]GARMIN_EMAIL og GARMIN_PASSWORD må settes i .env[/red]")
        sys.exit(1)

    try:
        garmin = Garmin(email, password)
        garmin.login()
        return garmin
    except GarminConnectAuthenticationError as e:
        console.print(f"[red]Innlogging feilet: {e}[/red]")
        sys.exit(1)


def get_activities_with_potential_laps(weeks: int) -> list:
    """Henter løpeaktiviteter fra databasen som kan ha lap-data."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    start_date = (datetime.now() - timedelta(weeks=weeks)).strftime('%Y-%m-%d')

    cursor.execute("""
        SELECT id, garmin_id, start_date, name, distance_km, moving_time_s
        FROM activities
        WHERE garmin_id IS NOT NULL
        AND sport IN ('running', 'Run', 'trail_running')
        AND date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= ?
        ORDER BY start_date DESC
    """, (start_date,))

    activities = cursor.fetchall()
    conn.close()

    return activities


def fetch_laps_for_activity(garmin, garmin_id: int) -> Optional[dict]:
    """Henter lap-data for en aktivitet."""
    try:
        time.sleep(2)  # Rate limiting
        splits = garmin.get_activity_splits(garmin_id)
        return splits
    except Exception as e:
        console.print(f"[yellow]Kunne ikke hente laps for {garmin_id}: {e}[/yellow]")
        return None


def is_interval_workout(laps: list) -> bool:
    """
    Sjekker om økten har manuelle laps som tyder på intervalltrening.

    Kriterier:
    - Mer enn 2 laps (ikke bare start/slutt)
    - Variasjon i pace mellom laps
    """
    if len(laps) < 3:
        return False

    # Sjekk pace-variasjon
    paces = []
    for lap in laps:
        dist = lap.get('distance', 0)
        dur = lap.get('duration', 0)
        if dist > 100 and dur > 30:  # Ignorer veldig korte segmenter
            pace = dur / (dist / 1000) if dist > 0 else 0
            paces.append(pace)

    if len(paces) < 2:
        return False

    # Hvis pace varierer med mer enn 30 sek/km mellom laps, er det trolig intervaller
    pace_range = max(paces) - min(paces)
    return pace_range > 30


def classify_lap_type(lap: dict, all_laps: list, idx: int) -> str:
    """Klassifiserer et lap som 'work' eller 'rest' basert på pace."""
    dist = lap.get('distance', 0)
    dur = lap.get('duration', 0)

    if dist < 50:
        return 'pause'

    pace = dur / (dist / 1000) if dist > 0 else 999

    # Beregn gjennomsnittlig pace for alle laps
    all_paces = []
    for l in all_laps:
        d = l.get('distance', 0)
        t = l.get('duration', 0)
        if d > 100:
            all_paces.append(t / (d / 1000))

    if not all_paces:
        return 'unknown'

    avg_pace = sum(all_paces) / len(all_paces)

    # Hvis pace er mer enn 20% saktere enn snitt, er det trolig pause/jog
    if pace > avg_pace * 1.2:
        return 'rest'
    elif pace < avg_pace * 0.9:
        return 'work'
    else:
        return 'work'  # Default til work


def save_laps_to_db(activity_id: int, garmin_id: int, activity_date: str,
                    activity_name: str, laps: list):
    """Lagrer lap-data til databasen."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for idx, lap in enumerate(laps):
        dist = lap.get('distance', 0)
        dur = lap.get('duration', 0)
        pace = dur / (dist / 1000) if dist > 0 else None

        lap_type = classify_lap_type(lap, laps, idx)

        # Beregn pause etter dette lappet (tid til neste lap starter)
        rest_after = None
        if idx < len(laps) - 1:
            next_lap = laps[idx + 1]
            if classify_lap_type(next_lap, laps, idx + 1) == 'rest':
                rest_after = next_lap.get('duration', 0)

        cursor.execute("""
            INSERT OR REPLACE INTO interval_laps (
                activity_id, garmin_id, activity_date, activity_name,
                lap_index, lap_type, distance_m, duration_s, pace_s_per_km,
                avg_hr, max_hr, avg_cadence, elevation_gain, elevation_loss,
                rest_after_s
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            activity_id, garmin_id, activity_date[:10], activity_name,
            idx + 1, lap_type, dist, dur, pace,
            lap.get('averageHR'), lap.get('maxHR'),
            lap.get('averageRunCadence'),
            lap.get('elevationGain'), lap.get('elevationLoss'),
            rest_after
        ))

    conn.commit()
    conn.close()


def generate_intervals_report(weeks: int):
    """Genererer rapport over intervalldata."""
    conn = sqlite3.connect(DB_PATH)

    start_date = (datetime.now() - timedelta(weeks=weeks)).strftime('%Y-%m-%d')

    # Hent alle work-laps
    query = """
        SELECT
            activity_date,
            activity_name,
            lap_index,
            distance_m,
            duration_s,
            pace_s_per_km,
            avg_hr,
            max_hr,
            rest_after_s
        FROM interval_laps
        WHERE lap_type = 'work'
        AND activity_date >= ?
        ORDER BY activity_date DESC, lap_index
    """

    import pandas as pd
    df = pd.read_sql_query(query, conn, params=(start_date,))
    conn.close()

    if len(df) == 0:
        console.print("[yellow]Ingen intervalldata funnet[/yellow]")
        return

    # Formater pace
    def format_pace(pace_s):
        if pd.isna(pace_s):
            return "—"
        mins = int(pace_s // 60)
        secs = int(pace_s % 60)
        return f"{mins}:{secs:02d}"

    # Grupper etter økt
    console.print(f"\n[bold]Intervalldata siste {weeks} uker[/bold]\n")

    for date in df['activity_date'].unique():
        session = df[df['activity_date'] == date]
        name = session.iloc[0]['activity_name']

        console.print(f"[bold cyan]{date}[/bold cyan] – {name}")

        table = Table(show_header=True, header_style="bold")
        table.add_column("Drag", width=6)
        table.add_column("Dist", width=8)
        table.add_column("Pace", width=8)
        table.add_column("HR", width=6)  # Kun slutt-HR (max)
        table.add_column("Pause", width=8)

        for _, row in session.iterrows():
            dist_str = f"{row['distance_m']:.0f}m" if row['distance_m'] < 1500 else f"{row['distance_m']/1000:.2f}km"
            pace_str = format_pace(row['pace_s_per_km'])

            # Kun slutt-HR (max) - det du jobber deg opp mot
            hr_str = f"{int(row['max_hr'])}" if pd.notna(row['max_hr']) else "—"

            rest_str = f"{int(row['rest_after_s'])}s" if pd.notna(row['rest_after_s']) else "—"

            table.add_row(
                str(row['lap_index']),
                dist_str,
                pace_str,
                hr_str,
                rest_str
            )

        console.print(table)
        console.print()

    # Oppsummering
    console.print("[bold]Oppsummering:[/bold]")

    # Grupper etter distanse-kategori
    df['dist_category'] = df['distance_m'].apply(
        lambda x: '400m' if x < 500 else ('1000m' if x < 1200 else ('1500m+' if x < 2000 else '2000m+'))
    )

    summary = df.groupby('dist_category').agg({
        'pace_s_per_km': ['mean', 'min', 'count'],
        'max_hr': 'mean'  # Snitt av slutt-HR per drag
    }).round(1)

    console.print(f"  Totalt {len(df)} work-drag fra {df['activity_date'].nunique()} økter")

    for cat in ['400m', '1000m', '1500m+', '2000m+']:
        if cat in summary.index:
            avg_pace = summary.loc[cat, ('pace_s_per_km', 'mean')]
            best_pace = summary.loc[cat, ('pace_s_per_km', 'min')]
            count = int(summary.loc[cat, ('pace_s_per_km', 'count')])
            max_hr = summary.loc[cat, ('max_hr', 'mean')]

            console.print(f"  {cat}: {count} drag, snitt {format_pace(avg_pace)}, best {format_pace(best_pace)}, HR {max_hr:.0f}")


@click.command()
@click.option("--weeks", default=8, help="Antall uker å hente (default: 8)")
@click.option("--report-only", is_flag=True, help="Bare vis rapport, ikke hent ny data")
def main(weeks: int, report_only: bool):
    """Henter og analyserer intervalldata."""

    console.print(f"[bold blue]Intervalldata – siste {weeks} uker[/bold blue]\n")

    init_intervals_table()

    if report_only:
        generate_intervals_report(weeks)
        return

    # Hent aktiviteter
    activities = get_activities_with_potential_laps(weeks)
    console.print(f"Fant {len(activities)} løpeaktiviteter")

    # Logg inn på Garmin
    garmin = get_garmin_client()
    console.print("[green]Logget inn på Garmin[/green]\n")

    interval_sessions = 0
    total_laps = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Henter lap-data...", total=len(activities))

        for act_id, garmin_id, start_date, name, dist_km, time_s in activities:
            progress.update(task, description=f"Sjekker: {name[:30]}...")

            # Hent laps
            splits_data = fetch_laps_for_activity(garmin, garmin_id)

            if splits_data and 'lapDTOs' in splits_data:
                laps = splits_data['lapDTOs']

                if is_interval_workout(laps):
                    interval_sessions += 1
                    total_laps += len(laps)

                    # Lagre rå data
                    raw_file = INTERVALS_DIR / f"{start_date[:10]}_{garmin_id}.json"
                    with open(raw_file, 'w') as f:
                        json.dump(splits_data, f, indent=2, default=str)

                    # Lagre til database
                    save_laps_to_db(act_id, garmin_id, start_date, name, laps)

                    console.print(f"  [green]✓[/green] {name}: {len(laps)} laps")

            progress.advance(task)

    console.print(f"\n[bold green]Ferdig![/bold green]")
    console.print(f"  Intervalløkter funnet: {interval_sessions}")
    console.print(f"  Totalt laps lagret: {total_laps}")

    # Vis rapport
    if interval_sessions > 0:
        generate_intervals_report(weeks)


if __name__ == "__main__":
    main()
