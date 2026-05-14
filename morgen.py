#!/usr/bin/env python3
"""
Snarvei til morgenrutine.

Kanonisk versjon: scripts/morgen_status.py
Dette scriptet kaller: .venv/bin/python scripts/morgen_status.py --sync

Bruk helst: ./run.sh morgen (fra hvor som helst etter alias-oppsett)
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"

if __name__ == "__main__":
    if not VENV_PYTHON.exists():
        print(f"FEIL: {VENV_PYTHON} finnes ikke. Kjør: uv venv && uv pip install -r requirements.txt")
        sys.exit(1)

    script = PROJECT_ROOT / "scripts" / "morgen_status.py"
    sys.exit(subprocess.call([str(VENV_PYTHON), str(script), "--sync"]))
