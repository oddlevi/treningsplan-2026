#!/usr/bin/env python3
"""
Henter aktiviteter og metrikker fra Garmin Connect.
MAKSIMAL forsiktighet mot rate-limiting.

Bruk:
    python scripts/fetch_garmin.py                 # Hent full historikk (default)
    python scripts/fetch_garmin.py --days 7        # Test med siste 7 dager
    python scripts/fetch_garmin.py --resume        # Fortsett fra sjekkpunkt
"""

import os
import sys
import json
import sqlite3
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta
import time

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from dotenv import load_dotenv

# Last miljøvariabler
load_dotenv()

console = Console()

# Prosjektstier
PROJECT_ROOT = Path(__file__).parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw" / "garmin"
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
PROGRESS_FILE = RAW_DATA_DIR / "_progress.json"
ERROR_LOG = RAW_DATA_DIR / "_errors.log"
GARMIN_SESSION_DIR = Path.home() / ".garminconnect"

# Opprett mapper
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Logging
logging.basicConfig(
    filename=str(ERROR_LOG),
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


def log_error(msg: str):
    """Logger feil til fil og konsoll."""
    logging.error(msg)
    console.print(f"[red]FEIL: {msg}[/red]")


def save_progress(progress_data: dict):
    """Lagrer sjekkpunkt."""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress_data, f, indent=2, default=str)


def load_progress() -> Optional[dict]:
    """Laster sjekkpunkt hvis det finnes."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return None


def init_database():
    """Oppretter databasetabeller."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY,
            garmin_id INTEGER UNIQUE,
            start_date TIMESTAMP,
            sport TEXT,
            name TEXT,
            distance_km REAL,
            moving_time_s INTEGER,
            elapsed_time_s INTEGER,
            avg_pace_s_per_km REAL,
            avg_hr INTEGER,
            max_hr INTEGER,
            elevation_gain_m REAL,
            avg_cadence REAL,
            perceived_effort INTEGER,
            is_race BOOLEAN,
            training_load REAL,
            aerobic_te REAL,
            anaerobic_te REAL,
            vo2max_estimate REAL,
            has_hrv BOOLEAN DEFAULT 0,
            has_readiness BOOLEAN DEFAULT 0,
            has_training_load BOOLEAN DEFAULT 0,
            ground_contact_time_ms REAL,
            vertical_oscillation_cm REAL,
            stride_length_m REAL,
            primary_benefit TEXT,
            secondary_benefit TEXT,
            source TEXT DEFAULT 'garmin',
            raw_json TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date DATE PRIMARY KEY,
            hrv_status TEXT,
            hrv_weekly_avg REAL,
            hrv_value REAL,
            training_readiness INTEGER,
            body_battery_max INTEGER,
            body_battery_min INTEGER,
            sleep_score INTEGER,
            sleep_hours REAL,
            resting_hr INTEGER,
            stress_avg INTEGER,
            steps INTEGER,
            training_status TEXT,
            acute_load REAL,
            chronic_load REAL,
            load_ratio REAL,
            vo2max_running REAL,
            raw_json TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS splits (
            activity_id INTEGER,
            split_km INTEGER,
            pace_s_per_km REAL,
            hr INTEGER,
            elevation_change_m REAL,
            PRIMARY KEY (activity_id, split_km)
        )
    """)

    conn.commit()
    conn.close()


def get_garmin_client():
    """Logger inn på Garmin Connect med token-caching."""
    from garminconnect import Garmin, GarminConnectAuthenticationError

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    if not email or not password:
        console.print("[red]GARMIN_EMAIL og GARMIN_PASSWORD må settes i .env[/red]")
        sys.exit(1)

    console.print(f"Logger inn som {email}...")

    # Enkel innlogging med credentials (garminconnect håndterer caching internt)
    try:
        garmin = Garmin(email, password)
        garmin.login()
        console.print("[green]Innlogget på Garmin Connect[/green]")
        return garmin
    except GarminConnectAuthenticationError as e:
        console.print(f"[red]INNLOGGING FEILET: {e}[/red]")
        console.print("[red]STOPPER - ikke prøv igjen med samme credentials![/red]")
        console.print("[yellow]Sjekk: riktig passord? MFA aktivert? Prøv å logge inn på connect.garmin.com først.[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Uventet feil ved innlogging: {e}[/red]")
        sys.exit(1)


def api_call_with_backoff(func, *args, max_retries=3, **kwargs):
    """Utfører API-kall med eksponentiell backoff."""
    backoff_times = [60, 120, 300, 600]  # 1, 2, 5, 10 minutter

    for attempt in range(max_retries):
        try:
            time.sleep(2)  # 2 sek pause mellom ALLE requests
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            error_msg = str(e)

            if "429" in error_msg or "rate" in error_msg.lower():
                if attempt < len(backoff_times):
                    wait = backoff_times[attempt]
                    console.print(f"[yellow]Rate limit - venter {wait}s (forsøk {attempt+1}/{max_retries})[/yellow]")
                    time.sleep(wait)
                else:
                    log_error(f"Rate limit etter {max_retries} forsøk: {error_msg}")
                    return None
            else:
                log_error(f"API-feil (forsøk {attempt+1}): {error_msg}")
                if attempt < max_retries - 1:
                    time.sleep(30)
                else:
                    return None

    return None


def save_raw_data(data: dict, category: str, filename: str):
    """Lagrer rå JSON."""
    category_dir = RAW_DATA_DIR / category
    category_dir.mkdir(parents=True, exist_ok=True)
    filepath = category_dir / f"{filename}.json"
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)


def fetch_and_save_activities(garmin, start_date: datetime, end_date: datetime, progress_data: dict):
    """Henter aktiviteter i batcher."""
    console.print(f"\n[bold]Henter aktiviteter fra {start_date.date()} til {end_date.date()}[/bold]")

    all_activities = []
    offset = progress_data.get("activities_offset", 0)
    batch_size = 50

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as prog:
        task = prog.add_task("Henter aktiviteter...", total=None)

        while True:
            prog.update(task, description=f"Henter aktiviteter (offset {offset})...")

            batch = api_call_with_backoff(garmin.get_activities, offset, batch_size)

            if batch is None:
                log_error(f"Kunne ikke hente aktiviteter ved offset {offset}")
                break

            if not batch:
                break

            # Filtrer på dato
            for activity in batch:
                try:
                    act_date = datetime.fromisoformat(activity["startTimeLocal"].replace("Z", ""))
                    if act_date < start_date:
                        # Ferdig - vi har gått forbi startdato
                        break
                    if act_date <= end_date:
                        all_activities.append(activity)
                        # Lagre rå data
                        save_raw_data(activity, "activities", f"{act_date.date()}_{activity['activityId']}")
                except Exception as e:
                    log_error(f"Feil ved parsing av aktivitet: {e}")

            # Sjekk om vi har gått forbi startdato
            if batch:
                oldest = datetime.fromisoformat(batch[-1]["startTimeLocal"].replace("Z", ""))
                if oldest < start_date:
                    break

            offset += batch_size

            # Lagre sjekkpunkt
            progress_data["activities_offset"] = offset
            progress_data["activities_count"] = len(all_activities)
            save_progress(progress_data)

            # 10 sek pause mellom batcher
            time.sleep(10)

    console.print(f"[green]Hentet {len(all_activities)} aktiviteter[/green]")
    return all_activities


def normalize_datetime(start_date_str: str) -> Optional[datetime]:
    """
    Konverterer start_date til datetime-objekt.
    Håndterer både:
    - '2026-05-09 21:53:11' (Garmin-format)
    - '2026-05-09T19:53:11Z' (Strava ISO-format)
    """
    if start_date_str is None:
        return None

    try:
        # Prøv Strava ISO-format først
        if 'T' in start_date_str:
            clean = start_date_str.replace('T', ' ').replace('Z', '')
            return datetime.strptime(clean, '%Y-%m-%d %H:%M:%S')
        else:
            # Garmin-format
            return datetime.strptime(start_date_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        # Fallback: prøv bare dato
        try:
            return datetime.strptime(start_date_str[:10], '%Y-%m-%d')
        except ValueError:
            return None


# Tidsvindu for å anse to aktiviteter som samme tur (minutter)
# 180 min pga tidssone-forskjell: Strava=UTC, Garmin=lokal tid (UTC+1/+2)
DUPLICATE_WINDOW_MINUTES = 180


def find_existing_activity(cursor, start_date: str, distance_km: float, moving_time_s: int) -> Optional[int]:
    """
    Finner eksisterende aktivitet basert på matching-kriterier.

    DUPLIKAT-FOREBYGGING v2: Aktiviteter fra Strava og Garmin kan være samme økt.
    Vi matcher på dato + avrundet distanse + 60-minutters tidsvindu.

    Hvis to aktiviteter har samme dato + distanse men starter >60 min fra hverandre,
    er de SEPARATE turer (f.eks. morgen + kveld) og beholdes begge.
    """
    # Hent alle kandidater med samme dato og avrundet distanse
    cursor.execute("""
        SELECT id, start_date FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) = date(CASE
            WHEN ? LIKE '%T%' THEN replace(replace(?, 'T', ' '), 'Z', '')
            ELSE ?
        END)
        AND ROUND(distance_km, 1) = ROUND(?, 1)
    """, (start_date, start_date, start_date, distance_km))

    candidates = cursor.fetchall()
    if not candidates:
        return None

    # Sjekk tidsavstand - finn aktivitet innen 60 minutter
    new_time = normalize_datetime(start_date)
    if new_time is None:
        # Kan ikke parse tid, fall tilbake til første match
        return candidates[0][0]

    for existing_id, existing_start in candidates:
        existing_time = normalize_datetime(existing_start)
        if existing_time is None:
            continue

        diff_minutes = abs((new_time - existing_time).total_seconds() / 60)
        if diff_minutes <= DUPLICATE_WINDOW_MINUTES:
            return existing_id

    # Ingen match innen 60 min = dette er en separat tur
    return None


def merge_activity_fields(cursor, existing_id: int, new_data: dict):
    """
    Oppdaterer eksisterende aktivitet med nye felt, men overskriver IKKE eksisterende data med NULL.

    MERGE-LOGIKK: Garmin og Strava har ulike felter. Ved merge beholder vi verdier
    fra begge kilder - nye verdier fyller kun inn der det er NULL.
    Garmin-data prioriteres fordi det har trening-spesifikke metrikker (training_load, TE, VO2max).
    """
    # Hent eksisterende verdier
    cursor.execute("SELECT * FROM activities WHERE id = ?", (existing_id,))
    existing = cursor.fetchone()
    if not existing:
        return

    columns = [desc[0] for desc in cursor.description]
    existing_dict = dict(zip(columns, existing))

    # Bygg UPDATE - Garmin-data overskriver NULL-verdier OG oppdaterer source til garmin
    updates = []
    values = []
    garmin_specific_fields = (
        'garmin_id', 'training_load', 'aerobic_te', 'anaerobic_te', 'vo2max_estimate',
        'has_training_load', 'ground_contact_time_ms', 'vertical_oscillation_cm',
        'stride_length_m', 'primary_benefit', 'secondary_benefit'
    )
    for col, new_val in new_data.items():
        if col in existing_dict and new_val is not None:
            # Overskriv hvis eksisterende er NULL, eller for Garmin-spesifikke felt
            if existing_dict[col] is None or col in garmin_specific_fields:
                updates.append(f"{col} = ?")
                values.append(new_val)

    # Oppdater source til garmin hvis vi legger til Garmin-data
    if 'garmin_id' in new_data and new_data['garmin_id'] is not None:
        updates.append("source = 'garmin'")

    if updates:
        values.append(existing_id)
        cursor.execute(f"UPDATE activities SET {', '.join(updates)} WHERE id = ?", values)


def fetch_activity_laps(garmin, activity_id: int) -> list:
    """Henter intervall/lap-data for en aktivitet."""
    try:
        # Bruk get_activity_splits som returnerer lapDTOs
        splits = api_call_with_backoff(garmin.get_activity_splits, activity_id)
        if splits and "lapDTOs" in splits:
            laps = splits.get("lapDTOs", [])
            if laps:
                console.print(f"[dim]  Hentet {len(laps)} laps for aktivitet {activity_id}[/dim]")
                return laps
    except Exception as e:
        log_error(f"Kunne ikke hente laps for {activity_id}: {e}")
    return []


def save_activity_laps_to_db(garmin_id: int, activity_date: str, activity_name: str, laps: list):
    """Lagrer lap-data til interval_laps tabellen."""
    if not laps:
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Slett eksisterende laps for denne aktiviteten
    cursor.execute("DELETE FROM interval_laps WHERE garmin_id = ?", (garmin_id,))

    for i, lap in enumerate(laps):
        # Garmin API feltnavn (fra lapDTOs)
        distance_m = lap.get("distance", 0)
        duration_s = lap.get("duration") or lap.get("movingDuration") or 0

        avg_hr = lap.get("averageHR")
        max_hr = lap.get("maxHR")
        avg_cadence = lap.get("averageRunCadence")

        # Beregn pace
        pace_s_per_km = (duration_s / distance_m * 1000) if distance_m > 0 else None

        # Bestem lap_type basert på intensityType eller pace
        intensity_type = lap.get("intensityType", "")
        if intensity_type == "REST" or intensity_type == "RECOVERY":
            lap_type = "rest"
        elif intensity_type == "WARMUP":
            lap_type = "warmup"
        elif intensity_type == "COOLDOWN":
            lap_type = "cooldown"
        elif pace_s_per_km:
            if pace_s_per_km > 360:  # Saktere enn 6:00/km
                lap_type = "rest"
            elif pace_s_per_km > 300:  # 5:00-6:00/km
                lap_type = "pause"
            else:
                lap_type = "work"
        else:
            lap_type = "work"

        lap_index = lap.get("lapIndex", i + 1)

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO interval_laps (
                    garmin_id, activity_date, activity_name, lap_index, lap_type,
                    distance_m, duration_s, pace_s_per_km, avg_hr, max_hr, avg_cadence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                garmin_id, activity_date, activity_name, lap_index, lap_type,
                distance_m, duration_s, pace_s_per_km, avg_hr, max_hr, avg_cadence
            ))
        except Exception as e:
            log_error(f"Feil ved lagring av lap {i} for {garmin_id}: {e}")

    conn.commit()
    conn.close()


def save_activity_to_db(activity: dict, garmin=None):
    """Lagrer aktivitet til database med upsert-logikk."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    garmin_id = activity["activityId"]
    start_date = activity.get("startTimeLocal", "")
    sport = activity.get("activityType", {}).get("typeKey", "unknown")
    name = activity.get("activityName", "")

    distance_km = (activity.get("distance") or 0) / 1000
    moving_time_s = int(activity.get("movingDuration") or activity.get("duration") or 0)
    elapsed_time_s = int(activity.get("duration") or 0)
    avg_pace = moving_time_s / distance_km if distance_km > 0 else None

    avg_hr = activity.get("averageHR")
    max_hr = activity.get("maxHR")
    elevation = activity.get("elevationGain")
    cadence = activity.get("averageRunningCadenceInStepsPerMinute")

    training_load = activity.get("activityTrainingLoad")
    aerobic_te = activity.get("aerobicTrainingEffect")
    anaerobic_te = activity.get("anaerobicTrainingEffect")
    vo2max = activity.get("vO2MaxValue")

    has_training_load = training_load is not None

    # Running Dynamics og Benefits finnes direkte i aktivitetslisten
    ground_contact_time_ms = activity.get("avgGroundContactTime")
    vertical_oscillation_cm = activity.get("avgVerticalOscillation")
    stride_length_m = activity.get("avgStrideLength")
    if stride_length_m:
        stride_length_m = stride_length_m / 100  # Garmin gir cm, vi vil ha meter
    primary_benefit = activity.get("trainingEffectLabel")
    # Secondary benefit finnes ikke direkte - sett til None
    secondary_benefit = None

    # UPSERT: Sjekk om aktivitet med samme dato/distanse/tid finnes
    existing_id = find_existing_activity(cursor, start_date, distance_km, moving_time_s)

    if existing_id:
        # Merge Garmin-data inn i eksisterende rad (Garmin-felt har prioritet)
        new_data = {
            'garmin_id': garmin_id,
            'name': name,
            'sport': sport,
            'avg_hr': avg_hr,
            'max_hr': max_hr,
            'elevation_gain_m': elevation,
            'avg_cadence': cadence,
            'training_load': training_load,
            'aerobic_te': aerobic_te,
            'anaerobic_te': anaerobic_te,
            'vo2max_estimate': vo2max,
            'has_training_load': has_training_load,
            'ground_contact_time_ms': ground_contact_time_ms,
            'vertical_oscillation_cm': vertical_oscillation_cm,
            'stride_length_m': stride_length_m,
            'primary_benefit': primary_benefit,
            'secondary_benefit': secondary_benefit,
            'raw_json': json.dumps(activity, default=str),
        }
        merge_activity_fields(cursor, existing_id, new_data)
    else:
        # Ny aktivitet - INSERT
        cursor.execute("""
            INSERT INTO activities (
                garmin_id, start_date, sport, name, distance_km, moving_time_s, elapsed_time_s,
                avg_pace_s_per_km, avg_hr, max_hr, elevation_gain_m, avg_cadence,
                training_load, aerobic_te, anaerobic_te, vo2max_estimate,
                has_training_load, ground_contact_time_ms, vertical_oscillation_cm, stride_length_m,
                primary_benefit, secondary_benefit, source, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'garmin', ?)
        """, (
            garmin_id, start_date, sport, name, distance_km, moving_time_s, elapsed_time_s,
            avg_pace, avg_hr, max_hr, elevation, cadence,
            training_load, aerobic_te, anaerobic_te, vo2max,
            has_training_load,
            ground_contact_time_ms,
            vertical_oscillation_cm,
            stride_length_m,
            primary_benefit,
            secondary_benefit,
            json.dumps(activity, default=str)
        ))

    conn.commit()
    conn.close()

    # Hent og lagre laps for løpeaktiviteter
    if garmin and sport in ('running', 'trail_running') and distance_km >= 3:
        laps = fetch_activity_laps(garmin, garmin_id)
        if laps:
            save_activity_laps_to_db(garmin_id, start_date[:10], name, laps)


def fetch_daily_metrics_batch(garmin, start_date: datetime, end_date: datetime, progress_data: dict):
    """Henter daglige metrikker i 30-dagers batcher."""
    console.print(f"\n[bold]Henter daglige metrikker fra {start_date.date()} til {end_date.date()}[/bold]")

    # Sett opp batcher (30 dager)
    batch_size = 30
    current_start = start_date

    # Gjenoppta fra sjekkpunkt
    if "metrics_current_date" in progress_data:
        current_start = datetime.fromisoformat(progress_data["metrics_current_date"])
        console.print(f"[yellow]Gjenopptar fra {current_start.date()}[/yellow]")

    total_days = (end_date - current_start).days + 1
    days_processed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as prog:
        task = prog.add_task("Henter metrikker...", total=total_days)

        while current_start <= end_date:
            batch_end = min(current_start + timedelta(days=batch_size - 1), end_date)

            prog.update(task, description=f"Batch: {current_start.date()} - {batch_end.date()}")

            # Hent metrikker for hver dag i batchen
            current_date = current_start
            while current_date <= batch_end:
                date_str = current_date.strftime("%Y-%m-%d")
                metrics = {}

                # HRV
                try:
                    hrv = api_call_with_backoff(garmin.get_hrv_data, date_str)
                    if hrv:
                        metrics["hrv"] = hrv
                        metrics["hrv_status"] = hrv.get("hrvSummary", {}).get("status")
                        metrics["hrv_value"] = hrv.get("hrvSummary", {}).get("lastNightAvg")
                        metrics["hrv_weekly_avg"] = hrv.get("hrvSummary", {}).get("weeklyAvg")
                except Exception as e:
                    log_error(f"HRV {date_str}: {e}")

                # Training Readiness
                try:
                    readiness = api_call_with_backoff(garmin.get_training_readiness, date_str)
                    if readiness and isinstance(readiness, list) and readiness:
                        # Finn morgen-readiness (AFTER_WAKEUP_RESET) hvis tilgjengelig
                        morning_entry = next(
                            (r for r in readiness if r.get("inputContext") == "AFTER_WAKEUP_RESET"),
                            None
                        )
                        if morning_entry:
                            metrics["training_readiness"] = morning_entry.get("score")
                        else:
                            # Fallback til første entry
                            metrics["training_readiness"] = readiness[0].get("score")
                except Exception as e:
                    log_error(f"Readiness {date_str}: {e}")

                # Body Battery
                try:
                    bb = api_call_with_backoff(garmin.get_body_battery, date_str)
                    if bb and isinstance(bb, list):
                        values = [x.get("bodyBatteryLevel") for x in bb if x.get("bodyBatteryLevel")]
                        if values:
                            metrics["body_battery_max"] = max(values)
                            metrics["body_battery_min"] = min(values)
                except Exception as e:
                    log_error(f"Body Battery {date_str}: {e}")

                # Søvn
                try:
                    sleep = api_call_with_backoff(garmin.get_sleep_data, date_str)
                    if sleep:
                        # Søvndata ligger i dailySleepDTO
                        daily_sleep = sleep.get("dailySleepDTO", {})
                        if daily_sleep:
                            sleep_seconds = daily_sleep.get("sleepTimeSeconds")
                            # Kun sett sleep_hours hvis vi har faktisk søvndata
                            if sleep_seconds and sleep_seconds > 0:
                                metrics["sleep_hours"] = round(sleep_seconds / 3600, 1)
                            # Sleep score ligger i dailySleepDTO.sleepScores.overall.value
                            sleep_scores = daily_sleep.get("sleepScores", {})
                            if sleep_scores:
                                metrics["sleep_score"] = sleep_scores.get("overall", {}).get("value")
                except Exception as e:
                    log_error(f"Sleep {date_str}: {e}")

                # Hvile-HR
                try:
                    rhr = api_call_with_backoff(garmin.get_rhr_day, date_str)
                    if rhr:
                        metrics["resting_hr"] = rhr.get("restingHeartRate")
                except Exception as e:
                    log_error(f"RHR {date_str}: {e}")

                # Training Load (akutt/kronisk) og Training Status
                try:
                    training_status = api_call_with_backoff(garmin.get_training_status, date_str)
                    if training_status and isinstance(training_status, dict):
                        metrics["acute_load"] = training_status.get("weeklyTrainingLoad")
                        metrics["chronic_load"] = training_status.get("monthlyTrainingLoad")
                        metrics["training_status"] = training_status.get("trainingStatusPhrase")
                        # Beregn load ratio (ACWR)
                        if metrics.get("acute_load") and metrics.get("chronic_load"):
                            metrics["load_ratio"] = round(metrics["acute_load"] / metrics["chronic_load"], 2)
                except Exception as e:
                    log_error(f"Training Status {date_str}: {e}")

                # VO2 Max
                try:
                    vo2_data = api_call_with_backoff(garmin.get_max_metrics, date_str)
                    if vo2_data and isinstance(vo2_data, dict):
                        generic = vo2_data.get("generic", {})
                        metrics["vo2max_running"] = generic.get("vo2MaxPreciseValue")
                except Exception as e:
                    log_error(f"VO2 Max {date_str}: {e}")

                # Stress-nivå
                try:
                    stress = api_call_with_backoff(garmin.get_stress_data, date_str)
                    if stress and isinstance(stress, dict):
                        metrics["stress_avg"] = stress.get("avgStressLevel")
                except Exception as e:
                    log_error(f"Stress {date_str}: {e}")

                # Lagre hvis vi har data
                if metrics:
                    save_raw_data(metrics, "daily", date_str)
                    save_daily_metrics_to_db(current_date, metrics)

                days_processed += 1
                prog.advance(task)
                current_date += timedelta(days=1)

            # Lagre sjekkpunkt etter hver batch
            progress_data["metrics_current_date"] = batch_end.isoformat()
            progress_data["metrics_days_processed"] = days_processed
            save_progress(progress_data)

            # 10 sek pause mellom batcher
            console.print(f"[dim]Batch ferdig. Pause 10s...[/dim]")
            time.sleep(10)

            current_start = batch_end + timedelta(days=1)

    console.print(f"[green]Hentet metrikker for {days_processed} dager[/green]")


def update_activity_flags():
    """Oppdaterer has_hrv, has_readiness, has_training_load flagg på aktiviteter."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Hent alle datoer med HRV-data
    cursor.execute("SELECT date FROM daily_metrics WHERE hrv_value IS NOT NULL")
    hrv_dates = {row[0] for row in cursor.fetchall()}

    # Hent alle datoer med readiness-data
    cursor.execute("SELECT date FROM daily_metrics WHERE training_readiness IS NOT NULL")
    readiness_dates = {row[0] for row in cursor.fetchall()}

    # Oppdater aktiviteter
    cursor.execute("SELECT garmin_id, start_date FROM activities WHERE source='garmin'")
    for garmin_id, start_date in cursor.fetchall():
        if start_date:
            date_str = start_date[:10]  # YYYY-MM-DD
            has_hrv = 1 if date_str in hrv_dates else 0
            has_readiness = 1 if date_str in readiness_dates else 0

            cursor.execute("""
                UPDATE activities
                SET has_hrv = ?, has_readiness = ?
                WHERE garmin_id = ?
            """, (has_hrv, has_readiness, garmin_id))

    conn.commit()
    conn.close()


def save_daily_metrics_to_db(date: datetime, metrics: dict):
    """Lagrer daglige metrikker."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    date_str = date.strftime("%Y-%m-%d")

    cursor.execute("""
        INSERT OR REPLACE INTO daily_metrics (
            date, hrv_status, hrv_weekly_avg, hrv_value, training_readiness,
            body_battery_max, body_battery_min, sleep_score, sleep_hours,
            resting_hr, stress_avg, acute_load, chronic_load, load_ratio, training_status,
            vo2max_running, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        date_str,
        metrics.get("hrv_status"),
        metrics.get("hrv_weekly_avg"),
        metrics.get("hrv_value"),
        metrics.get("training_readiness"),
        metrics.get("body_battery_max"),
        metrics.get("body_battery_min"),
        metrics.get("sleep_score"),
        metrics.get("sleep_hours"),
        metrics.get("resting_hr"),
        metrics.get("stress_avg"),
        metrics.get("acute_load"),
        metrics.get("chronic_load"),
        metrics.get("load_ratio"),
        metrics.get("training_status"),
        metrics.get("vo2max_running"),
        json.dumps(metrics, default=str),
    ))

    conn.commit()
    conn.close()


@click.command()
@click.option("--days", default=0, type=int, help="Antall dager å hente (default: full historikk)")
@click.option("--resume", is_flag=True, help="Fortsett fra sjekkpunkt")
@click.option("--activities-only", is_flag=True, help="Kun hent aktiviteter")
@click.option("--metrics-only", is_flag=True, help="Kun hent daglige metrikker")
def main(days: int, resume: bool, activities_only: bool, metrics_only: bool):
    """Henter data fra Garmin Connect med maksimal forsiktighet."""

    console.print("[bold blue]Garmin Connect Sync[/bold blue]")
    console.print("[dim]Maks forsiktighet mot rate-limiting aktivert[/dim]\n")

    # Initialiser database
    init_database()

    # Håndter sjekkpunkt
    progress_data = {}
    if resume:
        progress_data = load_progress() or {}
        if progress_data:
            console.print(f"[yellow]Gjenopptar fra sjekkpunkt: {PROGRESS_FILE}[/yellow]")
        else:
            console.print("[yellow]Ingen sjekkpunkt funnet, starter fra begynnelsen[/yellow]")

    # Logg inn
    garmin = get_garmin_client()

    # Bestem tidsperiode
    end_date = datetime.now()

    if days > 0:
        start_date = end_date - timedelta(days=days)
        console.print(f"[bold]Siste {days} dager: {start_date.date()} → {end_date.date()}[/bold]")
    else:
        # Default: full historikk fra 2020
        start_date = datetime(2020, 1, 1)
        console.print(f"[bold]FULL HISTORIKK: {start_date.date()} → {end_date.date()}[/bold]")

    progress_data["start_date"] = start_date.isoformat()
    progress_data["end_date"] = end_date.isoformat()
    progress_data["started_at"] = datetime.now().isoformat()
    save_progress(progress_data)

    # Hent aktiviteter
    if not metrics_only:
        activities = fetch_and_save_activities(garmin, start_date, end_date, progress_data)
        for activity in activities:
            save_activity_to_db(activity, garmin)
        progress_data["activities_done"] = True
        save_progress(progress_data)

    # Hent daglige metrikker (begrens til 2 år maks)
    if not activities_only:
        metrics_start = max(start_date, datetime.now() - timedelta(days=730))
        fetch_daily_metrics_batch(garmin, metrics_start, end_date, progress_data)
        progress_data["metrics_done"] = True
        save_progress(progress_data)

    # Oppdater has_hrv og has_readiness flagg på aktiviteter
    console.print("\n[dim]Oppdaterer aktivitetsflagg basert på daglige metrikker...[/dim]")
    update_activity_flags()

    # Oppsummering
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM activities WHERE source='garmin'")
    act_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM daily_metrics")
    metrics_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM activities WHERE source='garmin' AND sport='running'")
    run_count = cursor.fetchone()[0]
    conn.close()

    console.print(f"\n[bold green]FERDIG![/bold green]")
    console.print(f"  Aktiviteter: {act_count} (hvorav {run_count} løping)")
    console.print(f"  Daglige metrikker: {metrics_count} dager")
    console.print(f"  Database: {DB_PATH}")


if __name__ == "__main__":
    main()
