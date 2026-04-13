from flask import Flask, request, redirect, render_template_string
import sqlite3

app = Flask(__name__)

def init_db():
    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()

    c.execute("CREATE TABLE IF NOT EXISTS workers (id INTEGER PRIMARY KEY, name TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS clients (id INTEGER PRIMARY KEY, name TEXT)")
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

HTML = """
<h1>PLAN RADNIKA</h1>

<h2>Dodaj radnika</h2>
<form method="post" action="/add_worker">
<input name="name" placeholder="Ime">
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
<input name="date" placeholder="Datum (2026-04-10)">
<input name="time" placeholder="Vrijeme">
<button>Dodaj</button>
</form>

<h2>PLAN</h2>
<ul>
{% for s in shifts %}
<li>{{s[3]}} | {{s[4]}} | {{s[1]}} → {{s[2]}}</li>
{% endfor %}
</ul>
"""

@app.route("/")
def index():
    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()
    shifts = c.execute("SELECT * FROM shifts").fetchall()
    conn.close()
    return render_template_string(HTML, shifts=shifts)

@app.route("/add_worker", methods=["POST"])
def add_worker():
    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()
    c.execute("INSERT INTO workers (name) VALUES (?)", (request.form["name"],))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/add_client", methods=["POST"])
def add_client():
    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()
    c.execute("INSERT INTO clients (name) VALUES (?)", (request.form["name"],))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/add_shift", methods=["POST"])
def add_shift():
    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()
    c.execute(
        "INSERT INTO shifts (worker, client, date, time) VALUES (?, ?, ?, ?)",
        (request.form["worker"], request.form["client"], request.form["date"], request.form["time"])
    )
    conn.commit()
    conn.close()
    return redirect("/")

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
