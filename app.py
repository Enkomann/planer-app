from flask import Flask, request, redirect, render_template_string, session
import sqlite3
import os
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "luxmann_secret_key"

# ---------------- USERS ----------------
USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "worker1": {"password": "1234", "role": "worker"}
}

# ---------------- DATABASE ----------------
def init_db():
    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY,
            name TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY,
            name TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY,
            worker TEXT,
            client TEXT,
            date TEXT,
            time TEXT
        )
    """)

    conn.commit()
    conn.close()

init_db()

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = USERS.get(request.form["username"])

        if user and user["password"] == request.form["password"]:
            session["user"] = request.form["username"]
            session["role"] = user["role"]
            return redirect("/")

        return "Login failed"

    return """
    <h2>Login</h2>
    <form method="post">
        <input name="username" placeholder="Username"><br><br>
        <input name="password" type="password" placeholder="Password"><br><br>
        <button>Login</button>
    </form>
    """

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ---------------- HOME ----------------
@app.route("/")
def index():

    if "user" not in session:
        return redirect("/login")

    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()

    date_filter = request.args.get("date")
    user = session["user"]
    role = session["role"]

    # FILTER
    if role == "admin":
        if date_filter:
            shifts = c.execute(
                "SELECT * FROM shifts WHERE date = ?",
                (date_filter,)
            ).fetchall()
        else:
            shifts = c.execute("SELECT * FROM shifts ORDER BY date").fetchall()
    else:
        if date_filter:
            shifts = c.execute(
                "SELECT * FROM shifts WHERE worker = ? AND date = ?",
                (user, date_filter)
            ).fetchall()
        else:
            shifts = c.execute(
                "SELECT * FROM shifts WHERE worker = ? ORDER BY date",
                (user,)
            ).fetchall()

    # DROPDOWN DATA
    workers = c.execute("SELECT name FROM workers").fetchall()
    clients = c.execute("SELECT name FROM clients").fetchall()

    conn.close()

    return render_template_string("""
    <style>
    body { font-family: Arial; margin: 20px; }
    h1 { color: #2c3e50; }
    button { padding: 5px 10px; }
    input, select { padding: 5px; margin: 3px; }
    a { text-decoration: none; }
    .card { background:#f9f9f9; padding:10px; margin:5px; border-radius:8px; }
    </style>

    <h1>PLAN RADNIKA</h1>

    <p>Logovan kao: {{session['user']}} ({{session['role']}})</p>
    <a href="/logout">Logout</a>

    <hr>

    {% if session['role'] == 'admin' %}
    <h3>Dodaj radnika</h3>
    <form method="post" action="/add_worker">
        <input name="name" placeholder="Ime radnika">
        <button>Dodaj</button>
    </form>

    <h3>Dodaj klijenta</h3>
    <form method="post" action="/add_client">
        <input name="name" placeholder="Klijent">
        <button>Dodaj</button>
    </form>
    {% endif %}

    <h3>Dodaj smjenu</h3>
    <form method="post" action="/add_shift">

        <select name="worker">
            {% for w in workers %}
                <option value="{{w[0]}}">{{w[0]}}</option>
            {% endfor %}
        </select>

        <select name="client">
            {% for c in clients %}
                <option value="{{c[0]}}">{{c[0]}}</option>
            {% endfor %}
        </select>

        <input name="date" type="date">
        <input name="time" placeholder="Vrijeme">
        <button>Dodaj</button>
    </form>

    <hr>

    <h3>Filter po datumu</h3>
    <form method="get">
        <input type="date" name="date">
        <button>Filtriraj</button>
    </form>

    <a href="/">Reset</a>

    <hr>

    <h2>PLAN</h2>

    {% for s in shifts %}
        <div class="card">
            <b>{{s[3]}}</b> | {{s[4]}}<br>
            👤 {{s[1]}} → 🏢 {{s[2]}}
            {% if session['role'] == 'admin' %}
                <a href="/delete_shift/{{s[0]}}" style="color:red;">❌</a>
            {% endif %}
        </div>
    {% endfor %}

    <br>
    <a href="/week">📅 Sedmični kalendar</a>
    """, shifts=shifts, workers=workers, clients=clients)

# ---------------- DELETE ----------------
@app.route("/delete_shift/<int:id>")
def delete_shift(id):

    if "user" not in session or session["role"] != "admin":
        return redirect("/")

    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()

    c.execute("DELETE FROM shifts WHERE id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect("/")

# ---------------- WEEK VIEW ----------------
@app.route("/week")
def week_view():

    if "user" not in session:
        return redirect("/login")

    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()

    today = datetime.today()
    start_week = today - timedelta(days=today.weekday())

    week_days = [
        (start_week + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(7)
    ]

    user = session["user"]
    role = session["role"]

    if role == "admin":
        shifts = c.execute("SELECT * FROM shifts").fetchall()
    else:
        shifts = c.execute(
            "SELECT * FROM shifts WHERE worker = ?",
            (user,)
        ).fetchall()

    conn.close()

    return render_template_string("""
    <h1>📅 Sedmični plan</h1>

    <a href="/">← Nazad</a>

    <div style="display:flex; gap:10px; flex-wrap:wrap;">

    {% for day in week_days %}
        <div style="border:1px solid #ccc; padding:10px; width:160px; border-radius:10px;">
            <h3>{{day}}</h3>

            {% for s in shifts %}
                {% if s[3] == day %}
                    <div style="background:#e3f2fd; margin:5px; padding:5px; border-radius:6px;">
                        <b>{{s[1]}}</b><br>
                        {{s[2]}}<br>
                        {{s[4]}}
                    </div>
                {% endif %}
            {% endfor %}
        </div>
    {% endfor %}

    </div>
    """, week_days=week_days, shifts=shifts)

# ---------------- ADD WORKER ----------------
@app.route("/add_worker", methods=["POST"])
def add_worker():
    if session.get("role") != "admin":
        return redirect("/")

    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()

    c.execute("INSERT INTO workers (name) VALUES (?)", (request.form["name"],))

    conn.commit()
    conn.close()

    return redirect("/")

# ---------------- ADD CLIENT ----------------
@app.route("/add_client", methods=["POST"])
def add_client():
    if session.get("role") != "admin":
        return redirect("/")

    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()

    c.execute("INSERT INTO clients (name) VALUES (?)", (request.form["name"],))

    conn.commit()
    conn.close()

    return redirect("/")

# ---------------- ADD SHIFT ----------------
@app.route("/add_shift", methods=["POST"])
def add_shift():
    if "user" not in session:
        return redirect("/login")

    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()

    c.execute("""
        INSERT INTO shifts (worker, client, date, time)
        VALUES (?, ?, ?, ?)
    """, (
        request.form["worker"],
        request.form["client"],
        request.form["date"],
        request.form["time"]
    ))

    conn.commit()
    conn.close()

    return redirect("/")

# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
