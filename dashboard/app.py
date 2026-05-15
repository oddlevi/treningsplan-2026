#!/usr/bin/env python3
"""
Treningsplan – Enkel visning med kommentarfelt per seksjon.

Kjør med:
    streamlit run dashboard/app.py
"""

import streamlit as st
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PLAN_PATH = PROJECT_ROOT / "plan" / "current_plan.md"
COMMENTS_PATH = PROJECT_ROOT / "data" / "kommentarer.json"

st.set_page_config(
    page_title="Treningsplan",
    page_icon="🏃",
    layout="wide",
)

st.markdown("""
<style>
    div[data-testid="stTextInput"] input {
        background-color: #e8f5e9 !important;
        border: 1px solid #a5d6a7 !important;
    }
    /* Kompakt meny */
    div[data-testid="stRadio"] > div[role="radiogroup"] {
        gap: 0 !important;
    }
    div[data-testid="stRadio"] label {
        padding: 1px 8px !important;
        margin: 0 !important;
        min-height: auto !important;
    }
    div[data-testid="stRadio"] label p {
        margin: 0 !important;
        line-height: 1.4 !important;
    }
    div[data-testid="stRadio"] label[data-checked="true"] {
        background-color: #e3f2fd !important;
        font-weight: 600;
        border-radius: 4px;
    }
    /* 2024-kolonne (siste kolonne) i grå */
    table td:last-child,
    table th:last-child {
        color: #999 !important;
    }
</style>
""", unsafe_allow_html=True)


def load_comments() -> dict:
    """Laster kommentarer fra fil."""
    if COMMENTS_PATH.exists():
        try:
            return json.loads(COMMENTS_PATH.read_text())
        except:
            return {}
    return {}


def save_comments(comments: dict):
    """Lagrer alle kommentarer."""
    COMMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMENTS_PATH.write_text(json.dumps(comments, indent=2, ensure_ascii=False))


def parse_plan() -> list:
    """Parser planen til seksjoner."""
    if not PLAN_PATH.exists():
        return [{"id": "intro", "title": "Feil", "content": "Plan ikke funnet"}]

    content = PLAN_PATH.read_text()
    lines = content.split("\n")

    sections = []
    current = {"id": "intro", "title": "Introduksjon", "content": []}

    for line in lines:
        # Ny hovedseksjon (# BLOKK eller # UKE)
        if line.startswith("# BLOKK ") or line.startswith("# UKE "):
            # Lagre forrige seksjon
            if current["content"]:
                current["content"] = "\n".join(current["content"])
                sections.append(current)

            title = line.replace("#", "").strip()
            section_id = title.lower().replace(" ", "_").replace("(", "").replace(")", "")
            current = {"id": section_id, "title": title, "content": []}

        current["content"].append(line)

    # Lagre siste seksjon
    if current["content"]:
        current["content"] = "\n".join(current["content"])
        sections.append(current)

    return sections


def main():
    # Last kommentarer
    if "comments" not in st.session_state:
        st.session_state.comments = load_comments()

    sections = parse_plan()

    # Sidebar med navigasjon
    with st.sidebar:
        st.header("📋 Navigasjon")

        # Lag liste med seksjonstitler (uten intro)
        nav_sections = [s for s in sections if s["id"] != "intro"]

        # Bygg valgliste med innrykk for uker
        options = ["Full plan"]
        title_map = {}  # Kort navn -> fullt navn
        for section in nav_sections:
            if section["title"].startswith("BLOKK"):
                options.append(section["title"])
                title_map[section["title"]] = section["title"]
            elif section["title"].startswith("UKE"):
                # Kort versjon: "Uke 1" i stedet for "UKE 1 (datoer)"
                short = section["title"].split("(")[0].strip()
                short = short.replace("UKE", "Uke")
                options.append(f"  {short}")
                title_map[short] = section["title"]

        selected_raw = st.radio("Gå til", options, label_visibility="collapsed", key="nav_radio")
        selected_clean = selected_raw.strip()
        selected = title_map.get(selected_clean, selected_clean)  # Map til fullt navn

        st.divider()

        # Vis antall kommentarer
        num_comments = sum(1 for v in st.session_state.comments.values() if v)
        st.caption(f"💬 {num_comments} kommentarer")

    # Hovedinnhold - vis alltid hele planen
    display_sections = sections

    # Finn section_id for valgt seksjon (for scrolling)
    scroll_to = None
    if selected != "Full plan":
        for s in sections:
            if s["title"] == selected:
                scroll_to = s["id"]
                break

    for section in display_sections:
        st.divider()

        # Legg til anchor for navigasjon
        st.markdown(f'<div id="{section["id"]}"></div>', unsafe_allow_html=True)

        # Vis seksjonens innhold
        st.markdown(section["content"])

        # Kommentarfelt for denne seksjonen (ikke for intro)
        if section["id"] != "intro":
            section_id = section["id"]
            current_comment = st.session_state.comments.get(section_id, "")

            col0, col1, col2 = st.columns([0.4, 6, 0.8])
            with col0:
                st.markdown("#### 💬")
            with col1:
                new_comment = st.text_input(
                    "Kommentar",
                    value=current_comment,
                    key=f"comment_{section_id}",
                    placeholder="Legg til kommentar...",
                    label_visibility="collapsed"
                )
            with col2:
                is_changed = new_comment != current_comment
                if st.button("💾" if is_changed else "✓", key=f"save_{section_id}", disabled=not is_changed):
                    st.session_state.comments[section_id] = new_comment
                    save_comments(st.session_state.comments)
                    st.rerun()

    st.divider()

    # Scroll til valgt seksjon
    if scroll_to:
        st.markdown(f"""
        <script>
            var element = document.getElementById("{scroll_to}");
            if (element) {{
                element.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            }}
        </script>
        """, unsafe_allow_html=True)

    # Eksporter kommentarer
    if st.session_state.comments:
        import json
        comments_json = json.dumps(st.session_state.comments, indent=2, ensure_ascii=False)
        st.download_button(
            "📥 Last ned kommentarer",
            comments_json,
            file_name="kommentarer.json",
            mime="application/json"
        )


if __name__ == "__main__":
    main()
