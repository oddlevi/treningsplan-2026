#!/usr/bin/env python3
"""
Henter aktiviteter fra Strava API.

Bruk:
    python scripts/fetch_strava.py              # Hent nye aktiviteter siden sist sync
    python scripts/fetch_strava.py --full-history   # Hent ALL historikk
    python scripts/fetch_strava.py --authorize      # Kun kjør OAuth-autorisering
"""

import os
import sys
import json
import sqlite3
import webbrowser
from pathlib import Path
from typing import Optional
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import time

import click
import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from dotenv import load_dotenv, set_key

# Last miljøvariabler
load_dotenv()

console = Console()

# Strava API-endepunkter
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"

# Prosjektstier
PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw" / "strava"
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Håndterer OAuth callback fra Strava."""

    authorization_code = None

    def do_GET(self):
        """Mottar authorization code fra Strava."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            OAuthCallbackHandler.authorization_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write("""
                <html><body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
                <h1>Autorisering vellykket!</h1>
                <p>Du kan lukke dette vinduet og ga tilbake til terminalen.</p>
                </body></html>
            """.encode("utf-8"))
        else:
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            error = params.get("error", ["Ukjent feil"])[0]
            self.wfile.write(f"<html><body><h1>Feil: {error}</h1></body></html>".encode())

    def log_message(self, format, *args):
        """Undertrykk logging."""
        pass


def get_authorization_code(client_id: str) -> str:
    """Kjører OAuth2 authorization flow."""

    # Bygg autoriserings-URL
    auth_params = {
        "client_id": client_id,
        "redirect_uri": "http://localhost:8000/callback",
        "response_type": "code",
        "scope": "read,activity:read_all",
    }
    auth_url = f"{STRAVA_AUTH_URL}?{'&'.join(f'{k}={v}' for k, v in auth_params.items())}"

    console.print("\n[bold]Strava-autorisering[/bold]")
    console.print("Åpner nettleseren for å autorisere tilgang til Strava...")
    console.print(f"[dim]URL: {auth_url}[/dim]\n")

    # Start lokal server for callback
    server = HTTPServer(("localhost", 8000), OAuthCallbackHandler)
    server.timeout = 120  # 2 minutter timeout

    # Åpne nettleser
    webbrowser.open(auth_url)

    console.print("[yellow]Venter på autorisering i nettleseren...[/yellow]")

    # Vent på callback
    while OAuthCallbackHandler.authorization_code is None:
        server.handle_request()

    server.server_close()
    return OAuthCallbackHandler.authorization_code


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> dict:
    """Bytter authorization code mot access/refresh tokens."""

    response = requests.post(STRAVA_TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    })
    response.raise_for_status()
    return response.json()


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Fornyer access token med refresh token."""

    response = requests.post(STRAVA_TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    response.raise_for_status()
    return response.json()


def get_valid_access_token() -> str:
    """Henter gyldig access token, fornyer om nødvendig."""

    client_id = os.getenv("STRAVA_CLIENT_ID")
    client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    refresh_token = os.getenv("STRAVA_REFRESH_TOKEN")

    if not client_id or not client_secret:
        console.print("[red]Feil: STRAVA_CLIENT_ID og STRAVA_CLIENT_SECRET må settes i .env[/red]")
        sys.exit(1)

    if not refresh_token:
        # Første gangs autorisering
        console.print("[yellow]Ingen refresh token funnet. Starter autorisering...[/yellow]")
        code = get_authorization_code(client_id)
        tokens = exchange_code_for_tokens(client_id, client_secret, code)

        # Lagre refresh token til .env
        set_key(str(ENV_FILE), "STRAVA_REFRESH_TOKEN", tokens["refresh_token"])
        console.print("[green]Refresh token lagret i .env[/green]")

        return tokens["access_token"]

    # Forny token
    tokens = refresh_access_token(client_id, client_secret, refresh_token)

    # Oppdater refresh token hvis den er ny
    if tokens.get("refresh_token") != refresh_token:
        set_key(str(ENV_FILE), "STRAVA_REFRESH_TOKEN", tokens["refresh_token"])

    return tokens["access_token"]


def init_database():
    """Oppretter databasetabeller hvis de ikke finnes."""

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY,
            garmin_id INTEGER,
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
            source TEXT DEFAULT 'strava',
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_status (
            source TEXT PRIMARY KEY,
            last_sync TIMESTAMP,
            last_activity_date TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def get_last_sync_date() -> Optional[datetime]:
    """Henter dato for siste synkroniserte aktivitet."""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT last_activity_date FROM sync_status WHERE source = 'strava'
    """)
    row = cursor.fetchone()
    conn.close()

    if row and row[0]:
        return datetime.fromisoformat(row[0])
    return None


def update_sync_status(last_activity_date: datetime):
    """Oppdaterer sync-status i databasen."""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO sync_status (source, last_sync, last_activity_date)
        VALUES ('strava', ?, ?)
    """, (datetime.now().isoformat(), last_activity_date.isoformat()))

    conn.commit()
    conn.close()


def fetch_activities(access_token: str, after: datetime = None, per_page: int = 100, limit: int = 0) -> list:
    """Henter aktiviteter fra Strava API med automatisk rate limit-håndtering."""

    headers = {"Authorization": f"Bearer {access_token}"}
    activities = []
    page = 1

    params = {"per_page": per_page}
    if after:
        params["after"] = int(after.timestamp())

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Henter aktiviteter fra Strava...", total=None)

        while True:
            params["page"] = page

            # Retry-logikk med rate limit-håndtering
            for attempt in range(5):
                response = requests.get(
                    f"{STRAVA_API_BASE}/athlete/activities",
                    headers=headers,
                    params=params,
                )

                if response.status_code == 429:
                    wait_time = 60 * (attempt + 1)  # 1, 2, 3, 4, 5 minutter
                    progress.update(task, description=f"[yellow]Rate limit! Venter {wait_time//60} min...[/yellow]")
                    time.sleep(wait_time)
                    continue

                response.raise_for_status()
                break
            else:
                console.print("[red]Ga opp etter 5 forsøk på rate limit[/red]")
                break

            batch = response.json()
            if not batch:
                break

            activities.extend(batch)
            progress.update(task, description=f"Hentet {len(activities)} aktiviteter...")

            # Stopp hvis vi har nådd limit (testmodus)
            if limit > 0 and len(activities) >= limit:
                activities = activities[:limit]
                break

            page += 1

            # Respekter rate limit - 1 sekund mellom requests
            time.sleep(1)

    return activities


def fetch_activity_details(access_token: str, activity_id: int, max_retries: int = 3) -> dict:
    """Henter detaljert info om én aktivitet med retry ved rate limit."""

    headers = {"Authorization": f"Bearer {access_token}"}

    for attempt in range(max_retries):
        response = requests.get(
            f"{STRAVA_API_BASE}/activities/{activity_id}",
            headers=headers,
        )

        if response.status_code == 429:
            # Rate limit - vent og prøv igjen
            wait_time = 15 * (attempt + 1)  # 15, 30, 45 sekunder
            console.print(f"[yellow]Rate limit. Venter {wait_time}s...[/yellow]")
            time.sleep(wait_time)
            continue

        response.raise_for_status()
        return response.json()

    raise Exception(f"Rate limit etter {max_retries} forsøk")


def save_raw_activity(activity: dict):
    """Lagrer rå JSON til fil."""

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    activity_id = activity["id"]
    start_date = activity["start_date"][:10]  # YYYY-MM-DD

    filepath = RAW_DATA_DIR / f"{start_date}_{activity_id}.json"
    with open(filepath, "w") as f:
        json.dump(activity, f, indent=2)


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
    """
    # Hent eksisterende verdier
    cursor.execute("SELECT * FROM activities WHERE id = ?", (existing_id,))
    existing = cursor.fetchone()
    if not existing:
        return

    columns = [desc[0] for desc in cursor.description]
    existing_dict = dict(zip(columns, existing))

    # Bygg UPDATE med kun felt som er NULL i eksisterende eller har verdi i ny
    updates = []
    values = []
    for col, new_val in new_data.items():
        if col in existing_dict and new_val is not None:
            if existing_dict[col] is None:
                updates.append(f"{col} = ?")
                values.append(new_val)

    if updates:
        values.append(existing_id)
        cursor.execute(f"UPDATE activities SET {', '.join(updates)} WHERE id = ?", values)


def save_activity_to_db(activity: dict):
    """Lagrer aktivitet til SQLite med upsert-logikk."""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Beregn pace hvis det er en løpeaktivitet med distanse
    avg_pace = None
    distance_km = activity.get("distance", 0) / 1000
    moving_time_s = activity.get("moving_time", 0)

    if distance_km > 0 and moving_time_s > 0:
        avg_pace = moving_time_s / distance_km

    # Sjekk om det er et løp
    is_race = activity.get("workout_type") == 1  # 1 = Race i Strava
    start_date = activity["start_date"]

    # UPSERT: Sjekk om aktivitet med samme dato/distanse/tid finnes
    existing_id = find_existing_activity(cursor, start_date, distance_km, moving_time_s)

    if existing_id:
        # Merge nye felt inn i eksisterende rad
        new_data = {
            'name': activity.get("name"),
            'avg_hr': activity.get("average_heartrate"),
            'max_hr': activity.get("max_heartrate"),
            'elevation_gain_m': activity.get("total_elevation_gain"),
            'avg_cadence': activity.get("average_cadence"),
            'perceived_effort': activity.get("perceived_exertion"),
            'is_race': is_race,
        }
        merge_activity_fields(cursor, existing_id, new_data)
        activity_id = existing_id
    else:
        # Ny aktivitet - INSERT
        cursor.execute("""
            INSERT INTO activities (
                id, start_date, sport, name, distance_km, moving_time_s, elapsed_time_s,
                avg_pace_s_per_km, avg_hr, max_hr, elevation_gain_m, avg_cadence,
                perceived_effort, is_race, source, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'strava', ?)
        """, (
            activity["id"],
            start_date,
            activity["type"],
            activity.get("name"),
            distance_km,
            moving_time_s,
            activity.get("elapsed_time"),
            avg_pace,
            activity.get("average_heartrate"),
            activity.get("max_heartrate"),
            activity.get("total_elevation_gain"),
            activity.get("average_cadence"),
            activity.get("perceived_exertion"),
            is_race,
            json.dumps(activity),
        ))
        activity_id = activity["id"]

    # Lagre splits hvis tilgjengelig
    if "splits_metric" in activity:
        for split in activity["splits_metric"]:
            if split.get("distance", 0) >= 900:  # Nesten full km
                pace = split.get("moving_time", 0) / (split.get("distance", 1000) / 1000)
                cursor.execute("""
                    INSERT OR REPLACE INTO splits (activity_id, split_km, pace_s_per_km, hr, elevation_change_m)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    activity_id,
                    split["split"],
                    pace,
                    split.get("average_heartrate"),
                    split.get("elevation_difference"),
                ))

    conn.commit()
    conn.close()


def save_activity_basic(activity: dict):
    """Lagrer grunnleggende aktivitetsdata fra liste-API (uten splits) med upsert-logikk."""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    distance_km = activity.get("distance", 0) / 1000
    moving_time_s = activity.get("moving_time", 0)
    avg_pace = moving_time_s / distance_km if distance_km > 0 else None
    start_date = activity["start_date"]

    is_race = activity.get("workout_type") == 1

    # UPSERT: Sjekk om aktivitet med samme dato/distanse/tid finnes
    existing_id = find_existing_activity(cursor, start_date, distance_km, moving_time_s)

    if existing_id:
        # Merge nye felt inn i eksisterende rad (behold ikke-null verdier)
        new_data = {
            'name': activity.get("name"),
            'avg_hr': activity.get("average_heartrate"),
            'max_hr': activity.get("max_heartrate"),
            'elevation_gain_m': activity.get("total_elevation_gain"),
            'avg_cadence': activity.get("average_cadence"),
            'is_race': is_race,
        }
        merge_activity_fields(cursor, existing_id, new_data)
    else:
        # Ny aktivitet - INSERT
        cursor.execute("""
            INSERT INTO activities (
                id, start_date, sport, name, distance_km, moving_time_s, elapsed_time_s,
                avg_pace_s_per_km, avg_hr, max_hr, elevation_gain_m, avg_cadence,
                is_race, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'strava')
        """, (
            activity["id"],
            start_date,
            activity["type"],
            activity.get("name"),
            distance_km,
            moving_time_s,
            activity.get("elapsed_time"),
            avg_pace,
            activity.get("average_heartrate"),
            activity.get("max_heartrate"),
            activity.get("total_elevation_gain"),
            activity.get("average_cadence"),
            is_race,
        ))

    conn.commit()
    conn.close()


@click.command()
@click.option("--full-history", is_flag=True, help="Hent ALL historikk, ikke bare nye aktiviteter")
@click.option("--authorize", is_flag=True, help="Kun kjør OAuth-autorisering")
@click.option("--with-details", is_flag=True, help="Hent full detalj for alle (tar lang tid)")
@click.option("--test", default=0, type=int, help="Testmodus: hent kun N aktiviteter")
def main(full_history: bool, authorize: bool, with_details: bool, test: int):
    """Henter aktiviteter fra Strava."""

    console.print("[bold blue]Strava Sync[/bold blue]\n")

    # Initialiser database
    init_database()

    # Hent access token
    access_token = get_valid_access_token()

    if authorize:
        console.print("[green]Autorisering fullført![/green]")
        return

    # Bestem startpunkt
    after = None
    if not full_history:
        after = get_last_sync_date()
        if after:
            console.print(f"Henter aktiviteter etter {after.strftime('%Y-%m-%d')}...")
        else:
            console.print("Ingen tidligere sync funnet. Henter all historikk...")
    else:
        console.print("Henter FULL historikk...")

    # Testmodus
    if test > 0:
        console.print(f"[yellow]TESTMODUS: Henter kun {test} aktiviteter[/yellow]\n")

    # Hent aktivitetsliste
    activities = fetch_activities(access_token, after=after, limit=test if test > 0 else 0)

    if not activities:
        console.print("[yellow]Ingen nye aktiviteter å hente.[/yellow]")
        return

    console.print(f"\nFant {len(activities)} aktiviteter.")

    # Sjekk hvilke aktiviteter som allerede er i databasen
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM activities")
    existing_ids = {row[0] for row in cursor.fetchall()}
    conn.close()

    new_activities = [a for a in activities if a["id"] not in existing_ids]
    console.print(f"  - {len(existing_ids)} allerede i databasen")
    console.print(f"  - {len(new_activities)} nye aktiviteter")

    # Lagre grunndata for alle nye aktiviteter (raskt, ingen ekstra API-kall)
    if new_activities:
        console.print("\nLagrer grunndata...")
        for activity in new_activities:
            save_activity_basic(activity)
            save_raw_activity(activity)
        console.print(f"[green]Lagret {len(new_activities)} aktiviteter (grunndata)[/green]")

    # Hent detaljer kun for løp og nyere aktiviteter (siste 90 dager) eller hvis --with-details
    latest_date = None

    if with_details:
        activities_for_details = new_activities
    else:
        cutoff = datetime.now().timestamp() - (90 * 24 * 3600)  # 90 dager
        activities_for_details = [
            a for a in new_activities
            if a.get("workout_type") == 1  # Løp
            or datetime.fromisoformat(a["start_date"].replace("Z", "+00:00")).timestamp() > cutoff
        ]

    if activities_for_details:
        console.print(f"\nHenter detaljer for {len(activities_for_details)} aktiviteter (løp + siste 90 dager)...")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Prosesserer...", total=len(activities_for_details))

            for i, activity in enumerate(activities_for_details):
                try:
                    detailed = fetch_activity_details(access_token, activity["id"])
                    save_raw_activity(detailed)
                    save_activity_to_db(detailed)

                    activity_date = datetime.fromisoformat(detailed["start_date"].replace("Z", "+00:00"))
                    if latest_date is None or activity_date > latest_date:
                        latest_date = activity_date

                    progress.update(task, advance=1, description=f"Prosessert {i+1}/{len(activities_for_details)}")
                    time.sleep(10)

                except Exception as e:
                    console.print(f"[red]Feil ved aktivitet {activity['id']}: {e}[/red]")
                    time.sleep(15)

    # Oppdater sync-status
    if new_activities:
        newest = max(new_activities, key=lambda a: a["start_date"])
        newest_date = datetime.fromisoformat(newest["start_date"].replace("Z", "+00:00"))
        update_sync_status(newest_date)

    # Oppsummering
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM activities WHERE source='strava'")
    total = cursor.fetchone()[0]
    conn.close()

    console.print(f"\n[green]Ferdig![/green]")
    console.print(f"  - Totalt {total} Strava-aktiviteter i databasen")
    console.print(f"  - Database: {DB_PATH}")


if __name__ == "__main__":
    main()
