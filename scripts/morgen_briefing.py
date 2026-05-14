#!/usr/bin/env python3
"""
Treningsbrief – personlig treningsbriefing generert etter kveldsøkt.

Genererer en 4-5 minutters lesning som føles som om en personlig trener
snakker direkte til deg, basert på dagens data og din historikk.

Bruk:
    python scripts/morgen_briefing.py
    python scripts/morgen_briefing.py --dato 2026-05-13
"""

import sqlite3
import re
import random
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List, Tuple

# Prosjektstier
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "treningsplan.db"
PLAN_PATH = PROJECT_ROOT / "plan" / "current_plan.md"
RAPPORT_PATH = PROJECT_ROOT / "rapport"

# Importer funksjoner fra morgen_status
from morgen_status import (
    hent_dagens_metrikker,
    hent_baseline,
    hent_volume_stats,
    parse_planlagt_okt,
    hent_hrv_historie,
    generer_anbefaling,
    DagensMetrikker,
    Baseline,
    VolumeStats,
    PlanlagtOkt
)


def hent_planlagt_drag_detaljer(dato: str) -> Optional[str]:
    """
    Parser current_plan.md for å finne drag-detaljer for en gitt dato.
    Returnerer f.eks. '4×6min' eller '8×3min'.
    """
    if not PLAN_PATH.exists():
        return None

    plan_text = PLAN_PATH.read_text(encoding='utf-8')

    # Finn datoen i planen (format: "### Tirsdag 12.05" eller "### Torsdag 14.05")
    dt = datetime.strptime(dato, '%Y-%m-%d')
    dag_måned = dt.strftime('%d.%m')  # f.eks. "12.05"

    # Søk etter seksjonen for denne datoen
    # Mønster: ### Ukedag DD.MM
    pattern = rf'###\s+\w+\s+{dag_måned}.*?(?=###|\Z)'
    match = re.search(pattern, plan_text, re.DOTALL)

    if not match:
        return None

    section = match.group(0)

    # Søk etter hovedøkt-mønster: "X × Y min" eller "X×Ymin"
    # Eksempler: "4 × 6 min", "8×3min", "5 × 7 min"
    drag_pattern = r'(\d+)\s*[×x]\s*(\d+)\s*min'
    drag_match = re.search(drag_pattern, section, re.IGNORECASE)

    if drag_match:
        antall = drag_match.group(1)
        varighet = drag_match.group(2)
        return f"{antall}x{varighet}min"

    return None


def hent_planlagt_type(dato: str) -> Optional[str]:
    """
    Parser current_plan.md for å finne økttype (T1, T2, vo2max, etc.) for en gitt dato.
    """
    if not PLAN_PATH.exists():
        return None

    plan_text = PLAN_PATH.read_text(encoding='utf-8')

    dt = datetime.strptime(dato, '%Y-%m-%d')
    dag_måned = dt.strftime('%d.%m')

    pattern = rf'###\s+\w+\s+{dag_måned}.*?(?=###|\Z)'
    match = re.search(pattern, plan_text, re.DOTALL)

    if not match:
        return None

    section = match.group(0)

    # Sjekk overskriften først (mer pålitelig)
    # Format: "### Tirsdag 12.05 – Terskel 1: Lange drag 🔴"
    header_match = re.search(r'###\s+\w+\s+\d+\.\d+\s*[–-]\s*(.+?)(?:\s*[🔴🟡🟢⚪]|$)', section)
    if header_match:
        header = header_match.group(1).lower().strip()

        # Sjekk hvile først (høyest prioritet)
        if 'hvile' in header:
            return 'hvile'
        elif 'terskel 2' in header or 'korte drag' in header:
            return 'T2'
        elif 'terskel 1' in header or 'lange drag' in header:
            return 'T1'
        elif 'vo2' in header:
            return 'vo2max'
        elif 'lang tur' in header or 'langtur' in header:
            return 'lang'
        elif 'rolig' in header or 'restitusjon' in header:
            return 'rolig'
        elif 'halvmaraton' in header or '10 km' in header or '10k' in header:
            return 'race'

    return None


@dataclass
class TilbakeblikkOkt:
    """En økt fra samme tid i fjor."""
    dato: str
    navn: str
    distanse_km: float
    pace_str: str
    hr_avg: int


@dataclass
class UkensStatus:
    """Status for inneværende uke."""
    uke_nr: int
    fase: str
    dag_i_uka: int  # 0=man, 6=søn
    km_gjort: float
    km_planlagt: float
    neste_okter: List[str]


def hent_uke_og_fase(dato: str) -> Tuple[int, str]:
    """Beregner hvilken treningsuke og fase vi er i."""
    dt = datetime.strptime(dato, '%Y-%m-%d')

    # Treningsplanen starter 11.05.2026
    plan_start = datetime(2026, 5, 11)
    if dt < plan_start:
        return 0, "Pre-plan"

    dager_siden_start = (dt - plan_start).days
    uke_nr = (dager_siden_start // 7) + 1

    # Bestem fase basert på uke
    if uke_nr <= 4:
        fase = "Blokk 1: Base/Comeback"
    elif uke_nr <= 8:
        fase = "Blokk 2: Aerob build"
    elif uke_nr <= 12:
        fase = "Blokk 3: Terskel-build"
    elif uke_nr <= 16:
        fase = "Blokk 4: 10k-spesifikk"
    else:
        fase = "Blokk 5: Halv-spesifikk"

    return uke_nr, fase


def hent_tilbakeblikk(dato: str) -> Optional[TilbakeblikkOkt]:
    """Henter aktivitet fra samme periode i 2024."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Parse dato og lag vindu for samme tid i 2024
    dt = datetime.strptime(dato, '%Y-%m-%d')
    dt_2024 = dt.replace(year=2024)

    # Søk i ±3 dagers vindu
    cursor.execute("""
        SELECT
            date(start_date) as dato,
            name,
            distance_km,
            avg_pace_s_per_km,
            avg_hr
        FROM activities
        WHERE date(start_date) BETWEEN date(?, '-3 days') AND date(?, '+3 days')
        AND sport IN ('running', 'Run', 'trail_running')
        AND distance_km > 5
        ORDER BY distance_km DESC
        LIMIT 1
    """, (dt_2024.strftime('%Y-%m-%d'), dt_2024.strftime('%Y-%m-%d')))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    pace_s = row[3] or 0
    pace_str = f"{int(pace_s//60)}:{int(pace_s%60):02d}" if pace_s else "ukjent"

    return TilbakeblikkOkt(
        dato=row[0],
        navn=row[1] or "Løpetur",
        distanse_km=row[2] or 0,
        pace_str=pace_str,
        hr_avg=row[4] or 0
    )


def hent_ukens_status(dato: str) -> UkensStatus:
    """Henter status for inneværende uke."""
    dt = datetime.strptime(dato, '%Y-%m-%d')
    dag_i_uka = dt.weekday()  # 0=mandag

    # Finn ukens start (mandag)
    uke_start = dt - timedelta(days=dag_i_uka)
    uke_slutt = uke_start + timedelta(days=6)

    # Hent km løpt så langt denne uka
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COALESCE(SUM(distance_km), 0)
        FROM activities
        WHERE date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) >= ?
        AND date(CASE
            WHEN start_date LIKE '%T%' THEN replace(replace(start_date, 'T', ' '), 'Z', '')
            ELSE start_date
        END) < ?
        AND sport IN ('running', 'Run', 'trail_running')
    """, (uke_start.strftime('%Y-%m-%d'), dato))

    km_gjort = cursor.fetchone()[0] or 0
    conn.close()

    uke_nr, fase = hent_uke_og_fase(dato)

    # Planlagt volum per uke (fra planen)
    planlagt_volum = {1: 60, 2: 64, 3: 66, 4: 35}
    km_planlagt = planlagt_volum.get(uke_nr, 60)

    # Finn neste økter (forenklet)
    neste_okter = []
    for i in range(1, min(4, 7 - dag_i_uka)):
        neste_dato = (dt + timedelta(days=i)).strftime('%Y-%m-%d')
        okt = parse_planlagt_okt(neste_dato)
        if okt:
            neste_okter.append(okt.type)

    return UkensStatus(
        uke_nr=uke_nr,
        fase=fase,
        dag_i_uka=dag_i_uka,
        km_gjort=km_gjort,
        km_planlagt=km_planlagt,
        neste_okter=neste_okter
    )


def hent_siste_dagers_trend(dato: str, dager: int = 3) -> str:
    """Analyserer trend i Readiness/søvn de siste dagene."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT date, training_readiness, sleep_hours, hrv_weekly_avg
        FROM daily_metrics
        WHERE date <= ? AND date >= date(?, ?)
        ORDER BY date DESC
        LIMIT ?
    """, (dato, dato, f'-{dager} days', dager + 1))

    rows = cursor.fetchall()
    conn.close()

    if len(rows) < 2:
        return ""

    # Sammenlign i dag med snitt av foregående dager
    i_dag = rows[0]
    tidligere = rows[1:]

    if not tidligere:
        return ""

    readiness_nå = i_dag[1] or 0
    readiness_før = sum(r[1] or 0 for r in tidligere) / len(tidligere)

    søvn_nå = i_dag[2] or 0
    søvn_før = sum(r[2] or 0 for r in tidligere) / len(tidligere)

    # Bygg trendsetning
    if readiness_nå > readiness_før + 10:
        return f"Readiness har klatret fra {readiness_før:.0f} til {readiness_nå:.0f} de siste dagene."
    elif readiness_nå < readiness_før - 10:
        return f"Readiness har falt fra {readiness_før:.0f} til {readiness_nå:.0f}. Kroppen signaliserer behov for mer hvile."
    elif søvn_nå > søvn_før + 0.5:
        return f"Du har sovet bedre de siste nettene, med snitt {søvn_nå:.1f} timer mot {søvn_før:.1f} tidligere."
    elif søvn_nå < søvn_før - 0.5:
        return f"Søvnen har vært kortere de siste nettene. Det påvirker restitusjonen."

    return ""


def hent_forrige_tilsvarende_okt(dato: str, okt_type: str) -> Optional[Tuple[str, str, float]]:
    """Henter forrige gang du gjorde samme økttype."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Søk etter lignende økter basert på pace og distanse
    if 'terskel' in okt_type.lower():
        # Terskeløkter: 4:00-4:20 pace, 8-15 km
        cursor.execute("""
            SELECT date(start_date), avg_pace_s_per_km, distance_km
            FROM activities
            WHERE date(start_date) < ?
            AND sport IN ('running', 'Run')
            AND avg_pace_s_per_km BETWEEN 240 AND 270
            AND distance_km BETWEEN 8 AND 15
            ORDER BY start_date DESC
            LIMIT 1
        """, (dato,))
    elif 'lang' in okt_type.lower():
        # Langturer: >14 km
        cursor.execute("""
            SELECT date(start_date), avg_pace_s_per_km, distance_km
            FROM activities
            WHERE date(start_date) < ?
            AND sport IN ('running', 'Run')
            AND distance_km > 14
            ORDER BY start_date DESC
            LIMIT 1
        """, (dato,))
    else:
        conn.close()
        return None

    row = cursor.fetchone()
    conn.close()

    if row:
        pace_s = row[1]
        pace_str = f"{int(pace_s//60)}:{int(pace_s%60):02d}" if pace_s else "ukjent"
        return (row[0], pace_str, row[2])

    return None


# ============================================================================
# BRIEFING-GENERATORER
# ============================================================================

def generer_apning(dato: str, metrikker: Optional[DagensMetrikker], uke_status: UkensStatus) -> str:
    """Genererer åpningsseksjonen."""
    dt = datetime.strptime(dato, '%Y-%m-%d')
    ukedager = ['mandag', 'tirsdag', 'onsdag', 'torsdag', 'fredag', 'lørdag', 'søndag']
    ukedag = ukedager[dt.weekday()]

    # Varierte hilsener
    hilsener = {
        0: ["God mandag, Odd.", "Ny uke, nye muligheter.", "Mandag morgen."],
        1: ["God tirsdag.", "Tirsdag betyr terskeløkt.", "God morgen."],
        2: ["Halvveis i uka.", "God onsdag.", "Onsdag – midt i uka."],
        3: ["God torsdag.", "Nesten helg.", "Torsdag morgen."],
        4: ["God fredag.", "Siste arbeidsdag.", "Fredag – snart helg."],
        5: ["God lørdag.", "Helgemorgen.", "Lørdag."],
        6: ["God søndag.", "Ukens siste dag.", "Søndag morgen."]
    }

    hilsen = random.choice(hilsener[dt.weekday()])

    # Dato og fase
    dato_str = dt.strftime('%d. %B').replace('May', 'mai').replace('June', 'juni').replace('July', 'juli').replace('August', 'august').replace('September', 'september')

    lines = [hilsen]
    lines.append(f"I dag er det {dato_str}, og du er i uke {uke_status.uke_nr} av treningsplanen, {uke_status.fase}.")

    # Personlig setning basert på trend
    trend = hent_siste_dagers_trend(dato)
    if trend:
        lines.append(trend)

    return " ".join(lines)


def generer_kroppens_status(metrikker: Optional[DagensMetrikker], baseline: Baseline,
                            volume: VolumeStats, signal: str, begrunnelse: str) -> str:
    """Genererer kroppens status-seksjonen."""
    if not metrikker:
        return "Jeg har ikke data fra Garmin for i dag. Kjør etter følelse, og sync klokka når du får sjansen."

    lines = []

    # Readiness-tolkning
    readiness = metrikker.training_readiness
    if readiness >= 60:
        lines.append(f"Readiness er {readiness}, som er solid.")
    elif readiness >= 40:
        lines.append(f"Readiness ligger på {readiness}. Det er greit, men ikke optimalt.")
    elif readiness >= 25:
        lines.append(f"Readiness er bare {readiness}. Kroppen er fortsatt sliten.")
    else:
        lines.append(f"Readiness er nede på {readiness}. Det er et klart signal om at kroppen trenger mer hvile.")

    # HRV-tolkning
    hrv_status = metrikker.hrv_status.lower()
    if hrv_status == 'balanced':
        lines.append("HRV er i balansert sone, som betyr at det autonome nervesystemet fungerer som det skal.")
    elif hrv_status in ['low', 'poor']:
        lines.append("HRV-status er lav. Dette indikerer at kroppen jobber med å restituere seg.")
    elif hrv_status == 'unbalanced':
        lines.append("HRV er i ubalanse. Vær oppmerksom på signalene i dag.")

    # Søvn
    if metrikker.sleep_hours > 0:
        if metrikker.sleep_hours >= 7:
            lines.append(f"Du fikk {metrikker.sleep_hours:.1f} timer søvn i natt, som er bra.")
        elif metrikker.sleep_hours >= 6:
            lines.append(f"Søvnen var {metrikker.sleep_hours:.1f} timer. Litt i korteste laget, men greit.")
        else:
            lines.append(f"Du sov bare {metrikker.sleep_hours:.1f} timer. Det er for lite for optimal restitusjon.")

    # ACWR
    acwr = volume.acwr
    if acwr > 1.5:
        lines.append(f"ACWR er på {acwr}, som er over skaderisiko-grensen. Vi må være forsiktige.")
    elif acwr > 1.3:
        lines.append(f"ACWR ligger på {acwr}. Det er høy progresjon, men innenfor grensen.")
    elif acwr > 1.0:
        lines.append(f"ACWR er {acwr}, som indikerer god progresjon.")
    else:
        lines.append(f"ACWR på {acwr} viser at belastningen er stabil.")

    # Fargestatus
    if signal == 'GRØNT':
        lines.append("Alt i alt: grønt lys. Kroppen er klar for dagens økt.")
    elif signal == 'GULT':
        lines.append("Samlet vurdering: gult lys. Vi justerer dagens økt litt ned.")
    else:
        lines.append("Samlet vurdering: rødt lys. I dag bør du ta det helt rolig eller hvile.")

    return " ".join(lines)


def generer_dagens_okt(okt: Optional[PlanlagtOkt], signal: str, anbefaling: str, dato: str) -> str:
    """Genererer dagens økt-seksjonen."""
    if not okt:
        return "Det er ingen planlagt økt i dag. Bruk dagen på hvile, eller ta en lett gåtur hvis du føler for det."

    if 'hvile' in okt.type.lower():
        return "I dag er det hviledag. Nyt den. Fokuser på å spise godt, drikke nok, og slappe av. Hvilen er en del av treningen."

    lines = []

    # Hva planen sier
    lines.append(f"Planen sier {okt.type}.")

    # Hvorfor denne økten
    if 'terskel 1' in okt.type.lower():
        lines.append("Terskel én-økter handler om å bygge utholdenhet ved terskel. Lange drag i fire til seks minutter gir kroppen tid til å stabilisere seg i laktat-sonen.")
    elif 'terskel 2' in okt.type.lower():
        lines.append("Terskel to er kortere og litt raskere drag. Her akkumulerer du mer laktat, som lærer kroppen å håndtere syren bedre.")
    elif 'lang' in okt.type.lower():
        lines.append("Langturen bygger aerob utholdenhet og mental kapasitet. Det handler om varighet, ikke intensitet.")
    elif 'rolig' in okt.type.lower() or 'restitusjon' in okt.type.lower():
        lines.append("En rolig økt som denne fremmer restitusjon. Hold deg i sone to hele veien.")

    # Konkret utførelse
    if okt.distanse_km > 0:
        if okt.terskelarbeid_min > 0:
            lines.append(f"Økten er rundt {okt.distanse_km:.0f} kilometer totalt, med {okt.terskelarbeid_min} minutter terskelarbeid.")
        else:
            lines.append(f"Økten er rundt {okt.distanse_km:.0f} kilometer.")

    if okt.pace:
        pace_tekst = okt.pace.replace('/km', ' per kilometer').replace('-', ' til ')
        lines.append(f"Hold deg i {pace_tekst}.")

    # Justert anbefaling
    if signal == 'GULT':
        lines.append(f"Med tanke på dagens status: {anbefaling.replace('Kjør', 'kjør').lower()}")
    elif signal == 'RØDT':
        lines.append(f"Gitt dagens signaler anbefaler jeg: {anbefaling.lower()}")

    # Fokuspoeng
    if 'terskel' in okt.type.lower():
        lines.append("Fokuspunkt i dag: Ikke press pacen på første drag. La det første intervallet være fem til sju sekunder saktere enn de neste. Det er sånn du finner riktig rytme.")
    elif 'lang' in okt.type.lower():
        lines.append("Fokuspunkt: Start roligere enn du tror er nødvendig. De første fem kilometerne skal føles for lette.")
    elif 'rolig' in okt.type.lower():
        lines.append("Fokuspunkt: Hold igjen. En rolig økt som føles for lett, er perfekt gjennomført.")

    return " ".join(lines)


def generer_tilbakeblikk(dato: str, okt: Optional[PlanlagtOkt]) -> str:
    """Genererer tilbakeblikk-seksjonen."""
    tilbakeblikk = hent_tilbakeblikk(dato)

    if not tilbakeblikk:
        return ""  # Hopp seksjonen hvis ingen data

    dt = datetime.strptime(tilbakeblikk.dato, '%Y-%m-%d')
    dato_str = dt.strftime('%d. %B %Y').replace('August', 'august').replace('September', 'september').replace('May', 'mai')

    lines = []
    lines.append(f"For to år siden, {dato_str}, løp du {tilbakeblikk.distanse_km:.1f} kilometer på {tilbakeblikk.pace_str} per kilometer.")

    if tilbakeblikk.hr_avg > 0:
        lines.append(f"Pulsen var på {tilbakeblikk.hr_avg}.")

    # Sammenligning hvis dagens økt er lik
    if okt and 'terskel' in okt.type.lower() and tilbakeblikk.pace_str:
        try:
            # Parse pace
            pace_parts = tilbakeblikk.pace_str.split(':')
            pace_2024 = int(pace_parts[0]) * 60 + int(pace_parts[1])

            if pace_2024 < 260:  # Under 4:20 - var en hard økt
                lines.append("Det var sannsynligvis en hard økt. Sammenlign med dagens terskelarbeid for å se utviklingen.")
        except:
            pass

    return " ".join(lines) if lines else ""


def generer_ukens_bilde(uke_status: UkensStatus, volume: VolumeStats) -> str:
    """Genererer ukens bilde-seksjonen."""
    ukedager = ['mandag', 'tirsdag', 'onsdag', 'torsdag', 'fredag', 'lørdag', 'søndag']

    lines = []

    # Hvor i uka
    if uke_status.dag_i_uka == 0:
        lines.append("Vi er i starten av uka.")
    elif uke_status.dag_i_uka <= 2:
        lines.append("Vi er tidlig i uka.")
    elif uke_status.dag_i_uka <= 4:
        lines.append("Vi er midt i uka.")
    else:
        lines.append("Vi nærmer oss slutten av uka.")

    # Volum så langt
    gjenstående = uke_status.km_planlagt - uke_status.km_gjort
    if uke_status.km_gjort > 0:
        lines.append(f"Du har løpt {uke_status.km_gjort:.0f} kilometer så langt denne uka.")
        if gjenstående > 0:
            lines.append(f"Det gjenstår rundt {gjenstående:.0f} kilometer for å nå ukemålet på {uke_status.km_planlagt:.0f}.")
    else:
        lines.append(f"Ukemålet er {uke_status.km_planlagt:.0f} kilometer.")

    # Hva som kommer
    if uke_status.neste_okter:
        neste = uke_status.neste_okter[0].lower()
        if 'terskel' in neste or 'lang' in neste:
            lines.append(f"Neste nøkkeløkt er {neste}.")

    return " ".join(lines)


def hent_intervall_struktur(activity_id: int) -> Tuple[str, Optional[float]]:
    """
    Parser interval_laps for å detektere intervallstruktur.
    Returnerer (struktur, intervall_pace) f.eks. ('5x1000m', 242.5) eller ('', None).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT lap_type, distance_m, duration_s, pace_s_per_km
        FROM interval_laps
        WHERE activity_id = ?
        ORDER BY lap_index
    """, (activity_id,))

    laps = cursor.fetchall()
    conn.close()

    if not laps:
        return "", None

    # Finn alle work-laps
    work_laps = [(dist, dur, pace) for lap_type, dist, dur, pace in laps if lap_type == 'work']

    if len(work_laps) < 2:
        return "", None

    # Analyser work-laps for å finne mønster
    # Filtrer ut oppvarming/nedjogg (mye lengre/saktere enn intervallene)
    distances = [lap[0] for lap in work_laps]
    paces = [lap[2] for lap in work_laps]

    # Finn intervall-laps (de med lignende distanse og rask pace)
    # Hopp over første og siste hvis de er mye lengre (oppvarming/nedjogg)
    avg_dist = sum(distances) / len(distances)
    avg_pace = sum(paces) / len(paces)

    # Filtrer til bare intervall-laps (innenfor 30% av gjennomsnitt)
    intervall_laps = []
    for dist, dur, pace in work_laps:
        # Hopp over laps som er mye lengre enn snittet (sannsynligvis oppvarming)
        if dist > avg_dist * 2:
            continue
        # Hopp over laps som er mye saktere enn snittet
        if pace > avg_pace * 1.5:
            continue
        intervall_laps.append((dist, dur, pace))

    if len(intervall_laps) < 2:
        return "", None

    # Beregn gjennomsnittlig distanse og pace på intervallene
    avg_intervall_dist = sum(lap[0] for lap in intervall_laps) / len(intervall_laps)
    avg_intervall_pace = sum(lap[2] for lap in intervall_laps) / len(intervall_laps)
    antall = len(intervall_laps)

    # Kategoriser distanse
    if avg_intervall_dist < 250:
        dist_str = "200m"
    elif avg_intervall_dist < 500:
        dist_str = "400m"
    elif avg_intervall_dist < 700:
        dist_str = "600m"
    elif avg_intervall_dist < 900:
        dist_str = "800m"
    elif avg_intervall_dist < 1100:
        dist_str = "1000m"
    elif avg_intervall_dist < 1300:
        dist_str = "1200m"
    elif avg_intervall_dist < 1700:
        dist_str = "1500m"
    elif avg_intervall_dist < 2200:
        dist_str = "2000m"
    elif avg_intervall_dist < 3500:
        dist_str = "3000m"
    else:
        # For lengre drag, vis i km eller minutter
        avg_dur = sum(lap[1] for lap in intervall_laps) / len(intervall_laps)
        if avg_dur > 180:  # Over 3 min, vis som minutter
            dist_str = f"{int(avg_dur/60)}min"
        else:
            dist_str = f"{int(avg_intervall_dist)}m"

    return f"{antall}x{dist_str}", avg_intervall_pace


def klassifiser_intervall_type(intervall_pace: float) -> Optional[str]:
    """
    Klassifiserer økttype basert på intervall-pace.
    Returnerer None hvis pace er for sakte til å være intervaller.

    Basert på treningsplanen:
    - VO2 Max: 3:30-3:40/km (210-220 sek)
    - Terskel 2: 4:00-4:10/km (240-250 sek)
    - Terskel 1: 4:10-4:20/km (250-260 sek)

    Vi bruker litt bredere grenser for å fange opp variasjon.
    """
    if intervall_pace < 225:  # < 3:45/km
        return "vo2max"
    elif intervall_pace < 250:  # < 4:10/km (Terskel 2-sone)
        return "T2"
    elif intervall_pace < 270:  # < 4:30/km (Terskel 1-sone, litt margin)
        return "T1"
    else:
        # Pace >= 4:30/km er for sakte til å være strukturerte intervaller
        # ifølge treningsplanen. Dette er tempo/moderat eller auto-split.
        return None


def hent_uke_2024(dato: str) -> List[Tuple[str, str, float, str, int, int, int, bool]]:
    """Henter alle aktiviteter fra tilsvarende uke i 2024."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Parse dato og finn uke i 2024
    dt = datetime.strptime(dato, '%Y-%m-%d')
    dt_2024 = dt.replace(year=2024)

    # Finn mandagen i samme uke
    dag_i_uka = dt_2024.weekday()
    mandag_2024 = dt_2024 - timedelta(days=dag_i_uka)
    sondag_2024 = mandag_2024 + timedelta(days=6)

    cursor.execute("""
        SELECT
            date(start_date) as dato,
            name,
            distance_km,
            avg_pace_s_per_km,
            avg_hr,
            max_hr,
            id,
            is_race
        FROM activities
        WHERE date(start_date) BETWEEN ? AND ?
        AND sport IN ('running', 'Run', 'trail_running')
        ORDER BY start_date
    """, (mandag_2024.strftime('%Y-%m-%d'), sondag_2024.strftime('%Y-%m-%d')))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        pace_s = row[3] or 0
        pace_str = f"{int(pace_s//60)}:{int(pace_s%60):02d}" if pace_s else ""
        is_race = bool(row[7]) if row[7] is not None else False
        # (dato, navn, km, pace, avg_hr, max_hr, activity_id, is_race)
        result.append((row[0], row[1] or "Løpetur", row[2] or 0, pace_str, row[4] or 0, row[5] or 0, row[6] or 0, is_race))

    return result


def hent_ukas_okter(dato: str) -> List[Tuple[str, str, float, str, int, int, int, bool]]:
    """Henter alle aktiviteter fra inneværende uke."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    dt = datetime.strptime(dato, '%Y-%m-%d')
    dag_i_uka = dt.weekday()
    mandag = dt - timedelta(days=dag_i_uka)
    sondag = mandag + timedelta(days=6)

    cursor.execute("""
        SELECT
            date(start_date) as dato,
            name,
            distance_km,
            avg_pace_s_per_km,
            avg_hr,
            max_hr,
            id,
            is_race
        FROM activities
        WHERE date(start_date) BETWEEN ? AND ?
        AND sport IN ('running', 'Run', 'trail_running')
        ORDER BY start_date
    """, (mandag.strftime('%Y-%m-%d'), sondag.strftime('%Y-%m-%d')))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        pace_s = row[3] or 0
        pace_str = f"{int(pace_s//60)}:{int(pace_s%60):02d}" if pace_s else ""
        is_race = bool(row[7]) if row[7] is not None else False
        # (dato, navn, km, pace, avg_hr, max_hr, activity_id, is_race)
        result.append((row[0], row[1] or "Løpetur", row[2] or 0, pace_str, row[4] or 0, row[5] or 0, row[6] or 0, is_race))

    return result


def hent_form_sammenligning(dato: str) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    Henter pace ved rolig HR for sammenligning av faktisk form.
    Sammenligner med 2024-økter ved LIK HR (±5 slag).
    Returnerer (pace_nå, hr_nå, pace_2024, hr_2024).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    dt = datetime.strptime(dato, '%Y-%m-%d')

    # Finn ukens start (mandag)
    dag_i_uka = dt.weekday()
    uke_start = dt - timedelta(days=dag_i_uka)
    uke_slutt = uke_start + timedelta(days=6)

    def finn_rolige_okter(start_dato: str, slutt_dato: str, hr_min: int = 120, hr_max: int = 140):
        """Henter rolige økter i perioden (pace > 5:00/km, distanse > 5 km)."""
        cursor.execute("""
            SELECT avg_pace_s_per_km, avg_hr, distance_km
            FROM activities
            WHERE date(start_date) BETWEEN ? AND ?
            AND sport IN ('running', 'Run', 'trail_running')
            AND avg_hr BETWEEN ? AND ?
            AND distance_km > 5
            AND avg_pace_s_per_km > 300
        """, (start_dato, slutt_dato, hr_min, hr_max))
        return cursor.fetchall()

    # Prøv denne uka først
    rolig_nå = finn_rolige_okter(uke_start.strftime('%Y-%m-%d'), uke_slutt.strftime('%Y-%m-%d'))

    # Hvis ingen treff, utvid til ±1 uke
    if not rolig_nå:
        utvidet_start = uke_start - timedelta(days=7)
        utvidet_slutt = uke_slutt + timedelta(days=7)
        rolig_nå = finn_rolige_okter(utvidet_start.strftime('%Y-%m-%d'), utvidet_slutt.strftime('%Y-%m-%d'))

    # Hvis fortsatt ingen, utvid til ±2 uker
    if not rolig_nå:
        utvidet_start = uke_start - timedelta(days=14)
        utvidet_slutt = uke_slutt + timedelta(days=14)
        rolig_nå = finn_rolige_okter(utvidet_start.strftime('%Y-%m-%d'), utvidet_slutt.strftime('%Y-%m-%d'))

    pace_nå = hr_nå = pace_2024 = hr_2024 = None

    if rolig_nå:
        pace_nå = sum(r[0] for r in rolig_nå) / len(rolig_nå)
        hr_nå = sum(r[1] for r in rolig_nå) / len(rolig_nå)

        # Søk i 2024 med ±5 slag fra nåværende HR
        hr_target = int(hr_nå)
        hr_min_2024 = hr_target - 5
        hr_max_2024 = hr_target + 5

        # Søk i hele 2024 for å finne sammenlignbare økter
        rolig_2024 = finn_rolige_okter('2024-01-01', '2024-12-31', hr_min_2024, hr_max_2024)

        # Hvis ingen treff med ±5, prøv ±8
        if not rolig_2024:
            hr_min_2024 = hr_target - 8
            hr_max_2024 = hr_target + 8
            rolig_2024 = finn_rolige_okter('2024-01-01', '2024-12-31', hr_min_2024, hr_max_2024)

        if rolig_2024:
            pace_2024 = sum(r[0] for r in rolig_2024) / len(rolig_2024)
            hr_2024 = sum(r[1] for r in rolig_2024) / len(rolig_2024)

    conn.close()

    return pace_nå, hr_nå, pace_2024, hr_2024


def generer_ukesammenligning(dato: str, uke_status: UkensStatus) -> str:
    """Genererer sammenligning mellom denne uka og tilsvarende uke i 2024."""
    ukas_okter = hent_ukas_okter(dato)
    uke_2024 = hent_uke_2024(dato)

    lines = []
    dt = datetime.strptime(dato, '%Y-%m-%d')
    dagnavn = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn']

    # Klassifiser økttype basert på data
    def klassifiser_okt(km, pace_str, hr):
        if not pace_str:
            return "?"
        try:
            parts = pace_str.split(':')
            pace_sec = int(parts[0]) * 60 + int(parts[1])
        except:
            return "?"

        # VO2 max: veldig rask (<3:45/km) med høy HR
        if pace_sec < 225 and hr and hr > 165:
            return "vo2max"
        if km < 1.5 and pace_sec < 230:  # Korte raske reps
            return "vo2max"

        # Korte raske = intervaller
        if km < 1.5 and pace_sec < 260:  # <4:20
            return "400m"
        if km < 2.5 and pace_sec < 250:  # <4:10
            return "1km"
        if km < 4 and pace_sec < 260:
            return "int"

        # Terskel 2: 4:00-4:10/km (240-250 sec), kortere drag
        if hr and hr > 150 and pace_sec < 250 and km > 6:
            return "T2"

        # Terskel 1: 4:10-4:25/km (250-265 sec), lengre drag
        if hr and hr > 140 and pace_sec < 265 and km > 8:
            return "T1"

        # Terskel generelt: 4:00-5:00 pace med HR > 140
        if hr and hr > 140 and pace_sec < 300 and km > 8:
            return "terskel"
        if pace_sec < 270 and hr and hr > 160:  # <4:30, HR>160
            return "terskel"

        # Tempo: rask men ikke terskel
        if pace_sec < 280 and km > 6:
            return "tempo"

        # Lang rolig
        if km > 14 and pace_sec > 300:
            return "lang"

        # Rolig
        if pace_sec > 310:
            return "rolig"

        # Moderat (grå sone)
        if pace_sec > 280:
            return "moderat"

        return "hard"

    # Organiser økter per ukedag (0=man, 6=søn)
    def grupper_per_dag(okter):
        per_dag = {i: [] for i in range(7)}
        for dato_str, navn, km, pace, hr, max_hr, activity_id, is_race in okter:
            dag_dt = datetime.strptime(dato_str, '%Y-%m-%d')
            ukedag = dag_dt.weekday()

            # Parse pace til sekunder
            pace_sec = 0
            if pace:
                try:
                    parts = pace.split(':')
                    pace_sec = int(parts[0]) * 60 + int(parts[1])
                except:
                    pass

            # Race-deteksjon: database-flagg ELLER heuristikk
            # Heuristikk: pace < 4:10 (250s), max_hr > 175, distanse 2-6 km
            is_detected_race = is_race or (
                pace_sec > 0 and pace_sec < 250 and
                max_hr and max_hr > 175 and
                2.0 <= km <= 6.0
            )

            if is_detected_race:
                okt_type = "race"
                per_dag[ukedag].append(f"🏁 {km:.1f}km {pace} maks{max_hr}")
                continue

            # Hent intervall-info først for å kunne klassifisere riktig
            intervall_info, intervall_pace = hent_intervall_struktur(activity_id) if activity_id else ("", None)

            # Klassifiser basert på intervall-pace hvis det er raske nok intervaller
            intervall_type = None
            if intervall_pace:
                intervall_type = klassifiser_intervall_type(intervall_pace)

            # Bruk intervall-klassifisering kun hvis pace er rask nok
            if intervall_type:
                okt_type = intervall_type
            else:
                okt_type = klassifiser_okt(km, pace, hr)
                # Ikke vis intervall-info for sakte økter (auto-split laps)
                if intervall_pace and intervall_pace >= 270:
                    intervall_info = ""

            # Intervaller/terskel/VO2max: vis drag-detaljer + maks-HR
            if okt_type in ['terskel', 'T1', 'T2', 'vo2max', 'int', '400m', '1km', 'tempo', 'hard']:
                max_hr_str = f" maks{max_hr}" if max_hr else ""
                if intervall_info:
                    per_dag[ukedag].append(f"{okt_type} {km:.0f}km {pace} {intervall_info}{max_hr_str}")
                else:
                    per_dag[ukedag].append(f"{okt_type} {km:.0f}km {pace}{max_hr_str}")
            else:
                # Rolige økter: vis snitt-HR
                hr_str = f" HR{hr}" if hr else ""
                per_dag[ukedag].append(f"{okt_type} {km:.0f}km {pace}{hr_str}")
        return per_dag

    okter_2024 = grupper_per_dag(uke_2024)
    okter_2026 = grupper_per_dag(ukas_okter)

    # Hent planlagte økter for gjenstående dager
    dag_i_uka = dt.weekday()
    planlagt = {}
    planlagt_obj = {}
    for i in range(7):
        if i >= dag_i_uka:  # Kun fremtidige dager (inkl i dag)
            sjekk_dato = (dt - timedelta(days=dag_i_uka) + timedelta(days=i)).strftime('%Y-%m-%d')
            okt = parse_planlagt_okt(sjekk_dato)
            if okt:
                # Hent økttype og drag-detaljer fra planen
                okt_type = hent_planlagt_type(sjekk_dato) or "økt"
                drag_detaljer = hent_planlagt_drag_detaljer(sjekk_dato)

                if okt.distanse_km > 0:
                    if drag_detaljer:
                        planlagt[i] = f"{okt_type} {okt.distanse_km:.0f}km {drag_detaljer}"
                    else:
                        planlagt[i] = f"{okt_type} {okt.distanse_km:.0f}km"
                else:
                    planlagt[i] = okt_type
                planlagt_obj[i] = okt

    # Tabell-header
    lines.append("| Dag | 2024 | 2026 |")
    lines.append("|:----|:-----|:-----|")

    total_2024 = sum(o[2] for o in uke_2024)
    total_2026 = sum(o[2] for o in ukas_okter)

    # Bygg rader per dag
    for dag_idx in range(7):
        dag = dagnavn[dag_idx]

        # 2024-kolonne
        if okter_2024[dag_idx]:
            col_24 = " + ".join(okter_2024[dag_idx])
        else:
            col_24 = "—"

        # 2026-kolonne
        if okter_2026[dag_idx]:
            col_26 = " + ".join(okter_2026[dag_idx])
        elif dag_idx in planlagt:
            # Vis planlagt økt i kursiv
            col_26 = f"*{planlagt[dag_idx]}*"
        elif dag_idx < dag_i_uka:
            col_26 = "—"
        else:
            col_26 = "—"

        lines.append(f"| {dag} | {col_24} | {col_26} |")

    # Beregn planlagt volum
    planlagt_km = 0
    for i in range(7):
        if i >= dag_i_uka and i in planlagt:
            sjekk_dato = (dt - timedelta(days=dag_i_uka) + timedelta(days=i)).strftime('%Y-%m-%d')
            okt = parse_planlagt_okt(sjekk_dato)
            if okt and okt.distanse_km > 0:
                planlagt_km += okt.distanse_km

    # Totalrad
    total_2026_mål = total_2026 + planlagt_km
    lines.append(f"| **Total** | **{total_2024:.1f} km** | **{total_2026:.0f} + {planlagt_km:.0f} = {total_2026_mål:.0f} km** |")

    # Hent neste ukes volum
    neste_uke_start = dt - timedelta(days=dag_i_uka) + timedelta(days=7)
    neste_uke_slutt = neste_uke_start + timedelta(days=6)

    # Neste uke 2024
    neste_2024_start = neste_uke_start.replace(year=2024)
    neste_2024_slutt = neste_uke_slutt.replace(year=2024)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COALESCE(SUM(distance_km), 0)
        FROM activities
        WHERE date(start_date) BETWEEN ? AND ?
        AND sport IN ('running', 'Run', 'trail_running')
    """, (neste_2024_start.strftime('%Y-%m-%d'), neste_2024_slutt.strftime('%Y-%m-%d')))
    neste_2024_km = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(distance_km), 0)
        FROM activities
        WHERE date(start_date) BETWEEN ? AND ?
        AND sport IN ('running', 'Run', 'trail_running')
    """, (neste_uke_start.strftime('%Y-%m-%d'), neste_uke_slutt.strftime('%Y-%m-%d')))
    neste_2026_km = cursor.fetchone()[0]

    conn.close()

    # Hent planlagt volum for neste uke fra planen
    neste_uke_planlagt = 0
    for i in range(7):
        sjekk_dato = (neste_uke_start + timedelta(days=i)).strftime('%Y-%m-%d')
        okt = parse_planlagt_okt(sjekk_dato)
        if okt and okt.distanse_km > 0:
            neste_uke_planlagt += okt.distanse_km

    # Vis neste uke
    neste_2024_str = f"{neste_2024_km:.0f} km" if neste_2024_km > 0 else "—"
    if neste_2026_km > 0:
        neste_2026_str = f"{neste_2026_km:.0f} km"
    elif neste_uke_planlagt > 0:
        neste_2026_str = f"~{neste_uke_planlagt:.0f} km"
    else:
        neste_2026_str = "—"
    lines.append(f"| *Neste uke* | *{neste_2024_str}* | *{neste_2026_str}* |")

    # Form-sammenligning basert på rolige økter
    pace_nå, hr_nå, pace_2024, hr_2024 = hent_form_sammenligning(dato)

    lines.append("")

    if pace_nå and pace_2024:
        def sec_to_pace(s):
            return f"{int(s//60)}:{int(s%60):02d}"

        pace_diff = pace_nå - pace_2024  # Positiv = saktere nå

        pace_nå_str = sec_to_pace(pace_nå)
        pace_2024_str = sec_to_pace(pace_2024)

        # Sammenligning ved lik HR (±5 slag)
        if pace_diff > 15:
            lines.append(f"**Form (ved HR ~{hr_nå:.0f}):** {pace_nå_str}/km vs {pace_2024_str}/km (2024). {int(pace_diff)} sek/km saktere.")
        elif pace_diff > 5:
            lines.append(f"**Form (ved HR ~{hr_nå:.0f}):** {pace_nå_str}/km vs {pace_2024_str}/km (2024). {int(pace_diff)} sek/km gap.")
        elif pace_diff > 0:
            lines.append(f"**Form (ved HR ~{hr_nå:.0f}):** {pace_nå_str}/km vs {pace_2024_str}/km (2024). Bare {int(pace_diff)} sek/km forskjell.")
        elif pace_diff < -5:
            lines.append(f"**Form (ved HR ~{hr_nå:.0f}):** {pace_nå_str}/km vs {pace_2024_str}/km (2024). {int(-pace_diff)} sek/km raskere!")
        else:
            lines.append(f"**Form (ved HR ~{hr_nå:.0f}):** {pace_nå_str}/km vs {pace_2024_str}/km (2024). Lik form.")
    elif pace_nå:
        def sec_to_pace(s):
            return f"{int(s//60)}:{int(s%60):02d}"
        lines.append(f"**Form denne uka:** {sec_to_pace(pace_nå)}/km @ HR {hr_nå:.0f}. Ingen rolige økter i 2024 å sammenligne med.")
    else:
        lines.append("**Form:** Trenger rolige økter (>5 km, rolig pace) for å vurdere.")

    return "\n".join(lines)


def generer_avslutning(okt: Optional[PlanlagtOkt], dato: str, metrikker: Optional[DagensMetrikker]) -> str:
    """Genererer avslutningsseksjonen."""
    lines = []

    # Prøv å finne forrige tilsvarende økt for sammenligning
    if okt and 'terskel' in okt.type.lower():
        forrige = hent_forrige_tilsvarende_okt(dato, okt.type)
        if forrige:
            lines.append(f"Sist du gjorde en lignende terskeløkt, den {forrige[0]}, holdt du {forrige[1]} per kilometer. Bruk det som referanse i dag.")

    # Fokus for dagen
    if not lines:
        if okt and 'terskel' in okt.type.lower():
            lines.append("Fokuset i dag er kontrollert intensitet. Bedre å treffe riktig enn å presse for hardt.")
        elif okt and 'lang' in okt.type.lower():
            lines.append("Fokuset i dag er jevn innsats over tid. Tålmodighet vinner.")
        elif okt and 'rolig' in okt.type.lower():
            lines.append("Fokuset i dag er restitusjon. En god rolig økt setter deg opp for neste harde økt.")
        elif not okt or 'hvile' in (okt.type if okt else '').lower():
            lines.append("Fokuset i dag er hvile. Det er en del av treningen.")
        else:
            lines.append("Fokuset i dag er å lytte til kroppen og gjøre det som føles riktig.")

    lines.append("Lykke til.")

    return " ".join(lines)


# ============================================================================
# HOVEDFUNKSJONER
# ============================================================================

def generer_briefing(dato: str) -> Tuple[str, str]:
    """
    Genererer morgenbriefing for gitt dato.

    Returnerer (markdown_versjon, talemanus_versjon)
    """
    # Hent all data
    metrikker = hent_dagens_metrikker(dato)
    baseline = hent_baseline(dato)
    volume = hent_volume_stats(dato)
    okt = parse_planlagt_okt(dato)
    hrv_historie = hent_hrv_historie(dato, dager=3)
    uke_status = hent_ukens_status(dato)

    # Generer anbefaling
    signal, anbefaling, begrunnelse = generer_anbefaling(
        metrikker, baseline, volume, okt, hrv_historie
    )

    # Bygg seksjonene
    seksjoner = []

    # 1. Åpning
    apning = generer_apning(dato, metrikker, uke_status)
    seksjoner.append(("Åpning", apning))

    # 2. Kroppens status
    status = generer_kroppens_status(metrikker, baseline, volume, signal, begrunnelse)
    seksjoner.append(("Kroppens status", status))

    # 3. Dagens økt
    dagens_okt = generer_dagens_okt(okt, signal, anbefaling, dato)
    seksjoner.append(("Dagens økt", dagens_okt))

    # 4. Tilbakeblikk (hopp hvis tom)
    tilbakeblikk = generer_tilbakeblikk(dato, okt)
    if tilbakeblikk:
        seksjoner.append(("Tilbakeblikk", tilbakeblikk))

    # 5. Ukens bilde
    ukens_bilde = generer_ukens_bilde(uke_status, volume)
    seksjoner.append(("Ukens bilde", ukens_bilde))

    # 6. Avslutning
    avslutning = generer_avslutning(okt, dato, metrikker)
    seksjoner.append(("Avslutning", avslutning))

    # 7. Ukesammenligning (2024 vs 2026)
    ukesammenligning = generer_ukesammenligning(dato, uke_status)
    seksjoner.append(("Uke 2024 vs 2026", ukesammenligning))

    # Bygg markdown-versjon
    # Konverter dato til lesbar norsk format (13. mai 2026)
    dt_for_title = datetime.strptime(dato, '%Y-%m-%d')
    måneder = ['januar', 'februar', 'mars', 'april', 'mai', 'juni',
               'juli', 'august', 'september', 'oktober', 'november', 'desember']
    dato_lesbar = f"{dt_for_title.day}. {måneder[dt_for_title.month - 1]} {dt_for_title.year}"
    md_lines = [f"# Treningsbrief – {dato_lesbar}\n"]
    for tittel, innhold in seksjoner:
        md_lines.append(f"## {tittel}\n")
        md_lines.append(f"{innhold}\n")
    markdown = "\n".join(md_lines)

    # Bygg talemanus-versjon (ingen markdown, med pause-markører)
    speech_lines = []
    for tittel, innhold in seksjoner:
        # Konverter tekst til TTS-vennlig format
        tale = innhold
        # Fjern markdown-formatering
        tale = re.sub(r'\*\*([^*]+)\*\*', r'\1', tale)
        tale = re.sub(r'\*([^*]+)\*', r'\1', tale)
        # Konverter tall og enheter - viktig å gjøre i riktig rekkefølge
        # Først: fjern @-symbol
        tale = tale.replace(' @ ', ' i ')
        tale = tale.replace('@', ' i ')
        # Pace-range (f.eks. 5:15-5:30/km)
        tale = re.sub(r'(\d+):(\d+)-(\d+):(\d+)/km',
                      r'\1 minutter \2 til \3 minutter \4 per kilometer', tale)
        # Enkel pace-format (før vi endrer /km)
        tale = re.sub(r'(\d+):(\d+)/km', r'\1 minutter og \2 sekunder per kilometer', tale)
        tale = re.sub(r'(\d+):(\d+) per kilometer', r'\1 minutter og \2 sekunder per kilometer', tale)
        # Så: fristående pace-tall (f.eks. "på 5:21")
        tale = re.sub(r' på (\d+):(\d+)', r' på \1 minutter og \2 sekunder', tale)
        tale = re.sub(r' holdt (\d+):(\d+)', r' holdt \1 minutter og \2 sekunder', tale)
        # Til slutt: enheter (men ikke inni ord)
        tale = re.sub(r' (\d+) km\b', r' \1 kilometer', tale)
        tale = re.sub(r' (\d+) min\b', r' \1 minutter', tale)
        tale = re.sub(r' (\d+) sek\b', r' \1 sekunder', tale)
        # Legg til tale
        speech_lines.append(tale)
        speech_lines.append("<pause>")

    # Fjern siste pause
    if speech_lines and speech_lines[-1] == "<pause>":
        speech_lines = speech_lines[:-1]

    speech = "\n\n".join(speech_lines)

    return markdown, speech


def lagre_briefing(dato: str):
    """Genererer og lagrer briefing til fil."""
    markdown, speech = generer_briefing(dato)

    # Opprett rapport-mappe hvis den ikke finnes
    RAPPORT_PATH.mkdir(parents=True, exist_ok=True)

    # Lagre markdown
    md_path = RAPPORT_PATH / f"briefing_{dato}.md"
    md_path.write_text(markdown, encoding='utf-8')

    # Lagre talemanus
    speech_path = RAPPORT_PATH / f"briefing_{dato}_speech.txt"
    speech_path.write_text(speech, encoding='utf-8')

    # Lagre som "siste"
    siste_md = RAPPORT_PATH / "briefing_siste.md"
    siste_md.write_text(markdown, encoding='utf-8')

    siste_speech = RAPPORT_PATH / "briefing_siste_speech.txt"
    siste_speech.write_text(speech, encoding='utf-8')

    return md_path, speech_path


def vis_briefing(dato: str):
    """Viser briefing i terminalen."""
    markdown, _ = generer_briefing(dato)
    print(markdown)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Generer morgenbriefing')
    parser.add_argument('--dato', type=str, help='Dato å generere for (YYYY-MM-DD)')
    parser.add_argument('--lagre', action='store_true', help='Lagre til fil')
    parser.add_argument('--vis', action='store_true', help='Vis i terminal')
    args = parser.parse_args()

    dato = args.dato or datetime.now().strftime('%Y-%m-%d')

    if args.lagre or not args.vis:
        md_path, speech_path = lagre_briefing(dato)
        print(f"📄 Treningsbrief lagret:")
        print(f"   Lesbar: {md_path}")
        print(f"   Tale:   {speech_path}")
        print(f"   Siste:  {RAPPORT_PATH / 'briefing_siste.md'}")

    if args.vis:
        vis_briefing(dato)


if __name__ == '__main__':
    main()
