#!/usr/bin/env python3
"""
Telegram-bot for treningsplan.

Kommandoer:
    /morgen     - Morgenstatus med sync
    /okt        - Evaluer siste økt
    /plan       - Dagens planlagte økt
    /uke        - Ukeoversikt
    /hjelp      - Vis kommandoer

Kjør:
    python scripts/telegram_bot.py

Eller som bakgrunnsprosess:
    nohup python scripts/telegram_bot.py &
"""

import os
import sys
import time
import json
import requests
import subprocess
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Legg til project root i path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Fil for å holde styr på siste oppdatering
OFFSET_FILE = PROJECT_ROOT / "data" / ".telegram_offset"


def get_offset() -> int:
    """Henter siste offset fra fil."""
    if OFFSET_FILE.exists():
        return int(OFFSET_FILE.read_text().strip())
    return 0


def save_offset(offset: int):
    """Lagrer offset til fil."""
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset))


def send_message(text: str, parse_mode: str = None) -> bool:
    """Sender melding til Telegram."""
    # Begrens lengde
    if len(text) > 4000:
        text = text[:3997] + "..."

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        response = requests.post(f"{API_URL}/sendMessage", data=payload, timeout=10)
        return response.json().get("ok", False)
    except Exception as e:
        print(f"Feil ved sending: {e}")
        return False


def run_script(script_name: str, args: list = None) -> str:
    """Kjører et Python-script og returnerer output."""
    cmd = [str(PROJECT_ROOT / ".venv" / "bin" / "python"), str(PROJECT_ROOT / "scripts" / script_name)]
    if args:
        cmd.extend(args)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(PROJECT_ROOT)
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "Tidsavbrudd - scriptet tok for lang tid."
    except Exception as e:
        return f"Feil: {e}"


def clean_output(text: str) -> str:
    """Renser output for Telegram."""
    import re

    # Fjern ANSI fargekoder
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)

    # Fjern rich-formatering
    text = re.sub(r'\[/?[a-z_ ]+\]', '', text)

    # Fjern progress-indikatorer
    text = re.sub(r'[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]', '', text)
    text = re.sub(r'━+\s*\d+%', '', text)

    # Fjern tabell-tegn
    table_chars = '┏┓┗┛┃━┳┻┣┫╋┡┩┯┷├┤┼─│╭╮╯╰'
    for char in table_chars:
        text = text.replace(char, ' ')

    # Fjern sync-output
    lines = text.split('\n')
    filtered = []
    skip_until_status = True

    for line in lines:
        if 'MORGEN STATUS' in line or 'ØKT-EVALUERING' in line:
            skip_until_status = False
        if skip_until_status and ('Syncer' in line or 'Henter' in line or 'Batch' in line or 'FERDIG' in line):
            continue
        if 'Database:' in line:
            continue
        filtered.append(line)

    # Fjern flere mellomrom
    text = '\n'.join(filtered)
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n\n\n+', '\n\n', text)

    return text.strip()


def get_dagens_plan() -> str:
    """Henter dagens planlagte økt fra current_plan.md."""
    plan_file = PROJECT_ROOT / "plan" / "current_plan.md"
    if not plan_file.exists():
        return "Finner ikke treningsplan."

    today = datetime.now()
    day_names = {
        0: 'Man', 1: 'Tir', 2: 'Ons', 3: 'Tor', 4: 'Fre', 5: 'Lør', 6: 'Søn'
    }
    today_prefix = f"{day_names[today.weekday()]} {today.day:02d}.{today.month:02d}"

    content = plan_file.read_text()
    lines = content.split('\n')

    # Finn dagens linje
    for i, line in enumerate(lines):
        if today_prefix in line or f"{today.day}.{today.month:02d}" in line:
            # Returner linjen og litt kontekst
            start = max(0, i - 2)
            end = min(len(lines), i + 3)
            return '\n'.join(lines[start:end])

    return f"Finner ikke plan for {today_prefix}"


def handle_command(command: str) -> str:
    """Håndterer en kommando og returnerer svar."""
    command = command.lower().strip()

    if command in ['/morgen', '/m', 'morgen']:
        send_message("⏳ Henter morgenstatus...")
        output = run_script("morgen_status.py", ["--sync", "--telegram"])
        return clean_output(output)

    elif command in ['/okt', '/økt', 'okt', 'økt']:
        send_message("⏳ Evaluerer siste økt...")
        output = run_script("evaluer_okt.py")
        return clean_output(output)

    elif command in ['/plan', '/p', 'plan']:
        return f"📋 Dagens plan:\n\n{get_dagens_plan()}"

    elif command in ['/uke', '/u', 'uke']:
        send_message("⏳ Genererer ukeoversikt...")
        output = run_script("sync_weekly.py", ["--no-sync"])
        return clean_output(output)

    elif command in ['/hjelp', '/help', '/h', 'hjelp', 'help']:
        return """🏃 Treningsplan-bot

Kommandoer:
/morgen - Morgenstatus (sync + anbefaling)
/okt - Evaluer siste økt
/plan - Dagens planlagte økt
/uke - Ukeoversikt
/hjelp - Vis denne hjelpen"""

    elif command.startswith('/'):
        return f"Ukjent kommando: {command}\n\nSkriv /hjelp for å se tilgjengelige kommandoer."

    else:
        return None  # Ignorer ikke-kommandoer


def poll_updates():
    """Poller etter nye meldinger."""
    offset = get_offset()

    try:
        response = requests.get(
            f"{API_URL}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35
        )
        data = response.json()

        if not data.get("ok"):
            print(f"Feil fra Telegram: {data}")
            return

        for update in data.get("result", []):
            update_id = update["update_id"]
            save_offset(update_id + 1)

            message = update.get("message", {})
            chat_id = str(message.get("chat", {}).get("id", ""))
            text = message.get("text", "")

            # Sjekk at meldingen er fra riktig chat
            if chat_id != CHAT_ID:
                print(f"Ignorerer melding fra {chat_id}")
                continue

            if text:
                print(f"Mottok: {text}")
                response_text = handle_command(text)
                if response_text:
                    send_message(response_text)

    except requests.exceptions.Timeout:
        pass  # Normal timeout, fortsett
    except Exception as e:
        print(f"Polling-feil: {e}")
        time.sleep(5)


def main():
    print(f"🤖 Telegram-bot startet")
    print(f"   Chat ID: {CHAT_ID}")
    print(f"   Lytter på kommandoer...")
    print()

    # Send oppstartmelding
    send_message("🤖 Treningsplan-bot er online!\n\nSkriv /hjelp for kommandoer.")

    while True:
        try:
            poll_updates()
        except KeyboardInterrupt:
            print("\nAvslutter...")
            send_message("🤖 Bot avsluttet.")
            break
        except Exception as e:
            print(f"Feil: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
