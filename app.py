from flask import Flask, request, redirect, render_template_string, session, send_file
import sqlite3
import io
import os
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

app = Flask(__name__)
app.secret_key = "luxmann_secret_key"

USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "worker1": {"password": "1234", "role": "worker"}
}

TRANSLATIONS = {
    "fr": {
        "login_title": "Connexion",
        "username": "Nom d'utilisateur",
        "password": "Mot de passe",
        "login_btn": "Connexion",
        "login_error": "Nom d'utilisateur ou mot de passe incorrect",
        "title": "PLAN DE TRAVAIL",
        "logged_as": "Connecté comme",
        "logout": "Déconnexion",
        "add_worker": "Ajouter employé",
        "add_client": "Ajouter client",
        "add_shift": "Ajouter mission",
        "worker_name": "Nom de l'employé",
        "client_name": "Nom du client",
        "choose_worker": "Choisir employé",
        "choose_client": "Choisir client",
        "date_filter": "Filtrer par date",
        "filter_btn": "Filtrer",
        "reset": "Réinitialiser",
        "plan": "PLANNING",
        "no_workers": "Aucun employé dans la base.",
        "no_clients": "Aucun client dans la base.",
        "no_shifts": "Aucune mission enregistrée.",
        "edit": "Modifier",
        "delete": "Supprimer",
        "week_calendar": "Calendrier hebdomadaire",
        "pdf": "PDF planning",
        "back": "← Retour",
        "edit_shift": "Modifier mission",
        "save": "Enregistrer",
        "time_placeholder": "Horaire, ex. 08:00-12:00",
        "pdf_title": "Planning des employés",
        "pdf_user": "Utilisateur",
        "pdf_date": "Date",
        "pdf_time": "Heure",
        "pdf_worker": "Employé",
        "pdf_client": "Client",
        "pdf_no_shifts": "Aucune mission",
        "language": "Langue"
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
        "date_filter": "Filter by date",
        "filter_btn": "Filter",
        "reset": "Reset",
        "plan": "SCHEDULE",
        "no_workers": "No workers in database.",
        "no_clients": "No clients in database.",
        "no_shifts": "No shifts entered.",
        "edit": "Edit",
        "delete": "Delete",
        "week_calendar": "Weekly calendar",
        "pdf": "Schedule PDF",
        "back": "← Back",
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
        "language": "Language"
    },
    "bos": {
        "login_title": "Prijava",
        "username": "Korisničko ime",
        "password": "Lozinka",
        "login_btn": "Prijava",
        "login_error": "Pogrešno korisničko ime ili lozinka",
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
        "date_filter": "Filter po datumu",
        "filter_btn": "Filtriraj",
        "reset": "Reset",
        "plan": "PLAN",
        "no_workers": "Nema radnika u bazi.",
        "no_clients": "Nema klijenata u bazi.",
        "no_shifts": "Trenutno nema unesenih smjena.",
        "edit": "Izmijeni",
        "delete": "Obriši",
        "week_calendar": "Sedmični kalendar",
        "pdf": "PDF raspored",
        "back": "← Nazad",
        "edit_shift": "Izmijeni smjenu",
        "save": "Sačuvaj",
        "time_placeholder": "Vrijeme, npr. 08:00-12:00",
        "pdf_title": "Raspored radnika",
        "pdf_user": "Korisnik",
        "pdf_date": "Datum",
        "pdf_time": "Vrijeme",
        "pdf_worker": "Radnik",
        "pdf_client": "Klijent",
        "pdf_no_shifts": "Nema smjena",
        "language": "Jezik"
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
        "add_worker": "Mitarbeiter hinzufügen",
        "add_client": "Kunde hinzufügen",
        "add_shift": "Einsatz hinzufügen",
        "worker_name": "Name des Mitarbeiters",
        "client_name": "Name des Kunden",
        "choose_worker": "Mitarbeiter wählen",
        "choose_client": "Kunden wählen",
        "date_filter": "Nach Datum filtern",
        "filter_btn": "Filtern",
        "reset": "Zurücksetzen",
        "plan": "PLANUNG",
        "no_workers": "Keine Mitarbeiter in der Datenbank.",
        "no_clients": "Keine Kunden in der Datenbank.",
        "no_shifts": "Keine Einsätze vorhanden.",
        "edit": "Bearbeiten",
        "delete": "Löschen",
        "week_calendar": "Wochenkalender",
        "pdf": "PDF Plan",
        "back": "← Zurück",
        "edit_shift": "Einsatz bearbeiten",
        "save": "Speichern",
        "time_placeholder": "Zeit, z. B. 08:00-12:00",
        "pdf_title": "Mitarbeiterplan",
        "pdf_user": "Benutzer",
        "pdf_date": "Datum",
        "pdf_time": "Zeit",
        "pdf_worker": "Mitarbeiter",
        "pdf_client": "Kunde",
        "pdf_no_shifts": "Keine Einsätze",
        "language": "Sprache"
    }
}


def get_lang():
    return session.get("lang", "fr")


def tr():
    return TRANSLATIONS.get(get_lang(), TRANSLATIONS["fr"])


def get_conn():
    conn = sqlite3.connect("db.sqlite")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker TEXT NOT NULL,
            client TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL
        )
    """)

    c.execute("INSERT OR IGNORE INTO workers (name) VALUES (?)", ("admin",))
    c.execute("INSERT OR IGNORE INTO workers (name) VALUES (?)", ("worker1",))

    conn.commit()
    conn.close()


init_db()


def get_workers_and_clients():
    conn = get_conn()
    c = conn.cursor()
    workers = c.execute("SELECT name FROM workers ORDER BY name").fetchall()
    clients = c.execute("SELECT name FROM clients ORDER BY name").fetchall()
    conn.close()
    return workers, clients


@app.route("/set_lang/<lang>")
def set_lang(lang):
    if lang in TRANSLATIONS:
        session["lang"] = lang
    return redirect(request.referrer or "/")


@app.route("/login", methods=["GET", "POST"])
def login():
    t = tr()
    error = ""

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        user = USERS.get(username)
        if user and user["password"] == password:
            session["user"] = username
            session["role"] = user["role"]
            return redirect("/")

        error = t["login_error"]

    return render_template_string("""
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 40px;
            background: #f4f6f8;
        }
        .langbar {
            max-width: 420px;
            margin: 0 auto 12px auto;
            text-align: right;
        }
        .langbar a {
            text-decoration: none;
            margin-left: 8px;
            font-weight: bold;
            color: #1f4f82;
        }
        .box {
            max-width: 420px;
            margin: auto;
            background: white;
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.08);
        }
        h2 { margin-top: 0; }
        input, button {
            width: 100%;
            padding: 12px;
            margin-top: 10px;
            box-sizing: border-box;
            border-radius: 8px;
        }
        input { border: 1px solid #cbd5e1; }
        button {
            background: #1f4f82;
            color: white;
            border: none;
            cursor: pointer;
        }
        .error { color: #b00020; margin-top: 10px; }
    </style>

    <div class="langbar">
        <a href="/set_lang/fr">FR</a>
        <a href="/set_lang/en">EN</a>
        <a href="/set_lang/bos">BOS</a>
        <a href="/set_lang/de">DE</a>
    </div>

    <div class="box">
        <h2>{{ t["login_title"] }}</h2>
        <form method="post">
            <input name="username" placeholder="{{ t['username'] }}" required>
            <input name="password" type="password" placeholder="{{ t['password'] }}" required>
            <button type="submit">{{ t["login_btn"] }}</button>
        </form>
        {% if error %}
            <div class="error">{{ error }}</div>
        {% endif %}
    </div>
    """, t=t, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
def index():
    if "user" not in session:
        return redirect("/login")

    t = tr()
    conn = get_conn()
    c = conn.cursor()

    workers = c.execute("SELECT name FROM workers ORDER BY name").fetchall()
    clients = c.execute("SELECT name FROM clients ORDER BY name").fetchall()

    date_filter = request.args.get("date", "").strip()
    selected_date = request.args.get("selected_date", "").strip()

    user = session["user"]
    role = session["role"]

    if role == "admin":
        if date_filter:
            shifts = c.execute(
                "SELECT * FROM shifts WHERE date = ? ORDER BY date, time",
                (date_filter,)
            ).fetchall()
        else:
            shifts = c.execute(
                "SELECT * FROM shifts ORDER BY date, time"
            ).fetchall()
    else:
        if date_filter:
            shifts = c.execute(
                "SELECT * FROM shifts WHERE worker = ? AND date = ? ORDER BY date, time",
                (user, date_filter)
            ).fetchall()
        else:
            shifts = c.execute(
                "SELECT * FROM shifts WHERE worker = ? ORDER BY date, time",
                (user,)
            ).fetchall()

    conn.close()

    return render_template_string("""
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 24px;
            background: #f4f6f8;
            color: #1f2937;
        }
        h1 {
            color: #1f4f82;
            margin-bottom: 8px;
        }
        .langbar {
            margin-bottom: 14px;
        }
        .langbar a {
            text-decoration: none;
            margin-right: 10px;
            font-weight: bold;
            color: #1f4f82;
        }
        .topbar {
            margin-bottom: 20px;
        }
        .topbar a {
            color: #1f4f82;
            text-decoration: none;
            font-weight: bold;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
        }
        .card {
            background: white;
            border-radius: 12px;
            padding: 18px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.06);
        }
        input, select, button {
            padding: 10px;
            margin: 6px 0;
            width: 100%;
            box-sizing: border-box;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
        }
        button {
            background: #1f4f82;
            color: white;
            border: none;
            cursor: pointer;
        }
        .shift {
            background: white;
            border-left: 5px solid #1f4f82;
            padding: 12px;
            margin: 10px 0;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        .action-link {
            text-decoration: none;
            margin-left: 10px;
            font-weight: bold;
        }
        .edit-link { color: #1f4f82; }
        .delete-link { color: #c62828; }
        .main-link {
            display: inline-block;
            margin-top: 12px;
            margin-right: 12px;
            text-decoration: none;
            color: #1f4f82;
            font-weight: bold;
        }
        .muted {
            color: #64748b;
            font-size: 14px;
        }
    </style>

    <div class="langbar">
        <strong>{{ t["language"] }}:</strong>
        <a href="/set_lang/fr">FR</a>
        <a href="/set_lang/en">EN</a>
        <a href="/set_lang/bos">BOS</a>
        <a href="/set_lang/de">DE</a>
    </div>

    <h1>{{ t["title"] }}</h1>
    <div class="topbar">
        {{ t["logged_as"] }}: <b>{{ session['user'] }}</b> ({{ session['role'] }})<br><br>
        <a href="/logout">{{ t["logout"] }}</a>
    </div>

    <div class="grid">
        {% if session['role'] == 'admin' %}
        <div class="card">
            <h3>{{ t["add_worker"] }}</h3>
            <form method="post" action="/add_worker">
                <input name="name" placeholder="{{ t['worker_name'] }}" required>
                <button>{{ t["add_worker"] }}</button>
            </form>
        </div>

        <div class="card">
            <h3>{{ t["add_client"] }}</h3>
            <form method="post" action="/add_client">
                <input name="name" placeholder="{{ t['client_name'] }}" required>
                <button>{{ t["add_client"] }}</button>
            </form>
        </div>
        {% endif %}

        <div class="card">
            <h3>{{ t["add_shift"] }}</h3>

            {% if workers|length == 0 %}
                <div class="muted">{{ t["no_workers"] }}</div>
            {% endif %}
            {% if clients|length == 0 %}
                <div class="muted">{{ t["no_clients"] }}</div>
            {% endif %}

            <form method="post" action="/add_shift">
                <select name="worker" required>
                    <option value="">{{ t["choose_worker"] }}</option>
                    {% for w in workers %}
                        <option value="{{ w['name'] }}">{{ w['name'] }}</option>
                    {% endfor %}
                </select>

                <select name="client" required>
                    <option value="">{{ t["choose_client"] }}</option>
                    {% for c in clients %}
                        <option value="{{ c['name'] }}">{{ c['name'] }}</option>
                    {% endfor %}
                </select>

                <input name="date" type="date" value="{{ selected_date }}" required>
                <input name="time" placeholder="{{ t['time_placeholder'] }}" required>
                <button>{{ t["add_shift"] }}</button>
            </form>
        </div>

        <div class="card">
            <h3>{{ t["date_filter"] }}</h3>
            <form method="get">
                <input type="date" name="date" value="{{ request.args.get('date', '') }}">
                <button>{{ t["filter_btn"] }}</button>
            </form>
            <a href="/">{{ t["reset"] }}</a>
        </div>
    </div>

    <div class="card" style="margin-top:20px;">
        <h2>{{ t["plan"] }}</h2>

        {% if shifts|length == 0 %}
            <div class="muted">{{ t["no_shifts"] }}</div>
        {% endif %}

        {% for s in shifts %}
            <div class="shift">
                <b>{{ s['date'] }}</b> | {{ s['time'] }}<br>
                👤 {{ s['worker'] }} → 🏢 {{ s['client'] }}
                {% if session['role'] == 'admin' %}
                    <a class="action-link edit-link" href="/edit_shift/{{ s['id'] }}">{{ t["edit"] }}</a>
                    <a class="action-link delete-link" href="/delete_shift/{{ s['id'] }}">{{ t["delete"] }}</a>
                {% endif %}
            </div>
        {% endfor %}

        <a class="main-link" href="/week">📅 {{ t["week_calendar"] }}</a>
        <a class="main-link" href="/export_pdf{% if request.args.get('date') %}?date={{ request.args.get('date') }}{% endif %}" target="_blank">📄 {{ t["pdf"] }}</a>
    </div>
    """, t=t, workers=workers, clients=clients, shifts=shifts, selected_date=selected_date)


@app.route("/delete_shift/<int:shift_id>")
def delete_shift(shift_id):
    if "user" not in session or session["role"] != "admin":
        return redirect("/")

    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))
    conn.commit()
    conn.close()
    return redirect("/")


@app.route("/edit_shift/<int:shift_id>", methods=["GET", "POST"])
def edit_shift(shift_id):
    if "user" not in session or session["role"] != "admin":
        return redirect("/")

    t = tr()
    conn = get_conn()
    c = conn.cursor()

    if request.method == "POST":
        worker = request.form["worker"].strip()
        client = request.form["client"].strip()
        date = request.form["date"].strip()
        time = request.form["time"].strip()

        c.execute("""
            UPDATE shifts
            SET worker = ?, client = ?, date = ?, time = ?
            WHERE id = ?
        """, (worker, client, date, time, shift_id))
        conn.commit()
        conn.close()
        return redirect("/")

    shift = c.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
    workers = c.execute("SELECT name FROM workers ORDER BY name").fetchall()
    clients = c.execute("SELECT name FROM clients ORDER BY name").fetchall()
    conn.close()

    if not shift:
        return redirect("/")

    return render_template_string("""
    <style>
        body { font-family: Arial, sans-serif; margin: 24px; background:#f4f6f8; }
        .langbar {
            max-width: 520px;
            margin: 0 auto 12px auto;
            text-align: right;
        }
        .langbar a {
            text-decoration: none;
            margin-left: 8px;
            font-weight: bold;
            color: #1f4f82;
        }
        .card {
            max-width: 520px;
            background:white;
            border-radius:12px;
            padding:20px;
            box-shadow:0 4px 14px rgba(0,0,0,0.06);
            margin:auto;
        }
        input, select, button {
            padding:10px;
            margin:6px 0;
            width:100%;
            box-sizing:border-box;
            border:1px solid #cbd5e1;
            border-radius:8px;
        }
        button {
            background:#1f4f82;
            color:white;
            border:none;
            cursor:pointer;
        }
        a { text-decoration:none; color:#1f4f82; font-weight:bold; }
    </style>

    <div class="langbar">
        <a href="/set_lang/fr">FR</a>
        <a href="/set_lang/en">EN</a>
        <a href="/set_lang/bos">BOS</a>
        <a href="/set_lang/de">DE</a>
    </div>

    <div class="card">
        <h2>{{ t["edit_shift"] }}</h2>
        <form method="post">
            <select name="worker" required>
                {% for w in workers %}
                    <option value="{{ w['name'] }}" {% if w['name'] == shift['worker'] %}selected{% endif %}>{{ w['name'] }}</option>
                {% endfor %}
            </select>

            <select name="client" required>
                {% for c in clients %}
                    <option value="{{ c['name'] }}" {% if c['name'] == shift['client'] %}selected{% endif %}>{{ c['name'] }}</option>
                {% endfor %}
            </select>

            <input type="date" name="date" value="{{ shift['date'] }}" required>
            <input type="text" name="time" value="{{ shift['time'] }}" required>

            <button type="submit">{{ t["save"] }}</button>
        </form>

        <br>
        <a href="/">{{ t["back"] }}</a>
    </div>
    """, t=t, shift=shift, workers=workers, clients=clients)


@app.route("/week")
def week_view():
    if "user" not in session:
        return redirect("/login")

    t = tr()
    conn = get_conn()
    c = conn.cursor()

    today = datetime.today()
    start_week = today - timedelta(days=today.weekday())
    week_days = [(start_week + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    user = session["user"]
    role = session["role"]

    if role == "admin":
        shifts = c.execute("SELECT * FROM shifts ORDER BY date, time").fetchall()
    else:
        shifts = c.execute(
            "SELECT * FROM shifts WHERE worker = ? ORDER BY date, time",
            (user,)
        ).fetchall()

    conn.close()

    return render_template_string("""
    <style>
        body { font-family: Arial, sans-serif; margin: 24px; background:#f4f6f8; }
        .langbar { margin-bottom: 12px; }
        .langbar a {
            text-decoration: none;
            margin-right: 10px;
            font-weight: bold;
            color: #1f4f82;
        }
        .week-wrap {
            display:flex;
            gap:12px;
            flex-wrap:wrap;
        }
        .day-card {
            background:white;
            border-radius:12px;
            padding:14px;
            width:180px;
            box-shadow:0 4px 14px rgba(0,0,0,0.06);
        }
        .day-link {
            text-decoration:none;
            color:#1f4f82;
            font-weight:bold;
            display:block;
            margin-bottom:8px;
        }
        .shift {
            background:#e8f1fb;
            margin-top:8px;
            padding:8px;
            border-radius:8px;
        }
        a { text-decoration:none; color:#1f4f82; font-weight:bold; }
    </style>

    <div class="langbar">
        <strong>{{ t["language"] }}:</strong>
        <a href="/set_lang/fr">FR</a>
        <a href="/set_lang/en">EN</a>
        <a href="/set_lang/bos">BOS</a>
        <a href="/set_lang/de">DE</a>
    </div>

    <h1>📅 {{ t["week_calendar"] }}</h1>
    <a href="/">{{ t["back"] }}</a><br><br>

    <div class="week-wrap">
        {% for day in week_days %}
            <div class="day-card">
                <a class="day-link" href="/?selected_date={{ day }}">{{ day }}</a>
                {% for s in shifts %}
                    {% if s['date'] == day %}
                        <div class="shift">
                            <b>{{ s['worker'] }}</b><br>
                            {{ s['client'] }}<br>
                            {{ s['time'] }}
                        </div>
                    {% endif %}
                {% endfor %}
            </div>
        {% endfor %}
    </div>
    """, t=t, week_days=week_days, shifts=shifts)


@app.route("/export_pdf")
def export_pdf():
    if "user" not in session:
        return redirect("/login")

    t = tr()
    conn = get_conn()
    c = conn.cursor()

    date_filter = request.args.get("date", "").strip()
    user = session["user"]
    role = session["role"]

    if role == "admin":
        if date_filter:
            shifts = c.execute(
                "SELECT * FROM shifts WHERE date = ? ORDER BY date, time",
                (date_filter,)
            ).fetchall()
        else:
            shifts = c.execute("SELECT * FROM shifts ORDER BY date, time").fetchall()
    else:
        if date_filter:
            shifts = c.execute(
                "SELECT * FROM shifts WHERE worker = ? AND date = ? ORDER BY date, time",
                (user, date_filter)
            ).fetchall()
        else:
            shifts = c.execute(
                "SELECT * FROM shifts WHERE worker = ? ORDER BY date, time",
                (user,)
            ).fetchall()

    conn.close()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm
    )

    styles = getSampleStyleSheet()
    elements = []

    title = t["pdf_title"]
    if date_filter:
        title += f" - {date_filter}"

    elements.append(Paragraph(title, styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"{t['pdf_user']}: {session['user']} ({session['role']})", styles["Normal"]))
    elements.append(Spacer(1, 12))

    table_data = [[t["pdf_date"], t["pdf_time"], t["pdf_worker"], t["pdf_client"]]]

    if shifts:
        for s in shifts:
            table_data.append([s["date"], s["time"], s["worker"], s["client"]])
    else:
        table_data.append(["-", "-", "-", t["pdf_no_shifts"]])

    table = Table(table_data, colWidths=[3.2 * cm, 3.2 * cm, 4.5 * cm, 6.0 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4f82")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#eaf2fb")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
    ]))

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    filename = "schedule.pdf" if not date_filter else f"schedule_{date_filter}.pdf"

    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )


@app.route("/add_worker", methods=["POST"])
def
