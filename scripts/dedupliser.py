#!/usr/bin/env python3
"""
Dedupliserer activities-tabellen i treningsplan.db.

Matching-logikk v2:
- Aktiviteter regnes som duplikater hvis de har samme:
  date(start_date) AND ROUND(distance_km, 1)
  OG starter innen 60 minutter av hverandre

- Hvis to aktiviteter har samme dato + distanse men starter >60 minutter
  fra hverandre, er de SEPARATE turer (f.eks. morgen + kveld) og beholdes begge.

Prioritet for hvilken versjon å beholde:
  a) Garmin-versjonen først (garmin_id IS NOT NULL og has_training_load = 1)
  b) Deretter versjonen med flest utfylte felt
  c) Til slutt: den eldste raden (laveste id)
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
BACKUP_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db.backup-FOERDEDUP2"
LOG_PATH = PROJECT_ROOT / "data" / "processed" / "dedupliseringslogg.txt"

# Tidsvindu for å anse to aktiviteter som samme tur (minutter)
# 180 min pga tidssone-forskjell: Strava=UTC, Garmin=lokal tid (UTC+1/+2)
DUPLICATE_WINDOW_MINUTES = 180


def check_backup():
    """Sjekker at backup-fil finnes før deduplisering."""
    if not BACKUP_PATH.exists():
        print("=" * 60)
        print("FEIL: Backup-fil mangler!")
        print("=" * 60)
        print(f"\nForventet fil: {BACKUP_PATH}")
        print("\nDu må lage backup manuelt før deduplisering:")
        print(f"  cp '{DB_PATH}' '{BACKUP_PATH}'")
        print("\nABORTER.")
        sys.exit(1)
    print(f"✓ Backup funnet: {BACKUP_PATH}")


def normalize_datetime(start_date_str):
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


def get_all_activities(cursor):
    """Henter alle aktiviteter med nødvendig info for duplikatsjekk."""
    cursor.execute("""
        SELECT
            id,
            start_date,
            date(CASE
                WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                ELSE start_date
            END) as activity_date,
            ROUND(distance_km, 1) as dist,
            distance_km,
            moving_time_s,
            source,
            garmin_id,
            has_training_load,
            name,
            -- Count non-null fields for priority b
            (CASE WHEN name IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN distance_km IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN moving_time_s IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN avg_pace_s_per_km IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN avg_hr IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN max_hr IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN elevation_gain_m IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN avg_cadence IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN training_load IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN aerobic_te IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN anaerobic_te IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN vo2max_estimate IS NOT NULL THEN 1 ELSE 0 END) as non_null_count
        FROM activities
        WHERE start_date IS NOT NULL
        ORDER BY activity_date, dist
    """)
    return cursor.fetchall()


def find_duplicate_groups(activities):
    """
    Finner duplikatgrupper basert på dato + distanse + 60-min tidsvindu.

    Returnerer liste av grupper, der hver gruppe er en liste av aktiviteter
    som regnes som duplikater (samme tur, ulik kilde).
    """
    # Grupper først etter dato + avrundet distanse
    date_dist_groups = defaultdict(list)
    for activity in activities:
        key = (activity[2], activity[3])  # activity_date, dist
        date_dist_groups[key].append(activity)

    duplicate_groups = []
    separate_runs = []

    for key, group in date_dist_groups.items():
        if len(group) < 2:
            continue  # Ingen duplikater mulig

        # Sorter etter starttid
        sorted_group = sorted(group, key=lambda x: normalize_datetime(x[1]) or datetime.min)

        # Sjekk tidsavstand mellom hver aktivitet
        current_cluster = [sorted_group[0]]

        for i in range(1, len(sorted_group)):
            prev_time = normalize_datetime(sorted_group[i-1][1])
            curr_time = normalize_datetime(sorted_group[i][1])

            if prev_time and curr_time:
                diff_minutes = abs((curr_time - prev_time).total_seconds() / 60)

                if diff_minutes <= DUPLICATE_WINDOW_MINUTES:
                    # Innenfor vindu = samme tur, legg til cluster
                    current_cluster.append(sorted_group[i])
                else:
                    # Utenfor vindu = ny separat tur
                    if len(current_cluster) > 1:
                        duplicate_groups.append(current_cluster)
                    elif len(current_cluster) == 1:
                        pass  # Enkeltstående, ingen duplikat
                    separate_runs.append((key, diff_minutes))
                    current_cluster = [sorted_group[i]]
            else:
                # Kan ikke sammenligne tid, anta duplikat for sikkerhet
                current_cluster.append(sorted_group[i])

        # Sjekk siste cluster
        if len(current_cluster) > 1:
            duplicate_groups.append(current_cluster)

    return duplicate_groups, separate_runs


def prioritize_activity(activity):
    """
    Returnerer prioritetsscore for en aktivitet.
    Lavere score = høyere prioritet (beholdes).
    """
    row_id, start_date, activity_date, dist, distance_km, moving_time_s, \
        source, garmin_id, has_training_load, name, non_null_count = activity

    # Priority a: Garmin with training_load = 0 (best)
    if garmin_id is not None and has_training_load == 1:
        priority_a = 0
    else:
        priority_a = 1

    # Priority b: Most non-null fields (negative for sorting)
    priority_b = -non_null_count

    # Priority c: Lowest id
    priority_c = row_id

    return (priority_a, priority_b, priority_c)


def determine_rule(activity):
    """Bestemmer hvilken regel som ble brukt for prioritering."""
    garmin_id = activity[7]
    has_training_load = activity[8]

    if garmin_id is not None and has_training_load == 1:
        return "a (Garmin med training_load)"
    else:
        return "b (flest utfylte felt) eller c (laveste id)"


def analyze_duplicates(dry_run=True):
    """
    Analyserer duplikater.
    Hvis dry_run=True, viser kun analyse uten å slette.
    Hvis dry_run=False, sletter duplikater.
    """
    check_backup()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Hent statistikk før
    cursor.execute("SELECT COUNT(*) FROM activities")
    total_before = cursor.fetchone()[0]

    # Hent alle aktiviteter
    activities = get_all_activities(cursor)

    # Finn duplikatgrupper
    duplicate_groups, separate_runs = find_duplicate_groups(activities)

    print()
    print("=" * 70)
    print("DUPLIKAT-ANALYSE (v2: 60-minutters tidsvindu)")
    print("=" * 70)
    print()
    print(f"Totalt antall rader nå: {total_before}")
    print()

    # Tell opp
    total_duplicates = sum(len(g) - 1 for g in duplicate_groups)  # -1 fordi vi beholder én per gruppe
    total_groups = len(duplicate_groups)

    print(f"Duplikatgrupper funnet: {total_groups}")
    print(f"Rader som vil slettes:  {total_duplicates}")
    print(f"Rader etter sletting:   {total_before - total_duplicates}")
    print()

    if separate_runs:
        print(f"Separate turer (samme dato+distanse, >60 min mellom) som BEHOLDES: {len(separate_runs)}")
        for (date_dist, diff) in separate_runs[:5]:  # Vis maks 5
            print(f"  - {date_dist[0]} | {date_dist[1]} km | {diff:.0f} min mellom")
        if len(separate_runs) > 5:
            print(f"  ... og {len(separate_runs) - 5} til")
        print()

    print("-" * 70)
    print("DUPLIKATGRUPPER SOM VIL BEHANDLES:")
    print("-" * 70)
    print()

    to_delete = []

    for group in duplicate_groups:
        # Sorter etter prioritet
        sorted_group = sorted(group, key=prioritize_activity)
        keep = sorted_group[0]
        delete = sorted_group[1:]

        activity_date = keep[2]
        dist = keep[3]
        duration_min = (keep[5] or 0) / 60

        keep_source = keep[6] or "ukjent"
        keep_id = keep[0]
        rule = determine_rule(keep)

        print(f"Økt: {activity_date} | {dist} km | ~{duration_min:.0f} min")
        print(f"  Beholder: ID {keep_id} ({keep_source})")

        for d in delete:
            d_id = d[0]
            d_source = d[6] or "ukjent"
            d_time = normalize_datetime(d[1])
            keep_time = normalize_datetime(keep[1])
            if d_time and keep_time:
                diff = abs((d_time - keep_time).total_seconds() / 60)
                print(f"  Sletter:  ID {d_id} ({d_source}) - {diff:.0f} min avvik")
            else:
                print(f"  Sletter:  ID {d_id} ({d_source})")
            to_delete.append(d_id)

        print(f"  Regel: {rule}")
        print()

    print("-" * 70)
    print("OPPSUMMERING")
    print("-" * 70)
    print(f"Totalt antall rader nå:       {total_before}")
    print(f"Ekte duplikater (slettes):    {len(to_delete)}")
    print(f"Separate turer (beholdes):    {len(separate_runs)}")
    print(f"Estimert antall rader etter:  {total_before - len(to_delete)}")
    print()

    if dry_run:
        print("=" * 70)
        print("ANALYSE FERDIG - INGEN ENDRINGER GJORT")
        print("=" * 70)
        print()
        print("Skriv 'ja, slett' for å utføre slettingen.")
        conn.close()
        return to_delete
    else:
        # Utfør sletting
        with open(LOG_PATH, 'a') as log:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.write(f"\n{'=' * 60}\n")
            log.write(f"DEDUPLISERING v2 - {timestamp}\n")
            log.write(f"{'=' * 60}\n\n")

            for group in duplicate_groups:
                sorted_group = sorted(group, key=prioritize_activity)
                keep = sorted_group[0]
                delete = sorted_group[1:]

                activity_date = keep[2]
                dist = keep[3]
                duration_min = (keep[5] or 0) / 60
                rule = determine_rule(keep)

                log.write(f"Økt: {activity_date} | {dist} km | ~{duration_min:.0f} min\n")
                log.write(f"  Beholdt: ID {keep[0]} ({keep[6]})\n")

                delete_ids = []
                for d in delete:
                    cursor.execute("DELETE FROM activities WHERE id = ?", (d[0],))
                    delete_ids.append(str(d[0]))

                log.write(f"  Slettet: ID {', '.join(delete_ids)}\n")
                log.write(f"  Regel: {rule}\n\n")

            log.write(f"TOTALT SLETTET: {len(to_delete)} rader\n")

        conn.commit()

        # Hent statistikk etter
        cursor.execute("SELECT COUNT(*) FROM activities")
        total_after = cursor.fetchone()[0]

        conn.close()

        print("=" * 70)
        print("DEDUPLISERING FULLFØRT")
        print("=" * 70)
        print()
        print(f"Rader før:   {total_before}")
        print(f"Rader etter: {total_after}")
        print(f"Slettet:     {len(to_delete)}")
        print()
        print(f"Logg skrevet til: {LOG_PATH}")

        return to_delete


def run_deduplication():
    """Hovedfunksjon som først viser analyse, deretter venter på bekreftelse."""
    to_delete = analyze_duplicates(dry_run=True)

    if not to_delete:
        print("Ingen duplikater å slette!")
        return

    # Vent på bekreftelse
    response = input("\nVil du slette disse duplikatene? (skriv 'ja, slett'): ").strip().lower()

    if response == "ja, slett":
        analyze_duplicates(dry_run=False)
    else:
        print("\nAvbrutt. Ingen endringer gjort.")


if __name__ == "__main__":
    run_deduplication()
