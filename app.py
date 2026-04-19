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
    body{font-family:sans-serif;background:#f4f6f8;padding:20px}
    .card{background:white;padding:15px;border-radius:10px;margin:10px;box-shadow:0 2px 8px rgba(0,0,0,0.05)}
    button{background:#1f4f82;color:white;border:none;padding:10px;border-radius:6px}
    select,input{padding:8px;margin:5px;width:100%}
    </style>

    <div>
    <a href="/lang/fr">FR</a> |
    <a href="/lang/en">EN</a> |
    <a href="/lang/bos">BOS</a> |
    <a href="/lang/de">DE</a>
    </div>

    <h1>{{t["title"]}}</h1>
    <a href="/logout">{{t["logout"]}}</a>

    <div class="card">
    <h3>{{t["add"]}} {{t["shift"]}}</h3>
    <form method="post" action="/add">
    <select name="worker">{% for w in workers %}<option>{{w[0]}}</option>{% endfor %}</select>
    <select name="client">{% for c in clients %}<option>{{c[0]}}</option>{% endfor %}</select>
    <input type="date" name="date">
    <input name="time">
    <button>OK</button>
    </form>
    </div>

    <div class="card">
    <h2>{{t["title"]}}</h2>
    {% for s in shifts %}
    <div>
    {{s[3]}} | {{s[4]}} | {{s[1]}} → {{s[2]}}
    <a href="/del/{{s[0]}}">❌</a>
    </div>
    {% endfor %}
    </div>

    <a href="/week">{{t["calendar"]}}</a> |
    <a href="/pdf">{{t["pdf"]}}</a>

    """,t=t,workers=workers,clients=clients,shifts=shifts)

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
