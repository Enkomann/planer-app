from flask import Flask, request, redirect, render_template_string, session, send_file, url_for
import sqlite3
import re
import io
import os
import calendar
from datetime import datetime, timedelta, date as dt_date
from zoneinfo import ZoneInfo

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "luxmann_secret_key")

DEFAULT_WORKER_COLORS = {"admin": "#1f4f82", "worker1": "#16a34a"}
STATUS_COLORS = {
    "planned": "#f59e0b",      # Planirano - narandzasto
    "in_progress": "#16a34a",  # U toku - zeleno
    "done": "#ef4444",         # Zavrseno - crveno
}
ABSENCE_COLORS = {"sick": "#ef4444", "vacation": "#8b5cf6", "other": "#64748b"}

TRANSLATIONS = {
    "bos": {
        "login_title": "Prijava", "username": "Korisnicko ime", "password": "Lozinka",
        "login_btn": "Prijava", "login_error": "Pogresno korisnicko ime ili lozinka",
        "title": "PLAN RADNIKA", "logged_as": "Logovan kao", "logout": "Odjava",
        "add_worker": "Dodaj radnika", "add_client": "Dodaj klijenta", "add_shift": "Dodaj smjenu",
        "worker_name": "Ime radnika", "client_name": "Naziv klijenta", "address": "Adresa",
        "choose_worker": "Izaberi radnike", "choose_client": "Izaberi klijenta",
        "filter_btn": "Filtriraj", "reset": "Reset", "plan": "PLAN",
        "no_shifts": "Trenutno nema unesenih smjena.", "edit": "Izmijeni", "delete": "Obrisi",
        "copy": "Copy", "copy_shift": "Kopiraj smjenu", "paste": "+ Paste",
        "week_calendar": "Sedmicni kalendar", "month_calendar": "Mjesecni kalendar", "pdf": "PDF raspored",
        "month_pdf": "PDF mjesecni kalendar", "back": "Nazad", "edit_shift": "Izmijeni smjenu", "save": "Sacuvaj",
        "pdf_title": "Raspored radnika", "pdf_user": "Korisnik", "pdf_date": "Datum",
        "pdf_time": "Vrijeme", "pdf_worker": "Radnici", "pdf_client": "Klijent",
        "pdf_no_shifts": "Nema smjena", "user_mgmt": "Upravljanje korisnicima",
        "add_user": "Dodaj korisnika", "role_admin": "admin", "role_worker": "worker",
        "existing_users": "Postojeci korisnici", "delete_user": "Obrisi korisnika",
        "status": "Status", "status_planned": "Planirano", "status_in_progress": "U toku",
        "status_done": "Zavrseno", "weekly_hours": "Nedeljni sati",
        "monthly_hours": "Mjesecni sati", "monthly_absence_days": "Mjesecni dani odsustva",
        "hours": "sati", "days": "dana", "all_workers": "Svi radnici",
        "all_clients": "Svi klijenti", "theme": "Tema", "light_theme": "Svijetla",
        "dark_theme": "Tamna", "worker_colors": "Boje radnika",
        "update_color": "Azuriraj boju", "prev_month": "Prosli mjesec",
        "next_month": "Sljedeci mjesec", "prev_week": "Prethodna sedmica",
        "next_week": "Sljedeca sedmica", "current_week": "Trenutna sedmica",
        "change_password": "Promijeni lozinku", "new_password": "Nova lozinka",
        "search_shifts": "Pretraga smjena",
        "search_placeholder": "Pretrazi po klijentu, radniku, vremenu...",
        "week_period": "Period", "workers": "Radnici", "clients": "Klijenti", "menu": "Menu",
        "start_time": "Pocetak", "end_time": "Kraj", "team": "Radnici zajedno",
        "add_holiday": "Dodaj praznik / neradni dan", "holiday_name": "Naziv praznika", "holiday": "Praznik",
        "sick_vacation": "Bolovanje / Odmor", "absence_type": "Vrsta odsustva", "sick": "Bolovanje",
        "vacation": "Odmor", "other_absence": "Drugo", "date_from": "Od datuma", "date_to": "Do datuma",
        "note": "Napomena", "add_absence": "Dodaj odsustvo", "active_absences": "Upisana odsustva",
        "monday": "Pon", "tuesday": "Uto", "wednesday": "Sri", "thursday": "Cet",
        "friday": "Pet", "saturday": "Sub", "sunday": "Ned", "cancel": "Odustani",
    }
}

# Keep previous language buttons. If a translation is missing, Bosnian text is used as fallback.
for lang in ["fr", "en", "de", "pt"]:
    TRANSLATIONS[lang] = TRANSLATIONS["bos"].copy()

TRANSLATIONS["fr"].update({
    "login_title": "Connexion", "login_btn": "Connexion", "logout": "Deconnexion",
    "title": "PLAN DE TRAVAIL", "add_worker": "Ajouter employe", "add_client": "Ajouter client",
    "add_shift": "Ajouter mission", "workers": "Employes", "clients": "Clients",
    "week_calendar": "Calendrier hebdomadaire", "month_calendar": "Calendrier mensuel",
    "monthly_hours": "Heures mensuelles", "weekly_hours": "Heures hebdomadaires",
    "back": "Retour", "save": "Enregistrer", "delete": "Supprimer", "edit": "Modifier",
    "status_planned": "Planifié", "status_in_progress": "En cours", "status_done": "Terminé",
    "sick": "Maladie", "vacation": "Conge", "sick_vacation": "Maladie / Conge",
})
TRANSLATIONS["en"].update({
    "login_title": "Login", "login_btn": "Login", "logout": "Logout",
    "title": "WORK SCHEDULE", "add_worker": "Add worker", "add_client": "Add client",
    "add_shift": "Add shift", "workers": "Workers", "clients": "Clients",
    "week_calendar": "Weekly calendar", "month_calendar": "Monthly calendar",
    "monthly_hours": "Monthly hours", "weekly_hours": "Weekly hours",
    "back": "Back", "save": "Save", "delete": "Delete", "edit": "Edit",
    "status_planned": "Planned", "status_in_progress": "In progress", "status_done": "Done",
    "sick": "Sick leave", "vacation": "Vacation", "sick_vacation": "Sick leave / Vacation",
})
TRANSLATIONS["de"].update({
    "login_title": "Anmeldung", "login_btn": "Anmelden", "logout": "Abmelden",
    "title": "ARBEITSPLAN", "add_worker": "Mitarbeiter hinzufugen", "add_client": "Kunde hinzufugen",
    "add_shift": "Einsatz hinzufugen", "workers": "Mitarbeiter", "clients": "Kunden",
    "week_calendar": "Wochenkalender", "month_calendar": "Monatskalender",
    "monthly_hours": "Monatsstunden", "weekly_hours": "Wochenstunden",
    "back": "Zuruck", "save": "Speichern", "delete": "Loschen", "edit": "Bearbeiten",
    "status_planned": "Geplant", "status_in_progress": "In Arbeit", "status_done": "Fertig",
    "sick": "Krankheit", "vacation": "Urlaub", "sick_vacation": "Krankheit / Urlaub",
})
TRANSLATIONS["pt"].update({
    "login_title": "Entrar", "login_btn": "Entrar", "logout": "Sair",
    "title": "PLANO DE TRABALHO", "add_worker": "Adicionar trabalhador", "add_client": "Adicionar cliente",
    "add_shift": "Adicionar turno", "workers": "Trabalhadores", "clients": "Clientes",
    "week_calendar": "Calendario semanal", "month_calendar": "Calendario mensal",
    "monthly_hours": "Horas mensais", "weekly_hours": "Horas semanais",
    "back": "Voltar", "save": "Guardar", "delete": "Apagar", "edit": "Editar",
    "status_planned": "Planeado", "status_in_progress": "Em andamento", "status_done": "Concluído",
    "sick": "Baixa medica", "vacation": "Ferias", "sick_vacation": "Baixa / Ferias",
})


def get_lang():
    return session.get("lang", "bos")


def t():
    return TRANSLATIONS.get(get_lang(), TRANSLATIONS["bos"])


def get_theme():
    return session.get("theme", "light")


class _PgCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self._fake_rows = None

    def _translate(self, query):
        q = query
        q = q.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")

        # SQLite -> PostgreSQL syntax fixes
        m = re.match(r"\s*PRAGMA\s+table_info\((\w+)\)\s*", q, re.IGNORECASE)
        if m:
            table = m.group(1)
            self.cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            cols = self.cursor.fetchall()
            self._fake_rows = [(None, row[0]) for row in cols]
            return None

        q = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", q, flags=re.IGNORECASE)

        # Specific SQLite UPSERT replacements
        if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+holidays", query, re.IGNORECASE):
            q = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", query, flags=re.IGNORECASE)
            q += " ON CONFLICT(date) DO UPDATE SET name = EXCLUDED.name"
        elif re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+worker_colors", query, re.IGNORECASE):
            q = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", query, flags=re.IGNORECASE)
            q += " ON CONFLICT(worker_name) DO UPDATE SET color = EXCLUDED.color"
        elif re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", query, re.IGNORECASE):
            q += " ON CONFLICT DO NOTHING"

        q = q.replace("?", "%s")
        return q

    def execute(self, query, params=()):
        self._fake_rows = None
        translated = self._translate(query)
        if translated is not None:
            self.cursor.execute(translated, params)
        return self

    def fetchall(self):
        if self._fake_rows is not None:
            return self._fake_rows
        return self.cursor.fetchall()

    def fetchone(self):
        if self._fake_rows is not None:
            return self._fake_rows[0] if self._fake_rows else None
        return self.cursor.fetchone()


class _PgConn:
    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return _PgCursor(self.conn.cursor())

    def commit(self):
        return self.conn.commit()

    def close(self):
        return self.conn.close()


def get_conn():
    if USE_POSTGRES:
        # Render PostgreSQL provides DATABASE_URL. Internal URL is recommended when the DB
        # and web service are in the same Render account/region.
        return _PgConn(psycopg2.connect(DATABASE_URL))
    return sqlite3.connect("db.sqlite")


def format_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return date_str


def split_workers(worker_text):
    if not worker_text:
        return []
    return [w.strip() for w in worker_text.split(",") if w.strip()]


def join_workers(worker_list):
    return ", ".join([w.strip() for w in worker_list if w.strip()])


def worker_in_shift(worker_name, worker_text):
    return worker_name in split_workers(worker_text)


def replace_worker_in_shift(worker_text, old_name, new_name):
    return join_workers([new_name if n == old_name else n for n in split_workers(worker_text)])


def remove_worker_from_shift(worker_text, name):
    return join_workers([n for n in split_workers(worker_text) if n != name])


def get_status_label(status_key, tr):
    if status_key == "planned":
        return tr.get("status_planned", "Planirano")
    if status_key == "in_progress":
        return tr.get("status_in_progress", "U toku")
    if status_key == "done":
        return tr.get("status_done", "Završeno")
    return status_key


def split_time_range(time_range):
    if "-" in time_range:
        parts = time_range.split("-")
        return parts[0].strip(), parts[1].strip()
    return "", ""


def split_hour_min(time_value):
    try:
        h, m = time_value.split(":")
        return h.zfill(2), m.zfill(2)
    except Exception:
        return "08", "00"


def time_hours():
    return [f"{h:02d}" for h in range(24)]


def time_minutes():
    return ["00", "15", "30", "45"]


def is_weekend(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").weekday() >= 5
    except Exception:
        return False


def parse_shift_hours(time_str):
    try:
        start_str, end_str = [x.strip() for x in time_str.split("-")]
        start = datetime.strptime(start_str, "%H:%M")
        end = datetime.strptime(end_str, "%H:%M")
        return max((end - start).total_seconds() / 3600, 0.0)
    except Exception:
        return 0.0


def get_auto_status(shift_date, time_range):
    """Automatski status po datumu i vremenu smjene, po vremenu u Luksemburgu."""
    try:
        start_str, end_str = [x.strip() for x in time_range.split("-")]

        lux_tz = ZoneInfo("Europe/Luxembourg")
        start_dt = datetime.strptime(f"{shift_date} {start_str}", "%Y-%m-%d %H:%M").replace(tzinfo=lux_tz)
        end_dt = datetime.strptime(f"{shift_date} {end_str}", "%Y-%m-%d %H:%M").replace(tzinfo=lux_tz)
        now = datetime.now(lux_tz)

        if now < start_dt:
            return "planned"
        if start_dt <= now <= end_dt:
            return "in_progress"
        return "done"
    except Exception:
        return "planned"


def calculate_hours_for_user(shifts, username=None):
    totals = {}
    for s in shifts:
        hours = parse_shift_hours(s[4])
        for worker in split_workers(s[1]):
            if username and worker != username:
                continue
            totals[worker] = totals.get(worker, 0.0) + hours
    return totals


def get_worker_colors(conn):
    c = conn.cursor()
    rows = c.execute("SELECT worker_name, color FROM worker_colors").fetchall()
    colors_map = DEFAULT_WORKER_COLORS.copy()
    for worker_name, color in rows:
        colors_map[worker_name] = color
    return colors_map


def month_navigation(year, month, delta):
    if delta == -1:
        return (year - 1, 12) if month == 1 else (year, month - 1)
    return (year + 1, 1) if month == 12 else (year, month + 1)


def get_week_start_from_request():
    week_start_str = request.args.get("start", "").strip()
    if week_start_str:
        try:
            d = datetime.strptime(week_start_str, "%Y-%m-%d")
            return d - timedelta(days=d.weekday())
        except Exception:
            pass
    today = datetime.today()
    return today - timedelta(days=today.weekday())


def easter_date(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime(year, month, day)


def lux_holidays_for_year(year):
    easter = easter_date(year)
    return {
        f"{year}-01-01": "Nouvel An",
        (easter + timedelta(days=1)).strftime("%Y-%m-%d"): "Lundi de Paques",
        f"{year}-05-01": "Fete du Travail",
        f"{year}-05-09": "Jour de l'Europe",
        (easter + timedelta(days=39)).strftime("%Y-%m-%d"): "Ascension",
        (easter + timedelta(days=50)).strftime("%Y-%m-%d"): "Lundi de Pentecote",
        f"{year}-06-23": "Fete nationale",
        f"{year}-08-15": "Assomption",
        f"{year}-11-01": "Toussaint",
        f"{year}-12-25": "Noel",
        f"{year}-12-26": "Saint Etienne",
    }


def get_custom_holidays(conn):
    c = conn.cursor()
    return {row[0]: row[1] for row in c.execute("SELECT date, name FROM holidays").fetchall()}


def get_all_holidays(conn, years):
    holidays = {}
    for y in years:
        holidays.update(lux_holidays_for_year(y))
    holidays.update(get_custom_holidays(conn))
    return holidays


def group_shifts_by_week(shifts):
    weeks = {}
    for s in shifts:
        try:
            d = datetime.strptime(s[3], "%Y-%m-%d")
            week_start = d - timedelta(days=d.weekday())
            key = week_start.strftime("%Y-%m-%d")
            weeks.setdefault(key, []).append(s)
        except Exception:
            pass
    return dict(sorted(weeks.items()))


def absence_days_in_month(absence, year, month):
    # absence row: id, worker, type, date_from, date_to, note
    try:
        start = datetime.strptime(absence[3], "%Y-%m-%d").date()
        end = datetime.strptime(absence[4], "%Y-%m-%d").date()
        month_start = dt_date(year, month, 1)
        month_end = dt_date(year, month, calendar.monthrange(year, month)[1])
        real_start = max(start, month_start)
        real_end = min(end, month_end)
        if real_end < real_start:
            return 0
        return (real_end - real_start).days + 1
    except Exception:
        return 0


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            address TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            address TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker TEXT,
            client TEXT,
            date TEXT,
            time TEXT,
            status TEXT DEFAULT 'planned'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS worker_colors (
            worker_name TEXT PRIMARY KEY,
            color TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS holidays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            name TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS absences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker TEXT,
            type TEXT,
            date_from TEXT,
            date_to TEXT,
            note TEXT DEFAULT ''
        )
    """)

    # Migration safety
    shift_cols = [row[1] for row in c.execute("PRAGMA table_info(shifts)").fetchall()]
    if "status" not in shift_cols:
        c.execute("ALTER TABLE shifts ADD COLUMN status TEXT DEFAULT 'planned'")
    worker_cols = [row[1] for row in c.execute("PRAGMA table_info(workers)").fetchall()]
    if "address" not in worker_cols:
        c.execute("ALTER TABLE workers ADD COLUMN address TEXT DEFAULT ''")
    client_cols = [row[1] for row in c.execute("PRAGMA table_info(clients)").fetchall()]
    if "address" not in client_cols:
        c.execute("ALTER TABLE clients ADD COLUMN address TEXT DEFAULT ''")

    c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", ("admin", "admin123", "admin"))
    c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", ("worker1", "1234", "worker"))
    c.execute("INSERT OR IGNORE INTO workers (name, address) VALUES (?, ?)", ("admin", ""))
    c.execute("INSERT OR IGNORE INTO workers (name, address) VALUES (?, ?)", ("worker1", ""))

    for worker_name, color in DEFAULT_WORKER_COLORS.items():
        c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (worker_name, color))

    conn.commit()
    conn.close()


init_db()


BASE_STYLE = """
<style>
    body { font-family: Arial, sans-serif; margin:24px; background: {{ '#0f172a' if dark else '#f4f6f8' }}; color: {{ '#e5e7eb' if dark else '#1f2937' }}; }
    h1 { color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
    h2, h3, h4 { color: {{ '#e5e7eb' if dark else '#111827' }}; }
    .brandbar, .card { background: {{ '#111827' if dark else 'white' }}; border-radius:12px; box-shadow:0 4px 14px rgba(0,0,0,0.06); }
    .brandbar { display:flex; justify-content:space-between; align-items:center; padding:14px 18px; margin-bottom:18px; }
    .brandleft { display:flex; align-items:center; gap:14px; }
    .brandleft img { height:56px; }
    .brandtitle { font-size:24px; font-weight:700; color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
    .langbar a, .topbar a, .theme-links a, .week-link, .pdf-link, .reset-link, a { color: {{ '#93c5fd' if dark else '#1f4f82' }}; text-decoration:none; font-weight:bold; margin-right:10px; }
    .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:16px; }
    .card { padding:18px; }
    input, select, button { padding:10px; margin:6px 0; width:100%; box-sizing:border-box; border:1px solid {{ '#374151' if dark else '#cbd5e1' }}; border-radius:8px; background: {{ '#1f2937' if dark else 'white' }}; color: {{ '#e5e7eb' if dark else '#111827' }}; }
    button { background:#1f4f82; color:white; border:none; cursor:pointer; }
    .shift { background: {{ 'linear-gradient(135deg, #111827, #1f2937)' if dark else 'linear-gradient(135deg, #ffffff, #f1f5f9)' }}; padding:14px; margin:12px 0; border-radius:12px; box-shadow:0 4px 14px rgba(0,0,0,0.06); }
    .mini-shift { margin-top:6px; padding:6px; border-radius:8px; font-size:12px; background: {{ '#1f2937' if dark else '#f8fafc' }}; }
    .user-row, .hours-row { padding:8px 0; border-bottom:1px solid {{ '#374151' if dark else '#e5e7eb' }}; }
    .muted { color: {{ '#9ca3af' if dark else '#64748b' }}; font-size:14px; }
    .status-badge { color:white; padding:4px 8px; border-radius:6px; font-size:12px; font-weight:bold; margin-left:8px; }
    .action-link, .mini-link { text-decoration:none; margin-left:10px; font-weight:bold; font-size:12px; }
    .edit-link { color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
    .delete-link { color:#ef4444; }
    .copy-link { color:#16a34a; }
    .check-row { display:flex; align-items:center; gap:8px; margin:5px 0; }
    .check-row input { width:auto; }
    .weekend-soft { border:2px solid #ef4444 !important; background:{{ '#3f1f1f' if dark else '#fff1f1' }} !important; }
    .holiday-soft { background:{{ '#3f2f12' if dark else '#fff7df' }} !important; border:2px solid #f59e0b !important; }
    .holiday-note { display:block; color:#dc2626; font-size:11px; margin-top:4px; font-weight:bold; }
    .drop-target { outline:2px dashed #22c55e; }
    .modal-backdrop { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.45); z-index:50; }
    .modal-card { max-width:420px; margin:12vh auto; background:{{ '#111827' if dark else 'white' }}; color:{{ '#e5e7eb' if dark else '#111827' }}; border-radius:12px; padding:20px; box-shadow:0 10px 30px rgba(0,0,0,0.25); }
</style>
"""


def header_html():
    return """
    <div class="brandbar">
        <div class="brandleft">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="Luxmann Logo">
            <div class="brandtitle">Luxmann Planner</div>
        </div>
        <div>
            <div class="langbar">
                <a href="/set_lang/fr">FR</a><a href="/set_lang/en">EN</a><a href="/set_lang/bos">BOS</a><a href="/set_lang/de">DE</a><a href="/set_lang/pt">PT</a>
            </div>
            <div class="theme-links" style="text-align:right; margin-top:8px;">
                {{ tr["theme"] }}: <a href="/set_theme/light">{{ tr["light_theme"] }}</a><a href="/set_theme/dark">{{ tr["dark_theme"] }}</a>
            </div>
        </div>
    </div>
    """


@app.route("/set_lang/<lang>")
def set_lang(lang):
    if lang in TRANSLATIONS:
        session["lang"] = lang
    return redirect(request.referrer or "/")


@app.route("/set_theme/<theme>")
def set_theme(theme):
    if theme in ("light", "dark"):
        session["theme"] = theme
    return redirect(request.referrer or "/")


@app.route("/login", methods=["GET", "POST"])
def login():
    tr = t()
    dark = get_theme() == "dark"
    error = ""
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        conn = get_conn()
        c = conn.cursor()
        user = c.execute("SELECT username, password, role FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        if user and user[1] == password:
            session["user"] = user[0]
            session["role"] = user[2]
            return redirect("/")
        error = tr["login_error"]

    return render_template_string(BASE_STYLE + """
    <div class="langbar" style="max-width:420px; margin:0 auto 12px auto; text-align:right;">
        <a href="/set_lang/fr">FR</a><a href="/set_lang/en">EN</a><a href="/set_lang/bos">BOS</a><a href="/set_lang/de">DE</a><a href="/set_lang/pt">PT</a>
    </div>
    <div class="card" style="max-width:420px; margin:auto; text-align:center; padding:30px;">
        <img src="{{ url_for('static', filename='logo.png') }}" alt="Luxmann Logo" style="height:70px; margin-bottom:12px;">
        <h2>{{ tr["login_title"] }}</h2>
        <form method="post">
            <input name="username" placeholder="{{ tr['username'] }}" required>
            <input name="password" type="password" placeholder="{{ tr['password'] }}" required>
            <button type="submit">{{ tr["login_btn"] }}</button>
        </form>
        {% if error %}<div style="color:#ef4444; margin-top:10px;">{{ error }}</div>{% endif %}
    </div>
    """, tr=tr, error=error, dark=dark)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/change_password", methods=["POST"])
def change_password():
    if "user" not in session or session.get("role") != "admin":
        return redirect("/")
    new_password = request.form["new_password"].strip()
    if new_password:
        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE users SET password = ? WHERE username = ?", (new_password, session["user"]))
        conn.commit()
        conn.close()
    return redirect("/")


def load_index_data():
    is_admin = session.get("role") == "admin"
    current_user = session.get("user")
    conn = get_conn()
    c = conn.cursor()
    workers = c.execute("SELECT name, address FROM workers ORDER BY name").fetchall()
    clients = c.execute("SELECT name, address FROM clients ORDER BY name").fetchall()
    db_users = c.execute("SELECT id, username, role FROM users ORDER BY username").fetchall() if is_admin else []
    worker_colors = get_worker_colors(conn)

    date_filter = request.args.get("date", "").strip()
    selected_date = request.args.get("selected_date", "").strip()
    worker_filter = request.args.get("worker", "").strip() if is_admin else current_user
    client_filter = request.args.get("client", "").strip()
    search_query = request.args.get("q", "").strip().lower()

    base_query = "SELECT * FROM shifts WHERE 1=1"
    params = []
    if date_filter:
        base_query += " AND date = ?"
        params.append(date_filter)
    if is_admin and client_filter:
        base_query += " AND client = ?"
        params.append(client_filter)
    base_query += " ORDER BY date, time"
    all_loaded_shifts = c.execute(base_query, tuple(params)).fetchall()

    shifts = []
    for s in all_loaded_shifts:
        if not is_admin and not worker_in_shift(current_user, s[1]):
            continue
        if is_admin and worker_filter and not worker_in_shift(worker_filter, s[1]):
            continue
        if search_query and search_query not in f"{s[1]} {s[2]} {s[3]} {s[4]} {s[5]}".lower():
            continue
        shifts.append(s)

    today = datetime.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    all_shifts_for_hours = c.execute("SELECT * FROM shifts").fetchall()
    if not is_admin:
        all_shifts_for_hours = [s for s in all_shifts_for_hours if worker_in_shift(current_user, s[1])]

    week_shifts, month_shifts = [], []
    for s in all_shifts_for_hours:
        try:
            d = datetime.strptime(s[3], "%Y-%m-%d")
            if week_start.date() <= d.date() <= week_end.date():
                week_shifts.append(s)
            if d.year == today.year and d.month == today.month:
                month_shifts.append(s)
        except Exception:
            pass

    absences = c.execute("SELECT id, worker, type, date_from, date_to, note FROM absences ORDER BY date_from DESC").fetchall() if is_admin else c.execute("SELECT id, worker, type, date_from, date_to, note FROM absences WHERE worker = ? ORDER BY date_from DESC", (current_user,)).fetchall()
    absence_summary = []
    for a in absences:
        days = absence_days_in_month(a, today.year, today.month)
        if days > 0:
            absence_summary.append((a, days))

    conn.close()
    return {
        "is_admin": is_admin, "current_user": current_user, "workers": workers, "clients": clients,
        "db_users": db_users, "worker_colors": worker_colors, "shifts": shifts,
        "selected_date": selected_date, "worker_filter": worker_filter, "client_filter": client_filter,
        "weekly_hours": calculate_hours_for_user(week_shifts, None if is_admin else current_user),
        "monthly_hours": calculate_hours_for_user(month_shifts, None if is_admin else current_user),
        "week_period": f"{format_date(week_start.strftime('%Y-%m-%d'))} - {format_date(week_end.strftime('%Y-%m-%d'))}",
        "month_period": today.strftime("%m/%Y"), "weeks_grouped": group_shifts_by_week(shifts),
        "absences": absences, "absence_summary": absence_summary,
    }


@app.route("/")
def index():
    if "user" not in session:
        return redirect("/login")
    tr = t()
    dark = get_theme() == "dark"
    data = load_index_data()

    return render_template_string(BASE_STYLE + header_html() + """
    <h1>{{ tr["title"] }}</h1>
    <div class="topbar">{{ tr["logged_as"] }}: <b>{{ session['user'] }}</b> ({{ session['role'] }})<br><br><a href="/logout">{{ tr["logout"] }}</a></div>

    <div class="grid">
        {% if is_admin %}
        <div class="card" style="grid-column:1/-1;">
            <button onclick="toggleMenu()" type="button">☰ {{ tr["menu"] }}</button>
            <div id="menuBox" style="display:none; margin-top:15px;">
                <div class="grid">
                    <div class="card"><h3>{{ tr["change_password"] }}</h3><form method="post" action="/change_password"><input name="new_password" type="password" placeholder="{{ tr['new_password'] }}" required><button>{{ tr["save"] }}</button></form></div>
                    <div class="card"><h3>{{ tr["user_mgmt"] }}</h3><form method="post" action="/add_user"><input name="username" placeholder="{{ tr['username'] }}" required><input name="password" placeholder="{{ tr['password'] }}" required><select name="role"><option value="admin">{{ tr['role_admin'] }}</option><option value="worker">{{ tr['role_worker'] }}</option></select><button>{{ tr["add_user"] }}</button></form></div>
                    <div class="card"><h3>{{ tr["existing_users"] }}</h3>{% for u in db_users %}<div class="user-row"><b>{{ u[1] }}</b> ({{ u[2] }}){% if u[1] != 'admin' %}<a class="delete-link" href="/delete_user/{{ u[0] }}">{{ tr["delete"] }}</a>{% endif %}</div>{% endfor %}</div>
                    <div class="card"><h3>{{ tr["worker_colors"] }}</h3>{% for w in workers %}<form method="post" action="/update_worker_color"><input type="hidden" name="worker_name" value="{{ w[0] }}"><div style="display:flex; gap:10px; align-items:center;"><div style="min-width:110px;">{{ w[0] }}</div><input type="color" name="color" value="{{ worker_colors.get(w[0], '#1f4f82') }}"><button>{{ tr["update_color"] }}</button></div></form>{% endfor %}</div>
                    <div class="card"><h3>{{ tr["workers"] }}</h3>{% for w in workers %}<div class="user-row"><b>{{ w[0] }}</b><br><small>{{ w[1] }}</small><br><a class="edit-link" href="/edit_worker/{{ w[0] }}">{{ tr["edit"] }}</a>{% if w[0] != 'admin' %}<a class="delete-link" href="/delete_worker/{{ w[0] }}">{{ tr["delete"] }}</a>{% endif %}</div>{% endfor %}</div>
                    <div class="card"><h3>{{ tr["clients"] }}</h3>{% for c in clients %}<div class="user-row"><b>{{ c[0] }}</b><br><small>{{ c[1] }}</small><br><a class="edit-link" href="/edit_client/{{ c[0] }}">{{ tr["edit"] }}</a><a class="delete-link" href="/delete_client/{{ c[0] }}">{{ tr["delete"] }}</a></div>{% endfor %}</div>
                </div>
            </div>
        </div>

        <div class="card"><h3>{{ tr["add_worker"] }}</h3><form method="post" action="/add_worker" autocomplete="off"><input name="worker_name" placeholder="{{ tr['worker_name'] }}" required autocomplete="off"><input name="address" placeholder="{{ tr['address'] }}" autocomplete="off"><button>{{ tr["add_worker"] }}</button></form></div>
        <div class="card"><h3>{{ tr["add_client"] }}</h3><form method="post" action="/add_client" autocomplete="off"><input name="client_name" placeholder="{{ tr['client_name'] }}" required autocomplete="off"><input name="address" placeholder="{{ tr['address'] }}" autocomplete="off"><button>{{ tr["add_client"] }}</button></form></div>

        <div class="card">
            <h3>{{ tr["add_shift"] }}</h3>
            <form method="post" action="/add_shift">
                <label>{{ tr["choose_worker"] }}</label>
                {% for w in workers %}{% if w[0] != 'admin' %}<label class="check-row"><input type="checkbox" name="workers" value="{{ w[0] }}">{{ w[0] }}</label>{% endif %}{% endfor %}
                <select name="client" required><option value="">{{ tr["choose_client"] }}</option>{% for c in clients %}<option value="{{ c[0] }}">{{ c[0] }}</option>{% endfor %}</select>
                <input name="date" type="date" value="{{ selected_date }}" required>
                <label>{{ tr["start_time"] }}</label>
                <div style="display:flex; gap:6px;"><select name="start_hour">{% for h in time_hours %}<option value="{{ h }}">{{ h }}</option>{% endfor %}</select><select name="start_minute"><option value="00" selected>00</option><option value="15">15</option><option value="30">30</option><option value="45">45</option></select></div>
                <label>{{ tr["end_time"] }}</label>
                <div style="display:flex; gap:6px;"><select name="end_hour">{% for h in time_hours %}<option value="{{ h }}">{{ h }}</option>{% endfor %}</select><select name="end_minute"><option value="00" selected>00</option><option value="15">15</option><option value="30">30</option><option value="45">45</option></select></div>
                <select name="status" required><option value="planned">{{ tr["status_planned"] }}</option><option value="in_progress">{{ tr["status_in_progress"] }}</option><option value="done">{{ tr["status_done"] }}</option></select>
                <button>{{ tr["add_shift"] }}</button>
            </form>
        </div>

        <div class="card">
            <h3>{{ tr["search_shifts"] }}</h3>
            <form method="get"><input type="date" name="date" value="{{ request.args.get('date', '') }}"><select name="worker"><option value="">{{ tr["all_workers"] }}</option>{% for w in workers %}<option value="{{ w[0] }}" {% if worker_filter == w[0] %}selected{% endif %}>{{ w[0] }}</option>{% endfor %}</select><select name="client"><option value="">{{ tr["all_clients"] }}</option>{% for c in clients %}<option value="{{ c[0] }}" {% if client_filter == c[0] %}selected{% endif %}>{{ c[0] }}</option>{% endfor %}</select><input name="q" value="{{ request.args.get('q', '') }}" placeholder="{{ tr['search_placeholder'] }}"><button>{{ tr["filter_btn"] }}</button></form><a class="reset-link" href="/">{{ tr["reset"] }}</a>
        </div>

        <div class="card">
            <h3>{{ tr["sick_vacation"] }}</h3>
            <form method="post" action="/add_absence">
                <select name="worker" required><option value="">{{ tr["choose_worker"] }}</option>{% for w in workers %}{% if w[0] != 'admin' %}<option value="{{ w[0] }}">{{ w[0] }}</option>{% endif %}{% endfor %}</select>
                <select name="type"><option value="sick">{{ tr["sick"] }}</option><option value="vacation">{{ tr["vacation"] }}</option><option value="other">{{ tr["other_absence"] }}</option></select>
                <label>{{ tr["date_from"] }}</label><input type="date" name="date_from" required>
                <label>{{ tr["date_to"] }}</label><input type="date" name="date_to" required>
                <input name="note" placeholder="{{ tr['note'] }}"><button>{{ tr["add_absence"] }}</button>
            </form>
            <h4>{{ tr["active_absences"] }}</h4>
            {% for a in absences[:8] %}<div class="user-row"><b>{{ a[1] }}</b> - {{ tr.get(a[2], a[2]) }}<br><small>{{ format_date(a[3]) }} - {{ format_date(a[4]) }} {{ a[5] }}</small><a class="delete-link" href="/delete_absence/{{ a[0] }}">{{ tr["delete"] }}</a></div>{% endfor %}
        </div>
        {% endif %}

        <div class="card"><h3>{{ tr["weekly_hours"] }}</h3><div class="muted">{{ tr["week_period"] }}: {{ week_period }}</div>{% for worker, hours in weekly_hours.items() %}<div class="hours-row"><span>{{ worker }}</span><span>{{ "%.2f"|format(hours) }} {{ tr["hours"] }}</span></div>{% endfor %}{% if weekly_hours|length == 0 %}<div class="muted">0 {{ tr["hours"] }}</div>{% endif %}</div>
        <div class="card"><h3>{{ tr["monthly_hours"] }}</h3><div class="muted">{{ month_period }}</div>{% for worker, hours in monthly_hours.items() %}<div class="hours-row"><span>{{ worker }}</span><span>{{ "%.2f"|format(hours) }} {{ tr["hours"] }}</span></div>{% endfor %}{% if monthly_hours|length == 0 %}<div class="muted">0 {{ tr["hours"] }}</div>{% endif %}<br><a class="pdf-link" href="/month_pdf" target="_blank">{{ tr["month_pdf"] }}</a></div>
        <div class="card"><h3>{{ tr["monthly_absence_days"] }}</h3><div class="muted">{{ month_period }}</div>{% for a, days in absence_summary %}<div class="hours-row"><b>{{ a[1] }}</b> - {{ tr.get(a[2], a[2]) }}: {{ days }} {{ tr["days"] }}<br><small>{{ format_date(a[3]) }} - {{ format_date(a[4]) }}</small></div>{% endfor %}{% if absence_summary|length == 0 %}<div class="muted">0 {{ tr["days"] }}</div>{% endif %}</div>
    </div>

    <div class="card" style="margin-top:20px;">
        <h2>{{ tr["plan"] }}</h2>
        {% if shifts|length == 0 %}<div class="muted">{{ tr["no_shifts"] }}</div>{% endif %}
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(360px, 1fr)); gap:18px;">
        {% for week_start_key, week_shifts in weeks_grouped.items() %}
            {% set week_end_key = (datetime.strptime(week_start_key, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d") %}
            <div class="card" style="padding:12px;"><h3 style="border-bottom:2px solid #1f4f82; padding-bottom:8px; margin-top:0;">{{ format_date(week_start_key) }} - {{ format_date(week_end_key) }}</h3>
            {% for s in week_shifts %}{% set auto_status = get_auto_status(s[3], s[4]) %}<div class="shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="border-left:6px solid {{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#1f4f82') }}"><b>{{ format_date(s[3]) }}</b> | {{ s[4] }}<span class="status-badge" style="background:{{ status_colors.get(auto_status, '#6b7280') }};">{{ get_status_label(auto_status, tr) }}</span><br><br><b>{{ tr["team"] }}:</b> {{ s[1] }}<br><b>{{ tr["pdf_client"] }}:</b> {{ s[2] }}{% if is_admin %}<a class="action-link edit-link" href="/edit_shift/{{ s[0] }}">{{ tr["edit"] }}</a><a class="action-link delete-link" href="/delete_shift/{{ s[0] }}">{{ tr["delete"] }}</a><a class="action-link copy-link" href="/copy_shift/{{ s[0] }}">{{ tr["copy"] }}</a>{% endif %}</div>{% endfor %}</div>
        {% endfor %}
        </div>
        <a class="week-link" href="/week">{{ tr["week_calendar"] }}</a><a class="week-link" href="/month">{{ tr["month_calendar"] }}</a><a class="pdf-link" href="/export_pdf{% if request.args.get('date') %}?date={{ request.args.get('date') }}{% endif %}" target="_blank">{{ tr["pdf"] }}</a>
    </div>

    <script>
    function toggleMenu(){var m=document.getElementById('menuBox');m.style.display=(m.style.display==='none')?'block':'none';}
    function dragShift(ev, shiftId){ev.dataTransfer.setData('shift_id', shiftId);}
    // Automatski osvježi stranicu da se status i boja promijene bez ručnog refresh-a.
    setInterval(function() {
        window.location.reload();
    }, 30000);
    </script>
    """, tr=tr, dark=dark, datetime=datetime, timedelta=timedelta, format_date=format_date,
       time_hours=time_hours(), time_minutes=time_minutes(), status_colors=STATUS_COLORS,
       get_status_label=get_status_label, get_auto_status=get_auto_status, split_workers=split_workers, **data)


@app.route("/copy_shift/<int:id>")
def copy_shift(id):
    if "user" not in session or session.get("role") != "admin":
        return redirect("/")
    session["copied_shift_id"] = id
    return redirect("/month")


@app.route("/paste_shift/<date>")
def paste_shift(date):
    if "user" not in session or session.get("role") != "admin":
        return redirect("/")
    copied_id = session.get("copied_shift_id")
    if not copied_id:
        return redirect("/month")
    conn = get_conn()
    c = conn.cursor()
    original_shift = c.execute("SELECT worker, client, time, status FROM shifts WHERE id = ?", (copied_id,)).fetchone()
    if original_shift:
        worker, client, time, status = original_shift
        c.execute("INSERT INTO shifts (worker, client, date, time, status) VALUES (?, ?, ?, ?, ?)", (worker, client, date, time, status))
        conn.commit()
    conn.close()
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        return redirect(f"/month?year={d.year}&month={d.month}")
    except Exception:
        return redirect("/month")


@app.route("/clear_copy")
def clear_copy():
    if session.get("role") == "admin":
        session.pop("copied_shift_id", None)
    return redirect("/month")


@app.route("/add_holiday", methods=["POST"])
def add_holiday():
    if "user" not in session or session.get("role") != "admin":
        return redirect("/")
    date = request.form.get("date", "").strip()
    name = request.form.get("name", "").strip() or t().get("holiday", "Praznik")
    if date:
        conn = get_conn()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO holidays (date, name) VALUES (?, ?)", (date, name))
        conn.commit()
        conn.close()
    return redirect(request.referrer or "/month")


@app.route("/move_shift", methods=["POST"])
def move_shift():
    if "user" not in session or session.get("role") != "admin":
        return ("Forbidden", 403)
    shift_id = request.form.get("shift_id", "").strip()
    new_date = request.form.get("date", "").strip()
    if not shift_id or not new_date:
        return ("Bad request", 400)
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE shifts SET date = ? WHERE id = ?", (new_date, shift_id))
    conn.commit()
    conn.close()
    return ("OK", 200)


@app.route("/add_absence", methods=["POST"])
def add_absence():
    if session.get("role") != "admin":
        return redirect("/")
    worker = request.form.get("worker", "").strip()
    absence_type = request.form.get("type", "other").strip()
    date_from = request.form.get("date_from", "").strip()
    date_to = request.form.get("date_to", "").strip()
    note = request.form.get("note", "").strip()
    if worker and date_from and date_to:
        conn = get_conn()
        c = conn.cursor()
        c.execute("INSERT INTO absences (worker, type, date_from, date_to, note) VALUES (?, ?, ?, ?, ?)", (worker, absence_type, date_from, date_to, note))
        conn.commit()
        conn.close()
    return redirect("/")


@app.route("/delete_absence/<int:id>")
def delete_absence(id):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM absences WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect("/")


@app.route("/week")
def week_view():
    if "user" not in session:
        return redirect("/login")
    tr = t()
    dark = get_theme() == "dark"
    is_admin = session.get("role") == "admin"
    current_user = session.get("user")
    start_week = get_week_start_from_request()
    week_end = start_week + timedelta(days=6)
    prev_week = (start_week - timedelta(days=7)).strftime("%Y-%m-%d")
    next_week = (start_week + timedelta(days=7)).strftime("%Y-%m-%d")
    current_week = (datetime.today() - timedelta(days=datetime.today().weekday())).strftime("%Y-%m-%d")

    conn = get_conn()
    c = conn.cursor()
    worker_colors = get_worker_colors(conn)
    week_days = [(start_week + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    shifts = c.execute("SELECT * FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time", (week_days[0], week_days[-1])).fetchall()
    if not is_admin:
        shifts = [s for s in shifts if worker_in_shift(current_user, s[1])]
    holidays_map = get_all_holidays(conn, {start_week.year, week_end.year})
    conn.close()
    day_names = [tr["monday"], tr["tuesday"], tr["wednesday"], tr["thursday"], tr["friday"], tr["saturday"], tr["sunday"]]

    return render_template_string(BASE_STYLE + header_html() + """
    <h1>{{ tr["week_calendar"] }}</h1><a href="/">{{ tr["back"] }}</a>
    <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin:16px 0; flex-wrap:wrap;">
        <a href="/week?start={{ prev_week }}">{{ tr["prev_week"] }}</a><strong>{{ format_date(week_days[0]) }} - {{ format_date(week_days[-1]) }}</strong><a href="/week?start={{ next_week }}">{{ tr["next_week"] }}</a><a href="/week?start={{ current_week }}">{{ tr["current_week"] }}</a>
    </div>
    <div style="display:flex; gap:12px; flex-wrap:wrap;">
        {% for day in week_days %}
            {% set holiday_name = holidays_map.get(day) %}
            <div class="card {% if holiday_name %}holiday-soft{% endif %} {% if is_weekend(day) %}weekend-soft{% endif %}" style="width:180px; min-height:130px;" ondragover="allowDrop(event)" ondragleave="clearDrop(event)" ondrop="dropShift(event, '{{ day }}')">
                <a href="{% if is_admin %}javascript:void(0){% else %}/?selected_date={{ day }}{% endif %}" {% if is_admin %}onclick="openHolidayModal('{{ day }}')"{% endif %} style="{% if is_weekend(day) %}color:#ef4444;{% endif %}">{{ day_names[loop.index0] }}<br>{{ format_date(day) }}</a>
                {% if holiday_name %}<small class="holiday-note">{{ holiday_name }}</small>{% endif %}
                {% for s in shifts %}{% if s[3] == day %}<div class="mini-shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="border-left:5px solid {{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#1f4f82') }};">{% set auto_status = get_auto_status(s[3], s[4]) %}<b>{{ s[1] }}</b><br>{{ s[2] }}<br>{{ s[4] }}<br><span class="status-badge" style="background:{{ status_colors.get(auto_status, '#6b7280') }}; margin-left:0; display:inline-block; margin-top:4px;">{{ get_status_label(auto_status, tr) }}</span>{% if is_admin %}<br><a class="mini-link edit-link" href="/edit_shift/{{ s[0] }}">{{ tr["edit"] }}</a><a class="mini-link delete-link" href="/delete_shift/{{ s[0] }}">{{ tr["delete"] }}</a><a class="mini-link copy-link" href="/copy_shift/{{ s[0] }}">{{ tr["copy"] }}</a>{% endif %}</div>{% endif %}{% endfor %}
            </div>
        {% endfor %}
    </div>
    {% if is_admin %}<div id="holidayModal" class="modal-backdrop"><div class="modal-card"><h3>{{ tr["add_holiday"] }}</h3><form method="post" action="/add_holiday"><input type="date" name="date" id="holidayDate" required><input type="text" name="name" placeholder="{{ tr['holiday_name'] }}" required><button>{{ tr["save"] }}</button></form><button type="button" onclick="closeHolidayModal()">{{ tr["cancel"] }}</button></div></div>{% endif %}
    <script>
    function openHolidayModal(dateStr){var m=document.getElementById('holidayModal');var d=document.getElementById('holidayDate');if(m&&d){d.value=dateStr;m.style.display='block';}}
    function closeHolidayModal(){var m=document.getElementById('holidayModal');if(m){m.style.display='none';}}
    function dragShift(ev, shiftId){ev.dataTransfer.setData('shift_id', shiftId);} function allowDrop(ev){ev.preventDefault();ev.currentTarget.classList.add('drop-target');} function clearDrop(ev){ev.currentTarget.classList.remove('drop-target');}
    function dropShift(ev, dateStr){ev.preventDefault();ev.currentTarget.classList.remove('drop-target');var shiftId=ev.dataTransfer.getData('shift_id');if(!shiftId)return;fetch('/move_shift',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'shift_id='+encodeURIComponent(shiftId)+'&date='+encodeURIComponent(dateStr)}).then(function(){window.location.reload();});}
    // Automatski osvježi stranicu da se status i boja promijene bez ručnog refresh-a.
    setInterval(function() {
        window.location.reload();
    }, 30000);
    </script>
    """, tr=tr, dark=dark, week_days=week_days, shifts=shifts, worker_colors=worker_colors, format_date=format_date, holidays_map=holidays_map, day_names=day_names, status_colors=STATUS_COLORS, get_status_label=get_status_label, get_auto_status=get_auto_status, split_workers=split_workers, is_weekend=is_weekend, is_admin=is_admin, prev_week=prev_week, next_week=next_week, current_week=current_week)


@app.route("/month")
def month_view():
    if "user" not in session:
        return redirect("/login")
    tr = t(); dark = get_theme() == "dark"; is_admin = session.get("role") == "admin"; current_user = session.get("user"); copied_shift_id = session.get("copied_shift_id")
    year = request.args.get("year", type=int) or datetime.today().year
    month = request.args.get("month", type=int) or datetime.today().month
    prev_year, prev_month = month_navigation(year, month, -1); next_year, next_month = month_navigation(year, month, 1)
    conn = get_conn(); c = conn.cursor(); worker_colors = get_worker_colors(conn)
    start_date = f"{year:04d}-{month:02d}-01"; end_date = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    shifts = c.execute("SELECT * FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time", (start_date, end_date)).fetchall()
    if not is_admin: shifts = [s for s in shifts if worker_in_shift(current_user, s[1])]
    cal = calendar.Calendar(firstweekday=0); month_days = cal.monthdatescalendar(year, month)
    holiday_years = {d.year for wk in month_days for d in wk}; holidays_map = get_all_holidays(conn, holiday_years); conn.close()
    shifts_by_date = {}; [shifts_by_date.setdefault(s[3], []).append(s) for s in shifts]
    day_names = [tr["monday"], tr["tuesday"], tr["wednesday"], tr["thursday"], tr["friday"], tr["saturday"], tr["sunday"]]
    return render_template_string(BASE_STYLE + header_html() + """
    <div><a href="/">{{ tr["back"] }}</a><a href="/week">{{ tr["week_calendar"] }}</a><a href="/month_pdf?year={{ year }}&month={{ month }}" target="_blank">{{ tr["month_pdf"] }}</a></div>
    {% if is_admin and copied_shift_id %}<div style="background:#16a34a;color:white;padding:8px 12px;border-radius:8px;display:inline-block;margin:10px 0;font-weight:bold;">Copy aktivan - klikni + Paste na zeljeni datum. <a style="color:white;" href="/clear_copy">Ponisti</a></div>{% endif %}
    <div style="display:flex; justify-content:space-between; align-items:center; margin:16px 0; gap:12px;"><a href="/month?year={{ prev_year }}&month={{ prev_month }}">{{ tr["prev_month"] }}</a><h2>{{ tr["month_calendar"] }} - {{ "%02d/%04d"|format(month, year) }}</h2><a href="/month?year={{ next_year }}&month={{ next_month }}">{{ tr["next_month"] }}</a></div>
    <div style="display:grid; grid-template-columns:repeat(7,1fr); gap:10px;">
        {% for dn in day_names %}<div class="card" style="min-height:auto; text-align:center; font-weight:bold;">{{ dn }}</div>{% endfor %}
        {% for week in month_days %}{% for day in week %}{% set daystr = day.strftime('%Y-%m-%d') %}{% set holiday_name = holidays_map.get(daystr) %}
            <div class="card {% if holiday_name %}holiday-soft{% endif %} {% if day.weekday() >= 5 %}weekend-soft{% endif %}" style="min-height:120px;" ondragover="allowDrop(event)" ondragleave="clearDrop(event)" ondrop="dropShift(event, '{{ daystr }}')">
                <div style="font-weight:bold; margin-bottom:8px;"><a href="{% if is_admin %}javascript:void(0){% else %}/?selected_date={{ daystr }}{% endif %}" {% if is_admin %}onclick="openHolidayModal('{{ daystr }}')"{% endif %} style="{% if day.weekday() >= 5 %}color:#ef4444;{% endif %}">{{ day.strftime('%d/%m/%Y') }}</a>{% if is_admin and copied_shift_id %}<br><a style="display:inline-block;margin-top:6px;padding:4px 7px;border-radius:6px;background:#16a34a;color:white!important;font-size:11px;" href="/paste_shift/{{ daystr }}">{{ tr["paste"] }}</a>{% endif %}</div>
                {% if holiday_name %}<small class="holiday-note">{{ holiday_name }}</small>{% endif %}
                {% for s in shifts_by_date.get(daystr, []) %}<div class="mini-shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="border-left:5px solid {{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#1f4f82') }};">{% set auto_status = get_auto_status(s[3], s[4]) %}<b>{{ s[1] }}</b><br>{{ s[2] }}<br>{{ s[4] }}<br><span class="status-badge" style="background:{{ status_colors.get(auto_status, '#6b7280') }}; margin-left:0; display:inline-block; margin-top:4px;">{{ get_status_label(auto_status, tr) }}</span>{% if is_admin %}<br><a class="mini-link edit-link" href="/edit_shift/{{ s[0] }}">{{ tr["edit"] }}</a><a class="mini-link delete-link" href="/delete_shift/{{ s[0] }}">{{ tr["delete"] }}</a><a class="mini-link copy-link" href="/copy_shift/{{ s[0] }}">{{ tr["copy"] }}</a>{% endif %}</div>{% endfor %}
            </div>
        {% endfor %}{% endfor %}
    </div>
    {% if is_admin %}<div id="holidayModal" class="modal-backdrop"><div class="modal-card"><h3>{{ tr["add_holiday"] }}</h3><form method="post" action="/add_holiday"><input type="date" name="date" id="holidayDate" required><input type="text" name="name" placeholder="{{ tr['holiday_name'] }}" required><button>{{ tr["save"] }}</button></form><button type="button" onclick="closeHolidayModal()">{{ tr["cancel"] }}</button></div></div>{% endif %}
    <script>
    function openHolidayModal(dateStr){var m=document.getElementById('holidayModal');var d=document.getElementById('holidayDate');if(m&&d){d.value=dateStr;m.style.display='block';}} function closeHolidayModal(){var m=document.getElementById('holidayModal');if(m){m.style.display='none';}}
    function dragShift(ev, shiftId){ev.dataTransfer.setData('shift_id', shiftId);} function allowDrop(ev){ev.preventDefault();ev.currentTarget.classList.add('drop-target');} function clearDrop(ev){ev.currentTarget.classList.remove('drop-target');}
    function dropShift(ev, dateStr){ev.preventDefault();ev.currentTarget.classList.remove('drop-target');var shiftId=ev.dataTransfer.getData('shift_id');if(!shiftId)return;fetch('/move_shift',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'shift_id='+encodeURIComponent(shiftId)+'&date='+encodeURIComponent(dateStr)}).then(function(){window.location.reload();});}
    // Automatski osvježi stranicu da se status i boja promijene bez ručnog refresh-a.
    setInterval(function() {
        window.location.reload();
    }, 30000);
    </script>
    """, tr=tr, dark=dark, year=year, month=month, prev_year=prev_year, prev_month=prev_month, next_year=next_year, next_month=next_month, month_days=month_days, day_names=day_names, shifts_by_date=shifts_by_date, worker_colors=worker_colors, holidays_map=holidays_map, is_admin=is_admin, copied_shift_id=copied_shift_id, status_colors=STATUS_COLORS, get_status_label=get_status_label, get_auto_status=get_auto_status, split_workers=split_workers)


@app.route("/export_pdf")
def export_pdf():
    if "user" not in session: return redirect("/login")
    tr = t(); is_admin = session.get("role") == "admin"; current_user = session.get("user")
    conn = get_conn(); c = conn.cursor(); date_filter = request.args.get("date", "").strip()
    shifts = c.execute("SELECT * FROM shifts WHERE date = ? ORDER BY date, time", (date_filter,)).fetchall() if date_filter else c.execute("SELECT * FROM shifts ORDER BY date, time").fetchall()
    if not is_admin: shifts = [s for s in shifts if worker_in_shift(current_user, s[1])]
    conn.close(); buffer = io.BytesIO(); doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet(); elements = []
    if os.path.exists("static/logo.png"): elements += [Image("static/logo.png", width=4*cm, height=2*cm), Spacer(1, 8)]
    title = tr["pdf_title"] + (f" - {format_date(date_filter)}" if date_filter else "")
    elements += [Paragraph(title, styles["Title"]), Spacer(1, 12), Paragraph(f"{tr['pdf_user']}: {session['user']} ({session['role']})", styles["Normal"]), Spacer(1, 12)]
    table_data = [[tr["pdf_date"], tr["pdf_time"], tr["pdf_worker"], tr["pdf_client"], tr["status"]]]
    for s in shifts: table_data.append([format_date(s[3]), s[4], s[1], s[2], get_status_label(get_auto_status(s[3], s[4]), tr)])
    if not shifts: table_data.append(["-", "-", "-", "-", tr["pdf_no_shifts"]])
    table = Table(table_data, colWidths=[2.8*cm, 2.8*cm, 4.0*cm, 4.8*cm, 3.0*cm]); table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4f82")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), 0.5, colors.grey), ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.HexColor("#eaf2fb")]), ("FONTSIZE", (0,0), (-1,-1), 10)])); elements.append(table); doc.build(elements); buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="schedule.pdf", mimetype="application/pdf")


@app.route("/month_pdf")
def month_pdf():
    if "user" not in session: return redirect("/login")
    tr = t(); is_admin = session.get("role") == "admin"; current_user = session.get("user")
    year = request.args.get("year", type=int) or datetime.today().year; month = request.args.get("month", type=int) or datetime.today().month
    start_date = f"{year:04d}-{month:02d}-01"; end_date = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    conn = get_conn(); c = conn.cursor(); shifts = c.execute("SELECT * FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time", (start_date, end_date)).fetchall(); absences = c.execute("SELECT id, worker, type, date_from, date_to, note FROM absences ORDER BY worker, date_from").fetchall(); conn.close()
    if not is_admin: shifts = [s for s in shifts if worker_in_shift(current_user, s[1])]; absences = [a for a in absences if a[1] == current_user]
    buffer = io.BytesIO(); doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=1*cm, leftMargin=1*cm, topMargin=1*cm, bottomMargin=1*cm); styles = getSampleStyleSheet(); elements = [Paragraph(f"{tr['month_calendar']} {month:02d}/{year}", styles["Title"]), Spacer(1, 10)]
    table_data = [[tr["pdf_date"], tr["pdf_time"], tr["pdf_worker"], tr["pdf_client"], tr["status"]]]
    for s in shifts: table_data.append([format_date(s[3]), s[4], s[1], s[2], get_status_label(get_auto_status(s[3], s[4]), tr)])
    if len(table_data) == 1: table_data.append(["-", "-", "-", "-", tr["pdf_no_shifts"]])
    table = Table(table_data, colWidths=[3*cm, 3*cm, 6*cm, 6*cm, 4*cm]); table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4f82")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("GRID", (0,0), (-1,-1), 0.5, colors.grey), ("FONTSIZE", (0,0), (-1,-1), 9)])); elements.append(table)
    absence_lines = []
    for a in absences:
        days = absence_days_in_month(a, year, month)
        if days > 0: absence_lines.append(f"{a[1]} - {tr.get(a[2], a[2])}: {format_date(a[3])} - {format_date(a[4])} ({days} {tr['days']})")
    if absence_lines: elements += [Spacer(1, 12), Paragraph(tr["monthly_absence_days"], styles["Heading2"])] + [Paragraph(x, styles["Normal"]) for x in absence_lines]
    doc.build(elements); buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"month_calendar_{year}_{month:02d}.pdf", mimetype="application/pdf")


# CRUD routes
@app.route("/update_worker_color", methods=["POST"])
def update_worker_color():
    if session.get("role") != "admin": return redirect("/")
    worker_name = request.form["worker_name"].strip(); color = request.form["color"].strip()
    if worker_name and color:
        conn = get_conn(); c = conn.cursor(); c.execute("INSERT INTO worker_colors (worker_name, color) VALUES (?, ?) ON CONFLICT(worker_name) DO UPDATE SET color = excluded.color", (worker_name, color)); conn.commit(); conn.close()
    return redirect("/")

@app.route("/add_user", methods=["POST"])
def add_user():
    if session.get("role") != "admin": return redirect("/")
    username = request.form["username"].strip(); password = request.form["password"].strip(); role = request.form["role"].strip()
    if username and password and role in ("admin", "worker"):
        conn = get_conn(); c = conn.cursor(); c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", (username, password, role))
        if role == "worker": c.execute("INSERT OR IGNORE INTO workers (name, address) VALUES (?, ?)", (username, "")); c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (username, "#f97316"))
        conn.commit(); conn.close()
    return redirect("/")

@app.route("/delete_user/<int:user_id>")
def delete_user(user_id):
    if session.get("role") != "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor(); user = c.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if user and user[0] != "admin": c.execute("DELETE FROM users WHERE id = ?", (user_id,)); c.execute("DELETE FROM workers WHERE name = ?", (user[0],)); c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (user[0],))
    conn.commit(); conn.close(); return redirect("/")

@app.route("/delete_worker/<path:name>")
def delete_worker(name):
    if session.get("role") != "admin" or name == "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor(); c.execute("DELETE FROM workers WHERE name = ?", (name,)); c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (name,)); conn.commit(); conn.close(); return redirect("/")

@app.route("/delete_client/<path:name>")
def delete_client(name):
    if session.get("role") != "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor(); c.execute("DELETE FROM clients WHERE name = ?", (name,)); conn.commit(); conn.close(); return redirect("/")

@app.route("/delete_shift/<int:id>")
def delete_shift(id):
    if session.get("role") != "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor(); c.execute("DELETE FROM shifts WHERE id = ?", (id,)); conn.commit(); conn.close(); return redirect(request.referrer or "/")

@app.route("/edit_worker/<path:name>", methods=["GET", "POST"])
def edit_worker(name):
    if session.get("role") != "admin": return redirect("/")
    tr = t(); dark = get_theme() == "dark"; conn = get_conn(); c = conn.cursor()
    if request.method == "POST":
        new_name = request.form["name"].strip(); address = request.form["address"].strip()
        if new_name:
            old_color = c.execute("SELECT color FROM worker_colors WHERE worker_name = ?", (name,)).fetchone(); color_value = old_color[0] if old_color else "#f97316"; c.execute("UPDATE workers SET name = ?, address = ? WHERE name = ?", (new_name, address, name))
            for shift_id, worker_text in c.execute("SELECT id, worker FROM shifts").fetchall(): c.execute("UPDATE shifts SET worker = ? WHERE id = ?", (replace_worker_in_shift(worker_text, name, new_name), shift_id))
            c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (name,)); c.execute("INSERT OR REPLACE INTO worker_colors (worker_name, color) VALUES (?, ?)", (new_name, color_value))
        conn.commit(); conn.close(); return redirect("/")
    worker = c.execute("SELECT name, address FROM workers WHERE name = ?", (name,)).fetchone(); conn.close()
    if not worker: return redirect("/")
    return render_template_string(BASE_STYLE + """<div class="card" style="max-width:500px;margin:auto;"><h2>{{ tr["workers"] }} - {{ tr["edit"] }}</h2><form method="post"><input name="name" value="{{ worker[0] }}" required><input name="address" value="{{ worker[1] }}" placeholder="{{ tr['address'] }}"><button>{{ tr["save"] }}</button></form><br><a href="/">{{ tr["back"] }}</a></div>""", tr=tr, worker=worker, dark=dark)

@app.route("/edit_client/<path:name>", methods=["GET", "POST"])
def edit_client(name):
    if session.get("role") != "admin": return redirect("/")
    tr = t(); dark = get_theme() == "dark"; conn = get_conn(); c = conn.cursor()
    if request.method == "POST":
        new_name = request.form["name"].strip(); address = request.form["address"].strip()
        if new_name: c.execute("UPDATE clients SET name = ?, address = ? WHERE name = ?", (new_name, address, name)); c.execute("UPDATE shifts SET client = ? WHERE client = ?", (new_name, name))
        conn.commit(); conn.close(); return redirect("/")
    client = c.execute("SELECT name, address FROM clients WHERE name = ?", (name,)).fetchone(); conn.close()
    if not client: return redirect("/")
    return render_template_string(BASE_STYLE + """<div class="card" style="max-width:500px;margin:auto;"><h2>{{ tr["clients"] }} - {{ tr["edit"] }}</h2><form method="post"><input name="name" value="{{ client[0] }}" required><input name="address" value="{{ client[1] }}" placeholder="{{ tr['address'] }}"><button>{{ tr["save"] }}</button></form><br><a href="/">{{ tr["back"] }}</a></div>""", tr=tr, client=client, dark=dark)

@app.route("/edit_shift/<int:id>", methods=["GET", "POST"])
def edit_shift(id):
    if session.get("role") != "admin": return redirect("/")
    tr = t(); dark = get_theme() == "dark"; conn = get_conn(); c = conn.cursor()
    if request.method == "POST":
        worker = join_workers(request.form.getlist("workers")); client = request.form["client"].strip(); date = request.form["date"].strip(); start_time = f"{request.form['start_hour']}:{request.form['start_minute']}"; end_time = f"{request.form['end_hour']}:{request.form['end_minute']}"; status = request.form["status"].strip()
        if worker: c.execute("UPDATE shifts SET worker = ?, client = ?, date = ?, time = ?, status = ? WHERE id = ?", (worker, client, date, f"{start_time}-{end_time}", status, id)); conn.commit()
        conn.close(); return redirect("/")
    shift = c.execute("SELECT * FROM shifts WHERE id = ?", (id,)).fetchone(); workers = c.execute("SELECT name, address FROM workers ORDER BY name").fetchall(); clients = c.execute("SELECT name, address FROM clients ORDER BY name").fetchall(); conn.close()
    if not shift: return redirect("/")
    start_time, end_time = split_time_range(shift[4]); sh, sm = split_hour_min(start_time); eh, em = split_hour_min(end_time); selected_workers = split_workers(shift[1])
    return render_template_string(BASE_STYLE + """<div class="card" style="max-width:520px;margin:auto;"><h2>{{ tr["edit_shift"] }}</h2><form method="post"><label>{{ tr["choose_worker"] }}</label>{% for w in workers %}{% if w[0] != 'admin' %}<label class="check-row"><input type="checkbox" name="workers" value="{{ w[0] }}" {% if w[0] in selected_workers %}checked{% endif %}>{{ w[0] }}</label>{% endif %}{% endfor %}<select name="client" required>{% for c in clients %}<option value="{{ c[0] }}" {% if c[0] == shift[2] %}selected{% endif %}>{{ c[0] }}</option>{% endfor %}</select><input type="date" name="date" value="{{ shift[3] }}" required><label>{{ tr["start_time"] }}</label><div style="display:flex;gap:6px;"><select name="start_hour">{% for h in time_hours %}<option value="{{ h }}" {% if h == sh %}selected{% endif %}>{{ h }}</option>{% endfor %}</select><select name="start_minute">{% for m in time_minutes %}<option value="{{ m }}" {% if m == sm %}selected{% endif %}>{{ m }}</option>{% endfor %}</select></div><label>{{ tr["end_time"] }}</label><div style="display:flex;gap:6px;"><select name="end_hour">{% for h in time_hours %}<option value="{{ h }}" {% if h == eh %}selected{% endif %}>{{ h }}</option>{% endfor %}</select><select name="end_minute">{% for m in time_minutes %}<option value="{{ m }}" {% if m == em %}selected{% endif %}>{{ m }}</option>{% endfor %}</select></div><select name="status"><option value="planned" {% if shift[5] == 'planned' %}selected{% endif %}>{{ tr["status_planned"] }}</option><option value="in_progress" {% if shift[5] == 'in_progress' %}selected{% endif %}>{{ tr["status_in_progress"] }}</option><option value="done" {% if shift[5] == 'done' %}selected{% endif %}>{{ tr["status_done"] }}</option></select><button>{{ tr["save"] }}</button></form><br><a href="/">{{ tr["back"] }}</a></div>""", tr=tr, dark=dark, shift=shift, workers=workers, clients=clients, selected_workers=selected_workers, sh=sh, sm=sm, eh=eh, em=em, time_hours=time_hours(), time_minutes=time_minutes())

@app.route("/add_worker", methods=["POST"])
def add_worker():
    if session.get("role") != "admin": return redirect("/")
    name = request.form["worker_name"].strip(); address = request.form.get("address", "").strip()
    if name:
        conn = get_conn(); c = conn.cursor(); c.execute("INSERT OR IGNORE INTO workers (name, address) VALUES (?, ?)", (name, address)); c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (name, "#f97316")); conn.commit(); conn.close()
    return redirect("/")

@app.route("/add_client", methods=["POST"])
def add_client():
    if session.get("role") != "admin": return redirect("/")
    name = request.form["client_name"].strip(); address = request.form.get("address", "").strip()
    if name:
        conn = get_conn(); c = conn.cursor(); c.execute("INSERT OR IGNORE INTO clients (name, address) VALUES (?, ?)", (name, address)); conn.commit(); conn.close()
    return redirect("/")

@app.route("/add_shift", methods=["POST"])
def add_shift():
    if "user" not in session or session.get("role") != "admin": return redirect("/")
    worker = join_workers(request.form.getlist("workers")); client = request.form["client"].strip(); date = request.form["date"].strip(); start_time = f"{request.form['start_hour']}:{request.form['start_minute']}"; end_time = f"{request.form['end_hour']}:{request.form['end_minute']}"; status = request.form["status"].strip()
    if worker and client and date:
        conn = get_conn(); c = conn.cursor(); c.execute("INSERT INTO shifts (worker, client, date, time, status) VALUES (?, ?, ?, ?, ?)", (worker, client, date, f"{start_time}-{end_time}", status)); conn.commit(); conn.close()
    return redirect("/")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
