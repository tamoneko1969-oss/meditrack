"""
MediTrack — Lični Zdravstveni Asistent (v1.0)
=============================================================
Privatni, premium tracker za zdravlje sa AI „Health Guard" zaštitom.
Razvijen po istom obrascu kao „Punu Mask Authenticator Pro":
jedan Streamlit fajl + lokalna SQLite baza.

AI sloj (hibrid, kao Art Hunter — Vision hrani LLM):
  • Google Cloud Vision  → OCR (čita tekst sa deklaracije/dokumenta)
  • Claude (Anthropic)   → rezonovanje nad pročitanim tekstom (verdikt, uvidi)

Funkcije:
  • Dashboard sa vitalnim znacima (san/REM/duboki san, puls, stres, pritisak)
  • Smart Camera:  A) Skeniranje medicinskog dokumenta (Google OCR → struktura)
                   B) Skeniranje prehrambenog proizvoda (Google OCR → lična procena)
  • AI Health Guard: personalizovana procena (GREEN/YELLOW/RED) ukrštena sa
    tvojim dijagnozama, lab nalazima i jutrošnjim krvnim pritiskom.

Pokretanje:
    pip install -r requirements.txt
    setx ANTHROPIC_API_KEY "sk-ant-..."        (za rezonovanje)
    setx GOOGLE_VISION_API_KEY "AIza..."       (za OCR)
    streamlit run app.py
"""

import base64
import io
import json
import os
import sqlite3
from datetime import datetime, date, timedelta

import pandas as pd
import requests
import streamlit as st
from PIL import Image

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None


# --------------------------------------------------------------------------- #
#  Konfiguracija
# --------------------------------------------------------------------------- #
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "medi_track.db")
KEY_FILE = os.path.join(APP_DIR, ".anthropic_key")
GKEY_FILE = os.path.join(APP_DIR, ".google_key")

# Claude radi samo TEKSTUALNO rezonovanje (OCR preuzima Google Vision)
REASONING_MODELS = {
    "Claude Opus 4.8 (najdetaljnija analiza)": "claude-opus-4-8",
    "Claude Sonnet 4.6 (brzo & ekonomično)": "claude-sonnet-4-6",
    "Claude Haiku 4.5 (najbrže / najjeftinije)": "claude-haiku-4-5-20251001",
}

GOOGLE_VISION_URL = "https://vision.googleapis.com/v1/images:annotate"

st.set_page_config(
    page_title="MediTrack — Lični Zdravstveni Asistent",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# --------------------------------------------------------------------------- #
#  Paleta + tema (topla terapeutska estetika, sa Dark Mode prekidačem)
# --------------------------------------------------------------------------- #
# Futuristički „glassmorphism + neon" stil (tirkiz / magenta / neon-zelena na
# tamnom tirkizno-ljubičastom gradijentu). Dva nivoa dubine za prekidač.
PALETTES = {
    "dark": {
        "bg": "#06141f", "card": "#0c1c2e", "sunken": "#16304a",
        "text": "#eaf2ff", "muted": "#9fb3c8", "outline": "rgba(255,255,255,.16)",
        "primary": "#22d3ee", "amber": "#e879f9", "copper": "#34d399",
    },
    "light": {  # „svetliji" neon (i dalje taman gradijent, samo vedriji)
        "bg": "#0a1c2e", "card": "#11263b", "sunken": "#1c3853",
        "text": "#eef5ff", "muted": "#aebfd2", "outline": "rgba(255,255,255,.20)",
        "primary": "#2dd4ee", "amber": "#f0a6ff", "copper": "#4ade80",
    },
}
VERDICT = {"GREEN": "#34d399", "YELLOW": "#fbbf24", "RED": "#fb7185"}


def inject_css(mode: str) -> None:
    p = PALETTES[mode]
    st.markdown(
        f"""
        <style>
        :root {{
            --bg:{p['bg']}; --card:{p['card']}; --sunken:{p['sunken']};
            --text:{p['text']}; --muted:{p['muted']}; --outline:{p['outline']};
            --primary:{p['primary']}; --amber:{p['amber']}; --copper:{p['copper']};
        }}
        /* Neon gradijentna podloga + svetleći blobovi */
        .stApp {{
            color:{p['text']};
            background:
              radial-gradient(820px 560px at 6% 10%, rgba(34,211,238,.18), transparent 60%),
              radial-gradient(820px 640px at 96% 26%, rgba(232,121,249,.20), transparent 60%),
              radial-gradient(680px 480px at 50% 100%, rgba(52,211,153,.12), transparent 60%),
              linear-gradient(125deg,#06141f 0%, #0b1e30 40%, #1a1030 72%, #2a0b3a 100%);
            background-attachment:fixed;
        }}
        section[data-testid="stSidebar"] {{
            background:rgba(8,18,28,.72); backdrop-filter:blur(14px);
            border-right:1px solid {p['outline']};
        }}
        .stApp, .stApp p, .stApp label, .stApp span, .stMarkdown {{ color:{p['text']}; }}
        h1,h2,h3,h4,h5 {{ color:{p['text']};
            font-family:'Segoe UI',system-ui,sans-serif; font-weight:800; letter-spacing:-.3px; }}
        .block-container {{ padding-top:3.4rem; max-width:1180px; }}

        /* Staklena kartica */
        .mt-card {{
            background:rgba(255,255,255,.06); border:1px solid {p['outline']};
            border-radius:22px; padding:20px 22px; margin-bottom:16px;
            backdrop-filter:blur(10px);
            box-shadow:0 18px 40px rgba(0,0,0,.35);
        }}
        /* Hero vital kartica (4 prozora glavnih vitalnih znakova) */
        .mt-vital {{
            position:relative; background:rgba(255,255,255,.06);
            border:1px solid {p['outline']}; border-radius:20px; padding:18px 18px 16px;
            backdrop-filter:blur(10px); min-height:124px;
            transition:.25s; overflow:hidden;
        }}
        .mt-vital:hover {{ transform:translateY(-4px); }}
        .mt-vital .k {{ color:{p['muted']}; font-size:.8rem; font-weight:700;
            text-transform:uppercase; letter-spacing:.6px; }}
        .mt-vital .v {{ font-size:2.1rem; font-weight:900; margin-top:6px; line-height:1; }}
        .mt-vital .u {{ font-size:.9rem; font-weight:600; color:{p['muted']}; }}
        .mt-vital .ic {{ position:absolute; top:16px; right:16px; font-size:1.4rem; opacity:.9; }}
        .mt-vital .sub {{ color:{p['muted']}; font-size:.78rem; margin-top:8px; }}
        .mt-glow-t {{ box-shadow:0 0 0 1px rgba(34,211,238,.35),0 14px 34px rgba(34,211,238,.18); }}
        .mt-glow-m {{ box-shadow:0 0 0 1px rgba(232,121,249,.35),0 14px 34px rgba(232,121,249,.18); }}
        .mt-glow-g {{ box-shadow:0 0 0 1px rgba(52,211,153,.40),0 14px 34px rgba(52,211,153,.20); }}
        .mt-glow-y {{ box-shadow:0 0 0 1px rgba(251,191,36,.40),0 14px 34px rgba(251,191,36,.20); }}

        .mt-camera {{
            background:linear-gradient(135deg,{p['primary']},{p['amber']});
            border-radius:16px; padding:12px 18px; color:#04222a;
            box-shadow:0 10px 26px rgba(34,211,238,.30);
        }}
        .mt-camera h2 {{ color:#04222a!important; margin:0; font-size:1.15rem; }}
        .mt-camera p {{ margin:1px 0 0; font-size:.78rem; line-height:1.25; }}
        .mt-pill {{
            display:inline-flex; align-items:center; gap:8px;
            padding:6px 14px; border-radius:16px; font-weight:800; font-size:.85rem;
        }}
        .mt-metric {{
            background:rgba(255,255,255,.05); border:1px solid {p['outline']};
            border-radius:16px; padding:16px; text-align:center; backdrop-filter:blur(8px);
        }}
        .mt-metric .v {{ font-size:1.5rem; font-weight:800; color:{p['text']}; }}
        .mt-metric .u {{ font-size:.8rem; color:{p['muted']}; }}
        .mt-metric .l {{ font-size:.8rem; color:{p['muted']}; margin-top:2px; }}
        .mt-chip {{
            display:inline-block; padding:6px 13px; border-radius:14px;
            background:rgba(34,211,238,.14); color:{p['primary']};
            border:1px solid rgba(34,211,238,.3);
            font-weight:700; font-size:.82rem; margin:3px 4px 3px 0;
        }}
        .mt-guard {{
            border-radius:16px; padding:14px 16px; margin-bottom:10px;
            border-left:4px solid; backdrop-filter:blur(6px);
        }}
        .mt-muted {{ color:{p['muted']}; }}
        div[data-testid="stMetricValue"] {{ color:{p['text']}; }}
        div[data-testid="stMetricLabel"] {{ color:{p['muted']}; }}
        /* Dugmad: neon */
        .stButton>button {{
            border-radius:14px; font-weight:700; border:1px solid {p['outline']};
            background:rgba(255,255,255,.06); color:{p['text']};
        }}
        .stButton>button:hover {{ border-color:{p['primary']};
            box-shadow:0 0 20px rgba(34,211,238,.35); color:#fff; }}
        .stButton>button[kind="primary"] {{
            background:linear-gradient(135deg,{p['primary']},{p['amber']});
            color:#04222a; border:none; box-shadow:0 10px 26px rgba(34,211,238,.4); }}

        /* „Telefon" ekran — uska centrirana kolona sa vital karticama */
        .phone-screen {{
            max-width:460px; margin:0 auto; border-radius:34px; padding:16px;
            background:linear-gradient(180deg,rgba(8,19,31,.92),rgba(20,10,36,.92));
            border:1px solid {p['outline']};
            box-shadow:0 30px 70px rgba(0,0,0,.55), 0 0 56px rgba(34,211,238,.18);
        }}
        .phone-bar {{ display:flex; justify-content:space-between; align-items:center;
            color:{p['muted']}; font-size:.8rem; padding:4px 8px 14px; }}
        .phone-bar b {{ color:#fff; font-weight:800; display:flex; align-items:center; gap:8px; }}
        .phone-bar .d {{ width:22px; height:22px; border-radius:7px;
            background:linear-gradient(135deg,{p['primary']},{p['amber']}); }}
        .vcard {{ display:flex; justify-content:space-between; align-items:center;
            background:rgba(255,255,255,.06); border:1px solid {p['outline']};
            border-radius:18px; padding:15px 18px; margin-bottom:12px; backdrop-filter:blur(8px); }}
        .vcard:last-child {{ margin-bottom:0; }}
        .vcard .k {{ color:{p['muted']}; font-size:.76rem; font-weight:700;
            text-transform:uppercase; letter-spacing:.5px; }}
        .vcard .v {{ font-size:1.95rem; font-weight:900; line-height:1.05; margin-top:3px; }}
        .vcard .u {{ font-size:.85rem; color:{p['muted']}; font-weight:600; }}
        .vcard .sub {{ color:{p['muted']}; font-size:.74rem; margin-top:4px; }}
        .vcard .viz {{ flex:0 0 auto; margin-left:14px; display:flex; align-items:center; }}
        .led-dot {{ width:18px; height:18px; border-radius:50%; }}

        /* Navigacija: 3×2 mreža koja OSTAJE mreža i na telefonu (ne slaže se vertikalno) */
        .st-key-mtnav div[data-testid="stHorizontalBlock"] {{
            flex-direction:row !important; flex-wrap:nowrap !important; gap:8px !important;
            margin-bottom:8px;
        }}
        .st-key-mtnav div[data-testid="stColumn"],
        .st-key-mtnav div[data-testid="column"] {{
            min-width:0 !important; width:33.33% !important; flex:1 1 0 !important;
        }}
        .st-key-mtnav .stButton>button {{
            padding:10px 4px !important; font-size:.82rem !important;
            line-height:1.1 !important; white-space:nowrap;
        }}

        /* Smart Camera: veliki (skoro pun ekran) preview umesto malog prozora */
        div[data-testid="stCameraInput"] {{ width:100% !important; }}
        div[data-testid="stCameraInput"] > div {{ width:100% !important; max-width:100% !important; }}
        div[data-testid="stCameraInput"] video,
        div[data-testid="stCameraInput"] img {{
            width:100% !important; height:auto !important; max-height:76vh !important;
            object-fit:cover !important; border-radius:16px !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def ring_svg(percent: float, top: str, bottom: str, color: str) -> str:
    """Mali SVG donut prsten za vitalne znake (premium izgled)."""
    r, c = 46, 46
    circ = 2 * 3.14159 * 40
    off = circ * (1 - max(0.0, min(1.0, percent)))
    track = PALETTES[st.session_state.get("theme", "light")]["sunken"]
    text = PALETTES[st.session_state.get("theme", "light")]["text"]
    muted = PALETTES[st.session_state.get("theme", "light")]["muted"]
    return f"""
    <div style="text-align:center">
      <svg width="104" height="104" viewBox="0 0 92 92">
        <circle cx="{c}" cy="{c}" r="40" fill="none" stroke="{track}" stroke-width="8"/>
        <circle cx="{c}" cy="{c}" r="40" fill="none" stroke="{color}" stroke-width="8"
          stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{off:.1f}"
          transform="rotate(-90 {c} {c})"/>
        <text x="{c}" y="{c-2}" text-anchor="middle" font-size="15" font-weight="700"
          fill="{text}">{top}</text>
        <text x="{c}" y="{c+14}" text-anchor="middle" font-size="10"
          fill="{muted}">{bottom}</text>
      </svg>
    </div>"""


def heart_svg(color: str = "#fb7185", size: int = 44) -> str:
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="{color}" '
        f'style="filter:drop-shadow(0 0 7px {color})"><path d="M12 21s-7-4.6-9.5-9'
        f'C1 9 2.6 5.5 6 5.5c2 0 3.2 1.2 4 2.4.8-1.2 2-2.4 4-2.4 3.4 0 5 3.5 3.5 6.5'
        f'C19 16.4 12 21 12 21z"/></svg>'
    )


def sparkline_svg(values: list[float], color: str, w: int = 104, h: int = 46) -> str:
    """Mini linijski grafik (npr. trend sistolnog pritiska)."""
    vals = [float(x) for x in values if x is not None]
    if len(vals) < 2:
        vals = (vals * 2) if vals else [1, 1]
    mn, mx = min(vals), max(vals)
    rng = (mx - mn) or 1
    n = len(vals)
    pts = []
    for i, val in enumerate(vals):
        x = i * (w / (n - 1))
        y = h - 5 - ((val - mn) / rng) * (h - 12)
        pts.append(f"{x:.0f},{y:.0f}")
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"><polyline points="{" ".join(pts)}" '
        f'fill="none" stroke="{color}" stroke-width="2.6" stroke-linecap="round" '
        f'stroke-linejoin="round" style="filter:drop-shadow(0 0 6px {color})"/></svg>'
    )


def mini_ring_svg(percent: float, color: str, label: str = "", size: int = 50) -> str:
    """Mali prsten (npr. udeo sna), sa opcionim tekstom u sredini."""
    r = 19
    circ = 2 * 3.14159 * r
    off = circ * (1 - max(0.0, min(1.0, percent)))
    cx = size / 2
    track = PALETTES[st.session_state.get("theme", "dark")]["sunken"]
    txt = PALETTES[st.session_state.get("theme", "dark")]["text"]
    inner = (f'<text x="{cx}" y="{cx+3}" text-anchor="middle" font-size="11" '
             f'font-weight="800" fill="{txt}">{label}</text>') if label else ""
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<circle cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="{track}" stroke-width="5"/>'
        f'<circle cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="{color}" stroke-width="5" '
        f'stroke-linecap="round" stroke-dasharray="{circ:.0f}" stroke-dashoffset="{off:.0f}" '
        f'transform="rotate(-90 {cx} {cx})" style="filter:drop-shadow(0 0 5px {color})"/>'
        f'{inner}</svg>'
    )


# --------------------------------------------------------------------------- #
#  Sloj baze — TRAJNI Postgres (Supabase) na cloud-u, SQLite lokalno.
#  Aktivira se Postgres ako postoji DATABASE_URL (env ili Streamlit secrets);
#  inače radi lokalni SQLite fajl. Isti SQL (`?` placeholderi) za oba.
# --------------------------------------------------------------------------- #
def _db_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    try:
        return str(st.secrets.get("DATABASE_URL", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


DATABASE_URL = _db_url()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))


def get_conn():
    if IS_PG:
        import psycopg2  # lazy — potrebno samo na cloud-u
        return psycopg2.connect(DATABASE_URL, sslmode="require")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _q(sql: str, params: tuple, fetch: str | None):
    """Jedinstveni izvršilac upita za oba backenda. fetch: 'all'|'one'|None."""
    conn = get_conn()
    try:
        if IS_PG:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql.replace("?", "%s"), params)
        else:
            cur = conn.cursor()
            cur.execute(sql, params)
        res = cur.fetchall() if fetch == "all" else (cur.fetchone() if fetch == "one" else None)
        conn.commit()
        return res
    finally:
        conn.close()


def q_all(sql: str, args: tuple = ()):
    return _q(sql, args, "all")


def q_one(sql: str, args: tuple = ()):
    return _q(sql, args, "one")


def q_exec(sql: str, args: tuple = ()) -> None:
    _q(sql, args, None)


def q_execmany(sql: str, seq) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.executemany(sql.replace("?", "%s") if IS_PG else sql, list(seq))
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    pk = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    real = "DOUBLE PRECISION" if IS_PG else "REAL"
    stmts = [
        f"""CREATE TABLE IF NOT EXISTS user_vitals (
            id {pk}, heart_rate INTEGER, sleep_duration INTEGER,
            deep_sleep_duration INTEGER, rem_sleep_duration INTEGER,
            restless_count INTEGER, stress_level INTEGER,
            blood_pressure_sys INTEGER, blood_pressure_dia INTEGER,
            timestamp TEXT NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS medical_history (
            id {pk}, diagnosis_name TEXT NOT NULL, doctor_report_text TEXT,
            date_diagnosed TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active')""",
        f"""CREATE TABLE IF NOT EXISTS lab_results (
            id {pk}, parameter_name TEXT NOT NULL, value {real} NOT NULL,
            unit TEXT NOT NULL, reference_range TEXT, test_date TEXT NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS scanned_products_log (
            id {pk}, product_name TEXT NOT NULL, ingredients_text TEXT,
            ai_verdict TEXT NOT NULL, analysis_reason TEXT, timestamp TEXT NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY, age INTEGER, height_cm INTEGER,
            weight_kg {real}, sex TEXT, updated_at TEXT)""",
    ]
    conn = get_conn()
    try:
        cur = conn.cursor()
        for s in stmts:
            cur.execute(s)
        conn.commit()
    finally:
        conn.close()


def seed_demo_data() -> None:
    """Ubacuje primer podataka da Dashboard ne bude prazan na prvom startu."""
    q_exec(
        """INSERT INTO user_vitals (heart_rate,sleep_duration,deep_sleep_duration,
           rem_sleep_duration,restless_count,stress_level,blood_pressure_sys,
           blood_pressure_dia,timestamp) VALUES (?,?,?,?,?,?,?,?,?)""",
        (58, 378, 92, 124, 3, 32, 145, 95, datetime.now().isoformat()),
    )
    q_execmany(
        """INSERT INTO medical_history (diagnosis_name,doctor_report_text,
           date_diagnosed,status) VALUES (?,?,?,?)""",
        [
            ("Hipertenzija", "Granična, pod kontrolom dijetom.", "2024-09-01", "active"),
            ("Deficit gvožđa", "Blaga sideropenija.", "2025-02-14", "active"),
        ],
    )
    q_execmany(
        """INSERT INTO lab_results (parameter_name,value,unit,reference_range,test_date)
           VALUES (?,?,?,?,?)""",
        [
            ("Glukoza", 5.1, "mmol/L", "3.9-5.5", "2025-05-20"),
            ("Natrijum", 138, "mmol/L", "136-145", "2025-05-20"),
            ("Gvožđe", 9.2, "µmol/L", "11-28", "2025-05-20"),
            ("Holesterol", 5.6, "mmol/L", "<5.2", "2025-05-20"),
        ],
    )


def latest_vitals():
    return q_one("SELECT * FROM user_vitals ORDER BY timestamp DESC LIMIT 1")


def active_diagnoses():
    return q_all(
        "SELECT * FROM medical_history WHERE status='active' ORDER BY date_diagnosed DESC"
    )


def latest_labs():
    return q_all(
        """SELECT lr.* FROM lab_results lr INNER JOIN (
               SELECT parameter_name, MAX(test_date) md FROM lab_results
               GROUP BY parameter_name) t
           ON lr.parameter_name=t.parameter_name AND lr.test_date=t.md
           ORDER BY lr.parameter_name"""
    )


def recent_scans(limit: int = 15):
    return q_all(
        "SELECT * FROM scanned_products_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    )


def vitals_series(days: int):
    """Hronološki niz vitalnih znakova u poslednjih `days` dana (rastuće po vremenu)."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    return q_all(
        "SELECT * FROM user_vitals WHERE timestamp >= ? ORDER BY timestamp ASC",
        (since,),
    )


def get_profile():
    """Lični profil korisnika (godine, visina, težina, pol) — jedan red (id=1)."""
    return q_one("SELECT * FROM user_profile WHERE id = 1")


def save_profile(age, height_cm, weight_kg, sex) -> None:
    q_exec(
        """INSERT INTO user_profile (id, age, height_cm, weight_kg, sex, updated_at)
           VALUES (1, ?, ?, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET age=excluded.age,
             height_cm=excluded.height_cm, weight_kg=excluded.weight_kg,
             sex=excluded.sex, updated_at=excluded.updated_at""",
        (age, height_cm, weight_kg, sex, datetime.now().isoformat()),
    )


def compute_bmi(height_cm, weight_kg):
    """Vraća (bmi, kategorija) ili (None, '') ako nema podataka."""
    if not height_cm or not weight_kg:
        return (None, "")
    bmi = weight_kg / ((height_cm / 100) ** 2)
    if bmi < 18.5:
        cat = "pothranjenost"
    elif bmi < 25:
        cat = "normalna težina"
    elif bmi < 30:
        cat = "prekomerna težina"
    else:
        cat = "gojaznost"
    return (round(bmi, 1), cat)


# --------------------------------------------------------------------------- #
#  API ključ — isti obrazac kao Punu app (secrets → env → lokalni fajl)
# --------------------------------------------------------------------------- #
def _secret(name: str, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:  # noqa: BLE001
        return default


def require_login() -> None:
    """Ako je u secrets postavljen `app_password`, traži ga pre pristupa.
    Lokalno (bez secrets) aplikacija je otvorena."""
    pw = _secret("app_password")
    if not pw or st.session_state.get("_authed"):
        return
    st.markdown("### 🔐 MediTrack — pristup zaštićen")
    st.caption("Unesi lozinku da bi koristio aplikaciju.")
    entered = st.text_input("Lozinka", type="password", key="_login_pw")
    if st.button("Uđi", type="primary"):
        if entered == pw:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Pogrešna lozinka.")
    st.stop()


def load_key(secret_names: list[str], file_path: str) -> str:
    """Učitava ključ: secrets (cloud) → env varijabla → lokalni fajl."""
    for name in secret_names:
        sec = _secret(name)
        if sec:
            return str(sec).strip()
        env = os.environ.get(name, "").strip()
        if env:
            return env
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def save_key_file(file_path: str, value: str) -> None:
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write(value.strip())


# --------------------------------------------------------------------------- #
#  Vision / AI sloj — isti obrazac (encode → stream → robustan JSON)
# --------------------------------------------------------------------------- #
def image_to_b64(uploaded_file) -> str | None:
    """Priprema sliku za Google Vision: skalira i vraća base64 JPEG string."""
    if uploaded_file is None:
        return None
    img = Image.open(uploaded_file)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.thumbnail((1600, 1600))  # Vision voli veću rezoluciju za OCR
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def google_ocr(image_b64: str, google_key: str) -> str:
    """Google Cloud Vision DOCUMENT_TEXT_DETECTION → ceo pročitan tekst."""
    payload = {
        "requests": [
            {
                "image": {"content": image_b64},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "imageContext": {"languageHints": ["sr", "en", "hr", "de"]},
            }
        ]
    }
    r = requests.post(
        GOOGLE_VISION_URL, params={"key": google_key.strip()},
        json=payload, timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Google Vision HTTP {r.status_code}: {r.text[:300]}")
    resp = r.json().get("responses", [{}])[0]
    if "error" in resp:
        raise RuntimeError(f"Google Vision: {resp['error'].get('message', 'greška')}")
    fta = resp.get("fullTextAnnotation")
    return (fta or {}).get("text", "").strip()


def _make_client(api_key: str) -> "anthropic.Anthropic":
    return anthropic.Anthropic(api_key=api_key.strip(), timeout=120.0, max_retries=4)


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.lstrip().startswith("json"):
            raw = raw.lstrip()[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]
    return json.loads(raw)


def build_health_context() -> str:
    """Skuplja kompletan zdravstveni kontekst korisnika za AI ukrštanje.
    Lični profil (godine/visina/težina/BMI) je OBAVEZAN okvir za svaki savet."""
    p = get_profile()
    v = latest_vitals()
    dx = active_diagnoses()
    labs = latest_labs()

    if p and (p["age"] or p["height_cm"] or p["weight_kg"]):
        bmi, cat = compute_bmi(p["height_cm"], p["weight_kg"])
        prof = (
            f"godine: {p['age'] or '—'}, visina: {p['height_cm'] or '—'} cm, "
            f"težina: {p['weight_kg'] or '—'} kg, pol: {p['sex'] or '—'}, "
            f"BMI: {bmi if bmi else '—'}"
            + (f" ({cat})" if cat else "")
        )
    else:
        prof = "NIJE UNET — zatraži od korisnika da popuni Profil za precizniji savet"

    bp = "nije zabeležen"
    if v and v["blood_pressure_sys"]:
        bp = f"{v['blood_pressure_sys']}/{v['blood_pressure_dia']} mmHg"
    dx_list = ", ".join(d["diagnosis_name"] for d in dx) or "nema aktivnih dijagnoza"
    lab_list = (
        "; ".join(
            f"{l['parameter_name']} {l['value']} {l['unit']} (ref {l['reference_range'] or '—'})"
            for l in labs
        )
        or "nema lab nalaza"
    )
    hr = v["heart_rate"] if v else "—"
    return (
        f"LIČNI PROFIL (OBAVEZNO uzeti u obzir za SVAKI savet, dozu, normu i procenu): {prof}\n"
        f"KRVNI PRITISAK (poslednji): {bp}\n"
        f"PULS U MIROVANJU: {hr} bpm\n"
        f"AKTIVNE DIJAGNOZE: {dx_list}\n"
        f"LABORATORIJSKI NALAZI (poslednji): {lab_list}\n"
        f"PRAVILO: Svaku procenu, preporuku i pretragu prilagodi OVOM osobi — "
        f"njenim godinama, telesnoj masi (BMI) i polu. Norme i porcije računaj po "
        f"kg telesne mase gde je relevantno. Ne daj generičke savete."
    )


FOOD_SYSTEM = """Ti si lični klinički nutricionista-asistent. Dobijaš TEKST sa \
deklaracije prehrambenog proizvoda (nutritivna tablica / sastojci), pročitan \
preko OCR-a, i daješ STROGO PERSONALIZOVANU procenu za KONKRETNOG korisnika \
čiji zdravstveni profil dobijaš u poruci.

NE daješ generičku recenziju. Uvek ukrštaš sastav proizvoda sa korisnikovim \
krvnim pritiskom, aktivnim dijagnozama i laboratorijskim nalazima. Primeri logike:
- Visok natrijum + povišen pritisak ili hipertenzija → RED ili YELLOW.
- Visok šećer + povišena glukoza / dijabetes → RED ili YELLOW.
- Visoko gvožđe ili vitamin C + deficit gvožđa → pozitivno (GREEN).
- Zasićene masti + povišen holesterol → YELLOW ili RED.

Budi konkretan: u obrazloženju citiraj korisnikovu vrednost (npr. „natrijum se \
kosi sa tvojim jutrošnjim pritiskom 145/95").

VRATI ISKLJUČIVO validan JSON (bez markdown ograda, bez teksta okolo):
{
  "product_name": "<naziv proizvoda ako se vidi, inače kratak opis>",
  "ingredients_text": "<ključni sastojci / nutritivne vrednosti pročitane sa slike>",
  "status": "GREEN" | "YELLOW" | "RED",
  "verdict_message": "<personalizovano obrazloženje sa korisnikovim vrednostima>",
  "actionable_advice": "<konkretan savet ili zdravija alternativa>"
}
Sav tekst piši na srpskom. Ako je OCR tekst prazan/nečitak, status='YELLOW' i objasni zašto."""


def analyze_food(ocr_text: str, model_id: str, api_key: str) -> dict:
    client = _make_client(api_key)
    ctx = build_health_context()
    user_msg = (
        f"ZDRAVSTVENI PROFIL KORISNIKA:\n{ctx}\n\n"
        f"PROČITAN TEKST SA DEKLARACIJE (OCR):\n\"\"\"\n{ocr_text or '(prazno)'}\n\"\"\"\n\n"
        f"Vrati ISKLJUČIVO JSON prema strukturi."
    )
    with client.messages.stream(
        model=model_id, max_tokens=1200, system=FOOD_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        resp = stream.get_final_message()
    raw = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(raw)


DOC_SYSTEM = """Ti si medicinski strukturator. Dobijaš TEKST medicinskog dokumenta \
(laboratorijski nalaz, lekarski izveštaj, otpusna lista, CT/MR nalaz) pročitan \
preko OCR-a. Izdvoj strukturisane podatke iz tog teksta.

VRATI ISKLJUČIVO validan JSON:
{
  "document_type": "<lab nalaz | lekarski izveštaj | otpusna lista | snimak | ostalo>",
  "full_text": "<uredno formatiran tekst dokumenta>",
  "lab_results": [
    {"parameter_name":"<npr. Glukoza>","value":<broj>,"unit":"<npr. mmol/L>","reference_range":"<npr. 3.9-5.5 ili prazno>"}
  ],
  "diagnoses": ["<dijagnoza ako je navedena>"],
  "summary": "<2-3 rečenice sažetka nalaza na srpskom>"
}
Ako nešto nije prisutno, vrati prazan niz. Sve opisne delove piši na srpskom."""


def ocr_document(ocr_text: str, model_id: str, api_key: str) -> dict:
    client = _make_client(api_key)
    user_msg = (
        f"PROČITAN TEKST DOKUMENTA (OCR):\n\"\"\"\n{ocr_text or '(prazno)'}\n\"\"\"\n\n"
        f"Strukturiraj ga i vrati ISKLJUČIVO JSON prema strukturi."
    )
    with client.messages.stream(
        model=model_id, max_tokens=2000, system=DOC_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        resp = stream.get_final_message()
    raw = "".join(b.text for b in resp.content if b.type == "text")
    data = _parse_json(raw)
    # Garancija: ako model ne vrati full_text, koristi sirovi OCR
    if not data.get("full_text"):
        data["full_text"] = ocr_text
    return data


GUARD_SYSTEM = """Ti si „AI Health Guard" — lični zdravstveni savetnik. Na osnovu \
korisnikovih vitalnih znakova, dijagnoza i lab nalaza daješ 2-3 kratke, konkretne, \
personalizovane dnevne preporuke i sigurnosna upozorenja.

VRATI ISKLJUČIVO validan JSON:
{ "insights": [ {"status":"GREEN|YELLOW|RED","title":"<kratak naslov>","message":"<1-2 rečenice konkretnog saveta>"} ] }
Piši na srpskom. Budi koristan i nealarmantan, ali jasan kod realnih rizika."""


def health_guard(model_id: str, api_key: str) -> dict:
    client = _make_client(api_key)
    ctx = build_health_context()
    with client.messages.stream(
        model=model_id, max_tokens=900, system=GUARD_SYSTEM,
        messages=[{"role": "user", "content": f"ZDRAVSTVENI PROFIL:\n{ctx}\n\nDaj uvide kao JSON."}],
    ) as stream:
        resp = stream.get_final_message()
    raw = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(raw)


VITALS_SYSTEM = """Ti čitaš VREDNOSTI sa ekrana medicinskog uređaja na osnovu OCR \
teksta sa fotografije. Izvor je obično jedan od:
- APARAT ZA PRITISAK (npr. Tensoval, Omron…): tri broja poređana odozgo nadole — \
  SYS (gornji/sistolni, najveći), DIA (donji/dijastolni), i PULS (PUL / ♥ / 1/min). \
  SYS je uvek veći od DIA. Često piše i TIME sa vremenom merenja (npr. „14:14").
- PAMETNI SAT: puls (bpm), nivo stresa (0-100), ili san (sati/minuti, duboki san, REM).

PRAVILA:
- IGNORIŠI oznake koje NISU merenja: M1/M2 (memorija), mmHg, 1/min, bpm, naziv brenda.
- Vreme „14:14" je VREME MERENJA, a NE puls/pritisak — stavi ga u reading_time.
- Izvuci ISKLJUČIVO ono što se zaista vidi. Za sve što nije prisutno → null.
- Sate sna pretvori u MINUTE (npr. „6h 18m" = 378; „1h 32m" = 92).

VRATI ISKLJUČIVO validan JSON (bez markdown ograda):
{
  "device_type": "<aparat za pritisak | pametni sat | drugo>",
  "device_name": "<naziv/brend ako se vidi, npr. Tensoval, inače null>",
  "reading_time": "<HH:MM sa ekrana ako se vidi, inače null>",
  "reading_date": "<YYYY-MM-DD ako se vidi datum, inače null>",
  "heart_rate": <bpm/puls ili null>,
  "blood_pressure_sys": <broj ili null>,
  "blood_pressure_dia": <broj ili null>,
  "stress_level": <0-100 ili null>,
  "sleep_duration": <ukupan san u minutima ili null>,
  "deep_sleep_duration": <minuti ili null>,
  "rem_sleep_duration": <minuti ili null>,
  "restless_count": <broj buđenja ili null>,
  "notes": "<kratko na srpskom šta si pročitao i sa kog uređaja>"
}
Ako je tekst nečitak, sva merenja null i objasni u notes."""


def analyze_vitals(ocr_text: str, model_id: str, api_key: str) -> dict:
    client = _make_client(api_key)
    user_msg = (
        f"OCR TEKST SA EKRANA UREĐAJA:\n\"\"\"\n{ocr_text or '(prazno)'}\n\"\"\"\n\n"
        f"Izvuci merenja i vrati ISKLJUČIVO JSON prema strukturi."
    )
    with client.messages.stream(
        model=model_id, max_tokens=600, system=VITALS_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        resp = stream.get_final_message()
    raw = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(raw)


SMART_ROUTER_SYSTEM = """Ti si „mozak" zdravstvene aplikacije. Dobijaš TEKST \
pročitan sa fotografije (preko OCR-a) i zdravstveni profil korisnika. Tvoj posao:
1) PREPOZNAJ šta je na slici (klasifikuj), 2) IZVUCI odgovarajuće podatke.

Tipovi (doc_type):
- "vitals_device" — ekran MERAČA PRITISKA (SYS/DIA/PULS, npr. Tensoval/Omron) ili \
  PAMETNOG SATA (puls, stres, san). Ima brojeve merenja, često i vreme (npr. 14:14).
- "food_product" — DEKLARACIJA HRANE (nutritivna tablica, sastojci, energetska vrednost).
- "medical_document" — LABORATORIJSKI NALAZ, lekarski izveštaj, otpusna lista, CT/MR.
- "unknown" — ako se ne može pouzdano svrstati.

Popuni SAMO objekat koji odgovara prepoznatom tipu; ostale stavi na null.

VRATI ISKLJUČIVO validan JSON (bez markdown ograda):
{
  "doc_type": "vitals_device" | "food_product" | "medical_document" | "unknown",
  "confidence": "niska" | "srednja" | "visoka",
  "reason": "<kratko zašto si tako klasifikovao>",
  "vitals": {
    "device_type": "<aparat za pritisak | pametni sat | drugo>",
    "device_name": "<brend ili null>",
    "reading_time": "<HH:MM ili null>", "reading_date": "<YYYY-MM-DD ili null>",
    "heart_rate": <broj|null>, "blood_pressure_sys": <broj|null>,
    "blood_pressure_dia": <broj|null>, "stress_level": <0-100|null>,
    "sleep_duration": <min|null>, "deep_sleep_duration": <min|null>,
    "rem_sleep_duration": <min|null>, "restless_count": <broj|null>,
    "notes": "<kratko>"
  } | null,
  "food": {
    "product_name": "<naziv ili opis>", "ingredients_text": "<ključni sastojci/vrednosti>",
    "status": "GREEN" | "YELLOW" | "RED",
    "verdict_message": "<personalizovano, citiraj korisnikove vrednosti>",
    "actionable_advice": "<konkretan savet>"
  } | null,
  "document": {
    "document_type": "<lab nalaz | lekarski izveštaj | otpusna lista | snimak | ostalo>",
    "full_text": "<uredan tekst>",
    "lab_results": [ {"parameter_name":"<...>","value":<broj>,"unit":"<...>","reference_range":"<... ili prazno>"} ],
    "diagnoses": ["<dijagnoza>"], "summary": "<2-3 rečenice>"
  } | null,
  "notes": "<poruka korisniku na srpskom>"
}

PRAVILA za vitals (KRITIČNO za tačnost sa LCD/sedam-segmentnih ekrana):
- Brojevi su poređani VERTIKALNO odozgo nadole: 1) SYS (gornji/sistolni, NAJVEĆI broj),
  2) DIA (donji/dijastolni, srednji broj), 3) PULS (najdonji broj, uz „PULSE/1/min").
- Čitaj cifre PAŽLJIVO sa SLIKE (npr. „140" se na sedam-segmentu lako pomeša). SYS je
  skoro uvek 100-180, DIA 60-110, PULS 45-100 — koristi to za proveru logike.
- IGNORIŠI: M1/M2 (memorija), mmHg, 1/min, bpm, brend. Vreme (npr. 17:24) ide u
  reading_time i NIJE merenje. Sate sna pretvori u minute.
PRAVILA za food: NE generička recenzija — ukrsti sa korisnikovim pritiskom, dijagnozama \
i lab nalazima (visok natrijum + povišen pritisak → RED/YELLOW, itd.).
Sav opisni tekst na srpskom."""


def _claude_image_block(image_b64: str) -> dict:
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}}


def smart_analyze(image_b64: str, ocr_text: str, model_id: str, api_key: str) -> dict:
    """Jedan poziv: Claude ČITA SAMU SLIKU (pouzdano za LCD/cifre), klasifikuje i
    izvlači podatke. Google OCR tekst je samo pomoćni nagoveštaj."""
    client = _make_client(api_key)
    ctx = build_health_context()
    content = [
        {"type": "text", "text": f"ZDRAVSTVENI PROFIL KORISNIKA:\n{ctx}"},
        {"type": "text", "text": "FOTOGRAFIJA — čitaj PRVENSTVENO direktno sa slike "
         "(naročito brojeve na ekranu/LCD-u, deklaracije i nalaze):"},
        _claude_image_block(image_b64),
    ]
    if ocr_text:
        content.append({"type": "text", "text": "Pomoćni OCR tekst (može imati grešaka — "
                        f"SLIKA ima prednost):\n\"\"\"\n{ocr_text}\n\"\"\""})
    content.append({"type": "text", "text":
                    "Klasifikuj i izvuci podatke. Vrati ISKLJUČIVO JSON prema strukturi."})
    with client.messages.stream(
        model=model_id, max_tokens=2200, system=SMART_ROUTER_SYSTEM,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        resp = stream.get_final_message()
    raw = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(raw)


# --------------------------------------------------------------------------- #
#  Inicijalizacija stanja
# --------------------------------------------------------------------------- #
init_db()
if "theme" not in st.session_state:
    st.session_state["theme"] = "light"
if "view" not in st.session_state:
    st.session_state["view"] = "Dashboard"
inject_css(st.session_state["theme"])
require_login()

# Auto-seed na praznoj bazi (samo prvi put)
if latest_vitals() is None and not q_one("SELECT 1 FROM lab_results LIMIT 1"):
    seed_demo_data()


# --------------------------------------------------------------------------- #
#  Sidebar — podešavanja i API ključ
# --------------------------------------------------------------------------- #
def key_input(label: str, secret_names: list[str], file_path: str,
              state_key: str, help_url: str) -> str:
    """Uniforman unos ključa: secrets (cloud) → polje + Sačuvaj/Obriši (lokalno)."""
    sec = next((str(_secret(n)).strip() for n in secret_names if _secret(n)), "")
    if sec:
        st.caption(f"🔒 {label} je konfigurisan na serveru (secrets).")
        return sec
    clear_flag = f"_clear_{state_key}"
    if st.session_state.pop(clear_flag, False):
        st.session_state[state_key] = ""
    if state_key not in st.session_state:
        st.session_state[state_key] = load_key(secret_names, file_path)
    val = st.text_input(label, type="password", key=state_key, help=help_url)
    remember = st.checkbox("💾 Zapamti", value=os.path.exists(file_path), key=f"rem_{state_key}")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Sačuvaj", use_container_width=True, key=f"save_{state_key}"):
            if remember and val.strip():
                try:
                    save_key_file(file_path, val)
                    st.success("Sačuvano.")
                except OSError as e:
                    st.error(f"Greška: {e}")
            else:
                st.info("Čekiraj „Zapamti“ i unesi ključ.")
    with c2:
        if st.button("Obriši", use_container_width=True, key=f"del_{state_key}"):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                st.session_state[clear_flag] = True
                st.rerun()
            except OSError as e:
                st.error(f"Greška: {e}")
    return val


with st.sidebar:
    st.header("⚙️ Podešavanja")
    model_label = st.selectbox("Claude model (rezonovanje)",
                               list(REASONING_MODELS.keys()), index=0)
    model_id = REASONING_MODELS[model_label]

    dark = st.toggle("🌙 Tamni režim", value=st.session_state["theme"] == "dark")
    new_theme = "dark" if dark else "light"
    if new_theme != st.session_state["theme"]:
        st.session_state["theme"] = new_theme
        st.rerun()

    st.divider()
    st.markdown("**🔑 Google Vision** — OCR (čitanje teksta)")
    google_key = key_input(
        "GOOGLE_VISION_API_KEY", ["GOOGLE_VISION_API_KEY", "GOOGLE_API_KEY"],
        GKEY_FILE, "google_key",
        "GCP → APIs & Services → Credentials → API key (uključen Cloud Vision API).",
    )

    st.divider()
    st.markdown("**🔑 Anthropic** — rezonovanje (verdikt, uvidi)")
    api_key = key_input(
        "ANTHROPIC_API_KEY", ["ANTHROPIC_API_KEY"], KEY_FILE, "api_key",
        "console.anthropic.com → API Keys.",
    )

    st.divider()
    st.caption(
        "MediTrack daje **savetodavne** informacije i nije zamena za lekara. "
        "Kod hitnih simptoma obrati se lekaru."
    )

google_ready = bool(google_key)
api_ready = bool(api_key)


# --------------------------------------------------------------------------- #
#  Navigacija
# --------------------------------------------------------------------------- #
labels = ["🏠 Dashboard", "📷 Smart Camera", "📈 Trendovi", "👤 Profil",
          "✍️ Unos podataka", "🗂️ Istorija"]
views = ["Dashboard", "Smart Camera", "Trendovi", "Profil", "Unos podataka", "Istorija"]
# Navigacija kao 3×2 mreža — ostaje mreža i na telefonu (CSS .st-key-mtnav)
with st.container(key="mtnav"):
    rows = [st.columns(3), st.columns(3)]
    for idx, (lab, vw) in enumerate(zip(labels, views)):
        with rows[idx // 3][idx % 3]:
            kind = "primary" if st.session_state["view"] == vw else "secondary"
            if st.button(lab, use_container_width=True, type=kind, key=f"nav_{vw}"):
                st.session_state["view"] = vw
                st.rerun()

st.write("")
view = st.session_state["view"]


# =========================================================================== #
#  VIEW: DASHBOARD
# =========================================================================== #
def compute_status(v) -> tuple[str, str]:
    """Obrada poslednjeg unosa → (labela, ključ boje GREEN/YELLOW/RED)."""
    if not v:
        return ("Nema podataka", "YELLOW")
    sev = 0  # 0=ok, 1=granično, 2=povišeno
    label = "Optimalan"
    sys, dia = v["blood_pressure_sys"], v["blood_pressure_dia"]
    if sys and dia:
        if sys >= 140 or dia >= 90:
            sev, label = 2, "Povišen pritisak"
        elif sys >= 130 or dia >= 85:
            sev, label = max(sev, 1), "Granični pritisak"
    hr = v["heart_rate"]
    if hr and (hr > 100 or hr < 45) and sev < 2:
        sev, label = max(sev, 1), "Puls van granica"
    stress = v["stress_level"]
    if stress and stress >= 75 and sev < 2:
        sev, label = max(sev, 1), "Povišen stres"
    return (label, ["GREEN", "YELLOW", "RED"][sev])


def _vital_card(k: str, val, unit: str, glow: str, icon: str,
                sub: str = "", color: str | None = None) -> str:
    cv = f"color:{color}" if color else ""
    return (
        f"<div class='mt-vital {glow}'><span class='ic'>{icon}</span>"
        f"<div class='k'>{k}</div>"
        f"<div class='v' style='{cv}'>{val} <span class='u'>{unit}</span></div>"
        f"<div class='sub'>{sub}</div></div>"
    )


def render_dashboard():
    today = date.today().strftime("%A, %d.%m.%Y.")
    v = latest_vitals()
    status_label, status_key = compute_status(v)
    status_col = VERDICT[status_key]
    last_ts = v["timestamp"][:16].replace("T", " ") if v else "—"

    # --- Header ---
    st.markdown(f"<div class='mt-muted' style='letter-spacing:1.5px'>{today.upper()}</div>",
                unsafe_allow_html=True)
    st.markdown("## Dobro jutro, Dejane 👋")
    st.markdown(
        f"<span class='mt-pill' style='background:{status_col}22;color:{status_col};"
        f"border:1px solid {status_col}55'>● Status: {status_label}</span>"
        f" <span class='mt-muted' style='font-size:.82rem'>· obrađeno iz poslednjeg "
        f"unosa ({last_ts})</span>", unsafe_allow_html=True)

    st.write("")

    # --- Telefon-ekran: glavni vitalni znaci (jedan ispod drugog, sa vizualima) ---
    if v:
        bp = (f"{v['blood_pressure_sys']}/{v['blood_pressure_dia']}"
              if v["blood_pressure_sys"] else "—")
        sl = v["sleep_duration"] or 0
        sleep_txt = f"{sl//60}h {sl%60:02d}m" if sl else "—"
        sleep_pct = min(sl / 480, 1) if sl else 0
        sys_series = [r["blood_pressure_sys"] for r in vitals_series(30)
                      if r["blood_pressure_sys"]]
        clock = v["timestamp"][11:16] if len(v["timestamp"]) >= 16 else ""

        st.markdown(f"""
        <div class="phone-screen">
          <div class="phone-bar"><b><span class="d"></span> MediTrack</b><span>{clock} 🔔</span></div>

          <div class="vcard">
            <div><div class="k">Zdravstveni status</div>
              <div class="v" style="color:{status_col}">{status_label}</div>
              <div class="sub">obrađeno iz poslednjeg unosa · {last_ts}</div></div>
            <div class="viz"><span class="led-dot" style="background:{status_col};
              box-shadow:0 0 16px {status_col}"></span></div>
          </div>

          <div class="vcard">
            <div><div class="k">Puls</div>
              <div class="v">{v['heart_rate'] or '—'} <span class="u">bpm</span></div>
              <div class="sub">u mirovanju</div></div>
            <div class="viz">{heart_svg()}</div>
          </div>

          <div class="vcard">
            <div><div class="k">Pritisak</div>
              <div class="v">{bp} <span class="u">mmHg</span></div>
              <div class="sub">sistolni / dijastolni</div></div>
            <div class="viz">{sparkline_svg(sys_series, PALETTES[st.session_state['theme']]['amber'])}</div>
          </div>

          <div class="vcard">
            <div><div class="k">San</div>
              <div class="v">{sleep_txt}</div>
              <div class="sub">stres {v['stress_level'] or '—'}/100</div></div>
            <div class="viz">{mini_ring_svg(sleep_pct, PALETTES[st.session_state['theme']]['primary'])}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Još nema vitalnih podataka — skeniraj merač pritiska ili dodaj na „Unos podataka“.")

    st.write("")

    # --- Detaljni vitalni znaci (san + dodatne metrike) ---
    st.markdown("### 🌙 San i detaljne metrike")
    if v:
        sleep = v["sleep_duration"] or 0
        deep = v["deep_sleep_duration"] or 0
        rem = v["rem_sleep_duration"] or 0
        r1, r2, r3 = st.columns(3)
        with r1:
            st.markdown(ring_svg(min(sleep / 480, 1), f"{sleep//60}h {sleep%60:02d}m",
                                 "San", PALETTES[st.session_state["theme"]]["primary"]),
                        unsafe_allow_html=True)
        with r2:
            st.markdown(ring_svg(min(deep / 120, 1), f"{deep//60}h {deep%60:02d}m",
                                 "Duboki", PALETTES[st.session_state["theme"]]["amber"]),
                        unsafe_allow_html=True)
        with r3:
            st.markdown(ring_svg(min(rem / 150, 1), f"{rem//60}h {rem%60:02d}m",
                                 "REM", PALETTES[st.session_state["theme"]]["copper"]),
                        unsafe_allow_html=True)

        m1, m2, m3, m4 = st.columns(4)
        bp = (f"{v['blood_pressure_sys']}/{v['blood_pressure_dia']}"
              if v["blood_pressure_sys"] else "—")
        for col, val, unit, lab in [
            (m1, v["heart_rate"] or "—", "bpm", "Puls (mirovanje)"),
            (m2, v["stress_level"] or "—", "/100", "Stres"),
            (m3, v["restless_count"] or "—", "x", "Nemiran san"),
            (m4, bp, "mmHg", "Krvni pritisak"),
        ]:
            with col:
                st.markdown(
                    f"<div class='mt-metric'><div class='v'>{val} <span class='u'>{unit}</span></div>"
                    f"<div class='l'>{lab}</div></div>", unsafe_allow_html=True)
    else:
        st.info("Još nema vitalnih podataka — dodaj ih na kartici „Unos podataka“.")

    st.write("")

    # --- Medicinski karton ---
    cc_left, cc_right = st.columns(2)
    with cc_left:
        st.markdown("### 📋 Medicinski karton")
        dx = active_diagnoses()
        if dx:
            chips = "".join(f"<span class='mt-chip'>{d['diagnosis_name']}</span>" for d in dx)
            st.markdown(chips, unsafe_allow_html=True)
        else:
            st.caption("Nema aktivnih dijagnoza.")
        st.write("")
        for l in latest_labs():
            ref = l["reference_range"] or ""
            st.markdown(
                f"**{l['parameter_name']}** — {l['value']} {l['unit']}  "
                f"<span class='mt-muted'>(ref {ref})</span>", unsafe_allow_html=True)

    with cc_right:
        st.markdown("### ✨ AI Health Guard")
        if not api_ready:
            st.caption("Unesi API ključ u ⚙️ (levo) za personalizovane uvide.")
        elif st.button("Generiši dnevne uvide", use_container_width=True):
            with st.spinner("AI analizira tvoj profil…"):
                try:
                    data = health_guard(model_id, api_key)
                    st.session_state["guard"] = data.get("insights", [])
                except Exception as e:  # noqa: BLE001
                    st.error(f"Greška: {e}")
        for ins in st.session_state.get("guard", []):
            col = VERDICT.get(ins.get("status", "YELLOW"), VERDICT["YELLOW"])
            st.markdown(
                f"<div class='mt-guard' style='border-color:{col};background:{col}1A'>"
                f"<b style='color:{col}'>{ins.get('title','')}</b><br>{ins.get('message','')}</div>",
                unsafe_allow_html=True)


# =========================================================================== #
#  VIEW: SMART CAMERA
# =========================================================================== #
def _handle_scan_result(res: dict) -> bool:
    """Rutira jedan AI nalaz u pravu tabelu i prikazuje ga. Vraća True ako prepoznato."""
    dt = res.get("doc_type")
    conf = res.get("confidence", "")
    if dt == "vitals_device" and res.get("vitals"):
        st.success(f"🩺 Prepoznato: merenje sa uređaja · pouzdanost {conf}.")
        _transfer_vitals_to_entry(res["vitals"])
    elif dt == "food_product" and res.get("food"):
        st.success(f"🍎 Prepoznato: prehrambeni proizvod · pouzdanost {conf}.")
        _show_food_result(res["food"])
    elif dt == "medical_document" and res.get("document"):
        st.success(f"📄 Prepoznato: medicinski dokument · pouzdanost {conf}.")
        _store_and_show_doc(res["document"])
    else:
        st.warning("Nisam uspeo pouzdano da prepoznam sliku. "
                   f"{res.get('notes') or ''} Probaj jasniju/bližu fotografiju.")
        return False
    return True


def render_camera():
    st.markdown("## 📷 Smart Camera")
    st.caption("Slikaj ili otpremi bilo šta — merač pritiska, deklaraciju hrane ili "
               "medicinski nalaz. Mozak sam prepozna tip i smesti na pravo mesto. "
               "Iz galerije možeš odabrati i **više slika odjednom** (5+).")

    src = st.radio("Izvor slike",
                   ["📁 Otpremi iz galerije (više slika)", "📸 Kamera"], horizontal=True)
    if src.startswith("📸"):
        one = st.camera_input("Slikaj")
        images = [one] if one else []
    else:
        images = st.file_uploader(
            "Otpremi slike — možeš izabrati 5 i više odjednom",
            type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True) or []

    if not api_ready:
        st.warning("Unesi ANTHROPIC_API_KEY u ⚙️ (bočna traka) — Claude čita sliku i radi procenu.")
        return
    if not images:
        st.caption("Izaberi jednu ili više slika iz galerije (ili prebaci na Kamera).")
        return

    st.caption(f"Spremno za analizu: **{len(images)}** slika. "
               f"{'Google Vision pomaže pri čitanju teksta.' if google_ready else ''}")
    if st.button(f"🔍 Analiziraj ({len(images)})", type="primary", use_container_width=True):
        ok = 0
        for i, img_file in enumerate(images, 1):
            if len(images) > 1:
                st.markdown(f"#### 🖼️ Slika {i} / {len(images)}")
            try:
                b64 = image_to_b64(img_file)
                if not b64:
                    st.error("Ne mogu da pročitam sliku.")
                    continue
                # Google OCR je samo pomoćni nagoveštaj — Claude čita samu sliku
                ocr_text = ""
                if google_ready:
                    try:
                        ocr_text = google_ocr(b64, google_key)
                    except Exception:  # noqa: BLE001
                        ocr_text = ""
                    if ocr_text:
                        with st.expander("📝 Pomoćni OCR tekst (Google Vision)"):
                            st.text(ocr_text)
                with st.spinner(f"Mozak čita sliku i obrađuje… (slika {i})"):
                    res = smart_analyze(b64, ocr_text, model_id, api_key)
                if _handle_scan_result(res):
                    ok += 1
            except Exception as e:  # noqa: BLE001
                st.error(f"Greška na slici {i}: {e}")
            if len(images) > 1:
                st.divider()
        st.success(f"✅ Obrađeno {ok} / {len(images)} slika.")
        if st.session_state.pop("go_to_entry", False):
            st.session_state["view"] = "Unos podataka"
            st.rerun()


def _transfer_vitals_to_entry(vit: dict):
    """Prebacuje pročitana merenja (pritisak/puls...) u formu „Unos podataka"
    na proveru i čuvanje — umesto tihog upisa u bazu."""
    rdt = _parse_reading_dt(vit)
    vals = {
        "ev_sys": _iv(vit, "blood_pressure_sys"),
        "ev_dia": _iv(vit, "blood_pressure_dia"),
        "ev_hr": _iv(vit, "heart_rate"),
        "ev_stress": _iv(vit, "stress_level"),
        "ev_sleep": _iv(vit, "sleep_duration"),
        "ev_deep": _iv(vit, "deep_sleep_duration"),
        "ev_rem": _iv(vit, "rem_sleep_duration"),
        "ev_restless": _iv(vit, "restless_count"),
    }
    for k, val in vals.items():
        st.session_state[k] = val
    st.session_state["ev_date"] = rdt.date()
    st.session_state["ev_time"] = rdt.time().replace(second=0, microsecond=0)
    st.session_state["prefill_vitals_active"] = True
    st.session_state["go_to_entry"] = True

    dev = vit.get("device_name") or vit.get("device_type") or "uređaj"
    parts = []
    if vals["ev_sys"] and vals["ev_dia"]:
        parts.append(f"Pritisak <b>{vals['ev_sys']}/{vals['ev_dia']}</b> mmHg")
    if vals["ev_hr"]:
        parts.append(f"Puls <b>{vals['ev_hr']}</b> bpm")
    st.markdown(
        f"<div class='mt-card'><b>📟 {dev}</b> · "
        f"<span class='mt-muted'>{rdt:%Y-%m-%d %H:%M}</span><br>"
        f"{' · '.join(parts) or 'nije pročitano nijedno merenje'}</div>",
        unsafe_allow_html=True)
    st.info("➡️ Vrednosti su prebačene u „Unos podataka“ — proveri i klikni Sačuvaj.")


def _store_and_show_doc(doc: dict):
    """Prikaz medicinskog dokumenta + auto-čuvanje prepoznatih lab nalaza."""
    st.markdown(f"#### 📄 {str(doc.get('document_type', 'Dokument')).title()}")
    if doc.get("summary"):
        st.caption(doc["summary"])
    with st.expander("Pročitani tekst"):
        st.write(doc.get("full_text", "—"))
    labs = doc.get("lab_results") or []
    saved = 0
    if labs:
        for l in labs:
            try:
                q_exec(
                    """INSERT INTO lab_results (parameter_name,value,unit,
                       reference_range,test_date) VALUES (?,?,?,?,?)""",
                    (l.get("parameter_name", "?"), float(l.get("value", 0)),
                     l.get("unit", ""), l.get("reference_range", ""),
                     date.today().isoformat()))
                saved += 1
            except (ValueError, TypeError):
                continue
        st.markdown("##### 🧪 Sačuvani laboratorijski parametri")
        st.dataframe(labs, use_container_width=True)
    dxs = doc.get("diagnoses") or []
    if dxs:
        st.markdown("##### 🩺 Prepoznate dijagnoze")
        for d in dxs:
            st.markdown(f"- {d}")
        st.caption("Dijagnoze nisu automatski sačuvane — dodaj ih na „✍️ Unos podataka“ ako želiš.")
    st.success(f"Sačuvano {saved} lab parametara u karton." if saved
               else "Dokument obrađen i prikazan.")


def _show_food_result(res: dict):
    status = (res.get("status") or "YELLOW").upper()
    col = VERDICT.get(status, VERDICT["YELLOW"])
    label = {"GREEN": "✅ BEZBEDNO", "YELLOW": "⚠️ UMERENO", "RED": "⛔ IZBEGAVATI"}.get(status, status)
    st.markdown(
        f"<div class='mt-card' style='border-color:{col}'>"
        f"<span class='mt-pill' style='background:{col}1A;color:{col}'>{label}</span>"
        f"<h3 style='margin:.5rem 0'>{res.get('product_name','Proizvod')}</h3>"
        f"<p>{res.get('verdict_message','')}</p>"
        f"<p class='mt-muted'><b>Savet:</b> {res.get('actionable_advice','')}</p></div>",
        unsafe_allow_html=True)
    with st.expander("Pročitani sastojci / nutritivne vrednosti"):
        st.write(res.get("ingredients_text", "—"))

    # Upis u dnevnik skeniranja
    q_exec(
        """INSERT INTO scanned_products_log (product_name,ingredients_text,
           ai_verdict,analysis_reason,timestamp) VALUES (?,?,?,?,?)""",
        (res.get("product_name", "Proizvod"), res.get("ingredients_text"),
         status, res.get("verdict_message"), datetime.now().isoformat()),
    )
    st.success("Rezultat sačuvan u istoriju skeniranja.")


def _iv(res: dict, key: str) -> int:
    """Bezbedno čita ceo broj iz AI nalaza (null/tekst → 0)."""
    try:
        v = res.get(key)
        return int(float(v)) if v not in (None, "") else 0
    except (ValueError, TypeError):
        return 0


def _parse_reading_dt(res: dict) -> datetime:
    """Spaja pročitani datum/vreme sa ekrana u datetime (fallback: sada)."""
    now = datetime.now()
    d, t = now.date(), now.time().replace(second=0, microsecond=0)
    rd = res.get("reading_date")
    if rd:
        try:
            d = datetime.strptime(rd, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass
    rt = res.get("reading_time")
    if rt:
        for fmt in ("%H:%M", "%H.%M"):
            try:
                t = datetime.strptime(rt.strip(), fmt).time()
                break
            except (ValueError, TypeError):
                continue
    return datetime.combine(d, t)


# =========================================================================== #
#  VIEW: PROFIL (lični podaci — obavezni okvir za sve AI procene)
# =========================================================================== #
def render_profile():
    st.markdown("## 👤 Moj profil")
    st.caption("Ovi podaci su **obavezan okvir za sve AI procene** — svaki savet, doza, "
               "norma i pretraga računaju se baš za tebe (godine, telesna masa, BMI, pol).")
    p = get_profile()
    sex_opts = ["—", "muški", "ženski"]

    with st.form("profile_form"):
        c1, c2 = st.columns(2)
        age = c1.number_input("Godine starosti", 0, 120,
                              int(p["age"]) if p and p["age"] else 30)
        sex = c2.selectbox("Pol", sex_opts,
                           index=sex_opts.index(p["sex"]) if p and p["sex"] in sex_opts else 0)
        c3, c4 = st.columns(2)
        height = c3.number_input("Visina (cm)", 0, 250,
                                 int(p["height_cm"]) if p and p["height_cm"] else 175)
        weight = c4.number_input("Težina (kg)", 0.0, 400.0,
                                 float(p["weight_kg"]) if p and p["weight_kg"] else 80.0,
                                 step=0.5, format="%.1f")
        if st.form_submit_button("💾 Sačuvaj profil", type="primary"):
            save_profile(age or None, height or None, weight or None,
                         None if sex == "—" else sex)
            st.success("Profil sačuvan — AI ga od sada uzima u obzir u svemu.")
            st.rerun()

    # BMI kartica iz sačuvanog profila
    p = get_profile()
    if p and p["height_cm"] and p["weight_kg"]:
        bmi, cat = compute_bmi(p["height_cm"], p["weight_kg"])
        col = (VERDICT["GREEN"] if cat == "normalna težina"
               else VERDICT["RED"] if cat == "gojaznost" else VERDICT["YELLOW"])
        st.markdown(
            f"<div class='mt-card'><div class='mt-muted'>INDEKS TELESNE MASE (BMI)</div>"
            f"<div style='font-size:2rem;font-weight:900;color:{col}'>{bmi} "
            f"<span style='font-size:1rem;color:{col}'>· {cat}</span></div></div>",
            unsafe_allow_html=True)
    else:
        st.info("Popuni i sačuvaj profil — bez ovih podataka AI daje generičke procene.")


# =========================================================================== #
#  VIEW: UNOS PODATAKA
# =========================================================================== #
_EV_DEFAULTS = {"ev_hr": 60, "ev_stress": 30, "ev_restless": 0, "ev_sleep": 420,
                "ev_deep": 90, "ev_rem": 110, "ev_sys": 120, "ev_dia": 80}


def render_entry():
    # Posle čuvanja: očisti polja i prefill oznaku (pre kreiranja widgeta)
    if st.session_state.pop("_entry_saved", False):
        for k in list(_EV_DEFAULTS) + ["ev_date", "ev_time", "prefill_vitals_active"]:
            st.session_state.pop(k, None)
    # Pre-seed default vrednosti polja (prefill iz kamere ih je već možda postavio)
    for k, dv in _EV_DEFAULTS.items():
        st.session_state.setdefault(k, dv)
    st.session_state.setdefault("ev_date", date.today())
    st.session_state.setdefault("ev_time",
                                datetime.now().time().replace(second=0, microsecond=0))

    st.markdown("## ✍️ Unos podataka")
    t1, t2, t3 = st.tabs(["🫀 Vitalni znaci", "🩺 Dijagnoza", "🧪 Lab nalaz"])

    with t1:
        if st.session_state.get("prefill_vitals_active"):
            st.info("📷 Vrednosti su prebačene sa slike merača — proveri i klikni Sačuvaj.")
        with st.form("vitals"):
            cd, ct = st.columns(2)
            mdate = cd.date_input("Datum merenja", key="ev_date")
            mtime = ct.time_input("Vreme merenja", key="ev_time")
            c7, c8, c3 = st.columns(3)
            sys = c7.number_input("Pritisak — sistolni (gornji)", 0, 300, key="ev_sys")
            dia = c8.number_input("Pritisak — dijastolni (donji)", 0, 200, key="ev_dia")
            hr = c3.number_input("Puls (bpm)", 0, 250, key="ev_hr")
            c2, c4, c5 = st.columns(3)
            stress = c2.number_input("Stres (0-100)", 0, 100, key="ev_stress")
            sleep = c4.number_input("San (min)", 0, 1000, key="ev_sleep")
            deep = c5.number_input("Duboki san (min)", 0, 600, key="ev_deep")
            c6, c9 = st.columns(2)
            rem = c6.number_input("REM (min)", 0, 600, key="ev_rem")
            restless = c9.number_input("Nemiran san (x)", 0, 50, key="ev_restless")
            if st.form_submit_button("💾 Sačuvaj vitalne znake", type="primary"):
                n = lambda x: x if x else None  # 0 = nije izmereno → NULL  # noqa: E731
                ts = datetime.combine(mdate, mtime).isoformat()
                q_exec(
                    """INSERT INTO user_vitals (heart_rate,sleep_duration,
                       deep_sleep_duration,rem_sleep_duration,restless_count,
                       stress_level,blood_pressure_sys,blood_pressure_dia,timestamp)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (n(hr), n(sleep), n(deep), n(rem), n(restless), n(stress),
                     n(sys), n(dia), ts))
                st.session_state["_entry_saved"] = True
                st.success("Vitalni znaci sačuvani — Dashboard ažuriran.")
                st.rerun()

    with t2:
        st.markdown("##### 📎 Otpremi lekarski izveštaj — AI izvuče dijagnoze")
        dx_imgs = st.file_uploader(
            "Otpremi izveštaj/otpusnu listu (slika, može više)",
            type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True,
            key="dx_upload")
        if dx_imgs:
            if not api_ready:
                st.warning("Unesi ANTHROPIC_API_KEY u ⚙️ (bočna traka) da bi AI pročitao izveštaj.")
            elif st.button("🔍 Pročitaj i sačuvaj dijagnoze", type="primary", key="dx_analyze"):
                for i, img in enumerate(dx_imgs, 1):
                    if len(dx_imgs) > 1:
                        st.markdown(f"**🖼️ Slika {i} / {len(dx_imgs)}**")
                    try:
                        b64 = image_to_b64(img)
                        if not b64:
                            st.error("Ne mogu da pročitam sliku.")
                            continue
                        ocr_text = ""
                        if google_ready:
                            try:
                                ocr_text = google_ocr(b64, google_key)
                            except Exception:  # noqa: BLE001
                                ocr_text = ""
                        with st.spinner(f"AI čita izveštaj… (slika {i})"):
                            res = smart_analyze(b64, ocr_text, model_id, api_key)
                        doc = res.get("document") or {}
                        dxs = [str(d).strip() for d in (doc.get("diagnoses") or []) if str(d).strip()]
                        if dxs:
                            report = (doc.get("full_text") or doc.get("summary") or "")[:2000]
                            for d in dxs:
                                q_exec(
                                    """INSERT INTO medical_history (diagnosis_name,
                                       doctor_report_text,date_diagnosed,status)
                                       VALUES (?,?,?,?)""",
                                    (d, report, date.today().isoformat(), "active"))
                            st.success("Sačuvane dijagnoze: " + ", ".join(dxs))
                            if doc.get("summary"):
                                st.caption(doc["summary"])
                        else:
                            st.warning(f"Slika {i}: nisam prepoznao dijagnozu. "
                                       f"{res.get('notes') or ''} Probaj jasniju sliku.")
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Greška na slici {i}: {e}")

        st.divider()
        st.markdown("##### ✍️ Ručni unos")
        with st.form("dx"):
            name = st.text_input("Naziv dijagnoze")
            report = st.text_area("Izveštaj lekara (opciono)")
            c1, c2 = st.columns(2)
            ddate = c1.date_input("Datum dijagnoze", date.today())
            status = c2.selectbox("Status", ["active", "monitoring", "resolved"])
            if st.form_submit_button("Sačuvaj dijagnozu", type="primary"):
                if name.strip():
                    q_exec(
                        """INSERT INTO medical_history (diagnosis_name,
                           doctor_report_text,date_diagnosed,status)
                           VALUES (?,?,?,?)""",
                        (name.strip(), report.strip(), ddate.isoformat(), status))
                    st.success("Dijagnoza sačuvana.")
                else:
                    st.warning("Unesi naziv dijagnoze.")

    with t3:
        st.markdown("##### 📎 Otpremi sliku nalaza — AI pročita i upiše parametre")
        lab_imgs = st.file_uploader(
            "Otpremi laboratorijski nalaz (slika, može više)",
            type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True,
            key="lab_upload")
        if lab_imgs:
            if not api_ready:
                st.warning("Unesi ANTHROPIC_API_KEY u ⚙️ (bočna traka) da bi AI pročitao nalaz.")
            elif st.button("🔍 Pročitaj i sačuvaj nalaze", type="primary", key="lab_analyze"):
                for i, img in enumerate(lab_imgs, 1):
                    if len(lab_imgs) > 1:
                        st.markdown(f"**🖼️ Slika {i} / {len(lab_imgs)}**")
                    try:
                        b64 = image_to_b64(img)
                        if not b64:
                            st.error("Ne mogu da pročitam sliku.")
                            continue
                        ocr_text = ""
                        if google_ready:
                            try:
                                ocr_text = google_ocr(b64, google_key)
                            except Exception:  # noqa: BLE001
                                ocr_text = ""
                        with st.spinner(f"AI čita nalaz… (slika {i})"):
                            res = smart_analyze(b64, ocr_text, model_id, api_key)
                        doc = res.get("document")
                        if doc and doc.get("lab_results"):
                            _store_and_show_doc(doc)
                        else:
                            st.warning(f"Slika {i}: nisam prepoznao lab parametre. "
                                       f"{res.get('notes') or ''} Probaj jasniju sliku.")
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Greška na slici {i}: {e}")

        st.divider()
        st.markdown("##### ✍️ Ručni unos")
        with st.form("lab"):
            c1, c2 = st.columns(2)
            pname = c1.text_input("Parametar (npr. Glukoza)")
            val = c2.number_input("Vrednost", value=0.0, format="%.2f")
            c3, c4, c5 = st.columns(3)
            unit = c3.text_input("Jedinica", "mmol/L")
            ref = c4.text_input("Referentni opseg", "")
            tdate = c5.date_input("Datum nalaza", date.today())
            if st.form_submit_button("Sačuvaj nalaz", type="primary"):
                if pname.strip():
                    q_exec(
                        """INSERT INTO lab_results (parameter_name,value,unit,
                           reference_range,test_date) VALUES (?,?,?,?,?)""",
                        (pname.strip(), val, unit, ref, tdate.isoformat()))
                    st.success("Laboratorijski nalaz sačuvan.")
                else:
                    st.warning("Unesi naziv parametra.")


# =========================================================================== #
#  VIEW: ISTORIJA
# =========================================================================== #
def render_history():
    st.markdown("## 🗂️ Istorija skeniranja")
    scans = recent_scans(50)
    if not scans:
        st.info("Još nema skeniranih proizvoda.")
    for s in scans:
        col = VERDICT.get(s["ai_verdict"], VERDICT["YELLOW"])
        ts = s["timestamp"][:16].replace("T", " ")
        st.markdown(
            f"<div class='mt-card' style='border-left:4px solid {col}'>"
            f"<span class='mt-muted'>{ts}</span><br>"
            f"<b>{s['product_name']}</b> — "
            f"<span style='color:{col};font-weight:700'>{s['ai_verdict']}</span><br>"
            f"<span class='mt-muted'>{s['analysis_reason'] or ''}</span></div>",
            unsafe_allow_html=True)

    st.divider()
    st.markdown("### 🧪 Svi laboratorijski nalazi")
    labs = q_all("SELECT parameter_name,value,unit,reference_range,test_date "
                 "FROM lab_results ORDER BY test_date DESC, parameter_name")
    if labs:
        st.dataframe([dict(r) for r in labs], use_container_width=True)
    else:
        st.caption("Nema unetih nalaza.")


# =========================================================================== #
#  VIEW: TRENDOVI (hronološko praćenje na zahtev)
# =========================================================================== #
def render_trends():
    st.markdown("## 📈 Trendovi")
    st.caption("Praćenje kretanja kroz vreme — biraš period, grafik se prikazuje na zahtev.")

    PERIODS = {"📅 1 dan": 1, "🗓️ 7 dana": 7, "📆 30 dana": 30}
    MIN_POINTS = {1: 2, 7: 3, 30: 4}  # minimum merenja da grafik ima smisla
    choice = st.radio("Period", list(PERIODS.keys()), horizontal=True, index=1)
    days = PERIODS[choice]

    rows = vitals_series(days)
    need = MIN_POINTS[days]
    if len(rows) < need:
        st.info(
            f"📊 Potrebno je bar **{need}** merenja u poslednjih **{days}** dana "
            f"da bi grafik imao smisla (uneseno: {len(rows)}). "
            f"Nastavi da unosiš/skeniraš merenja — grafik se otključava automatski."
        )
        return

    pal = PALETTES[st.session_state["theme"]]
    df = pd.DataFrame([dict(r) for r in rows])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()

    # --- Krvni pritisak ---
    bp = df[["blood_pressure_sys", "blood_pressure_dia"]].dropna(how="all")
    if not bp.empty:
        st.markdown("#### 🩸 Krvni pritisak")
        last = bp.iloc[-1]
        m1, m2, m3 = st.columns(3)
        m1.metric("Poslednji SYS", int(last["blood_pressure_sys"]),
                  delta=_delta(bp["blood_pressure_sys"]))
        m2.metric("Poslednji DIA", int(last["blood_pressure_dia"]),
                  delta=_delta(bp["blood_pressure_dia"]))
        m3.metric("Prosek SYS", f"{bp['blood_pressure_sys'].mean():.0f}")
        chart = bp.rename(columns={"blood_pressure_sys": "Sistolni (SYS)",
                                   "blood_pressure_dia": "Dijastolni (DIA)"})
        st.line_chart(chart, color=[pal["primary"], pal["amber"]])

    # --- Puls ---
    hr = df[["heart_rate"]].dropna()
    if not hr.empty:
        st.markdown("#### 💓 Puls (bpm)")
        st.line_chart(hr.rename(columns={"heart_rate": "Puls"}),
                      color=[pal["copper"]])

    # --- Stres ---
    stress = df[["stress_level"]].dropna()
    if not stress.empty:
        st.markdown("#### 🧠 Nivo stresa (0-100)")
        st.line_chart(stress.rename(columns={"stress_level": "Stres"}),
                      color=[pal["amber"]])

    # --- San ---
    sleep = df[["sleep_duration", "deep_sleep_duration", "rem_sleep_duration"]].dropna(how="all")
    if not sleep.empty and sleep.notna().sum().sum() >= need:
        st.markdown("#### 😴 San (minuti)")
        st.bar_chart(sleep.rename(columns={
            "sleep_duration": "Ukupno", "deep_sleep_duration": "Duboki",
            "rem_sleep_duration": "REM"}))

    st.caption(f"Prikazano {len(rows)} merenja iz poslednjih {days} dana "
               f"(hronološki). Više podataka → preciznije korelacije.")


def _delta(series) -> str | None:
    """Promena u odnosu na prethodno merenje (za st.metric strelicu)."""
    s = series.dropna()
    if len(s) < 2:
        return None
    diff = s.iloc[-1] - s.iloc[-2]
    return f"{diff:+.0f}"


# --------------------------------------------------------------------------- #
#  Render
# --------------------------------------------------------------------------- #
if view == "Dashboard":
    render_dashboard()
elif view == "Smart Camera":
    render_camera()
elif view == "Trendovi":
    render_trends()
elif view == "Profil":
    render_profile()
elif view == "Unos podataka":
    render_entry()
else:
    render_history()
