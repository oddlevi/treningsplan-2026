"""
Database-spørringer og databehandling for treningsdashboardet.
Støtter flere brukere med separate datamapper.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent

# Bruker-konfigurasjon
USERS = {
    "odd_levi": {
        "navn": "Odd Levi",
        "data_path": PROJECT_ROOT / "data",  # Eksisterende data
        "mål_10k": "37:00",
        "mål_halv": "1:25:00",
    },
    # Legg til flere brukere her:
    # "bruker_b": {
    #     "navn": "Bruker B",
    #     "data_path": PROJECT_ROOT / "data" / "users" / "bruker_b",
    #     "mål_10k": "40:00",
    #     "mål_halv": "1:35:00",
    # },
}

# Aktiv bruker (settes av app.py)
_current_user = "odd_levi"


def set_current_user(user_id: str):
    """Setter aktiv bruker."""
    global _current_user
    if user_id in USERS:
        _current_user = user_id


def get_current_user() -> str:
    """Returnerer aktiv bruker-ID."""
    return _current_user


def get_user_config() -> dict:
    """Returnerer konfigurasjon for aktiv bruker."""
    return USERS.get(_current_user, USERS["odd_levi"])


def get_db_path() -> Path:
    """Returnerer database-sti for aktiv bruker."""
    config = get_user_config()
    return config["data_path"] / "processed" / "treningsplan.db"


@dataclass
class CurrentMetrics:
    """Nøkkeltall for dashboard-header."""
    vo2max: Optional[float]
    vo2max_change: Optional[float]
    readiness: Optional[int]
    hrv: Optional[float]
    hrv_status: str
    acwr: float


def get_db_connection():
    """Returnerer database-tilkobling for aktiv bruker."""
    return sqlite3.connect(get_db_path())


def get_current_metrics() -> CurrentMetrics:
    """
    Henter nåværende nøkkeltall.
    """
    conn = get_db_connection()

    # Siste VO2 Max og endring
    vo2_df = pd.read_sql_query("""
        SELECT date, vo2max_running
        FROM daily_metrics
        WHERE vo2max_running IS NOT NULL
        ORDER BY date DESC
        LIMIT 30
    """, conn)

    vo2max = None
    vo2max_change = None
    if len(vo2_df) > 0:
        vo2max = vo2_df.iloc[0]['vo2max_running']
        if len(vo2_df) >= 7:
            vo2max_7d_ago = vo2_df.iloc[min(6, len(vo2_df)-1)]['vo2max_running']
            if vo2max_7d_ago:
                vo2max_change = vo2max - vo2max_7d_ago

    # Siste Training Readiness og HRV
    today = datetime.now().strftime('%Y-%m-%d')
    metrics_df = pd.read_sql_query("""
        SELECT date, training_readiness, hrv_weekly_avg, hrv_status
        FROM daily_metrics
        WHERE date <= ?
        ORDER BY date DESC
        LIMIT 1
    """, conn, params=(today,))

    readiness = None
    hrv = None
    hrv_status = "UNKNOWN"
    if len(metrics_df) > 0:
        readiness = metrics_df.iloc[0]['training_readiness']
        hrv = metrics_df.iloc[0]['hrv_weekly_avg']
        hrv_status = metrics_df.iloc[0]['hrv_status'] or "UNKNOWN"

    # ACWR (Akutt:Kronisk belastningsratio)
    acwr = calculate_acwr(conn, today)

    conn.close()

    return CurrentMetrics(
        vo2max=vo2max,
        vo2max_change=vo2max_change,
        readiness=int(readiness) if readiness else None,
        hrv=hrv,
        hrv_status=hrv_status,
        acwr=acwr
    )


def calculate_acwr(conn, date_str: str) -> float:
    """
    Beregner ACWR (Acute:Chronic Workload Ratio).
    Akutt = siste 7 dager, Kronisk = siste 28 dager (gjennomsnitt per uke).
    """
    # Siste 7 dager
    df_7d = pd.read_sql_query("""
        SELECT COALESCE(SUM(distance_km), 0) as km
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
    """, conn, params=(date_str, date_str))
    km_7d = df_7d.iloc[0]['km'] if len(df_7d) > 0 else 0

    # Siste 28 dager
    df_28d = pd.read_sql_query("""
        SELECT COALESCE(SUM(distance_km), 0) as km
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
    """, conn, params=(date_str, date_str))
    km_28d = df_28d.iloc[0]['km'] if len(df_28d) > 0 else 0

    kronisk_per_uke = km_28d / 4 if km_28d > 0 else 1
    return round(km_7d / kronisk_per_uke, 2) if kronisk_per_uke > 0 else 0


def get_weekly_volume(weeks: int = 12) -> pd.DataFrame:
    """
    Henter ukentlig volum for de siste N ukene.
    """
    conn = get_db_connection()

    end_date = datetime.now()
    start_date = end_date - timedelta(weeks=weeks)

    df = pd.read_sql_query("""
        SELECT
            strftime('%Y-W%W', CASE
                WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                ELSE start_date
            END) as week,
            SUM(distance_km) as km,
            COUNT(*) as runs,
            SUM(moving_time_s) / 60.0 as minutes
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= ?
        AND sport IN ('running', 'Run', 'trail_running')
        GROUP BY week
        ORDER BY week
    """, conn, params=(start_date.strftime('%Y-%m-%d'),))

    conn.close()
    return df


def get_pace_at_hr(hr_target: int = 160, months: int = 6) -> pd.DataFrame:
    """
    Henter pace ved gitt HR over tid.
    Brukes for å spore formutvikling (aerob effektivitet).
    """
    conn = get_db_connection()

    end_date = datetime.now()
    start_date = end_date - timedelta(days=months * 30)

    # Henter aktiviteter med HR i målområdet (+/- 5 bpm)
    df = pd.read_sql_query("""
        SELECT
            date(CASE
                WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                ELSE start_date
            END) as date,
            avg_pace_s_per_km as pace,
            avg_hr as hr,
            distance_km
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= ?
        AND sport IN ('running', 'Run', 'trail_running')
        AND avg_hr BETWEEN ? AND ?
        AND avg_pace_s_per_km > 0
        AND distance_km >= 3
        ORDER BY date
    """, conn, params=(start_date.strftime('%Y-%m-%d'), hr_target - 5, hr_target + 5))

    conn.close()

    if len(df) > 0:
        df['date'] = pd.to_datetime(df['date'])
        # Konverter pace fra sekunder til mm:ss format for visning
        df['pace_formatted'] = df['pace'].apply(lambda x: f"{int(x//60)}:{int(x%60):02d}")

    return df


def get_hrv_readiness_history(days: int = 30) -> pd.DataFrame:
    """
    Henter HRV og Training Readiness historikk.
    """
    conn = get_db_connection()

    df = pd.read_sql_query("""
        SELECT
            date,
            hrv_weekly_avg as hrv,
            hrv_status,
            training_readiness as readiness,
            sleep_score,
            resting_hr
        FROM daily_metrics
        WHERE date >= date('now', ?)
        ORDER BY date
    """, conn, params=(f'-{days} days',))

    conn.close()

    if len(df) > 0:
        df['date'] = pd.to_datetime(df['date'])

    return df


def get_load_history(days: int = 30) -> pd.DataFrame:
    """
    Henter belastningshistorikk (akutt/kronisk load).
    """
    conn = get_db_connection()

    df = pd.read_sql_query("""
        SELECT
            date,
            acute_load,
            chronic_load,
            load_ratio
        FROM daily_metrics
        WHERE date >= date('now', ?)
        AND (acute_load IS NOT NULL OR chronic_load IS NOT NULL)
        ORDER BY date
    """, conn, params=(f'-{days} days',))

    conn.close()

    if len(df) > 0:
        df['date'] = pd.to_datetime(df['date'])

    return df


def get_vo2max_history(months: int = 12) -> pd.DataFrame:
    """
    Henter VO2 Max historikk.
    """
    conn = get_db_connection()

    df = pd.read_sql_query("""
        SELECT
            date,
            vo2max_running as vo2max
        FROM daily_metrics
        WHERE date >= date('now', ?)
        AND vo2max_running IS NOT NULL
        ORDER BY date
    """, conn, params=(f'-{months} months',))

    conn.close()

    if len(df) > 0:
        df['date'] = pd.to_datetime(df['date'])

    return df


def estimate_race_times(vo2max: float) -> dict:
    """
    Estimerer løpstider basert på VO2 Max.
    Bruker Jack Daniels' VDOT-formler.
    """
    if not vo2max:
        return {}

    # Forenklet estimering basert på VDOT
    # VO2 Max 56 ≈ 36:30 10k, VO2 Max 60 ≈ 34:00 10k
    # Lineær interpolasjon for enkelthet

    # 10k estimat (sekunder)
    # VO2 Max 50 → ~42:00, VO2 Max 60 → ~34:00, VO2 Max 70 → ~28:00
    time_10k_s = 2520 - (vo2max - 50) * 48  # Forenklet formel

    # Halvmaraton estimat (ca 2.15x 10k-tid for godt trent løper)
    time_half_s = time_10k_s * 2.15

    def format_time(seconds):
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours}:{mins:02d}:{secs:02d}"
        return f"{mins}:{secs:02d}"

    return {
        '10k': format_time(time_10k_s),
        '10k_seconds': time_10k_s,
        'half': format_time(time_half_s),
        'half_seconds': time_half_s
    }


def calculate_goal_progress() -> dict:
    """
    Beregner progresjon mot målene:
    - Sub 35:00 på 10k
    - Sub 1:20:00 på halvmaraton
    """
    conn = get_db_connection()

    # Hent nåværende VO2 Max
    vo2_df = pd.read_sql_query("""
        SELECT vo2max_running
        FROM daily_metrics
        WHERE vo2max_running IS NOT NULL
        ORDER BY date DESC
        LIMIT 1
    """, conn)

    conn.close()

    current_vo2 = vo2_df.iloc[0]['vo2max_running'] if len(vo2_df) > 0 else 56

    # Mål-VO2 for sub 35 10k: ca 62-63
    # Mål-VO2 for sub 1:20 halv: ca 64-65
    target_vo2_10k = 62
    target_vo2_half = 64

    # Progresjon (0-100%)
    # Startpunkt: VO2 Max 56 (nåværende nivå dokumentert)
    start_vo2 = 56

    progress_10k = min(100, max(0, (current_vo2 - start_vo2) / (target_vo2_10k - start_vo2) * 100))
    progress_half = min(100, max(0, (current_vo2 - start_vo2) / (target_vo2_half - start_vo2) * 100))

    # Estimert tid til mål (basert på typisk VO2-økning: ~1-2 poeng per 2-3 måneder)
    vo2_increase_per_month = 0.5  # konservativt
    months_to_10k = max(0, (target_vo2_10k - current_vo2) / vo2_increase_per_month)
    months_to_half = max(0, (target_vo2_half - current_vo2) / vo2_increase_per_month)

    estimates = estimate_race_times(current_vo2)

    return {
        'current_vo2': current_vo2,
        '10k': {
            'target': '35:00',
            'target_seconds': 35 * 60,
            'current_estimate': estimates.get('10k', 'N/A'),
            'current_seconds': estimates.get('10k_seconds', 0),
            'progress_pct': progress_10k,
            'months_remaining': months_to_10k
        },
        'half': {
            'target': '1:20:00',
            'target_seconds': 80 * 60,
            'current_estimate': estimates.get('half', 'N/A'),
            'current_seconds': estimates.get('half_seconds', 0),
            'progress_pct': progress_half,
            'months_remaining': months_to_half
        }
    }


def sync_garmin():
    """
    Syncer data fra Garmin (kaller fetch_garmin.py).
    Bruker aktiv brukers data-mappe.
    """
    import subprocess
    import sys

    fetch_script = PROJECT_ROOT / "scripts" / "fetch_garmin.py"
    config = get_user_config()

    # TODO: Legg til --user flagg når flere brukere støttes
    result = subprocess.run(
        [sys.executable, str(fetch_script), "--days", "2"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env={**dict(__import__('os').environ), "DATA_PATH": str(config["data_path"])}
    )
    return result.returncode == 0, result.stdout + result.stderr


def get_all_users() -> dict:
    """Returnerer alle tilgjengelige brukere."""
    return USERS
