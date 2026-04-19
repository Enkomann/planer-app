from flask import Flask, request, redirect, render_template_string, session, send_file
import sqlite3
import os
import io
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle
)

app = Flask(__name__)
app.secret_key = "luxmann_secret_key"

USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "worker1": {"password": "1234", "role": "worker"}
}

def get_conn():
    return sqlite3.connect("db.sqlite")

def init_db():
    conn = get_conn()
    c = conn.cursor()

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
            time TEXT
        )
    """)

    c.execute("INSERT OR IGNORE INTO workers (name) VALUES (?)", ("admin",))
    c.execute("INSERT OR IGNORE INTO workers (name) VALUES (?)", ("worker1",))

    conn.commit()
    conn.close()

init_db()

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        user = USERS.get(username)
        if user and user["password"] == password:
            session["user"] = username
            session["role"] = user["role"]
            return redirect("/")

        error = "Pogrešan username ili password"

    return render_template_string("""
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 40px;
            background: #f4f6f8;
        }
        .box {
            max-width: 400px;
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
        input {
            border: 1px solid #cbd5e1;
        }
        button {
            background: #1f4f82;
            color: white;
            border: none;
            cursor: pointer;
        }
        .error {
            color: #b00020;
            margin-top: 10px;
        }
    </style>

    <div class="box">
        <h2>Prijava</h2>
        <form method="post">
            <input name="username" placeholder="Username" required>
            <input name="password" type="password" placeholder="Password" required>
            <button type="submit">Login</button>
        </form>
        {% if error %}
            <div class="error">{{ error }}</div>
        {% endif %}
    </div>
    """, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/")
def index():
    if "user" not in session:
        return redirect("/login")

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
        .week-link, .pdf-link {
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

    <h1>PLAN RADNIKA</h1>
    <div class="topbar">
        Logovan kao: <b>{{ session['user'] }}</b> ({{ session['role'] }})<br><br>
        <a href="/logout">Logout</a>
    </div>

    <div class="grid">
        {% if session['role'] == 'admin' %}
        <div class="card">
            <h3>Dodaj radnika</h3>
            <form method="post" action="/add_worker">
                <input name="name" placeholder="Ime radnika" required>
                <button>Dodaj</button>
            </form>
        </div>

        <div class="card">
            <h3>Dodaj klijenta</h3>
            <form method="post" action="/add_client">
                <input name="name" placeholder="Klijent" required>
                <button>Dodaj</button>
            </form>
        </div>
        {% endif %}

        <div class="card">
            <h3>Dodaj smjenu</h3>

            {% if workers|length == 0 %}
                <div class="muted">Nema radnika u bazi.</div>
            {% endif %}

            {% if clients|length == 0 %}
                <div class="muted">Nema klijenata u bazi.</div>
            {% endif %}

            <form method="post" action="/add_shift">
                <select name="worker" required>
                    <option value="">Izaberi radnika</option>
                    {% for w in workers %}
                        <option value="{{ w[0] }}">{{ w[0] }}</option>
                    {% endfor %}
                </select>

                <select name="client" required>
                    <option value="">Izaberi klijenta</option>
                    {% for c in clients %}
                        <option value="{{ c[0] }}">{{ c[0] }}</option>
                    {% endfor %}
                </select>

                <input name="date" type="date" value="{{ selected_date }}" required>
                <input name="time" placeholder="Vrijeme, npr. 08:00-12:00" required>
                <button>Dodaj smjenu</button>
            </form>
        </div>

        <div class="card">
            <h3>Filter po datumu</h3>
            <form method="get">
                <input type="date" name="date" value="{{ request.args.get('date', '') }}">
                <button>Filtriraj</button>
            </form>
            <a href="/">Reset</a>
        </div>
    </div>

    <div class="card" style="margin-top:20px;">
        <h2>PLAN</h2>

        {% if shifts|length == 0 %}
            <div class="muted">Trenutno nema unesenih smjena.</div>
        {% endif %}

        {% for s in shifts %}
            <div class="shift">
                <b>{{ s[3] }}</b> | {{ s[4] }}<br>
                👤 {{ s[1] }} → 🏢 {{ s[2] }}
                {% if session['role'] == 'admin' %}
                    <a class="action-link edit-link" href="/edit_shift/{{ s[0] }}">Izmijeni</a>
                    <a class="action-link delete-link" href="/delete_shift/{{ s[0] }}">Obriši</a>
                {% endif %}
            </div>
        {% endfor %}

        <a class="week-link" href="/week">📅 Sedmični kalendar</a>
        <a class="pdf-link" href="/export_pdf{% if request.args.get('date') %}?date={{ request.args.get('date') }}{% endif %}" target="_blank">📄 PDF raspored</a>
    </div>
    """, shifts=shifts, workers=workers, clients=clients, selected_date=selected_date)

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
        """, (worker, client, date, time, id))

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
        body { font-family: Arial, sans-serif; margin: 24px; background:#f4f6f8; }
        .card {
            max-width: 500px;
            background:white;
            border-radius:12px;
            padding:20px;
            box-shadow:0 4px 14px rgba(0,0,0,0.06);
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

    <div class="card">
        <h2>Izmijeni smjenu</h2>

        <form method="post">
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
            <input type="text" name="time" value="{{ shift[4] }}" required>

            <button type="submit">Sačuvaj</button>
        </form>

        <br>
        <a href="/">← Nazad</a>
    </div>
    """, shift=shift, workers=workers, clients=clients)

@app.route("/week")
def week_view():
    if "user" not in session:
        return redirect("/login")

    conn = get_conn()
    c = conn.cursor()

    today = datetime.today()
    start_week = today - timedelta(days=today.weekday())
    week_days = [(start_week + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    user = session["user"]
    role = session["role"]

    if role == "admin":
        shifts = c.execute("SELECT * FROM shifts").fetchall()
    else:
        shifts = c.execute("SELECT * FROM shifts WHERE worker = ?", (user,)).fetchall()

    conn.close()

    return render_template_string("""
    <style>
        body { font-family: Arial, sans-serif; margin: 24px; background:#f4f6f8; }
        .week-wrap { display:flex; gap:12px; flex-wrap:wrap; }
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

    <h1>📅 Sedmični plan</h1>
    <a href="/">← Nazad</a><br><br>

    <div class="week-wrap">
        {% for day in week_days %}
            <div class="day-card">
                <a class="day-link" href="/?selected_date={{ day }}">{{ day }}</a>
                {% for s in shifts %}
                    {% if s[3] == day %}
                        <div class="shift">
                            <b>{{ s[1] }}</b><br>
                            {{ s[2] }}<br>
                            {{ s[4] }}
                        </div>
                    {% endif %}
                {% endfor %}
            </div>
        {% endfor %}
    </div>
    """, week_days=week_days, shifts=shifts)

@app.route("/export_pdf")
def export_pdf():
    if "user" not in session:
        return redirect("/login")

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

    title = "Raspored radnika"
    if date_filter:
        title += f" - {date_filter}"

    elements.append(Paragraph(title, styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"Korisnik: {session['user']} ({session['role']})", styles["Normal"]))
    elements.append(Spacer(1, 12))

    table_data = [["Datum", "Vrijeme", "Radnik", "Klijent"]]

    if shifts:
        for s in shifts:
            table_data.append([s[3], s[4], s[1], s[2]])
    else:
        table_data.append(["-", "-", "-", "Nema smjena"])

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
    filename = "raspored_radnika.pdf" if not date_filter else f"raspored_{date_filter}.pdf"

    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )

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

    if not worker or not client or not date or not time:
        return redirect("/")

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO shifts (worker, client, date, time)
        VALUES (?, ?, ?, ?)
    """, (worker, client, date, time))
    conn.commit()
    conn.close()

    return redirect("/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
