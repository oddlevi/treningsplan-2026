#!/usr/bin/env python3
"""
Sender melding til Telegram.

Bruk:
    python scripts/send_telegram.py "Din melding her"
    python scripts/send_telegram.py --file plan/10k_31mai_2026.md
    python scripts/send_telegram.py --stdin  (leser fra stdin)
"""

import sys
import argparse
from pathlib import Path

# Legg til project root i path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.telegram import send_message


def format_for_telegram(text: str) -> str:
    """
    Formaterer tekst for Telegram.
    Konverterer markdown til Telegram-vennlig format.
    """
    lines = text.strip().split('\n')
    result = []

    for line in lines:
        # Konverter markdown headers til bold
        if line.startswith('### '):
            line = f"*{line[4:]}*"
        elif line.startswith('## '):
            line = f"*{line[3:]}*"
        elif line.startswith('# '):
            line = f"*{line[2:]}*"

        # Fjern markdown tabeller (komplekse)
        if line.startswith('|') and '|' in line[1:]:
            # Enkel tabell-konvertering
            cells = [c.strip() for c in line.split('|')[1:-1]]
            if cells and not all(c.replace('-', '').replace(':', '') == '' for c in cells):
                line = '  '.join(cells)
            else:
                continue  # Hopp over separator-linjer

        # Fjern --- separatorer
        if line.strip() == '---':
            line = ''

        result.append(line)

    # Fjern flere tomme linjer på rad
    cleaned = []
    prev_empty = False
    for line in result:
        is_empty = not line.strip()
        if is_empty and prev_empty:
            continue
        cleaned.append(line)
        prev_empty = is_empty

    return '\n'.join(cleaned).strip()


def truncate_message(text: str, max_length: int = 4000) -> list[str]:
    """
    Deler opp lange meldinger i flere deler.
    Telegram har maks 4096 tegn per melding.
    """
    if len(text) <= max_length:
        return [text]

    parts = []
    lines = text.split('\n')
    current_part = []
    current_length = 0

    for line in lines:
        line_length = len(line) + 1  # +1 for newline

        if current_length + line_length > max_length:
            if current_part:
                parts.append('\n'.join(current_part))
            current_part = [line]
            current_length = line_length
        else:
            current_part.append(line)
            current_length += line_length

    if current_part:
        parts.append('\n'.join(current_part))

    return parts


def main():
    parser = argparse.ArgumentParser(description='Send melding til Telegram')
    parser.add_argument('message', nargs='?', help='Melding å sende')
    parser.add_argument('--file', '-f', help='Les melding fra fil')
    parser.add_argument('--stdin', '-s', action='store_true', help='Les fra stdin')
    parser.add_argument('--raw', '-r', action='store_true', help='Ikke formater teksten')
    args = parser.parse_args()

    # Hent tekst
    if args.file:
        path = Path(args.file)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            print(f"Finner ikke fil: {path}")
            sys.exit(1)
        text = path.read_text()
    elif args.stdin:
        text = sys.stdin.read()
    elif args.message:
        text = args.message
    else:
        print("Ingen melding gitt. Bruk --help for hjelp.")
        sys.exit(1)

    # Formater hvis ikke --raw
    if not args.raw:
        text = format_for_telegram(text)

    # Del opp og send
    parts = truncate_message(text)

    success = True
    for i, part in enumerate(parts):
        if len(parts) > 1:
            part = f"({i+1}/{len(parts)})\n\n{part}"

        if not send_message(part):
            success = False
            print(f"Feil ved sending av del {i+1}")

    if success:
        print(f"✓ Sendt til Telegram ({len(parts)} del{'er' if len(parts) > 1 else ''})")
    else:
        print("✗ Noe feilet")
        sys.exit(1)


if __name__ == "__main__":
    main()
