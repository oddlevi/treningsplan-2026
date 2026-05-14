#!/usr/bin/env python3
"""
Treningsplan-generator basert på fase, pace-soner og faktisk treningshistorikk.

Genererer 4 uker av gangen med detaljerte daglige økter.
Leser fase fra plan/current_plan.md eller --fase argument.
"""

import argparse
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
PLAN_PATH = PROJECT_ROOT / "plan" / "current_plan.md"


@dataclass
class PaceZones:
    """Pace-soner i sekunder per km."""
    recovery_low: int = 337    # 5:37
    recovery_high: int = 313   # 5:13
    easy_low: int = 313        # 5:13
    easy_high: int = 281       # 4:41
    marathon_low: int = 261    # 4:21
    marathon_high: int = 249   # 4:09
    threshold_low: int = 249   # 4:09
    threshold_high: int = 238  # 3:58
    vo2max_low: int = 231      # 3:51
    vo2max_high: int = 219     # 3:39
    race_10k: int = 210        # 3:30 (mål)

    @classmethod
    def from_10k_pace(cls, race_pace_s: int):
        """Beregn alle soner fra 10k race pace."""
        return cls(
            recovery_low=round(race_pace_s / 0.65),
            recovery_high=round(race_pace_s / 0.70),
            easy_low=round(race_pace_s / 0.70),
            easy_high=round(race_pace_s / 0.78),
            marathon_low=round(race_pace_s / 0.84),
            marathon_high=round(race_pace_s / 0.88),
            threshold_low=round(race_pace_s / 0.88),
            threshold_high=round(race_pace_s / 0.92),
            vo2max_low=round(race_pace_s / 0.95),
            vo2max_high=round(race_pace_s / 1.00),
            race_10k=round(race_pace_s / 1.05)  # Sub-35 mål
        )


def format_pace(seconds: int) -> str:
    """Formater sekunder per km som MM:SS."""
    mins = seconds // 60
    secs = seconds % 60
    return f"{mins}:{secs:02d}"


def get_recent_volume(days: int = 28) -> dict:
    """Hent faktisk volum fra databasen siste N dager, med ukesvis breakdown."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Hent per-uke volum for å identifisere arbeidsvolum vs. deload
    cursor.execute("""
        SELECT
            strftime('%Y-%W', date(CASE
                WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
                ELSE start_date
            END)) as week,
            ROUND(SUM(distance_km), 1) as weekly_km,
            COUNT(*) as sessions
        FROM activities
        WHERE lower(sport) IN ('running', 'run', 'trail_running', 'treadmill_running')
        AND date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= date('now', ? || ' days')
        AND distance_km > 0.5
        GROUP BY week
        ORDER BY week DESC
    """, (f"-{days}",))

    weeks = cursor.fetchall()

    # Finn arbeidsvolum (median av ukene, ignorerer outliers)
    weekly_volumes = [w['weekly_km'] for w in weeks if w['weekly_km']]
    if weekly_volumes:
        weekly_volumes_sorted = sorted(weekly_volumes)
        # Bruk median for å unngå at deload-uker drar ned snittet
        median_idx = len(weekly_volumes_sorted) // 2
        working_volume = weekly_volumes_sorted[median_idx]
        # Eller bruk høyeste "normale" uke (ikke outlier-høy)
        typical_high = max(v for v in weekly_volumes if v <= max(weekly_volumes) * 0.95)
    else:
        working_volume = 0
        typical_high = 0

    cursor.execute("""
        SELECT
            ROUND(SUM(distance_km), 1) as total_km,
            COUNT(*) as sessions,
            ROUND(SUM(distance_km) / ?, 1) as weekly_avg,
            ROUND(COUNT(*) * 1.0 / ?, 1) as sessions_per_week,
            ROUND(MAX(distance_km), 1) as longest_run
        FROM activities
        WHERE lower(sport) IN ('running', 'run', 'trail_running', 'treadmill_running')
        AND date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= date('now', ? || ' days')
        AND distance_km > 0.5
    """, (days / 7, days / 7, f"-{days}"))

    result = cursor.fetchone()
    conn.close()

    return {
        'total_km': result['total_km'] or 0,
        'sessions': result['sessions'] or 0,
        'weekly_avg': result['weekly_avg'] or 0,
        'working_volume': working_volume,  # Typisk arbeidsuke (ikke deload)
        'sessions_per_week': result['sessions_per_week'] or 0,
        'longest_run': result['longest_run'] or 0,
        'weekly_breakdown': [(w['week'], w['weekly_km']) for w in weeks]
    }


def get_best_10k_pace() -> int:
    """Hent beste 10k pace fra databasen (sekunder per km)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT MIN(avg_pace_s_per_km) as best_pace
        FROM activities
        WHERE lower(sport) IN ('running', 'run')
        AND distance_km BETWEEN 9.5 AND 10.5
        AND avg_pace_s_per_km IS NOT NULL
    """)

    result = cursor.fetchone()
    conn.close()

    return int(result[0]) if result[0] else 219  # Default 3:39


# Fase-definisjoner
PHASES = {
    '1A': {
        'name': 'Comeback/Gjenoppbygging',
        'weeks': 4,
        'hard_per_week': 1,
        'focus': 'Sone-fordeling (80% lett), aerob base, ingen race-pace',
        'long_run_max': 18,
        'volume_target': 65,
        'intensity': 'terskel-light'
    },
    '1B': {
        'name': 'Build',
        'weeks': 4,
        'hard_per_week': 2,
        'focus': 'VO2 Max + terskel, volum 65-70 km/uke',
        'long_run_max': 22,
        'volume_target': 70,
        'intensity': 'vo2max + terskel'
    },
    '1C': {
        'name': 'Peak',
        'weeks': 3,
        'hard_per_week': 2,
        'focus': 'Race-spesifikk, 10k-pace intervaller',
        'long_run_max': 20,
        'volume_target': 63,
        'intensity': '10k-pace + fartlek'
    },
    '1D': {
        'name': 'Taper + Race',
        'weeks': 1,
        'hard_per_week': 1,
        'focus': 'Reduksjon, holde systemet våkent',
        'long_run_max': 0,
        'volume_target': 35,
        'intensity': 'strides + lett 10k-pace'
    },
    '2A': {
        'name': 'Aerob Base (Halv)',
        'weeks': 4,
        'hard_per_week': 1,
        'focus': 'Volumøkning, lange turer opp til 25 km',
        'long_run_max': 25,
        'volume_target': 80,
        'intensity': 'tempo-finish på langturer'
    },
    '2B': {
        'name': 'Terskel-Build (Halv)',
        'weeks': 4,
        'hard_per_week': 2,
        'focus': 'Lange terskelintervaller, marathon pace',
        'long_run_max': 28,
        'volume_target': 85,
        'intensity': 'terskel + marathon pace'
    },
    '2C': {
        'name': 'Spesifikk (Halv)',
        'weeks': 3,
        'hard_per_week': 2,
        'focus': 'Halv race pace, lange progresjoner',
        'long_run_max': 25,
        'volume_target': 75,
        'intensity': 'halv-pace + progresjon'
    },
    '2D': {
        'name': 'Taper + Race (Halv)',
        'weeks': 1,
        'hard_per_week': 1,
        'focus': 'Reduksjon før halvmaraton',
        'long_run_max': 0,
        'volume_target': 50,
        'intensity': 'strides + lett tempo'
    }
}


@dataclass
class Session:
    """Enkeltøkt."""
    day: str
    name: str
    description: str
    distance_km: float
    pace_range: str
    purpose: str
    is_hard: bool = False


def generate_week_1a(week_num: int, target_volume: float, zones: PaceZones, is_deload: bool = False) -> list[Session]:
    """Generer uke for fase 1A (comeback)."""
    sessions = []

    easy_pace = f"{format_pace(zones.easy_low)}-{format_pace(zones.easy_high)}"
    recovery_pace = f"{format_pace(zones.recovery_low)}-{format_pace(zones.recovery_high)}"
    threshold_pace = f"{format_pace(zones.threshold_low)}-{format_pace(zones.threshold_high)}"
    warmup_pace = f"{format_pace(zones.easy_low)}"

    if is_deload:
        # Nedtrappingsuke: -20% volum, ingen hard økt
        deload_vol = target_volume * 0.8
        long_run = min(15, deload_vol * 0.35)
        easy_runs = (deload_vol - long_run) / 3

        sessions = [
            Session("Mandag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
            Session("Tirsdag", "Rolig løp", f"{easy_runs:.0f} km @ {easy_pace}", easy_runs, easy_pace,
                    "Aktiv restitusjon, sone 2"),
            Session("Onsdag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
            Session("Torsdag", "Rolig løp", f"{easy_runs:.0f} km @ {easy_pace}", easy_runs, easy_pace,
                    "Lett aerob, holde rytme"),
            Session("Fredag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
            Session("Lørdag", "Lang tur (lett)", f"{long_run:.0f} km @ {easy_pace}", long_run, easy_pace,
                    "Aerob utholdenhet, ingen press"),
            Session("Søndag", "Rolig løp", f"{easy_runs:.0f} km @ {recovery_pace}", easy_runs, recovery_pace,
                    "Aktiv restitusjon etter langtur"),
        ]
    else:
        # Normal uke: 1 hard økt (terskel-light), resten lett
        # Fordeling: langtur ~28%, terskel ~20%, 3 lette ~52%
        long_run = min(14 + week_num * 1.5, 18)  # 15.5, 17, 18 km
        terskel_dist = 12 + week_num * 0.5       # 12.5, 13, 13.5 km
        remaining = target_volume - long_run - terskel_dist - 6  # 6 km søndag fast
        easy_per_day = remaining / 2  # Fordelt på onsdag og torsdag

        sessions = [
            Session("Mandag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
            Session("Tirsdag", "Terskel-light",
                    f"15 min oppvarming @ {warmup_pace}, 4×5 min @ {threshold_pace} (p=2 min jog), 10 min nedjogg",
                    terskel_dist, threshold_pace,
                    "Heve laktatterskel gradvis, ikke maks innsats", is_hard=True),
            Session("Onsdag", "Rolig løp", f"{easy_per_day:.0f} km @ {easy_pace}", easy_per_day, easy_pace,
                    "Restitusjon fra terskel, sone 2"),
            Session("Torsdag", "Rolig løp", f"{easy_per_day:.0f} km @ {easy_pace}", easy_per_day, easy_pace,
                    "Aerob base, sone 2"),
            Session("Fredag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
            Session("Lørdag", "Lang tur",
                    f"{long_run:.0f} km @ {easy_pace}, siste 3 km kan være litt raskere hvis det føles bra",
                    long_run, easy_pace,
                    "Aerob utholdenhet, fettforbrenning"),
            Session("Søndag", "Rolig løp", f"6 km @ {recovery_pace}", 6, recovery_pace,
                    "Aktiv restitusjon etter langtur"),
        ]

    return sessions


def generate_week_1b(week_num: int, target_volume: float, zones: PaceZones, is_deload: bool = False) -> list[Session]:
    """Generer uke for fase 1B (build)."""
    easy_pace = f"{format_pace(zones.easy_low)}-{format_pace(zones.easy_high)}"
    recovery_pace = f"{format_pace(zones.recovery_low)}-{format_pace(zones.recovery_high)}"
    threshold_pace = f"{format_pace(zones.threshold_low)}-{format_pace(zones.threshold_high)}"
    vo2max_pace = f"{format_pace(zones.vo2max_low)}-{format_pace(zones.vo2max_high)}"
    marathon_pace = f"{format_pace(zones.marathon_low)}-{format_pace(zones.marathon_high)}"
    warmup_pace = f"{format_pace(zones.easy_low)}"

    if is_deload:
        return [
            Session("Mandag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
            Session("Tirsdag", "Rolig løp + strides", f"8 km @ {easy_pace} + 4×100m strides", 8, easy_pace,
                    "Hold systemet aktivt"),
            Session("Onsdag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
            Session("Torsdag", "Lett terskel", f"10 min oppvarming, 2×6 min @ {threshold_pace}, 10 min nedjogg",
                    10, threshold_pace, "Minne kroppen på intensitet", is_hard=True),
            Session("Fredag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
            Session("Lørdag", "Rolig langtur", f"16 km @ {easy_pace}", 16, easy_pace,
                    "Aerob vedlikehold"),
            Session("Søndag", "Rolig løp", f"6 km @ {recovery_pace}", 6, recovery_pace,
                    "Aktiv restitusjon"),
        ]

    # Normal uke: 1× VO2 Max + 1× terskel
    reps = 5 + (week_num - 1)  # 5, 6, 7, 8 reps
    long_run = 18 + (week_num - 1)  # 18, 19, 20, 21 km

    return [
        Session("Mandag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
        Session("Tirsdag", "VO2 Max-intervall",
                f"15 min oppvarming @ {warmup_pace}, {reps}×1000m @ {vo2max_pace} (p=400m jog), 10 min nedjogg",
                13 + week_num * 0.5, vo2max_pace,
                "Heve maksimalt oksygenopptak", is_hard=True),
        Session("Onsdag", "Rolig løp", f"8 km @ {easy_pace}", 8, easy_pace,
                "Restitusjon fra VO2 Max"),
        Session("Torsdag", "Terskel-intervall",
                f"15 min oppvarming, {3 + week_num // 2}×8 min @ {threshold_pace} (p=2 min jog), 10 min nedjogg",
                14 + week_num * 0.3, threshold_pace,
                "Heve laktatterskel", is_hard=True),
        Session("Fredag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
        Session("Lørdag", "Lang tur med progresjon",
                f"{long_run} km: {long_run - 5} km @ {easy_pace}, siste 5 km @ {marathon_pace}",
                long_run, f"{easy_pace} → {marathon_pace}",
                "Aerob utholdenhet + tempo-finish"),
        Session("Søndag", "Rolig løp", f"7 km @ {recovery_pace}", 7, recovery_pace,
                "Aktiv restitusjon etter langtur"),
    ]


def generate_week_1c(week_num: int, target_volume: float, zones: PaceZones, is_deload: bool = False) -> list[Session]:
    """Generer uke for fase 1C (peak)."""
    easy_pace = f"{format_pace(zones.easy_low)}-{format_pace(zones.easy_high)}"
    recovery_pace = f"{format_pace(zones.recovery_low)}-{format_pace(zones.recovery_high)}"
    race_pace = f"{format_pace(zones.race_10k)}-{format_pace(zones.vo2max_high)}"
    vo2max_pace = f"{format_pace(zones.vo2max_low)}-{format_pace(zones.vo2max_high)}"
    warmup_pace = f"{format_pace(zones.easy_low)}"

    # Race-spesifikk trening
    return [
        Session("Mandag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
        Session("Tirsdag", "10k-pace intervall",
                f"15 min oppvarming @ {warmup_pace}, {6 - week_num + 1}×1000m @ {race_pace} (p=400m jog), 10 min nedjogg",
                13, race_pace,
                "Race-spesifikk fart", is_hard=True),
        Session("Onsdag", "Rolig løp", f"7 km @ {easy_pace}", 7, easy_pace,
                "Restitusjon"),
        Session("Torsdag", "Fartlek / korte reps",
                f"15 min oppvarming, 10×400m @ {format_pace(zones.race_10k - 10)} (p=200m jog), 10 min nedjogg",
                11, f"{format_pace(zones.race_10k - 10)}",
                "Beinspeed og løpsøkonomi", is_hard=True),
        Session("Fredag", "Hvile", "Restitusjon", 0, "-", "Fullstendig hvile"),
        Session("Lørdag", "Lang tur",
                f"18 km @ {easy_pace}, siste 3 km @ {format_pace(zones.marathon_high)}",
                18, easy_pace,
                "Aerob vedlikehold med tempo-finish"),
        Session("Søndag", "Rolig løp", f"6 km @ {recovery_pace}", 6, recovery_pace,
                "Aktiv restitusjon"),
    ]


def generate_week_1d(zones: PaceZones) -> list[Session]:
    """Generer taper-uke før 10k race."""
    easy_pace = f"{format_pace(zones.easy_low)}-{format_pace(zones.easy_high)}"
    race_pace = f"{format_pace(zones.race_10k)}"
    warmup_pace = f"{format_pace(zones.easy_low)}"

    return [
        Session("Mandag", "Hvile", "Full hvile før race-uke", 0, "-", "Mental og fysisk hvile"),
        Session("Tirsdag", "Lett løp + strides",
                f"8 km @ {easy_pace} + 4×200m strides @ {race_pace}",
                8, easy_pace,
                "Hold systemet aktivt"),
        Session("Onsdag", "Rolig løp", f"6 km @ {easy_pace}", 6, easy_pace,
                "Lett bevegelse"),
        Session("Torsdag", "Aktivering",
                f"5 km @ {easy_pace} + 3×400m @ {race_pace} (p=400m jog)",
                7, f"{easy_pace} / {race_pace}",
                "Vekke systemet før race", is_hard=True),
        Session("Fredag", "Hvile", "Hvile før race", 0, "-", "Karbo-loading, god søvn"),
        Session("Lørdag", "10K RACE",
                f"3 km oppvarming @ {warmup_pace}, 10K RACE @ {race_pace} (mål: sub 35:00), 2 km nedjogg",
                15, race_pace,
                "A-RACE: Sub-35 10k", is_hard=True),
        Session("Søndag", "Restitusjon", f"5 km @ {format_pace(zones.recovery_low)} eller hvile",
                5, f"{format_pace(zones.recovery_low)}",
                "Aktiv restitusjon etter race"),
    ]


def generate_phase_weeks(phase: str, start_volume: float, zones: PaceZones) -> list[tuple[int, list[Session], float]]:
    """Generer alle uker for en fase."""
    phase_info = PHASES[phase]
    weeks = []

    for week_num in range(1, 5):  # Alltid generer 4 uker
        is_deload = (week_num == 4)

        # Beregn målvolum for uken
        if is_deload:
            target_volume = start_volume * 0.8  # -20%
        else:
            # Progressiv økning: +10% per uke opp til phase target
            progress = min(1.0 + (week_num - 1) * 0.10, phase_info['volume_target'] / start_volume)
            target_volume = min(start_volume * progress, phase_info['volume_target'])

        # Generer økter basert på fase
        if phase == '1A':
            sessions = generate_week_1a(week_num, target_volume, zones, is_deload)
        elif phase == '1B':
            sessions = generate_week_1b(week_num, target_volume, zones, is_deload)
        elif phase == '1C':
            sessions = generate_week_1c(week_num, target_volume, zones, is_deload)
        elif phase == '1D':
            sessions = generate_week_1d(zones)
        else:
            # For fase 2, bruk 1B-struktur som base (kan utvides senere)
            sessions = generate_week_1b(week_num, target_volume, zones, is_deload)

        weeks.append((week_num, sessions, target_volume))

    return weeks


def calculate_week_totals(sessions: list[Session]) -> dict:
    """Beregn totaler for en uke."""
    total_km = sum(s.distance_km for s in sessions)
    total_sessions = sum(1 for s in sessions if s.distance_km > 0)
    hard_sessions = sum(1 for s in sessions if s.is_hard)
    easy_sessions = total_sessions - hard_sessions

    return {
        'total_km': total_km,
        'total_sessions': total_sessions,
        'hard_sessions': hard_sessions,
        'easy_sessions': easy_sessions,
        'easy_pct': (easy_sessions / total_sessions * 100) if total_sessions > 0 else 0
    }


def format_plan(phase: str, weeks: list, zones: PaceZones, start_date: datetime) -> str:
    """Formater plan som markdown."""
    phase_info = PHASES[phase]
    lines = []

    # Header
    lines.append(f"# Treningsplan – Fase {phase}: {phase_info['name']}\n")
    lines.append(f"*Generert {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
    lines.append(f"**Periode:** {start_date.strftime('%d.%m.%Y')} – {(start_date + timedelta(weeks=4)).strftime('%d.%m.%Y')}\n")
    lines.append("")

    # Fase-oversikt
    lines.append("## Fase-oversikt\n")
    lines.append(f"| Parameter | Verdi |")
    lines.append(f"|-----------|-------|")
    lines.append(f"| Fokus | {phase_info['focus']} |")
    lines.append(f"| Harde økter/uke | {phase_info['hard_per_week']} |")
    lines.append(f"| Mål langtur | {phase_info['long_run_max']} km |")
    lines.append(f"| Målvolum | {phase_info['volume_target']} km/uke |")
    lines.append("")

    # Pace-soner referanse
    lines.append("## Pace-soner (basert på 10k-PB)\n")
    lines.append("| Sone | Pace |")
    lines.append("|------|------|")
    lines.append(f"| Restitusjon | {format_pace(zones.recovery_high)}-{format_pace(zones.recovery_low)} |")
    lines.append(f"| Lett/Sone 2 | {format_pace(zones.easy_high)}-{format_pace(zones.easy_low)} |")
    lines.append(f"| Marathon | {format_pace(zones.marathon_high)}-{format_pace(zones.marathon_low)} |")
    lines.append(f"| Terskel | {format_pace(zones.threshold_high)}-{format_pace(zones.threshold_low)} |")
    lines.append(f"| VO2 Max | {format_pace(zones.vo2max_high)}-{format_pace(zones.vo2max_low)} |")
    lines.append(f"| 10k mål | {format_pace(zones.race_10k)} |")
    lines.append("")

    # Uker
    for week_num, sessions, target_volume in weeks:
        week_start = start_date + timedelta(weeks=week_num - 1)
        week_end = week_start + timedelta(days=6)
        totals = calculate_week_totals(sessions)

        is_deload = (week_num == 4)
        week_type = " (Nedtrapping)" if is_deload else ""

        lines.append("---\n")
        lines.append(f"## Uke {week_num}{week_type}\n")
        lines.append(f"**{week_start.strftime('%d.%m')} – {week_end.strftime('%d.%m.%Y')}**\n")
        lines.append("")

        # Ukesammendrag
        lines.append("### Ukesammendrag\n")
        lines.append(f"| Metrikk | Verdi |")
        lines.append(f"|---------|-------|")
        lines.append(f"| Målvolum | {totals['total_km']:.0f} km |")
        lines.append(f"| Økter | {totals['total_sessions']} |")
        lines.append(f"| Lette | {totals['easy_sessions']} ({totals['easy_pct']:.0f}%) |")
        lines.append(f"| Harde | {totals['hard_sessions']} |")
        lines.append("")

        # Fokus for uken
        if is_deload:
            lines.append("**Fokus:** Restitusjon og superkompenasjon. Redusert volum, ingen hard intensitet.\n")
        elif week_num == 1:
            lines.append("**Fokus:** Etabler rytme, prioriter 80% lett intensitet. Ikke press tempo.\n")
        elif week_num == 2:
            lines.append("**Fokus:** Første uke med terskel-stimuli. Fokus på kontrollert innsats.\n")
        elif week_num == 3:
            lines.append("**Fokus:** Høyeste volum denne blokken. Langtur med god energi.\n")
        lines.append("")

        # Daglige økter
        lines.append("### Daglig plan\n")
        lines.append("| Dag | Økt | Distanse | Pace | Hensikt |")
        lines.append("|-----|-----|----------|------|---------|")

        for session in sessions:
            intensity = "🔴" if session.is_hard else ("⚪" if session.distance_km == 0 else "🟢")
            dist = f"{session.distance_km:.0f} km" if session.distance_km > 0 else "Hvile"
            lines.append(f"| {session.day} | {intensity} {session.name} | {dist} | {session.pace_range} | {session.purpose} |")

        lines.append("")

        # Detaljerte øktbeskrivelser for harde økter
        hard_sessions = [s for s in sessions if s.is_hard]
        if hard_sessions:
            lines.append("### Detaljerte øktbeskrivelser\n")
            for session in hard_sessions:
                lines.append(f"**{session.day} – {session.name}**\n")
                lines.append(f"{session.description}\n")
                lines.append(f"*Totalt: ~{session.distance_km:.0f} km. Hensikt: {session.purpose}.*\n")
                lines.append("")

    # Footer
    lines.append("---\n")
    lines.append("## Justeringsregler\n")
    lines.append("- **HRV-status \"low\" 2+ dager:** Bytt hard økt med rolig\n")
    lines.append("- **Training Readiness <50:** Flytt hard økt til neste dag\n")
    lines.append("- **Sykdom/skade:** Stopp, vurder på nytt når frisk\n")
    lines.append("- **Føler deg sterk:** IKKE øk intensitet, hold planen\n")
    lines.append("")
    lines.append("*Neste steg: Kjør `analyse/baseline.py` etter uke 4 for å evaluere fremgang.*\n")

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Generer treningsplan')
    parser.add_argument('--fase', type=str, default='1A',
                        choices=['1A', '1B', '1C', '1D', '2A', '2B', '2C', '2D'],
                        help='Treningsfase (default: 1A)')
    parser.add_argument('--start', type=str, default=None,
                        help='Startdato (YYYY-MM-DD), default: neste mandag')
    parser.add_argument('--volum', type=float, default=None,
                        help='Overstyr startvolum (km/uke), default: beregnes fra database')
    args = parser.parse_args()

    # Hent faktisk volum
    recent = get_recent_volume(28)
    print(f"Siste 4 ukers breakdown: {recent['weekly_breakdown']}")
    print(f"Snittvolum: {recent['weekly_avg']:.1f} km/uke")
    print(f"Arbeidsvolum (typisk uke): {recent['working_volume']:.1f} km/uke")
    print(f"Økter/uke: {recent['sessions_per_week']:.1f}")

    # Bruk arbeidsvolum som utgangspunkt (ikke snitt som inkluderer deload-uker)
    if args.volum:
        start_volume = args.volum
        print(f"Bruker manuelt angitt startvolum: {start_volume:.0f} km/uke")
    else:
        start_volume = recent['working_volume'] if recent['working_volume'] > 0 else recent['weekly_avg']

    # Hent beste 10k pace og beregn soner
    best_10k_pace = get_best_10k_pace()
    zones = PaceZones.from_10k_pace(best_10k_pace)
    print(f"Beste 10k-pace: {format_pace(best_10k_pace)}")
    print(f"Pace-soner beregnet: Lett {format_pace(zones.easy_high)}-{format_pace(zones.easy_low)}, "
          f"Terskel {format_pace(zones.threshold_high)}-{format_pace(zones.threshold_low)}")

    # Bestem startdato
    if args.start:
        start_date = datetime.strptime(args.start, '%Y-%m-%d')
    else:
        # Neste mandag
        today = datetime.now()
        days_until_monday = (7 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        start_date = today + timedelta(days=days_until_monday)
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    print(f"Startdato: {start_date.strftime('%d.%m.%Y')}")
    print(f"Fase: {args.fase} – {PHASES[args.fase]['name']}")
    print("")

    # Generer plan
    weeks = generate_phase_weeks(args.fase, start_volume, zones)
    plan = format_plan(args.fase, weeks, zones, start_date)

    # Lagre
    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLAN_PATH, 'w', encoding='utf-8') as f:
        f.write(plan)

    print(f"Plan lagret: {PLAN_PATH}")
    print("")
    print("=" * 60)
    print(plan)


if __name__ == "__main__":
    main()
