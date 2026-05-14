#!/usr/bin/env python3
"""
Ukesbrief – ukentlig treningsoppsummering generert søndag kveld.

Gir det store bildet: hvordan gikk uka, kvalitetsandel vs 2024,
form-trend, belastning, og hva som kommer neste uke.

Bruk:
    python scripts/ukesbrief.py              # Denne uka
    python scripts/ukesbrief.py --uke 20     # Spesifikk uke
    python scripts/ukesbrief.py --dato 2026-05-17  # Uke som inneholder dato
"""

import sqlite3
import re
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List, Tuple

# Prosjektstier
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
PLAN_PATH = PROJECT_ROOT / "plan" / "current_plan.md"
RAPPORT_PATH = PROJECT_ROOT / "rapport"


@dataclass
class UkeData:
    """Data for én uke."""
    uke_nr: int
    år: int
    start_dato: str
    slutt_dato: str
    total_km: float
    antall_økter: int
    hard_km: float  # km under 4:30/km
    terskel_min: float
    lang_tur_km: float
    snitt_hr_rolig: Optional[float]
    snitt_pace_rolig: Optional[float]


def hent_uke_data(dato: str) -> UkeData:
    """Henter treningsdata for uka som inneholder dato."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    dt = datetime.strptime(dato, '%Y-%m-%d')
    dag_i_uka = dt.weekday()
    mandag = dt - timedelta(days=dag_i_uka)
    søndag = mandag + timedelta(days=6)
    uke_nr = dt.isocalendar()[1]

    # Hent alle aktiviteter denne uka
    cursor.execute("""
        SELECT
            distance_km,
            avg_pace_s_per_km,
            avg_hr,
            moving_time_s
        FROM activities
        WHERE date(start_date) BETWEEN ? AND ?
        AND sport IN ('running', 'Run', 'trail_running')
    """, (mandag.strftime('%Y-%m-%d'), søndag.strftime('%Y-%m-%d')))

    rows = cursor.fetchall()

    total_km = 0
    hard_km = 0
    terskel_min = 0
    lang_tur_km = 0
    rolig_paces = []
    rolig_hrs = []

    for dist, pace, hr, tid in rows:
        if dist:
            total_km += dist

            # Hard km (pace < 4:30 = 270 sek)
            if pace and pace < 270:
                hard_km += dist
                # Estimer terskel-minutter (ca 80% av tiden på harde økter)
                if tid:
                    terskel_min += (tid / 60) * 0.6

            # Lang tur (> 12 km)
            if dist > 12:
                lang_tur_km = max(lang_tur_km, dist)

            # Rolige økter for form-sammenligning (pace > 5:00 = 300 sek)
            if pace and pace > 300 and hr and hr < 140:
                rolig_paces.append(pace)
                rolig_hrs.append(hr)

    conn.close()

    snitt_pace = sum(rolig_paces) / len(rolig_paces) if rolig_paces else None
    snitt_hr = sum(rolig_hrs) / len(rolig_hrs) if rolig_hrs else None

    return UkeData(
        uke_nr=uke_nr,
        år=dt.year,
        start_dato=mandag.strftime('%Y-%m-%d'),
        slutt_dato=søndag.strftime('%Y-%m-%d'),
        total_km=total_km,
        antall_økter=len(rows),
        hard_km=hard_km,
        terskel_min=terskel_min,
        lang_tur_km=lang_tur_km,
        snitt_hr_rolig=snitt_hr,
        snitt_pace_rolig=snitt_pace
    )


def hent_kvalitetsarbeid_detaljer(start_dato: str, slutt_dato: str) -> Tuple[float, int]:
    """Henter detaljert kvalitetsarbeid fra interval_laps."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            il.distance_m,
            il.duration_s
        FROM activities a
        JOIN interval_laps il ON a.id = il.activity_id
        WHERE date(a.start_date) BETWEEN ? AND ?
        AND a.sport IN ('running', 'Run', 'trail_running')
        AND il.distance_m > 200
        AND (il.duration_s / (il.distance_m / 1000.0)) < 270
    """, (start_dato, slutt_dato))

    rows = cursor.fetchall()
    conn.close()

    total_m = sum(r[0] for r in rows if r[0])
    return total_m / 1000, len(rows)


def hent_planlagt_uke(start_dato: str) -> Tuple[float, float]:
    """Henter planlagt volum og terskelarbeid for uka."""
    if not PLAN_PATH.exists():
        return 0, 0

    # Importer parse_planlagt_okt
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from morgen_status import parse_planlagt_okt

    dt = datetime.strptime(start_dato, '%Y-%m-%d')

    planlagt_km = 0
    planlagt_terskel = 0

    for i in range(7):
        dag = (dt + timedelta(days=i)).strftime('%Y-%m-%d')
        okt = parse_planlagt_okt(dag)
        if okt:
            planlagt_km += okt.distanse_km or 0

    # Parse terskelarbeid fra plan (grovt estimat)
    plan_text = PLAN_PATH.read_text(encoding='utf-8')
    uke_nr = dt.isocalendar()[1]

    # Søk etter "Terskelarbeid: XX min" i uka
    pattern = rf'UKE\s+\d+.*?Terskelarbeid:\s*(\d+)\s*min'
    matches = re.findall(pattern, plan_text, re.IGNORECASE | re.DOTALL)
    if matches:
        planlagt_terskel = int(matches[0])

    return planlagt_km, planlagt_terskel


def hent_uke_2024(uke_nr: int) -> UkeData:
    """Henter data for samme uke i 2024."""
    # Finn mandag i uke uke_nr, 2024
    jan1 = datetime(2024, 1, 1)
    # Finn første mandag i 2024
    dager_til_mandag = (7 - jan1.weekday()) % 7
    første_mandag = jan1 + timedelta(days=dager_til_mandag)

    # Gå til riktig uke
    mandag_2024 = første_mandag + timedelta(weeks=uke_nr - 1)

    return hent_uke_data(mandag_2024.strftime('%Y-%m-%d'))


def hent_form_trend(dato: str, uker: int = 4) -> List[Tuple[str, float, float]]:
    """Henter form-trend basert på distanse-vektet intervall-pace ved HR ~170."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    dt = datetime.strptime(dato, '%Y-%m-%d')
    resultater = []

    for i in range(uker):
        uke_start = dt - timedelta(weeks=i, days=dt.weekday())
        uke_slutt = uke_start + timedelta(days=6)

        # Distanse-vektet snitt for intervaller:
        # - HR 165-180
        # - Distanse >= 1000m
        # - Pace < 5:00/km (filtrerer ut oppvarming/nedjogg)
        cursor.execute("""
            SELECT
                SUM(pace_s_per_km * distance_m) / SUM(distance_m) as vektet_pace,
                SUM(max_hr * distance_m) / SUM(distance_m) as vektet_hr,
                COUNT(*) as antall,
                SUM(distance_m) as total_dist
            FROM interval_laps
            WHERE activity_date BETWEEN ? AND ?
            AND lap_type = 'work'
            AND max_hr BETWEEN 165 AND 180
            AND distance_m >= 1000
            AND pace_s_per_km < 300
        """, (uke_start.strftime('%Y-%m-%d'), uke_slutt.strftime('%Y-%m-%d')))

        row = cursor.fetchone()
        if row[0] and row[1] and row[2] >= 2:  # Minimum 2 intervaller
            uke_label = f"Uke {uke_start.isocalendar()[1]}"
            resultater.append((uke_label, row[0], row[1]))

    conn.close()
    return list(reversed(resultater))


def hent_belastning_trend(dato: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Henter ACWR, søvnsnitt og Readiness-snitt for uka."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    dt = datetime.strptime(dato, '%Y-%m-%d')
    mandag = dt - timedelta(days=dt.weekday())
    søndag = mandag + timedelta(days=6)

    # Hent fra daily_metrics
    # NULLIF konverterer 0 til NULL så AVG ignorerer dager uten data
    cursor.execute("""
        SELECT
            AVG(CAST(load_ratio AS REAL)),
            AVG(NULLIF(CAST(sleep_hours AS REAL), 0)),
            AVG(CAST(training_readiness AS REAL))
        FROM daily_metrics
        WHERE date BETWEEN ? AND ?
    """, (mandag.strftime('%Y-%m-%d'), søndag.strftime('%Y-%m-%d')))

    row = cursor.fetchone()
    conn.close()

    if row:
        return row[0], row[1], row[2]
    return None, None, None


def hent_ukas_løp(start_dato: str, slutt_dato: str) -> List[Tuple[str, float, str]]:
    """Henter løp (races) fra uka."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT date(start_date), distance_km, avg_pace_s_per_km
        FROM activities
        WHERE date(start_date) BETWEEN ? AND ?
        AND is_race = 1
        ORDER BY start_date
    """, (start_dato, slutt_dato))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for dato, dist, pace in rows:
        pace_str = f"{int(pace//60)}:{int(pace%60):02d}/km" if pace else ""
        result.append((dato, dist, pace_str))

    return result


def generer_flagg(uke: UkeData, uke_2024: UkeData, kvalitet_pct: float, acwr: Optional[float], søvn: Optional[float]) -> List[str]:
    """Genererer advarsler/flagg basert på ukens data."""
    flagg = []

    if kvalitet_pct < 20 and uke.total_km > 30:
        flagg.append(f"⚠️ Lav kvalitetsandel ({kvalitet_pct:.0f}%) – risiko for 'junk miles'")

    if acwr and acwr > 1.4:
        flagg.append(f"⚠️ Høy ACWR ({acwr:.2f}) – økt skaderisiko")

    if søvn and søvn < 6.0:
        flagg.append(f"⚠️ Lite søvn (snitt {søvn:.1f}t) – restitusjon lider")

    if uke.total_km > uke_2024.total_km * 1.3 and uke_2024.total_km > 20:
        flagg.append(f"⚠️ Mye høyere volum enn 2024 (+{((uke.total_km/uke_2024.total_km)-1)*100:.0f}%)")

    return flagg


def generer_ukesbrief(dato: str) -> str:
    """Genererer ukesbrief for uka som inneholder dato."""
    dt = datetime.strptime(dato, '%Y-%m-%d')
    uke_nr = dt.isocalendar()[1]

    # Hent data
    uke = hent_uke_data(dato)
    uke_2024 = hent_uke_2024(uke_nr)
    kvalitet_km, kvalitet_laps = hent_kvalitetsarbeid_detaljer(uke.start_dato, uke.slutt_dato)
    kvalitet_2024_km, _ = hent_kvalitetsarbeid_detaljer(uke_2024.start_dato, uke_2024.slutt_dato)
    planlagt_km, planlagt_terskel = hent_planlagt_uke(uke.start_dato)
    form_trend = hent_form_trend(dato, uker=8)
    acwr, søvn, readiness = hent_belastning_trend(dato)
    løp = hent_ukas_løp(uke.start_dato, uke.slutt_dato)

    # Beregn kvalitetsandel
    kvalitet_pct = (kvalitet_km / uke.total_km * 100) if uke.total_km > 0 else 0
    kvalitet_2024_pct = (kvalitet_2024_km / uke_2024.total_km * 100) if uke_2024.total_km > 0 else 0

    # Generer flagg
    flagg = generer_flagg(uke, uke_2024, kvalitet_pct, acwr, søvn)

    # Neste uke
    neste_mandag = dt - timedelta(days=dt.weekday()) + timedelta(days=7)
    neste_uke_planlagt, neste_terskel = hent_planlagt_uke(neste_mandag.strftime('%Y-%m-%d'))
    neste_uke_2024 = hent_uke_2024(uke_nr + 1)

    # Formater dato for tittel
    måneder = ['januar', 'februar', 'mars', 'april', 'mai', 'juni',
               'juli', 'august', 'september', 'oktober', 'november', 'desember']
    start_dt = datetime.strptime(uke.start_dato, '%Y-%m-%d')
    slutt_dt = datetime.strptime(uke.slutt_dato, '%Y-%m-%d')

    if start_dt.month == slutt_dt.month:
        periode = f"{start_dt.day}.–{slutt_dt.day}. {måneder[start_dt.month-1]} {start_dt.year}"
    else:
        periode = f"{start_dt.day}. {måneder[start_dt.month-1]} – {slutt_dt.day}. {måneder[slutt_dt.month-1]} {start_dt.year}"

    # Bygg brief
    lines = []
    lines.append(f"# Ukesbrief – Uke {uke_nr}")
    lines.append(f"*{periode}*")
    lines.append("")

    # Ukesoppsummering
    lines.append("## Ukesoppsummering")
    lines.append("")
    lines.append("| | Planlagt | Faktisk | Avvik |")
    lines.append("|:--|:---------|:--------|:------|")

    km_avvik = uke.total_km - planlagt_km if planlagt_km > 0 else 0
    km_avvik_str = f"+{km_avvik:.0f}" if km_avvik > 0 else f"{km_avvik:.0f}"
    lines.append(f"| Volum | {planlagt_km:.0f} km | {uke.total_km:.0f} km | {km_avvik_str} km |")
    lines.append(f"| Økter | — | {uke.antall_økter} | — |")

    if uke.lang_tur_km > 0:
        lines.append(f"| Lengste økt | — | {uke.lang_tur_km:.0f} km | — |")

    lines.append("")

    # Kvalitetsandel
    lines.append("## Kvalitetsandel")
    lines.append("")
    lines.append(f"Hardt arbeid (<4:30/km): **{kvalitet_km:.1f} km** ({kvalitet_pct:.0f}%)")
    lines.append("")
    lines.append("| År | Hard km | Andel |")
    lines.append("|:---|:--------|:------|")
    lines.append(f"| 2024 | {kvalitet_2024_km:.1f} km | {kvalitet_2024_pct:.0f}% |")
    lines.append(f"| 2026 | {kvalitet_km:.1f} km | {kvalitet_pct:.0f}% |")

    diff = kvalitet_pct - kvalitet_2024_pct
    if diff > 5:
        lines.append(f"\n✅ Høyere kvalitetsandel enn 2024 (+{diff:.0f}%)")
    elif diff < -10:
        lines.append(f"\n⚠️ Lavere kvalitetsandel enn 2024 ({diff:.0f}%)")

    lines.append("")

    # Form-trend
    if form_trend:
        lines.append("## Form-trend")
        lines.append("")
        lines.append("*Intervall-pace ved HR ~170 (≥1000m, vektet)*")
        lines.append("")
        lines.append("| Uke | Pace | Snitt HR |")
        lines.append("|:----|:-----|:---------|")
        for uke_label, pace, hr in form_trend:
            pace_str = f"{int(pace//60)}:{int(pace%60):02d}/km"
            lines.append(f"| {uke_label} | {pace_str} | {hr:.0f} |")

        if len(form_trend) >= 2:
            første = form_trend[0][1]
            siste = form_trend[-1][1]
            diff = siste - første
            if diff < -5:
                lines.append(f"\n✅ Formen stiger ({-diff:.0f} sek/km raskere)")
            elif diff > 5:
                lines.append(f"\n⚠️ Formen faller ({diff:.0f} sek/km saktere)")

        lines.append("")

    # Belastning
    lines.append("## Belastning")
    lines.append("")
    if acwr or søvn or readiness:
        lines.append("| Metrikk | Snitt |")
        lines.append("|:--------|:------|")
        if acwr:
            acwr_status = "🟢" if acwr < 1.3 else "🟡" if acwr < 1.5 else "🔴"
            lines.append(f"| ACWR | {acwr:.2f} {acwr_status} |")
        if søvn:
            søvn_status = "🟢" if søvn > 7 else "🟡" if søvn > 6 else "🔴"
            lines.append(f"| Søvn | {søvn:.1f}t {søvn_status} |")
        if readiness:
            readiness_status = "🟢" if readiness > 50 else "🟡" if readiness > 30 else "🔴"
            lines.append(f"| Readiness | {readiness:.0f} {readiness_status} |")
    else:
        lines.append("*Ingen data*")
    lines.append("")

    # 2024 vs 2026
    lines.append("## 2024 vs 2026")
    lines.append("")
    lines.append(f"| | Uke {uke_nr} 2024 | Uke {uke_nr} 2026 |")
    lines.append("|:--|:--------------|:--------------|")
    lines.append(f"| Volum | {uke_2024.total_km:.0f} km | {uke.total_km:.0f} km |")
    lines.append(f"| Økter | {uke_2024.antall_økter} | {uke.antall_økter} |")
    lines.append(f"| Kvalitet | {kvalitet_2024_pct:.0f}% | {kvalitet_pct:.0f}% |")

    if løp:
        lines.append("")
        lines.append("**Løp denne uka:**")
        for race_dato, dist, pace in løp:
            lines.append(f"- 🏁 {race_dato}: {dist:.1f} km @ {pace}")

    lines.append("")

    # Neste uke
    lines.append("## Neste uke")
    lines.append("")
    lines.append(f"| | 2024 | 2026 (plan) |")
    lines.append("|:--|:-----|:------------|")
    lines.append(f"| Volum | {neste_uke_2024.total_km:.0f} km | ~{neste_uke_planlagt:.0f} km |")
    lines.append("")

    # Flagg
    if flagg:
        lines.append("## Flagg")
        lines.append("")
        for f in flagg:
            lines.append(f)
        lines.append("")

    return "\n".join(lines)


def lagre_ukesbrief(dato: str) -> Path:
    """Genererer og lagrer ukesbrief."""
    brief = generer_ukesbrief(dato)

    dt = datetime.strptime(dato, '%Y-%m-%d')
    uke_nr = dt.isocalendar()[1]

    # Lagre til fil
    RAPPORT_PATH.mkdir(parents=True, exist_ok=True)

    filnavn = f"ukesbrief_{dt.year}-W{uke_nr:02d}.md"
    filepath = RAPPORT_PATH / filnavn
    filepath.write_text(brief, encoding='utf-8')

    # Lagre også som siste
    siste_path = RAPPORT_PATH / "ukesbrief_siste.md"
    siste_path.write_text(brief, encoding='utf-8')

    return filepath


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Generer ukesbrief')
    parser.add_argument('--dato', type=str, help='Dato i uka (YYYY-MM-DD)')
    parser.add_argument('--uke', type=int, help='Ukenummer')
    args = parser.parse_args()

    if args.uke:
        # Finn mandag i angitt uke (2026) ved å bruke ISO calendar
        # ISO uke 1 er uka som inneholder 4. januar
        from datetime import date
        # Start med 4. januar 2026 (garantert i uke 1)
        jan4 = date(2026, 1, 4)
        # Finn mandagen i uke 1
        mandag_uke1 = jan4 - timedelta(days=jan4.weekday())
        # Gå til ønsket uke
        mandag = mandag_uke1 + timedelta(weeks=args.uke - 1)
        dato = mandag.strftime('%Y-%m-%d')
    elif args.dato:
        dato = args.dato
    else:
        dato = datetime.now().strftime('%Y-%m-%d')

    filepath = lagre_ukesbrief(dato)

    dt = datetime.strptime(dato, '%Y-%m-%d')
    uke_nr = dt.isocalendar()[1]

    print(f"📊 Ukesbrief lagret:")
    print(f"   Fil:   {filepath}")
    print(f"   Siste: {RAPPORT_PATH / 'ukesbrief_siste.md'}")


if __name__ == '__main__':
    main()
