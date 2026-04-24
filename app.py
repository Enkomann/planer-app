from flask import Flask, request, redirect, render_template_string, session, send_file, url_for
import sqlite3
import io
import os
from datetime import datetime, timedelta
import calendar

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

app = Flask(__name__)
app.secret_key = "luxmann_secret_key"

DEFAULT_WORKER_COLORS = {
    "admin": "#1f4f82",
    "worker1": "#16a34a",
}

STATUS_COLORS = {
    "planned": "#f59e0b",
    "in_progress": "#2563eb",
    "done": "#16a34a",
}

TRANSLATIONS = {
    "bos": {
        "login_title": "Prijava",
        "username": "Korisnicko ime",
        "password": "Lozinka",
        "login_btn": "Prijava",
        "login_error": "Pogresno korisnicko ime ili lozinka",
        "title": "PLAN RADNIKA",
        "logged_as": "Logovan kao",
        "logout": "Odjava",
        "add_worker": "Dodaj radnika",
        "add_client": "Dodaj klijenta",
        "add_shift": "Dodaj smjenu",
        "worker_name": "Ime radnika",
        "client_name": "Naziv klijenta",
        "choose_worker": "Izaberi radnika",
        "choose_client": "Izaberi klijenta",
        "filter_btn": "Filtriraj",
        "reset": "Reset",
        "plan": "PLAN",
        "no_shifts": "Trenutno nema unesenih smjena.",
        "edit": "Izmijeni",
        "delete": "Obrisi",
        "week_calendar": "Sedmicni kalendar",
        "month_calendar": "Mjesecni kalendar",
        "pdf": "PDF raspored",
        "back": "Nazad",
        "edit_shift": "Izmijeni smjenu",
        "save": "Sacuvaj",
        "time_placeholder": "Vrijeme, npr. 08:00-12:00",
        "pdf_title": "Raspored radnika",
        "pdf_user": "Korisnik",
        "pdf_date": "Datum",
        "pdf_time": "Vrijeme",
        "pdf_worker": "Radnik",
        "pdf_client": "Klijent",
        "pdf_no_shifts": "Nema smjena",
        "user_mgmt": "Upravljanje korisnicima",
        "add_user": "Dodaj korisnika",
        "role_admin": "admin",
        "role_worker": "worker",
        "existing_users": "Postojeci korisnici",
        "delete_user": "Obrisi korisnika",
        "status": "Status",
        "status_planned": "Planirano",
        "status_in_progress": "U toku",
        "status_done": "Zavrseno",
        "weekly_hours": "Nedeljni sati",
        "monthly_hours": "Mjesecni sati",
        "hours": "sati",
        "all_workers": "Svi radnici",
        "all_clients": "Svi klijenti",
        "theme": "Tema",
        "light_theme": "Svijetla",
        "dark_theme": "Tamna",
        "worker_colors": "Boje radnika",
        "update_color": "Azuriraj boju",
        "prev_month": "Prosli mjesec",
        "next_month": "Sljedeci mjesec",
        "change_password": "Promijeni lozinku",
        "new_password": "Nova lozinka",
        "search_shifts": "Pretraga smjena",
        "search_placeholder": "Pretrazi po klijentu, radniku, vremenu...",
        "week_period": "Period",
        "workers": "Radnici",
        "clients": "Klijenti",
        "menu": "Menu",
        "monday": "Pon",
        "tuesday": "Uto",
        "wednesday": "Sri",
        "thursday": "Cet",
        "friday": "Pet",
        "saturday": "Sub",
        "sunday": "Ned",
    },
    "fr": {
        "login_title": "Connexion",
        "username": "Nom d'utilisateur",
        "password": "Mot de passe",
        "login_btn": "Connexion",
        "login_error": "Nom d'utilisateur ou mot de passe incorrect",
        "title": "PLAN DE TRAVAIL",
        "logged_as": "Connecte comme",
        "logout": "Deconnexion",
        "add_worker": "Ajouter employe",
        "add_client": "Ajouter client",
        "add_shift": "Ajouter mission",
        "worker_name": "Nom de l'employe",
        "client_name": "Nom du client",
        "choose_worker": "Choisir employe",
        "choose_client": "Choisir client",
        "filter_btn": "Filtrer",
        "reset": "Reinitialiser",
        "plan": "PLANNING",
        "no_shifts": "Aucune mission enregistree.",
        "edit": "Modifier",
        "delete": "Supprimer",
        "week_calendar": "Calendrier hebdomadaire",
        "month_calendar": "Calendrier mensuel",
        "pdf": "PDF planning",
        "back": "Retour",
        "edit_shift": "Modifier mission",
        "save": "Enregistrer",
        "time_placeholder": "Horaire, ex. 08:00-12:00",
        "pdf_title": "Planning des employes",
        "pdf_user": "Utilisateur",
        "pdf_date": "Date",
        "pdf_time": "Heure",
        "pdf_worker": "Employe",
        "pdf_client": "Client",
        "pdf_no_shifts": "Aucune mission",
        "user_mgmt": "Gestion des utilisateurs",
        "add_user": "Ajouter utilisateur",
        "role_admin": "admin",
        "role_worker": "worker",
        "existing_users": "Utilisateurs existants",
        "delete_user": "Supprimer utilisateur",
        "status": "Statut",
        "status_planned": "Planifie",
        "status_in_progress": "En cours",
        "status_done": "Termine",
        "weekly_hours": "Heures hebdomadaires",
        "monthly_hours": "Heures mensuelles",
        "hours": "heures",
        "all_workers": "Tous les employes",
        "all_clients": "Tous les clients",
        "theme": "Theme",
        "light_theme": "Clair",
        "dark_theme": "Sombre",
        "worker_colors": "Couleurs des employes",
        "update_color": "Mettre a jour couleur",
        "prev_month": "Mois precedent",
        "next_month": "Mois suivant",
        "change_password": "Changer mot de passe",
        "new_password": "Nouveau mot de passe",
        "search_shifts": "Rechercher missions",
        "search_placeholder": "Rechercher par client, employe, heure...",
        "week_period": "Periode",
        "workers": "Employes",
        "clients": "Clients",
        "menu": "Menu",
        "monday": "Lun",
        "tuesday": "Mar",
        "wednesday": "Mer",
        "thursday": "Jeu",
        "friday": "Ven",
        "saturday": "Sam",
        "sunday": "Dim",
    },
    "en": {
        "login_title": "Login",
        "username": "Username",
        "password": "Password",
        "login_btn": "Login",
        "login_error": "Wrong username or password",
        "title": "WORK SCHEDULE",
        "logged_as": "Logged in as",
        "logout": "Logout",
        "add_worker": "Add worker",
        "add_client": "Add client",
        "add_shift": "Add shift",
        "worker_name": "Worker name",
        "client_name": "Client name",
        "choose_worker": "Choose worker",
        "choose_client": "Choose client",
        "filter_btn": "Filter",
        "reset": "Reset",
        "plan": "SCHEDULE",
        "no_shifts": "No shifts entered.",
        "edit": "Edit",
        "delete": "Delete",
        "week_calendar": "Weekly calendar",
        "month_calendar": "Monthly calendar",
        "pdf": "Schedule PDF",
        "back": "Back",
        "edit_shift": "Edit shift",
        "save": "Save",
        "time_placeholder": "Time, e.g. 08:00-12:00",
        "pdf_title": "Worker schedule",
        "pdf_user": "User",
        "pdf_date": "Date",
        "pdf_time": "Time",
        "pdf_worker": "Worker",
        "pdf_client": "Client",
        "pdf_no_shifts": "No shifts",
        "user_mgmt": "User management",
        "add_user": "Add user",
        "role_admin": "admin",
        "role_worker": "worker",
        "existing_users": "Existing users",
        "delete_user": "Delete user",
        "status": "Status",
        "status_planned": "Planned",
        "status_in_progress": "In progress",
        "status_done": "Done",
        "weekly_hours": "Weekly hours",
        "monthly_hours": "Monthly hours",
        "hours": "hours",
        "all_workers": "All workers",
        "all_clients": "All clients",
        "theme": "Theme",
        "light_theme": "Light",
        "dark_theme": "Dark",
        "worker_colors": "Worker colors",
        "update_color": "Update color",
        "prev_month": "Previous month",
        "next_month": "Next month",
        "change_password": "Change password",
        "new_password": "New password",
        "search_shifts": "Search shifts",
        "search_placeholder": "Search by client, worker, time...",
        "week_period": "Period",
        "workers": "Workers",
        "clients": "Clients",
        "menu": "Menu",
        "monday": "Mon",
        "tuesday": "Tue",
        "wednesday": "Wed",
        "thursday": "Thu",
        "friday": "Fri",
        "saturday": "Sat",
        "sunday": "Sun",
    },
    "de": {
        "login_title": "Anmeldung",
        "username": "Benutzername",
        "password": "Passwort",
        "login_btn": "Anmelden",
        "login_error": "Falscher Benutzername oder falsches Passwort",
        "title": "ARBEITSPLAN",
        "logged_as": "Angemeldet als",
        "logout": "Abmelden",
        "add_worker": "Mitarbeiter hinzufugen",
        "add_client": "Kunde hinzufugen",
        "add_shift": "Einsatz hinzufugen",
        "worker_name": "Name des Mitarbeiters",
        "client_name": "Name des Kunden",
        "choose_worker": "Mitarbeiter wahlen",
        "choose_client": "Kunden wahlen",
        "filter_btn": "Filtern",
        "reset": "Zurucksetzen",
        "plan": "PLANUNG",
        "no_shifts": "Keine Einsatze vorhanden.",
        "edit": "Bearbeiten",
        "delete": "Loschen",
        "week_calendar": "Wochenkalender",
        "month_calendar": "Monatskalender",
        "pdf": "PDF Plan",
        "back": "Zuruck",
        "edit_shift": "Einsatz bearbeiten",
        "save": "Speichern",
        "time_placeholder": "Zeit, z. B. 08:00-12:00",
        "pdf_title": "Mitarbeiterplan",
        "pdf_user": "Benutzer",
        "pdf_date": "Datum",
        "pdf_time": "Zeit",
        "pdf_worker": "Mitarbeiter",
        "pdf_client": "Kunde",
        "pdf_no_shifts": "Keine Einsatze",
        "user_mgmt": "Benutzerverwaltung",
        "add_user": "Benutzer hinzufugen",
        "role_admin": "admin",
        "role_worker": "worker",
        "existing_users": "Bestehende Benutzer",
        "delete_user": "Benutzer loschen",
        "status": "Status",
        "status_planned": "Geplant",
        "status_in_progress": "In Arbeit",
        "status_done": "Erledigt",
        "weekly_hours": "Wochenstunden",
        "monthly_hours": "Monatsstunden",
        "hours": "Stunden",
        "all_workers": "Alle Mitarbeiter",
        "all_clients": "Alle Kunden",
        "theme": "Thema",
        "light_theme": "Hell",
        "dark_theme": "Dunkel",
        "worker_colors": "Mitarbeiterfarben",
        "update_color": "Farbe aktualisieren",
        "prev_month": "Vorheriger Monat",
        "next_month": "Nachster Monat",
        "change_password": "Passwort andern",
        "new_password": "Neues Passwort",
        "search_shifts": "Einsatze suchen",
        "search_placeholder": "Nach Kunde, Mitarbeiter, Zeit suchen...",
        "week_period": "Zeitraum",
        "workers": "Mitarbeiter",
        "clients": "Kunden",
        "menu": "Menu",
        "monday": "Mo",
        "tuesday": "Di",
        "wednesday": "Mi",
        "thursday": "Do",
        "friday": "Fr",
        "saturday": "Sa",
        "sunday": "So",
    },
    "pt": {
        "login_title": "Entrar",
        "username": "Nome de utilizador",
        "password": "Palavra-passe",
        "login_btn": "Entrar",
        "login_error": "Nome de utilizador ou palavra-passe incorretos",
        "title": "PLANO DE TRABALHO",
        "logged_as": "Ligado como",
        "logout": "Sair",
        "add_worker": "Adicionar trabalhador",
        "add_client": "Adicionar cliente",
        "add_shift": "Adicionar turno",
        "worker_name": "Nome do trabalhador",
        "client_name": "Nome do cliente",
        "choose_worker": "Escolher trabalhador",
        "choose_client": "Escolher cliente",
        "filter_btn": "Filtrar",
        "reset": "Repor",
        "plan": "PLANO",
        "no_shifts": "Nao ha turnos inseridos.",
        "edit": "Editar",
        "delete": "Apagar",
        "week_calendar": "Calendario semanal",
        "month_calendar": "Calendario mensal",
        "pdf": "PDF do plano",
        "back": "Voltar",
        "edit_shift": "Editar turno",
        "save": "Guardar",
        "time_placeholder": "Hora, ex. 08:00-12:00",
        "pdf_title": "Plano dos trabalhadores",
        "pdf_user": "Utilizador",
        "pdf_date": "Data",
        "pdf_time": "Hora",
        "pdf_worker": "Trabalhador",
        "pdf_client": "Cliente",
        "pdf_no_shifts": "Sem turnos",
        "user_mgmt": "Gestao de utilizadores",
        "add_user": "Adicionar utilizador",
        "role_admin": "admin",
        "role_worker": "worker",
        "existing_users": "Utilizadores existentes",
        "delete_user": "Apagar utilizador",
        "status": "Estado",
        "status_planned": "Planeado",
        "status_in_progress": "Em curso",
        "status_done": "Concluido",
        "weekly_hours": "Horas semanais",
        "monthly_hours": "Horas mensais",
        "hours": "horas",
        "all_workers": "Todos os trabalhadores",
        "all_clients": "Todos os clientes",
        "theme": "Tema",
        "light_theme": "Claro",
        "dark_theme": "Escuro",
        "worker_colors": "Cores dos trabalhadores",
        "update_color": "Atualizar cor",
        "prev_month": "Mes anterior",
        "next_month": "Proximo mes",
        "change_password": "Alterar palavra-passe",
        "new_password": "Nova palavra-passe",
        "search_shifts": "Pesquisar turnos",
        "search_placeholder": "Pesquisar por cliente, trabalhador, hora...",
        "week_period": "Periodo",
        "workers": "Trabalhadores",
        "clients": "Clientes",
        "menu": "Menu",
        "monday": "Seg",
        "tuesday": "Ter",
        "wednesday": "Qua",
        "thursday": "Qui",
        "friday": "Sex",
        "saturday": "Sab",
        "sunday": "Dom",
    },
}

def get_lang():
    return session.get("lang", "fr")

def t():
    return TRANSLATIONS.get(get_lang(), TRANSLATIONS["fr"])

def get_theme():
    return session.get("theme", "light")

def get_conn():
    return sqlite3.connect("db.sqlite")

def format_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return date_str

def get_status_label(status_key, tr):
    return {
        "planned": tr["status_planned"],
        "in_progress": tr["status_in_progress"],
        "done": tr["status_done"],
    }.get(status_key, status_key)

def parse_shift_hours(time_str):
    try:
        start_str, end_str = [x.strip() for x in time_str.split("-")]
        start = datetime.strptime(start_str, "%H:%M")
        end = datetime.strptime(end_str, "%H:%M")
        return max((end - start).total_seconds() / 3600, 0.0)
    except Exception:
        return 0.0

def calculate_hours_by_worker(shifts):
    totals = {}
    for s in shifts:
        totals[s[1]] = totals.get(s[1], 0.0) + parse_shift_hours(s[4])
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
            name TEXT UNIQUE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
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

    cols = [row[1] for row in c.execute("PRAGMA table_info(shifts)").fetchall()]
    if "status" not in cols:
        c.execute("ALTER TABLE shifts ADD COLUMN status TEXT DEFAULT 'planned'")

    c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", ("admin", "admin123", "admin"))
    c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", ("worker1", "1234", "worker"))

    c.execute("INSERT OR IGNORE INTO workers (name) VALUES (?)", ("admin",))
    c.execute("INSERT OR IGNORE INTO workers (name) VALUES (?)", ("worker1",))

    for worker_name, color in DEFAULT_WORKER_COLORS.items():
        c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (worker_name, color))

    conn.commit()
    conn.close()

init_db()

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

    return render_template_string("""
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: {{ '#0f172a' if dark else '#f4f6f8' }}; color: {{ '#e5e7eb' if dark else '#111827' }}; }
        .langbar { max-width: 420px; margin: 0 auto 12px auto; text-align: right; }
        .langbar a { text-decoration:none; margin-left:8px; font-weight:bold; color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
        .box { max-width:420px; margin:auto; background: {{ '#111827' if dark else 'white' }}; padding:30px; border-radius:12px; box-shadow:0 4px 14px rgba(0,0,0,0.08); }
        input, button { width:100%; padding:12px; margin-top:10px; box-sizing:border-box; border-radius:8px; }
        input { border:1px solid {{ '#374151' if dark else '#cbd5e1' }}; background: {{ '#1f2937' if dark else 'white' }}; color: {{ '#e5e7eb' if dark else '#111827' }}; }
        button { background:#1f4f82; color:white; border:none; cursor:pointer; }
        .error { color:#ef4444; margin-top:10px; }
    </style>

    <div class="langbar">
        <a href="/set_lang/fr">FR</a><a href="/set_lang/en">EN</a><a href="/set_lang/bos">BOS</a><a href="/set_lang/de">DE</a><a href="/set_lang/pt">PT</a>
    </div>

    <div class="box" style="text-align:center;">
        <img src="{{ url_for('static', filename='logo.png') }}" alt="Luxmann Logo" style="height:70px; margin-bottom:12px;">
        <h2>{{ tr["login_title"] }}</h2>
        <form method="post">
            <input name="username" placeholder="{{ tr['username'] }}" required>
            <input name="password" type="password" placeholder="{{ tr['password'] }}" required>
            <button type="submit">{{ tr["login_btn"] }}</button>
        </form>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
    </div>
    """, tr=tr, error=error, dark=dark)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/change_password", methods=["POST"])
def change_password():
    if "user" not in session:
        return redirect("/login")
    new_password = request.form["new_password"].strip()
    if not new_password:
        return redirect("/")
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET password = ? WHERE username = ?", (new_password, session["user"]))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/")
def index():
    if "user" not in session:
        return redirect("/login")

    tr = t()
    dark = get_theme() == "dark"

    conn = get_conn()
    c = conn.cursor()

    workers = c.execute("SELECT name FROM workers ORDER BY name").fetchall()
    clients = c.execute("SELECT name FROM clients ORDER BY name").fetchall()
    db_users = c.execute("SELECT id, username, role FROM users ORDER BY username").fetchall()
    worker_colors = get_worker_colors(conn)

    date_filter = request.args.get("date", "").strip()
    selected_date = request.args.get("selected_date", "").strip()
    worker_filter = request.args.get("worker", "").strip()
    client_filter = request.args.get("client", "").strip()
    search_query = request.args.get("q", "").strip().lower()

    base_query = "SELECT * FROM shifts WHERE 1=1"
    params = []

    if session["role"] != "admin":
        base_query += " AND worker = ?"
        params.append(session["user"])
    if date_filter:
        base_query += " AND date = ?"
        params.append(date_filter)
    if worker_filter:
        base_query += " AND worker = ?"
        params.append(worker_filter)
    if client_filter:
        base_query += " AND client = ?"
        params.append(client_filter)

    base_query += " ORDER BY date, time"
    shifts = c.execute(base_query, tuple(params)).fetchall()

    if search_query:
        shifts = [s for s in shifts if search_query in f"{s[1]} {s[2]} {s[3]} {s[4]} {s[5]}".lower()]

    today = datetime.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    if session["role"] == "admin":
        all_shifts = c.execute("SELECT * FROM shifts").fetchall()
    else:
        all_shifts = c.execute("SELECT * FROM shifts WHERE worker = ?", (session["user"],)).fetchall()

    week_shifts, month_shifts = [], []
    for s in all_shifts:
        try:
            d = datetime.strptime(s[3], "%Y-%m-%d")
            if week_start.date() <= d.date() <= week_end.date():
                week_shifts.append(s)
            if d.year == today.year and d.month == today.month:
                month_shifts.append(s)
        except Exception:
            pass

    weekly_hours = calculate_hours_by_worker(week_shifts)
    monthly_hours = calculate_hours_by_worker(month_shifts)

    week_period = f"{format_date(week_start.strftime('%Y-%m-%d'))} - {format_date(week_end.strftime('%Y-%m-%d'))}"
    month_period = today.strftime("%m/%Y")

    conn.close()

    return render_template_string("""
    <style>
        body { font-family: Arial, sans-serif; margin:24px; background: {{ '#0f172a' if dark else '#f4f6f8' }}; color: {{ '#e5e7eb' if dark else '#1f2937' }}; }
        h1 { color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
        h2, h3, h4 { color: {{ '#e5e7eb' if dark else '#111827' }}; }
        .brandbar { display:flex; justify-content:space-between; align-items:center; background: {{ '#111827' if dark else 'white' }}; border-radius:12px; padding:14px 18px; margin-bottom:18px; box-shadow:0 4px 14px rgba(0,0,0,0.06); }
        .brandleft { display:flex; align-items:center; gap:14px; }
        .brandleft img { height:56px; }
        .brandtitle { font-size:24px; font-weight:700; color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
        .langbar a, .topbar a, .theme-links a, .week-link, .pdf-link { color: {{ '#93c5fd' if dark else '#1f4f82' }}; text-decoration:none; font-weight:bold; margin-right:10px; }
        .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:16px; }
        .card { background: {{ '#111827' if dark else 'white' }}; border-radius:12px; padding:18px; box-shadow:0 4px 14px rgba(0,0,0,0.06); }
        input, select, button { padding:10px; margin:6px 0; width:100%; box-sizing:border-box; border:1px solid {{ '#374151' if dark else '#cbd5e1' }}; border-radius:8px; background: {{ '#1f2937' if dark else 'white' }}; color: {{ '#e5e7eb' if dark else '#111827' }}; }
        button { background:#1f4f82; color:white; border:none; cursor:pointer; }
        .menu-button { font-size:16px; font-weight:bold; }
        .shift { background: {{ 'linear-gradient(135deg, #111827, #1f2937)' if dark else 'linear-gradient(135deg, #ffffff, #f1f5f9)' }}; padding:14px; margin:12px 0; border-radius:12px; box-shadow:0 4px 14px rgba(0,0,0,0.06); }
        .user-row, .hours-row { padding:8px 0; border-bottom:1px solid {{ '#374151' if dark else '#e5e7eb' }}; }
        .muted { color: {{ '#9ca3af' if dark else '#64748b' }}; font-size:14px; }
        .status-badge { color:white; padding:4px 8px; border-radius:6px; font-size:12px; font-weight:bold; margin-left:8px; }
        .action-link { text-decoration:none; margin-left:10px; font-weight:bold; }
        .edit-link { color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
        .delete-link { color:#ef4444; text-decoration:none; font-weight:bold; margin-left:10px; }
    </style>

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
                {{ tr["theme"] }}:
                <a href="/set_theme/light">{{ tr["light_theme"] }}</a>
                <a href="/set_theme/dark">{{ tr["dark_theme"] }}</a>
            </div>
        </div>
    </div>

    <h1>{{ tr["title"] }}</h1>
    <div class="topbar">
        {{ tr["logged_as"] }}: <b>{{ session['user'] }}</b> ({{ session['role'] }})<br><br>
        <a href="/logout">{{ tr["logout"] }}</a>
    </div>

    <div class="grid">

        <div class="card" style="grid-column:1/-1;">
            <button class="menu-button" onclick="toggleMenu()" type="button">☰ {{ tr["menu"] }}</button>

            <div id="menuBox" style="display:none; margin-top:15px;">
                <div class="grid">

                    <div class="card">
                        <h3>{{ tr["change_password"] }}</h3>
                        <form method="post" action="/change_password" autocomplete="off">
                            <input name="new_password" type="password" placeholder="{{ tr['new_password'] }}" required autocomplete="off">
                            <button>{{ tr["save"] }}</button>
                        </form>
                    </div>

                    {% if session['role'] == 'admin' %}

                    <div class="card">
                        <h3>{{ tr["user_mgmt"] }}</h3>
                        <form method="post" action="/add_user" autocomplete="off">
                            <input name="username" placeholder="{{ tr['username'] }}" required autocomplete="off">
                            <input name="password" placeholder="{{ tr['password'] }}" required autocomplete="new-password">
                            <select name="role" required>
                                <option value="admin">{{ tr["role_admin"] }}</option>
                                <option value="worker">{{ tr["role_worker"] }}</option>
                            </select>
                            <button>{{ tr["add_user"] }}</button>
                        </form>
                    </div>

                    <div class="card">
                        <h3>{{ tr["existing_users"] }}</h3>
                        {% for u in db_users %}
                            <div class="user-row">
                                <b>{{ u[1] }}</b> ({{ u[2] }})
                                {% if u[1] != 'admin' %}
                                    <a class="delete-link" href="/delete_user/{{ u[0] }}">{{ tr["delete"] }}</a>
                                {% endif %}
                            </div>
                        {% endfor %}
                    </div>

                    <div class="card">
                        <h3>{{ tr["worker_colors"] }}</h3>
                        {% for w in workers %}
                            <form method="post" action="/update_worker_color">
                                <input type="hidden" name="worker_name" value="{{ w[0] }}">
                                <div style="display:flex; gap:10px; align-items:center;">
                                    <div style="min-width:110px;">{{ w[0] }}</div>
                                    <input type="color" name="color" value="{{ worker_colors.get(w[0], '#1f4f82') }}">
                                    <button>{{ tr["update_color"] }}</button>
                                </div>
                            </form>
                        {% endfor %}
                    </div>

                    <div class="card">
                        <h3>{{ tr["workers"] }}</h3>
                        {% for w in workers %}
                            <div class="user-row">
                                {{ w[0] }}
                                {% if w[0] != 'admin' %}
                                    <a class="delete-link" href="/delete_worker/{{ w[0] }}">{{ tr["delete"] }}</a>
                                {% endif %}
                            </div>
                        {% endfor %}
                    </div>

                    <div class="card">
                        <h3>{{ tr["clients"] }}</h3>
                        {% for c in clients %}
                            <div class="user-row">
                                {{ c[0] }}
                                <a class="delete-link" href="/delete_client/{{ c[0] }}">{{ tr["delete"] }}</a>
                            </div>
                        {% endfor %}
                    </div>

                    {% endif %}

                </div>
            </div>
        </div>

        {% if session['role'] == 'admin' %}
        <div class="card">
            <h3>{{ tr["add_worker"] }}</h3>
            <form method="post" action="/add_worker" autocomplete="off">
                <input name="name" placeholder="{{ tr['worker_name'] }}" required autocomplete="off">
                <button>{{ tr["add_worker"] }}</button>
            </form>
        </div>

        <div class="card">
            <h3>{{ tr["add_client"] }}</h3>
            <form method="post" action="/add_client" autocomplete="off">
                <input name="name" placeholder="{{ tr['client_name'] }}" required autocomplete="off">
                <button>{{ tr["add_client"] }}</button>
            </form>
        </div>
        {% endif %}

        <div class="card">
            <h3>{{ tr["add_shift"] }}</h3>
            <form method="post" action="/add_shift" autocomplete="off">
                <select name="worker" required>
                    <option value="">{{ tr["choose_worker"] }}</option>
                    {% for w in workers %}
                        <option value="{{ w[0] }}">{{ w[0] }}</option>
                    {% endfor %}
                </select>

                <select name="client" required>
                    <option value="">{{ tr["choose_client"] }}</option>
                    {% for c in clients %}
                        <option value="{{ c[0] }}">{{ c[0] }}</option>
                    {% endfor %}
                </select>

                <input name="date" type="date" value="{{ selected_date }}" required>
                <input name="time" placeholder="{{ tr['time_placeholder'] }}" required autocomplete="off">

                <select name="status" required>
                    <option value="planned">{{ tr["status_planned"] }}</option>
                    <option value="in_progress">{{ tr["status_in_progress"] }}</option>
                    <option value="done">{{ tr["status_done"] }}</option>
                </select>

                <button>{{ tr["add_shift"] }}</button>
            </form>
        </div>

        <div class="card">
            <h3>{{ tr["search_shifts"] }}</h3>
            <form method="get" autocomplete="off">
                <input type="date" name="date" value="{{ request.args.get('date', '') }}">
                <select name="worker">
                    <option value="">{{ tr["all_workers"] }}</option>
                    {% for w in workers %}
                        <option value="{{ w[0] }}" {% if worker_filter == w[0] %}selected{% endif %}>{{ w[0] }}</option>
                    {% endfor %}
                </select>
                <select name="client">
                    <option value="">{{ tr["all_clients"] }}</option>
                    {% for c in clients %}
                        <option value="{{ c[0] }}" {% if client_filter == c[0] %}selected{% endif %}>{{ c[0] }}</option>
                    {% endfor %}
                </select>
                <input name="q" value="{{ request.args.get('q', '') }}" placeholder="{{ tr['search_placeholder'] }}" autocomplete="off">
                <button>{{ tr["filter_btn"] }}</button>
            </form>
            <a href="/">{{ tr["reset"] }}</a>
        </div>

        <div class="card">
            <h3>{{ tr["weekly_hours"] }}</h3>
            <div class="muted">{{ tr["week_period"] }}: {{ week_period }}</div>
            {% for worker, hours in weekly_hours.items() %}
                <div class="hours-row">
                    <span>{{ worker }}</span>
                    <span>{{ "%.2f"|format(hours) }} {{ tr["hours"] }}</span>
                </div>
            {% endfor %}
            {% if weekly_hours|length == 0 %}<div class="muted">0 {{ tr["hours"] }}</div>{% endif %}
        </div>

        <div class="card">
            <h3>{{ tr["monthly_hours"] }}</h3>
            <div class="muted">{{ month_period }}</div>
            {% for worker, hours in monthly_hours.items() %}
                <div class="hours-row">
                    <span>{{ worker }}</span>
                    <span>{{ "%.2f"|format(hours) }} {{ tr["hours"] }}</span>
                </div>
            {% endfor %}
            {% if monthly_hours|length == 0 %}<div class="muted">0 {{ tr["hours"] }}</div>{% endif %}
        </div>
    </div>

    <div class="card" style="margin-top:20px;">
        <h2>{{ tr["plan"] }}</h2>
        {% if shifts|length == 0 %}<div class="muted">{{ tr["no_shifts"] }}</div>{% endif %}
        {% for s in shifts %}
            <div class="shift" style="border-left: 6px solid {{ worker_colors.get(s[1], '#1f4f82') }}">
                <b>{{ format_date(s[3]) }}</b> | {{ s[4] }}
                <span class="status-badge" style="background: {{ status_colors.get(s[5], '#6b7280') }};">
                    {{ get_status_label(s[5], tr) }}
                </span>
                <br><br>
                <span style="background: {{ worker_colors.get(s[1], '#1f4f82') }}; color:white; padding:4px 8px; border-radius:6px; font-size:12px; font-weight:bold;">
                    {{ s[1] }}
                </span>
                → {{ s[2] }}
                {% if session['role'] == 'admin' %}
                    <a class="action-link edit-link" href="/edit_shift/{{ s[0] }}">{{ tr["edit"] }}</a>
                    <a class="action-link delete-link" href="/delete_shift/{{ s[0] }}">{{ tr["delete"] }}</a>
                {% endif %}
            </div>
        {% endfor %}
        <a class="week-link" href="/week">{{ tr["week_calendar"] }}</a>
        <a class="week-link" href="/month">{{ tr["month_calendar"] }}</a>
        <a class="pdf-link" href="/export_pdf{% if request.args.get('date') %}?date={{ request.args.get('date') }}{% endif %}" target="_blank">{{ tr["pdf"] }}</a>
    </div>

    <script>
    function toggleMenu() {
        var m = document.getElementById("menuBox");
        if (m.style.display === "none") {
            m.style.display = "block";
        } else {
            m.style.display = "none";
        }
    }
    </script>
    """, tr=tr, dark=dark, workers=workers, clients=clients, db_users=db_users,
       worker_colors=worker_colors, selected_date=selected_date,
       worker_filter=worker_filter, client_filter=client_filter, shifts=shifts,
       format_date=format_date, status_colors=STATUS_COLORS,
       get_status_label=get_status_label, weekly_hours=weekly_hours,
       monthly_hours=monthly_hours, week_period=week_period, month_period=month_period)

@app.route("/week")
def week_view():
    if "user" not in session:
        return redirect("/login")

    tr = t()
    dark = get_theme() == "dark"

    conn = get_conn()
    c = conn.cursor()
    worker_colors = get_worker_colors(conn)

    today = datetime.today()
    start_week = today - timedelta(days=today.weekday())
    week_days = [(start_week + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    if session["role"] == "admin":
        shifts = c.execute("SELECT * FROM shifts").fetchall()
    else:
        shifts = c.execute("SELECT * FROM shifts WHERE worker = ?", (session["user"],)).fetchall()

    conn.close()

    return render_template_string("""
    <style>
        body { font-family: Arial, sans-serif; margin:24px; background: {{ '#0f172a' if dark else '#f4f6f8' }}; color: {{ '#e5e7eb' if dark else '#111827' }}; }
        .langbar a, a { text-decoration:none; color: {{ '#93c5fd' if dark else '#1f4f82' }}; font-weight:bold; margin-right:10px; }
        .week-wrap { display:flex; gap:12px; flex-wrap:wrap; }
        .day-card { background: {{ '#111827' if dark else 'white' }}; border-radius:12px; padding:14px; width:180px; box-shadow:0 4px 14px rgba(0,0,0,0.06); }
        .day-link { display:block; margin-bottom:8px; }
        .shift { background: {{ 'linear-gradient(135deg, #111827, #1f2937)' if dark else 'linear-gradient(135deg, #ffffff, #f1f5f9)' }}; margin-top:8px; padding:10px; border-radius:10px; }
        .status-badge { color:white; padding:4px 8px; border-radius:6px; font-size:12px; font-weight:bold; display:inline-block; margin-top:6px; }
    </style>

    <div class="langbar">
        <a href="/set_lang/fr">FR</a><a href="/set_lang/en">EN</a><a href="/set_lang/bos">BOS</a><a href="/set_lang/de">DE</a><a href="/set_lang/pt">PT</a>
    </div>

    <h1>{{ tr["week_calendar"] }}</h1>
    <a href="/">{{ tr["back"] }}</a><br><br>

    <div class="week-wrap">
        {% for day in week_days %}
            <div class="day-card">
                <a class="day-link" href="/?selected_date={{ day }}">{{ format_date(day) }}</a>
                {% for s in shifts %}
                    {% if s[3] == day %}
                        <div class="shift" style="border-left: 6px solid {{ worker_colors.get(s[1], '#1f4f82') }}">
                            <span style="background: {{ worker_colors.get(s[1], '#1f4f82') }}; color:white; padding:4px 8px; border-radius:6px; font-size:12px; font-weight:bold;">
                                {{ s[1] }}
                            </span>
                            <br><br>{{ s[2] }}<br>{{ s[4] }}<br>
                            <span class="status-badge" style="background: {{ status_colors.get(s[5], '#6b7280') }};">{{ get_status_label(s[5], tr) }}</span>
                        </div>
                    {% endif %}
                {% endfor %}
            </div>
        {% endfor %}
    </div>
    """, tr=tr, dark=dark, week_days=week_days, shifts=shifts,
       worker_colors=worker_colors, format_date=format_date,
       status_colors=STATUS_COLORS, get_status_label=get_status_label)

@app.route("/month")
def month_view():
    if "user" not in session:
        return redirect("/login")

    tr = t()
    dark = get_theme() == "dark"

    year = request.args.get("year", type=int) or datetime.today().year
    month = request.args.get("month", type=int) or datetime.today().month

    prev_year, prev_month = month_navigation(year, month, -1)
    next_year, next_month = month_navigation(year, month, 1)

    conn = get_conn()
    c = conn.cursor()
    worker_colors = get_worker_colors(conn)

    start_date = f"{year:04d}-{month:02d}-01"
    end_date = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"

    if session["role"] == "admin":
        shifts = c.execute("SELECT * FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time", (start_date, end_date)).fetchall()
    else:
        shifts = c.execute("SELECT * FROM shifts WHERE worker = ? AND date >= ? AND date <= ? ORDER BY date, time", (session["user"], start_date, end_date)).fetchall()

    conn.close()

    cal = calendar.Calendar(firstweekday=0)
    month_days = cal.monthdatescalendar(year, month)
    shifts_by_date = {}
    for s in shifts:
        shifts_by_date.setdefault(s[3], []).append(s)

    day_names = [tr["monday"], tr["tuesday"], tr["wednesday"], tr["thursday"], tr["friday"], tr["saturday"], tr["sunday"]]

    return render_template_string("""
    <style>
        body { font-family: Arial, sans-serif; margin:24px; background: {{ '#0f172a' if dark else '#f4f6f8' }}; color: {{ '#e5e7eb' if dark else '#111827' }}; }
        .topnav a { color: {{ '#93c5fd' if dark else '#1f4f82' }}; text-decoration:none; font-weight:bold; margin-right:12px; }
        .month-nav { display:flex; justify-content:space-between; align-items:center; margin:16px 0; }
        .month-grid { display:grid; grid-template-columns: repeat(7, 1fr); gap:10px; }
        .day-header, .day-cell { background: {{ '#111827' if dark else 'white' }}; border-radius:12px; padding:10px; box-shadow:0 4px 14px rgba(0,0,0,0.06); min-height:120px; }
        .day-header { min-height:auto; font-weight:bold; text-align:center; }
        .day-num { font-weight:bold; margin-bottom:8px; color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
        .mini-shift { margin-top:6px; padding:6px; border-radius:8px; font-size:12px; background: {{ '#1f2937' if dark else '#f8fafc' }}; }
    </style>

    <div class="topnav">
        <a href="/">{{ tr["back"] }}</a>
        <a href="/week">{{ tr["week_calendar"] }}</a>
    </div>

    <div class="month-nav">
        <a href="/month?year={{ prev_year }}&month={{ prev_month }}">{{ tr["prev_month"] }}</a>
        <h2>{{ tr["month_calendar"] }} - {{ "%02d/%04d"|format(month, year) }}</h2>
        <a href="/month?year={{ next_year }}&month={{ next_month }}">{{ tr["next_month"] }}</a>
    </div>

    <div class="month-grid">
        {% for dn in day_names %}<div class="day-header">{{ dn }}</div>{% endfor %}
        {% for week in month_days %}
            {% for day in week %}
                <div class="day-cell">
                    <div class="day-num">
                        <a href="/?selected_date={{ day.strftime('%Y-%m-%d') }}" style="text-decoration:none; color:inherit;">
                            {{ day.strftime('%d/%m/%Y') }}
                        </a>
                    </div>
                    {% for s in shifts_by_date.get(day.strftime('%Y-%m-%d'), []) %}
                        <div class="mini-shift" style="border-left:5px solid {{ worker_colors.get(s[1], '#1f4f82') }};">
                            <b>{{ s[1] }}</b><br>{{ s[2] }}<br>{{ s[4] }}
                        </div>
                    {% endfor %}
                </div>
            {% endfor %}
        {% endfor %}
    </div>
    """, tr=tr, dark=dark, prev_year=prev_year, prev_month=prev_month,
       next_year=next_year, next_month=next_month, month=month, year=year,
       month_days=month_days, shifts_by_date=shifts_by_date,
       worker_colors=worker_colors, day_names=day_names)

@app.route("/export_pdf")
def export_pdf():
    if "user" not in session:
        return redirect("/login")

    tr = t()
    conn = get_conn()
    c = conn.cursor()

    date_filter = request.args.get("date", "").strip()

    if session["role"] == "admin":
        if date_filter:
            shifts = c.execute("SELECT * FROM shifts WHERE date = ? ORDER BY date, time", (date_filter,)).fetchall()
        else:
            shifts = c.execute("SELECT * FROM shifts ORDER BY date, time").fetchall()
    else:
        if date_filter:
            shifts = c.execute("SELECT * FROM shifts WHERE worker = ? AND date = ? ORDER BY date, time", (session["user"], date_filter)).fetchall()
        else:
            shifts = c.execute("SELECT * FROM shifts WHERE worker = ? ORDER BY date, time", (session["user"],)).fetchall()

    conn.close()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5 * cm, leftMargin=1.5 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm)

    styles = getSampleStyleSheet()
    elements = []

    logo_path = "static/logo.png"
    if os.path.exists(logo_path):
        elements.append(Image(logo_path, width=4 * cm, height=2 * cm))
        elements.append(Spacer(1, 8))

    title = tr["pdf_title"] + (f" - {format_date(date_filter)}" if date_filter else "")
    elements.append(Paragraph(title, styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"{tr['pdf_user']}: {session['user']} ({session['role']})", styles["Normal"]))
    elements.append(Spacer(1, 12))

    table_data = [[tr["pdf_date"], tr["pdf_time"], tr["pdf_worker"], tr["pdf_client"], tr["status"]]]

    if shifts:
        for s in shifts:
            table_data.append([format_date(s[3]), s[4], s[1], s[2], get_status_label(s[5], tr)])
    else:
        table_data.append(["-", "-", "-", "-", tr["pdf_no_shifts"]])

    table = Table(table_data, colWidths=[2.8 * cm, 2.8 * cm, 4.0 * cm, 4.8 * cm, 3.0 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4f82")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#eaf2fb")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]))

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    filename = "schedule.pdf" if not date_filter else f"schedule_{date_filter}.pdf"
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")

@app.route("/update_worker_color", methods=["POST"])
def update_worker_color():
    if session.get("role") != "admin":
        return redirect("/")
    worker_name = request.form["worker_name"].strip()
    color = request.form["color"].strip()
    if not worker_name or not color:
        return redirect("/")
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO worker_colors (worker_name, color)
        VALUES (?, ?)
        ON CONFLICT(worker_name) DO UPDATE SET color = excluded.color
    """, (worker_name, color))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/add_user", methods=["POST"])
def add_user():
    if session.get("role") != "admin":
        return redirect("/")

    username = request.form["username"].strip()
    password = request.form["password"].strip()
    role = request.form["role"].strip()

    if not username or not password or role not in ("admin", "worker"):
        return redirect("/")

    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", (username, password, role))
    if role == "worker":
        c.execute("INSERT OR IGNORE INTO workers (name) VALUES (?)", (username,))
        c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (username, "#f97316"))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/delete_user/<int:user_id>")
def delete_user(user_id):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    c = conn.cursor()
    user = c.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if user and user[0] != "admin":
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
        c.execute("DELETE FROM workers WHERE name = ?", (user[0],))
        c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (user[0],))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/delete_worker/<name>")
def delete_worker(name):
    if session.get("role") != "admin":
        return redirect("/")
    if name == "admin":
        return redirect("/")
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM workers WHERE name = ?", (name,))
    c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (name,))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/delete_client/<name>")
def delete_client(name):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM clients WHERE name = ?", (name,))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/delete_shift/<int:id>")
def delete_shift(id):
    if "user" not in session or session["role"] != "admin":
        return redirect("/")
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM shifts WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/edit_shift/<int:id>", methods=["GET", "POST"])
def edit_shift(id):
    if "user" not in session or session["role"] != "admin":
        return redirect("/")

    tr = t()
    dark = get_theme() == "dark"

    conn = get_conn()
    c = conn.cursor()

    if request.method == "POST":
        worker = request.form["worker"].strip()
        client = request.form["client"].strip()
        date = request.form["date"].strip()
        time = request.form["time"].strip()
        status = request.form["status"].strip()

        c.execute("UPDATE shifts SET worker = ?, client = ?, date = ?, time = ?, status = ? WHERE id = ?", (worker, client, date, time, status, id))
        conn.commit()
        conn.close()
        return redirect("/")

    shift = c.execute("SELECT * FROM shifts WHERE id = ?", (id,)).fetchone()
    workers = c.execute("SELECT name FROM workers ORDER BY name").fetchall()
    clients = c.execute("SELECT name FROM clients ORDER BY name").fetchall()
    conn.close()

    if not shift:
        return redirect("/")

    return render_template_string("""
    <style>
        body { font-family: Arial, sans-serif; margin:24px; background: {{ '#0f172a' if dark else '#f4f6f8' }}; color: {{ '#e5e7eb' if dark else '#111827' }}; }
        .langbar a, a { text-decoration:none; margin-right:8px; font-weight:bold; color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
        .card { max-width:500px; background: {{ '#111827' if dark else 'white' }}; border-radius:12px; padding:20px; box-shadow:0 4px 14px rgba(0,0,0,0.06); margin:auto; }
        input, select, button { padding:10px; margin:6px 0; width:100%; box-sizing:border-box; border:1px solid {{ '#374151' if dark else '#cbd5e1' }}; border-radius:8px; background: {{ '#1f2937' if dark else 'white' }}; color: {{ '#e5e7eb' if dark else '#111827' }}; }
        button { background:#1f4f82; color:white; border:none; cursor:pointer; }
    </style>

    <div class="langbar">
        <a href="/set_lang/fr">FR</a><a href="/set_lang/en">EN</a><a href="/set_lang/bos">BOS</a><a href="/set_lang/de">DE</a><a href="/set_lang/pt">PT</a>
    </div>

    <div class="card">
        <h2>{{ tr["edit_shift"] }}</h2>
        <form method="post" autocomplete="off">
            <select name="worker" required>
                {% for w in workers %}
                    <option value="{{ w[0] }}" {% if w[0] == shift[1] %}selected{% endif %}>{{ w[0] }}</option>
                {% endfor %}
            </select>
            <select name="client" required>
                {% for c in clients %}
                    <option value="{{ c[0] }}" {% if c[0] == shift[2] %}selected{% endif %}>{{ c[0] }}</option>
                {% endfor %}
            </select>
            <input type="date" name="date" value="{{ shift[3] }}" required>
            <input type="text" name="time" value="{{ shift[4] }}" required autocomplete="off">
            <select name="status" required>
                <option value="planned" {% if shift[5] == 'planned' %}selected{% endif %}>{{ tr["status_planned"] }}</option>
                <option value="in_progress" {% if shift[5] == 'in_progress' %}selected{% endif %}>{{ tr["status_in_progress"] }}</option>
                <option value="done" {% if shift[5] == 'done' %}selected{% endif %}>{{ tr["status_done"] }}</option>
            </select>
            <button type="submit">{{ tr["save"] }}</button>
        </form>
        <br><a href="/">{{ tr["back"] }}</a>
    </div>
    """, tr=tr, dark=dark, shift=shift, workers=workers, clients=clients)

@app.route("/add_worker", methods=["POST"])
def add_worker():
    if session.get("role") != "admin":
        return redirect("/")
    name = request.form["name"].strip()
    if not name:
        return redirect("/")
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO workers (name) VALUES (?)", (name,))
    c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (name, "#f97316"))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/add_client", methods=["POST"])
def add_client():
    if session.get("role") != "admin":
        return redirect("/")
    name = request.form["name"].strip()
    if not name:
        return redirect("/")
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO clients (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/add_shift", methods=["POST"])
def add_shift():
    if "user" not in session:
        return redirect("/login")

    worker = request.form["worker"].strip()
    client = request.form["client"].strip()
    date = request.form["date"].strip()
    time = request.form["time"].strip()
    status = request.form["status"].strip()

    if not worker or not client or not date or not time:
        return redirect("/")

    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO shifts (worker, client, date, time, status) VALUES (?, ?, ?, ?, ?)", (worker, client, date, time, status))
    conn.commit()
    conn.close()
    return redirect("/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
