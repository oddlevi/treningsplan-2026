#!/bin/bash
#
# Treningsplan – Kommando-snarvei
#
# Bruk:
#   ./run.sh morgen     - Morgenstatus med Garmin-sync
#   ./run.sh okt        - Evaluer siste økt
#   ./run.sh uke        - Generer ukerapport
#   ./run.sh juster     - Foreslå plan-justeringer
#   ./run.sh diagnose   - Analyser årsaker til lav Readiness
#   ./run.sh sync       - Bare sync fra Garmin (uten analyse)
#   ./run.sh dashboard  - Åpne treningsdashboard (Streamlit)
#   ./run.sh briefing   - Vis dagens treningsbrief
#   ./run.sh brief      - Generer ukesbrief
#
# Telegram-støtte:
#   ./run.sh morgen --telegram   - Send også til Telegram
#   ./run.sh telegram morgen     - Kun send til Telegram (ingen terminal-output)
#
# Etter alias-oppsett kan du også bruke: morgen, okt, uke, etc.

set -e

# Finn prosjektmappa (der dette scriptet ligger)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

# Sjekk at venv finnes
if [ ! -f "$VENV_PYTHON" ]; then
    echo "FEIL: .venv/bin/python finnes ikke."
    echo "Kjør: cd \"$SCRIPT_DIR\" && uv venv && uv pip install -r requirements.txt"
    exit 1
fi

# Naviger til prosjektmappa
cd "$SCRIPT_DIR"

# Funksjon for å sende til Telegram
send_telegram() {
    local message="$1"
    "$VENV_PYTHON" -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from utils.telegram import send_message
# Fjern ANSI-koder og konverter til ren tekst
import re
text = '''$message'''
text = re.sub(r'\x1b\[[0-9;]*m', '', text)
text = re.sub(r'\[.*?\]', '', text)  # Fjern rich-formatering
send_message(text, parse_mode='HTML')
"
}

# Sjekk for --telegram flagg
SEND_TELEGRAM=false
TELEGRAM_ONLY=false

for arg in "$@"; do
    if [ "$arg" = "--telegram" ]; then
        SEND_TELEGRAM=true
    fi
done

# Sjekk for "telegram" som første argument
if [ "${1:-}" = "telegram" ]; then
    TELEGRAM_ONLY=true
    shift
fi

# Parse kommando
case "${1:-help}" in
    morgen|m)
        if [ "$TELEGRAM_ONLY" = true ]; then
            output=$("$VENV_PYTHON" scripts/morgen_status.py --sync --telegram 2>&1)
            send_telegram "$output"
        elif [ "$SEND_TELEGRAM" = true ]; then
            "$VENV_PYTHON" scripts/morgen_status.py --sync 2>&1 | tee >(
                output=$(cat)
                send_telegram "$output"
            )
        else
            "$VENV_PYTHON" scripts/morgen_status.py --sync
        fi
        ;;
    okt|o|evaluer)
        if [ "$TELEGRAM_ONLY" = true ]; then
            output=$("$VENV_PYTHON" scripts/evaluer_okt.py 2>&1)
            send_telegram "$output"
        elif [ "$SEND_TELEGRAM" = true ]; then
            "$VENV_PYTHON" scripts/evaluer_okt.py 2>&1 | tee >(
                output=$(cat)
                send_telegram "$output"
            )
        else
            "$VENV_PYTHON" scripts/evaluer_okt.py
        fi
        ;;
    uke|u|ukerapport)
        if [ "$TELEGRAM_ONLY" = true ]; then
            output=$("$VENV_PYTHON" scripts/sync_weekly.py 2>&1)
            send_telegram "$output"
        elif [ "$SEND_TELEGRAM" = true ]; then
            "$VENV_PYTHON" scripts/sync_weekly.py 2>&1 | tee >(
                output=$(cat)
                send_telegram "$output"
            )
        else
            "$VENV_PYTHON" scripts/sync_weekly.py
        fi
        ;;
    juster|j)
        "$VENV_PYTHON" plan/juster.py
        ;;
    diagnose|d)
        "$VENV_PYTHON" scripts/diagnose_readiness.py
        ;;
    sync|s)
        "$VENV_PYTHON" scripts/fetch_garmin.py --days 2
        ;;
    intervaller|int|i)
        "$VENV_PYTHON" scripts/fetch_intervals.py "${@:2}"
        ;;
    dashboard|dash|db)
        "$SCRIPT_DIR/.venv/bin/streamlit" run "$SCRIPT_DIR/dashboard/app.py"
        ;;
    briefing)
        "$VENV_PYTHON" scripts/morgen_briefing.py --vis
        ;;
    brief)
        "$VENV_PYTHON" scripts/ukesbrief.py "${@:2}"
        ;;
    help|h|--help|-h)
        echo "Treningsplan – Kommandoer:"
        echo ""
        echo "  ./run.sh morgen     Morgenstatus med Garmin-sync"
        echo "  ./run.sh okt        Evaluer siste økt"
        echo "  ./run.sh uke        Generer ukerapport"
        echo "  ./run.sh juster     Foreslå plan-justeringer"
        echo "  ./run.sh diagnose   Analyser årsaker til lav Readiness"
        echo "  ./run.sh sync       Bare sync fra Garmin"
        echo "  ./run.sh dashboard  Åpne treningsdashboard (http://localhost:8501)"
        echo "  ./run.sh intervaller  Hent og vis intervalldata (siste 8 uker)"
        echo "  ./run.sh briefing     Vis dagens treningsbrief"
        echo "  ./run.sh brief        Generer ukesbrief (søndag kveld)"
        echo ""
        echo "Telegram:"
        echo "  ./run.sh morgen --telegram   Send også til Telegram"
        echo "  ./run.sh telegram morgen     Kun Telegram (stille)"
        echo ""
        echo "Forkortelser: m=morgen, o=okt, u=uke, j=juster, d=diagnose, s=sync, dash=dashboard, i=intervaller"
        ;;
    *)
        echo "Ukjent kommando: $1"
        echo "Bruk: ./run.sh help for å se tilgjengelige kommandoer"
        exit 1
        ;;
esac
