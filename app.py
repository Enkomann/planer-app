from flask import Flask, request, redirect, render_template_string, session
import sqlite3
import os

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
        username = request.form["username"]
        password = request.form["password"]

        user = USERS.get(username)

        if user and user["password"] == password:
            session["user"] = username
            session["role"] = user["role"]
            return redirect("/")

        return "Login failed"

    return """
    <h2>Login</h2>
    <form method="post">
        <input name="username" placeholder="Username"><br><br>
        <input name="password" type="password" placeholder="Password"><br><br>
        <button type="submit">Login</button>
    </form>
    """

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

    if date_filter:
        shifts = c.execute(
            "SELECT * FROM shifts WHERE date = ?",
            (date_filter,)
        ).fetchall()
    else:
        shifts = c.execute("SELECT * FROM shifts ORDER BY date").fetchall()

    conn.close()

    return render_template_string("""
    <h1>PLAN RADNIKA</h1>

    <p>Logovan kao: {{session['user']}} ({{session['role']}})</p>
    <a href="/logout">Logout</a>

    <hr>

    <h2>Dodaj radnika</h2>
    <form method="post" action="/add_worker">
        <input name="name" placeholder="Ime radnika">
        <button>Dodaj</button>
    </form>

    <h2>Dodaj klijenta</h2>
    <form method="post" action="/add_client">
        <input name="name" placeholder="Klijent">
        <button>Dodaj</button>
    </form>

    <h2>Dodaj smjenu</h2>
    <form method="post" action="/add_shift">
        <input name="worker" placeholder="Radnik">
        <input name="client" placeholder="Klijent">
        <input name="date" placeholder="Datum">
        <input name="time" placeholder="Vrijeme">
        <button>Dodaj</button>
    </form>

    <hr>

    <h2>Filter po datumu</h2>

    <form method="get">
        <input type="date" name="date">
        <button>Filtriraj</button>
    </form>

    <a href="/">Reset</a>

    <hr>

    <h2>PLAN</h2>
    <ul>
    {% for s in shifts %}
        <li>{{s[3]}} | {{s[4]}} | {{s[1]}} → {{s[2]}}</li>
    {% endfor %}
    </ul>
    """, shifts=shifts)
# ---------------- ADD WORKER ----------------
@app.route("/add_worker", methods=["POST"])
def add_worker():
    if "user" not in session:
        return redirect("/login")

    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()

    c.execute("INSERT INTO workers (name) VALUES (?)", (request.form["name"],))

    conn.commit()
    conn.close()

    return redirect("/")

# ---------------- ADD CLIENT ----------------
@app.route("/add_client", methods=["POST"])
def add_client():
    if "user" not in session:
        return redirect("/login")

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
