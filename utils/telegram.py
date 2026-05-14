"""
Telegram-integrasjon for treningsoppdateringer.

Bruk:
    from utils.telegram import send_message
    send_message("Morgenstatus: VO2 Max 56, Readiness 45")
"""

import os
import requests
from pathlib import Path
from dotenv import load_dotenv

# Last miljøvariabler
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def clean_for_telegram(text: str) -> str:
    """
    Renser tekst for Telegram - fjerner tabeller, ANSI-koder og sync-output.
    Strukturerer output for lesbarhet.
    """
    import re

    # Fjern ANSI fargekoder
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)

    # Fjern rich-formatering [bold], [/bold], etc.
    text = re.sub(r'\[/?[a-z_ ]+\]', '', text)

    # Fjern progress-indikatorer
    text = re.sub(r'[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]', '', text)
    text = re.sub(r'━+\s*\d+%', '', text)

    # Fjern tabell-tegn
    table_chars = '┏┓┗┛┃━┳┻┣┫╋┡┩┯┷├┤┼─│╭╮╯╰'
    for char in table_chars:
        text = text.replace(char, '')

    # Del opp i linjer
    lines = text.split('\n')

    # Finn hvor hovedinnholdet starter (etter sync-info)
    start_idx = 0
    for i, line in enumerate(lines):
        if 'MORGEN STATUS' in line or 'ØKTEVALUERING' in line or 'UKERAPPORT' in line:
            start_idx = i
            break
        if 'ØKT-EVALUERING' in line:
            start_idx = i
            break

    # Ta kun med fra hovedinnholdet
    lines = lines[start_idx:]

    # Rens og strukturer linjer
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()

        # Hopp over tomme og irrelevante linjer
        if not stripped:
            cleaned_lines.append('')
            continue
        if 'Batch' in stripped or 'Hentet' in stripped or 'Henter' in stripped:
            continue
        if 'Database:' in stripped or 'FERDIG!' in stripped:
            continue
        if stripped.startswith('Metrikk') or stripped.startswith('Verdi'):
            continue

        cleaned_lines.append(stripped)

    # Fjern flere tomme linjer på rad
    result = []
    prev_empty = False
    for line in cleaned_lines:
        is_empty = not line.strip()
        if is_empty and prev_empty:
            continue
        result.append(line)
        prev_empty = is_empty

    return '\n'.join(result).strip()


def send_message(text: str, parse_mode: str = None) -> bool:
    """
    Sender melding til Telegram.

    Args:
        text: Meldingstekst
        parse_mode: "HTML" eller "Markdown" (None = ingen formatering)

    Returns:
        True hvis vellykket, False ellers
    """
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN eller TELEGRAM_CHAT_ID mangler i .env")
        return False

    # Rens teksten
    text = clean_for_telegram(text)

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
    }

    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        response = requests.post(url, data=payload, timeout=10)
        return response.json().get("ok", False)
    except Exception as e:
        print(f"Telegram-feil: {e}")
        return False


def format_morgen_status(vo2max: float, readiness: int, hrv: float,
                         acwr: float, anbefaling: str) -> str:
    """Formaterer morgenstatus for Telegram."""

    # Emoji basert på readiness
    if readiness >= 50:
        status_emoji = "🟢"
    elif readiness >= 30:
        status_emoji = "🟡"
    else:
        status_emoji = "🔴"

    return f"""<b>🏃 Morgenstatus</b>

<b>VO2 Max:</b> {vo2max:.1f}
<b>Readiness:</b> {readiness} {status_emoji}
<b>HRV:</b> {hrv:.0f}
<b>ACWR:</b> {acwr:.2f}

<b>Anbefaling:</b>
{anbefaling}"""


def format_okt_evaluering(dato: str, navn: str, distanse: float,
                          pace: str, hr: int, vurdering: str) -> str:
    """Formaterer øktevaluering for Telegram."""

    return f"""<b>📊 Øktevaluering</b>

<b>Dato:</b> {dato}
<b>Økt:</b> {navn}
<b>Distanse:</b> {distanse:.1f} km
<b>Pace:</b> {pace}/km
<b>HR:</b> {hr}

{vurdering}"""


def format_uke_rapport(uke: int, km: float, okter: int,
                       fordeling: str, oppsummering: str) -> str:
    """Formaterer ukerapport for Telegram."""

    return f"""<b>📅 Ukerapport – Uke {uke}</b>

<b>Volum:</b> {km:.1f} km
<b>Økter:</b> {okter}
<b>Fordeling:</b> {fordeling}

{oppsummering}"""


if __name__ == "__main__":
    # Test
    success = send_message("🏃 <b>Test:</b> Telegram-integrasjon fungerer!")
    print("Sendt!" if success else "Feilet")
