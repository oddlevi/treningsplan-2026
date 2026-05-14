#!/usr/bin/env python3
"""
Genererer "Lær meg å kjenne"-rapport basert på løpehistorikk.

Leser fra data/processed/treningsplan.db og genererer rapport/laer_meg_aa_kjenne.md
med detaljert analyse av løperprofil, mønstre og anbefalinger.

Dekker alle 7 punkter fra CLAUDE.md:
1. Volumhistorikk
2. Pace-utvikling
3. Race-historikk
4. Skadeindikasjoner
5. Mønstre å lære av
6. Sterk-svak-profil
7. Anbefalt utgangspunkt
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import statistics

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
RAPPORT_PATH = PROJECT_ROOT / "rapport" / "laer_meg_aa_kjenne.md"


def get_connection():
    """Oppretter databasetilkobling med normalisering av sport og dato."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Opprett view som normaliserer sport-typer og dato-format
    # Håndterer: 'YYYY-MM-DD HH:MM:SS' (Garmin) og 'YYYY-MM-DDTHH:MM:SSZ' (Strava)
    conn.execute("""
        CREATE TEMP VIEW IF NOT EXISTS running_activities AS
        SELECT
            id,
            garmin_id,
            -- Normaliser dato: håndter både formater
            CASE
                WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                ELSE start_date
            END as start_date,
            date(CASE
                WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                ELSE start_date
            END) as activity_date,
            -- Normaliser sport til 'running' (running, Run, trail_running, treadmill_running)
            CASE
                WHEN lower(sport) IN ('running', 'run', 'trail_running', 'treadmill_running', 'track_running')
                THEN 'running'
                ELSE lower(sport)
            END as sport_normalized,
            sport as sport_original,
            name,
            distance_km,
            moving_time_s,
            elapsed_time_s,
            avg_pace_s_per_km,
            avg_hr,
            max_hr,
            elevation_gain_m,
            avg_cadence,
            perceived_effort,
            is_race,
            training_load,
            aerobic_te,
            anaerobic_te,
            vo2max_estimate,
            has_hrv,
            has_readiness,
            has_training_load,
            source
        FROM activities
        WHERE lower(sport) IN ('running', 'run', 'trail_running', 'treadmill_running', 'track_running')
        AND distance_km > 0.5
        AND moving_time_s > 60
    """)

    return conn


def format_pace(seconds_per_km):
    """Formaterer pace som MM:SS/km."""
    if seconds_per_km is None or seconds_per_km <= 0:
        return "-"
    mins = int(seconds_per_km // 60)
    secs = int(seconds_per_km % 60)
    return f"{mins}:{secs:02d}"


def format_time(seconds):
    """Formaterer tid som HH:MM:SS eller MM:SS."""
    if seconds is None:
        return "-"
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def analyze_volume_history(conn):
    """1. VOLUMHISTORIKK - Total km per år, snitt km/uke, sesongmønster."""
    cursor = conn.cursor()

    # Per år
    cursor.execute("""
        SELECT
            strftime('%Y', activity_date) as year,
            COUNT(*) as sessions,
            ROUND(SUM(distance_km), 1) as total_km,
            ROUND(AVG(distance_km), 1) as avg_km_per_session
        FROM running_activities
        GROUP BY year
        ORDER BY year
    """)
    yearly = cursor.fetchall()

    # Per måned siste 24 måneder
    cursor.execute("""
        SELECT
            strftime('%Y-%m', activity_date) as month,
            COUNT(*) as sessions,
            ROUND(SUM(distance_km), 1) as total_km,
            ROUND(SUM(distance_km) / 4.33, 1) as avg_km_per_week
        FROM running_activities
        WHERE activity_date >= date('now', '-24 months')
        GROUP BY month
        ORDER BY month
    """)
    monthly = cursor.fetchall()

    # Finn topp-måneder og hva som skjedde rett etter
    cursor.execute("""
        WITH monthly_vol AS (
            SELECT
                strftime('%Y-%m', activity_date) as month,
                ROUND(SUM(distance_km), 1) as total_km
            FROM running_activities
            GROUP BY month
        )
        SELECT
            m1.month,
            m1.total_km,
            m2.month as next_month,
            m2.total_km as next_month_km
        FROM monthly_vol m1
        LEFT JOIN monthly_vol m2 ON m2.month = strftime('%Y-%m', date(m1.month || '-01', '+1 month'))
        ORDER BY m1.total_km DESC
        LIMIT 5
    """)
    top_months = cursor.fetchall()

    # Sesongmønster (snitt per måned over alle år)
    cursor.execute("""
        SELECT
            month_num,
            ROUND(AVG(monthly_km), 1) as avg_km
        FROM (
            SELECT
                strftime('%Y-%m', activity_date) as ym,
                CAST(strftime('%m', activity_date) AS INTEGER) as month_num,
                SUM(distance_km) as monthly_km
            FROM running_activities
            GROUP BY ym
        )
        GROUP BY month_num
        ORDER BY month_num
    """)
    seasonal = cursor.fetchall()

    return {
        'yearly': yearly,
        'monthly': monthly,
        'top_months': top_months,
        'seasonal': seasonal
    }


def analyze_pace_development(conn):
    """2. PACE-UTVIKLING - Sone 2, per distanse, VO2 Max trend."""
    cursor = conn.cursor()

    # Pace i sone 2 (HR 130-150) per kvartal siden 2023
    cursor.execute("""
        SELECT
            strftime('%Y', activity_date) || '-Q' || ((CAST(strftime('%m', activity_date) AS INTEGER) - 1) / 3 + 1) as quarter,
            COUNT(*) as sessions,
            ROUND(AVG(avg_pace_s_per_km), 0) as avg_pace,
            ROUND(AVG(avg_hr), 0) as avg_hr
        FROM running_activities
        WHERE avg_hr BETWEEN 130 AND 150
        AND activity_date >= '2023-01-01'
        GROUP BY quarter
        HAVING COUNT(*) >= 3
        ORDER BY quarter
    """)
    zone2_pace = cursor.fetchall()

    # Pace per distanse-bucket over tid
    cursor.execute("""
        SELECT
            CASE
                WHEN distance_km BETWEEN 4.5 AND 5.5 THEN '5k'
                WHEN distance_km BETWEEN 9.5 AND 10.5 THEN '10k'
                WHEN distance_km BETWEEN 20 AND 22 THEN 'Halv'
                WHEN distance_km >= 15 THEN 'Lang (15+km)'
                ELSE 'Annet'
            END as distance_bucket,
            strftime('%Y', activity_date) as year,
            COUNT(*) as sessions,
            ROUND(AVG(avg_pace_s_per_km), 0) as avg_pace,
            ROUND(MIN(avg_pace_s_per_km), 0) as best_pace
        FROM running_activities
        WHERE distance_km >= 4.5 AND avg_pace_s_per_km IS NOT NULL
        GROUP BY distance_bucket, year
        HAVING distance_bucket != 'Annet'
        ORDER BY distance_bucket, year
    """)
    pace_by_distance = cursor.fetchall()

    # VO2 Max trend
    cursor.execute("""
        SELECT
            strftime('%Y-%m', activity_date) as month,
            ROUND(AVG(vo2max_estimate), 1) as vo2max,
            COUNT(*) as samples
        FROM running_activities
        WHERE vo2max_estimate IS NOT NULL AND vo2max_estimate > 0
        GROUP BY month
        ORDER BY month
    """)
    vo2max_trend = cursor.fetchall()

    return {
        'zone2_pace': zone2_pace,
        'pace_by_distance': pace_by_distance,
        'vo2max_trend': vo2max_trend
    }


def analyze_races(conn):
    """3. RACE-HISTORIKK - Alle løp med kontekst 6-12 uker før."""
    cursor = conn.cursor()

    # Finn alle races (markert som race eller rask pace på lengre distanser)
    cursor.execute("""
        SELECT
            activity_date,
            name,
            ROUND(distance_km, 2) as distance_km,
            moving_time_s,
            ROUND(avg_pace_s_per_km, 0) as pace,
            avg_hr,
            max_hr,
            is_race,
            source
        FROM running_activities
        WHERE is_race = 1
           OR (distance_km >= 9 AND avg_pace_s_per_km < 240)
           OR (distance_km >= 5 AND avg_pace_s_per_km < 220)
        ORDER BY activity_date DESC
    """)
    races = cursor.fetchall()

    # For hver race, hent kontekst (6-12 uker før)
    race_contexts = []
    for race in races:
        race_date = race['activity_date']
        cursor.execute("""
            SELECT
                COUNT(*) as sessions,
                ROUND(SUM(distance_km), 1) as total_km,
                ROUND(AVG(distance_km), 1) as avg_km,
                ROUND(SUM(distance_km) / 6.0, 1) as weekly_avg,
                SUM(CASE WHEN avg_hr > 160 THEN 1 ELSE 0 END) as hard_sessions
            FROM running_activities
            WHERE activity_date BETWEEN date(?, '-42 days') AND date(?, '-7 days')
        """, (race_date, race_date))
        context = cursor.fetchone()
        race_contexts.append({
            'race': dict(race),
            'context': dict(context) if context else None
        })

    return race_contexts


def analyze_injury_patterns(conn):
    """4. SKADEINDIKASJONER - Hull i trening, comeback-mønster, volumspiker."""
    cursor = conn.cursor()

    # Finn alle aktivitetsdatoer
    cursor.execute("""
        SELECT DISTINCT activity_date
        FROM running_activities
        ORDER BY activity_date
    """)
    dates = [row['activity_date'] for row in cursor.fetchall()]

    # Finn hull > 14 dager
    gaps = []
    for i in range(1, len(dates)):
        prev = datetime.strptime(dates[i-1], '%Y-%m-%d')
        curr = datetime.strptime(dates[i], '%Y-%m-%d')
        gap_days = (curr - prev).days
        if gap_days > 14:
            # Hent volum før og etter hullet
            cursor.execute("""
                SELECT ROUND(SUM(distance_km), 1) as km
                FROM running_activities
                WHERE activity_date BETWEEN date(?, '-28 days') AND ?
            """, (dates[i-1], dates[i-1]))
            before = cursor.fetchone()['km'] or 0

            cursor.execute("""
                SELECT ROUND(SUM(distance_km), 1) as km
                FROM running_activities
                WHERE activity_date BETWEEN ? AND date(?, '+28 days')
            """, (dates[i], dates[i]))
            after = cursor.fetchone()['km'] or 0

            gaps.append({
                'start': dates[i-1],
                'end': dates[i],
                'days': gap_days,
                'km_before_4wk': before,
                'km_after_4wk': after
            })

    # Finn plutselige volumøkninger (>50% uke-til-uke)
    cursor.execute("""
        WITH weekly AS (
            SELECT
                strftime('%Y-%W', activity_date) as week,
                MIN(activity_date) as week_start,
                SUM(distance_km) as km
            FROM running_activities
            GROUP BY week
        ),
        with_prev AS (
            SELECT
                w1.week,
                w1.week_start,
                w1.km,
                LAG(w1.km) OVER (ORDER BY w1.week) as prev_km
            FROM weekly w1
        )
        SELECT
            week,
            week_start,
            ROUND(km, 1) as km,
            ROUND(prev_km, 1) as prev_km,
            ROUND((km - prev_km) / prev_km * 100, 0) as pct_change
        FROM with_prev
        WHERE prev_km > 10 AND (km - prev_km) / prev_km > 0.5
        ORDER BY week
    """)
    volume_spikes = cursor.fetchall()

    return {
        'gaps': gaps,
        'volume_spikes': volume_spikes
    }


def analyze_patterns(conn):
    """5. MØNSTRE Å LÆRE AV - Ukestruktur, intensitet, volum-form korrelasjon."""
    cursor = conn.cursor()

    # Ukestruktur: antall økter per uke
    cursor.execute("""
        SELECT
            sessions_per_week,
            COUNT(*) as weeks,
            ROUND(AVG(weekly_km), 1) as avg_weekly_km
        FROM (
            SELECT
                strftime('%Y-%W', activity_date) as week,
                COUNT(*) as sessions_per_week,
                SUM(distance_km) as weekly_km
            FROM running_activities
            GROUP BY week
        )
        GROUP BY sessions_per_week
        ORDER BY sessions_per_week
    """)
    week_structure = cursor.fetchall()

    # Fordeling lett vs hardt (basert på HR)
    cursor.execute("""
        SELECT
            CASE
                WHEN avg_hr < 140 THEN 'Lett (HR<140)'
                WHEN avg_hr BETWEEN 140 AND 160 THEN 'Moderat (HR 140-160)'
                WHEN avg_hr > 160 THEN 'Hardt (HR>160)'
                ELSE 'Ukjent HR'
            END as intensity,
            COUNT(*) as sessions,
            ROUND(SUM(distance_km), 1) as total_km,
            ROUND(AVG(distance_km), 1) as avg_km,
            ROUND(SUM(moving_time_s) / 3600.0, 1) as total_hours
        FROM running_activities
        GROUP BY intensity
        ORDER BY
            CASE intensity
                WHEN 'Lett (HR<140)' THEN 1
                WHEN 'Moderat (HR 140-160)' THEN 2
                WHEN 'Hardt (HR>160)' THEN 3
                ELSE 4
            END
    """)
    intensity_distribution = cursor.fetchall()

    # Korrelasjon volum -> form
    cursor.execute("""
        WITH monthly AS (
            SELECT
                strftime('%Y-%m', activity_date) as month,
                SUM(distance_km) as monthly_km,
                AVG(avg_pace_s_per_km) as avg_pace
            FROM running_activities
            WHERE avg_pace_s_per_km IS NOT NULL AND avg_pace_s_per_km BETWEEN 180 AND 420
            GROUP BY month
            HAVING COUNT(*) >= 5
        )
        SELECT
            CASE WHEN monthly_km > 150 THEN 'Høyt volum (>150 km/mnd)' ELSE 'Lavt volum (≤150 km/mnd)' END as volume_category,
            COUNT(*) as months,
            ROUND(AVG(monthly_km), 0) as avg_km,
            ROUND(AVG(avg_pace), 0) as avg_pace
        FROM monthly
        GROUP BY volume_category
    """)
    volume_form_correlation = cursor.fetchall()

    # Ukedag-fordeling
    cursor.execute("""
        SELECT
            CASE CAST(strftime('%w', activity_date) AS INTEGER)
                WHEN 0 THEN 'Søndag'
                WHEN 1 THEN 'Mandag'
                WHEN 2 THEN 'Tirsdag'
                WHEN 3 THEN 'Onsdag'
                WHEN 4 THEN 'Torsdag'
                WHEN 5 THEN 'Fredag'
                WHEN 6 THEN 'Lørdag'
            END as weekday,
            CAST(strftime('%w', activity_date) AS INTEGER) as day_num,
            COUNT(*) as sessions,
            ROUND(AVG(distance_km), 1) as avg_km
        FROM running_activities
        GROUP BY day_num
        ORDER BY day_num
    """)
    weekday_distribution = cursor.fetchall()

    # HR-drift på lange turer (økende HR ved konstant pace)
    cursor.execute("""
        SELECT
            activity_date,
            name,
            distance_km,
            avg_hr,
            max_hr,
            ROUND(max_hr - avg_hr, 0) as hr_drift
        FROM running_activities
        WHERE distance_km >= 15 AND avg_hr IS NOT NULL AND max_hr IS NOT NULL
        ORDER BY hr_drift DESC
        LIMIT 10
    """)
    hr_drift = cursor.fetchall()

    return {
        'week_structure': week_structure,
        'intensity_distribution': intensity_distribution,
        'volume_form_correlation': volume_form_correlation,
        'weekday_distribution': weekday_distribution,
        'hr_drift': hr_drift
    }


def analyze_profile(conn):
    """6. STERK-SVAK-PROFIL - VO2 Max vs terskel, pace-degradering."""
    cursor = conn.cursor()

    # Beste pace per distanse med dato
    cursor.execute("""
        SELECT
            '5k' as distance,
            MIN(avg_pace_s_per_km) as best_pace,
            (SELECT activity_date FROM running_activities
             WHERE distance_km BETWEEN 4.8 AND 5.2
             AND avg_pace_s_per_km = (SELECT MIN(avg_pace_s_per_km) FROM running_activities WHERE distance_km BETWEEN 4.8 AND 5.2)
             LIMIT 1) as best_date,
            (SELECT MIN(moving_time_s) FROM running_activities WHERE distance_km BETWEEN 4.8 AND 5.2) as best_time
        FROM running_activities
        WHERE distance_km BETWEEN 4.8 AND 5.2 AND avg_pace_s_per_km IS NOT NULL

        UNION ALL

        SELECT
            '10k' as distance,
            MIN(avg_pace_s_per_km) as best_pace,
            (SELECT activity_date FROM running_activities
             WHERE distance_km BETWEEN 9.5 AND 10.5
             AND avg_pace_s_per_km = (SELECT MIN(avg_pace_s_per_km) FROM running_activities WHERE distance_km BETWEEN 9.5 AND 10.5)
             LIMIT 1) as best_date,
            (SELECT MIN(moving_time_s) FROM running_activities WHERE distance_km BETWEEN 9.5 AND 10.5) as best_time
        FROM running_activities
        WHERE distance_km BETWEEN 9.5 AND 10.5 AND avg_pace_s_per_km IS NOT NULL

        UNION ALL

        SELECT
            'Halv' as distance,
            MIN(avg_pace_s_per_km) as best_pace,
            (SELECT activity_date FROM running_activities
             WHERE distance_km BETWEEN 20 AND 22
             AND avg_pace_s_per_km = (SELECT MIN(avg_pace_s_per_km) FROM running_activities WHERE distance_km BETWEEN 20 AND 22)
             LIMIT 1) as best_date,
            (SELECT MIN(moving_time_s) FROM running_activities WHERE distance_km BETWEEN 20 AND 22) as best_time
        FROM running_activities
        WHERE distance_km BETWEEN 20 AND 22 AND avg_pace_s_per_km IS NOT NULL
    """)
    best_paces = cursor.fetchall()

    # VO2 Max snitt og maks
    cursor.execute("""
        SELECT
            ROUND(AVG(vo2max_estimate), 1) as avg_vo2max,
            ROUND(MAX(vo2max_estimate), 1) as max_vo2max
        FROM running_activities
        WHERE vo2max_estimate IS NOT NULL AND vo2max_estimate > 0
    """)
    vo2max = cursor.fetchone()

    # Kadense-mønster
    cursor.execute("""
        SELECT
            ROUND(AVG(avg_cadence), 0) as avg_cadence,
            ROUND(AVG(CASE WHEN avg_pace_s_per_km < 240 THEN avg_cadence END), 0) as fast_cadence,
            ROUND(AVG(CASE WHEN avg_pace_s_per_km >= 280 THEN avg_cadence END), 0) as easy_cadence
        FROM running_activities
        WHERE avg_cadence IS NOT NULL AND avg_cadence > 100
    """)
    cadence = cursor.fetchone()

    # Høydemeter-preferanse
    cursor.execute("""
        SELECT
            CASE
                WHEN elevation_gain_m / distance_km > 30 THEN 'Kupert (>30m/km)'
                WHEN elevation_gain_m / distance_km > 15 THEN 'Moderat (15-30m/km)'
                ELSE 'Flatt (<15m/km)'
            END as terrain,
            COUNT(*) as sessions,
            ROUND(AVG(avg_pace_s_per_km), 0) as avg_pace,
            ROUND(AVG(elevation_gain_m), 0) as avg_elev
        FROM running_activities
        WHERE elevation_gain_m IS NOT NULL AND distance_km > 3
        GROUP BY terrain
        ORDER BY
            CASE terrain
                WHEN 'Flatt (<15m/km)' THEN 1
                WHEN 'Moderat (15-30m/km)' THEN 2
                ELSE 3
            END
    """)
    terrain_pref = cursor.fetchall()

    return {
        'best_paces': best_paces,
        'vo2max': vo2max,
        'cadence': cadence,
        'terrain_pref': terrain_pref
    }


def analyze_recommendations(conn):
    """7. ANBEFALT UTGANGSPUNKT - Startvolum, pace-soner, realisme."""
    cursor = conn.cursor()

    # Snitt volum siste 12 uker
    cursor.execute("""
        SELECT
            ROUND(SUM(distance_km) / 12.0, 1) as weekly_avg,
            COUNT(*) as sessions,
            ROUND(COUNT(*) / 12.0, 1) as sessions_per_week,
            ROUND(AVG(distance_km), 1) as avg_session_km,
            ROUND(MAX(distance_km), 1) as longest_run
        FROM running_activities
        WHERE activity_date >= date('now', '-84 days')
    """)
    recent_volume = cursor.fetchone()

    # Beste 10k og halv for realisme-vurdering OG pace-sone-beregning
    cursor.execute("""
        SELECT
            MIN(CASE WHEN distance_km BETWEEN 9.5 AND 10.5 THEN avg_pace_s_per_km END) as best_10k_pace,
            MIN(CASE WHEN distance_km BETWEEN 9.5 AND 10.5 THEN moving_time_s END) as best_10k_time,
            MIN(CASE WHEN distance_km BETWEEN 20 AND 22 THEN avg_pace_s_per_km END) as best_half_pace,
            MIN(CASE WHEN distance_km BETWEEN 20 AND 22 THEN moving_time_s END) as best_half_time
        FROM running_activities
    """)
    best_times = cursor.fetchone()

    # Beregn pace-soner fra beste 10k-pace (ikke HR-basert)
    # Formel: % av 10k-FART (ikke pace), så lavere % = saktere pace = høyere tall
    pace_zones = None
    if best_times and best_times['best_10k_pace']:
        race_pace = best_times['best_10k_pace']  # sekunder per km
        pace_zones = {
            # VO2 Max-intervaller: 95-100% av 10k-fart
            'vo2max_low': round(race_pace / 1.00),   # 100% fart = race pace
            'vo2max_high': round(race_pace / 0.95),  # 95% fart
            # Terskel (LT): 88-92% av 10k-fart
            'threshold_low': round(race_pace / 0.92),
            'threshold_high': round(race_pace / 0.88),
            # Marathon: 84-88% av 10k-fart
            'marathon_low': round(race_pace / 0.88),
            'marathon_high': round(race_pace / 0.84),
            # Easy/sone 2: 70-78% av 10k-fart
            'easy_low': round(race_pace / 0.78),
            'easy_high': round(race_pace / 0.70),
            # Rolig restitusjon: 65-70% av 10k-fart
            'recovery_low': round(race_pace / 0.70),
            'recovery_high': round(race_pace / 0.65),
            # Lagre referansen
            'race_pace_10k': race_pace
        }

    # Intensitetsfordeling siste 12 uker (for comeback-analyse)
    cursor.execute("""
        SELECT
            SUM(CASE WHEN avg_hr < 140 THEN 1 ELSE 0 END) as easy_sessions,
            SUM(CASE WHEN avg_hr >= 140 AND avg_hr < 160 THEN 1 ELSE 0 END) as moderate_sessions,
            SUM(CASE WHEN avg_hr >= 160 THEN 1 ELSE 0 END) as hard_sessions,
            COUNT(*) as total_sessions
        FROM running_activities
        WHERE activity_date >= date('now', '-84 days') AND avg_hr IS NOT NULL
    """)
    recent_intensity = cursor.fetchone()

    # Toppform-referanse (beste VO2 Max og når)
    cursor.execute("""
        SELECT
            MAX(vo2max_estimate) as peak_vo2max,
            (SELECT activity_date FROM running_activities
             WHERE vo2max_estimate = (SELECT MAX(vo2max_estimate) FROM running_activities WHERE vo2max_estimate IS NOT NULL)
             LIMIT 1) as peak_date
        FROM running_activities
        WHERE vo2max_estimate IS NOT NULL
    """)
    peak_form = cursor.fetchone()

    # Nåværende VO2 Max (siste 4 uker snitt)
    cursor.execute("""
        SELECT ROUND(AVG(vo2max_estimate), 1) as current_vo2max
        FROM running_activities
        WHERE vo2max_estimate IS NOT NULL
        AND activity_date >= date('now', '-28 days')
    """)
    current_vo2max = cursor.fetchone()

    return {
        'recent_volume': recent_volume,
        'pace_zones': pace_zones,
        'best_times': best_times,
        'recent_intensity': recent_intensity,
        'peak_form': peak_form,
        'current_vo2max': current_vo2max
    }


def generate_key_insights(volume, pace, races, injuries, patterns, profile, recommendations):
    """Genererer 3-5 nøkkelinnsikter (TL;DR)."""
    insights = []

    # Volumtrend
    if volume['yearly'] and len(volume['yearly']) > 1:
        latest_year = dict(volume['yearly'][-1])
        prev_year = dict(volume['yearly'][-2])
        if latest_year['total_km'] and prev_year['total_km']:
            change = (latest_year['total_km'] - prev_year['total_km']) / prev_year['total_km'] * 100
            if change > 20:
                insights.append(f"📈 Volumet økte {change:.0f}% fra {prev_year['year']} til {latest_year['year']} ({prev_year['total_km']:.0f} → {latest_year['total_km']:.0f} km)")
            elif change < -20:
                insights.append(f"📉 Volumet falt {abs(change):.0f}% fra {prev_year['year']} til {latest_year['year']}")

    # Skademønster
    if injuries['gaps']:
        total_gap_days = sum(g['days'] for g in injuries['gaps'])
        insights.append(f"⚠️ {len(injuries['gaps'])} treningspauser >14 dager (totalt {total_gap_days} dager tapt)")

    # Intensitetsfordeling
    if patterns['intensity_distribution']:
        intensity_dict = {row['intensity']: dict(row) for row in patterns['intensity_distribution']}
        easy = intensity_dict.get('Lett (HR<140)', {})
        total_sessions = sum(row['sessions'] for row in patterns['intensity_distribution'] if row['intensity'] != 'Ukjent HR')
        if easy and total_sessions > 0:
            easy_pct = easy.get('sessions', 0) / total_sessions * 100
            if easy_pct < 70:
                insights.append(f"🔴 Kun {easy_pct:.0f}% av økter er lette (HR<140) – anbefalt er 80%")
            else:
                insights.append(f"✅ {easy_pct:.0f}% av økter er lette – god polarisert fordeling")

    # Race-potensial basert på beste 10k
    if profile['best_paces']:
        best_10k = next((dict(p) for p in profile['best_paces'] if p['distance'] == '10k'), None)
        if best_10k and best_10k['best_time']:
            current_time = best_10k['best_time'] / 60
            target_time = 35
            gap = current_time - target_time
            if gap > 0:
                insights.append(f"🎯 Beste 10k: {format_time(best_10k['best_time'])} – {gap:.1f} min fra sub-35-målet")
            else:
                insights.append(f"✅ Allerede under 35 min på 10k!")

    # Volum vs form korrelasjon
    if patterns['volume_form_correlation'] and len(patterns['volume_form_correlation']) >= 2:
        vol_dict = {row['volume_category']: dict(row) for row in patterns['volume_form_correlation']}
        high_vol = vol_dict.get('Høyt volum (>150 km/mnd)', {})
        low_vol = vol_dict.get('Lavt volum (≤150 km/mnd)', {})
        if high_vol.get('avg_pace') and low_vol.get('avg_pace'):
            pace_diff = low_vol['avg_pace'] - high_vol['avg_pace']
            if pace_diff > 10:
                insights.append(f"📊 Høyere volum = {pace_diff:.0f}s/km raskere – volum fungerer for deg")

    return insights[:5]


def generate_report():
    """Genererer komplett rapport."""
    conn = get_connection()

    # Hent totalt antall aktiviteter
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), MIN(activity_date), MAX(activity_date) FROM running_activities")
    stats = cursor.fetchone()
    total_activities = stats[0]
    date_from = stats[1]
    date_to = stats[2]

    # Hent alle analyser
    volume = analyze_volume_history(conn)
    pace = analyze_pace_development(conn)
    races = analyze_races(conn)
    injuries = analyze_injury_patterns(conn)
    patterns = analyze_patterns(conn)
    profile = analyze_profile(conn)
    recommendations = analyze_recommendations(conn)

    # Generer nøkkelinnsikter
    insights = generate_key_insights(volume, pace, races, injuries, patterns, profile, recommendations)

    # Bygg rapport
    report = []
    report.append("# Lær meg å kjenne – Løperprofil\n")
    report.append(f"*Generert {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
    report.append(f"*Basert på {total_activities} løpeaktiviteter fra {date_from} til {date_to}*\n")

    # TL;DR
    report.append("## 🔑 Nøkkelinnsikter (TL;DR)\n")
    for insight in insights:
        report.append(f"- {insight}")
    report.append("")

    # 1. VOLUMHISTORIKK
    report.append("---\n")
    report.append("## 1. Volumhistorikk\n")

    report.append("### Total km per år\n")
    report.append("| År | Økter | Total km | Snitt km/økt |")
    report.append("|---:|------:|---------:|-------------:|")
    for row in volume['yearly']:
        report.append(f"| {row['year']} | {row['sessions']} | {row['total_km'] or 0:.0f} | {row['avg_km_per_session'] or 0:.1f} |")
    report.append("")

    report.append("### Snitt km/uke siste 12 måneder\n")
    report.append("| Måned | Økter | Total km | Snitt km/uke |")
    report.append("|-------|------:|---------:|-------------:|")
    for row in volume['monthly'][-12:]:
        report.append(f"| {row['month']} | {row['sessions']} | {row['total_km'] or 0:.0f} | {row['avg_km_per_week'] or 0:.1f} |")
    report.append("")

    report.append("### Topp-måneder og hva som skjedde etter\n")
    report.append("| Måned | Volum | Neste måned | Endring |")
    report.append("|-------|------:|-------------|--------:|")
    for row in volume['top_months']:
        if row['next_month_km']:
            change = row['next_month_km'] - row['total_km']
            change_str = f"{change:+.0f} km"
        else:
            change_str = "-"
        report.append(f"| {row['month']} | {row['total_km']:.0f} km | {row['next_month'] or '-'} ({row['next_month_km'] or 0:.0f} km) | {change_str} |")
    report.append("")

    report.append("### Sesongmønster (snitt km per kalendermåned)\n")
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Des']
    report.append("| Måned | Snitt km |")
    report.append("|-------|--------:|")
    for row in volume['seasonal']:
        month_name = month_names[row['month_num'] - 1]
        report.append(f"| {month_name} | {row['avg_km'] or 0:.0f} |")
    report.append("")

    # 2. PACE-UTVIKLING
    report.append("---\n")
    report.append("## 2. Pace-utvikling\n")

    if pace['zone2_pace']:
        report.append("### Sone 2-pace per kvartal (HR 130-150)\n")
        report.append("*Isolerer ren formtrend uavhengig av intensitet*\n")
        report.append("| Kvartal | Økter | Snitt pace | Snitt HR |")
        report.append("|---------|------:|-----------:|---------:|")
        for row in pace['zone2_pace']:
            report.append(f"| {row['quarter']} | {row['sessions']} | {format_pace(row['avg_pace'])} | {row['avg_hr']:.0f} |")
        report.append("")

    if pace['pace_by_distance']:
        report.append("### Pace per distanse over tid\n")
        report.append("| Distanse | År | Økter | Snitt pace | Beste pace |")
        report.append("|----------|---:|------:|-----------:|-----------:|")
        for row in pace['pace_by_distance']:
            report.append(f"| {row['distance_bucket']} | {row['year']} | {row['sessions']} | {format_pace(row['avg_pace'])} | {format_pace(row['best_pace'])} |")
        report.append("")

    if pace['vo2max_trend']:
        report.append("### VO2 Max-trend (Garmin)\n")
        report.append("| Måned | VO2 Max | Målinger |")
        report.append("|-------|--------:|---------:|")
        for row in pace['vo2max_trend'][-12:]:
            report.append(f"| {row['month']} | {row['vo2max']} | {row['samples']} |")
        report.append("")

    # 3. RACE-HISTORIKK
    report.append("---\n")
    report.append("## 3. Race-historikk\n")

    if races:
        report.append("*Løp markert som race, eller raske økter (>9km under 4:00/km)*\n")
        report.append("| Dato | Navn | Distanse | Tid | Pace | Vol. 6 uker før | Harde økter |")
        report.append("|------|------|--------:|----:|-----:|----------------:|------------:|")
        for r in races[:15]:
            race = r['race']
            ctx = r['context']
            weekly_vol = f"{ctx['weekly_avg']:.0f} km/uke" if ctx and ctx['weekly_avg'] else "-"
            hard = ctx['hard_sessions'] if ctx and ctx['hard_sessions'] else 0
            report.append(f"| {race['activity_date']} | {race['name'][:25] if race['name'] else '-'} | {race['distance_km']:.1f} km | {format_time(race['moving_time_s'])} | {format_pace(race['pace'])} | {weekly_vol} | {hard} |")
        report.append("")
    else:
        report.append("*Ingen løp funnet*\n")

    # 4. SKADEINDIKASJONER
    report.append("---\n")
    report.append("## 4. Skadeindikasjoner og opphold\n")

    report.append("### Treningspauser >14 dager\n")
    if injuries['gaps']:
        report.append("| Fra | Til | Dager | Volum 4 uker før | Volum 4 uker etter | Comeback% |")
        report.append("|-----|-----|------:|-----------------:|-------------------:|----------:|")
        for gap in injuries['gaps']:
            comeback_pct = (gap['km_after_4wk'] / gap['km_before_4wk'] * 100) if gap['km_before_4wk'] > 0 else 0
            report.append(f"| {gap['start']} | {gap['end']} | {gap['days']} | {gap['km_before_4wk']:.0f} km | {gap['km_after_4wk']:.0f} km | {comeback_pct:.0f}% |")
        report.append("")

        if len(injuries['gaps']) > 0:
            avg_before = statistics.mean([g['km_before_4wk'] for g in injuries['gaps'] if g['km_before_4wk'] > 0]) if any(g['km_before_4wk'] > 0 for g in injuries['gaps']) else 0
            avg_after = statistics.mean([g['km_after_4wk'] for g in injuries['gaps'] if g['km_after_4wk'] > 0]) if any(g['km_after_4wk'] > 0 for g in injuries['gaps']) else 0
            if avg_before > 0:
                report.append(f"**Comeback-mønster:** Snitt {avg_before:.0f} km før → {avg_after:.0f} km etter ({avg_after/avg_before*100:.0f}% av nivået)\n")
    else:
        report.append("*Ingen treningspauser >14 dager registrert* ✅\n")

    report.append("### Plutselige volumøkninger (>50% uke-til-uke)\n")
    if injuries['volume_spikes']:
        report.append("| Uke | Dato | Volum | Forrige uke | Økning |")
        report.append("|-----|------|------:|------------:|-------:|")
        for spike in injuries['volume_spikes'][:10]:
            report.append(f"| {spike['week']} | {spike['week_start']} | {spike['km']:.0f} km | {spike['prev_km']:.0f} km | +{spike['pct_change']:.0f}% |")
        report.append("")
        report.append("⚠️ Plutselige volumøkninger øker skaderisiko. Hold ACWR under 1.5.\n")
    else:
        report.append("*Ingen plutselige volumøkninger registrert* ✅\n")

    # 5. MØNSTRE Å LÆRE AV
    report.append("---\n")
    report.append("## 5. Mønstre å lære av\n")

    report.append("### Optimal ukestruktur (historisk)\n")
    report.append("| Økter/uke | Antall uker | Snitt km/uke |")
    report.append("|----------:|------------:|-------------:|")
    for row in patterns['week_structure']:
        report.append(f"| {row['sessions_per_week']} | {row['weeks']} | {row['avg_weekly_km']:.0f} |")
    report.append("")

    report.append("### Intensitetsfordeling\n")
    report.append("| Intensitet | Økter | Total km | Timer | Snitt km |")
    report.append("|------------|------:|---------:|------:|---------:|")
    total_sessions = sum(row['sessions'] for row in patterns['intensity_distribution'])
    for row in patterns['intensity_distribution']:
        pct = row['sessions'] / total_sessions * 100 if total_sessions > 0 else 0
        report.append(f"| {row['intensity']} ({pct:.0f}%) | {row['sessions']} | {row['total_km']:.0f} | {row['total_hours']:.1f} | {row['avg_km']:.1f} |")
    report.append("")

    report.append("### Volum → Form-korrelasjon\n")
    report.append("| Volumkategori | Måneder | Snitt km/mnd | Snitt pace |")
    report.append("|---------------|--------:|-------------:|-----------:|")
    for row in patterns['volume_form_correlation']:
        report.append(f"| {row['volume_category']} | {row['months']} | {row['avg_km']:.0f} | {format_pace(row['avg_pace'])} |")
    report.append("")

    report.append("### Ukedag-fordeling\n")
    report.append("| Dag | Økter | Snitt km |")
    report.append("|-----|------:|---------:|")
    for row in patterns['weekday_distribution']:
        report.append(f"| {row['weekday']} | {row['sessions']} | {row['avg_km']:.1f} |")
    report.append("")

    if patterns['hr_drift']:
        report.append("### HR-drift på lange turer (maks HR - snitt HR)\n")
        report.append("*Høy drift kan indikere dehydrering eller manglende utholdenhet*\n")
        report.append("| Dato | Distanse | Snitt HR | Maks HR | Drift |")
        report.append("|------|--------:|---------:|--------:|------:|")
        for row in patterns['hr_drift'][:5]:
            report.append(f"| {row['activity_date']} | {row['distance_km']:.1f} km | {row['avg_hr']:.0f} | {row['max_hr']:.0f} | +{row['hr_drift']:.0f} |")
        report.append("")

    # 6. STERK-SVAK-PROFIL
    report.append("---\n")
    report.append("## 6. Sterk-svak-profil\n")

    report.append("### Beste tider per distanse\n")
    report.append("| Distanse | Tid | Pace | Dato |")
    report.append("|----------|----:|-----:|------|")
    best_paces_dict = {}
    for row in profile['best_paces']:
        if row['best_pace'] and row['best_time']:
            report.append(f"| {row['distance']} | {format_time(row['best_time'])} | {format_pace(row['best_pace'])} | {row['best_date'] or '-'} |")
            best_paces_dict[row['distance']] = row['best_pace']
    report.append("")

    # Pace-degradering analyse
    if '5k' in best_paces_dict and '10k' in best_paces_dict:
        deg_5_10 = (best_paces_dict['10k'] - best_paces_dict['5k']) / best_paces_dict['5k'] * 100
        report.append(f"**Pace-degradering 5k → 10k:** {deg_5_10:.1f}% (typisk 3-5%)\n")

        if 'Halv' in best_paces_dict:
            deg_10_half = (best_paces_dict['Halv'] - best_paces_dict['10k']) / best_paces_dict['10k'] * 100
            report.append(f"**Pace-degradering 10k → Halv:** {deg_10_half:.1f}% (typisk 5-8%)\n")

            if deg_5_10 > 6 or deg_10_half > 10:
                report.append("\n**Profil:** 🏃 *Speed-løper* – pace faller relativt raskt med distanse. Fokus på utholdenhet og terskelarbeid vil gi størst gevinst.\n")
            elif deg_5_10 < 4 and deg_10_half < 6:
                report.append("\n**Profil:** 🦾 *Utholdenhetsløper* – holder pace godt over distanse. God aerob base, kan fokusere på VO2 Max for å løfte toppfarten.\n")
            else:
                report.append("\n**Profil:** ⚖️ *Balansert* – normal pace-degradering. Kan forbedres i begge retninger.\n")

    if profile['vo2max'] and profile['vo2max']['avg_vo2max']:
        report.append(f"**VO2 Max:** Snitt {profile['vo2max']['avg_vo2max']}, maks {profile['vo2max']['max_vo2max']}\n")

    if profile['cadence'] and profile['cadence']['avg_cadence']:
        report.append(f"**Kadense:** Snitt {profile['cadence']['avg_cadence']:.0f} spm")
        if profile['cadence']['fast_cadence']:
            report.append(f", rask pace: {profile['cadence']['fast_cadence']:.0f} spm")
        if profile['cadence']['easy_cadence']:
            report.append(f", rolig: {profile['cadence']['easy_cadence']:.0f} spm")
        report.append("\n")

    if profile['terrain_pref']:
        report.append("### Terrengpreferanse\n")
        report.append("| Terreng | Økter | Snitt pace | Snitt høydemeter |")
        report.append("|---------|------:|-----------:|-----------------:|")
        for row in profile['terrain_pref']:
            report.append(f"| {row['terrain']} | {row['sessions']} | {format_pace(row['avg_pace'])} | {row['avg_elev']:.0f} m |")
        report.append("")

    # 7. ANBEFALT UTGANGSPUNKT
    report.append("---\n")
    report.append("## 7. Anbefalt utgangspunkt\n")

    rec = recommendations

    # Comeback-status analyse
    report.append("### Comeback-status\n")
    if rec['peak_form'] and rec['peak_form']['peak_vo2max'] and rec['current_vo2max'] and rec['current_vo2max']['current_vo2max']:
        peak_vo2 = rec['peak_form']['peak_vo2max']
        peak_date = rec['peak_form']['peak_date']
        current_vo2 = rec['current_vo2max']['current_vo2max']
        vo2_gap = peak_vo2 - current_vo2

        report.append(f"| Metrikk | Toppform | Nå | Gap |")
        report.append(f"|---------|----------|----|----|")
        report.append(f"| VO2 Max | {peak_vo2:.1f} ({peak_date}) | {current_vo2:.1f} | -{vo2_gap:.1f} |")

        if vo2_gap > 3:
            report.append(f"\n⚠️ **Comeback-fase bekreftet.** VO2 Max er {vo2_gap:.1f} poeng under toppform.")
            report.append("Prioritet i fase 1A: gjenoppbygging av aerob kapasitet.\n")
        elif vo2_gap > 0:
            report.append(f"\n✅ Nær toppform (kun {vo2_gap:.1f} VO2 Max-poeng under). Kan gå rett i build-fase.\n")
        else:
            report.append(f"\n🔥 **Ny toppform!** VO2 Max er høyere enn tidligere.\n")

    # Intensitetsfordeling-analyse
    report.append("### Intensitetsfordeling (siste 12 uker)\n")
    if rec['recent_intensity'] and rec['recent_intensity']['total_sessions']:
        ri = rec['recent_intensity']
        total = ri['total_sessions']
        easy_pct = (ri['easy_sessions'] / total * 100) if total > 0 else 0
        moderate_pct = (ri['moderate_sessions'] / total * 100) if total > 0 else 0
        hard_pct = (ri['hard_sessions'] / total * 100) if total > 0 else 0

        report.append(f"| Intensitet | Økter | Andel | Mål |")
        report.append(f"|------------|------:|------:|-----|")
        report.append(f"| Lett (HR<140) | {ri['easy_sessions']} | {easy_pct:.0f}% | 80% |")
        report.append(f"| Moderat (HR 140-160) | {ri['moderate_sessions']} | {moderate_pct:.0f}% | 0% |")
        report.append(f"| Hardt (HR>160) | {ri['hard_sessions']} | {hard_pct:.0f}% | 20% |")
        report.append("")

        if easy_pct < 70:
            report.append(f"🔴 **Kritisk:** Kun {easy_pct:.0f}% lette økter. Fase 1A må fokusere på å øke til 80% før kvalitet legges på.\n")
        elif easy_pct < 80:
            report.append(f"⚠️ Intensitetsfordeling bør forbedres fra {easy_pct:.0f}% → 80% lett i fase 1A.\n")
        else:
            report.append(f"✅ God polarisert fordeling ({easy_pct:.0f}% lett). Klar for build-fase.\n")

    # Volum-analyse
    if rec['recent_volume'] and rec['recent_volume']['weekly_avg']:
        rv = rec['recent_volume']
        report.append("### Start-volum for fase 1A (comeback)\n")
        report.append(f"*Basert på siste 12 ukers snitt*\n")
        report.append(f"- **Ukevolum nå:** {rv['weekly_avg']:.0f} km/uke")
        report.append(f"- **Økter per uke:** {rv['sessions_per_week']:.1f}")
        report.append(f"- **Snitt per økt:** {rv['avg_session_km']:.1f} km")
        report.append(f"- **Lengste løp:** {rv['longest_run']:.1f} km")
        report.append("")

        # Fase 1A volum-progresjon
        start_vol = rv['weekly_avg']
        report.append("**Fase 1A volumplan (4 uker):**\n")
        report.append(f"| Uke | Mål | Fokus |")
        report.append(f"|-----|-----|-------|")
        report.append(f"| 1 | {start_vol:.0f} km | Etabler rytme, 80% lett |")
        report.append(f"| 2 | {min(start_vol * 1.10, 60):.0f} km | +10%, maks 1 terskel-light |")
        report.append(f"| 3 | {min(start_vol * 1.20, 65):.0f} km | Lang tur 16-18 km |")
        report.append(f"| 4 | {min(start_vol * 0.96, 52):.0f} km | Nedtrapping (-20%) |")
        report.append("")
        report.append(f"**Mål etter fase 1A:** 65 km/uke, VO2 Max 58+, 80% lette økter\n")

    report.append("### Foreslåtte pace-soner (beregnet fra 10k-PB)\n")
    pz = rec['pace_zones']
    if pz:
        report.append(f"*Basert på beste 10k-pace: {format_pace(pz['race_pace_10k'])}*\n")
        report.append("| Sone | Intensitet | Pace-range |")
        report.append("|------|------------|------------|")
        report.append(f"| Restitusjon | 65-70% | {format_pace(pz['recovery_high'])}-{format_pace(pz['recovery_low'])} |")
        report.append(f"| Lett/Sone 2 | 70-78% | {format_pace(pz['easy_high'])}-{format_pace(pz['easy_low'])} |")
        report.append(f"| Marathon | 84-88% | {format_pace(pz['marathon_high'])}-{format_pace(pz['marathon_low'])} |")
        report.append(f"| Terskel (LT) | 88-92% | {format_pace(pz['threshold_high'])}-{format_pace(pz['threshold_low'])} |")
        report.append(f"| VO2 Max | 95-100% | {format_pace(pz['vo2max_high'])}-{format_pace(pz['vo2max_low'])} |")
        report.append("")
    else:
        report.append("*Ingen 10k-data tilgjengelig for å beregne pace-soner*\n")

    report.append("### Realisme-vurdering\n")
    bt = rec['best_times']
    if bt:
        if bt['best_10k_time']:
            current_10k = bt['best_10k_time'] / 60
            target_10k = 35
            gap_10k = current_10k - target_10k

            report.append(f"**10k:** Beste tid {format_time(bt['best_10k_time'])}, mål 35:00\n")
            if gap_10k <= 0:
                report.append(f"- ✅ **Allerede under målet!** Fokus på å gjenta prestasjonen konsistent.\n")
            elif gap_10k <= 2:
                report.append(f"- ✅ Sub-35 er realistisk innen 3-6 mnd med riktig trening (gap: {gap_10k:.1f} min)\n")
            elif gap_10k <= 4:
                report.append(f"- ⚠️ Sub-35 krever betydelig forbedring (gap: {gap_10k:.1f} min) – 6+ mnd er mer realistisk\n")
            else:
                report.append(f"- 🔴 Sub-35 er ambisiøst (gap: {gap_10k:.1f} min) – fokuser på gradvis forbedring over 6-12 mnd\n")

        if bt['best_half_time']:
            current_half = bt['best_half_time'] / 60
            target_half = 80
            gap_half = current_half - target_half

            report.append(f"\n**Halvmaraton:** Beste tid {format_time(bt['best_half_time'])}, mål 1:20:00\n")
            if gap_half <= 0:
                report.append(f"- ✅ **Allerede under målet!**\n")
            elif gap_half <= 4:
                report.append(f"- ✅ Sub-1:20 er realistisk etter 10k-blokken (gap: {gap_half:.1f} min)\n")
            elif gap_half <= 8:
                report.append(f"- ⚠️ Sub-1:20 krever solid halvblokk etter 10k (gap: {gap_half:.1f} min)\n")
            else:
                report.append(f"- 🔴 Sub-1:20 er langsiktig mål (gap: {gap_half:.1f} min) – bygg aerob base først\n")

    report.append("")
    report.append("---\n")
    report.append("*Rapport ferdig. Neste steg: `analyse/baseline.py` for nåværende form og pace-soner.*\n")

    conn.close()

    # Skriv til fil
    RAPPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RAPPORT_PATH, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report))

    print(f"Rapport generert: {RAPPORT_PATH}")
    return '\n'.join(report)


if __name__ == "__main__":
    report = generate_report()
