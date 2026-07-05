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
import hashlib
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
        f"""CREATE TABLE IF NOT EXISTS medications (
            id {pk}, medication_name TEXT NOT NULL, active_substance TEXT,
            dosage_text TEXT, form TEXT, purpose TEXT,
            status TEXT NOT NULL DEFAULT 'active', source TEXT, added_at TEXT NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY, age INTEGER, height_cm INTEGER,
            weight_kg {real}, sex TEXT, updated_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS daily_reports (
            id INTEGER PRIMARY KEY, report_date TEXT, generated_at TEXT,
            signature TEXT, content TEXT)""",
        """CREATE TABLE IF NOT EXISTS consortium_reports (
            id INTEGER PRIMARY KEY, generated_at TEXT, signature TEXT, content TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS ckg_edges (
            id {pk}, src TEXT, rel TEXT, dst TEXT)""",
        """CREATE TABLE IF NOT EXISTS supplement_plans (
            id INTEGER PRIMARY KEY, generated_at TEXT, signature TEXT, content TEXT)""",
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


def active_medications():
    return q_all(
        "SELECT * FROM medications WHERE status='active' ORDER BY added_at DESC"
    )


def add_medication(name, substance, dosage, form, purpose, source="scan") -> None:
    q_exec(
        """INSERT INTO medications (medication_name, active_substance, dosage_text,
           form, purpose, status, source, added_at) VALUES (?,?,?,?,?,'active',?,?)""",
        (name, substance, dosage, form, purpose, source, datetime.now().isoformat()),
    )


def stop_medication(med_id: int) -> None:
    q_exec("UPDATE medications SET status='stopped' WHERE id = ?", (med_id,))


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


def data_signature() -> str:
    """Potpis stanja svih unosa — menja se čim se doda novi unos/dokument/profil."""
    v = q_one("SELECT COUNT(*) c, MAX(timestamp) m FROM user_vitals")
    l = q_one("SELECT COUNT(*) c, MAX(test_date) m FROM lab_results")
    d = q_one("SELECT COUNT(*) c, MAX(date_diagnosed) m FROM medical_history")
    s = q_one("SELECT COUNT(*) c, MAX(timestamp) m FROM scanned_products_log")
    m = q_one("SELECT COUNT(*) c, MAX(added_at) m FROM medications WHERE status='active'")
    p = get_profile()
    raw = (f"{v['c']}|{v['m']}|{l['c']}|{l['m']}|{d['c']}|{d['m']}|"
           f"{s['c']}|{s['m']}|{m['c']}|{m['m']}|{p['updated_at'] if p else '-'}")
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_daily_report():
    return q_one("SELECT * FROM daily_reports WHERE id = 1")


def save_daily_report(report_date: str, signature: str, content: str) -> None:
    q_exec(
        """INSERT INTO daily_reports (id, report_date, generated_at, signature, content)
           VALUES (1, ?, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET report_date=excluded.report_date,
             generated_at=excluded.generated_at, signature=excluded.signature,
             content=excluded.content""",
        (report_date, datetime.now().isoformat(), signature, content),
    )


# =========================================================================== #
#  SLOJ 2: NORMALIZACIJA (LOINC) + VREMENSKA LINIJA + TREND VELOCITY
# =========================================================================== #
# Interni terminološki rečnik: kanonski naziv → LOINC kod, kanonska jedinica,
# sinonimi (srpski/nemački/engleski), smer pogoršanja i konverzije jedinica.
LOINC_MAP = {
    "Kalijum":     {"loinc": "2823-3",  "unit": "mmol/L", "bad": "up",
                    "syn": ["kalijum", "kalium", "potassium", "k+", "k "],
                    "ref": (3.5, 5.1)},
    "Natrijum":    {"loinc": "2951-2",  "unit": "mmol/L", "bad": "both",
                    "syn": ["natrijum", "natrium", "sodium", "na+"], "ref": (136, 145)},
    "Hlorid":      {"loinc": "2075-0",  "unit": "mmol/L", "bad": "both",
                    "syn": ["hlorid", "chlorid", "chloride", "cl-"], "ref": (98, 107)},
    "Kalcijum":    {"loinc": "17861-6", "unit": "mmol/L", "bad": "both",
                    "syn": ["kalcijum", "calcium", "kalzium", "ca "], "ref": (2.1, 2.6)},
    "Magnezijum":  {"loinc": "19123-9", "unit": "mmol/L", "bad": "both",
                    "syn": ["magnezijum", "magnesium", "mg "], "ref": (0.66, 1.07)},
    "Kreatinin":   {"loinc": "2160-0",  "unit": "mg/dL",  "bad": "up",
                    "syn": ["kreatinin", "creatinine", "krea"], "ref": (0.7, 1.3),
                    "convert": {"µmol/l": 1 / 88.42, "umol/l": 1 / 88.42}},
    "eGFR":        {"loinc": "62238-1", "unit": "ml/min", "bad": "down",
                    "syn": ["egfr", "gfr", "glomerularna filtracija", "ckd-epi"],
                    "ref": (90, 200)},
    "Urea":        {"loinc": "22664-7", "unit": "mg/dL",  "bad": "up",
                    "syn": ["urea", "harnstoff", "bun", "azot ureje"], "ref": (17, 43)},
    "Mokraćna kiselina": {"loinc": "3084-1", "unit": "mg/dL", "bad": "up",
                    "syn": ["mokraćna kiselina", "mokracna", "harnsäure", "harnsaure",
                            "uric acid", "urat"], "ref": (3.5, 7.2)},
    "Glukoza":     {"loinc": "2345-7",  "unit": "mmol/L", "bad": "up",
                    "syn": ["glukoza", "glucose", "glukose", "šećer", "secer"],
                    "ref": (3.9, 5.5), "convert": {"mg/dl": 1 / 18.016}},
    "HbA1c":       {"loinc": "4548-4",  "unit": "%",      "bad": "up",
                    "syn": ["hba1c", "glikozilirani"], "ref": (4.0, 5.7)},
    "TSH":         {"loinc": "3016-3",  "unit": "mU/L",   "bad": "both",
                    "syn": ["tsh", "tireostimulišući"], "ref": (0.35, 4.94)},
    "Holesterol":  {"loinc": "2093-3",  "unit": "mmol/L", "bad": "up",
                    "syn": ["holesterol", "cholesterin", "cholesterol ukupni",
                            "cholesterol"], "ref": (0, 5.2), "convert": {"mg/dl": 1 / 38.67}},
    "LDL":         {"loinc": "13457-7", "unit": "mmol/L", "bad": "up",
                    "syn": ["ldl"], "ref": (0, 3.0), "convert": {"mg/dl": 1 / 38.67}},
    "HDL":         {"loinc": "2085-9",  "unit": "mmol/L", "bad": "down",
                    "syn": ["hdl"], "ref": (1.0, 3.0), "convert": {"mg/dl": 1 / 38.67}},
    "Trigliceridi": {"loinc": "2571-8", "unit": "mmol/L", "bad": "up",
                    "syn": ["triglicerid", "triglyzerid", "triglyceride"],
                    "ref": (0, 1.7), "convert": {"mg/dl": 1 / 88.57}},
    "AST":         {"loinc": "1920-8",  "unit": "U/L",    "bad": "up",
                    "syn": ["ast", "got", "aspartat"], "ref": (0, 40)},
    "ALT":         {"loinc": "1742-6",  "unit": "U/L",    "bad": "up",
                    "syn": ["alt", "gpt", "alanin"], "ref": (0, 41)},
    "Gama-GT":     {"loinc": "2324-2",  "unit": "U/L",    "bad": "up",
                    "syn": ["gama-gt", "gamma-gt", "ggt", "gama gt"], "ref": (0, 60)},
    "Bilirubin":   {"loinc": "1975-2",  "unit": "mg/dL",  "bad": "up",
                    "syn": ["bilirubin"], "ref": (0, 1.2)},
    "ALP":         {"loinc": "6768-6",  "unit": "U/L",    "bad": "up",
                    "syn": ["alkalna fosfataza", "alkalische phosphatase", "alp"],
                    "ref": (40, 130)},
    "Gvožđe":      {"loinc": "2498-4",  "unit": "µmol/L", "bad": "down",
                    "syn": ["gvožđe", "gvozdje", "eisen", "iron", "fe "], "ref": (11, 28),
                    "convert": {"µg/dl": 0.179, "ug/dl": 0.179}},
    "Feritin":     {"loinc": "2276-4",  "unit": "µg/L",   "bad": "down",
                    "syn": ["feritin", "ferritin"], "ref": (30, 400)},
    "Vitamin B12": {"loinc": "2132-9",  "unit": "ng/L",   "bad": "down",
                    "syn": ["b12", "kobalamin", "cobalamin"], "ref": (197, 771)},
    "Folna kiselina": {"loinc": "2284-8", "unit": "µg/L", "bad": "down",
                    "syn": ["folna", "folsäure", "folsaure", "folat", "folate"],
                    "ref": (3.9, 26.8)},
    "Vitamin D":   {"loinc": "1989-3",  "unit": "nmol/L", "bad": "down",
                    "syn": ["vitamin d", "25-oh", "25(oh)d", "vitamin d3",
                            "kalciferol", "cholecalciferol"], "ref": (75, 250),
                    "convert": {"ng/ml": 2.496, "µg/l": 2.496, "ug/l": 2.496}},
    "Hemoglobin":  {"loinc": "718-7",   "unit": "g/L",    "bad": "down",
                    "syn": ["hemoglobin", "hämoglobin", "hgb", "hb "], "ref": (130, 175),
                    "convert": {"g/dl": 10.0}},
    "Leukociti":   {"loinc": "6690-2",  "unit": "10^9/L", "bad": "both",
                    "syn": ["leukociti", "leukozyten", "wbc", "leukocit"], "ref": (4, 10)},
    "Trombociti":  {"loinc": "777-3",   "unit": "10^9/L", "bad": "both",
                    "syn": ["trombociti", "thrombozyten", "plt", "platelet"],
                    "ref": (150, 400)},
    "CRP":         {"loinc": "1988-5",  "unit": "mg/L",   "bad": "up",
                    "syn": ["crp", "c-reaktivni"], "ref": (0, 5)},
    "PSA":         {"loinc": "2857-1",  "unit": "ng/mL",  "bad": "up",
                    "syn": ["psa", "prostata specifični"], "ref": (0, 3.1)},
    "CPK":         {"loinc": "2157-6",  "unit": "U/L",    "bad": "up",
                    "syn": ["cpk", "ck ", "kreatin kinaza", "creatine kinase"],
                    "ref": (0, 190)},
    "LDH":         {"loinc": "14804-9", "unit": "U/L",    "bad": "up",
                    "syn": ["ldh", "laktat dehidrogenaza"], "ref": (0, 250)},
    "Eritrociti u urinu": {"loinc": "13945-1", "unit": "/µL", "bad": "up",
                    "syn": ["eritrociti u urinu", "ery/", "erythrozyten im urin",
                            "hematurija"], "ref": (0, 25)},
    "Leukociti u urinu": {"loinc": "30405-5", "unit": "/µL", "bad": "up",
                    "syn": ["leukociti u urinu", "leu/", "leukozyten im urin"],
                    "ref": (0, 25)},
    "Protein u urinu": {"loinc": "2888-6", "unit": "mg/dL", "bad": "up",
                    "syn": ["protein u urinu", "proteinurija", "eiweiß im urin"],
                    "ref": (0, 15)},
}


def normalize_param(name: str) -> str:
    """Mapira naziv parametra (sr/de/en varijante) na kanonski naziv iz LOINC_MAP."""
    n = (name or "").strip().lower()
    if not n:
        return name
    for canon, meta in LOINC_MAP.items():
        if canon.lower() == n:
            return canon
        for s in meta["syn"]:
            if s in n or n in s:
                return canon
    return name.strip()


def canon_value(canon: str, value: float, unit: str) -> tuple[float, str]:
    """Konvertuje vrednost u kanonsku jedinicu (npr. mg/dL → mmol/L) ako je mapa zna."""
    meta = LOINC_MAP.get(canon)
    if not meta:
        return value, unit
    u = (unit or "").strip().lower().replace(" ", "")
    cu = meta["unit"].lower().replace(" ", "")
    if u == cu or not u:
        return value, meta["unit"]
    for src, factor in (meta.get("convert") or {}).items():
        if u == src.replace(" ", ""):
            return round(value * factor, 3), meta["unit"]
    return value, unit  # nepoznata jedinica — ostavi kako jeste


def lab_timeline() -> dict:
    """Svi lab nalazi poravnati na jednu hronološku osu, po kanonskom parametru:
    {canon: [(date, value, unit), ...] rastuće po datumu}."""
    rows = q_all("SELECT parameter_name, value, unit, test_date FROM lab_results "
                 "ORDER BY test_date ASC, id ASC")
    tl: dict[str, list] = {}
    for r in rows:
        canon = normalize_param(r["parameter_name"])
        try:
            val, unit = canon_value(canon, float(r["value"]), r["unit"])
        except (TypeError, ValueError):
            continue
        tl.setdefault(canon, []).append((r["test_date"], val, unit))
    return tl


def trend_velocity() -> list[dict]:
    """Vremenski ponderisana linearna regresija po parametru. Skoriji podaci nose
    veću težinu (w = exp(-starost/45d)). Flaguje nepovoljan vektor i unutar
    referentnog opsega (Trend Velocity umesto binarne provere prag-a)."""
    import math
    out = []
    for canon, pts in lab_timeline().items():
        if len(pts) < 2:
            continue
        meta = LOINC_MAP.get(canon, {})
        try:
            xs = [(datetime.fromisoformat(d[:10]) - datetime(2020, 1, 1)).days
                  for d, _, _ in pts]
        except ValueError:
            continue
        ys = [v for _, v, _ in pts]
        today_x = (datetime.now() - datetime(2020, 1, 1)).days
        ws = [math.exp(-(today_x - x) / 45.0) for x in xs]
        sw = sum(ws)
        if sw <= 0:
            continue
        mx = sum(w * x for w, x in zip(ws, xs)) / sw
        my = sum(w * y for w, y in zip(ws, ys)) / sw
        den = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
        if den == 0:
            continue
        slope_day = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys)) / den
        slope_month = slope_day * 30
        ref = meta.get("ref")
        span = (ref[1] - ref[0]) if ref and ref[1] > ref[0] else (abs(my) or 1)
        pct_month = 100 * slope_month / span
        bad = meta.get("bad")
        adverse = ((bad == "up" and pct_month > 4) or (bad == "down" and pct_month < -4)
                   or (bad == "both" and abs(pct_month) > 8))
        last_d, last_v, last_u = pts[-1]
        in_range = bool(ref and ref[0] <= last_v <= ref[1])
        out.append({
            "param": canon, "loinc": meta.get("loinc", "-"),
            "last_value": last_v, "unit": last_u, "last_date": last_d,
            "points": len(pts), "slope_month": round(slope_month, 3),
            "pct_of_range_per_month": round(pct_month, 1),
            "in_range": in_range, "adverse_trend": adverse,
        })
    return out


# =========================================================================== #
#  SLOJ 1: KRVNI PRITISAK 3x DNEVNO — obrasci i cross-talk okidači
# =========================================================================== #
def bp_period(ts: str) -> str:
    """Klasifikuje merenje: jutro (<11h), popodne (11-17h), veče (17h+)."""
    try:
        h = int(ts[11:13])
    except (ValueError, IndexError):
        return "nepoznato"
    return "jutro" if h < 11 else ("popodne" if h < 17 else "veče")


def bp_pattern_analysis(days: int = 7) -> dict:
    """Analiza obrazaca pritiska po dobu dana + cross-talk okidači agenata:
    jutarnji skok → Endokrinolog+Nefrolog; popodnevne fluktuacije → Hepatolog+
    Kardiolog; večernji non-dipping → kardiorenalni rizik (Kardiolog+Nefrolog)."""
    rows = [r for r in vitals_series(days) if r["blood_pressure_sys"]]
    per: dict[str, list] = {"jutro": [], "popodne": [], "veče": []}
    for r in rows:
        p = bp_period(r["timestamp"])
        if p in per:
            per[p].append((r["blood_pressure_sys"], r["blood_pressure_dia"] or 0))
    avg = {p: (round(sum(x[0] for x in v) / len(v)), round(sum(x[1] for x in v) / len(v)))
           if v else None for p, v in per.items()}
    triggers, patterns = set(), []
    j, po, ve = avg["jutro"], avg["popodne"], avg["veče"]
    if j and ((po and j[0] >= po[0] + 10) or j[0] >= 140):
        patterns.append(f"JUTARNJI SKOK: prosek jutro {j[0]}/{j[1]} mmHg")
        triggers.update(["Endokrinolog", "Nefrolog"])
    if po and j and abs(po[0] - j[0]) >= 15:
        patterns.append(f"POPODNEVNE FLUKTUACIJE: jutro {j[0]} vs popodne {po[0]} mmHg")
        triggers.update(["Hepatolog", "Kardiolog"])
    if ve and j and ve[0] >= j[0] - 2:
        patterns.append(f"NON-DIPPING (veče visoko): veče {ve[0]}/{ve[1]} vs jutro "
                        f"{j[0]}/{j[1]} mmHg — povišen kardiorenalni rizik")
        triggers.update(["Kardiolog", "Nefrolog"])
    if any(a and a[0] >= 140 for a in (j, po, ve)):
        triggers.add("Kardiolog")
    return {"averages": avg, "patterns": patterns, "triggers": sorted(triggers),
            "readings": len(rows), "days": days}


# =========================================================================== #
#  SLOJ 5: KLINIČKA TRIJAŽA — RED FLAGS (tvrde brave IZVAN LLM-a)
# =========================================================================== #
# Pragovi akutne opasnosti. Provera je čist Python — zaobilazi AI u potpunosti.
def check_red_flags() -> list[dict]:
    flags = []
    tl = lab_timeline()

    def last(canon):
        pts = tl.get(canon) or []
        return pts[-1] if pts else None

    k = last("Kalijum")
    if k and k[1] > 6.0:
        flags.append({"title": "KALIJUM > 6.0 mmol/L (hiperkalemija — rizik po srce)",
                      "value": f"{k[1]} mmol/L ({k[0]})"})
    na = last("Natrijum")
    if na and (na[1] < 125 or na[1] > 155):
        flags.append({"title": "NATRIJUM u opasnoj zoni",
                      "value": f"{na[1]} mmol/L ({na[0]})"})
    glu = last("Glukoza")
    if glu and (glu[1] > 16.7 or glu[1] < 3.0):
        flags.append({"title": "GLUKOZA u opasnoj zoni",
                      "value": f"{glu[1]} mmol/L ({glu[0]})"})
    hgb = last("Hemoglobin")
    if hgb and hgb[1] < 70:
        flags.append({"title": "TEŠKA ANEMIJA (Hemoglobin < 70 g/L)",
                      "value": f"{hgb[1]} g/L ({hgb[0]})"})
    ery_u = last("Eritrociti u urinu")
    if ery_u and ery_u[1] >= 500:
        flags.append({"title": "TEŠKA HEMATURIJA (krv u urinu)",
                      "value": f"{ery_u[1]} /µL ({ery_u[0]})"})

    # eGFR: apsolutno < 30 ILI drastičan pad (>25% u odnosu na prethodno merenje)
    gfr_pts = tl.get("eGFR") or []
    if gfr_pts:
        g_last = gfr_pts[-1]
        if g_last[1] < 30:
            flags.append({"title": "eGFR < 30 ml/min (teška bubrežna insuficijencija)",
                          "value": f"{g_last[1]} ml/min ({g_last[0]})"})
        elif len(gfr_pts) >= 2 and gfr_pts[-2][1] > 0:
            drop = 100 * (gfr_pts[-2][1] - g_last[1]) / gfr_pts[-2][1]
            if drop >= 25:
                flags.append({"title": f"DRASTIČAN PAD eGFR (−{drop:.0f}% između merenja)",
                              "value": f"{gfr_pts[-2][1]} → {g_last[1]} ml/min"})

    # Hipertenzivna kriza: 2+ uzastopna merenja ≥180 SYS ili ≥120 DIA u 48h
    recent = [r for r in vitals_series(2) if r["blood_pressure_sys"]]
    crisis = [r for r in recent if r["blood_pressure_sys"] >= 180
              or (r["blood_pressure_dia"] or 0) >= 120]
    if len(crisis) >= 2:
        last_c = crisis[-1]
        flags.append({"title": "HIPERTENZIVNA KRIZA (≥180/120, ponovljena merenja)",
                      "value": f"{last_c['blood_pressure_sys']}/"
                               f"{last_c['blood_pressure_dia']} mmHg"})
    return flags


def render_red_flag_screen(flags: list[dict]) -> None:
    """Dominantan kritičан ekran — blokira AI savete (tvrda brava izvan LLM-a)."""
    items = "".join(
        f"<li style='margin:6px 0'><b>{f['title']}</b><br>"
        f"<span style='opacity:.85'>Izmereno: {f['value']}</span></li>" for f in flags)
    st.markdown(f"""
    <div style='border:2px solid {VERDICT["RED"]};border-radius:22px;padding:26px;
         background:{VERDICT["RED"]}22;box-shadow:0 0 40px {VERDICT["RED"]}55'>
      <div style='font-size:1.6rem;font-weight:900;color:{VERDICT["RED"]}'>
        ⛔ KRITIČNO UPOZORENJE</div>
      <p style='margin:10px 0'>Detektovane su vrednosti u zoni akutne opasnosti.
      <b>Svi nutritivni i lifestyle saveti su obustavljeni.</b>
      Odmah se javi lekaru ili hitnoj službi (194).</p>
      <ul style='padding-left:20px'>{items}</ul>
      <p style='font-size:.8rem;opacity:.8'>Ova provera je ugrađena bezbednosna brava
      (nezavisna od AI). Saveti se automatski otključavaju kad nove izmerene vrednosti
      izađu iz kritične zone.</p>
    </div>""", unsafe_allow_html=True)


# =========================================================================== #
#  SLOJ 2.2: CLINICAL KNOWLEDGE GRAPH (CKG) — patofiziološke veze
# =========================================================================== #
# Ivice: (izvor, relacija, cilj). Relacije: INFLUENCES, EXACERBATES, INDICATES,
# CONTRAINDICATED_BY. Seed-uje se jednom; koristi se u konflikt-rezoluciji.
CKG_SEED = [
    ("Kalijum", "CONTRAINDICATED_BY", "Dijeta bogata kalijumom (kod hiperkalemije)"),
    ("Kalijum", "INFLUENCES", "Srčani ritam"),
    ("eGFR", "INDICATES", "Bubrežna funkcija"),
    ("Nizak eGFR", "EXACERBATES", "Hiperkalemija"),
    ("Nizak eGFR", "CONTRAINDICATED_BY", "Visok unos proteina"),
    ("Hipertenzija", "EXACERBATES", "Bubrežna insuficijencija"),
    ("Bubrežna insuficijencija", "EXACERBATES", "Hipertenzija"),
    ("Natrijum (unos)", "INFLUENCES", "Krvni pritisak"),
    ("Non-dipping pritisak", "INDICATES", "Kardiorenalni rizik"),
    ("Jutarnji BP skok", "INDICATES", "Hormonalna/bubrežna disregulacija"),
    ("TSH", "INFLUENCES", "Krvni pritisak"),
    ("TSH", "INFLUENCES", "Lipidni profil"),
    ("Glukoza", "EXACERBATES", "Bubrežna insuficijencija"),
    ("Trigliceridi", "INDICATES", "Metabolički sindrom"),
    ("Gama-GT", "INDICATES", "Jetrena/metabolička disfunkcija"),
    ("Mokraćna kiselina", "EXACERBATES", "Hipertenzija"),
    ("Mokraćna kiselina", "EXACERBATES", "Bubrežna insuficijencija"),
    ("Eritrociti u urinu", "INDICATES", "Urološka/nefrološka patologija"),
    ("Protein u urinu", "INDICATES", "Bubrežno oštećenje"),
    ("Gvožđe", "INFLUENCES", "Hemoglobin"),
    ("Feritin", "INDICATES", "Depoi gvožđa / inflamacija"),
    ("Nizak Hemoglobin", "EXACERBATES", "Kardiovaskularno opterećenje"),
    ("CRP", "INDICATES", "Inflamacija"),
    ("Kreatinin", "INDICATES", "Bubrežna funkcija"),
    ("Visok unos soli", "EXACERBATES", "Non-dipping pritisak"),
    # --- Suplement-specifične kontraindikacije (Faza: Suplementacija) ---
    ("Kalijum suplement", "CONTRAINDICATED_BY", "Hiperkalemija ili nizak eGFR"),
    ("Magnezijum suplement", "CONTRAINDICATED_BY", "Nizak eGFR (<60 ml/min)"),
    ("Kalcijum suplement", "CONTRAINDICATED_BY", "Hiperkalcijemija"),
    ("Gvožđe suplement", "CONTRAINDICATED_BY", "Normalan ili visok feritin"),
    ("Visoka doza Vitamina D", "CONTRAINDICATED_BY", "Hiperkalcijemija"),
    ("Vitamin K", "CONTRAINDICATED_BY", "Antikoagulantna terapija (varfarin)"),
    ("Omega-3 visoka doza", "CONTRAINDICATED_BY", "Antikoagulantna/antiagregaciona terapija"),
    ("Kalijum suplement", "CONTRAINDICATED_BY", "ACE inhibitor ili diuretik koji štedi kalijum"),
]


def seed_ckg() -> None:
    """Dodaje samo NEDOSTAJUĆE ivice (idempotentno) — sigurno za pozivanje i
    kad je tabela već delimično popunjena (npr. posle proširenja CKG_SEED)."""
    existing = {(r["src"], r["rel"], r["dst"]) for r in
                q_all("SELECT src, rel, dst FROM ckg_edges")}
    missing = [e for e in CKG_SEED if e not in existing]
    if missing:
        q_execmany("INSERT INTO ckg_edges (src, rel, dst) VALUES (?,?,?)", missing)


def ckg_context() -> str:
    """CKG ivice kao tekst — kontekst za konflikt-rezoluciju konzilijuma."""
    rows = q_all("SELECT src, rel, dst FROM ckg_edges")
    return "\n".join(f"- [{r['src']}] --{r['rel']}--> [{r['dst']}]" for r in rows)


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
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Robusno popravljanje neispravnog/odsečenog JSON-a iz LLM-a
        try:
            import json_repair
            fixed = json_repair.loads(raw)
            if isinstance(fixed, dict):
                return fixed
        except Exception:  # noqa: BLE001
            pass
        return _repair_json_fallback(raw)


def _repair_json_fallback(raw: str) -> dict:
    """Rezervno popravljanje bez zavisnosti: zatvara nedovršene stringove/zagrade
    (npr. kad se odgovor odsekao na max_tokens)."""
    s = raw
    # ako je string ostao otvoren, zatvori ga
    if s.count('"') % 2 == 1:
        s += '"'
    # ukloni eventualni trailing zarez
    s = s.rstrip().rstrip(",")
    # dopuni nedostajuće zatvarajuće ] i }
    opens = s.count("{") - s.count("}")
    brs = s.count("[") - s.count("]")
    s += "]" * max(0, brs)
    s += "}" * max(0, opens)
    return json.loads(s)


def build_health_context() -> str:
    """Skuplja kompletan zdravstveni kontekst korisnika za AI ukrštanje.
    Lični profil (godine/visina/težina/BMI) je OBAVEZAN okvir za svaki savet."""
    p = get_profile()
    v = latest_vitals()
    dx = active_diagnoses()
    labs = latest_labs()
    meds = active_medications()

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
    meds_list = (
        "; ".join(
            f"{m['medication_name']}"
            + (f" ({m['active_substance']})" if m["active_substance"] else "")
            + (f", {m['dosage_text']}" if m["dosage_text"] else "")
            for m in meds
        )
        or "nema unetih lekova"
    )
    return (
        f"LIČNI PROFIL (OBAVEZNO uzeti u obzir za SVAKI savet, dozu, normu i procenu): {prof}\n"
        f"KRVNI PRITISAK (poslednji): {bp}\n"
        f"PULS U MIROVANJU: {hr} bpm\n"
        f"AKTIVNE DIJAGNOZE: {dx_list}\n"
        f"LABORATORIJSKI NALAZI (poslednji): {lab_list}\n"
        f"LEKOVI KOJE KORISNIK TRENUTNO KORISTI: {meds_list}\n"
        f"PRAVILO: Svaku procenu, preporuku i pretragu prilagodi OVOM osobi — "
        f"njenim godinama, telesnoj masi (BMI) i polu. Norme i porcije računaj po "
        f"kg telesne mase gde je relevantno. Ne daj generičke savete. UVEK proveri "
        f"moguću interakciju predloga (hrana/suplement/savet) sa lekovima koje "
        f"korisnik već koristi i eksplicitno je pomeni ako postoji."
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


def _vitals_trend_summary() -> str:
    """Kratak tekstualni sažetak trenda vitalnih znakova (7 dana) za AI."""
    rows = vitals_series(7)
    if not rows:
        return "nema merenja u poslednjih 7 dana"
    sys = [r["blood_pressure_sys"] for r in rows if r["blood_pressure_sys"]]
    dia = [r["blood_pressure_dia"] for r in rows if r["blood_pressure_dia"]]
    hr = [r["heart_rate"] for r in rows if r["heart_rate"]]
    parts = [f"{len(rows)} merenja u 7 dana"]
    if sys:
        parts.append(f"SYS prosek {sum(sys)/len(sys):.0f} (raspon {min(sys)}-{max(sys)}, "
                     f"poslednji {sys[-1]})")
    if dia:
        parts.append(f"DIA prosek {sum(dia)/len(dia):.0f} (poslednji {dia[-1]})")
    if hr:
        parts.append(f"puls prosek {sum(hr)/len(hr):.0f}")
    return "; ".join(parts)


DAILY_SYSTEM = """Ti si lični lekar-asistent koji SVAKODNEVNO procenjuje STANJE \
ORGANIZMA korisnika. Na osnovu njegovog LIČNOG PROFILA (godine, visina, težina, BMI, \
pol), poslednjih vitalnih znakova i njihovog TRENDA, laboratorijskih nalaza i aktivnih \
dijagnoza — daj sažetu, personalizovanu dnevnu procenu. Sve norme i preporuke prilagodi \
BAŠ TOJ osobi (po godinama, telesnoj masi/BMI, polu). Ne daj generičke savete.

VRATI ISKLJUČIVO validan JSON (bez markdown ograda):
{
  "overall": "GREEN" | "YELLOW" | "RED",
  "score": <ceo broj 0-100 — dnevni indeks organizma>,
  "headline": "<kratka ocena, do 8 reči>",
  "summary": "<2-4 rečenice dnevnog stanja organizma>",
  "insights": [ {"status":"GREEN|YELLOW|RED","title":"<kratko>","message":"<1-2 rečenice>"} ],
  "focus": ["<konkretna preporuka za DANAS>", "..."]
}
Piši na srpskom. Budi koristan i jasan kod realnih rizika, ali nealarmantan.
Ako nema dovoljno podataka, budi iskren u summary i predloži šta korisnik da unese."""


def generate_daily_report(model_id: str, api_key: str) -> dict:
    client = _make_client(api_key)
    ctx = build_health_context()
    trend = _vitals_trend_summary()
    msg = (f"ZDRAVSTVENI PROFIL I PODACI:\n{ctx}\n\n"
           f"TREND VITALNIH ZNAKOVA (7 dana): {trend}\n\n"
           f"Datum: {date.today():%d.%m.%Y}. Daj DNEVNO STANJE ORGANIZMA kao JSON.")
    with client.messages.stream(
        model=model_id, max_tokens=1200, system=DAILY_SYSTEM,
        messages=[{"role": "user", "content": msg}],
    ) as stream:
        resp = stream.get_final_message()
    raw = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(raw)


# =========================================================================== #
#  SLOJ 3+4: MULTI-AGENT KONZILIJUM (nezavisna analiza → protiv-teg filter →
#  sinteza sa Chain-of-Citation). Svaki agent je vezan za svoje smernice.
# =========================================================================== #
AGENT_JSON_SHAPE = """VRATI ISKLJUČIVO validan JSON (bez markdown ograda):
{
  "agent": "<tvoje ime>",
  "risk_score": <0-100, rizik u TVOM domenu za ovog konkretnog pacijenta>,
  "observations": ["<nalaz kroz tvoju kliničku prizmu>", "..."],
  "recommendations": [
    {"text": "<konkretna preporuka>", "category": "ishrana|životni stil|praćenje|lekar",
     "strength": "jaka|umerena|slaba",
     "citation": {"guideline": "<smernica i godina, npr. ESC 2024 Arterial Hypertension>",
                  "section": "<poglavlje/sekcija, npr. §8.2 Dietary sodium>"}}
  ],
  "flags": ["<nalaz koji zahteva pažnju drugih specijalista>"]
}
PRAVILA: Ostani STRIKTNO u svom domenu. SVAKA preporuka mora imati citation
(smernica + sekcija) — bez izvora nema preporuke. NE izmišljaj DOI ni autore;
citiraj samo naziv smernice i sekciju. Sve prilagodi profilu pacijenta
(godine, BMI, pol). Piši na srpskom."""

AGENTS = {
    "Kardiolog": {
        "emoji": "🫀", "guidelines": "ESC (European Society of Cardiology), AHA",
        "triggers": ["Holesterol", "LDL", "HDL", "Trigliceridi", "CPK", "LDH"],
        "system": "Ti si Agent KARDIOLOG konzilijuma. Radiš ISKLJUČIVO po ESC i AHA "
                  "smernicama (arterijska hipertenzija, dislipidemija, KV prevencija). "
                  "Analiziraš: pritisak (3x dnevno, obrasce jutro/popodne/veče, "
                  "non-dipping), lipidni panel, CPK, LDH. " + AGENT_JSON_SHAPE},
    "Urolog": {
        "emoji": "🚻", "guidelines": "EAU (European Association of Urology)",
        "triggers": ["Eritrociti u urinu", "Leukociti u urinu", "Protein u urinu", "PSA"],
        "system": "Ti si Agent UROLOG konzilijuma. Radiš ISKLJUČIVO po EAU smernicama "
                  "(hematurija, infekcije, prostata). Analiziraš: urinalizu (eritrociti, "
                  "leukociti, protein, nitriti), PSA. " + AGENT_JSON_SHAPE},
    "Nefrolog": {
        "emoji": "🫘", "guidelines": "KDIGO",
        "triggers": ["Kreatinin", "eGFR", "Mokraćna kiselina", "Kalijum", "Natrijum",
                     "Hlorid", "Kalcijum", "Magnezijum", "Urea"],
        "system": "Ti si Agent NEFROLOG konzilijuma. Radiš ISKLJUČIVO po KDIGO "
                  "smernicama (CKD, elektroliti, renalna hipertenzija). Analiziraš: "
                  "kreatinin, eGFR (CKD-EPI), mokraćnu kiselinu, elektrolite, dnevne BP "
                  "trendove. POSEBNO pazi na kalijum i pad eGFR — tvoja mišljenja imaju "
                  "pravo veta na dijetetske preporuke drugih. " + AGENT_JSON_SHAPE},
    "Endokrinolog": {
        "emoji": "🦋", "guidelines": "ATA, ADA",
        "triggers": ["Glukoza", "HbA1c", "TSH"],
        "system": "Ti si Agent ENDOKRINOLOG konzilijuma. Radiš ISKLJUČIVO po ATA i ADA "
                  "smernicama (štitnjača, glikemija, metabolizam). Analiziraš: glukozu "
                  "natašte, HbA1c, TSH, lipidne frakcije, JUTARNJE BP skokove "
                  "(kortizol/hormonska komponenta). " + AGENT_JSON_SHAPE},
    "Hepatolog": {
        "emoji": "🫓", "guidelines": "EASL",
        "triggers": ["AST", "ALT", "Gama-GT", "Bilirubin", "ALP", "Trigliceridi"],
        "system": "Ti si Agent HEPATOLOG konzilijuma. Radiš ISKLJUČIVO po EASL "
                  "smernicama (jetra, MAFLD). Analiziraš: GOT/AST, GPT/ALT, Gama-GT, "
                  "bilirubin, ALP, trigliceride, popodnevne (post-prandijalne) BP "
                  "odgovore. " + AGENT_JSON_SHAPE},
    "Hematolog": {
        "emoji": "🩸", "guidelines": "ASH",
        "triggers": ["Gvožđe", "Feritin", "Vitamin B12", "Folna kiselina",
                     "Hemoglobin", "Eritrociti u urinu", "Leukociti", "Trombociti"],
        "system": "Ti si Agent HEMATOLOG konzilijuma. Radiš ISKLJUČIVO po ASH "
                  "smernicama (anemije, deficiti). Analiziraš: gvožđe, feritin, B12, "
                  "folnu kiselinu, hemoglobin i krvnu sliku. " + AGENT_JSON_SHAPE},
}

NUTRI_SYSTEM = """Ti si Agent KLINIČKI NUTRICIONISTA konzilijuma (ESPEN smernice, \
DASH/Mediteranske studije). Dobijaš RAZREŠENE preporuke svih specijalista (posle \
protiv-teg filtera) i kompletne podatke pacijenta. Napravi biometrijski optimalan \
nutritivni plan koji POŠTUJE SVA ograničenja specijalista (naročito nefrološka — \
kalijum/so/protein) i profil pacijenta (godine, BMI, pol).
VRATI ISKLJUČIVO validan JSON:
{
  "nutrition_plan": [
    {"text": "<konkretna nutritivna intervencija>", "reason": "<zašto, vezano za nalaze>",
     "citation": {"guideline": "<npr. ESPEN 2023 / DASH trial>", "section": "<sekcija>"}}
  ],
  "avoid": ["<šta izbegavati i zašto ukratko>"],
  "notes": "<kratka napomena>"
}
NE izmišljaj DOI. Piši na srpskom."""

CONFLICT_SYSTEM = """Ti si ORKESTRATOR konzilijuma — protiv-teg filter (Counter-Weight). \
Dobijaš nezavisne JSON izveštaje specijalista i Klinički graf znanja (CKG). Zadatak: \
STROGA provera protivrečnosti između preporuka.
Primer pravila: ako Kardiolog preporuči ishranu bogatu kalijumom, a Nefrolog vidi \
povišen kalijum ili pad eGFR — kardiološka preporuka se DEPRECIRA ili MODIFIKUJE, \
uz eksplicitno obrazloženje ("Modifikujem kardiološki protokol X zbog renalnog \
parametra Y"). Nefrološka bezbednost ima prednost nad optimizacijom.
VRATI ISKLJUČIVO validan JSON:
{
  "resolutions": [
    {"original": "<originalna preporuka>", "from_agent": "<ko ju je dao>",
     "action": "zadržano|modifikovano|deprecirano",
     "modified_to": "<nova verzija ili null>",
     "reason": "<eksplicitno: Modifikujem/Depreciram X zbog Y>",
     "due_to_agent": "<čiji nalaz je presudio ili null>"}
  ],
  "final_recommendations": [
    {"text": "<konačna preporuka>", "category": "ishrana|životni stil|praćenje|lekar",
     "strength": "jaka|umerena|slaba", "from_agent": "<izvor>",
     "citation": {"guideline": "<smernica>", "section": "<sekcija>"}}
  ]
}
Piši na srpskom."""

SYNTH_SYSTEM = """Ti si PREDSEDAVAJUĆI konzilijuma. Dobijaš: razrešene preporuke \
(posle protiv-teg filtera), nutritivni plan, rizik-skorove agenata i podatke pacijenta. \
Sastavi KONAČAN dnevni izveštaj stanja organizma.
VRATI ISKLJUČIVO validan JSON:
{
  "overall": "GREEN" | "YELLOW" | "RED",
  "score": <0-100 dnevni indeks organizma (uzmi u obzir rizik-skorove svih agenata)>,
  "headline": "<kratka ocena, do 8 reči>",
  "summary": "<3-5 rečenica: sinteza svih domena, pomeni ključne trendove>",
  "insights": [ {"status":"GREEN|YELLOW|RED","title":"<domen: kratko>","message":"<1-2 rečenice>"} ],
  "focus": ["<konkretna preporuka za DANAS (iz final_recommendations/nutritivnog plana)>"],
  "citations": [ {"guideline":"<smernica>","section":"<sekcija>","claim":"<na šta se odnosi>"} ],
  "conflicts": ["<rečenica o svakom razrešenom sukobu, npr. Modifikovan X zbog Y>"]
}
SVAKA tvrdnja u focus/insights mora biti pokrivena stavkom u citations (Chain-of-
Citation). NE izmišljaj DOI. Piši na srpskom."""


def clinical_data_bundle() -> str:
    """Kompletan klinički paket za agente: profil + vremenska linija sa trend
    velocity analizom + BP obrasci (3x dnevno) + dijagnoze."""
    ctx = build_health_context()
    tv = trend_velocity()
    lines = []
    for t in sorted(tv, key=lambda x: (not x["adverse_trend"], x["param"])):
        flag = " ⚠ NEPOVOLJAN TREND" if t["adverse_trend"] else ""
        rng = "u opsegu" if t["in_range"] else "VAN OPSEGA"
        lines.append(f"- {t['param']} [LOINC {t['loinc']}]: {t['last_value']} {t['unit']} "
                     f"({t['last_date']}), {rng}, trend "
                     f"{t['pct_of_range_per_month']:+.1f}% opsega/mes, n={t['points']}{flag}")
    bp = bp_pattern_analysis(7)
    bp_lines = [f"  {p}: {a[0]}/{a[1]} mmHg" if a else f"  {p}: nema merenja"
                for p, a in bp["averages"].items()]
    return (f"{ctx}\n\nVREMENSKA LINIJA LAB PARAMETARA (Trend Velocity — ponderisana "
            f"regresija, skoriji podaci teži):\n" + "\n".join(lines or ["- nema lab podataka"])
            + f"\n\nKRVNI PRITISAK — PROSECI PO DOBU DANA (poslednjih {bp['days']} dana, "
            f"{bp['readings']} merenja):\n" + "\n".join(bp_lines)
            + ("\nDETEKTOVANI OBRASCI:\n" + "\n".join(f"  ⚠ {p}" for p in bp["patterns"])
               if bp["patterns"] else "\n(bez detektovanih BP obrazaca)"))


def _agent_call(client, model_id: str, system: str, user_msg: str,
                max_tokens: int = 1600) -> dict:
    with client.messages.stream(
        model=model_id, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        resp = stream.get_final_message()
    return _parse_json("".join(b.text for b in resp.content if b.type == "text"))


def run_consortium(model_id: str, api_key: str, status=None) -> dict:
    """Hijerarhijski konsenzus: 1) nezavisne analize po domenu → 2) protiv-teg
    filter (rešavanje sukoba uz CKG) → 3) nutricionista → 4) sinteza sa citatima."""
    client = _make_client(api_key)
    bundle = clinical_data_bundle()
    bp = bp_pattern_analysis(7)
    present = {t["param"] for t in trend_velocity()}

    # Koji agenti se aktiviraju (imaju svoje podatke ili BP cross-talk okidač)
    triggered = [n for n, a in AGENTS.items()
                 if (present & set(a["triggers"])) or (n in bp["triggers"])]
    if not triggered:
        triggered = ["Kardiolog"]

    # Korak 1 — nezavisna analiza po domenu
    sub_reports = {}
    for name in triggered:
        if status:
            status.update(label=f"{AGENTS[name]['emoji']} Agent {name} analizira "
                                f"({AGENTS[name]['guidelines']})…")
        try:
            sub_reports[name] = _agent_call(
                client, model_id, AGENTS[name]["system"],
                f"PODACI PACIJENTA:\n{bundle}\n\nDaj svoj izolovani sub-report kao JSON.")
        except Exception as e:  # noqa: BLE001
            sub_reports[name] = {"agent": name, "error": str(e)[:200],
                                 "observations": [], "recommendations": [], "flags": []}

    # Korak 2 — protiv-teg filter (konflikt-rezolucija uz CKG)
    if status:
        status.update(label="⚖️ Protiv-teg filter: provera protivrečnosti…")
    conflict_input = (f"KLINIČKI GRAF ZNANJA (CKG):\n{ckg_context()}\n\n"
                      f"SUB-REPORTI SPECIJALISTA:\n"
                      f"{json.dumps(sub_reports, ensure_ascii=False)}\n\n"
                      f"Razreši protivrečnosti i vrati JSON.")
    try:
        resolution = _agent_call(client, model_id, CONFLICT_SYSTEM, conflict_input, 2500)
    except Exception:  # noqa: BLE001
        resolution = {"resolutions": [], "final_recommendations": []}

    # Korak 2.5 — klinički nutricionista (sintetiše razrešene preporuke)
    if status:
        status.update(label="🥗 Agent Nutricionista pravi biometrijski plan (ESPEN)…")
    try:
        nutrition = _agent_call(
            client, model_id, NUTRI_SYSTEM,
            f"PODACI PACIJENTA:\n{bundle}\n\nRAZREŠENE PREPORUKE KONZILIJUMA:\n"
            f"{json.dumps(resolution.get('final_recommendations', []), ensure_ascii=False)}"
            f"\n\nNapravi nutritivni plan kao JSON.")
    except Exception:  # noqa: BLE001
        nutrition = {"nutrition_plan": [], "avoid": [], "notes": ""}

    # Korak 3 — sinteza (Chain-of-Citation)
    if status:
        status.update(label="🧬 Predsedavajući sastavlja konačan izveštaj…")
    synth_input = (
        f"PODACI PACIJENTA:\n{bundle}\n\n"
        f"RIZIK-SKOROVI: " + ", ".join(
            f"{n}={r.get('risk_score', '—')}" for n, r in sub_reports.items()) + "\n\n"
        f"RAZREŠENE PREPORUKE:\n{json.dumps(resolution, ensure_ascii=False)}\n\n"
        f"NUTRITIVNI PLAN:\n{json.dumps(nutrition, ensure_ascii=False)}\n\n"
        f"Datum: {date.today():%d.%m.%Y}. Sastavi konačan izveštaj kao JSON.")
    final = _agent_call(client, model_id, SYNTH_SYSTEM, synth_input, 2500)

    # Sačuvaj kompletan zapisnik konzilijuma
    detail = {"sub_reports": sub_reports, "resolution": resolution,
              "nutrition": nutrition, "triggered": triggered,
              "bp_patterns": bp["patterns"]}
    q_exec("""INSERT INTO consortium_reports (id, generated_at, signature, content)
              VALUES (1, ?, ?, ?)
              ON CONFLICT (id) DO UPDATE SET generated_at=excluded.generated_at,
                signature=excluded.signature, content=excluded.content""",
           (datetime.now().isoformat(), data_signature(),
            json.dumps(detail, ensure_ascii=False)))
    return final


def get_consortium_detail():
    row = q_one("SELECT * FROM consortium_reports WHERE id = 1")
    if not row:
        return None
    try:
        return json.loads(row["content"])
    except Exception:  # noqa: BLE001
        return None


# =========================================================================== #
#  SUPLEMENTACIJA — poseban, ručni poziv (1 API poziv) koji ČITA poslednji
#  zapisnik konzilijuma umesto da ponavlja svih 6 agenata. Predlaže SAMO
#  suplemente sa potvrđenom efikasnošću (NIH ODS / EFSA nivo dokaza), sa
#  konkretnim dozama. Tvrde brave u kodu (ne u AI-ju) sprečavaju opasne
#  predloge kod bubrežne insuficijencije / već potvrđenog viška nutrijenta.
# =========================================================================== #
SUPPLEMENT_SYSTEM = """Ti si Agent SUPLEMENTOLOG — klinički farmakolog/nutricionista \
fokusiran ISKLJUČIVO na dokazanu efikasnost. Radiš po smernicama NIH Office of \
Dietary Supplements (ODS fact sheets) i EFSA (gornji bezbedni limiti unosa).

STROGO PRAVILO — DOKAZANA EFIKASNOST: Predlaži SAMO suplemente za koje postoji \
JAK, opšte prihvaćen naučni konsenzus da koriguju POTVRĐEN laboratorijski deficit \
(npr. vitamin D kod niskog 25-OH-D, B12 kod niskog B12, gvožđe kod potvrđene \
sideropenije uz nizak feritin, folna kiselina kod niskog folata, magnezijum kod \
potvrđeno niskog nivoa, omega-3 kod povišenih triglicerida). NIKAD ne predlaži \
„modne"/nepotvrđene suplemente (detoks mešavine, imunitet-bez-dokaza, itd.) — \
ako dokazi nisu jaki za KONKRETAN nalaz ovog korisnika, eksplicitno napiši da \
nema dovoljno osnova, umesto da nešto izmišljaš.

OBAVEZNO uzmi u obzir:
- Kompletnu lab istoriju i trend (ne samo poslednju vrednost).
- Mišljenja i rizik-skorove specijalista konzilijuma i već razrešene sukobe
  (protiv-teg filter) — ne predlaži ništa što je konzilijum već označio kao rizično.
- LEKOVE koje korisnik trenutno koristi (iz konteksta) — eksplicitno proveri
  interakciju leka i suplementa i pomeni je ako postoji.
- Klinički graf znanja (CKG) — kontraindikacije suplemenata.

VRATI ISKLJUČIVO validan JSON (bez markdown ograda):
{
  "supplements": [
    {"nutrient": "<npr. Vitamin D3>", "form": "<npr. holekalciferol, kapsula>",
     "dose": "<KONKRETNA doza, npr. '2000 IU dnevno uz obrok'>",
     "reason": "<vezano za konkretan lab nalaz ovog korisnika>",
     "priority": "visok" | "umeren" | "nizak",
     "retest_after_weeks": <broj>,
     "interaction_check": "<eksplicitno: nema interakcije sa unetim lekovima | postoji interakcija sa X>",
     "citation": {"guideline": "NIH ODS ili EFSA", "section": "<naziv fact sheet-a/sekcije>"},
     "caution": "<upozorenje ako postoji>"}
  ],
  "avoided": [
    {"nutrient": "<naziv>", "reason": "<zašto NIJE predložen — npr. nedovoljno dokaza,
     već u referentnom opsegu, kontraindikacija zbog X>"}
  ],
  "disclaimer": "Ovo je savetodavna informacija — konsultuj lekara/farmaceuta pre početka bilo kog suplementa."
}
Sav tekst na srpskom. Ne izmišljaj DOI ni brojeve studija — citiraj samo naziv
smernice/fact sheet-a."""


def run_supplement_analysis(model_id: str, api_key: str) -> dict:
    """Jedan API poziv: čita poslednji zapisnik konzilijuma + kompletnu lab
    istoriju + CKG, predlaže suplemente sa konkretnim dozama. Rezultat prolazi
    kroz tvrde Python bezbednosne filtere pre čuvanja (ispod)."""
    client = _make_client(api_key)
    bundle = clinical_data_bundle()
    detail = get_consortium_detail() or {}
    consortium_summary = json.dumps({
        "sub_reports": {n: {"risk_score": r.get("risk_score"),
                            "flags": r.get("flags", [])}
                       for n, r in (detail.get("sub_reports") or {}).items()},
        "resolution": detail.get("resolution", {}),
    }, ensure_ascii=False)
    msg = (f"PODACI PACIJENTA:\n{bundle}\n\n"
           f"KLINIČKI GRAF ZNANJA (CKG):\n{ckg_context()}\n\n"
           f"NALAZI/RIZICI IZ POSLEDNJEG KONZILIJUMA:\n{consortium_summary}\n\n"
           f"Predloži suplementaciju kao JSON prema strukturi.")
    with client.messages.stream(
        model=model_id, max_tokens=2500, system=SUPPLEMENT_SYSTEM,
        messages=[{"role": "user", "content": msg}],
    ) as stream:
        resp = stream.get_final_message()
    raw = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(raw)


def _apply_supplement_safety_filter(data: dict) -> dict:
    """TVRDA brava u kodu (ne u AI-ju) — deterministički uklanja opasne predloge
    bez obzira šta je AI predložio. Isti princip kao Red Flags."""
    tl = lab_timeline()

    def last_val(canon):
        pts = tl.get(canon) or []
        return pts[-1][1] if pts else None

    egfr = last_val("eGFR")
    kalijum = last_val("Kalijum")
    feritin = last_val("Feritin")
    kalcijum = last_val("Kalcijum")

    blocked_terms = {
        "kalijum": (egfr is not None and egfr < 60) or (kalijum is not None and kalijum >= 5.0),
        "magnezijum": egfr is not None and egfr < 60,
        "gvožđe": feritin is not None and feritin >= 150,  # dovoljne zalihe — rizik od preopterećenja
        "kalcijum": kalcijum is not None and kalcijum > 2.6,
    }

    kept, blocked = [], list(data.get("avoided") or [])
    for s in (data.get("supplements") or []):
        nutrient_lc = str(s.get("nutrient", "")).lower()
        hit = next((term for term, cond in blocked_terms.items()
                   if term in nutrient_lc and cond), None)
        if hit:
            blocked.append({"nutrient": s.get("nutrient"),
                            "reason": f"BLOKIRANO (bezbednosni filter): postojeći lab nalaz "
                                     f"({hit}) čini ovaj suplement rizičnim — AI predlog je "
                                     f"automatski odbačen bez obzira na obrazloženje."})
        else:
            kept.append(s)
    data["supplements"] = kept
    data["avoided"] = blocked
    return data


def _load_supplement_cached():
    """Učitava POSLEDNJI predlog suplementacije — BEZ ikakvog AI poziva.
    Vraća (data|None, is_stale)."""
    row = q_one("SELECT * FROM supplement_plans WHERE id = 1")
    if not row:
        return None, False
    try:
        data = json.loads(row["content"])
    except Exception:  # noqa: BLE001
        return None, False
    return data, row["signature"] != data_signature()


def ensure_supplement_plan(force: bool = False):
    """Generiše predlog suplementacije ISKLJUČIVO na zahtev (force=True, klik
    dugmeta) — nikad automatski."""
    if not force or not api_ready:
        return None
    if st.session_state.get("red_flags"):
        return None  # kritično stanje — suplementi obustavljeni, isto kao hrana
    sig = data_signature()
    try:
        with st.spinner("💊 Agent Suplementolog analizira lab nalaze i konzilijum…"):
            data = run_supplement_analysis(model_id, api_key)
            data = _apply_supplement_safety_filter(data)
    except Exception as e:  # noqa: BLE001
        em = str(e).lower()
        if "credit balance" in em or "billing" in em or "quota" in em:
            st.warning("💳 Anthropic nalog nema kredita — dopuni na "
                       "console.anthropic.com → Plans & Billing.")
        else:
            st.warning(f"Ne mogu sada da predložim suplementaciju: {e}")
        return None
    q_exec("""INSERT INTO supplement_plans (id, generated_at, signature, content)
              VALUES (1, ?, ?, ?)
              ON CONFLICT (id) DO UPDATE SET generated_at=excluded.generated_at,
                signature=excluded.signature, content=excluded.content""",
           (datetime.now().isoformat(), sig, json.dumps(data, ensure_ascii=False)))
    return data


def load_cached_report():
    """Učitava POSLEDNJI sačuvan izveštaj konzilijuma — BEZ ikakvog AI poziva.
    Konzilijum se više NE saziva automatski; ovo samo čita ono što već postoji.
    Vraća (report_dict|None, is_stale) — is_stale=True znači da ima novijih
    unosa od poslednjeg sazivanja (samo informativno, ne pokreće ništa)."""
    rep = get_daily_report()
    if not rep:
        return None, False
    try:
        data = json.loads(rep["content"])
    except Exception:  # noqa: BLE001
        return None, False
    is_stale = rep["signature"] != data_signature()
    return data, is_stale


def run_consortium_and_save():
    """Saziva konzilijum ISKLJUČIVO na zahtev (klik na dugme „Konzilijum")."""
    sig = data_signature()
    today = date.today().isoformat()
    try:
        with st.status("🧠 Konzilijum zaseda — analiza podataka…",
                       expanded=False) as status:
            data = run_consortium(model_id, api_key, status)
            status.update(label="✅ Konzilijum završio konsenzus.", state="complete")
    except Exception as e:  # noqa: BLE001
        em = str(e).lower()
        if "credit balance" in em or "billing" in em or "quota" in em:
            st.warning("💳 Anthropic nalog nema kredita — dopuni na "
                       "console.anthropic.com → Plans & Billing.")
        else:
            # Rezerva: stari brzi mozak, da Dashboard ne ostane prazan
            try:
                data = generate_daily_report(model_id, api_key)
                save_daily_report(today, sig, json.dumps(data, ensure_ascii=False))
                st.caption(f"Konzilijum nedostupan ({str(e)[:120]}) — prikazana brza procena.")
                return data
            except Exception:  # noqa: BLE001
                st.warning(f"Ne mogu sada da generišem dnevno stanje: {e}")
        return None
    save_daily_report(today, sig, json.dumps(data, ensure_ascii=False))
    return data


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
- "medication" — PAKOVANJE LEKA, BLISTER, KUTIJA ili UPUTSTVO ZA LEK (naziv leka,
  aktivna supstanca, jačina/doza, farmaceutski oblik).
- "unknown" — ako se ne može pouzdano svrstati.

Popuni SAMO objekat koji odgovara prepoznatom tipu; ostale stavi na null.

VRATI ISKLJUČIVO validan JSON (bez markdown ograda):
{
  "doc_type": "vitals_device" | "food_product" | "medical_document" | "medication" | "unknown",
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
  "medication": {
    "medication_name": "<komercijalni naziv leka>",
    "active_substance": "<aktivna supstanca (INN naziv) ili null>",
    "dosage_text": "<jačina/doza, npr. '20 mg' ili '50mg/5ml', ili null>",
    "form": "<tableta|kapsula|sirup|injekcija|mast|kapi|ostalo>",
    "purpose": "<terapijska grupa/namena ako je vidljiva na pakovanju, inače null>",
    "notes": "<kratko šta si pročitao>"
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
PRAVILA za medication: Čitaj TAČNO naziv i jačinu sa kutije/blistera/uputstva —
ne pogađaj. Ako aktivna supstanca nije eksplicitno napisana, ostavi null (ne izmišljaj).
Sav opisni tekst na srpskom."""


def _claude_image_block(image_b64: str) -> dict:
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}}


def prepare_media(uploaded_file) -> tuple[dict, str]:
    """Iz uploada pravi Anthropic blok (slika ili PDF dokument) + opcioni OCR tekst.
    PDF ide Claude-u direktno; slika se skalira i (ako ima Google ključa) OCR-uje."""
    name = (getattr(uploaded_file, "name", "") or "").lower()
    if name.endswith(".pdf"):
        data = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
        b64 = base64.standard_b64encode(data).decode("utf-8")
        block = {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
        return block, ""
    b64 = image_to_b64(uploaded_file)
    if not b64:
        raise ValueError("Ne mogu da pročitam sliku.")
    ocr_text = ""
    if google_ready:
        try:
            ocr_text = google_ocr(b64, google_key)
        except Exception:  # noqa: BLE001
            ocr_text = ""
    return _claude_image_block(b64), ocr_text


def smart_analyze(media_block: dict, ocr_text: str, model_id: str, api_key: str) -> dict:
    """Jedan poziv: Claude ČITA SAM PRILOG (slika/PDF — pouzdano za LCD/cifre i nalaze),
    klasifikuje i izvlači podatke. Google OCR tekst je samo pomoćni nagoveštaj."""
    client = _make_client(api_key)
    ctx = build_health_context()
    content = [
        {"type": "text", "text": f"ZDRAVSTVENI PROFIL KORISNIKA:\n{ctx}"},
        {"type": "text", "text": "PRILOG (slika ili PDF) — čitaj PRVENSTVENO direktno sa "
         "priloga (brojevi na ekranu/LCD-u, deklaracije, laboratorijski nalazi, izveštaji):"},
        media_block,
    ]
    if ocr_text:
        content.append({"type": "text", "text": "Pomoćni OCR tekst (može imati grešaka — "
                        f"PRILOG ima prednost):\n\"\"\"\n{ocr_text}\n\"\"\""})
    content.append({"type": "text", "text":
                    "Klasifikuj i izvuci podatke. Vrati ISKLJUČIVO JSON prema strukturi."})
    with client.messages.stream(
        model=model_id, max_tokens=4096, system=SMART_ROUTER_SYSTEM,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        resp = stream.get_final_message()
    raw = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(raw)


# --------------------------------------------------------------------------- #
#  Inicijalizacija stanja
# --------------------------------------------------------------------------- #
init_db()
seed_ckg()
if "theme" not in st.session_state:
    st.session_state["theme"] = "light"
if "view" not in st.session_state:
    st.session_state["view"] = "Dashboard"
inject_css(st.session_state["theme"])
require_login()

# Auto-seed na praznoj bazi (samo prvi put)
if latest_vitals() is None and not q_one("SELECT 1 FROM lab_results LIMIT 1"):
    seed_demo_data()

# KLINIČKA TRIJAŽA (Red Flags) — tvrda brava izvan LLM-a, na svako pokretanje
st.session_state["red_flags"] = check_red_flags()


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

    # --- RED FLAGS: dominantan kritičан ekran, blokira sve AI savete ---
    red_flags = st.session_state.get("red_flags") or []
    if red_flags:
        render_red_flag_screen(red_flags)
        st.write("")

    # --- Dnevno stanje organizma — SAMO na zahtev (dugme „Konzilijum"), ---
    # --- više se NE saziva automatski posle svakog novog unosa. ---
    report, is_stale = (None, False) if red_flags else load_cached_report()
    if not red_flags:
        if not api_ready:
            st.info("Unesi ANTHROPIC_API_KEY (⚙️ levo) da bi mogao da sazoveš konzilijum.")
        else:
            if report and is_stale:
                st.caption("📥 Ima novijih unosa od poslednjeg sazivanja konzilijuma.")
            elif not report:
                st.caption("Konzilijum (6 specijalista + nutricionista) još nije sazivan.")
            if st.button("🧠 Konzilijum", key="run_konzilijum",
                        type="primary", use_container_width=True):
                report = run_consortium_and_save()
                if report is not None:
                    st.rerun()  # samo na uspeh — greška ostaje vidljiva ako pukne
            st.write("")

    if report:
        dcol = VERDICT.get(str(report.get("overall", "YELLOW")).upper(), VERDICT["YELLOW"])
        score = report.get("score", "—")
        focus = report.get("focus") or []
        focus_html = "".join(f"<li>{f}</li>" for f in focus)
        st.markdown(f"""
        <div class='mt-card' style='border:1px solid {dcol}55;
             box-shadow:0 0 0 1px {dcol}33, 0 14px 34px {dcol}22'>
          <div style='display:flex;align-items:center;gap:16px'>
            <div style='flex:0 0 auto;width:74px;height:74px;border-radius:50%;
                 display:grid;place-items:center;background:{dcol}1A;border:2px solid {dcol}'>
              <div style='font-size:1.5rem;font-weight:900;color:{dcol}'>{score}</div>
            </div>
            <div>
              <div class='mt-muted' style='letter-spacing:1px;font-size:.72rem'>
                🧬 DNEVNO STANJE ORGANIZMA</div>
              <div style='font-size:1.15rem;font-weight:800;color:{dcol}'>{report.get('headline','')}</div>
            </div>
          </div>
          <p style='margin:12px 0 0'>{report.get('summary','')}</p>
          {("<b style='font-size:.85rem'>Fokus danas:</b><ul style='margin:6px 0 0;padding-left:18px'>" + focus_html + "</ul>") if focus else ""}
        </div>
        """, unsafe_allow_html=True)

        # Protiv-teg filter: eksplicitno prikaži razrešene sukobe
        conflicts = report.get("conflicts") or []
        for c in conflicts:
            st.markdown(
                f"<div class='mt-guard' style='border-color:{VERDICT['YELLOW']};"
                f"background:{VERDICT['YELLOW']}1A'>⚖️ <b>Protiv-teg filter:</b> {c}</div>",
                unsafe_allow_html=True)

        # Zapisnik konzilijuma: sub-reporti, rezolucije, nutritivni plan, citati
        detail = get_consortium_detail()
        if detail:
            with st.expander("🧠 Zapisnik konzilijuma — mišljenja specijalista"):
                for name in detail.get("triggered", []):
                    sr = (detail.get("sub_reports") or {}).get(name) or {}
                    em = AGENTS.get(name, {}).get("emoji", "🩺")
                    gl = AGENTS.get(name, {}).get("guidelines", "")
                    risk = sr.get("risk_score", "—")
                    st.markdown(f"**{em} {name}** ({gl}) — rizik u domenu: **{risk}/100**")
                    for o in (sr.get("observations") or [])[:4]:
                        st.markdown(f"- {o}")
                    if sr.get("error"):
                        st.caption(f"⚠ Agent nije završio: {sr['error']}")
                    st.write("")
                res = (detail.get("resolution") or {}).get("resolutions") or []
                changed = [r for r in res if r.get("action") in ("modifikovano", "deprecirano")]
                if changed:
                    st.markdown("**⚖️ Razrešeni sukobi (protiv-teg):**")
                    for r in changed:
                        st.markdown(f"- **{r.get('action', '').upper()}** "
                                    f"[{r.get('from_agent', '')}] „{r.get('original', '')}“ → "
                                    f"{r.get('reason', '')}")
                nut = detail.get("nutrition") or {}
                if nut.get("nutrition_plan"):
                    st.markdown("**🥗 Nutritivni plan (ESPEN/DASH):**")
                    for item in nut["nutrition_plan"][:6]:
                        cit = item.get("citation") or {}
                        st.markdown(f"- {item.get('text', '')} "
                                    f"<span class='mt-muted'>[{cit.get('guideline', '')} "
                                    f"{cit.get('section', '')}]</span>", unsafe_allow_html=True)
                if nut.get("avoid"):
                    st.markdown("**⛔ Izbegavati:** " + " · ".join(nut["avoid"][:6]))

        # Chain-of-Citation: izvori za sve tvrdnje
        cits = report.get("citations") or []
        if cits:
            with st.expander(f"📚 Izvori — Chain-of-Citation ({len(cits)})"):
                for c in cits:
                    st.markdown(f"- **{c.get('guideline', '')}**, {c.get('section', '')} — "
                                f"<span class='mt-muted'>{c.get('claim', '')}</span>",
                                unsafe_allow_html=True)

        st.session_state["daily_insights"] = report.get("insights") or []

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

    # --- Medicinski karton (sažeto — sadržaj se prikazuje tek na klik) ---
    dx = active_diagnoses()
    labs = latest_labs()
    with st.expander(f"📋 Medicinski karton ({len(dx)} dijagnoza · {len(labs)} nalaza)"):
        if dx:
            chips = "".join(f"<span class='mt-chip'>{d['diagnosis_name']}</span>" for d in dx)
            st.markdown(chips, unsafe_allow_html=True)
        else:
            st.caption("Nema aktivnih dijagnoza.")
        st.write("")
        for l in labs:
            ref = l["reference_range"] or ""
            st.markdown(
                f"**{l['parameter_name']}** — {l['value']} {l['unit']}  "
                f"<span class='mt-muted'>(ref {ref})</span>", unsafe_allow_html=True)

    st.write("")

    # --- Lekovi koje koristim (sažeto — unos ide preko Smart Camere; analiza/
    # korelacija sa zdravstvenim stanjem radi se ispod haube, kroz zajednički
    # kontekst koji čita ceo mozak — bez posebnog dugmeta za analizu) ---
    meds = active_medications()
    with st.expander(f"💊 Lekovi koje koristim ({len(meds)})"):
        if meds:
            for med in meds:
                dose = f" · {med['dosage_text']}" if med["dosage_text"] else ""
                sub = f" ({med['active_substance']})" if med["active_substance"] else ""
                mcol1, mcol2 = st.columns([5, 1])
                with mcol1:
                    st.markdown(f"**{med['medication_name']}**{sub}{dose}")
                    if med["purpose"]:
                        st.caption(med["purpose"])
                with mcol2:
                    if st.button("🗑️", key=f"stop_med_{med['id']}"):
                        stop_medication(med["id"])
                        st.rerun()
        else:
            st.caption("Nema unetih lekova.")
        st.caption("📷 Skeniraj kutiju/uputstvo leka preko Smart Camere da ga dodaš — "
                   "aplikacija ga automatski uzima u obzir u svim procenama.")

    st.write("")

    # --- Suplementacija (sažeto — analiza SAMO na klik, nikad automatski) ---
    sup, sup_stale = (None, False) if red_flags else _load_supplement_cached()
    n_sup = len(sup.get("supplements") or []) if sup else 0
    with st.expander(f"💊 Suplementacija ({n_sup} predloga)" if sup else "💊 Suplementacija"):
        if red_flags:
            st.caption("Obustavljeno dok je aktivno kritično upozorenje (Red Flag).")
        elif not api_ready:
            st.caption("Unesi ANTHROPIC_API_KEY (⚙️ levo) da bi mogao da zatražiš predlog.")
        else:
            st.caption("Agent Suplementolog (NIH ODS / EFSA) čita tvoju lab istoriju, "
                       "mišljenje konzilijuma i unete lekove — predlaže SAMO suplemente "
                       "sa potvrđenom efikasnošću za tvoj konkretan nalaz, sa konkretnim "
                       "dozama. Opasni predlozi se automatski blokiraju (bezbednosni filter).")
            if sup and sup_stale:
                st.caption("📥 Ima novijih podataka od poslednjeg predloga.")
            label = "🔍 Predloži suplementaciju" if not sup else "🔍 Osveži predlog"
            if st.button(label, key="run_supplement", type="primary", use_container_width=True):
                sup = ensure_supplement_plan(force=True)
                if sup is not None:
                    st.rerun()  # samo na uspeh — greška ostaje vidljiva ako pukne

        if sup:
            supplements = sup.get("supplements") or []
            avoided_preview = sup.get("avoided") or []
            if not supplements and not avoided_preview:
                st.info("✅ Na osnovu trenutnih lab nalaza, nema suplementa sa dovoljno "
                        "jakim dokazima za predlog — svi praćeni nutrijenti su ili u "
                        "referentnom opsegu ili nema dovoljno podataka o njima.")
            for s in supplements:
                cit = s.get("citation") or {}
                pr_col = {"visok": VERDICT["RED"], "umeren": VERDICT["YELLOW"],
                          "nizak": VERDICT["GREEN"]}.get(s.get("priority", ""), VERDICT["YELLOW"])
                st.markdown(
                    f"<div class='mt-guard' style='border-color:{pr_col};background:{pr_col}1A'>"
                    f"<b style='color:{pr_col}'>{s.get('nutrient','')}</b> — {s.get('dose','')}"
                    f" <span class='mt-muted'>({s.get('form','')})</span><br>"
                    f"{s.get('reason','')}<br>"
                    f"<span class='mt-muted'>💊 Interakcija: {s.get('interaction_check','')} · "
                    f"Ponovna provera za {s.get('retest_after_weeks','?')} ned. · "
                    f"[{cit.get('guideline','')} — {cit.get('section','')}]</span>"
                    + (f"<br><span style='color:{VERDICT['YELLOW']}'>⚠ {s['caution']}</span>"
                       if s.get("caution") else "")
                    + "</div>", unsafe_allow_html=True)
            avoided = sup.get("avoided") or []
            if avoided:
                st.markdown("**⛔ Nije predloženo:**")
                for a in avoided:
                    st.markdown(f"- **{a.get('nutrient','')}** — {a.get('reason','')}")
            if sup.get("disclaimer"):
                st.caption(sup["disclaimer"])

    st.write("")

    # --- AI Health Guard ---
    st.markdown("### ✨ AI Health Guard")
    insights = st.session_state.get("daily_insights", [])
    if not api_ready:
        st.caption("Unesi API ključ u ⚙️ (levo) za personalizovane uvide.")
    elif not insights:
        st.caption("Uvidi se generišu uz dnevno stanje organizma (gore).")
    for ins in insights:
        col = VERDICT.get(str(ins.get("status", "YELLOW")).upper(), VERDICT["YELLOW"])
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
        if st.session_state.get("red_flags"):
            # Tvrda brava: kod kritičnog stanja nutritivni saveti su obustavljeni
            st.error("⛔ Kritično stanje detektovano (Red Flag) — nutritivni saveti su "
                     "obustavljeni dok se vrednosti ne normalizuju. Javi se lekaru.")
            return False
        st.success(f"🍎 Prepoznato: prehrambeni proizvod · pouzdanost {conf}.")
        _show_food_result(res["food"])
    elif dt == "medical_document" and res.get("document"):
        st.success(f"📄 Prepoznato: medicinski dokument · pouzdanost {conf}.")
        _store_and_show_doc(res["document"])
    elif dt == "medication" and res.get("medication"):
        st.success(f"💊 Prepoznat lek · pouzdanost {conf}.")
        _store_and_show_medication(res["medication"])
    else:
        st.warning("Nisam uspeo pouzdano da prepoznam sliku. "
                   f"{res.get('notes') or ''} Probaj jasniju/bližu fotografiju.")
        return False
    return True


def _store_and_show_medication(med: dict):
    """Auto-čuvanje leka prepoznatog sa pakovanja/uputstva + prikaz sažetka.
    Korelacija sa zdravstvenim stanjem se NE radi ovde posebno — lek ulazi u
    zajednički kontekst (build_health_context) koji već čita ceo mozak
    (konzilijum, food verdict, dnevno stanje) — „ispod haube", bez posebnog koraka."""
    name = med.get("medication_name") or "Nepoznat lek"
    add_medication(
        name, med.get("active_substance"), med.get("dosage_text"),
        med.get("form"), med.get("purpose"), source="scan",
    )
    dose = f" · {med['dosage_text']}" if med.get("dosage_text") else ""
    sub = f" ({med['active_substance']})" if med.get("active_substance") else ""
    st.markdown(
        f"<div class='mt-card'><b>💊 {name}</b>{sub}{dose}<br>"
        f"<span class='mt-muted'>{med.get('notes','')}</span></div>",
        unsafe_allow_html=True)
    st.success("Sačuvano u sekciju Lekovi koje koristim — od sada se automatski "
               "uzima u obzir u svim procenama (konzilijum, ocena hrane, dnevno stanje).")


def render_camera():
    st.markdown("## 📷 Smart Camera")
    st.caption("Slikaj ili otpremi bilo šta — merač pritiska, deklaraciju hrane, "
               "medicinski nalaz ili pakovanje leka. Mozak sam prepozna tip i smesti "
               "na pravo mesto. Iz galerije možeš odabrati i **više slika odjednom** (5+).")

    src = st.radio("Izvor slike",
                   ["📁 Otpremi iz galerije (više slika)", "📸 Kamera"], horizontal=True)
    if src.startswith("📸"):
        one = st.camera_input("Slikaj")
        images = [one] if one else []
    else:
        images = st.file_uploader(
            "Otpremi slike ili PDF — možeš izabrati 5 i više odjednom",
            type=["png", "jpg", "jpeg", "webp", "pdf"], accept_multiple_files=True) or []

    if not api_ready:
        st.warning("Unesi ANTHROPIC_API_KEY u ⚙️ (bočna traka) — Claude čita prilog i radi procenu.")
        return
    if not images:
        st.caption("Izaberi jednu ili više datoteka iz galerije (ili prebaci na Kamera).")
        return

    st.caption(f"Spremno za analizu: **{len(images)}** "
               f"{'datoteka' if len(images) != 1 else 'datoteka'}.")
    if st.button(f"🔍 Analiziraj ({len(images)})", type="primary", use_container_width=True):
        ok = 0
        for i, img_file in enumerate(images, 1):
            if len(images) > 1:
                st.markdown(f"#### 🖼️ Prilog {i} / {len(images)}")
            try:
                block, ocr_text = prepare_media(img_file)
                if ocr_text:
                    with st.expander("📝 Pomoćni OCR tekst (Google Vision)"):
                        st.text(ocr_text)
                with st.spinner(f"Mozak čita prilog i obrađuje… ({i})"):
                    res = smart_analyze(block, ocr_text, model_id, api_key)
                if _handle_scan_result(res):
                    ok += 1
            except Exception as e:  # noqa: BLE001
                st.error(f"Greška na prilogu {i}: {e}")
            if len(images) > 1:
                st.divider()
        st.success(f"✅ Obrađeno {ok} / {len(images)} datoteka.")
        if st.session_state.pop("go_to_entry", False):
            st.session_state["view"] = "Unos podataka"
            st.rerun()


def _transfer_vitals_to_entry(vit: dict):
    """Prebacuje pročitana merenja (pritisak/puls...) u formu „Unos podataka"
    na proveru i čuvanje — umesto tihog upisa u bazu.
    Datum/vreme unosa je UVEK stvarni trenutak skeniranja (aplikacija ga sama
    generiše), a ne vreme pročitano sa ekrana uređaja — sat na meraču/satu
    često nije tačno podešen, dok je vreme skeniranja pouzdano i precizno."""
    rdt = datetime.now().replace(second=0, microsecond=0)
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
            "Otpremi izveštaj/otpusnu listu (slika ili PDF, može više)",
            type=["png", "jpg", "jpeg", "webp", "pdf"], accept_multiple_files=True,
            key="dx_upload")
        if dx_imgs:
            if not api_ready:
                st.warning("Unesi ANTHROPIC_API_KEY u ⚙️ (bočna traka) da bi AI pročitao izveštaj.")
            elif st.button("🔍 Pročitaj i sačuvaj dijagnoze", type="primary", key="dx_analyze"):
                for i, img in enumerate(dx_imgs, 1):
                    if len(dx_imgs) > 1:
                        st.markdown(f"**🖼️ Prilog {i} / {len(dx_imgs)}**")
                    try:
                        block, ocr_text = prepare_media(img)
                        with st.spinner(f"AI čita izveštaj… ({i})"):
                            res = smart_analyze(block, ocr_text, model_id, api_key)
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
        st.markdown("##### 📎 Otpremi nalaz (slika ili PDF) — AI pročita i upiše parametre")
        lab_imgs = st.file_uploader(
            "Otpremi laboratorijski nalaz (slika ili PDF, može više)",
            type=["png", "jpg", "jpeg", "webp", "pdf"], accept_multiple_files=True,
            key="lab_upload")
        if lab_imgs:
            if not api_ready:
                st.warning("Unesi ANTHROPIC_API_KEY u ⚙️ (bočna traka) da bi AI pročitao nalaz.")
            elif st.button("🔍 Pročitaj i sačuvaj nalaze", type="primary", key="lab_analyze"):
                for i, img in enumerate(lab_imgs, 1):
                    if len(lab_imgs) > 1:
                        st.markdown(f"**🖼️ Prilog {i} / {len(lab_imgs)}**")
                    try:
                        block, ocr_text = prepare_media(img)
                        with st.spinner(f"AI čita nalaz… ({i})"):
                            res = smart_analyze(block, ocr_text, model_id, api_key)
                        doc = res.get("document")
                        if doc and doc.get("lab_results"):
                            _store_and_show_doc(doc)
                        else:
                            st.warning(f"Prilog {i}: nisam prepoznao lab parametre. "
                                       f"{res.get('notes') or ''} Probaj jasniji prilog.")
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Greška na prilogu {i}: {e}")


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
