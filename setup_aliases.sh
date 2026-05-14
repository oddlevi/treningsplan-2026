#!/bin/bash
#
# Treningsplan – Shell-aliaser
#
# Legg til i ~/.zshrc:
#   source "/Users/oddlevipaulsen/Odd Levi HD/Kodeprosjekter/Treningsplan Garmin Strava/setup_aliases.sh"
#
# Deretter: source ~/.zshrc (eller åpne ny terminal)

TRENINGSPLAN_DIR="/Users/oddlevipaulsen/Odd Levi HD/Kodeprosjekter/Treningsplan Garmin Strava"
VENV_PYTHON="$TRENINGSPLAN_DIR/.venv/bin/python"

# Naviger til prosjektmappa
alias trening='cd "$TRENINGSPLAN_DIR"'

# Daglige kommandoer (terminal)
alias morgen='cd "$TRENINGSPLAN_DIR" && "$VENV_PYTHON" scripts/morgen_status.py --sync'
alias okt='cd "$TRENINGSPLAN_DIR" && "$VENV_PYTHON" scripts/evaluer_okt.py'
alias uke='cd "$TRENINGSPLAN_DIR" && "$VENV_PYTHON" scripts/sync_weekly.py'
alias juster='cd "$TRENINGSPLAN_DIR" && "$VENV_PYTHON" plan/juster.py'
alias diagnose='cd "$TRENINGSPLAN_DIR" && "$VENV_PYTHON" scripts/diagnose_readiness.py'
alias briefing='cd "$TRENINGSPLAN_DIR" && "$VENV_PYTHON" scripts/morgen_briefing.py --vis'

# Telegram-versjoner (t = telegram)
alias tmorgen='cd "$TRENINGSPLAN_DIR" && ./run.sh telegram morgen'
alias tokt='cd "$TRENINGSPLAN_DIR" && ./run.sh telegram okt'
alias tuke='cd "$TRENINGSPLAN_DIR" && ./run.sh telegram uke'

# Sync uten analyse
alias treningsync='cd "$TRENINGSPLAN_DIR" && "$VENV_PYTHON" scripts/fetch_garmin.py --days 2'

# Dashboard
alias dashboard='cd "$TRENINGSPLAN_DIR" && "$TRENINGSPLAN_DIR/.venv/bin/streamlit" run "$TRENINGSPLAN_DIR/dashboard/app.py"'

echo "Treningsplan-aliaser lastet: morgen, okt, uke, juster, diagnose, briefing, trening, treningsync, dashboard"
echo "Telegram-aliaser: tmorgen, tokt, tuke"
