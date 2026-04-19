# ===== IMPORTS =====
from flask import Flask, request, redirect, render_template_string, session, send_file
import sqlite3, os, io
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm

# ===== APP =====
app = Flask(__name__)
app.secret_key = "luxmann_secret_key"

# ===== USERS =====
USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "worker1": {"password": "1234", "role": "worker"}
}

# ===== TRANSLATIONS =====
T = {
    "fr": {"title":"PLAN","login":"Connexion","logout":"Déconnexion","add":"Ajouter","worker":"Employé","client":"Client","shift":"Mission","calendar":"Calendrier","pdf":"PDF"},
    "en": {"title":"PLAN","login":"Login","logout":"Logout","add":"Add","worker":"Worker","client":"Client","shift":"Shift","calendar":"Calendar","pdf":"PDF"},
    "bos": {"title":"PLAN","login":"Prijava","logout":"Odjava","add":"Dodaj","worker":"Radnik","client":"Klijent","shift":"Smjena","calendar":"Kalendar","pdf":"PDF"},
    "de": {"title":"PLAN","login":"Login","logout":"Logout","add":"Hinzufügen","worker":"Mitarbeiter","client":"Kunde","shift":"Einsatz","calendar":"Kalender","pdf":"PDF"},
}

def tr():
    return T.get(session.get("lang","fr"))

# ===== DB =====
def db():
    return sqlite3.connect("db.sqlite")

def init():
    c=db().cursor()
    c.execute("CREATE TABLE IF NOT EXISTS workers(id INTEGER PRIMARY KEY,name TEXT UNIQUE)")
    c.execute("CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY,name TEXT UNIQUE)")
    c.execute("CREATE TABLE IF NOT EXISTS shifts(id INTEGER PRIMARY KEY,worker TEXT,client TEXT,date TEXT,time TEXT)")
    c.connection.commit()

init()

# ===== LANGUAGE =====
@app.route("/lang/<l>")
def lang(l):
    session["lang"]=l
    return redirect("/")

# ===== LOGIN =====
@app.route("/login", methods=["GET","POST"])
def login():
    t=tr()
    if request.method=="POST":
        u=request.form["u"]; p=request.form["p"]
        if u in USERS and USERS[u]["password"]==p:
            session["user"]=u
            session["role"]=USERS[u]["role"]
            return redirect("/")
    return f"""
    <h2>{t["login"]}</h2>
    <form method=post>
    <input name=u placeholder=user>
    <input name=p type=password placeholder=pass>
    <button>OK</button>
    </form>
    """

# ===== LOGOUT =====
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ===== HOME =====
@app.route("/")
def home():
    if "user" not in session: return redirect("/login")

    t=tr()
    c=db().cursor()

    workers=c.execute("SELECT name FROM workers").fetchall()
    clients=c.execute("SELECT name FROM clients").fetchall()
    shifts=c.execute("SELECT * FROM shifts ORDER BY date").fetchall()

    return render_template_string("""
    <style>
body {
    font-family: "Segoe UI", Arial;
    background: #f1f5f9;
    margin: 0;
}

.header {
    background: #1f4f82;
    color: white;
    padding: 15px 25px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.lang a {
    color: white;
    margin-right: 10px;
    text-decoration: none;
    font-weight: bold;
}

.container {
    padding: 25px;
}

.card {
    background: white;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 0 6px 18px rgba(0,0,0,0.06);
}

input, select {
    width: 100%;
    padding: 10px;
    margin-top: 8px;
    border-radius: 8px;
    border: 1px solid #cbd5e1;
}

button {
    margin-top: 10px;
    padding: 10px;
    background: #1f4f82;
    color: white;
    border: none;
    border-radius: 8px;
    cursor: pointer;
}

button:hover {
    background: #163d66;
}

.shift {
    background: #f8fafc;
    padding: 12px;
    margin-top: 10px;
    border-radius: 10px;
    border-left: 5px solid #1f4f82;
}

a {
    text-decoration: none;
}

.actions a {
    margin-left: 10px;
    font-size: 14px;
}

.delete { color: red; }
.edit { color: #1f4f82; }

.footer-links {
    margin-top: 15px;
}
</style>

<div class="header">
    <div>
        <b>Luxmann Planner</b>
    </div>
    <div class="lang">
        <a href="/lang/fr">FR</a>
        <a href="/lang/en">EN</a>
        <a href="/lang/bos">BOS</a>
        <a href="/lang/de">DE</a>
    </div>
</div>

<div class="container">

<h2>{{t["title"]}}</h2>
<a href="/logout">{{t["logout"]}}</a>

<div class="card">
<h3>{{t["add"]}} {{t["shift"]}}</h3>

<form method="post" action="/add">
<select name="worker">
{% for w in workers %}
<option>{{w[0]}}</option>
{% endfor %}
</select>

<select name="client">
{% for c in clients %}
<option>{{c[0]}}</option>
{% endfor %}
</select>

<input type="date" name="date">
<input name="time" placeholder="08:00 - 12:00">

<button>OK</button>
</form>
</div>

<div class="card">
<h3>{{t["title"]}}</h3>

{% for s in shifts %}
<div class="shift">
<b>{{s[3]}}</b> | {{s[4]}}<br>
👤 {{s[1]}} → 🏢 {{s[2]}}

<div class="actions">
<a class="edit" href="/edit_shift/{{s[0]}}">✏</a>
<a class="delete" href="/del/{{s[0]}}">❌</a>
</div>

</div>
{% endfor %}

<div class="footer-links">
<a href="/week">📅 {{t["calendar"]}}</a> |
<a href="/pdf">📄 {{t["pdf"]}}</a>
</div>

</div>

</div>
# ===== ADD =====
@app.route("/add",methods=["POST"])
def add():
    c=db().cursor()
    c.execute("INSERT INTO shifts(worker,client,date,time) VALUES(?,?,?,?)",
              (request.form["worker"],request.form["client"],request.form["date"],request.form["time"]))
    c.connection.commit()
    return redirect("/")

# ===== DELETE =====
@app.route("/del/<id>")
def del_shift(id):
    c=db().cursor()
    c.execute("DELETE FROM shifts WHERE id=?",(id,))
    c.connection.commit()
    return redirect("/")

# ===== WEEK =====
@app.route("/week")
def week():
    c=db().cursor()
    shifts=c.execute("SELECT * FROM shifts").fetchall()

    today=datetime.today()
    start=today-timedelta(days=today.weekday())
    days=[(start+timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    return render_template_string("""
    <h1>Week</h1>
    <a href="/">←</a>
    <div style="display:flex">
    {% for d in days %}
    <div style="margin:10px">
    <b>{{d}}</b>
    {% for s in shifts %}
    {% if s[3]==d %}
    <div>{{s[1]}}<br>{{s[2]}}</div>
    {% endif %}
    {% endfor %}
    </div>
    {% endfor %}
    </div>
    """,days=days,shifts=shifts)

# ===== PDF =====
@app.route("/pdf")
def pdf():
    c=db().cursor()
    shifts=c.execute("SELECT * FROM shifts").fetchall()

    buffer=io.BytesIO()
    doc=SimpleDocTemplate(buffer,pagesize=A4)
    elements=[]
    styles=getSampleStyleSheet()

    elements.append(Paragraph("Schedule",styles["Title"]))

    data=[["Date","Time","Worker","Client"]]
    for s in shifts:
        data.append([s[3],s[4],s[1],s[2]])

    table=Table(data)
    table.setStyle(TableStyle([("GRID",(0,0),(-1,-1),1,colors.black)]))
    elements.append(table)

    doc.build(elements)
    buffer.seek(0)

    return send_file(buffer,as_attachment=True,download_name="plan.pdf")

# ===== RUN =====
if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
