#!/usr/bin/env python3
"""
Beregner korrekt 2024-data basert på ukedag, ikke dato.

For hver uke i 2026-planen, finner vi tilsvarende ISO-uke i 2024
slik at mandag matcher mandag, tirsdag matcher tirsdag, osv.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"

# 2026-uker (mandag-start for hver uke)
WEEKS_2026 = [
    (1, "2026-05-11"),   # Uke 1: 11.05 - 17.05
    (2, "2026-05-18"),   # Uke 2: 18.05 - 24.05
    (3, "2026-05-25"),   # Uke 3: 25.05 - 31.05
    (4, "2026-06-01"),   # Uke 4: 01.06 - 07.06
    (5, "2026-06-08"),   # Uke 5: 08.06 - 14.06
    (6, "2026-06-15"),   # Uke 6: 15.06 - 21.06
    (7, "2026-06-22"),   # Uke 7: 22.06 - 28.06
    (8, "2026-06-29"),   # Uke 8: 29.06 - 05.07
    (9, "2026-07-06"),   # Uke 9: 06.07 - 12.07
    (10, "2026-07-13"),  # Uke 10: 13.07 - 19.07
    (11, "2026-07-20"),  # Uke 11: 20.07 - 26.07
    (12, "2026-07-27"),  # Uke 12: 27.07 - 02.08
    (13, "2026-08-03"),  # Uke 13: 03.08 - 09.08
    (14, "2026-08-10"),  # Uke 14: 10.08 - 16.08
    (15, "2026-08-17"),  # Uke 15: 17.08 - 23.08
    (16, "2026-08-24"),  # Uke 16: 24.08 - 30.08
    (17, "2026-09-01"),  # Uke 17: 01.09 - 07.09 (NB: starter på tirsdag, men vi bruker mandag 31.08)
    (18, "2026-09-08"),  # Uke 18: 08.09 - 14.09
    (19, "2026-09-15"),  # Uke 19: 15.09 - 21.09
]

WEEKDAY_NAMES = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn']


def get_iso_week_monday(date_str: str) -> datetime:
    """Finn mandagen i ISO-uken for en gitt dato."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    # Finn mandagen i denne uken
    monday = dt - timedelta(days=dt.weekday())
    return monday


def get_corresponding_2024_monday(date_2026_str: str) -> datetime:
    """Finn mandagen i 2024 som tilsvarer samme ISO-uke."""
    dt_2026 = datetime.strptime(date_2026_str, "%Y-%m-%d")
    iso_year, iso_week, _ = dt_2026.isocalendar()

    # Finn første dag i samme ISO-uke i 2024
    # ISO uke 1 i 2024 starter 01.01.2024 (som er en mandag)
    jan4_2024 = datetime(2024, 1, 4)  # 4. januar er alltid i uke 1
    jan4_week1_monday = jan4_2024 - timedelta(days=jan4_2024.weekday())

    # Beregn mandagen i ønsket uke
    target_monday = jan4_week1_monday + timedelta(weeks=iso_week - 1)

    return target_monday


def format_pace(seconds_per_km: float) -> str:
    """Formater pace som m:ss."""
    if not seconds_per_km or seconds_per_km <= 0:
        return ""
    mins = int(seconds_per_km // 60)
    secs = int(seconds_per_km % 60)
    return f"{mins}:{secs:02d}"


def format_time(seconds: float) -> str:
    """Formater tid som h:mm:ss eller m:ss."""
    if not seconds or seconds <= 0:
        return ""
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def get_activities_for_week(conn, monday_2024: datetime) -> dict:
    """Hent aktiviteter for en uke i 2024, gruppert etter ukedag."""
    start = monday_2024.strftime("%Y-%m-%d")
    end = (monday_2024 + timedelta(days=6)).strftime("%Y-%m-%d")

    cur = conn.cursor()

    # Hent alle aktiviteter
    cur.execute("""
        SELECT
            date(start_date) as dato,
            strftime('%w', start_date) as weekday_num,
            ROUND(distance_km, 1) as km,
            ROUND(avg_pace_s_per_km, 0) as pace,
            max_hr,
            sport,
            is_race,
            moving_time_s,
            elapsed_time_s,
            name
        FROM activities
        WHERE date(start_date) BETWEEN ? AND ?
        ORDER BY start_date
    """, (start, end))

    activities_by_day = {i: [] for i in range(7)}  # 0=Man, 1=Tir, etc.

    for row in cur.fetchall():
        dato, weekday_num, km, pace, max_hr, sport, is_race, moving_time, elapsed_time, name = row
        # SQLite weekday: 0=Sunday, 1=Monday, etc. Konverter til 0=Monday
        weekday_idx = (int(weekday_num) - 1) % 7

        activities_by_day[weekday_idx].append({
            'dato': dato,
            'km': km,
            'pace': pace,
            'max_hr': max_hr,
            'sport': sport,
            'is_race': is_race,
            'moving_time': moving_time,
            'elapsed_time': elapsed_time,
            'name': name or ''
        })

    # Hent intervall-data for løpeaktiviteter
    cur.execute("""
        SELECT
            a.id,
            date(a.start_date) as dato,
            strftime('%w', a.start_date) as weekday_num,
            COUNT(CASE WHEN il.lap_type = 'work' THEN 1 END) as num_intervals,
            ROUND(AVG(CASE WHEN il.lap_type = 'work' THEN il.distance_m END), 0) as avg_dist,
            ROUND(MIN(CASE WHEN il.lap_type = 'work' THEN il.pace_s_per_km END), 0) as min_pace,
            ROUND(MAX(CASE WHEN il.lap_type = 'work' THEN il.pace_s_per_km END), 0) as max_pace,
            MAX(CASE WHEN il.lap_type = 'work' THEN il.max_hr END) as interval_max_hr
        FROM activities a
        JOIN interval_laps il ON a.id = il.activity_id
        WHERE date(a.start_date) BETWEEN ? AND ?
          AND a.sport = 'running'
        GROUP BY a.id
        HAVING num_intervals >= 3
    """, (start, end))

    intervals_by_date = {}
    for row in cur.fetchall():
        _, dato, _, num, avg_dist, min_pace, max_pace, interval_hr = row
        intervals_by_date[dato] = {
            'num': num,
            'avg_dist': avg_dist,
            'min_pace': min_pace,
            'max_pace': max_pace,
            'max_hr': interval_hr
        }

    return activities_by_day, intervals_by_date


def format_day_activities(activities: list, intervals_by_date: dict) -> str:
    """Formater aktiviteter for én dag."""
    if not activities:
        return "Hvile"

    parts = []
    total_km = 0

    for act in activities:
        total_km += act['km'] or 0

        if act['sport'] == 'soccer':
            # Bruk elapsed_time for fotball (total kamptid), ikke moving_time
            mins = int((act['elapsed_time'] or act['moving_time'] or 0) / 60)
            parts.append(f"{act['km']} km ⚽ {mins} min")
        elif act['is_race']:
            time_str = format_time(act['moving_time'])
            pace_str = format_pace(act['pace'])
            hr = act['max_hr'] or ''
            race_km = act['km']
            parts.append(f"🏁 {race_km} km {time_str} @ {pace_str} HR {hr}")
        elif act['dato'] in intervals_by_date:
            iv = intervals_by_date[act['dato']]
            dist_m = int(iv['avg_dist']) if iv['avg_dist'] else 0
            min_p = format_pace(iv['min_pace']) if iv['min_pace'] else ''
            max_p = format_pace(iv['max_pace']) if iv['max_pace'] else ''
            hr = iv['max_hr'] or ''
            parts.append(f"{act['km']} km · {iv['num']}×{dist_m}m @ {min_p} HR {hr}")
        else:
            pace_str = format_pace(act['pace'])
            hr = act['max_hr'] or ''
            if pace_str and hr:
                parts.append(f"{act['km']} km @ {pace_str} HR {hr}")
            elif act['km']:
                parts.append(f"{act['km']} km")

    if not parts:
        return "Hvile"

    # Kombiner hvis flere aktiviteter
    result = " + ".join(parts) if len(parts) <= 2 else f"{round(total_km, 1)} km (flere økter)"
    return result


def main():
    conn = sqlite3.connect(DB_PATH)

    print("=" * 70)
    print("2024-DATA BASERT PÅ UKEDAG (ikke dato)")
    print("=" * 70)

    for week_num, monday_2026_str in WEEKS_2026:
        monday_2026 = datetime.strptime(monday_2026_str, "%Y-%m-%d")
        monday_2024 = get_corresponding_2024_monday(monday_2026_str)

        print(f"\n### UKE {week_num}")
        print(f"2026: {monday_2026.strftime('%d.%m')} - {(monday_2026 + timedelta(days=6)).strftime('%d.%m')}")
        print(f"2024: {monday_2024.strftime('%d.%m')} - {(monday_2024 + timedelta(days=6)).strftime('%d.%m')} (ISO uke {monday_2024.isocalendar()[1]})")
        print()

        activities_by_day, intervals_by_date = get_activities_for_week(conn, monday_2024)

        total_km = 0
        for day_idx in range(7):
            date_2026 = monday_2026 + timedelta(days=day_idx)
            date_2024 = monday_2024 + timedelta(days=day_idx)

            day_activities = activities_by_day[day_idx]
            formatted = format_day_activities(day_activities, intervals_by_date)

            day_km = sum(a['km'] or 0 for a in day_activities)
            total_km += day_km

            print(f"| {WEEKDAY_NAMES[day_idx]} {date_2026.strftime('%d.%m')} | ... | {formatted} |")

        print(f"| **Total** | ... | **{round(total_km, 1)} km** |")

    conn.close()


if __name__ == "__main__":
    main()
