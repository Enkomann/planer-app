from flask import Flask, request, redirect, render_template_string, session, send_file, url_for, flash, after_this_request
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import re
import io
import os
import secrets
import calendar
import json
import math
import html as _html
import zipfile
import tempfile
import urllib.parse
import urllib.request
import unicodedata
from datetime import datetime, timedelta, date as dt_date
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

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
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if not SECRET_KEY:
    if os.environ.get("RENDER"):
        raise RuntimeError("SECRET_KEY must be set in Render Environment variables.")
    SECRET_KEY = "dev-only-change-me"
app.secret_key = SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER") or os.environ.get("SESSION_COOKIE_SECURE") == "1"),
)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "600")) * 1024 * 1024

@app.context_processor
def inject_header_globals():
    """Umetni pending notifikacije u svaki template."""
    pending_count = 0
    pending_items = []
    if session.get("user") and session.get("role") == "admin":
        try:
            conn = get_conn()
            c    = conn.cursor()
            rows = c.execute(
                "SELECT id, worker, type, date_from, date_to, note "
                "FROM leave_requests WHERE status='pending' ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            pending_count = len(rows)
            pending_items = [{"id": r[0], "worker": r[1], "type": r[2],
                               "dfrom": r[3], "dto": r[4], "note": r[5] or ""}
                             for r in rows]
        except Exception:
            pass
    lang = session.get("lang", "fr").upper()
    return {"hdr_pending_count": pending_count,
            "hdr_pending_items": pending_items,
            "hdr_lang": lang}

STORAGE_ROOT  = os.path.abspath(os.environ.get("STORAGE_ROOT", "storage"))
DOCUMENT_ROOT = os.path.join(STORAGE_ROOT, "documents")
SQLITE_PATH   = os.path.join(STORAGE_ROOT, "db.sqlite")
BACKUP_ROOT   = os.path.join(STORAGE_ROOT, "backups")
os.makedirs(DOCUMENT_ROOT, exist_ok=True)
os.makedirs(BACKUP_ROOT, exist_ok=True)
DOCUMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp", "doc", "docx", "xls", "xlsx", "csv", "txt"}
DOCUMENT_INLINE_MIME_PREFIXES = ("application/pdf", "image/")

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
        "duplicate_shift_warning": "Ova smjena sa istim radnicima, istim vremenom i istim klijentom vec postoji.",
        "nav_plan": "Plan", "nav_week": "Sedmica", "nav_month": "Mjesec",
        "nav_payroll": "Plate", "nav_diagram": "Dijagram", "nav_route": "Ruta",
        "nav_settings": "Postavke", "nav_docs_short": "Dok.",
        "nav_language": "Jezik", "nav_tools": "Alati",
        "nav_admin_section": "Administracija", "nav_account": "Racun",
        "nav_users": "Korisnici i lozinka", "nav_navigation": "Navigacija",
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
    "sick": "Maladie", "vacation": "Conge", "sick_vacation": "Maladie / Conge",
    "duplicate_shift_warning": "Cette mission avec les memes employes, horaires et client existe deja.",
    "nav_plan": "Plan", "nav_week": "Semaine", "nav_month": "Mois",
    "nav_payroll": "Salaires", "nav_diagram": "Graphique", "nav_route": "Itineraire",
    "nav_settings": "Reglages", "nav_docs_short": "Doc.",
    "nav_language": "Langue", "nav_tools": "Outils",
    "nav_admin_section": "Administration", "nav_account": "Compte",
    "nav_users": "Utilisateurs et mot de passe", "nav_navigation": "Navigation",
})
TRANSLATIONS["en"].update({
    "login_title": "Login", "login_btn": "Login", "logout": "Logout",
    "title": "WORK SCHEDULE", "add_worker": "Add worker", "add_client": "Add client",
    "add_shift": "Add shift", "workers": "Workers", "clients": "Clients",
    "nav_plan": "Plan", "nav_week": "Week", "nav_month": "Month",
    "nav_payroll": "Payroll", "nav_diagram": "Chart", "nav_route": "Route",
    "nav_settings": "Settings", "nav_docs_short": "Docs",
    "nav_language": "Language", "nav_tools": "Tools",
    "nav_admin_section": "Administration", "nav_account": "Account",
    "nav_users": "Users & password", "nav_navigation": "Navigation",
    "week_calendar": "Weekly calendar", "month_calendar": "Monthly calendar",
    "monthly_hours": "Monthly hours", "weekly_hours": "Weekly hours",
    "back": "Back", "save": "Save", "delete": "Delete", "edit": "Edit",
    "sick": "Sick leave", "vacation": "Vacation", "sick_vacation": "Sick leave / Vacation",
    "duplicate_shift_warning": "This shift with the same workers, time and client already exists.",
})
TRANSLATIONS["de"].update({
    "login_title": "Anmeldung", "login_btn": "Anmelden", "logout": "Abmelden",
    "title": "ARBEITSPLAN", "add_worker": "Mitarbeiter hinzufugen", "add_client": "Kunde hinzufugen",
    "add_shift": "Einsatz hinzufugen", "workers": "Mitarbeiter", "clients": "Kunden",
    "week_calendar": "Wochenkalender", "month_calendar": "Monatskalender",
    "monthly_hours": "Monatsstunden", "weekly_hours": "Wochenstunden",
    "back": "Zuruck", "save": "Speichern", "delete": "Loschen", "edit": "Bearbeiten",
    "sick": "Krankheit", "vacation": "Urlaub", "sick_vacation": "Krankheit / Urlaub",
    "duplicate_shift_warning": "Dieser Einsatz mit denselben Mitarbeitern, derselben Zeit und demselben Kunden existiert bereits.",
    "nav_plan": "Plan", "nav_week": "Woche", "nav_month": "Monat",
    "nav_payroll": "Gehalt", "nav_diagram": "Diagramm", "nav_route": "Route",
    "nav_settings": "Einst.", "nav_docs_short": "Dok.",
    "nav_language": "Sprache", "nav_tools": "Werkzeuge",
    "nav_admin_section": "Verwaltung", "nav_account": "Konto",
    "nav_users": "Benutzer und Passwort", "nav_navigation": "Navigation",
})
TRANSLATIONS["pt"].update({
    "login_title": "Entrar", "login_btn": "Entrar", "logout": "Sair",
    "title": "PLANO DE TRABALHO", "add_worker": "Adicionar trabalhador", "add_client": "Adicionar cliente",
    "add_shift": "Adicionar turno", "workers": "Trabalhadores", "clients": "Clientes",
    "week_calendar": "Calendario semanal", "month_calendar": "Calendario mensal",
    "monthly_hours": "Horas mensais", "weekly_hours": "Horas semanais",
    "back": "Voltar", "save": "Guardar", "delete": "Apagar", "edit": "Editar",
    "sick": "Baixa medica", "vacation": "Ferias", "sick_vacation": "Baixa / Ferias",
    "duplicate_shift_warning": "Este turno com os mesmos trabalhadores, horario e cliente ja existe.",
    "nav_plan": "Plano", "nav_week": "Semana", "nav_month": "Mes",
    "nav_payroll": "Salarios", "nav_diagram": "Grafico", "nav_route": "Rota",
    "nav_settings": "Definicoes", "nav_docs_short": "Doc.",
    "nav_language": "Idioma", "nav_tools": "Ferramentas",
    "nav_admin_section": "Administracao", "nav_account": "Conta",
    "nav_users": "Utilizadores e palavra-passe", "nav_navigation": "Navegacao",
})

ROUTE_TRANSLATIONS = {
    "bos": {
        "route_optimizer": "Optimizacija rute",
        "route_title": "Optimizacija rute po radniku",
        "route_desc": "Izaberi datum i radnika. Sistem predlaže redoslijed klijenata sa najmanje približne vožnje.",
        "start_address": "Početna adresa",
        "start_address_help": "Npr. adresa firme ili prvi polazak radnika",
        "optimize_route": "Optimizuj rutu",
        "optimized_order": "Predloženi redoslijed",
        "approx_distance": "Približna kilometraža",
        "open_in_maps": "Otvori u Google Maps",
        "route_warning": "Napomena: kilometraža je približna zračna udaljenost. Google Maps daje realnu vožnju.",
        "missing_address": "Nedostaje adresa",
        "geocode_failed": "Nije moguće pronaći koordinate za adresu",
        "no_route_shifts": "Nema smjena za izabrani datum/radnika.",
        "client_address": "Adresa klijenta",
        "api_missing": "ORS_API_KEY nije dodat u Render Environment.",
    },
    "en": {
        "route_optimizer": "Route optimizer",
        "route_title": "Route optimization by worker",
        "route_desc": "Choose a date and worker. The system suggests a client order with the lowest approximate travel distance.",
        "start_address": "Start address",
        "start_address_help": "For example company address or worker departure address",
        "optimize_route": "Optimize route",
        "optimized_order": "Suggested order",
        "approx_distance": "Approx. mileage",
        "open_in_maps": "Open in Google Maps",
        "route_warning": "Note: mileage is approximate straight-line distance. Google Maps provides real driving route.",
        "missing_address": "Missing address",
        "geocode_failed": "Could not find coordinates for address",
        "no_route_shifts": "No shifts for the selected date/worker.",
        "client_address": "Client address",
        "api_missing": "ORS_API_KEY is not set in Render Environment.",
    },
    "fr": {
        "route_optimizer": "Optimisation de tournée",
        "route_title": "Optimisation de tournée par employé",
        "route_desc": "Choisissez une date et un employé. Le système propose l’ordre des clients avec le moins de trajet approximatif.",
        "start_address": "Adresse de départ",
        "start_address_help": "Par exemple l’adresse de la société ou le départ de l’employé",
        "optimize_route": "Optimiser la tournée",
        "optimized_order": "Ordre proposé",
        "approx_distance": "Kilométrage approximatif",
        "open_in_maps": "Ouvrir dans Google Maps",
        "route_warning": "Remarque : le kilométrage est approximatif à vol d’oiseau. Google Maps donne le trajet réel.",
        "missing_address": "Adresse manquante",
        "geocode_failed": "Impossible de trouver les coordonnées pour l’adresse",
        "no_route_shifts": "Aucune mission pour la date/l’employé sélectionné.",
        "client_address": "Adresse client",
        "api_missing": "ORS_API_KEY n’est pas ajouté dans Render Environment.",
    },
    "de": {
        "route_optimizer": "Routenoptimierung",
        "route_title": "Routenoptimierung nach Mitarbeiter",
        "route_desc": "Wählen Sie Datum und Mitarbeiter. Das System schlägt eine Kundenreihenfolge mit möglichst geringer ungefährer Fahrstrecke vor.",
        "start_address": "Startadresse",
        "start_address_help": "Zum Beispiel Firmenadresse oder Startadresse des Mitarbeiters",
        "optimize_route": "Route optimieren",
        "optimized_order": "Vorgeschlagene Reihenfolge",
        "approx_distance": "Ungefähre Kilometer",
        "open_in_maps": "In Google Maps öffnen",
        "route_warning": "Hinweis: Kilometer sind ungefähre Luftlinienentfernungen. Google Maps zeigt die reale Fahrstrecke.",
        "missing_address": "Adresse fehlt",
        "geocode_failed": "Koordinaten für die Adresse konnten nicht gefunden werden",
        "no_route_shifts": "Keine Einsätze für das gewählte Datum/den Mitarbeiter.",
        "client_address": "Kundenadresse",
        "api_missing": "ORS_API_KEY ist nicht in Render Environment gesetzt.",
    },
    "pt": {
        "route_optimizer": "Otimização de rota",
        "route_title": "Otimização de rota por trabalhador",
        "route_desc": "Escolha uma data e um trabalhador. O sistema sugere a ordem dos clientes com a menor distância aproximada.",
        "start_address": "Endereço inicial",
        "start_address_help": "Por exemplo, endereço da empresa ou ponto de partida do trabalhador",
        "optimize_route": "Otimizar rota",
        "optimized_order": "Ordem sugerida",
        "approx_distance": "Quilometragem aproximada",
        "open_in_maps": "Abrir no Google Maps",
        "route_warning": "Nota: a quilometragem é uma distância aproximada em linha reta. Google Maps dá o trajeto real.",
        "missing_address": "Endereço em falta",
        "geocode_failed": "Não foi possível encontrar coordenadas para o endereço",
        "no_route_shifts": "Não há turnos para a data/trabalhador selecionado.",
        "client_address": "Endereço do cliente",
        "api_missing": "ORS_API_KEY não foi adicionado no Render Environment.",
    },
}
for _lang, _values in ROUTE_TRANSLATIONS.items():
    TRANSLATIONS[_lang].update(_values)

PRO_UI_TRANSLATIONS = {
    "bos": {
        "dashboard": "Dashboard", "overview": "Pregled", "quick_actions": "Brze akcije",
        "today_shifts": "Današnje smjene", "active_workers": "Aktivni radnici", "registered_clients": "Klijenti",
        "this_month_hours": "Sati ovog mjeseca", "today": "Danas", "management": "Administracija",
        "planning_tools": "Alati za planiranje", "professional_menu": "Profesionalni meni",
        "open_maps": "Otvori mapu", "route_distance_return": "Kilometraža sa povratkom",
        "copy_active": "Copy aktivan - klikni + Paste na željeni datum.", "clear": "Poništi",
        "add_data": "Unos podataka", "reports_exports": "Izvještaji i export",
    },
    "fr": {
        "dashboard": "Tableau de bord", "overview": "Aperçu", "quick_actions": "Actions rapides",
        "today_shifts": "Missions aujourd’hui", "active_workers": "Employés actifs", "registered_clients": "Clients",
        "this_month_hours": "Heures ce mois-ci", "today": "Aujourd’hui", "management": "Administration",
        "planning_tools": "Outils de planification", "professional_menu": "Menu professionnel",
        "open_maps": "Ouvrir la carte", "route_distance_return": "Kilométrage avec retour",
        "copy_active": "Copie active - cliquez sur + Paste à la date souhaitée.", "clear": "Annuler",
        "add_data": "Saisie des données", "reports_exports": "Rapports et export",
        "logged_as": "Connecté en tant que", "username": "Nom d’utilisateur", "password": "Mot de passe",
        "worker_name": "Nom de l’employé", "client_name": "Nom du client", "address": "Adresse",
        "choose_worker": "Choisir l’employé", "choose_client": "Choisir le client", "filter_btn": "Filtrer",
        "reset": "Réinitialiser", "plan": "PLANNING", "no_shifts": "Aucune mission enregistrée.",
        "copy": "Copier", "copy_shift": "Copier la mission", "paste": "+ Coller",
        "pdf": "PDF planning", "month_pdf": "PDF calendrier mensuel", "pdf_title": "Planning des employés",
        "pdf_user": "Utilisateur", "pdf_date": "Date", "pdf_time": "Heure", "pdf_worker": "Employés",
        "pdf_client": "Client", "pdf_no_shifts": "Aucune mission", "user_mgmt": "Gestion des utilisateurs",
        "add_user": "Ajouter utilisateur", "role_admin": "admin", "role_worker": "employé",
        "existing_users": "Utilisateurs existants", "delete_user": "Supprimer utilisateur",
        "status": "Statut", "status_planned": "Planifié", "status_in_progress": "En cours", "status_done": "Terminé",
        "monthly_absence_days": "Jours d’absence mensuels", "hours": "heures", "days": "jours",
        "all_workers": "Tous les employés", "all_clients": "Tous les clients", "theme": "Thème",
        "light_theme": "Clair", "dark_theme": "Sombre", "worker_colors": "Couleurs des employés",
        "update_color": "Mettre à jour", "prev_month": "Mois précédent", "next_month": "Mois suivant",
        "prev_week": "Semaine précédente", "next_week": "Semaine suivante", "current_week": "Semaine actuelle",
        "change_password": "Changer le mot de passe", "new_password": "Nouveau mot de passe",
        "search_shifts": "Recherche des missions", "search_placeholder": "Rechercher client, employé, heure...",
        "week_period": "Période", "menu": "Menu", "start_time": "Début", "end_time": "Fin",
        "team": "Équipe", "add_holiday": "Ajouter jour férié / non ouvré", "holiday_name": "Nom du jour",
        "holiday": "Jour férié", "absence_type": "Type d’absence", "other_absence": "Autre",
        "date_from": "Du", "date_to": "Au", "note": "Note", "add_absence": "Ajouter absence",
        "active_absences": "Absences enregistrées", "monday": "Lun", "tuesday": "Mar", "wednesday": "Mer",
        "thursday": "Jeu", "friday": "Ven", "saturday": "Sam", "sunday": "Dim", "cancel": "Annuler",
    },
    "en": {
        "dashboard": "Dashboard", "overview": "Overview", "quick_actions": "Quick actions",
        "today_shifts": "Today’s shifts", "active_workers": "Active workers", "registered_clients": "Clients",
        "this_month_hours": "Hours this month", "today": "Today", "management": "Management",
        "planning_tools": "Planning tools", "professional_menu": "Professional menu",
        "open_maps": "Open map", "route_distance_return": "Mileage with return",
        "copy_active": "Copy active - click + Paste on the desired date.", "clear": "Clear",
        "add_data": "Data entry", "reports_exports": "Reports and export",
        "worker_name": "Worker name", "client_name": "Client name", "choose_worker": "Choose worker",
        "choose_client": "Choose client", "filter_btn": "Filter", "reset": "Reset", "plan": "SCHEDULE",
        "no_shifts": "No shifts entered yet.", "copy": "Copy", "paste": "+ Paste", "pdf": "PDF schedule",
        "user_mgmt": "User management", "existing_users": "Existing users", "status_planned": "Planned",
        "status_in_progress": "In progress", "status_done": "Done", "all_workers": "All workers",
        "all_clients": "All clients", "theme": "Theme", "light_theme": "Light", "dark_theme": "Dark",
        "worker_colors": "Worker colors", "update_color": "Update color", "change_password": "Change password",
        "new_password": "New password", "search_shifts": "Shift search", "menu": "Menu", "team": "Team",
        "other_absence": "Other", "date_from": "From date", "date_to": "To date", "note": "Note",
        "add_absence": "Add absence", "active_absences": "Registered absences",
    },
    "de": {
        "dashboard": "Dashboard", "overview": "Übersicht", "quick_actions": "Schnellaktionen",
        "today_shifts": "Heutige Einsätze", "active_workers": "Aktive Mitarbeiter", "registered_clients": "Kunden",
        "this_month_hours": "Stunden diesen Monat", "today": "Heute", "management": "Verwaltung",
        "planning_tools": "Planungstools", "professional_menu": "Professionelles Menü",
        "open_maps": "Karte öffnen", "route_distance_return": "Kilometer mit Rückfahrt",
        "copy_active": "Kopie aktiv - klicken Sie + Einfügen am gewünschten Datum.", "clear": "Zurücksetzen",
        "add_data": "Dateneingabe", "reports_exports": "Berichte und Export",
        "status_planned": "Geplant", "status_in_progress": "In Arbeit", "status_done": "Fertig",
        "choose_worker": "Mitarbeiter auswählen", "choose_client": "Kunde auswählen", "filter_btn": "Filtern",
        "reset": "Zurücksetzen", "plan": "PLAN", "no_shifts": "Keine Einsätze vorhanden.",
        "team": "Team", "menu": "Menü", "open_in_maps": "In Google Maps öffnen",
    },
    "pt": {
        "dashboard": "Painel", "overview": "Visão geral", "quick_actions": "Ações rápidas",
        "today_shifts": "Turnos de hoje", "active_workers": "Trabalhadores ativos", "registered_clients": "Clientes",
        "this_month_hours": "Horas este mês", "today": "Hoje", "management": "Administração",
        "planning_tools": "Ferramentas de planeamento", "professional_menu": "Menu profissional",
        "open_maps": "Abrir mapa", "route_distance_return": "Quilometragem com regresso",
        "copy_active": "Cópia ativa - clique + Colar na data desejada.", "clear": "Limpar",
        "add_data": "Entrada de dados", "reports_exports": "Relatórios e exportação",
        "status_planned": "Planeado", "status_in_progress": "Em curso", "status_done": "Concluído",
        "choose_worker": "Escolher trabalhador", "choose_client": "Escolher cliente", "filter_btn": "Filtrar",
        "reset": "Repor", "plan": "PLANO", "no_shifts": "Ainda não há turnos.",
        "team": "Equipa", "menu": "Menu", "open_in_maps": "Abrir no Google Maps",
    },
}
for _lang, _values in PRO_UI_TRANSLATIONS.items():
    TRANSLATIONS[_lang].update(_values)

LANGUAGE_COMPLETION = {
    "de": {
        "username": "Benutzername", "password": "Passwort", "login_error": "Falscher Benutzername oder falsches Passwort",
        "logged_as": "Angemeldet als", "worker_name": "Name des Mitarbeiters", "client_name": "Name des Kunden",
        "address": "Adresse", "choose_worker": "Mitarbeiter auswaehlen", "choose_client": "Kunden auswaehlen",
        "filter_btn": "Filtern", "reset": "Zuruecksetzen", "plan": "PLAN", "no_shifts": "Keine Einsaetze vorhanden.",
        "copy": "Kopieren", "copy_shift": "Einsatz kopieren", "paste": "+ Einfuegen", "pdf": "PDF Arbeitsplan",
        "month_pdf": "PDF Monatskalender", "pdf_title": "Arbeitsplan der Mitarbeiter", "pdf_user": "Benutzer",
        "pdf_date": "Datum", "pdf_time": "Zeit", "pdf_worker": "Mitarbeiter", "pdf_client": "Kunde",
        "pdf_no_shifts": "Keine Einsaetze", "user_mgmt": "Benutzerverwaltung", "add_user": "Benutzer hinzufuegen",
        "role_admin": "admin", "role_worker": "Mitarbeiter", "existing_users": "Bestehende Benutzer",
        "delete_user": "Benutzer loeschen", "status": "Status", "monthly_absence_days": "Monatliche Abwesenheitstage",
        "hours": "Stunden", "days": "Tage", "all_workers": "Alle Mitarbeiter", "all_clients": "Alle Kunden",
        "theme": "Design", "light_theme": "Hell", "dark_theme": "Dunkel", "worker_colors": "Mitarbeiterfarben",
        "update_color": "Farbe aktualisieren", "prev_month": "Vorheriger Monat", "next_month": "Naechster Monat",
        "prev_week": "Vorherige Woche", "next_week": "Naechste Woche", "current_week": "Aktuelle Woche",
        "change_password": "Passwort aendern", "new_password": "Neues Passwort", "search_shifts": "Einsaetze suchen",
        "search_placeholder": "Nach Kunde, Mitarbeiter, Zeit suchen...", "week_period": "Zeitraum",
        "start_time": "Beginn", "end_time": "Ende", "add_holiday": "Feiertag / arbeitsfreien Tag hinzufuegen",
        "holiday_name": "Name des Feiertags", "holiday": "Feiertag", "sick_vacation": "Krankheit / Urlaub",
        "absence_type": "Art der Abwesenheit", "sick": "Krankheit", "vacation": "Urlaub", "other_absence": "Andere",
        "date_from": "Von", "date_to": "Bis", "note": "Notiz", "add_absence": "Abwesenheit hinzufuegen",
        "active_absences": "Eingetragene Abwesenheiten", "monday": "Mo", "tuesday": "Di", "wednesday": "Mi",
        "thursday": "Do", "friday": "Fr", "saturday": "Sa", "sunday": "So", "cancel": "Abbrechen",
    },
    "pt": {
        "username": "Nome de utilizador", "password": "Palavra-passe", "login_error": "Nome de utilizador ou palavra-passe incorretos",
        "logged_as": "Sessao iniciada como", "worker_name": "Nome do trabalhador", "client_name": "Nome do cliente",
        "address": "Endereco", "choose_worker": "Escolher trabalhadores", "choose_client": "Escolher cliente",
        "filter_btn": "Filtrar", "reset": "Repor", "plan": "PLANO", "no_shifts": "Ainda nao ha turnos registados.",
        "copy": "Copiar", "copy_shift": "Copiar turno", "paste": "+ Colar", "pdf": "PDF do plano",
        "month_pdf": "PDF calendario mensal", "pdf_title": "Plano dos trabalhadores", "pdf_user": "Utilizador",
        "pdf_date": "Data", "pdf_time": "Hora", "pdf_worker": "Trabalhadores", "pdf_client": "Cliente",
        "pdf_no_shifts": "Sem turnos", "user_mgmt": "Gestao de utilizadores", "add_user": "Adicionar utilizador",
        "role_admin": "admin", "role_worker": "trabalhador", "existing_users": "Utilizadores existentes",
        "delete_user": "Apagar utilizador", "status": "Estado", "status_planned": "Planeado",
        "status_in_progress": "Em curso", "status_done": "Concluido", "monthly_absence_days": "Dias de ausencia mensais",
        "hours": "horas", "days": "dias", "all_workers": "Todos os trabalhadores", "all_clients": "Todos os clientes",
        "theme": "Tema", "light_theme": "Claro", "dark_theme": "Escuro", "worker_colors": "Cores dos trabalhadores",
        "update_color": "Atualizar cor", "prev_month": "Mes anterior", "next_month": "Mes seguinte",
        "prev_week": "Semana anterior", "next_week": "Semana seguinte", "current_week": "Semana atual",
        "change_password": "Alterar palavra-passe", "new_password": "Nova palavra-passe", "search_shifts": "Pesquisa de turnos",
        "search_placeholder": "Pesquisar por cliente, trabalhador, hora...", "week_period": "Periodo",
        "start_time": "Inicio", "end_time": "Fim", "team": "Equipa", "add_holiday": "Adicionar feriado / dia nao util",
        "holiday_name": "Nome do feriado", "holiday": "Feriado", "sick_vacation": "Baixa / Ferias",
        "absence_type": "Tipo de ausencia", "sick": "Baixa medica", "vacation": "Ferias", "other_absence": "Outro",
        "date_from": "De", "date_to": "Ate", "note": "Nota", "add_absence": "Adicionar ausencia",
        "active_absences": "Ausencias registadas", "monday": "Seg", "tuesday": "Ter", "wednesday": "Qua",
        "thursday": "Qui", "friday": "Sex", "saturday": "Sab", "sunday": "Dom", "cancel": "Cancelar",
    },
}
for _lang, _values in LANGUAGE_COMPLETION.items():
    TRANSLATIONS[_lang].update(_values)

TRANSLATIONS["bos"].update({"navigate_to_address": "Pokreni Google Maps do adrese"})
TRANSLATIONS["en"].update({"navigate_to_address": "Start Google Maps to address"})
TRANSLATIONS["fr"].update({"navigate_to_address": "Lancer Google Maps vers l'adresse"})
TRANSLATIONS["de"].update({"navigate_to_address": "Google Maps zur Adresse starten"})
TRANSLATIONS["pt"].update({"navigate_to_address": "Abrir Google Maps ate ao endereco"})

TRANSLATIONS["bos"].update({
    "contract_type": "Vrsta ugovora", "contract_end_date": "Istek ugovora",
    "contract_reminders": "Podsjetnik", "contract_expired": "Ugovor je istekao",
    "contract_expires_soon": "Ugovor uskoro istice", "worked_hours": "Odradjeni sati",
    "leave_request": "Zahtjev za odmor", "leave_type_vacation": "Placeni odmor",
    "leave_type_sick": "Bolovanje", "leave_type_other": "Slobodan dan",
    "leave_date_from": "Od datuma", "leave_date_to": "Do datuma",
    "leave_note": "Napomena", "leave_send": "Posalji zahtjev",
    "leave_my_requests": "Moji zahtjevi", "leave_pending": "Na cekanju",
    "leave_approved": "Odobren", "leave_rejected": "Odbijen",
    "leave_requests_pending": "Zahtjevi za odmor", "leave_approve": "Odobri",
    "leave_reject": "Odbij", "leave_no_requests": "Nema zahtjeva",
    "archive": "Arhiva", "shifts": "smjena", "shift_singular": "smjena",
    "inv_gen_ok": "{n} faktura generisano", "inv_gen_exists": "{n} već postoji za ovaj period",
    "inv_gen_no_rate": "Bez postavljene cijene", "inv_gen_empty": "Nema smjena ili klijenata sa postavljenom cijenom.",
    "inv_convert_banner": "Uređuješ automatski generisanu fakturu br. {num} — sačuvaj da pretvoriš u ručnu fakturu.",
    "zip_unavail_title": "Dokumenti privremeno nisu dostupni",
    "zip_unavail_body": "Zatraženi fajlovi trenutno nisu dostupni. Molite administratora da vam pošalje fajlove direktno.",
    "file_unavail_title": "Fajl privremeno nije dostupan",
    "file_unavail_body": "Traženi fajl trenutno nije dostupan. Molite administratora.",
    "folder_empty_title": "Folder ne sadrži dokumente",
    "folder_empty_body": "Nema fajlova za preuzimanje u ovom folderu.",
})
TRANSLATIONS["en"].update({
    "contract_type": "Contract type", "contract_end_date": "Contract end date",
    "contract_reminders": "Reminders", "contract_expired": "Contract expired",
    "contract_expires_soon": "Contract expires soon", "worked_hours": "Worked hours",
    "leave_request": "Leave request", "leave_type_vacation": "Paid leave",
    "leave_type_sick": "Sick leave", "leave_type_other": "Day off",
    "leave_date_from": "From", "leave_date_to": "To",
    "leave_note": "Note", "leave_send": "Send request",
    "leave_my_requests": "My requests", "leave_pending": "Pending",
    "leave_approved": "Approved", "leave_rejected": "Rejected",
    "leave_requests_pending": "Leave requests", "leave_approve": "Approve",
    "leave_reject": "Reject", "leave_no_requests": "No requests",
    "archive": "Archive", "shifts": "shifts", "shift_singular": "shift",
    "inv_gen_ok": "{n} invoices generated", "inv_gen_exists": "{n} already exist for this period",
    "inv_gen_no_rate": "No rate set", "inv_gen_empty": "No shifts or clients with a rate in this period.",
    "inv_convert_banner": "Editing auto-generated invoice #{num} — save to convert it to a manual invoice.",
    "zip_unavail_title": "Documents temporarily unavailable",
    "zip_unavail_body": "The requested files are currently unavailable. Please contact the administrator to send them directly.",
    "file_unavail_title": "File temporarily unavailable",
    "file_unavail_body": "The requested file is currently unavailable. Please contact the administrator.",
    "folder_empty_title": "Folder contains no documents",
    "folder_empty_body": "There are no files to download in this folder.",
})
TRANSLATIONS["fr"].update({
    "contract_type": "Type de contrat", "contract_end_date": "Fin du contrat",
    "contract_reminders": "Rappels", "contract_expired": "Contrat expire",
    "contract_expires_soon": "Contrat bientot expire", "worked_hours": "Heures travaillees",
    "leave_request": "Demande de conge", "leave_type_vacation": "Conge paye",
    "leave_type_sick": "Arret maladie", "leave_type_other": "Jour de repos",
    "leave_date_from": "Du", "leave_date_to": "Au",
    "leave_note": "Remarque", "leave_send": "Envoyer la demande",
    "leave_my_requests": "Mes demandes", "leave_pending": "En attente",
    "leave_approved": "Approuvee", "leave_rejected": "Refusee",
    "leave_requests_pending": "Demandes de conge", "leave_approve": "Approuver",
    "leave_reject": "Refuser", "leave_no_requests": "Aucune demande",
    "archive": "Archives", "shifts": "interventions", "shift_singular": "intervention",
    "inv_gen_ok": "{n} factures generees", "inv_gen_exists": "{n} existent deja pour cette periode",
    "inv_gen_no_rate": "Tarif non defini", "inv_gen_empty": "Aucune prestation ou tarif client absent.",
    "inv_convert_banner": "Modification facture auto n°{num} — sauvegarder pour convertir en facture manuelle.",
    "zip_unavail_title": "Documents temporairement indisponibles",
    "zip_unavail_body": "Les fichiers demandés sont actuellement indisponibles. Veuillez contacter l'administrateur.",
    "file_unavail_title": "Fichier temporairement indisponible",
    "file_unavail_body": "Le fichier demandé est actuellement indisponible. Veuillez contacter l'administrateur.",
    "folder_empty_title": "Dossier vide",
    "folder_empty_body": "Il n'y a aucun fichier à télécharger dans ce dossier.",
})
TRANSLATIONS["de"].update({
    "contract_type": "Vertragsart", "contract_end_date": "Vertragsende",
    "contract_reminders": "Erinnerungen", "contract_expired": "Vertrag abgelaufen",
    "contract_expires_soon": "Vertrag laeuft bald ab", "worked_hours": "Geleistete Stunden",
    "leave_request": "Urlaubsantrag", "leave_type_vacation": "Bezahlter Urlaub",
    "leave_type_sick": "Krankmeldung", "leave_type_other": "Freier Tag",
    "leave_date_from": "Von", "leave_date_to": "Bis",
    "leave_note": "Notiz", "leave_send": "Antrag senden",
    "leave_my_requests": "Meine Antraege", "leave_pending": "Ausstehend",
    "leave_approved": "Genehmigt", "leave_rejected": "Abgelehnt",
    "leave_requests_pending": "Urlaubsantraege", "leave_approve": "Genehmigen",
    "leave_reject": "Ablehnen", "leave_no_requests": "Keine Antraege",
    "archive": "Archiv", "shifts": "Schichten", "shift_singular": "Schicht",
    "inv_gen_ok": "{n} Rechnungen erstellt", "inv_gen_exists": "{n} bereits vorhanden fuer diesen Zeitraum",
    "inv_gen_no_rate": "Kein Tarif festgelegt", "inv_gen_empty": "Keine Schichten oder Tarife fuer diesen Zeitraum.",
    "inv_convert_banner": "Auto-Rechnung Nr. {num} bearbeiten — speichern zum Umwandeln in manuelle Rechnung.",
    "zip_unavail_title": "Dokumente vorübergehend nicht verfügbar",
    "zip_unavail_body": "Die angeforderten Dateien sind derzeit nicht verfügbar. Bitte wenden Sie sich an den Administrator.",
    "file_unavail_title": "Datei vorübergehend nicht verfügbar",
    "file_unavail_body": "Die angeforderte Datei ist derzeit nicht verfügbar. Bitte wenden Sie sich an den Administrator.",
    "folder_empty_title": "Ordner enthält keine Dokumente",
    "folder_empty_body": "In diesem Ordner gibt es keine Dateien zum Herunterladen.",
})
TRANSLATIONS["pt"].update({
    "contract_type": "Tipo de contrato", "contract_end_date": "Fim do contrato",
    "contract_reminders": "Lembretes", "contract_expired": "Contrato expirado",
    "contract_expires_soon": "Contrato termina em breve", "worked_hours": "Horas trabalhadas",
    "leave_request": "Pedido de ferias", "leave_type_vacation": "Ferias pagas",
    "leave_type_sick": "Baixa medica", "leave_type_other": "Dia de folga",
    "leave_date_from": "De", "leave_date_to": "Ate",
    "leave_note": "Nota", "leave_send": "Enviar pedido",
    "leave_my_requests": "Os meus pedidos", "leave_pending": "Pendente",
    "leave_approved": "Aprovado", "leave_rejected": "Recusado",
    "leave_requests_pending": "Pedidos de ferias", "leave_approve": "Aprovar",
    "leave_reject": "Recusar", "leave_no_requests": "Sem pedidos",
    "archive": "Arquivo", "shifts": "turnos", "shift_singular": "turno",
    "inv_gen_ok": "{n} faturas geradas", "inv_gen_exists": "{n} ja existem para este periodo",
    "inv_gen_no_rate": "Tarifa nao definida", "inv_gen_empty": "Sem servicos ou tarifas definidas para este periodo.",
    "inv_convert_banner": "A editar fatura automatica n.°{num} — guarde para converter em fatura manual.",
    "zip_unavail_title": "Documentos temporariamente indisponíveis",
    "zip_unavail_body": "Os ficheiros solicitados estão indisponíveis. Por favor contacte o administrador.",
    "file_unavail_title": "Ficheiro temporariamente indisponível",
    "file_unavail_body": "O ficheiro solicitado está indisponível. Por favor contacte o administrador.",
    "folder_empty_title": "Pasta sem documentos",
    "folder_empty_body": "Não há ficheiros para descarregar nesta pasta.",
})

# ── Module translations: Workers, Backup, Diagram, Payroll ───────────────────
MODULE_TRANSLATIONS = {
    "bos": {
        # Workers
        "color_label": "Boja:",
        "delete_worker_confirm": "Obrisati radnika",
        # Backup
        "backup_create_new": "Kreiraj novi backup",
        "backup_create_desc": "Kreira ZIP arhivu koja sadrzi bazu podataka i sve uploadovane dokumente. Backup se cuva na persistentnom disku.",
        "backup_create_btn": "Kreiraj backup sada",
        "backup_list_title": "Sacuvani backupi",
        "backup_restore_confirm": "Restore backup {name}? Baza podataka i dokumenti ce biti zamijenjeni podacima iz backup-a.",
        "backup_restore_btn": "Restore backup",
        "backup_delete_confirm": "Obrisati backup {name}?",
        "backup_empty": "Nema sacuvanih backupa. Kreirajte prvi backup gore.",
        "backup_note_restore": "Restore vraca bazu podataka i uploadovane dokumente. Redoslijed: (1) dokumenti se ekstraktuju u privremeni folder, (2) baza se importuje — ako bilo koji korak ne uspije, baza se rollback-uje i staging se brise. (3) Tek nakon uspjesnog importa, fajlovi se premjestaju na finalne lokacije. Greske pri premjestanju fajlova (korak 3) se prijavljuju odvojeno.",
        # Diagram
        "diagram_title": "Dijagram zarade",
        "diagram_subtitle": "Prihod iz faktura",
        "diagram_year_label": "Godina:",
        "diagram_total_ht": "Ukupno HT",
        "diagram_without_vat_note": "Bez TVA",
        "diagram_total_ttc": "Ukupno TTC",
        "diagram_with_vat_note": "Sa TVA",
        "diagram_paid_label": "Naplaceno",
        "diagram_pct_of_ttc": "od TTC",
        "diagram_unpaid_label": "Neplaceno",
        "diagram_open_invoices": "Otvorene fakture",
        "diagram_best_month": "Najbolji mjesec",
        "diagram_avg_month": "Prosjek / mj",
        "diagram_active_months_abbr": "aktivni mj",
        "diagram_revenue_by_month": "Prihod po mjesecima",
        "diagram_cumulative_ttc": "Kumulativ TTC",
        "diagram_paid_vs_unpaid_ttc": "Naplaceno vs Neplaceno (TTC)",
        "diagram_revenue_by_client": "Prihod po klijentu",
        "diagram_details_by_month": "Detalji po mjesecima",
        "diagram_month_col": "Mj.",
        "diagram_num_invoices_abbr": "Br. fakt.",
        "diagram_cumulative": "Kumulativ",
        "diagram_total_row": "UKUPNO",
        # Payroll
        "payroll_title": "Obracun plata — Luksemburg",
        "payroll_settings_per_worker": "Podesavanja po radniku",
        "payroll_salary_type_label": "Tip plate",
        "payroll_hourly_label": "Satnica",
        "payroll_fixed_label": "Fiksna bruto",
        "payroll_hourly_rate_input": "Satnica (EUR/h)",
        "payroll_fixed_gross_input": "Fiksna bruto plata (EUR/mj)",
        "payroll_independent_hours": "Neovisno od sati",
        "payroll_tax_class_label": "Klasa d'impot",
        "payroll_single_option": "1 – Samac",
        "payroll_single_parent_option": "1a – Monoparental",
        "payroll_married_option": "2 – Bracni par",
        "payroll_children_label": "Broj djece",
        "payroll_period_title": "Period obracuna",
        "payroll_calculate_btn": "Izracunaj plate",
        "payroll_results_title": "Rezultati:",
        "payroll_gross_legend": "Brut (EUR)",
        "payroll_deductions_legend": "Odbitci CCSS + impot",
        "payroll_employer_legend": "Cijena za poslodavca",
        "payroll_worker_col": "Radnik",
        "payroll_hours_col": "Sati",
        "payroll_ccss_col": "CCSS odbitci",
        "payroll_tax_col": "Porez",
        "payroll_employer_col": "Cijena poslodc.",
        "payroll_fix_gross_badge": "Fix bruto",
        "payroll_total_ccss": "Ukupno CCSS",
        "payroll_tax_base_abbr": "Baza:",
        "payroll_total_row": "UKUPNO",
        "payroll_worker_singular": "radnik",
        "payroll_worker_plural": "radnika",
        "payroll_calculation_note": "Napomena o obracunu:",
        "payroll_no_results": "Nema evidentiranih smjena za odabrani period ili nijedan radnik nema unesenu satnicu.",
        # Manual invoice UI
        "mi_title": "Rucna faktura",
        "mi_invoice_num": "Broj fakture",
        "mi_billed_to": "Fakturisi na",
        "mi_billing_address": "Adresa fakturiranja",
        "mi_items_title": "Stavke / Usluge",
        "mi_designation": "Opis",
        "mi_amount_ht": "Iznos HT (EUR)",
        "mi_add_item": "+ Dodaj stavku",
        "mi_payment_conditions": "Uslovi placanja",
        "mi_saved_items": "Sacuvane stavke",
        "mi_use_item": "+ Koristi",
        "mi_delete_template_confirm": "Obrisati ovaj sablon?",
        "mi_no_templates": "Nema sacuvanih sablona.",
        "mi_save_template_btn": "+ Sacuvaj sablon",
        "mi_default_amount": "Podrazumijevani iznos (EUR)",
        "mi_default_vat": "Podrazumijevana TVA (%)",
        "mi_save_template": "Sacuvaj sablon",
        "mi_save_invoice": "Sacuvaj fakturu",
        "mi_save_pdf": "Sacuvaj + PDF",
        "mi_designation_placeholder": "Opis usluge...",
        "mi_reserve_error": "Nije moguce rezervisati broj fakture. Pokusajte ponovo.",
        "mi_vat_col": "TVA (%)",
        "mi_vat_short": "TVA",
        "mi_actions": "Akcije",
        "payroll_note_franchise_abbr": "franšiza",
        "payroll_note_tax_line": "Porez: progresivni razredi ACD + impôt de solidarité (7% kl.1/1a · 9% kl.2). Odbitna stavka: maladie + pension + forfait frais d'obtention 45 €/mj.",
        "payroll_note_disclaimer": "Ovaj obračun je informativan — provjerite sa fiduciaire ili CCSS za tačne iznose.",
        "payroll_eg_placeholder": "npr. 2800.00",
        "payroll_per_month_abbr": "mj",
        "diagram_best_badge": "★ best",
    },
    "en": {
        "color_label": "Color:",
        "delete_worker_confirm": "Delete worker",
        "backup_create_new": "Create new backup",
        "backup_create_desc": "Creates a ZIP archive with the database and all uploaded documents. Backup stored on persistent disk.",
        "backup_create_btn": "Create backup now",
        "backup_list_title": "Saved backups",
        "backup_restore_confirm": "Restore backup {name}? The database and documents will be replaced with backup data.",
        "backup_restore_btn": "Restore backup",
        "backup_delete_confirm": "Delete backup {name}?",
        "backup_empty": "No saved backups. Create the first backup above.",
        "backup_note_restore": "Restore replaces the database and uploaded documents. Steps: (1) documents extracted to temp folder, (2) database imported — if either step fails the DB is rolled back and staging is deleted. (3) Files moved to final locations only after a successful import. Move errors (step 3) are reported separately and do not roll back the database.",
        "diagram_title": "Revenue chart",
        "diagram_subtitle": "Revenue from invoices",
        "diagram_year_label": "Year:",
        "diagram_total_ht": "Total HT",
        "diagram_without_vat_note": "Excl. VAT",
        "diagram_total_ttc": "Total TTC",
        "diagram_with_vat_note": "Incl. VAT",
        "diagram_paid_label": "Paid",
        "diagram_pct_of_ttc": "of TTC",
        "diagram_unpaid_label": "Unpaid",
        "diagram_open_invoices": "Open invoices",
        "diagram_best_month": "Best month",
        "diagram_avg_month": "Avg / month",
        "diagram_active_months_abbr": "active mo.",
        "diagram_revenue_by_month": "Revenue by month",
        "diagram_cumulative_ttc": "Cumulative TTC",
        "diagram_paid_vs_unpaid_ttc": "Paid vs Unpaid (TTC)",
        "diagram_revenue_by_client": "Revenue by client",
        "diagram_details_by_month": "Details by month",
        "diagram_month_col": "Mo.",
        "diagram_num_invoices_abbr": "# inv.",
        "diagram_cumulative": "Cumulative",
        "diagram_total_row": "TOTAL",
        "payroll_title": "Payroll — Luxembourg",
        "payroll_settings_per_worker": "Settings per worker",
        "payroll_salary_type_label": "Salary type",
        "payroll_hourly_label": "Hourly",
        "payroll_fixed_label": "Fixed gross",
        "payroll_hourly_rate_input": "Hourly rate (EUR/h)",
        "payroll_fixed_gross_input": "Fixed gross salary (EUR/mo.)",
        "payroll_independent_hours": "Independent of hours",
        "payroll_tax_class_label": "Tax class (Klasse d'impot)",
        "payroll_single_option": "1 – Single",
        "payroll_single_parent_option": "1a – Single parent",
        "payroll_married_option": "2 – Married",
        "payroll_children_label": "Number of children",
        "payroll_period_title": "Calculation period",
        "payroll_calculate_btn": "Calculate payroll",
        "payroll_results_title": "Results:",
        "payroll_gross_legend": "Gross (EUR)",
        "payroll_deductions_legend": "CCSS + tax deductions",
        "payroll_employer_legend": "Employer cost",
        "payroll_worker_col": "Worker",
        "payroll_hours_col": "Hours",
        "payroll_ccss_col": "CCSS deductions",
        "payroll_tax_col": "Tax",
        "payroll_employer_col": "Employer cost",
        "payroll_fix_gross_badge": "Fixed gross",
        "payroll_total_ccss": "Total CCSS",
        "payroll_tax_base_abbr": "Base:",
        "payroll_total_row": "TOTAL",
        "payroll_worker_singular": "worker",
        "payroll_worker_plural": "workers",
        "payroll_calculation_note": "Calculation note:",
        "payroll_no_results": "No recorded shifts for the selected period or no worker has an hourly rate set.",
        "mi_title": "Manual invoice",
        "mi_invoice_num": "Invoice no.",
        "mi_billed_to": "Bill to",
        "mi_billing_address": "Billing address",
        "mi_items_title": "Items / Services",
        "mi_designation": "Description",
        "mi_amount_ht": "Amount HT (EUR)",
        "mi_add_item": "+ Add item",
        "mi_payment_conditions": "Payment conditions",
        "mi_saved_items": "Saved items",
        "mi_use_item": "+ Use",
        "mi_delete_template_confirm": "Delete this template?",
        "mi_no_templates": "No saved templates.",
        "mi_save_template_btn": "+ Save template",
        "mi_default_amount": "Default amount (EUR)",
        "mi_default_vat": "Default VAT (%)",
        "mi_save_template": "Save template",
        "mi_save_invoice": "Save invoice",
        "mi_save_pdf": "Save + PDF",
        "mi_designation_placeholder": "Description of service...",
        "mi_reserve_error": "Could not reserve invoice number. Please try again.",
        "mi_vat_col": "VAT (%)",
        "mi_vat_short": "VAT",
        "mi_actions": "Actions",
        "payroll_note_franchise_abbr": "franchise",
        "payroll_note_tax_line": "Tax: progressive ACD brackets + impôt de solidarité (7% class 1/1a · 9% class 2). Deductible: maladie + pension + forfait frais d'obtention 45 €/mo.",
        "payroll_note_disclaimer": "This calculation is indicative — verify with your fiduciaire or CCSS for exact amounts.",
        "payroll_eg_placeholder": "e.g. 2800.00",
        "payroll_per_month_abbr": "mo.",
        "diagram_best_badge": "★ best",
    },
    "fr": {
        "color_label": "Couleur:",
        "delete_worker_confirm": "Supprimer le travailleur",
        "backup_create_new": "Creer une sauvegarde",
        "backup_create_desc": "Cree une archive ZIP avec la base de donnees et tous les documents. Sauvegarde stockee sur disque persistant.",
        "backup_create_btn": "Creer maintenant",
        "backup_list_title": "Sauvegardes enregistrees",
        "backup_restore_confirm": "Restaurer la sauvegarde {name} ? La base et les documents seront remplacees par les donnees de la sauvegarde.",
        "backup_restore_btn": "Restaurer",
        "backup_delete_confirm": "Supprimer la sauvegarde {name} ?",
        "backup_empty": "Aucune sauvegarde. Creez la premiere sauvegarde ci-dessus.",
        "backup_note_restore": "La restauration remplace la base de donnees et les documents. Etapes : (1) extraction dans un dossier temporaire, (2) import de la base — en cas d'echec la base est restauree et le staging supprime. (3) Les fichiers sont deplaces vers les emplacements finaux apres un import reussi. Les erreurs de deplacement (etape 3) sont signalees separement.",
        "diagram_title": "Graphique des revenus",
        "diagram_subtitle": "Revenus des factures",
        "diagram_year_label": "Annee:",
        "diagram_total_ht": "Total HT",
        "diagram_without_vat_note": "Hors TVA",
        "diagram_total_ttc": "Total TTC",
        "diagram_with_vat_note": "TVA incl.",
        "diagram_paid_label": "Encaisse",
        "diagram_pct_of_ttc": "du TTC",
        "diagram_unpaid_label": "Non encaisse",
        "diagram_open_invoices": "Factures ouvertes",
        "diagram_best_month": "Meilleur mois",
        "diagram_avg_month": "Moy. / mois",
        "diagram_active_months_abbr": "mois actifs",
        "diagram_revenue_by_month": "Revenus par mois",
        "diagram_cumulative_ttc": "Cumulatif TTC",
        "diagram_paid_vs_unpaid_ttc": "Encaisse vs Non encaisse (TTC)",
        "diagram_revenue_by_client": "Revenus par client",
        "diagram_details_by_month": "Details par mois",
        "diagram_month_col": "Mois",
        "diagram_num_invoices_abbr": "Nb fac.",
        "diagram_cumulative": "Cumulatif",
        "diagram_total_row": "TOTAL",
        "payroll_title": "Calcul des salaires — Luxembourg",
        "payroll_settings_per_worker": "Parametres par employe",
        "payroll_salary_type_label": "Type de salaire",
        "payroll_hourly_label": "Horaire",
        "payroll_fixed_label": "Fixe brut",
        "payroll_hourly_rate_input": "Taux horaire (EUR/h)",
        "payroll_fixed_gross_input": "Salaire fixe brut (EUR/mois)",
        "payroll_independent_hours": "Independant des heures",
        "payroll_tax_class_label": "Classe d'impot",
        "payroll_single_option": "1 – Celibataire",
        "payroll_single_parent_option": "1a – Monoparental",
        "payroll_married_option": "2 – Marie",
        "payroll_children_label": "Nombre d'enfants",
        "payroll_period_title": "Periode de calcul",
        "payroll_calculate_btn": "Calculer les salaires",
        "payroll_results_title": "Resultats:",
        "payroll_gross_legend": "Brut (EUR)",
        "payroll_deductions_legend": "Retenues CCSS + impot",
        "payroll_employer_legend": "Cout employeur",
        "payroll_worker_col": "Employe",
        "payroll_hours_col": "Heures",
        "payroll_ccss_col": "Retenues CCSS",
        "payroll_tax_col": "Impot",
        "payroll_employer_col": "Cout empl.",
        "payroll_fix_gross_badge": "Fixe brut",
        "payroll_total_ccss": "Total CCSS",
        "payroll_tax_base_abbr": "Base:",
        "payroll_total_row": "TOTAL",
        "payroll_worker_singular": "employe",
        "payroll_worker_plural": "employes",
        "payroll_calculation_note": "Note de calcul:",
        "payroll_no_results": "Aucune mission pour la periode selectionnee ou aucun employe n'a de taux horaire defini.",
        "mi_title": "Facture manuelle",
        "mi_invoice_num": "Facture n°",
        "mi_billed_to": "Facturé à",
        "mi_billing_address": "Adresse de facturation",
        "mi_items_title": "Articles / Prestations",
        "mi_designation": "Désignation",
        "mi_amount_ht": "Montant HT (€)",
        "mi_add_item": "+ Ajouter un article",
        "mi_payment_conditions": "Conditions et modalités de paiement",
        "mi_saved_items": "Articles sauvegardés",
        "mi_use_item": "+ Utiliser",
        "mi_delete_template_confirm": "Supprimer ce modèle ?",
        "mi_no_templates": "Aucun modèle sauvegardé.",
        "mi_save_template_btn": "+ Sauvegarder un modèle",
        "mi_default_amount": "Montant par défaut (€)",
        "mi_default_vat": "TVA par défaut (%)",
        "mi_save_template": "Sauvegarder le modèle",
        "mi_save_invoice": "Sauvegarder la facture",
        "mi_save_pdf": "Sauvegarder + PDF",
        "mi_designation_placeholder": "Désignation de la prestation...",
        "mi_reserve_error": "Impossible de réserver le numéro de facture. Veuillez réessayer.",
        "mi_vat_col": "TVA (%)",
        "mi_vat_short": "TVA",
        "mi_actions": "Actions",
        "payroll_note_franchise_abbr": "franchise",
        "payroll_note_tax_line": "Impot: bareme progressif de l'ACD + impot de solidarite (7% cl.1/1a · 9% cl.2). Deductions: maladie + pension + forfait frais d'obtention 45 €/mois.",
        "payroll_note_disclaimer": "Ce calcul est indicatif — verifiez avec votre fiduciaire ou la CCSS pour les montants exacts.",
        "payroll_eg_placeholder": "ex. 2800,00",
        "payroll_per_month_abbr": "mois",
        "diagram_best_badge": "★ meilleur",
    },
    "de": {
        "color_label": "Farbe:",
        "delete_worker_confirm": "Mitarbeiter loschen",
        "backup_create_new": "Neue Sicherung erstellen",
        "backup_create_desc": "Erstellt ein ZIP-Archiv mit Datenbank und allen hochgeladenen Dokumenten. Sicherung auf persistentem Speicher.",
        "backup_create_btn": "Jetzt sichern",
        "backup_list_title": "Gespeicherte Sicherungen",
        "backup_restore_confirm": "Sicherung {name} wiederherstellen? Datenbank und Dokumente werden durch Sicherungsdaten ersetzt.",
        "backup_restore_btn": "Wiederherstellen",
        "backup_delete_confirm": "Sicherung {name} loeschen?",
        "backup_empty": "Keine Sicherungen. Erste Sicherung oben erstellen.",
        "backup_note_restore": "Die Wiederherstellung ersetzt Datenbank und Dokumente. Schritte: (1) Extraktion in Temp-Ordner, (2) Datenbankimport — schlaegt ein Schritt fehl wird die DB zurueckgesetzt und Staging geloescht. (3) Dateien werden nach erfolgreichem Import verschoben. Fehler beim Verschieben (Schritt 3) werden getrennt gemeldet.",
        "diagram_title": "Einnahmendiagramm",
        "diagram_subtitle": "Einnahmen aus Rechnungen",
        "diagram_year_label": "Jahr:",
        "diagram_total_ht": "Gesamt HT",
        "diagram_without_vat_note": "Ohne MwSt",
        "diagram_total_ttc": "Gesamt TTC",
        "diagram_with_vat_note": "Mit MwSt",
        "diagram_paid_label": "Bezahlt",
        "diagram_pct_of_ttc": "von TTC",
        "diagram_unpaid_label": "Offen",
        "diagram_open_invoices": "Offene Rechnungen",
        "diagram_best_month": "Bester Monat",
        "diagram_avg_month": "Durchschn. / Mon.",
        "diagram_active_months_abbr": "akt. Mon.",
        "diagram_revenue_by_month": "Einnahmen pro Monat",
        "diagram_cumulative_ttc": "Kumulativ TTC",
        "diagram_paid_vs_unpaid_ttc": "Bezahlt vs Offen (TTC)",
        "diagram_revenue_by_client": "Einnahmen pro Kunde",
        "diagram_details_by_month": "Details pro Monat",
        "diagram_month_col": "Mon.",
        "diagram_num_invoices_abbr": "Anz. Re.",
        "diagram_cumulative": "Kumulativ",
        "diagram_total_row": "GESAMT",
        "payroll_title": "Lohnabrechnung — Luxemburg",
        "payroll_settings_per_worker": "Einstellungen pro Mitarbeiter",
        "payroll_salary_type_label": "Gehaltstyp",
        "payroll_hourly_label": "Stundenlohn",
        "payroll_fixed_label": "Fixes Bruttogehalt",
        "payroll_hourly_rate_input": "Stundensatz (EUR/h)",
        "payroll_fixed_gross_input": "Fixes Bruttogehalt (EUR/Mon.)",
        "payroll_independent_hours": "Unabhaengig von Stunden",
        "payroll_tax_class_label": "Steuerklasse (Klasse d'impot)",
        "payroll_single_option": "1 – Ledig",
        "payroll_single_parent_option": "1a – Alleinerziehend",
        "payroll_married_option": "2 – Verheiratet",
        "payroll_children_label": "Anzahl Kinder",
        "payroll_period_title": "Abrechnungszeitraum",
        "payroll_calculate_btn": "Gehalt berechnen",
        "payroll_results_title": "Ergebnisse:",
        "payroll_gross_legend": "Brutto (EUR)",
        "payroll_deductions_legend": "CCSS + Steuer Abzuege",
        "payroll_employer_legend": "Arbeitgeberkosten",
        "payroll_worker_col": "Mitarbeiter",
        "payroll_hours_col": "Stunden",
        "payroll_ccss_col": "CCSS Abzuege",
        "payroll_tax_col": "Steuer",
        "payroll_employer_col": "AG-Kosten",
        "payroll_fix_gross_badge": "Fix Brutto",
        "payroll_total_ccss": "Gesamt CCSS",
        "payroll_tax_base_abbr": "Basis:",
        "payroll_total_row": "GESAMT",
        "payroll_worker_singular": "Mitarbeiter",
        "payroll_worker_plural": "Mitarbeiter",
        "payroll_calculation_note": "Hinweis zur Berechnung:",
        "payroll_no_results": "Keine Schichten fuer den gewahlten Zeitraum oder kein Mitarbeiter hat einen Stundensatz.",
        "mi_title": "Manuelle Rechnung",
        "mi_invoice_num": "Rechnung Nr.",
        "mi_billed_to": "Rechnungsempfanger",
        "mi_billing_address": "Rechnungsadresse",
        "mi_items_title": "Positionen / Leistungen",
        "mi_designation": "Bezeichnung",
        "mi_amount_ht": "Betrag HT (EUR)",
        "mi_add_item": "+ Position hinzufuegen",
        "mi_payment_conditions": "Zahlungsbedingungen",
        "mi_saved_items": "Gespeicherte Positionen",
        "mi_use_item": "+ Verwenden",
        "mi_delete_template_confirm": "Diese Vorlage loeschen?",
        "mi_no_templates": "Keine gespeicherten Vorlagen.",
        "mi_save_template_btn": "+ Vorlage speichern",
        "mi_default_amount": "Standardbetrag (EUR)",
        "mi_default_vat": "Standard-MwSt (%)",
        "mi_save_template": "Vorlage speichern",
        "mi_save_invoice": "Rechnung speichern",
        "mi_save_pdf": "Speichern + PDF",
        "mi_designation_placeholder": "Leistungsbeschreibung...",
        "mi_reserve_error": "Rechnungsnummer konnte nicht reserviert werden. Bitte erneut versuchen.",
        "mi_vat_col": "MwSt (%)",
        "mi_vat_short": "MwSt",
        "mi_actions": "Aktionen",
        "payroll_note_franchise_abbr": "Freibetrag",
        "payroll_note_tax_line": "Steuer: progressive ACD-Klassen + impot de solidarite (7% Kl.1/1a · 9% Kl.2). Abzuge: maladie + pension + forfait frais d'obtention 45 €/Mon.",
        "payroll_note_disclaimer": "Diese Berechnung ist informativ — prufen Sie mit dem Steuerberater oder der CCSS fuer genaue Betrage.",
        "payroll_eg_placeholder": "z.B. 2800,00",
        "payroll_per_month_abbr": "Mon.",
        "diagram_best_badge": "★ bester",
    },
    "pt": {
        "color_label": "Cor:",
        "delete_worker_confirm": "Eliminar trabalhador",
        "backup_create_new": "Criar copia de seguranca",
        "backup_create_desc": "Cria um arquivo ZIP com a base de dados e todos os documentos. Copia guardada em disco persistente.",
        "backup_create_btn": "Criar agora",
        "backup_list_title": "Copias guardadas",
        "backup_restore_confirm": "Restaurar copia {name}? A base de dados e os documentos serao substituidos pelos dados da copia.",
        "backup_restore_btn": "Restaurar",
        "backup_delete_confirm": "Eliminar copia {name}?",
        "backup_empty": "Sem copias de seguranca. Crie a primeira copia acima.",
        "backup_note_restore": "A restauracao substitui a base de dados e os documentos. Passos: (1) extracao para pasta temporaria, (2) importacao da base — em caso de falha a base e revertida e o staging eliminado. (3) Os ficheiros sao movidos para locais finais apos importacao bem-sucedida. Erros de movimentacao (passo 3) sao reportados separadamente.",
        "diagram_title": "Grafico de receitas",
        "diagram_subtitle": "Receitas de faturas",
        "diagram_year_label": "Ano:",
        "diagram_total_ht": "Total HT",
        "diagram_without_vat_note": "Sem IVA",
        "diagram_total_ttc": "Total TTC",
        "diagram_with_vat_note": "Com IVA",
        "diagram_paid_label": "Cobrado",
        "diagram_pct_of_ttc": "do TTC",
        "diagram_unpaid_label": "Por cobrar",
        "diagram_open_invoices": "Faturas em aberto",
        "diagram_best_month": "Melhor mes",
        "diagram_avg_month": "Media / mes",
        "diagram_active_months_abbr": "meses ativos",
        "diagram_revenue_by_month": "Receitas por mes",
        "diagram_cumulative_ttc": "Cumulativo TTC",
        "diagram_paid_vs_unpaid_ttc": "Cobrado vs Por cobrar (TTC)",
        "diagram_revenue_by_client": "Receitas por cliente",
        "diagram_details_by_month": "Detalhes por mes",
        "diagram_month_col": "Mes",
        "diagram_num_invoices_abbr": "Nr. fat.",
        "diagram_cumulative": "Cumulativo",
        "diagram_total_row": "TOTAL",
        "payroll_title": "Calculo de salarios — Luxemburgo",
        "payroll_settings_per_worker": "Definicoes por trabalhador",
        "payroll_salary_type_label": "Tipo de salario",
        "payroll_hourly_label": "Hora",
        "payroll_fixed_label": "Fixo bruto",
        "payroll_hourly_rate_input": "Taxa horaria (EUR/h)",
        "payroll_fixed_gross_input": "Salario fixo bruto (EUR/mes)",
        "payroll_independent_hours": "Independente das horas",
        "payroll_tax_class_label": "Classe de imposto (Klasse d'impot)",
        "payroll_single_option": "1 – Solteiro",
        "payroll_single_parent_option": "1a – Monoparental",
        "payroll_married_option": "2 – Casado",
        "payroll_children_label": "Numero de filhos",
        "payroll_period_title": "Periodo de calculo",
        "payroll_calculate_btn": "Calcular salarios",
        "payroll_results_title": "Resultados:",
        "payroll_gross_legend": "Bruto (EUR)",
        "payroll_deductions_legend": "Deducoes CCSS + imposto",
        "payroll_employer_legend": "Custo patronal",
        "payroll_worker_col": "Trabalhador",
        "payroll_hours_col": "Horas",
        "payroll_ccss_col": "Deducoes CCSS",
        "payroll_tax_col": "Imposto",
        "payroll_employer_col": "Custo patronal",
        "payroll_fix_gross_badge": "Fixo bruto",
        "payroll_total_ccss": "Total CCSS",
        "payroll_tax_base_abbr": "Base:",
        "payroll_total_row": "TOTAL",
        "payroll_worker_singular": "trabalhador",
        "payroll_worker_plural": "trabalhadores",
        "payroll_calculation_note": "Nota de calculo:",
        "payroll_no_results": "Nenhum turno registado para o periodo selecionado ou nenhum trabalhador tem taxa horaria.",
        "mi_title": "Fatura manual",
        "mi_invoice_num": "Fatura n.°",
        "mi_billed_to": "Faturar a",
        "mi_billing_address": "Endereco de faturacao",
        "mi_items_title": "Artigos / Servicos",
        "mi_designation": "Descricao",
        "mi_amount_ht": "Montante HT (EUR)",
        "mi_add_item": "+ Adicionar artigo",
        "mi_payment_conditions": "Condicoes de pagamento",
        "mi_saved_items": "Artigos guardados",
        "mi_use_item": "+ Usar",
        "mi_delete_template_confirm": "Eliminar este modelo?",
        "mi_no_templates": "Sem modelos guardados.",
        "mi_save_template_btn": "+ Guardar modelo",
        "mi_default_amount": "Montante predefinido (EUR)",
        "mi_default_vat": "IVA predefinido (%)",
        "mi_save_template": "Guardar modelo",
        "mi_save_invoice": "Guardar fatura",
        "mi_save_pdf": "Guardar + PDF",
        "mi_designation_placeholder": "Descricao do servico...",
        "mi_reserve_error": "Nao foi possivel reservar o numero da fatura. Tente novamente.",
        "mi_vat_col": "IVA (%)",
        "mi_vat_short": "IVA",
        "mi_actions": "Acoes",
        "payroll_note_franchise_abbr": "franquia",
        "payroll_note_tax_line": "Imposto: escaloes progressivos ACD + impot de solidarite (7% cl.1/1a · 9% cl.2). Deducoes: maladie + pension + forfait frais d'obtention 45 €/mes.",
        "payroll_note_disclaimer": "Este calculo e indicativo — verifique com o fiduciaire ou a CCSS para valores exactos.",
        "payroll_eg_placeholder": "ex. 2800,00",
        "payroll_per_month_abbr": "mes",
        "diagram_best_badge": "★ melhor",
    },
}
for _lang, _values in MODULE_TRANSLATIONS.items():
    TRANSLATIONS[_lang].update(_values)

DOCUMENT_TRANSLATIONS = {
    "bos": {
        "documents": "Dokumenti", "upload_document": "Dodaj dokument", "document_name": "Naziv dokumenta",
        "document_file": "Datoteka", "document_category": "Kategorija", "document_note": "Napomena",
        "document_accounting": "Racunovodstvo", "document_clients": "Klijenti", "document_workers": "Radnici",
        "document_contracts": "Ugovori", "document_invoices": "Fakture", "document_other": "Ostalo",
        "uploaded_at": "Dodato", "file_size": "Velicina", "preview": "Pregledaj", "download": "Preuzmi",
        "share_link": "Link za dijeljenje", "create_share_link": "Kreiraj link", "shared_links": "Aktivni linkovi",
        "expires_in": "Istice za", "expires_never": "Bez isteka", "expires_days": "dana",
        "revoke_link": "Ukini link", "document_search": "Pretrazi dokumente", "all_categories": "Sve kategorije",
        "document_missing": "Dokument nije dostupan.", "share_expired": "Ovaj link je istekao ili je ukinut.",
        "accountant_access": "Pristup preko ovog linka vazi samo za ovaj dokument.",
        "file_type_error": "Dozvoljeni su PDF, slike, Word, Excel, CSV i TXT dokumenti.",
        "document_upload_error": "Izaberi datoteku ili fasciklu za upload.", "open_document": "Otvori dokument",
        "single_document": "Pojedinacni dokument", "upload_documents": "Dodaj dokumente",
        "multiple_documents": "Vise dokumenata", "folder_documents": "Cijela fascikla",
        "documents_uploaded": "Dokumenata dodato", "documents_skipped": "Preskoceno",
        "upload_too_large": "Upload je prevelik. Izaberi manju fasciklu ili je podijeli u vise uploada.",
        "all_files": "Sve datoteke", "folders": "Fascikle", "pdf_documents": "PDF dokumenti",
        "images": "Slike", "new_folder": "Nova fascikla", "folder_name": "Naziv fascikle",
        "addition_time": "Datum dodavanja", "root_folder": "Glavna fascikla", "folder_exists": "Fascikla vec postoji.",
        "folder_created": "Fascikla je kreirana.", "open_folder": "Otvori fasciklu",
        "delete_folder": "Obrisi fasciklu",
        "delete_folder_confirm": "Da li zelite obrisati ovu fasciklu i sve dokumente u njoj?",
        "cleanup_confirm": "Obrisati sve zapise ciji fajl ne postoji na serveru?",
        "cleanup_orphans": "Ocisti izgubljene",
        "doc_delete_confirm": "Obrisati dokument?",
        "share_link_invalid": "Link istekao ili nije validan.",
        "folder_unavailable": "Folder nije dostupan.",
        "folder_not_found_pub": "Folder nije pronadjen.",
        "folder_empty": "Folder ne sadrzi dokumente.",
        "zip_error": "Greska pri kreiranju ZIP-a",
        "no_results": "Nema rezultata.",
    },
    "en": {
        "documents": "Documents", "upload_document": "Upload document", "document_name": "Document name",
        "document_file": "File", "document_category": "Category", "document_note": "Note",
        "document_accounting": "Accounting", "document_clients": "Clients", "document_workers": "Workers",
        "document_contracts": "Contracts", "document_invoices": "Invoices", "document_other": "Other",
        "uploaded_at": "Uploaded", "file_size": "Size", "preview": "Preview", "download": "Download",
        "share_link": "Share link", "create_share_link": "Create link", "shared_links": "Active links",
        "expires_in": "Expires in", "expires_never": "No expiry", "expires_days": "days",
        "revoke_link": "Revoke link", "document_search": "Search documents", "all_categories": "All categories",
        "document_missing": "Document is not available.", "share_expired": "This link expired or was revoked.",
        "accountant_access": "This link grants access only to this document.",
        "file_type_error": "PDF, image, Word, Excel, CSV and TXT documents are allowed.",
        "document_upload_error": "Choose a file or folder to upload.", "open_document": "Open document",
        "single_document": "Single document", "upload_documents": "Upload documents",
        "multiple_documents": "Multiple documents", "folder_documents": "Whole folder",
        "documents_uploaded": "Documents uploaded", "documents_skipped": "Skipped",
        "upload_too_large": "Upload is too large. Choose a smaller folder or split it into several uploads.",
        "all_files": "All files", "folders": "Folders", "pdf_documents": "PDF documents",
        "images": "Images", "new_folder": "New folder", "folder_name": "Folder name",
        "addition_time": "Added", "root_folder": "Root folder", "folder_exists": "Folder already exists.",
        "folder_created": "Folder created.", "open_folder": "Open folder",
        "delete_folder": "Delete folder",
        "delete_folder_confirm": "Delete this folder and all documents inside it?",
        "cleanup_confirm": "Delete all records whose file no longer exists on the server?",
        "cleanup_orphans": "Clean up lost files",
        "doc_delete_confirm": "Delete this document?",
        "share_link_invalid": "Link expired or is not valid.",
        "folder_unavailable": "Folder is not accessible.",
        "folder_not_found_pub": "Folder not found.",
        "folder_empty": "Folder contains no documents.",
        "zip_error": "Error creating ZIP",
        "no_results": "No results found.",
    },
    "fr": {
        "documents": "Documents", "upload_document": "Ajouter document", "document_name": "Nom du document",
        "document_file": "Fichier", "document_category": "Categorie", "document_note": "Note",
        "document_accounting": "Comptabilite", "document_clients": "Clients", "document_workers": "Employes",
        "document_contracts": "Contrats", "document_invoices": "Factures", "document_other": "Autre",
        "uploaded_at": "Ajoute", "file_size": "Taille", "preview": "Apercu", "download": "Telecharger",
        "share_link": "Lien de partage", "create_share_link": "Creer lien", "shared_links": "Liens actifs",
        "expires_in": "Expire dans", "expires_never": "Sans expiration", "expires_days": "jours",
        "revoke_link": "Revoquer lien", "document_search": "Rechercher documents", "all_categories": "Toutes categories",
        "document_missing": "Document indisponible.", "share_expired": "Ce lien a expire ou a ete revoque.",
        "accountant_access": "Ce lien donne acces uniquement a ce document.",
        "file_type_error": "PDF, images, Word, Excel, CSV et TXT sont autorises.",
        "document_upload_error": "Choisissez un fichier ou dossier.", "open_document": "Ouvrir document",
        "single_document": "Document individuel", "upload_documents": "Ajouter documents",
        "multiple_documents": "Plusieurs documents", "folder_documents": "Dossier complet",
        "documents_uploaded": "Documents ajoutes", "documents_skipped": "Ignores",
        "upload_too_large": "Upload trop volumineux. Choisissez un dossier plus petit ou divisez l'envoi.",
        "all_files": "Tous les fichiers", "folders": "Dossiers", "pdf_documents": "Documents PDF",
        "images": "Images", "new_folder": "Nouveau dossier", "folder_name": "Nom du dossier",
        "addition_time": "Ajoute", "root_folder": "Dossier principal", "folder_exists": "Le dossier existe deja.",
        "folder_created": "Dossier cree.", "open_folder": "Ouvrir dossier",
        "delete_folder": "Supprimer dossier",
        "delete_folder_confirm": "Supprimer ce dossier et tous les documents qu'il contient?",
        "cleanup_confirm": "Supprimer tous les enregistrements dont le fichier n'existe plus sur le serveur?",
        "cleanup_orphans": "Nettoyer les fichiers perdus",
        "doc_delete_confirm": "Supprimer ce document?",
        "share_link_invalid": "Lien expire ou invalide.",
        "folder_unavailable": "Dossier inaccessible.",
        "folder_not_found_pub": "Dossier introuvable.",
        "folder_empty": "Le dossier ne contient aucun document.",
        "zip_error": "Erreur lors de la creation du ZIP",
        "no_results": "Aucun resultat.",
    },
    "de": {
        "documents": "Dokumente", "upload_document": "Dokument hochladen", "document_name": "Dokumentname",
        "document_file": "Datei", "document_category": "Kategorie", "document_note": "Notiz",
        "document_accounting": "Buchhaltung", "document_clients": "Kunden", "document_workers": "Mitarbeiter",
        "document_contracts": "Vertraege", "document_invoices": "Rechnungen", "document_other": "Andere",
        "uploaded_at": "Hochgeladen", "file_size": "Groesse", "preview": "Vorschau", "download": "Herunterladen",
        "share_link": "Freigabelink", "create_share_link": "Link erstellen", "shared_links": "Aktive Links",
        "expires_in": "Laeuft ab in", "expires_never": "Ohne Ablauf", "expires_days": "Tagen",
        "revoke_link": "Link widerrufen", "document_search": "Dokumente suchen", "all_categories": "Alle Kategorien",
        "document_missing": "Dokument nicht verfuegbar.", "share_expired": "Dieser Link ist abgelaufen oder widerrufen.",
        "accountant_access": "Dieser Link gibt nur Zugriff auf dieses Dokument.",
        "file_type_error": "PDF, Bilder, Word, Excel, CSV und TXT sind erlaubt.",
        "document_upload_error": "Waehlen Sie Datei oder Ordner.", "open_document": "Dokument oeffnen",
        "single_document": "Ein Dokument", "upload_documents": "Dokumente hochladen",
        "multiple_documents": "Mehrere Dokumente", "folder_documents": "Ganzer Ordner",
        "documents_uploaded": "Dokumente hochgeladen", "documents_skipped": "Uebersprungen",
        "upload_too_large": "Upload zu gross. Waehlen Sie einen kleineren Ordner oder teilen Sie ihn auf.",
        "all_files": "Alle Dateien", "folders": "Ordner", "pdf_documents": "PDF Dokumente",
        "images": "Bilder", "new_folder": "Neuer Ordner", "folder_name": "Ordnername",
        "addition_time": "Hinzugefuegt", "root_folder": "Hauptordner", "folder_exists": "Ordner existiert bereits.",
        "folder_created": "Ordner erstellt.", "open_folder": "Ordner oeffnen",
        "delete_folder": "Ordner loeschen",
        "delete_folder_confirm": "Diesen Ordner und alle Dokumente darin loeschen?",
        "cleanup_confirm": "Alle Eintraege loeschen, deren Datei nicht mehr auf dem Server vorhanden ist?",
        "cleanup_orphans": "Verlorene bereinigen",
        "doc_delete_confirm": "Dieses Dokument loeschen?",
        "share_link_invalid": "Link abgelaufen oder ungueltig.",
        "folder_unavailable": "Ordner nicht zugaenglich.",
        "folder_not_found_pub": "Ordner nicht gefunden.",
        "folder_empty": "Ordner enthaelt keine Dokumente.",
        "zip_error": "Fehler beim Erstellen des ZIP",
        "no_results": "Keine Ergebnisse gefunden.",
    },
    "pt": {
        "documents": "Documentos", "upload_document": "Carregar documento", "document_name": "Nome do documento",
        "document_file": "Ficheiro", "document_category": "Categoria", "document_note": "Nota",
        "document_accounting": "Contabilidade", "document_clients": "Clientes", "document_workers": "Trabalhadores",
        "document_contracts": "Contratos", "document_invoices": "Faturas", "document_other": "Outro",
        "uploaded_at": "Carregado", "file_size": "Tamanho", "preview": "Prever", "download": "Descarregar",
        "share_link": "Link de partilha", "create_share_link": "Criar link", "shared_links": "Links ativos",
        "expires_in": "Expira em", "expires_never": "Sem expiracao", "expires_days": "dias",
        "revoke_link": "Revogar link", "document_search": "Pesquisar documentos", "all_categories": "Todas categorias",
        "document_missing": "Documento indisponivel.", "share_expired": "Este link expirou ou foi revogado.",
        "accountant_access": "Este link da acesso apenas a este documento.",
        "file_type_error": "PDF, imagens, Word, Excel, CSV e TXT sao permitidos.",
        "document_upload_error": "Escolha ficheiro ou pasta.", "open_document": "Abrir documento",
        "single_document": "Documento individual", "upload_documents": "Carregar documentos",
        "multiple_documents": "Varios documentos", "folder_documents": "Pasta completa",
        "documents_uploaded": "Documentos carregados", "documents_skipped": "Ignorados",
        "upload_too_large": "Upload demasiado grande. Escolha uma pasta menor ou divida o envio.",
        "all_files": "Todos os ficheiros", "folders": "Pastas", "pdf_documents": "Documentos PDF",
        "images": "Imagens", "new_folder": "Nova pasta", "folder_name": "Nome da pasta",
        "addition_time": "Adicionado", "root_folder": "Pasta principal", "folder_exists": "A pasta ja existe.",
        "folder_created": "Pasta criada.", "open_folder": "Abrir pasta",
        "delete_folder": "Apagar pasta",
        "delete_folder_confirm": "Apagar esta pasta e todos os documentos dentro dela?",
        "cleanup_confirm": "Apagar todos os registos cujo ficheiro ja nao existe no servidor?",
        "cleanup_orphans": "Limpar perdidos",
        "doc_delete_confirm": "Apagar este documento?",
        "share_link_invalid": "Ligacao expirada ou invalida.",
        "folder_unavailable": "Pasta inacessivel.",
        "folder_not_found_pub": "Pasta nao encontrada.",
        "folder_empty": "A pasta nao contem documentos.",
        "zip_error": "Erro ao criar o ZIP",
        "no_results": "Sem resultados.",
    },
}
for _lang, _values in DOCUMENT_TRANSLATIONS.items():
    TRANSLATIONS[_lang].update(_values)

INVOICE_TRANSLATIONS = {
    "bos": {
        "invoices": "Fakture", "invoice_settings": "Podesavanja faktura", "invoice_text": "Tekst na fakturi",
        "payment_terms": "Modalitet placanja", "bank_account": "Racun za uplatu", "invoice_profiles": "Profili klijenata za fakture",
        "client_type": "Tip klijenta", "private_client": "Privatno lice", "pro_client": "Profesionalni klijent",
        "hourly_rate": "Cijena po satu", "email": "Email", "vat_rate": "TVA", "generate_invoice": "Generisi fakturu",
        "download_all_invoices": "Preuzmi sve fakture PDF", "annual_certificate": "Godisnji certifikat",
        "date_from": "Od datuma", "date_to": "Do datuma", "invoice_date": "Datum fakture",
        "invoice_number": "Broj fakture", "amount_without_vat": "Iznos bez TVA", "amount_with_vat": "Iznos sa TVA",
        "fixed_amount": "Zeljeni iznos", "use_fixed_amount": "Koristi zeljeni iznos", "invoice_list": "Lista faktura",
        "total_invoices": "Ukupno faktura", "save_settings": "Sacuvaj podesavanja",
        "company_name": "Naziv firme", "company_address": "Adresa firme", "company_phone": "Telefon firme",
        "company_email": "Email firme", "company_vat": "TVA broj firme", "invoice_template": "Template fakture",
        "template_orange": "Narandzasti", "template_blue": "Plavi", "template_green": "Zeleni",
        "search_client": "Pretrazi klijenta", "save_client_profile": "Sacuvaj profil klijenta",
        "invoice_design": "Dizajn fakture", "service_details": "Detalji usluge", "service_dates": "Datumi rada",
        "invoice_start_number": "Pocetni broj fakture", "paid": "Placena", "unpaid": "Nije placena",
        "mark_paid": "Oznaci placeno", "mark_unpaid": "Oznaci neplaceno", "payment_status": "Status placanja",
        "company_settings": "Podaci firme", "sent": "Poslati", "quote": "Ponuda", "quote_number": "Broj ponude",
        "quote_date": "Datum ponude", "quote_text": "Tekst ponude", "quote_price": "Cijena ponude",
        "generate_quote": "Generisi ponudu", "client_email": "Email klijenta",
        "sent_status": "Status slanja", "sent_yes": "Poslato", "sent_no": "Neposlato",
        "mark_sent": "Oznaci poslato", "mark_unsent": "Oznaci neposlato",
    }
}
INVOICE_TRANSLATIONS["en"] = {
    "invoices": "Invoices", "invoice_settings": "Invoice settings", "invoice_text": "Invoice text",
    "payment_terms": "Payment terms", "bank_account": "Bank account", "invoice_profiles": "Client invoice profiles",
    "client_type": "Client type", "private_client": "Private client", "pro_client": "Professional client",
    "hourly_rate": "Hourly rate", "email": "Email", "vat_rate": "VAT", "generate_invoice": "Generate invoice",
    "download_all_invoices": "Download all invoice PDFs", "annual_certificate": "Annual certificate",
    "date_from": "Date from", "date_to": "Date to", "invoice_date": "Invoice date",
    "invoice_number": "Invoice number", "amount_without_vat": "Amount without VAT", "amount_with_vat": "Amount with VAT",
    "fixed_amount": "Custom amount", "use_fixed_amount": "Use custom amount", "invoice_list": "Invoice list",
    "total_invoices": "Total invoices", "save_settings": "Save settings", "company_name": "Company name",
    "company_address": "Company address", "company_phone": "Company phone", "company_email": "Company email",
    "company_vat": "Company VAT number", "invoice_template": "Invoice template", "template_orange": "Orange",
    "template_blue": "Blue", "template_green": "Green", "search_client": "Search client",
    "save_client_profile": "Save client profile", "invoice_design": "Invoice design", "service_details": "Service details",
    "service_dates": "Work dates", "invoice_start_number": "Starting invoice number", "paid": "Paid", "unpaid": "Unpaid",
    "mark_paid": "Mark paid", "mark_unpaid": "Mark unpaid", "payment_status": "Payment status", "company_settings": "Company details",
    "sent": "Sent", "quote": "Quote", "quote_number": "Quote number", "quote_date": "Quote date",
    "quote_text": "Quote text", "quote_price": "Quote price", "generate_quote": "Generate quote", "client_email": "Client email",
    "sent_status": "Sending status", "sent_yes": "Sent", "sent_no": "Not sent",
    "mark_sent": "Mark sent", "mark_unsent": "Mark not sent",
}
INVOICE_TRANSLATIONS["fr"] = {
    "invoices": "Factures", "invoice_settings": "Parametres des factures", "invoice_text": "Texte sur la facture",
    "payment_terms": "Conditions et modalites de paiement", "bank_account": "Compte bancaire", "invoice_profiles": "Profils de facturation clients",
    "client_type": "Type de client", "private_client": "Client prive", "pro_client": "Client professionnel",
    "hourly_rate": "Prix horaire", "email": "Email", "vat_rate": "TVA", "generate_invoice": "Generer facture",
    "download_all_invoices": "Telecharger toutes les factures PDF", "annual_certificate": "Certificat annuel",
    "date_from": "Date du", "date_to": "Date au", "invoice_date": "Date de facture",
    "invoice_number": "Facture no", "amount_without_vat": "Total HT", "amount_with_vat": "Total TTC",
    "fixed_amount": "Montant souhaite", "use_fixed_amount": "Utiliser montant souhaite", "invoice_list": "Liste des factures",
    "total_invoices": "Total factures", "save_settings": "Enregistrer les parametres", "company_name": "Nom de la societe",
    "company_address": "Adresse de la societe", "company_phone": "Telephone de la societe", "company_email": "Email de la societe",
    "company_vat": "Numero TVA", "invoice_template": "Modele de facture", "template_orange": "Orange",
    "template_blue": "Bleu", "template_green": "Vert", "search_client": "Rechercher client",
    "save_client_profile": "Enregistrer profil client", "invoice_design": "Design de facture", "service_details": "Details de prestation",
    "service_dates": "Dates de travail", "invoice_start_number": "Numero de facture initial", "paid": "Payee", "unpaid": "Non payee",
    "mark_paid": "Marquer payee", "mark_unpaid": "Marquer non payee", "payment_status": "Statut paiement", "company_settings": "Informations societe",
    "sent": "Envoye", "quote": "Devis", "quote_number": "Numero de devis", "quote_date": "Date de devis",
    "quote_text": "Texte du devis", "quote_price": "Montant du devis", "generate_quote": "Generer devis", "client_email": "Email client",
    "sent_status": "Statut d'envoi", "sent_yes": "Envoye", "sent_no": "Non envoye",
    "mark_sent": "Marquer envoye", "mark_unsent": "Marquer non envoye",
}
INVOICE_TRANSLATIONS["de"] = {
    "invoices": "Rechnungen", "invoice_settings": "Rechnungseinstellungen", "invoice_text": "Rechnungstext",
    "payment_terms": "Zahlungsbedingungen", "bank_account": "Bankkonto", "invoice_profiles": "Kundenprofile fuer Rechnungen",
    "client_type": "Kundentyp", "private_client": "Privatkunde", "pro_client": "Gewerbekunde",
    "hourly_rate": "Stundensatz", "email": "Email", "vat_rate": "MwSt.", "generate_invoice": "Rechnung erstellen",
    "download_all_invoices": "Alle Rechnungen als PDF herunterladen", "annual_certificate": "Jahreszertifikat",
    "date_from": "Datum von", "date_to": "Datum bis", "invoice_date": "Rechnungsdatum",
    "invoice_number": "Rechnungsnummer", "amount_without_vat": "Betrag ohne MwSt.", "amount_with_vat": "Betrag mit MwSt.",
    "fixed_amount": "Gewuenschter Betrag", "use_fixed_amount": "Gewuenschten Betrag verwenden", "invoice_list": "Rechnungsliste",
    "total_invoices": "Rechnungen gesamt", "save_settings": "Einstellungen speichern", "company_name": "Firmenname",
    "company_address": "Firmenadresse", "company_phone": "Telefon der Firma", "company_email": "Email der Firma",
    "company_vat": "USt-IdNr.", "invoice_template": "Rechnungsvorlage", "template_orange": "Orange",
    "template_blue": "Blau", "template_green": "Gruen", "search_client": "Kunde suchen",
    "save_client_profile": "Kundenprofil speichern", "invoice_design": "Rechnungsdesign", "service_details": "Leistungsdetails",
    "service_dates": "Arbeitstage", "invoice_start_number": "Start-Rechnungsnummer", "paid": "Bezahlt", "unpaid": "Nicht bezahlt",
    "mark_paid": "Als bezahlt markieren", "mark_unpaid": "Als unbezahlt markieren", "payment_status": "Zahlungsstatus", "company_settings": "Firmendaten",
    "sent": "Gesendet", "quote": "Angebot", "quote_number": "Angebotsnummer", "quote_date": "Angebotsdatum",
    "quote_text": "Angebotstext", "quote_price": "Angebotspreis", "generate_quote": "Angebot erstellen", "client_email": "Kunden-E-Mail",
    "sent_status": "Sendestatus", "sent_yes": "Gesendet", "sent_no": "Nicht gesendet",
    "mark_sent": "Als gesendet markieren", "mark_unsent": "Als nicht gesendet markieren",
}
INVOICE_TRANSLATIONS["pt"] = {
    "invoices": "Faturas", "invoice_settings": "Definicoes de faturas", "invoice_text": "Texto na fatura",
    "payment_terms": "Condicoes de pagamento", "bank_account": "Conta bancaria", "invoice_profiles": "Perfis de clientes para faturas",
    "client_type": "Tipo de cliente", "private_client": "Cliente privado", "pro_client": "Cliente profissional",
    "hourly_rate": "Preco por hora", "email": "Email", "vat_rate": "IVA", "generate_invoice": "Gerar fatura",
    "download_all_invoices": "Descarregar todas as faturas PDF", "annual_certificate": "Certificado anual",
    "date_from": "Data de", "date_to": "Data ate", "invoice_date": "Data da fatura",
    "invoice_number": "Numero da fatura", "amount_without_vat": "Valor sem IVA", "amount_with_vat": "Valor com IVA",
    "fixed_amount": "Valor desejado", "use_fixed_amount": "Usar valor desejado", "invoice_list": "Lista de faturas",
    "total_invoices": "Total de faturas", "save_settings": "Guardar definicoes", "company_name": "Nome da empresa",
    "company_address": "Endereco da empresa", "company_phone": "Telefone da empresa", "company_email": "Email da empresa",
    "company_vat": "Numero IVA", "invoice_template": "Modelo da fatura", "template_orange": "Laranja",
    "template_blue": "Azul", "template_green": "Verde", "search_client": "Pesquisar cliente",
    "save_client_profile": "Guardar perfil do cliente", "invoice_design": "Design da fatura", "service_details": "Detalhes do servico",
    "service_dates": "Datas de trabalho", "invoice_start_number": "Numero inicial da fatura", "paid": "Paga", "unpaid": "Nao paga",
    "mark_paid": "Marcar paga", "mark_unpaid": "Marcar nao paga", "payment_status": "Estado do pagamento", "company_settings": "Dados da empresa",
    "sent": "Enviadas", "quote": "Orcamento", "quote_number": "Numero do orcamento", "quote_date": "Data do orcamento",
    "quote_text": "Texto do orcamento", "quote_price": "Valor do orcamento", "generate_quote": "Gerar orcamento", "client_email": "Email do cliente",
    "sent_status": "Estado de envio", "sent_yes": "Enviada", "sent_no": "Nao enviada",
    "mark_sent": "Marcar enviada", "mark_unsent": "Marcar nao enviada",
}
for _lang, _values in INVOICE_TRANSLATIONS.items():
    TRANSLATIONS[_lang].update(_values)

MONTH_NAMES = {
    "bos": ["", "Januar", "Februar", "Mart", "April", "Maj", "Juni", "Juli", "August", "Septembar", "Oktobar", "Novembar", "Decembar"],
    "en": ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "fr": ["", "Janvier", "Fevrier", "Mars", "Avril", "Mai", "Juin", "Juillet", "Aout", "Septembre", "Octobre", "Novembre", "Decembre"],
    "de": ["", "Januar", "Februar", "Maerz", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
    "pt": ["", "Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"],
}



def get_lang():
    return session.get("lang", "bos")


def t():
    return TRANSLATIONS.get(get_lang(), TRANSLATIONS["bos"])


def get_theme():
    return session.get("theme", "light")


def hash_password(password):
    return generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)


def is_password_hash(value):
    return bool(value) and value.startswith(("pbkdf2:", "scrypt:"))


def verify_password(stored_password, submitted_password):
    if not stored_password:
        return False
    if is_password_hash(stored_password):
        return check_password_hash(stored_password, submitted_password)
    return stored_password == submitted_password


def lux_now():
    try:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("Europe/Luxembourg")).replace(tzinfo=None)
    except Exception as e:
        print("TIMEZONE ERROR:", e)
    return datetime.now()


def normalize_lux_address(address):
    address = (address or "").strip()
    if address and "luxembourg" not in address.lower():
        address += ", Luxembourg"
    return address


def get_coords(address):
    """Return (lat, lon) for an address using OpenRouteService geocoding."""
    api_key = os.environ.get("ORS_API_KEY", "").strip()
    address = normalize_lux_address(address)

    if not api_key:
        print("ROUTE ERROR: ORS_API_KEY is missing in Render Environment")
        return None
    if not address or len(address) < 3:
        print("ROUTE ERROR: bad/empty address", address)
        return None

    try:
        params = urllib.parse.urlencode({
            "api_key": api_key,
            "text": address,
            "size": 1,
            "boundary.country": "LU",
        })
        url = f"https://api.openrouteservice.org/geocode/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "LuxmannPlanner/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        if not data.get("features"):
            print("ROUTE ERROR: no coordinates found for", address)
            return None

        lon, lat = data["features"][0]["geometry"]["coordinates"][:2]
        return float(lat), float(lon)
    except Exception as e:
        print("ROUTE GEOCODING ERROR:", address, e)
        return None


def haversine_km(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def optimize_nearest_neighbor(start_coords, stops):
    remaining = stops[:]
    ordered = []
    total_km = 0.0
    current = start_coords

    while remaining:
        next_stop = min(remaining, key=lambda item: haversine_km(current, item["coords"]))
        total_km += haversine_km(current, next_stop["coords"])
        ordered.append(next_stop)
        current = next_stop["coords"]
        remaining.remove(next_stop)

    # Include return to the start address so mileage is useful for payroll/fuel planning.
    if ordered:
        total_km += haversine_km(current, start_coords)

    return ordered, total_km


def google_maps_directions_url(start_address, ordered_stops):
    # Start and finish at the worker start address. Stops become waypoints.
    waypoints = [stop["address"] for stop in ordered_stops]
    params = {
        "api": "1",
        "origin": normalize_lux_address(start_address),
        "destination": normalize_lux_address(start_address),
        "travelmode": "driving",
    }
    if waypoints:
        params["waypoints"] = "|".join(normalize_lux_address(x) for x in waypoints)
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(params)


def google_maps_embed_url(start_address, ordered_stops):
    # Simple embedded directions-style map. If Google blocks embed in some browsers, the normal Google Maps link still works.
    return google_maps_directions_url(start_address, ordered_stops) + "&output=embed"


def google_maps_navigation_url(address):
    params = {
        "api": "1",
        "destination": normalize_lux_address(address),
        "travelmode": "driving",
    }
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(params)


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

        # Translate SQLite strftime() → PostgreSQL TO_CHAR()
        # e.g. strftime('%Y-%m', col) → TO_CHAR(col::date, 'YYYY-MM')
        _STRFTIME_FMT = {'%Y': 'YYYY', '%m': 'MM', '%d': 'DD',
                         '%H': 'HH24', '%M': 'MI',  '%S': 'SS'}
        def _strftime_repl(m):
            fmt = m.group(1)
            col = m.group(2).strip()
            pg_fmt = fmt
            for k, v in _STRFTIME_FMT.items():
                pg_fmt = pg_fmt.replace(k, v)
            return f"TO_CHAR({col}::date, '{pg_fmt}')"
        q = re.sub(r"strftime\('([^']+)',\s*([^)]+?)\)", _strftime_repl, q, flags=re.IGNORECASE)

        q = q.replace("?", "%s")

        # Escape any bare % that psycopg2 would misread as a format specifier
        # (only % NOT already followed by 's' from our ?→%s replacement)
        q = re.sub(r'%(?!s)', '%%', q)
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

    @property
    def description(self):
        return self.cursor.description

    @property
    def rowcount(self):
        return self.cursor.rowcount


class _PgConn:
    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return _PgCursor(self.conn.cursor())

    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def close(self):
        return self.conn.close()


def get_conn():
    if USE_POSTGRES:
        # Render PostgreSQL provides DATABASE_URL. Internal URL is recommended when the DB
        # and web service are in the same Render account/region.
        return _PgConn(psycopg2.connect(DATABASE_URL))
    return sqlite3.connect(SQLITE_PATH)


def format_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return date_str


def safe_pdf_name(*parts):
    raw = "_".join(str(part or "").strip() for part in parts if str(part or "").strip())
    normalized = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized).strip("._")
    return cleaned or "document"


def client_city_from_address(address):
    text = " ".join(str(address or "").replace("\r", "\n").split())
    if not text:
        return ""
    after_postcode = re.search(r"\b(?:L-)?\d{4}\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' .-]*)$", text)
    if after_postcode:
        city = after_postcode.group(1).strip(" ,.-")
        return city[:1].upper() + city[1:] if city else ""
    before_postcode = re.search(r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'.-]*)\s+L-\d{4}\b", text)
    if before_postcode:
        city = before_postcode.group(1).strip(" ,.-")
        return city[:1].upper() + city[1:] if city else ""
    return ""


def client_city_map(clients):
    return {client[0]: client_city_from_address(client[1] if len(client) > 1 else "") for client in clients}


def allowed_document_name(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in DOCUMENT_EXTENSIONS


def safe_document_name(filename):
    normalized = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    return secure_filename(normalized)


def document_display_name(filename):
    normalized = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    normalized = re.sub(r"[\x00-\x1f\x7f]", "", normalized).strip()
    normalized = re.sub(r"^(?:&#(?:128196|128444);)+\s*", "", normalized)
    return re.sub(r"\s+", " ", normalized.replace("_", " ")).strip()


def document_relative_parts(path):
    parts = []
    for raw_part in str(path or "").replace("\\", "/").split("/"):
        clean_part = document_display_name(raw_part)
        if clean_part and clean_part not in (".", ".."):
            parts.append(clean_part[:140])
    return parts


def document_path(stored_name):
    safe_name = os.path.basename(stored_name or "")
    return os.path.join(DOCUMENT_ROOT, safe_name)


def document_inline_allowed(mime_type):
    return any((mime_type or "").startswith(prefix) for prefix in DOCUMENT_INLINE_MIME_PREFIXES)


def document_size_label(size_bytes):
    size = float(size_bytes or 0)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


def document_categories(tr):
    return [
        ("accounting", tr["document_accounting"]),
        ("clients", tr["document_clients"]),
        ("workers", tr["document_workers"]),
        ("contracts", tr["document_contracts"]),
        ("invoices", tr["document_invoices"]),
        ("other", tr["document_other"]),
    ]


def document_parent_id(value):
    try:
        parent_id = int(value or 0)
    except Exception:
        parent_id = 0
    return parent_id or None


def folder_breadcrumb(conn, folder_id):
    folders = []
    seen = set()
    current_id = document_parent_id(folder_id)
    c = conn.cursor()
    while current_id and current_id not in seen:
        seen.add(current_id)
        row = c.execute("SELECT id, name, parent_id FROM document_folders WHERE id = ?", (current_id,)).fetchone()
        if not row:
            break
        folders.append({"id": row[0], "name": row[1], "parent_id": row[2]})
        current_id = document_parent_id(row[2])
    return list(reversed(folders))


def get_or_create_document_folder(conn, name, parent_id=None):
    clean_name = re.sub(r"\s+", " ", str(name or "")).strip()[:140]
    if not clean_name:
        return parent_id
    c = conn.cursor()
    existing = c.execute(
        "SELECT id FROM document_folders WHERE name = ? AND " + ("parent_id = ?" if parent_id else "parent_id IS NULL"),
        (clean_name, parent_id) if parent_id else (clean_name,),
    ).fetchone()
    if existing:
        return existing[0]
    c.execute("""
        INSERT INTO document_folders (name, parent_id, created_at, created_by)
        VALUES (?, ?, ?, ?)
    """, (clean_name, parent_id, lux_now().strftime("%Y-%m-%d %H:%M:%S"), session.get("user", "")))
    inserted = c.execute(
        "SELECT id FROM document_folders WHERE name = ? AND " + ("parent_id = ?" if parent_id else "parent_id IS NULL"),
        (clean_name, parent_id) if parent_id else (clean_name,),
    ).fetchone()
    return inserted[0] if inserted else parent_id


def uploaded_document_folder(conn, parent_id, relative_path):
    path_parts = document_relative_parts(relative_path)
    for folder_name in path_parts[:-1]:
        parent_id = get_or_create_document_folder(conn, folder_name, parent_id)
    return parent_id


def document_folder_tree_ids(conn, folder_id):
    c = conn.cursor()
    folder_ids = []
    pending = [folder_id]
    seen = set()
    while pending:
        current_id = document_parent_id(pending.pop())
        if not current_id or current_id in seen:
            continue
        if not c.execute("SELECT id FROM document_folders WHERE id = ?", (current_id,)).fetchone():
            continue
        seen.add(current_id)
        folder_ids.append(current_id)
        pending.extend(row[0] for row in c.execute("SELECT id FROM document_folders WHERE parent_id = ?", (current_id,)).fetchall())
    return folder_ids


def share_expiry(days):
    try:
        days = int(days)
    except Exception:
        days = 7
    if days <= 0:
        return ""
    return (lux_now() + timedelta(days=min(days, 365))).strftime("%Y-%m-%d %H:%M:%S")


def share_is_active(expires_at, revoked):
    if revoked:
        return False
    if not expires_at:
        return True
    try:
        return datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S") > lux_now().replace(tzinfo=None)
    except Exception:
        return False


def save_uploaded_document(conn, upload, original_name, category, note, folder_id=None):
    clean_upload_name = safe_document_name(upload.filename)
    if not clean_upload_name or not allowed_document_name(clean_upload_name):
        return False
    clean_original_name = document_display_name(original_name) or document_display_name(clean_upload_name)
    if not allowed_document_name(safe_document_name(clean_original_name)):
        clean_original_name = document_display_name(clean_upload_name)
    extension = clean_upload_name.rsplit(".", 1)[1].lower()
    stored_name = f"{secrets.token_hex(18)}.{extension}"
    saved_path = document_path(stored_name)
    upload.save(saved_path)
    conn.cursor().execute("""
        INSERT INTO documents (original_name, stored_name, mime_type, file_size, category, folder_id, note, uploaded_at, uploaded_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        clean_original_name, stored_name, upload.mimetype or "", os.path.getsize(saved_path), category, folder_id,
        note, lux_now().strftime("%Y-%m-%d %H:%M:%S"), session.get("user", ""),
    ))
    return True


def pdf_doc(buffer, title, **kwargs):
    return SimpleDocTemplate(
        buffer,
        title=title,
        author="Luxmann Services",
        **kwargs,
    )


def month_name(month, lang=None):
    names = MONTH_NAMES.get(lang or get_lang(), MONTH_NAMES["bos"])
    if 1 <= int(month) <= 12:
        return names[int(month)]
    return str(month)


def format_month_year(year, month, lang=None):
    return f"{month_name(month, lang)} {year}"


def split_workers(worker_text):
    if not worker_text:
        return []
    return [w.strip() for w in worker_text.split(",") if w.strip()]


def join_workers(worker_list):
    return ", ".join([w.strip() for w in worker_list if w.strip()])


def worker_in_shift(worker_name, worker_text):
    return worker_name in split_workers(worker_text)


def worker_signature(worker_text):
    return tuple(sorted(split_workers(worker_text)))


def duplicate_shift_exists(conn, worker, client, date, time_range, exclude_id=None):
    c = conn.cursor()
    rows = c.execute("SELECT id, worker FROM shifts WHERE client = ? AND date = ? AND time = ?", (client, date, time_range)).fetchall()
    target = worker_signature(worker)
    for row_id, existing_worker in rows:
        if exclude_id and int(row_id) == int(exclude_id):
            continue
        if worker_signature(existing_worker) == target:
            return True
    return False


def replace_worker_in_shift(worker_text, old_name, new_name):
    return join_workers([new_name if n == old_name else n for n in split_workers(worker_text)])


def remove_worker_from_shift(worker_text, name):
    return join_workers([n for n in split_workers(worker_text) if n != name])


def get_status_label(status_key, tr):
    return {
        "planned": tr["status_planned"],
        "in_progress": tr["status_in_progress"],
        "done": tr["status_done"],
    }.get(status_key, status_key)


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
    """Automatski status po datumu i vremenu smjene (Europe/Luxembourg)."""
    try:
        start_str, end_str = [x.strip() for x in time_range.split("-")]
        start_dt = datetime.strptime(f"{shift_date} {start_str}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"{shift_date} {end_str}", "%Y-%m-%d %H:%M")
        now = lux_now()  # UTC+1/+2 — Luksemburg, ne server UTC

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


# ── Luxembourg Payroll Constants (2025) ──────────────────────────────────────
LUX_SSM_MENSUEL           = 2702.49  # Salaire social minimum non-qualifié 2025 (€/mois)
LUX_STD_MONTHLY_HOURS     = 173.33   # Ore mensili standard (40h/sem × 52 / 12)
LUX_SSM_HORAIRE           = round(LUX_SSM_MENSUEL / LUX_STD_MONTHLY_HOURS, 4)  # ≈ 15.59 €/h
# Caisse Maladie (salarié) — deux composantes distinctes sur la fiche de salaire
LUX_CCSS_MALADIE_SOINS    = 0.0280   # Caisse Maladie Soins (2.8000 %)
LUX_CCSS_MALADIE_ESPECES  = 0.0025   # Caisse Maladie Espèces (0.2500 %)
LUX_CCSS_HEALTH_EMP       = LUX_CCSS_MALADIE_SOINS + LUX_CCSS_MALADIE_ESPECES  # = 3.05 %
LUX_CCSS_PENSION_EMP      = 0.0800   # Caisse de Pension (salarié, 8.00 %)
LUX_CCSS_DEP_RATE         = 0.0140   # Caisse Dépendance (salarié, 1.4000 %)
LUX_CCSS_DEP_FRANCHISE    = LUX_SSM_MENSUEL / 3  # ≈ 900.83 €/mois
LUX_FORFAIT_FRAIS         = 45.0     # Forfait frais d'obtention mensuel (540 €/an ÷ 12)
LUX_SOLIDARITY_1          = 0.07     # Impôt de solidarité classe 1 & 1a
LUX_SOLIDARITY_2          = 0.09     # Impôt de solidarité classe 2
LUX_EMPLOYER_HEALTH       = 0.0305   # Assurance maladie (patronal, 3.05 %)
LUX_EMPLOYER_PENSION      = 0.0800   # Assurance pension (patronal, 8.00 %)
LUX_EMPLOYER_ACCIDENT     = 0.0110   # Assurance accident (patronal, ~1.1 %)

# Tranches d'imposition annuelles Luxembourg 2024/2025 (taux marginaux)
_LUX_BRACKETS = [
    (11265,0.00),(13173,0.08),(15081,0.09),(16989,0.10),(18897,0.11),
    (20805,0.12),(22713,0.14),(24621,0.16),(26529,0.18),(28437,0.20),
    (30345,0.22),(32253,0.24),(34161,0.26),(36069,0.28),(37977,0.30),
    (39885,0.32),(41793,0.34),(43701,0.36),(45609,0.38),(100002,0.39),
    (150000,0.40),(200004,0.41),(float('inf'),0.42),
]

def _lux_bracket_tax(annual_income):
    """Calcul de l'impôt annuel par tranches progressives."""
    if annual_income <= 0:
        return 0.0
    tax, prev = 0.0, 0
    for ceiling, rate in _LUX_BRACKETS:
        if annual_income <= prev:
            break
        tax += (min(annual_income, ceiling) - prev) * rate
        prev = ceiling
    return tax

def calc_lux_payroll(gross_monthly, tax_class='1', hours=0.0):
    """
    Obračun plate za Luksemburg.
    Vraća dict sa svim komponentama (brut → net → cijena za poslodavca).
    """
    # ── Odbitci CCSS (salarié) ──
    maladie_soins   = gross_monthly * LUX_CCSS_MALADIE_SOINS
    maladie_especes = gross_monthly * LUX_CCSS_MALADIE_ESPECES
    health          = maladie_soins + maladie_especes          # ukupno maladie
    pension         = gross_monthly * LUX_CCSS_PENSION_EMP
    dep_base        = max(0.0, gross_monthly - LUX_CCSS_DEP_FRANCHISE)
    dependency      = dep_base * LUX_CCSS_DEP_RATE
    total_ccss      = health + pension + dependency

    # ── Baza za porez (brut − maladie − pension − forfait frais) ──
    taxable_m   = max(0.0, gross_monthly - health - pension - LUX_FORFAIT_FRAIS)
    taxable_y   = taxable_m * 12

    # ── Porez na dohodak po klasi ──
    if tax_class == '2':
        base_tax_y  = _lux_bracket_tax(taxable_y / 2) * 2
        solidarity  = LUX_SOLIDARITY_2
    elif tax_class == '1a':
        t1 = _lux_bracket_tax(taxable_y)
        t2 = _lux_bracket_tax(taxable_y / 2) * 2
        base_tax_y  = (t1 + t2) / 2    # interpolacija između klase 1 i 2
        solidarity  = LUX_SOLIDARITY_1
    else:   # klasa 1
        base_tax_y  = _lux_bracket_tax(taxable_y)
        solidarity  = LUX_SOLIDARITY_1
    annual_tax  = base_tax_y * (1 + solidarity)
    monthly_tax = annual_tax / 12

    # ── Net plata ──
    net = gross_monthly - total_ccss - monthly_tax

    # ── Cijena za poslodavca (bruto-bruto) ──
    emp_health   = gross_monthly * LUX_EMPLOYER_HEALTH
    emp_pension  = gross_monthly * LUX_EMPLOYER_PENSION
    emp_accident = gross_monthly * LUX_EMPLOYER_ACCIDENT
    employer_total = gross_monthly + emp_health + emp_pension + emp_accident

    return {
        'hours':            round(hours, 2),
        'gross':            round(gross_monthly, 2),
        'maladie_soins':    round(maladie_soins, 2),
        'maladie_especes':  round(maladie_especes, 2),
        'health':           round(health, 2),
        'pension':          round(pension, 2),
        'dependency':       round(dependency, 2),
        'total_ccss':       round(total_ccss, 2),
        'taxable_m':        round(taxable_m, 2),
        'income_tax':       round(monthly_tax, 2),
        'net':              round(net, 2),
        'emp_health':       round(emp_health, 2),
        'emp_pension':      round(emp_pension, 2),
        'emp_accident':     round(emp_accident, 2),
        'employer_total':   round(employer_total, 2),
    }


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
    return datetime(today.year, today.month, 1)


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
        # Odmor (vacation): broji samo radne dane pon–pet
        if absence[2] == "vacation":
            count = 0
            cur = real_start
            while cur <= real_end:
                if cur.weekday() < 5:   # 0=pon … 4=pet
                    count += 1
                cur += timedelta(days=1)
            return count
        # Bolovanje i ostalo: svi kalendarski dani
        return (real_end - real_start).days + 1
    except Exception:
        return 0


def absence_totals_by_worker(absences, year, month):
    totals = {}
    for absence in absences:
        worker = absence[1]
        absence_type = absence[2]
        days = absence_days_in_month(absence, year, month)
        if days <= 0:
            continue
        totals.setdefault(worker, {"sick": 0, "vacation": 0, "other": 0})
        totals[worker][absence_type if absence_type in totals[worker] else "other"] += days
    return totals


def contract_reminders(workers, days_before=30):
    today = lux_now().date()
    reminders = []
    for worker in workers:
        name = worker[0]
        if name == "admin":
            continue
        contract_type = worker[2] if len(worker) > 2 else ""
        contract_end = worker[3] if len(worker) > 3 else ""
        if not contract_end:
            continue
        try:
            end_date = datetime.strptime(contract_end, "%Y-%m-%d").date()
        except Exception:
            continue
        days_left = (end_date - today).days
        if days_left < 0:
            reminders.append({"worker": name, "contract_type": contract_type, "contract_end": contract_end, "status": "expired", "days_left": days_left})
        elif days_left <= days_before:
            reminders.append({"worker": name, "contract_type": contract_type, "contract_end": contract_end, "status": "soon", "days_left": days_left})
    return reminders


def previous_month_range():
    today = lux_now().date()
    first_this_month = dt_date(today.year, today.month, 1)
    last_prev_month = first_this_month - timedelta(days=1)
    first_prev_month = dt_date(last_prev_month.year, last_prev_month.month, 1)
    return first_prev_month.strftime("%Y-%m-%d"), last_prev_month.strftime("%Y-%m-%d")


def get_invoice_settings(conn):
    c = conn.cursor()
    row = c.execute("SELECT invoice_text, payment_terms, bank_account, company_name, company_address, company_phone, company_email, company_vat, invoice_template, invoice_start_number FROM invoice_settings WHERE id = 1").fetchone()
    if not row:
        return {"invoice_text": "", "payment_terms": "", "bank_account": "", "company_name": "Luxmann Services", "company_address": "", "company_phone": "", "company_email": "", "company_vat": "", "invoice_template": "orange", "invoice_start_number": 1}
    return {
        "invoice_text": row[0] or "", "payment_terms": row[1] or "", "bank_account": row[2] or "",
        "company_name": row[3] or "Luxmann Services", "company_address": row[4] or "",
        "company_phone": row[5] or "", "company_email": row[6] or "", "company_vat": row[7] or "",
        "invoice_template": row[8] or "orange", "invoice_start_number": int(row[9] or 1),
    }


def sync_invoice_profiles(conn):
    c = conn.cursor()
    clients = c.execute("SELECT name, address FROM clients ORDER BY name").fetchall()
    for client in clients:
        c.execute("INSERT OR IGNORE INTO client_invoice_profiles (client_name, custom_address) VALUES (?, ?)", (client[0], client[1] or ""))
    conn.commit()


def get_invoice_profiles(conn):
    sync_invoice_profiles(conn)
    c = conn.cursor()
    rows = c.execute("""
        SELECT c.name, c.address, p.email, p.client_type, p.hourly_rate, p.custom_address
        FROM clients c
        LEFT JOIN client_invoice_profiles p ON p.client_name = c.name
        ORDER BY c.name
    """).fetchall()
    profiles = []
    for row in rows:
        profiles.append({
            "client": row[0],
            "base_address": row[1] or "",
            "email": row[2] or "",
            "client_type": row[3] or "private",
            "hourly_rate": float(row[4] or 0),
            "address": row[5] or row[1] or "",
        })
    return profiles


def invoice_vat_rate(client_type):
    return 0.17 if client_type == "pro" else 0.08


def invoice_service_title(date_from, date_to):
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d")
    except Exception:
        start = lux_now()
    month = month_name(start.month, "fr")
    prefix = "d'" if month[:1].lower() in "aeiou" else "de "
    return f"Entretien et nettoyage de la maison pour le mois {prefix}{month}'{str(start.year)[-2:]}"


def get_invoice_paid_map(conn):
    c = conn.cursor()
    return {row[0]: bool(row[1]) for row in c.execute("SELECT invoice_number, paid FROM invoice_records").fetchall()}


def build_invoice_rows(conn, date_from, date_to, fixed_amount=None, settings=None):
    c = conn.cursor()
    settings = settings or get_invoice_settings(conn)
    profiles = {p["client"]: p for p in get_invoice_profiles(conn)}
    shifts = c.execute("SELECT client, time, worker, date FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time, id", (date_from, date_to)).fetchall()
    hours_by_client = {}
    details_by_client = {}
    for client, time_range, worker_text, shift_date in shifts:
        worker_count = max(1, len(split_workers(worker_text)))
        hours = parse_shift_hours(time_range) * worker_count
        hours_by_client[client] = hours_by_client.get(client, 0) + hours
        details_by_client.setdefault(client, []).append({"date": shift_date, "hours": hours, "time": time_range, "workers": worker_count})

    rows = []
    paid_map = get_invoice_paid_map(conn)
    for index, (client, hours) in enumerate(sorted(hours_by_client.items())):
        profile = profiles.get(client)
        if not profile:
            continue
        base_amount = float(fixed_amount) if fixed_amount not in (None, "") else hours * profile["hourly_rate"]
        vat_rate = invoice_vat_rate(profile["client_type"])
        vat_amount = base_amount * vat_rate
        number = invoice_number_from_index(settings, index)
        rows.append({
            "invoice_number": number,
            "client": client,
            "address": profile["address"],
            "email": profile["email"],
            "client_type": profile["client_type"],
            "hours": hours,
            "hourly_rate": profile["hourly_rate"],
            "amount": base_amount,
            "vat_rate": vat_rate,
            "vat_amount": vat_amount,
            "total": base_amount + vat_amount,
            "details": details_by_client.get(client, []),
            "service_title": invoice_service_title(date_from, date_to),
            "paid": paid_map.get(str(number), False),
        })
    return rows


def save_invoice_records(conn, rows, date_from, date_to, invoice_date):
    c = conn.cursor()
    existing = {
        row[0]: {"paid": int(row[1] or 0), "paid_date": row[2] or "", "deleted": int(row[3] or 0), "sent": int(row[4] or 0)}
        for row in c.execute("SELECT invoice_number, paid, paid_date, COALESCE(deleted, 0), COALESCE(sent, 0) FROM invoice_records").fetchall()
    }
    for row in rows:
        invoice_number = str(row["invoice_number"])
        previous = existing.get(invoice_number, {"paid": 0, "paid_date": "", "sent": 0})
        if previous.get("deleted"):
            continue
        c.execute("""
            INSERT INTO invoice_records (invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date, sent, deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(invoice_number) DO UPDATE SET client_name = excluded.client_name, date_from = excluded.date_from,
            date_to = excluded.date_to, invoice_date = excluded.invoice_date, amount = excluded.amount,
            vat_amount = excluded.vat_amount, total = excluded.total, paid = excluded.paid, paid_date = excluded.paid_date,
            sent = excluded.sent
        """, (
            invoice_number, row["client"], date_from, date_to, invoice_date,
            row["amount"], row["vat_amount"], row["total"], previous["paid"], previous["paid_date"], previous["sent"],
        ))
        row["paid"] = bool(previous["paid"])
        row["sent"] = bool(previous["sent"])
    conn.commit()


def invoice_record_to_dict(record):
    return {
        "invoice_number": record[0],
        "client": record[1],
        "date_from": record[2],
        "date_to": record[3],
        "invoice_date": record[4],
        "amount": float(record[5] or 0),
        "vat_amount": float(record[6] or 0),
        "total": float(record[7] or 0),
        "paid": bool(record[8]),
        "paid_date": record[9] or "",
        "sent": bool(record[10]) if len(record) > 10 else False,
        "sent_date": record[11] if len(record) > 11 and record[11] else "",
        "source": record[12] if len(record) > 12 and record[12] else "auto",
    }


def get_invoice_row_for_record(conn, record):
    settings = get_invoice_settings(conn)
    rows = build_invoice_rows(conn, record["date_from"], record["date_to"], None, settings)
    row = next((r for r in rows if str(r["invoice_number"]) == str(record["invoice_number"])), None)
    if not row:
        row = next((r for r in rows if r["client"] == record["client"]), None)
    if row:
        row["invoice_number"] = record["invoice_number"]
        row["paid"] = record["paid"]
        row["amount"] = record["amount"]
        row["vat_amount"] = record["vat_amount"]
        row["total"] = record["total"]
        row["sent"] = record.get("sent", False)
        return row, settings
    return None, settings


def fetch_invoice_records(conn, date_from=None, date_to=None, client=None, status="all"):
    c = conn.cursor()
    conditions = []
    params = []
    conditions.append("COALESCE(deleted, 0) = 0")
    if date_from:
        conditions.append("invoice_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("invoice_date <= ?")
        params.append(date_to)
    if client:
        conditions.append("client_name = ?")
        params.append(client)
    if status == "paid":
        conditions.append("paid = 1")
    elif status == "unpaid":
        conditions.append("paid = 0")
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"""
        SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date, COALESCE(sent, 0), COALESCE(sent_date, ''), COALESCE(source, 'auto')
        FROM invoice_records
        {where}
        ORDER BY invoice_date DESC, CAST(invoice_number AS INTEGER) DESC
    """
    return [invoice_record_to_dict(row) for row in c.execute(query, params).fetchall()]


def invoice_number_from_index(settings, index):
    return str(int(settings.get("invoice_start_number") or 1) + index)


def build_invoice_pdf(row, settings, invoice_date, date_from, date_to, document_title="FACTURE"):
    buffer = io.BytesIO()
    doc = pdf_doc(buffer, f"{document_title} {row['invoice_number']} - {row['client']}", pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    template_colors = {
        "orange": "#ff7a2f",
        "blue": "#1f4f82",
        "green": "#2f7d32",
    }
    accent = template_colors.get(settings.get("invoice_template", "orange"), "#ff7a2f")
    normal = styles["Normal"]
    header = Table([[Paragraph(f"<b>{settings['company_name']}</b>", styles["Title"]), Paragraph(f"<b>{document_title}</b>", styles["Title"])]], colWidths=[12.5*cm, 5*cm])
    header.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), colors.HexColor(accent)), ("TEXTCOLOR", (0,0), (-1,-1), colors.white), ("ALIGN", (1,0), (1,0), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("LEFTPADDING", (0,0), (-1,-1), 8), ("RIGHTPADDING", (0,0), (-1,-1), 8)]))
    elements = [header, Spacer(1, 18)]

    company_lines = [settings.get("company_address", "").replace("\n", "<br/>")]
    if settings.get("company_phone"):
        company_lines.append(f"Tel: {settings['company_phone']}")
    if settings.get("company_email"):
        company_lines.append(settings["company_email"])
    logo_cell = ""
    if os.path.exists("static/logo.png"):
        logo_cell = Image("static/logo.png", width=4.5*cm, height=2.4*cm)
    company_table = Table([[Paragraph("<br/>".join([x for x in company_lines if x]), normal), logo_cell]], colWidths=[10*cm, 7.5*cm])
    company_table.setStyle(TableStyle([("ALIGN", (1,0), (1,0), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "TOP")]))
    elements += [company_table, Spacer(1, 34)]

    billing = Paragraph(f"<b>Facture a</b><br/>{row['client']}<br/>{(row['address'] or '-').replace(chr(10), '<br/>')}" + (f"<br/>{row['email']}" if row["email"] else ""), normal)
    meta = Paragraph(f"<b>Facture no</b>&nbsp;&nbsp;&nbsp; {row['invoice_number']}<br/><b>Date</b>&nbsp;&nbsp;&nbsp; {format_date(invoice_date)}", normal)
    elements += [Table([[billing, meta]], colWidths=[10*cm, 7.5*cm], style=[("ALIGN", (1,0), (1,0), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "TOP")]), Spacer(1, 28)]

    detail_lines = [row["service_title"]]
    for detail in row.get("details", []):
        detail_lines.append(f"{format_date(detail['date'])[:5]} {detail['hours']:.2f}h")
    detail_lines += [f"Total {row['hours']:.2f}h", f"Prix {row['hourly_rate']:.2f} EUR l'heure"]
    if settings.get("invoice_text"):
        pass

    invoice_table = Table([
        [Paragraph("<b>DESIGNATION</b>", normal), Paragraph("<b>MONTANT</b>", normal)],
        [Paragraph("<br/>".join(detail_lines), normal), Paragraph(f"{row['amount']:.2f}", normal)],
        ["", Paragraph(f"Total HT&nbsp;&nbsp;&nbsp; {row['amount']:.2f}", normal)],
        ["", Paragraph(f"TVA {row['vat_rate']*100:.1f}%&nbsp;&nbsp;&nbsp; {row['vat_amount']:.2f}", normal)],
        [Paragraph("<b>TOTAL TTC</b>", styles["Heading2"]), Paragraph(f"<b>{row['total']:.2f} EUR</b>", styles["Heading2"])],
    ], colWidths=[12.8*cm, 4.7*cm])
    invoice_table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey), ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("ALIGN", (1,1), (1,-1), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("SPAN", (0,2), (0,3)), ("BACKGROUND", (1,4), (1,4), colors.whitesmoke),
        ("MINROWHEIGHT", (0,1), (-1,1), 4.2*cm),
    ]))
    elements += [invoice_table, Spacer(1, 90)]

    payment_lines = [settings.get("payment_terms", ""), settings.get("company_vat", "")]
    elements += [Paragraph("<b>Conditions et modalites de paiement</b>", normal), Paragraph("<br/>".join([x for x in payment_lines if x]), normal)]
    doc.build(elements)
    buffer.seek(0)
    return buffer


def build_quote_pdf(data, settings):
    buffer = io.BytesIO()
    doc = pdf_doc(buffer, f"{data.get('document_title') or 'DEVIS'} {data['quote_number']} - {data['client_name']}", pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    accent = {"orange": "#ff7a2f", "blue": "#1f4f82", "green": "#2f7d32"}.get(settings.get("invoice_template", "orange"), "#ff7a2f")
    normal = styles["Normal"]
    document_title = data.get("document_title") or "DEVIS"
    header = Table([[Paragraph(f"<b>{settings['company_name']}</b>", styles["Title"]), Paragraph(f"<b>{document_title}</b>", styles["Title"])]], colWidths=[12.5*cm, 5*cm])
    header.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), colors.HexColor(accent)), ("TEXTCOLOR", (0,0), (-1,-1), colors.white), ("ALIGN", (1,0), (1,0), "RIGHT"), ("LEFTPADDING", (0,0), (-1,-1), 8), ("RIGHTPADDING", (0,0), (-1,-1), 8)]))
    company_lines = [settings.get("company_address", "").replace("\n", "<br/>")]
    if settings.get("company_phone"):
        company_lines.append(f"Tel: {settings['company_phone']}")
    if settings.get("company_email"):
        company_lines.append(settings["company_email"])
    logo_cell = Image("static/logo.png", width=4.5*cm, height=2.4*cm) if os.path.exists("static/logo.png") else ""
    client_block = f"<b>Devis pour</b><br/>{data['client_name']}<br/>{data['client_address'].replace(chr(10), '<br/>')}"
    if data.get("client_email"):
        client_block += f"<br/>{data['client_email']}"
    amount = float(data.get("amount") or 0)
    vat_rate = float(data.get("vat_rate") or 0)
    vat_amount = amount * vat_rate
    total = amount + vat_amount
    elements = [
        header, Spacer(1, 18),
        Table([[Paragraph("<br/>".join([x for x in company_lines if x]), normal), logo_cell]], colWidths=[10*cm, 7.5*cm], style=[("ALIGN", (1,0), (1,0), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "TOP")]),
        Spacer(1, 34),
        Table([[Paragraph(client_block, normal), Paragraph(f"<b>Devis no</b>&nbsp;&nbsp;&nbsp; {data['quote_number']}<br/><b>Date</b>&nbsp;&nbsp;&nbsp; {format_date(data['quote_date'])}", normal)]], colWidths=[10*cm, 7.5*cm], style=[("ALIGN", (1,0), (1,0), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "TOP")]),
        Spacer(1, 28),
    ]
    quote_table = Table([
        [Paragraph("<b>DESIGNATION</b>", normal), Paragraph("<b>MONTANT</b>", normal)],
        [Paragraph((data.get("quote_text") or "-").replace("\n", "<br/>"), normal), Paragraph(f"{amount:.2f}", normal)],
        ["", Paragraph(f"Total HT&nbsp;&nbsp;&nbsp; {amount:.2f}", normal)],
        ["", Paragraph(f"TVA {vat_rate*100:.1f}%&nbsp;&nbsp;&nbsp; {vat_amount:.2f}", normal)],
        [Paragraph("<b>TOTAL TTC</b>", styles["Heading2"]), Paragraph(f"<b>{total:.2f} EUR</b>", styles["Heading2"])],
    ], colWidths=[12.8*cm, 4.7*cm])
    quote_table.setStyle(TableStyle([("GRID", (0,0), (-1,-1), 0.5, colors.grey), ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke), ("ALIGN", (1,1), (1,-1), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "TOP"), ("SPAN", (0,2), (0,3)), ("BACKGROUND", (1,4), (1,4), colors.whitesmoke), ("MINROWHEIGHT", (0,1), (-1,1), 4.2*cm)]))
    payment_lines = [settings.get("payment_terms", ""), settings.get("company_vat", "")]
    elements += [quote_table, Spacer(1, 80), Paragraph("<b>Conditions et modalites</b>", normal), Paragraph("<br/>".join([x for x in payment_lines if x]), normal)]
    doc.build(elements)
    buffer.seek(0)
    return buffer


def build_invoice_certificate_pdf(rows, invoice_date, date_from, date_to):
    buffer = io.BytesIO()
    doc = pdf_doc(buffer, f"Certificat factures {date_from} - {date_to}", pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Certificat annuel des factures", styles["Title"]), Spacer(1, 10), Paragraph(f"Periode: {format_date(date_from)} - {format_date(date_to)}", styles["Normal"]), Paragraph(f"Date: {format_date(invoice_date)}", styles["Normal"]), Spacer(1, 12)]
    data = [["Client", "Heures", "HTVA", "TVA", "Total"]]
    for row in rows:
        data.append([row["client"], f"{row['hours']:.2f}", f"{row['amount']:.2f}", f"{row['vat_amount']:.2f}", f"{row['total']:.2f}"])
    data.append(["TOTAL", "", f"{sum(r['amount'] for r in rows):.2f}", f"{sum(r['vat_amount'] for r in rows):.2f}", f"{sum(r['total'] for r in rows):.2f}"])
    table = Table(data, colWidths=[7*cm, 2.5*cm, 3*cm, 3*cm, 3*cm])
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4f82")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("GRID", (0,0), (-1,-1), 0.5, colors.grey), ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold")]))
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return buffer


def build_client_statement_pdf(client_name, records, date_from, date_to):
    buffer = io.BytesIO()
    doc = pdf_doc(buffer, f"Releve {client_name} {date_from} - {date_to}", pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    total_paid = sum(r["total"] for r in records if r["paid"])
    total_unpaid = sum(r["total"] for r in records if not r["paid"])
    elements = [
        Paragraph(f"Releve de compte client - {client_name}", styles["Title"]),
        Spacer(1, 10),
        Paragraph(f"Periode: {format_date(date_from)} - {format_date(date_to)}", styles["Normal"]),
        Spacer(1, 12),
    ]
    data = [["Facture", "Date", "Statut", "Paye", "Montant"]]
    for record in records:
        paid_amount = record["total"] if record["paid"] else 0
        data.append([
            record["invoice_number"],
            format_date(record["invoice_date"]),
            "Payee" if record["paid"] else "Non payee",
            f"{paid_amount:.2f} EUR",
            f"{record['total']:.2f} EUR",
        ])
    data.append(["TOTAL", "", "", f"{total_paid:.2f} EUR", f"{sum(r['total'] for r in records):.2f} EUR"])
    table = Table(data, colWidths=[3*cm, 3*cm, 4*cm, 4*cm, 4*cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#4a4a4a")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
    ]))
    elements += [table, Spacer(1, 12), Paragraph(f"Non paye: {total_unpaid:.2f} EUR", styles["Normal"])]
    doc.build(elements)
    buffer.seek(0)
    return buffer


def next_invoice_number(conn):
    """Return the next sequential invoice number as a string."""
    c = conn.cursor()
    settings = get_invoice_settings(conn)
    start = int(settings.get("invoice_start_number") or 1)
    rows = c.execute(
        "SELECT invoice_number FROM invoice_records WHERE COALESCE(deleted,0)=0"
    ).fetchall()
    nums = []
    for (n,) in rows:
        try:
            nums.append(int("".join(filter(str.isdigit, str(n)))))
        except Exception:
            pass
    return str(max(max(nums) + 1, start) if nums else start)


def build_manual_invoice_pdf(draft, settings):
    """Build a ReportLab PDF for a manually created multi-line-item invoice."""
    buffer = io.BytesIO()
    inv_num = draft["invoice_number"]
    doc = pdf_doc(
        buffer, f"FACTURE {inv_num} - {draft.get('client_name','')[:40]}",
        pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )
    styles = getSampleStyleSheet()
    accent = {"orange": "#ff7a2f", "blue": "#1f4f82", "green": "#2f7d32"}.get(
        settings.get("invoice_template", "orange"), "#ff7a2f"
    )
    normal = styles["Normal"]

    # ── Header bar ────────────────────────────────────────────────────────
    header = Table([
        [Paragraph(f"<b>{settings.get('company_name','')}</b>", styles["Title"]),
         Paragraph("<b>FACTURE</b>", styles["Title"])]
    ], colWidths=[12.5*cm, 5*cm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor(accent)),
        ("TEXTCOLOR", (0,0), (-1,-1), colors.white),
        ("ALIGN", (1,0), (1,0), "RIGHT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
    ]))

    # ── Company info + logo ───────────────────────────────────────────────
    co_lines = [settings.get("company_address", "").replace("\n", "<br/>")]
    if settings.get("company_phone"):
        co_lines.append(f"Tel: {settings['company_phone']}")
    if settings.get("company_email"):
        co_lines.append(settings["company_email"])
    logo_cell = (Image("static/logo.png", width=4.5*cm, height=2.4*cm)
                 if os.path.exists("static/logo.png") else "")
    co_tbl = Table(
        [[Paragraph("<br/>".join([x for x in co_lines if x]), normal), logo_cell]],
        colWidths=[10*cm, 7.5*cm],
    )
    co_tbl.setStyle(TableStyle([
        ("ALIGN", (1,0), (1,0), "RIGHT"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))

    # ── Billing address + invoice meta ────────────────────────────────────
    addr = (draft.get("client_address") or draft.get("client_name","")).replace("\n", "<br/>")
    billing = Paragraph(f"<b>Facture a</b><br/>{addr}", normal)
    meta = Paragraph(
        f"<b>Facture no</b>&nbsp;&nbsp;&nbsp; {inv_num}<br/>"
        f"<b>Date</b>&nbsp;&nbsp;&nbsp; {format_date(draft.get('invoice_date',''))}",
        normal,
    )
    bill_meta = Table([[billing, meta]], colWidths=[10*cm, 7.5*cm],
                      style=[("ALIGN",(1,0),(1,0),"RIGHT"),("VALIGN",(0,0),(-1,-1),"TOP")])

    # ── Line items table ──────────────────────────────────────────────────
    try:
        items = json.loads(draft.get("items_json") or "[]")
    except Exception:
        items = []

    tdata = [[
        Paragraph("<b>DESIGNATION</b>", normal),
        Paragraph("<b>HT (EUR)</b>", normal),
        Paragraph("<b>TVA</b>", normal),
        Paragraph("<b>TTC (EUR)</b>", normal),
    ]]
    total_ht = 0.0; total_vat = 0.0
    for item in items:
        amt = float(item.get("amount") or 0)
        vr  = float(item.get("vat_rate") or 0) / 100.0
        vat = amt * vr
        total_ht  += amt
        total_vat += vat
        tdata.append([
            Paragraph((item.get("designation") or "").replace("\n", "<br/>"), normal),
            Paragraph(f"{amt:.2f}", normal),
            Paragraph(f"{vr*100:.0f}%", normal),
            Paragraph(f"{amt+vat:.2f}", normal),
        ])
    total_ttc = total_ht + total_vat
    n_body = len(items) + 1   # number of rows before totals (header + item rows)
    tdata += [
        ["", Paragraph(f"Total HT:  {total_ht:.2f} EUR", normal), "", ""],
        ["", Paragraph(f"TVA:  {total_vat:.2f} EUR", normal), "", ""],
        [Paragraph("<b>TOTAL TTC</b>", styles["Heading2"]), "", "",
         Paragraph(f"<b>{total_ttc:.2f} EUR</b>", styles["Heading2"])],
    ]
    n_total = len(tdata) - 1   # row index of the TOTAL TTC row
    items_tbl = Table(tdata, colWidths=[8.8*cm, 3.2*cm, 1.8*cm, 3.7*cm])
    items_tbl.setStyle(TableStyle([
        ("GRID",       (0,0),       (-1, n_body-1), 0.5, colors.grey),
        ("BACKGROUND", (0,0),       (-1, 0),         colors.whitesmoke),
        ("ALIGN",      (1,0),       (-1, -1),        "RIGHT"),
        ("VALIGN",     (0,0),       (-1, -1),        "TOP"),
        # TOTAL TTC row: span cols 0-2 so the label is visible
        ("SPAN",       (0,n_total), (2, n_total)),
        ("ALIGN",      (0,n_total), (0, n_total),    "LEFT"),
        ("BACKGROUND", (0,n_total), (-1, n_total),   colors.whitesmoke),
        ("FONTNAME",   (0,n_total), (-1, n_total),   "Helvetica-Bold"),
        ("LINEABOVE",  (0,n_total), (-1, n_total),   1, colors.grey),
    ]))

    # ── Payment terms ─────────────────────────────────────────────────────
    pay = (draft.get("payment_terms") or settings.get("payment_terms", "")).replace("\n", "<br/>")

    elements = [
        header, Spacer(1, 18),
        co_tbl,  Spacer(1, 34),
        bill_meta, Spacer(1, 28),
        items_tbl, Spacer(1, 60),
        Paragraph("<b>Conditions et modalites de paiement</b>", normal),
        Paragraph(pay, normal),
    ]
    doc.build(elements)
    buffer.seek(0)
    return buffer


def build_invoice_list_pdf(records, date_from, date_to):
    buffer = io.BytesIO()
    doc = pdf_doc(buffer, f"Liste factures {date_from} - {date_to}", pagesize=landscape(A4), rightMargin=1.2*cm, leftMargin=1.2*cm, topMargin=1.2*cm, bottomMargin=1.2*cm)
    styles = getSampleStyleSheet()
    total_ht = sum(r["amount"] for r in records)
    total_tva = sum(r["vat_amount"] for r in records)
    total_ttc = sum(r["total"] for r in records)
    total_paid = sum(r["total"] for r in records if r["paid"])
    total_unpaid = sum(r["total"] for r in records if not r["paid"])
    elements = [
        Paragraph("Liste des factures", styles["Title"]),
        Spacer(1, 8),
        Paragraph(f"Periode: {format_date(date_from)} - {format_date(date_to)}", styles["Normal"]),
        Spacer(1, 12),
    ]
    data = [["No", "Client", "Date", "HT", "TVA", "TTC", "Statut"]]
    for record in records:
        data.append([
            record["invoice_number"],
            record["client"],
            format_date(record["invoice_date"]),
            f"{record['amount']:.2f}",
            f"{record['vat_amount']:.2f}",
            f"{record['total']:.2f}",
            "Payee" if record["paid"] else "Non payee",
        ])
    data.append(["TOTAL", "", "", f"{total_ht:.2f}", f"{total_tva:.2f}", f"{total_ttc:.2f}", ""])
    table = Table(data, colWidths=[2.2*cm, 7*cm, 3*cm, 3*cm, 3*cm, 3*cm, 3.5*cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#4a4a4a")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.4, colors.grey),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
    ]))
    elements += [table, Spacer(1, 12), Paragraph(f"Payee: {total_paid:.2f} EUR | Non payee: {total_unpaid:.2f} EUR", styles["Normal"])]
    doc.build(elements)
    buffer.seek(0)
    return buffer


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
            address TEXT DEFAULT '',
            contract_type TEXT DEFAULT '',
            contract_end_date TEXT DEFAULT ''
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'vacation',
            date_from TEXT NOT NULL,
            date_to TEXT NOT NULL,
            note TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            admin_note TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS worker_payroll_settings (
            worker TEXT PRIMARY KEY,
            salary_type TEXT DEFAULT 'hourly',
            hourly_rate REAL DEFAULT 15.59,
            fixed_gross REAL DEFAULT 0,
            tax_class TEXT DEFAULT '1',
            num_children INTEGER DEFAULT 0,
            notes TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS document_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            parent_id INTEGER,
            created_at TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            UNIQUE(name, parent_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_name TEXT,
            stored_name TEXT UNIQUE,
            mime_type TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0,
            category TEXT DEFAULT 'other',
            folder_id INTEGER,
            note TEXT DEFAULT '',
            uploaded_at TEXT DEFAULT '',
            uploaded_by TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS document_shares (
            token TEXT PRIMARY KEY,
            document_id INTEGER,
            created_at TEXT DEFAULT '',
            expires_at TEXT DEFAULT '',
            allow_download INTEGER DEFAULT 1,
            revoked INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS folder_shares (
            token TEXT PRIMARY KEY,
            folder_id INTEGER,
            created_at TEXT DEFAULT '',
            expires_at TEXT DEFAULT '',
            revoked INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoice_settings (
            id INTEGER PRIMARY KEY,
            invoice_text TEXT DEFAULT '',
            payment_terms TEXT DEFAULT '',
            bank_account TEXT DEFAULT '',
            company_name TEXT DEFAULT '',
            company_address TEXT DEFAULT '',
            company_phone TEXT DEFAULT '',
            company_email TEXT DEFAULT '',
            company_vat TEXT DEFAULT '',
            invoice_template TEXT DEFAULT 'orange',
            invoice_start_number INTEGER DEFAULT 1
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS client_invoice_profiles (
            client_name TEXT PRIMARY KEY,
            email TEXT DEFAULT '',
            client_type TEXT DEFAULT 'private',
            hourly_rate REAL DEFAULT 0,
            custom_address TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoice_records (
            invoice_number TEXT PRIMARY KEY,
            client_name TEXT,
            date_from TEXT,
            date_to TEXT,
            invoice_date TEXT,
            amount REAL DEFAULT 0,
            vat_amount REAL DEFAULT 0,
            total REAL DEFAULT 0,
            paid INTEGER DEFAULT 0,
            paid_date TEXT DEFAULT '',
            sent INTEGER DEFAULT 0,
            sent_date TEXT DEFAULT '',
            deleted INTEGER DEFAULT 0,
            source TEXT DEFAULT 'auto'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS manual_invoice_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT UNIQUE,
            client_name TEXT DEFAULT '',
            client_address TEXT DEFAULT '',
            invoice_date TEXT DEFAULT '',
            items_json TEXT DEFAULT '[]',
            payment_terms TEXT DEFAULT '',
            total_ht REAL DEFAULT 0,
            total_vat REAL DEFAULT 0,
            total_ttc REAL DEFAULT 0,
            created_at TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS manual_item_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            designation TEXT DEFAULT '',
            default_amount REAL DEFAULT 0,
            default_vat REAL DEFAULT 17,
            sort_order INTEGER DEFAULT 0
        )
    """)

    # Migration safety
    shift_cols = [row[1] for row in c.execute("PRAGMA table_info(shifts)").fetchall()]
    if "status" not in shift_cols:
        c.execute("ALTER TABLE shifts ADD COLUMN status TEXT DEFAULT 'planned'")
    worker_cols = [row[1] for row in c.execute("PRAGMA table_info(workers)").fetchall()]
    if "address" not in worker_cols:
        c.execute("ALTER TABLE workers ADD COLUMN address TEXT DEFAULT ''")
    if "contract_type" not in worker_cols:
        c.execute("ALTER TABLE workers ADD COLUMN contract_type TEXT DEFAULT ''")
    if "contract_end_date" not in worker_cols:
        c.execute("ALTER TABLE workers ADD COLUMN contract_end_date TEXT DEFAULT ''")
    client_cols = [row[1] for row in c.execute("PRAGMA table_info(clients)").fetchall()]
    if "address" not in client_cols:
        c.execute("ALTER TABLE clients ADD COLUMN address TEXT DEFAULT ''")
    invoice_cols = [row[1] for row in c.execute("PRAGMA table_info(invoice_settings)").fetchall()]
    for col_name, col_type in [
        ("company_name", "TEXT DEFAULT ''"), ("company_address", "TEXT DEFAULT ''"),
        ("company_phone", "TEXT DEFAULT ''"), ("company_email", "TEXT DEFAULT ''"),
        ("company_vat", "TEXT DEFAULT ''"), ("invoice_template", "TEXT DEFAULT 'orange'"),
        ("invoice_start_number", "INTEGER DEFAULT 1"),
    ]:
        if col_name not in invoice_cols:
            c.execute(f"ALTER TABLE invoice_settings ADD COLUMN {col_name} {col_type}")
    invoice_record_cols = [row[1] for row in c.execute("PRAGMA table_info(invoice_records)").fetchall()]
    if "deleted" not in invoice_record_cols:
        c.execute("ALTER TABLE invoice_records ADD COLUMN deleted INTEGER DEFAULT 0")
    if "sent" not in invoice_record_cols:
        c.execute("ALTER TABLE invoice_records ADD COLUMN sent INTEGER DEFAULT 0")
    if "sent_date" not in invoice_record_cols:
        c.execute("ALTER TABLE invoice_records ADD COLUMN sent_date TEXT DEFAULT ''")
    if "source" not in invoice_record_cols:
        c.execute("ALTER TABLE invoice_records ADD COLUMN source TEXT DEFAULT 'auto'")
    document_cols = [row[1] for row in c.execute("PRAGMA table_info(documents)").fetchall()]
    if "folder_id" not in document_cols:
        c.execute("ALTER TABLE documents ADD COLUMN folder_id INTEGER")
    payroll_cols = [row[1] for row in c.execute("PRAGMA table_info(worker_payroll_settings)").fetchall()]
    if "salary_type" not in payroll_cols:
        c.execute("ALTER TABLE worker_payroll_settings ADD COLUMN salary_type TEXT DEFAULT 'hourly'")
    if "fixed_gross" not in payroll_cols:
        c.execute("ALTER TABLE worker_payroll_settings ADD COLUMN fixed_gross REAL DEFAULT 0")

    c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", ("admin", hash_password("admin123"), "admin"))
    c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", ("worker1", hash_password("1234"), "worker"))
    c.execute("INSERT OR IGNORE INTO workers (name, address) VALUES (?, ?)", ("admin", ""))
    c.execute("INSERT OR IGNORE INTO workers (name, address) VALUES (?, ?)", ("worker1", ""))
    c.execute("INSERT OR IGNORE INTO invoice_settings (id, invoice_text, payment_terms, bank_account, company_name, company_address, company_phone, company_email, company_vat, invoice_template, invoice_start_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (1, "", "Paiement a 15 jours des reception de la facture.\nPost Luxembourg BIC (CCPLLULL) LU60 1111 7815 3607 0000\nLors du virement, veuillez indiquer reference suivante: ***Facture no***", "", "Luxmann Services", "32, rue Aneschbach\nWiltz L-9511", "+352691642003", "lux@mann.lu", "TVA: LU33673043", "orange", 1))

    for worker_name, color in DEFAULT_WORKER_COLORS.items():
        c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (worker_name, color))

    conn.commit()
    conn.close()


init_db()


BASE_STYLE = """
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Luxmann">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#1e3a5f">
<link rel="manifest" href="/manifest.json">
<script>if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(function(){});}</script>
<style>
    html { -webkit-text-size-adjust:100%; text-size-adjust:100%; }
    body { font-family: Arial, sans-serif; margin:24px; background: {{ '#111113' if dark else '#f4f6f8' }}; color: {{ '#e5e7eb' if dark else '#1f2937' }}; touch-action:pan-y; overflow-x:hidden; }
    h1 { color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
    h2, h3, h4 { color: {{ '#e5e7eb' if dark else '#111827' }}; }
    .brandbar, .card { background: {{ '#161618' if dark else 'white' }}; border-radius:12px; box-shadow:0 4px 14px rgba(0,0,0,0.06); }
    .brandbar { display:flex; justify-content:space-between; align-items:center; padding:14px 18px; margin-bottom:18px; }
    .brandleft { display:flex; align-items:center; gap:14px; }
    .brandleft img { height:56px; {% if dark %}filter:invert(1) hue-rotate(180deg);{% else %}mix-blend-mode:multiply;{% endif %} }
    .brandtitle { font-size:24px; font-weight:700; color: {{ '#93c5fd' if dark else '#1f4f82' }}; }
    .langbar a, .topbar a, .theme-links a, .week-link, .pdf-link, .reset-link, a { color: {{ '#93c5fd' if dark else '#1f4f82' }}; text-decoration:none; font-weight:bold; margin-right:10px; }
    .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:16px; }
    .card { padding:18px; }
    input, select, button { padding:10px; margin:6px 0; width:100%; box-sizing:border-box; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }}; border-radius:8px; background: {{ '#1e1e20' if dark else 'white' }}; color: {{ '#e5e7eb' if dark else '#111827' }}; }
    button { background:#1f4f82; color:white; border:none; cursor:pointer; }
    .shift { background: {{ 'linear-gradient(135deg, #161618, #1e1e20)' if dark else 'linear-gradient(135deg, #ffffff, #f1f5f9)' }}; padding:14px; margin:12px 0; border-radius:12px; box-shadow:0 4px 14px rgba(0,0,0,0.06); }
    .mini-shift { margin-top:6px; padding:6px; border-radius:8px; font-size:12px; background: {{ '#1e1e20' if dark else '#f8fafc' }}; }
    .calendar-board { border-radius:16px; padding:10px; background:{{ '#0c0c0e' if dark else '#eef3fb' }}; border:1px solid {{ '#222225' if dark else '#dce5f2' }}; }
    .calendar-day-card { background:{{ '#141416' if dark else '#fbfcff' }} !important; border:1px solid {{ '#222225' if dark else '#dfe7f2' }}; border-radius:9px; box-shadow:0 1px 5px rgba(15,23,42,0.07) !important; }
    .week-day-heading {
        display:block;
        margin:-7px -7px 10px;
        padding:11px 10px;
        border-radius:8px;
        background:{{ '#1e2124' if dark else '#d9e6f8' }};
        border:1px solid {{ '#2e3035' if dark else '#b7cee9' }};
        color:{{ '#dbeafe' if dark else '#173b63' }} !important;
        box-shadow:0 3px 8px rgba(15,23,42,0.1);
        line-height:1.35;
    }
    .weekend-soft .week-day-heading { background:{{ '#42252e' if dark else '#f8dfe5' }}; border-color:{{ '#70404d' if dark else '#edbdc9' }}; color:{{ '#fecdd3' if dark else '#8a2744' }} !important; }
    .calendar-board .mini-shift {
        --shift-accent:#7aa7df;
        padding:8px;
        color:{{ '#e5e7eb' if dark else '#1f2937' }};
        background:{{ '#191919' if dark else '#eaf2fd' }};
        background:color-mix(in srgb, var(--shift-accent) {{ '28%' if dark else '23%' }}, {{ '#161618' if dark else 'white' }});
        border:1px solid color-mix(in srgb, var(--shift-accent) {{ '48%' if dark else '34%' }}, {{ '#222225' if dark else '#d8e2f0' }});
        border-left:5px solid var(--shift-accent) !important;
        box-shadow:none;
        margin-bottom:3px;
        border-bottom:{{ '1px solid rgba(255,255,255,0.07)' if dark else '1px solid transparent' }} !important;
    }
    .client-city { font-weight:700; text-transform:capitalize; white-space:nowrap; }
    .user-row, .hours-row { padding:8px 0; border-bottom:1px solid {{ '#2c2c30' if dark else '#e5e7eb' }}; }
    .muted { color: {{ '#9ca3af' if dark else '#64748b' }}; font-size:14px; }
    .status-badge { color:white; padding:4px 8px; border-radius:6px; font-size:12px; font-weight:bold; margin-left:8px; }
    .action-link, .mini-link { display:inline-flex; align-items:center; justify-content:center; text-decoration:none; margin:2px 2px 0; font-weight:bold; font-size:12px; padding:5px 8px; border-radius:6px; min-height:28px; touch-action:manipulation; -webkit-tap-highlight-color:transparent; }
    .edit-link { color: {{ '#93c5fd' if dark else '#1f4f82' }}; background:{{ 'rgba(147,197,253,0.12)' if dark else 'rgba(31,79,130,0.08)' }}; }
    .delete-link { color:#ef4444; background:rgba(239,68,68,0.08); }
    .copy-link { color:#16a34a; background:rgba(22,163,74,0.08); }
    .check-row { display:flex; align-items:center; gap:8px; margin:5px 0; }
    .check-row input { width:auto; }
    .weekend-soft { border:2px solid {{ '#7f4141' if dark else '#f3caca' }} !important; background:{{ '#26191d' if dark else '#fff7f8' }} !important; }
    .holiday-soft { background:{{ '#302816' if dark else '#fffaf0' }} !important; border:2px solid {{ '#8a6a22' if dark else '#f4dda6' }} !important; }
    .holiday-note { display:block; color:#dc2626; font-size:11px; margin-top:4px; font-weight:bold; }
    .drop-target { outline:2px dashed #22c55e; }
    .modal-backdrop { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.45); z-index:50; }
    .modal-card { max-width:420px; margin:12vh auto; background:{{ '#161618' if dark else 'white' }}; color:{{ '#e5e7eb' if dark else '#111827' }}; border-radius:12px; padding:20px; box-shadow:0 10px 30px rgba(0,0,0,0.25); }
    .client-search-wrapper { position:relative; }
    .client-search-dropdown { display:none; position:absolute; top:100%; left:0; right:0; z-index:500; background:{{ '#1d1d1f' if dark else 'white' }}; border:1px solid {{ '#2c2c30' if dark else '#dbeafe' }}; border-radius:0 0 8px 8px; max-height:220px; overflow-y:auto; box-shadow:0 6px 20px rgba(0,0,0,0.15); }
    .client-search-item { padding:9px 13px; cursor:pointer; font-size:13px; border-bottom:1px solid {{ '#2c2c30' if dark else '#f0f4fa' }}; line-height:1.4; }
    .client-search-item:hover, .client-search-item.active { background:{{ '#2c2c30' if dark else '#eef4ff' }}; }
    .client-search-item:last-child { border-bottom:none; }

    /* ── Topbar action icons (desktop/tablet only) ── */
    .topbar {
        display:none; position:fixed; top:0; left:66px; right:0; height:52px;
        background:{{ '#0e0e10' if dark else '#f8fafc' }};
        border-bottom:1px solid {{ '#1d1d1f' if dark else '#e2e8f0' }};
        z-index:250; align-items:center; justify-content:flex-end;
        padding:0 16px; gap:8px;
    }
    .topicon-btn {
        width:38px; height:38px; border-radius:50%;
        border:1px solid {{ '#1d1d1f' if dark else '#e2e8f0' }};
        background:{{ '#1d1d1f' if dark else '#ffffff' }};
        display:flex; align-items:center; justify-content:center;
        cursor:pointer; color:{{ '#94a3b8' if dark else '#475569' }};
        position:relative; transition:all 0.15s; flex-shrink:0;
        font-size:16px; text-decoration:none;
        box-shadow: 0 1px 4px rgba(0,0,0,{{ '0.25' if dark else '0.07' }});
    }
    .topicon-btn:hover {
        background:{{ '#2c2c30' if dark else '#e9f0f8' }};
        color:{{ '#e2e8f0' if dark else '#1f4f82' }};
        border-color:{{ '#363638' if dark else '#c8def7' }};
    }
    .topicon-btn.active {
        background:#1f4f82; color:white; border-color:#1f4f82;
    }
    .topicon-lang {
        font-size:11px; font-weight:800; letter-spacing:0.03em;
    }
    .notif-badge {
        position:absolute; top:-3px; right:-3px; min-width:16px; height:16px;
        border-radius:8px; background:#ef4444; color:white;
        font-size:9px; font-weight:800; display:flex; align-items:center;
        justify-content:center; padding:0 3px; border:2px solid {{ '#0e0e10' if dark else '#f8fafc' }};
        animation: notif-pulse 2s infinite;
    }
    @keyframes notif-pulse {
        0%,100% { box-shadow:0 0 0 0 rgba(239,68,68,0.5); }
        50%      { box-shadow:0 0 0 5px rgba(239,68,68,0); }
    }
    /* Notification dropdown */
    .notif-dropdown {
        display:none; position:absolute; top:46px; right:0; width:320px;
        background:{{ '#1d1d1f' if dark else 'white' }};
        border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
        border-radius:14px; box-shadow:0 12px 32px rgba(0,0,0,0.22);
        z-index:400; overflow:hidden;
    }
    .notif-dropdown.open { display:block; }
    .notif-header {
        padding:12px 16px 8px; font-size:13px; font-weight:700;
        color:{{ '#e2e8f0' if dark else '#1e293b' }};
        border-bottom:1px solid {{ '#2c2c30' if dark else '#f1f5f9' }};
        display:flex; align-items:center; justify-content:space-between;
    }
    .notif-item {
        padding:12px 16px; border-bottom:1px solid {{ '#1d1d1f' if dark else '#f8fafc' }};
        font-size:13px;
    }
    .notif-item:last-child { border-bottom:none; }
    .notif-worker { font-weight:700; color:{{ '#93c5fd' if dark else '#1f4f82' }}; }
    .notif-type { font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; margin:2px 0 6px; }
    .notif-actions { display:flex; gap:6px; margin-top:6px; }
    .notif-approve { padding:4px 12px; border-radius:6px; font-size:12px; font-weight:600;
        background:#16a34a; color:white; border:none; cursor:pointer; }
    .notif-reject  { padding:4px 12px; border-radius:6px; font-size:12px; font-weight:600;
        background:#ef4444; color:white; border:none; cursor:pointer; }
    /* Language dropdown */
    .lang-dropdown {
        display:none; position:absolute; top:46px; right:0; width:130px;
        background:{{ '#1d1d1f' if dark else 'white' }};
        border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
        border-radius:12px; box-shadow:0 12px 32px rgba(0,0,0,0.18);
        z-index:400; overflow:hidden; padding:4px;
    }
    .lang-dropdown.open { display:block; }
    .lang-option {
        display:block; padding:9px 14px; border-radius:8px; font-size:13px;
        font-weight:600; color:{{ '#e2e8f0' if dark else '#1e293b' }};
        text-decoration:none; transition:background 0.1s;
    }
    .lang-option:hover { background:{{ '#2c2c30' if dark else '#f1f5f9' }}; }
    .lang-option.curr  { background:{{ 'rgba(59,130,246,0.18)' if dark else '#dbeafe' }};
        color:{{ '#93c5fd' if dark else '#1d4ed8' }}; }
    /* Search overlay */
    .search-overlay {
        display:none; position:fixed; inset:0; z-index:500;
        background:rgba(0,0,0,0.5); backdrop-filter:blur(4px);
        align-items:flex-start; justify-content:center; padding-top:80px;
    }
    .search-overlay.open { display:flex; }
    .search-box {
        background:{{ '#1d1d1f' if dark else 'white' }};
        border-radius:16px; width:100%; max-width:560px; margin:0 16px;
        box-shadow:0 24px 64px rgba(0,0,0,0.35); overflow:hidden;
    }
    .search-input-row {
        display:flex; align-items:center; padding:14px 18px; gap:12px;
        border-bottom:1px solid {{ '#2c2c30' if dark else '#f1f5f9' }};
    }
    .search-input-row input {
        flex:1; border:none; background:transparent; font-size:17px;
        color:{{ '#e2e8f0' if dark else '#1e293b' }}; outline:none;
    }
    .search-input-row input::placeholder { color:{{ '#94a3b8' if dark else '#6b7280' }}; }
    .search-results { max-height:380px; overflow-y:auto; padding:6px; }
    .search-result-item {
        display:flex; align-items:center; gap:10px; padding:10px 14px;
        border-radius:10px; cursor:pointer; text-decoration:none;
        color:{{ '#e2e8f0' if dark else '#1e293b' }}; font-size:14px;
    }
    .search-result-item:hover { background:{{ '#2c2c30' if dark else '#f1f5f9' }}; }
    .search-result-cat { font-size:10px; color:{{ '#94a3b8' if dark else '#64748b' }};
        font-weight:600; padding:6px 14px 2px; text-transform:uppercase; letter-spacing:0.05em; }
    /* ── Narrow icon sidebar (Agendrix style) ── */
    .sidebar {
        display:none; position:fixed; left:0; top:0; bottom:0; width:66px;
        background:{{ '#111113' if dark else '#f8fafc' }};
        border-right:1px solid {{ '#1d1d1f' if dark else '#e2e8f0' }};
        z-index:300; flex-direction:column; overflow-y:auto; overflow-x:hidden;
    }
    .sidebar-logo {
        padding:14px 0 10px; display:flex; flex-direction:column; align-items:center;
        border-bottom:1px solid {{ '#1d1d1f' if dark else '#e2e8f0' }};
        text-decoration:none; flex-shrink:0;
    }
    .sidebar-logo img {
        height:30px;
        {% if dark %}filter:invert(1) hue-rotate(180deg);{% else %}mix-blend-mode:multiply;{% endif %}
    }
    .sidebar-logo-title { display:none; }
    .sidebar-nav { flex:1; padding:6px 4px; overflow-y:auto; overflow-x:hidden; }
    .sidebar-section-label { display:none; }
    .sidebar-link {
        display:flex; flex-direction:column; align-items:center; justify-content:center;
        gap:3px; padding:10px 4px; border-radius:10px; margin:2px 0;
        color:{{ '#94a3b8' if dark else '#64748b' }};
        text-decoration:none; font-size:9.5px; font-weight:500; line-height:1.2;
        width:100%; box-sizing:border-box; border:none; background:none; cursor:pointer;
        text-align:center;
        border-bottom:1px solid {{ 'rgba(255,255,255,0.05)' if dark else 'rgba(0,0,0,0.04)' }};
    }
    .sidebar-link:hover {
        background:{{ '#1d1d1f' if dark else '#e9f0f8' }};
        color:{{ '#93c5fd' if dark else '#1f4f82' }};
    }
    .sidebar-link.active {
        background:{{ 'rgba(59,130,246,0.20)' if dark else 'rgba(31,79,130,0.12)' }};
        color:{{ '#93c5fd' if dark else '#1f4f82' }}; font-weight:700;
    }
    .sl-icon { font-size:22px; line-height:1; display:block; }
    .sidebar-divider { height:1px; background:{{ 'rgba(255,255,255,0.10)' if dark else '#e2e8f0' }}; margin:4px 8px; }
    .sidebar-bottom {
        border-top:1px solid {{ '#1d1d1f' if dark else '#e2e8f0' }};
        padding:6px 4px 10px; flex-shrink:0;
    }
    .sidebar-user {
        display:flex; flex-direction:column; align-items:center; padding:6px 4px 2px;
        font-size:8px; color:{{ '#94a3b8' if dark else '#475569' }}; text-align:center;
    }
    .sidebar-user strong {
        display:flex; align-items:center; justify-content:center;
        width:32px; height:32px; border-radius:50%;
        background:{{ '#1e3a5f' if dark else '#1f4f82' }};
        color:white; font-size:13px; font-weight:800; margin-bottom:3px;
    }
    .sidebar-link.danger { color:#ef4444; }
    .sidebar-link.danger:hover { background:rgba(239,68,68,0.10); color:#ef4444; }
    .nav-link { display:block; padding:11px 12px; border-radius:10px; margin:6px 0; background:{{ '#1e1e20' if dark else '#f8fafc' }}; color:{{ '#e5e7eb' if dark else '#1f4f82' }} !important; }
    .nav-link:hover { transform:translateX(2px); box-shadow:0 3px 10px rgba(0,0,0,0.08); }
    .main-content { min-width:0; }
    .hero { padding:22px; border-radius:16px; background:{{ 'linear-gradient(135deg,#161618,#1e1e20)' if dark else 'linear-gradient(135deg,#ffffff,#eaf2fb)' }}; margin-bottom:18px; }
    .hero h1 { margin:0 0 6px 0; }
    .stats-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; margin:14px 0 18px 0; }
    .stat-card { padding:16px; border-radius:14px; background:{{ '#161618' if dark else 'white' }}; box-shadow:0 4px 14px rgba(0,0,0,0.06); border-left:5px solid #1f4f82; border:1px solid transparent; }
    .stat-today { background:{{ '#142638' if dark else '#eaf3ff' }}; border-color:{{ '#274e73' if dark else '#c8def7' }}; border-left-color:#4b8fd8; }
    .stat-workers { background:{{ '#142b29' if dark else '#e8f7f1' }}; border-color:{{ '#24554c' if dark else '#c4eadb' }}; border-left-color:#37a47d; }
    .stat-clients { background:{{ '#292238' if dark else '#f1edff' }}; border-color:{{ '#51426d' if dark else '#d9cdf9' }}; border-left-color:#8d75cf; }
    .stat-hours { background:{{ '#30251d' if dark else '#fff3df' }}; border-color:{{ '#66503a' if dark else '#f2d5a5' }}; border-left-color:#d89a41; }
    .dashboard-panel { border:1px solid transparent; }
    .panel-worker { background:{{ '#132536' if dark else '#edf5ff' }} !important; border-color:{{ '#294b69' if dark else '#d1e3f8' }}; }
    .panel-client { background:{{ '#132927' if dark else '#edf9f4' }} !important; border-color:{{ '#2a534c' if dark else '#cfece1' }}; }
    .panel-shift { background:{{ '#252136' if dark else '#f3f0ff' }} !important; border-color:{{ '#494266' if dark else '#ddd5f7' }}; }
    .panel-absence { background:{{ '#321f29' if dark else '#fff1f6' }} !important; border-color:{{ '#684253' if dark else '#f4d2df' }}; }
    .panel-week-hours { background:{{ '#172939' if dark else '#edf6fd' }} !important; border-color:{{ '#33516b' if dark else '#d1e4f2' }}; }
    .panel-month-hours { background:{{ '#1a2c26' if dark else '#eff8ee' }} !important; border-color:{{ '#355a4a' if dark else '#d4ead0' }}; }
    .panel-absence-summary { background:{{ '#2d271d' if dark else '#fff7e9' }} !important; border-color:{{ '#635238' if dark else '#f0dfbe' }}; }
    .stat-number { font-size:26px; font-weight:800; margin-top:6px; }
    .section-title { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:22px 0 12px; }
    .big-map-button { display:inline-block; padding:16px 26px; border-radius:14px; background:#16a34a; color:white !important; font-size:18px; font-weight:800; text-decoration:none; box-shadow:0 6px 18px rgba(0,0,0,0.18); }
    .back-button { display:inline-flex; align-items:center; gap:7px; width:auto; padding:10px 14px; border-radius:999px; background:{{ '#e5e7eb' if dark else '#111827' }}; color:{{ '#161618' if dark else 'white' }} !important; box-shadow:0 4px 12px rgba(0,0,0,0.14); margin-right:14px; }
    .back-button::before { content:'<'; font-weight:900; }
    .alert-backdrop { display:none; position:fixed; inset:0; z-index:100; background:rgba(15,23,42,0.55); align-items:center; justify-content:center; padding:20px; }
    .alert-dialog { width:min(460px, 94vw); background:{{ '#161618' if dark else 'white' }}; color:{{ '#e5e7eb' if dark else '#111827' }}; border-radius:16px; padding:22px; box-shadow:0 20px 50px rgba(0,0,0,0.35); border-top:6px solid #f59e0b; }
    .alert-dialog h3 { margin:0 0 10px 0; }
    .alert-dialog p { margin:0 0 18px 0; line-height:1.45; }
    .alert-dialog button { width:auto; min-width:110px; float:right; }
    /* Touch-friendly globals */
    a, button, .nav-link, .mini-link, .action-link, .back-button { touch-action:manipulation; -webkit-tap-highlight-color:transparent; }
    button { touch-action:manipulation; }

    /* Bottom navigation (mobile only) */
    .bottom-nav { display:none; position:fixed; bottom:0; left:0; right:0; background:{{ '#161618' if dark else 'white' }}; border-top:1px solid {{ '#2c2c30' if dark else '#e5e7eb' }}; z-index:200; padding:0 0 env(safe-area-inset-bottom,0); box-shadow:0 -3px 16px rgba(0,0,0,0.12); }
    .bottom-nav-item { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:7px 4px 6px; color:{{ '#94a3b8' if dark else '#64748b' }}; text-decoration:none; font-size:10px; min-height:54px; font-weight:500; border:none; background:none; cursor:pointer; }
    .bottom-nav-item span { font-size:22px; line-height:1.1; }
    .bottom-nav-item small { margin-top:2px; font-size:10px; white-space:nowrap; }
    .bottom-nav-item.active { color:{{ '#93c5fd' if dark else '#1f4f82' }}; }

    /* Settings bottom sheet */
    .settings-sheet { display:none; position:fixed; inset:0; z-index:500; background:rgba(0,0,0,0.55); align-items:flex-end; }
    .settings-sheet.open { display:flex; }
    .settings-inner { background:{{ '#1d1d1f' if dark else 'white' }}; border-radius:20px 20px 0 0; width:100%; max-height:82vh; overflow-y:auto; padding-bottom:calc(20px + env(safe-area-inset-bottom,0)); }
    .settings-handle { width:40px; height:4px; border-radius:2px; background:{{ '#6b7280' if dark else '#d1d5db' }}; margin:14px auto 18px; }
    .settings-section { padding:0 20px 14px; margin-bottom:4px; }
    .settings-section + .settings-section { border-top:1px solid {{ '#2c2c30' if dark else '#f1f5f9' }}; padding-top:14px; }
    .settings-section h4 { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.07em; color:{{ '#94a3b8' if dark else '#64748b' }}; margin:0 0 12px; }
    .settings-pills { display:flex; gap:8px; flex-wrap:wrap; }
    .settings-pill { display:inline-flex; align-items:center; gap:5px; padding:7px 14px; border-radius:20px; font-size:13px; font-weight:600; text-decoration:none; border:2px solid {{ '#2c2c30' if dark else '#e5e7eb' }}; color:{{ '#d1d5db' if dark else '#374151' }}; background:{{ '#161618' if dark else '#f8fafc' }}; }
    .settings-pill.current { border-color:{{ '#3b82f6' if dark else '#1f4f82' }}; color:{{ '#93c5fd' if dark else '#1f4f82' }}; background:{{ 'rgba(59,130,246,0.15)' if dark else 'rgba(31,79,130,0.08)' }}; }
    .settings-navlink { display:flex; align-items:center; gap:14px; padding:13px 0; font-size:15px; font-weight:500; color:{{ '#d1d5db' if dark else '#1f2937' }}; text-decoration:none; border-bottom:1px solid {{ '#1e1e20' if dark else '#f3f4f6' }}; }
    .settings-navlink:last-child { border-bottom:none; }
    .settings-navlink-icon { font-size:22px; width:34px; text-align:center; flex-shrink:0; }
    .settings-navlink.danger { color:#ef4444; }

    /* Tablet 601–1024px */
    @media (min-width:601px) and (max-width:1024px) {
        .nav-link { padding:13px 12px; min-height:44px; display:flex; align-items:center; }
        .mini-link { padding:6px 10px; min-height:32px; font-size:12px; }
        .day-menu-wrapper button { min-width:34px; min-height:34px; padding:2px 6px !important; }
    }

    /* Phone ≤600px */
    @media (max-width:600px) {
        body { margin:0; padding-bottom:calc(64px + env(safe-area-inset-bottom,0)); }

        /* Brandbar scrolls away on mobile — day-name bar sticks instead */
        .brandbar { position:relative; border-radius:0 !important; margin:0; padding:8px 12px; }
        .brandleft img { height:34px; }
        .brandtitle { font-size:16px; }
        .langbar { display:none !important; }
        .theme-links { display:none !important; }
        .theme-links { font-size:12px; margin-top:4px !important; }
        .theme-links a { margin-right:6px; font-size:12px; }

        /* Sidebar hidden on mobile — bottom nav replaces it */
        .sidebar { display:none !important; }
        .main-content { padding:8px; }

        /* Bottom nav visible */
        .bottom-nav { display:flex !important; }

        /* Grid single column */
        .grid { grid-template-columns:1fr !important; }
        .stats-grid { grid-template-columns:repeat(2,1fr) !important; gap:8px !important; }

        /* Inputs — 16px stops iOS auto-zoom */
        input, select, textarea { font-size:16px !important; padding:12px !important; }
        button { padding:13px !important; font-size:15px !important; }

        /* Cards */
        .card { padding:12px; border-radius:10px; }
        .hero { padding:14px; }
        .stat-number { font-size:22px; }

        /* Mini links bigger touch zones */
        .mini-link { padding:8px 11px !important; font-size:13px !important; min-height:36px !important; margin:3px 2px !important; }
        .mini-shift { padding:10px 8px; }

        /* + day menu button */
        .day-menu-wrapper button { font-size:22px !important; padding:4px 8px !important; min-width:36px; min-height:36px; color:{{ '#4ade80' if dark else '#1f4f82' }} !important; }
        .day-mini-menu a { padding:13px 16px !important; font-size:14px !important; }

        /* Week calendar — full-width single-day snap scroll */
        .week-calendar-grid { flex-wrap:nowrap !important; overflow-x:scroll !important; -webkit-overflow-scrolling:touch; scroll-snap-type:x mandatory; scrollbar-width:none; gap:0 !important; padding:4px !important; }
        .week-calendar-grid::-webkit-scrollbar { display:none; }
        .week-calendar-grid .calendar-day-card { scroll-snap-align:start; flex:0 0 100% !important; min-width:100% !important; width:100% !important; box-sizing:border-box; }

        /* Month calendar — compact 7-col */
        .month-grid { gap:2px !important; }
        .month-weekday { font-size:9px !important; padding:4px 2px !important; }
        .month-grid .calendar-day-card { min-height:52px !important; padding:3px !important; font-size:9px; overflow:hidden; box-sizing:border-box; }
        .month-grid .calendar-day-card > div { font-size:10px; margin-bottom:2px; }
        /* Smjene: ime radnika / klijent / vrijeme – bez prelijevanja */
        .month-grid .mini-shift {
            overflow:hidden !important;
            max-width:100% !important;
            box-sizing:border-box !important;
            font-size:7px !important;
            padding:2px 3px !important;
            margin-top:3px !important;
            line-height:1.35 !important;
        }
        .month-grid .mini-shift .ms-w,
        .month-grid .mini-shift .ms-c,
        .month-grid .mini-shift .ms-t {
            display:block !important;
            overflow:hidden !important;
            white-space:nowrap !important;
            text-overflow:ellipsis !important;
            max-width:100% !important;
            width:100% !important;
            box-sizing:border-box !important;
        }
        .month-grid .mini-shift .ms-w { font-weight:bold !important; font-size:8px !important; }
        .month-grid .mini-shift .ms-c { font-size:6px !important; }
        .month-grid .mini-shift .ms-city { font-size:5px !important; opacity:0.7; }
        .month-grid .mini-shift .ms-t { opacity:0.75; font-size:6px !important; }
        .month-grid .mini-link { display:none !important; }
        .month-grid .day-menu-wrapper { top:2px !important; right:2px !important; }
        .month-grid .day-menu-wrapper button { font-size:14px !important; padding:1px 3px !important; min-width:22px; min-height:22px; }

        /* Modals — sheet from bottom */
        .modal-backdrop { align-items:flex-end !important; }
        .modal-card { margin:0 !important; border-radius:20px 20px 0 0 !important; max-width:100% !important; width:100% !important; max-height:85vh !important; overflow-y:auto; }

        /* Back button */
        .back-button { padding:9px 12px; font-size:13px; }

        /* Section title */
        .section-title { margin:14px 0 8px; }

        /* Nav links in sidebar (hidden anyway but safe) */
        .nav-link { min-height:48px; font-size:15px; }
    }

    @media (min-width:601px) {
        body { margin-left:66px; margin-top:52px; }
        .sidebar { display:flex !important; }
        .brandbar { display:none !important; }
        .topbar  { display:flex !important; }
    }
    @media (min-width:601px) and (max-width:900px) { body { margin:64px 12px 12px 78px; } }

    /* Telefon vodoravno (landscape) — sedmični kalendar puni širinu */
    @media (orientation:landscape) and (max-height:520px) {
        body { padding-bottom:0 !important; margin:4px !important; }
        .brandbar { padding:4px 10px !important; border-radius:0 !important; position:relative !important; }
        .brandleft img { height:26px !important; }
        .brandtitle { font-size:13px !important; }
        .muted { font-size:11px !important; }
        .sidebar { display:none !important; }
        .bottom-nav { display:none !important; }
        /* Svih 7 dana u jednom redu, pune širine */
        .week-calendar-grid {
            flex-wrap:nowrap !important;
            overflow-x:visible !important;
            scroll-snap-type:none !important;
            gap:3px !important;
        }
        .week-calendar-grid .calendar-day-card {
            flex:1 1 0 !important;
            min-width:0 !important;
            width:auto !important;
            min-height:70px !important;
            padding:4px !important;
        }
        .week-day-heading { font-size:9px !important; }
        .mini-shift { font-size:9px !important; padding:3px 4px !important; margin-top:3px !important; }
        .mini-link { font-size:10px !important; padding:3px 5px !important; min-height:24px !important; }
        .day-menu-wrapper button { font-size:16px !important; min-width:26px !important; min-height:26px !important; }
        .month-grid { gap:1px !important; }
        .month-grid .calendar-day-card { min-height:36px !important; padding:2px !important; }
    }

    /* ══════════════════════════════════════════════════════════
       WORKER APP MODE  (body.wapp — applied via JS for non-admin)
    ══════════════════════════════════════════════════════════ */
    body.wapp {
        margin: 0 !important;
        padding-top: 78px !important;
        padding-bottom: 86px !important;
        background: {{ '#111113' if dark else '#f1f5f9' }} !important;
    }
    /* Hide all standard admin chrome */
    body.wapp .brandbar,
    body.wapp .topbar,
    body.wapp .sidebar,
    body.wapp .bottom-nav { display:none !important; }
    /* Content layout tweaks for workers */
    body.wapp h1 { font-size:20px; }
    body.wapp .hero { padding:16px; border-radius:18px; margin:0 0 12px; }
    body.wapp .card { border-radius:16px; margin-bottom:12px; }
    body.wapp .grid  { grid-template-columns:1fr !important; gap:12px; }
    body.wapp .stats-grid { grid-template-columns:repeat(2,1fr) !important; }
    body.wapp .back-button { display:none !important; }  /* workers use nav tabs */
    body.wapp .week-link, body.wapp .pdf-link { display:none !important; }

    html.wapp-root { overscroll-behavior-x:none; }

    /* ── Worker floating header — transparent bar ───────────── */
    .wapp-hdr {
        display:none; position:fixed; top:12px; left:0; right:0; min-height:52px;
        z-index:199;
        background:transparent;
        align-items:flex-start; justify-content:space-between;
        padding:0 14px;
        pointer-events:none;
    }
    body.wapp .wapp-hdr { display:flex; }
    body.wapp .wapp-hdr > * { pointer-events:auto; }
    /* Clear glass pill ONLY behind title */
    .wapp-pill {
        display:flex; flex-direction:column; justify-content:center;
        min-height:42px;
        padding:6px 14px; border-radius:22px;
        background:{{ 'rgba(255,255,255,0.10)' if dark else 'rgba(255,255,255,0.48)' }};
        backdrop-filter:none;
        -webkit-backdrop-filter:none;
        border:1px solid {{ 'rgba(255,255,255,0.22)' if dark else 'rgba(255,255,255,0.78)' }};
        box-shadow:0 8px 24px rgba(0,0,0,.12), inset 0 1px 0 rgba(255,255,255,.28);
    }
    .wapp-page-title { font-size:17px; font-weight:800; color:{{ '#e2e8f0' if dark else '#1e293b' }}; line-height:1.1; }
    .wapp-page-sub   { font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; margin-top:1px; }
    .wapp-menu-wrap { position:relative; }
    /* Clear glass circle ONLY around menu */
    .wapp-menu-btn {
        width:42px; height:42px; border-radius:50%; flex-shrink:0;
        background:{{ 'rgba(255,255,255,0.10)' if dark else 'rgba(255,255,255,0.48)' }};
        backdrop-filter:none;
        -webkit-backdrop-filter:none;
        border:1px solid {{ 'rgba(255,255,255,0.22)' if dark else 'rgba(255,255,255,0.78)' }};
        box-shadow:0 8px 24px rgba(0,0,0,.12), inset 0 1px 0 rgba(255,255,255,.28);
        color:{{ '#e2e8f0' if dark else '#1e293b' }};
        display:flex; align-items:center; justify-content:center;
        font-size:21px; cursor:pointer;
        text-decoration:none; padding:0;
        touch-action:manipulation;
    }
    .wapp-menu-panel {
        display:none; position:absolute; right:0; top:50px;
        width:min(286px, calc(100vw - 28px));
        padding:8px;
        border-radius:22px;
        background:{{ 'rgba(22,22,24,0.92)' if dark else 'rgba(255,255,255,0.92)' }};
        border:1px solid {{ 'rgba(255,255,255,0.14)' if dark else 'rgba(226,232,240,0.95)' }};
        box-shadow:0 18px 46px rgba(0,0,0,.24), inset 0 1px 0 rgba(255,255,255,.22);
    }
    .wapp-menu-panel.open { display:block; }
    .wapp-menu-panel a {
        display:flex; align-items:center; gap:11px;
        padding:13px 12px; border-radius:16px;
        color:{{ '#e5e7eb' if dark else '#1e293b' }};
        text-decoration:none; font-size:16px; font-weight:800;
    }
    .wapp-menu-panel a:hover { background:{{ '#1e1e20' if dark else '#f1f5f9' }}; }
    .wapp-menu-panel span { width:34px; flex-shrink:0; text-align:center; font-size:13px; font-weight:900; }
    .wapp-menu-panel small { display:block; margin-top:2px; font-size:11px; font-weight:700; color:{{ '#94a3b8' if dark else '#64748b' }}; }
    .wapp-leave-sheet {
        display:none; position:fixed; inset:0; z-index:520;
        background:rgba(0,0,0,.42);
        align-items:flex-end;
    }
    .wapp-leave-sheet.open { display:flex; }
    .wapp-leave-inner {
        width:100%; max-height:86vh; overflow-y:auto;
        border-radius:26px 26px 0 0;
        padding:18px 18px calc(22px + env(safe-area-inset-bottom,0px));
        background:{{ '#161618' if dark else 'rgba(255,255,255,.96)' }};
        border-top:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
        box-shadow:0 -18px 50px rgba(0,0,0,.22);
    }
    .wapp-leave-inner h3 { margin:0 0 14px; }
    .wapp-leave-close { float:right; width:36px; height:36px; border-radius:50%; border:none; background:{{ '#1e1e20' if dark else '#f1f5f9' }}; color:inherit; font-weight:900; }
    .wapp-leave-inner input, .wapp-leave-inner select { width:100%; }

    /* ── Worker bottom nav — transparent, frosted bubbles ────── */
    .wapp-nav {
        display:none; position:fixed; bottom:0; left:0; right:0;
        height:calc(74px + env(safe-area-inset-bottom,0px));
        z-index:199;
        background:transparent;
        align-items:flex-start; justify-content:space-between;
        gap:6px;
        padding:8px 10px env(safe-area-inset-bottom,0px);
    }
    body.wapp .wapp-nav { display:flex; }
    .wapp-btn {
        display:flex; flex-direction:column; align-items:center; justify-content:flex-start;
        text-decoration:none; color:{{ '#94a3b8' if dark else '#64748b' }};
        flex:1 1 0; min-width:0; height:58px;
        border:none; background:transparent; cursor:pointer; padding:0; margin:0;
        font-family:inherit; line-height:1; appearance:none; -webkit-appearance:none;
        touch-action:manipulation;
    }
    /* Thick curved lens bubble ONLY around icon + label */
    .wapp-bubble {
        position:relative; overflow:hidden;
        display:flex; flex-direction:column; align-items:center; justify-content:center;
        gap:3px; padding:8px 9px 7px; border-radius:24px; width:70px; max-width:100%;
        min-height:56px; box-sizing:border-box;
        background:{{ 'linear-gradient(145deg,rgba(255,255,255,.18),rgba(255,255,255,.07))' if dark else 'linear-gradient(145deg,rgba(255,255,255,.82),rgba(255,255,255,.46))' }};
        backdrop-filter:saturate(185%) contrast(1.12) brightness(1.06) blur(.8px);
        -webkit-backdrop-filter:saturate(185%) contrast(1.12) brightness(1.06) blur(.8px);
        border:2px solid {{ 'rgba(255,255,255,0.25)' if dark else 'rgba(255,255,255,0.86)' }};
        box-shadow:
            0 14px 34px rgba(0,0,0,.16),
            inset 0 1px 0 rgba(255,255,255,.62),
            inset 0 -14px 22px {{ 'rgba(0,0,0,.22)' if dark else 'rgba(148,163,184,.22)' }},
            inset 10px 0 24px rgba(255,255,255,.12);
        transition:background .15s, border-color .15s, box-shadow .15s, transform .15s;
    }
    .wapp-bubble::before {
        content:""; position:absolute; inset:2px 3px auto 3px; height:52%;
        border-radius:22px 22px 18px 18px;
        background:linear-gradient(160deg,rgba(255,255,255,.58),rgba(255,255,255,.10) 58%,transparent);
        pointer-events:none;
    }
    .wapp-bubble::after {
        content:""; position:absolute; left:12%; right:12%; bottom:5px; height:12px;
        border-radius:999px;
        background:radial-gradient(ellipse at center,rgba(255,255,255,.30),transparent 70%);
        pointer-events:none;
    }
    .wapp-btn.wactive .wapp-bubble {
        background:{{ 'linear-gradient(145deg,rgba(37,99,235,.35),rgba(37,99,235,.16))' if dark else 'linear-gradient(145deg,rgba(219,234,254,.90),rgba(191,219,254,.58))' }};
        border-color:rgba(37,99,235,0.54);
        box-shadow:
            0 16px 38px rgba(37,99,235,.28),
            inset 0 1px 0 rgba(255,255,255,.70),
            inset 0 -14px 24px rgba(37,99,235,.18);
        transform:translateY(-2px) scale(1.03);
    }
    .wapp-btn.wactive { color:#2563eb; }
    .wapp-bubble .wb-icon  { position:relative; z-index:1; font-size:22px; line-height:1; display:block; }
    .wapp-bubble .wb-label { position:relative; z-index:1; font-size:9.5px; font-weight:800; white-space:nowrap; max-width:100%; overflow:hidden; text-overflow:ellipsis; }
    body.wapp .page-content {
        max-width:520px;
        margin:0 auto;
        padding:0 16px;
    }
    .wapp-home { display:flex; flex-direction:column; gap:12px; }
    .wapp-shift-hero {
        border-radius:24px;
        padding:22px 20px;
        color:white;
        background:linear-gradient(140deg,#2563eb 0%,#1d4ed8 100%);
        box-shadow:0 12px 32px rgba(37,99,235,.34);
        overflow:hidden;
    }
    .wapp-kicker { font-size:11px; font-weight:800; opacity:.78; text-transform:uppercase; letter-spacing:.08em; }
    .wapp-shift-time { font-size:30px; font-weight:900; margin:6px 0 8px; line-height:1.05; }
    .wapp-chip { display:inline-flex; align-items:center; gap:6px; padding:4px 11px; border-radius:999px; background:rgba(255,255,255,.18); font-size:12px; font-weight:800; }
    .wapp-chip::before { content:''; width:7px; height:7px; border-radius:50%; background:var(--chip-dot,#4ade80); }
    .wapp-hero-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:16px; }
    .wapp-hero-stat { border-radius:16px; padding:12px; background:rgba(255,255,255,.14); min-width:0; }
    .wapp-hero-val { font-size:23px; font-weight:900; line-height:1.1; }
    .wapp-hero-sub { font-size:11px; opacity:.78; margin-top:4px; }
    .wapp-sec { font-size:11px; font-weight:900; text-transform:uppercase; letter-spacing:.08em; color:{{ '#94a3b8' if dark else '#64748b' }}; margin:16px 0 4px; }
    .wapp-list-card {
        border-radius:20px;
        background:{{ '#161618' if dark else 'white' }};
        border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
        box-shadow:0 4px 14px rgba(0,0,0,.07);
        overflow:hidden;
    }
    .wapp-shift-row { display:grid; grid-template-columns:48px minmax(0, 1fr) auto; gap:10px; padding:14px 16px; border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; align-items:start; }
    .wapp-shift-row.wroute { grid-template-columns:84px minmax(0, 1fr) 40px; }
    .wapp-shift-row:last-child { border-bottom:none; }
    .wapp-time { text-align:right; font-weight:900; font-size:13px; padding-top:2px; color:{{ '#e2e8f0' if dark else '#1e293b' }}; white-space:nowrap; }
    .wapp-client { font-weight:850; color:{{ '#e5e7eb' if dark else '#1e293b' }}; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .wapp-address { font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; margin-top:3px; line-height:1.35; min-width:0; overflow:hidden; text-overflow:ellipsis; }
    .wapp-status-badge { display:inline-flex; margin-top:7px; padding:3px 9px; border-radius:999px; background:{{ '#1e1e20' if dark else '#f1f5f9' }}; color:{{ '#94a3b8' if dark else '#64748b' }}; font-size:10px; font-weight:900; box-shadow:0 4px 10px rgba(0,0,0,.10); }
    .wapp-map {
        display:inline-flex; align-items:center; justify-content:center;
        min-width:40px; min-height:36px; border-radius:14px;
        background:#16a34a; color:white !important; text-decoration:none; font-weight:900;
        box-shadow:0 4px 12px rgba(22,163,74,.24);
    }
    .wapp-mini-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    .wapp-mini-card { border-radius:18px; padding:15px; background:{{ '#161618' if dark else 'white' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; box-shadow:0 3px 12px rgba(0,0,0,.06); }
    .wapp-mini-card b { font-size:24px; display:block; color:{{ '#e5e7eb' if dark else '#1e293b' }}; }
    .wapp-mini-card span { font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; font-weight:700; }
    .wapp-form-card { border-radius:20px; padding:16px; background:{{ '#161618' if dark else 'white' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; box-shadow:0 4px 14px rgba(0,0,0,.07); }
    .wapp-form-card h3 { margin-top:0; }
    .wapp-week-shell { display:flex; flex-direction:column; gap:14px; }
    .wapp-month-label {
        display:inline-flex; align-self:flex-start;
        padding:6px 12px; border-radius:999px;
        background:{{ 'rgba(255,255,255,.08)' if dark else 'rgba(255,255,255,.55)' }};
        border:1px solid {{ 'rgba(255,255,255,.16)' if dark else 'rgba(255,255,255,.75)' }};
        color:{{ '#94a3b8' if dark else '#64748b' }};
        box-shadow:0 6px 18px rgba(0,0,0,.08), inset 0 1px 0 rgba(255,255,255,.22);
        font-size:12px; font-weight:900;
    }
    .wapp-date-strip {
        display:flex; gap:10px; overflow-x:auto; overflow-y:hidden;
        padding:2px 2px 10px; margin:0 -2px;
        -webkit-overflow-scrolling:touch; scrollbar-width:none;
        scroll-snap-type:x proximity;
    }
    .wapp-date-strip::-webkit-scrollbar,
    .wapp-day-panel:not(.active) { display:none; }
    .wapp-date-bubble {
        flex:0 0 auto; width:58px; height:58px; border-radius:22px;
        border:1px solid {{ 'rgba(255,255,255,.22)' if dark else 'rgba(255,255,255,.78)' }};
        background:{{ 'rgba(255,255,255,.10)' if dark else 'rgba(255,255,255,.50)' }};
        backdrop-filter:blur(16px) saturate(170%);
        -webkit-backdrop-filter:blur(16px) saturate(170%);
        color:{{ '#e5e7eb' if dark else '#1e293b' }};
        box-shadow:0 8px 24px rgba(0,0,0,.10), inset 0 1px 0 rgba(255,255,255,.24);
        font-size:21px; font-weight:900; cursor:pointer;
        scroll-snap-align:center;
    }
    .wapp-date-bubble.active {
        background:rgba(37,99,235,.22);
        border-color:rgba(37,99,235,.42);
        color:{{ '#bfdbfe' if dark else '#1d4ed8' }};
    }
    .wapp-week-shifts {
        display:flex; flex-direction:column; gap:12px;
        padding:2px 0 12px;
    }
    .wapp-week-card {
        border-radius:22px; padding:16px;
        background:{{ '#161618' if dark else 'white' }};
        border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
        border-left:7px solid var(--worker-color, #2563eb);
        box-shadow:0 6px 18px rgba(0,0,0,.08);
    }
    .wapp-week-time { font-size:24px; font-weight:900; color:{{ '#e5e7eb' if dark else '#1e293b' }}; }
    .wapp-week-client { margin-top:10px; font-size:17px; font-weight:900; color:{{ '#e5e7eb' if dark else '#1e293b' }}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .wapp-week-worker { margin-top:6px; font-size:12px; color:{{ '#94a3b8' if dark else '#64748b' }}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .wapp-empty-day { border-radius:22px; padding:22px; text-align:center; background:{{ '#161618' if dark else 'white' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; color:{{ '#94a3b8' if dark else '#64748b' }}; font-weight:800; }
    body.wapp input, body.wapp select, body.wapp button,
    body.wapp .settings-sheet input, body.wapp .settings-sheet select,
    body.wapp .settings-sheet button, body.wapp .settings-pill,
    body.wapp .settings-navlink { font-size:16px; }
    @media (max-width:420px) {
        body.wapp .page-content { padding:0 16px; }
        .wapp-shift-time { font-size:28px; }
        .wapp-shift-row { grid-template-columns:44px 1fr auto; padding:13px 14px; }
        .wapp-shift-row.wroute { grid-template-columns:78px minmax(0, 1fr) 38px; }
        .wapp-bubble { width:60px; padding-left:7px; padding-right:7px; }
        .wapp-bubble .wb-label { font-size:9px; }
    }

    /* ── Worker archive ──────────────────────────────────────── */
    .wapp-archive-year {
        font-size:13px; font-weight:900; color:{{ '#94a3b8' if dark else '#64748b' }};
        margin:8px 0 4px; padding:0 4px;
    }
    .wapp-archive-wrap { position:relative; margin-bottom:10px; }
    .wapp-archive-card {
        border-radius:20px;
        background:{{ '#161618' if dark else 'white' }};
        border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
        box-shadow:0 4px 14px rgba(0,0,0,.07);
        overflow:hidden;
    }
    .wapp-archive-card summary {
        display:flex; align-items:center;
        padding:14px 16px; padding-right:80px;
        cursor:pointer; list-style:none; user-select:none;
        font-weight:800; font-size:15px;
        color:{{ '#e5e7eb' if dark else '#1e293b' }};
        gap:8px;
    }
    .wapp-archive-card summary::-webkit-details-marker { display:none; }
    .wapp-archive-card summary::after {
        content:"›"; font-size:20px; font-weight:900; margin-left:auto;
        color:{{ '#94a3b8' if dark else '#64748b' }};
        transition:transform .2s; flex-shrink:0;
    }
    .wapp-archive-card[open] summary::after { transform:rotate(90deg); }
    .wapp-archive-count { font-size:12px; font-weight:700; color:{{ '#94a3b8' if dark else '#64748b' }}; flex-shrink:0; }
    .wapp-archive-pdf {
        position:absolute; top:14px; right:42px; transform:none;
        font-size:11px; font-weight:900; padding:4px 10px; border-radius:999px;
        background:{{ '#1e1e20' if dark else '#f1f5f9' }};
        color:{{ '#93c5fd' if dark else '#2563eb' }};
        text-decoration:none; white-space:nowrap;
        border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
        z-index:2;
    }
    .wapp-archive-body { border-top:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
</style>
<script>
(function(){
    var scrollKey = "luxmann_scroll_y";
    var restoreKey = "luxmann_restore_scroll";
    var shouldRestore = false;
    var restoreY = 0;
    try {
        if ("scrollRestoration" in history) {
            history.scrollRestoration = "manual";
        }
        shouldRestore = sessionStorage.getItem(restoreKey) === "1";
        restoreY = parseInt(sessionStorage.getItem(scrollKey) || "0", 10) || 0;
        if(shouldRestore){
            window.scrollTo(0, restoreY);
        }
    } catch(e) {}
    function rememberScroll(){
        try {
            sessionStorage.setItem(scrollKey, String(window.scrollY || window.pageYOffset || 0));
            sessionStorage.setItem(restoreKey, "1");
        } catch(e) {}
    }
    window.showPlannerAlert = function(message){
        var backdrop = document.getElementById("plannerAlertBackdrop");
        var text = document.getElementById("plannerAlertText");
        if(!backdrop || !text){ alert(message); return; }
        text.textContent = message || "";
        backdrop.style.display = "flex";
        var btn = document.getElementById("plannerAlertOk");
        if(btn){ btn.focus(); }
    };
    window.closePlannerAlert = function(){
        var backdrop = document.getElementById("plannerAlertBackdrop");
        if(backdrop){ backdrop.style.display = "none"; }
    };
    function restoreScroll(){
        if(!shouldRestore){ return; }
        try {
            window.scrollTo(0, restoreY);
            requestAnimationFrame(function(){
                window.scrollTo(0, restoreY);
                sessionStorage.removeItem(restoreKey);
            });
            window.setTimeout(function(){
                window.scrollTo(0, restoreY);
                sessionStorage.removeItem(restoreKey);
            }, 40);
        } catch(e) {}
    }
    document.addEventListener("click", function(event){
        var link = event.target.closest ? event.target.closest("a") : null;
        if(!link){ return; }
        var href = link.getAttribute("href") || "";
        if(!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0 || link.target === "_blank"){ return; }
        var keepPositionActions = [
            "/copy_shift/", "/paste_shift/", "/clear_copy", "/delete_shift/",
            "/invoices/mark_paid", "/invoices/mark_sent", "/invoices/delete"
        ];
        var shouldRemember = keepPositionActions.some(function(part){ return href.indexOf(part) === 0; });
        if(!shouldRemember){ return; }
        rememberScroll();
    }, true);
    document.addEventListener("submit", function(){ rememberScroll(); }, true);
    if(document.readyState === "loading"){
        document.addEventListener("DOMContentLoaded", restoreScroll);
    } else {
        restoreScroll();
    }
    document.addEventListener("DOMContentLoaded", function(){
        var params = new URLSearchParams(window.location.search);
        var message = params.get("notice");
        if(message){
            window.showPlannerAlert(message);
            params.delete("notice");
            var cleanUrl = window.location.pathname + (params.toString() ? "?" + params.toString() : "") + window.location.hash;
            window.history.replaceState({}, "", cleanUrl);
        }
    });
})();
window.initClientSearch = function(inputId, hiddenId, listId, data) {
    var input = document.getElementById(inputId);
    var hidden = document.getElementById(hiddenId);
    var list = document.getElementById(listId);
    if(!input || !hidden || !list) return;
    var activeIdx = -1;
    function items() { return list.querySelectorAll('.client-search-item'); }
    function setActive(idx) {
        items().forEach(function(el,i){ el.classList.toggle('active', i===idx); });
        activeIdx = idx;
    }
    function show(q) {
        q = (q||'').toLowerCase().trim();
        var matches = data.filter(function(c){
            return !q || c.name.toLowerCase().indexOf(q)!==-1 || (c.addr && c.addr.toLowerCase().indexOf(q)!==-1);
        });
        list.innerHTML=''; activeIdx=-1;
        if(!matches.length){list.style.display='none';return;}
        matches.slice(0,60).forEach(function(c){
            var d=document.createElement('div');
            d.className='client-search-item';
            d.innerHTML='<strong>'+c.name.replace(/</g,'&lt;')+'</strong>'+(c.addr?'<br><small style="opacity:0.6;">'+c.addr.replace(/</g,'&lt;')+'</small>':'');
            d.addEventListener('mousedown',function(e){e.preventDefault();input.value=c.name;hidden.value=c.name;list.style.display='none';});
            list.appendChild(d);
        });
        list.style.display='block';
    }
    input.addEventListener('input',function(){hidden.value='';show(input.value);});
    input.addEventListener('focus',function(){show(input.value);});
    input.addEventListener('blur',function(){setTimeout(function(){list.style.display='none';},200);});
    input.addEventListener('keydown',function(e){
        var its=items(); var n=its.length;
        if(e.key==='ArrowDown'){e.preventDefault();setActive(Math.min(activeIdx+1,n-1));if(its[activeIdx])its[activeIdx].scrollIntoView({block:'nearest'});}
        else if(e.key==='ArrowUp'){e.preventDefault();setActive(Math.max(activeIdx-1,0));if(its[activeIdx])its[activeIdx].scrollIntoView({block:'nearest'});}
        else if(e.key==='Enter'&&list.style.display!=='none'){e.preventDefault();var sel=activeIdx>=0?its[activeIdx]:its[0];if(sel)sel.dispatchEvent(new MouseEvent('mousedown'));}
        else if(e.key==='Escape'){list.style.display='none';}
    });
};
</script>
"""


def header_html():
    return """
    <!-- ═══ Narrow icon sidebar (Agendrix style, tablet + desktop) ═══ -->
    {% if session.get('user') %}
    <aside class="sidebar">
      <a class="sidebar-logo" href="/" title="Luxmann Planner">
        <img src="{{ url_for('static', filename='logo.png') }}" alt="L">
      </a>
      <nav class="sidebar-nav">
        <a href="/" class="sidebar-link {% if request.path == '/' %}active{% endif %}" title="{{ tr.get('nav_plan','Plan') }}">
          <span class="sl-icon">🏠</span><span>{{ tr.get("nav_plan","Plan") }}</span>
        </a>
        <a href="/week" class="sidebar-link {% if request.path == '/week' %}active{% endif %}" title="{{ tr.get('nav_week','Sedmica') }}">
          <span class="sl-icon">📅</span><span>{{ tr.get("nav_week","Sedmica") }}</span>
        </a>
        {% if session.get('role') == 'admin' %}
        <a href="/month" class="sidebar-link {% if request.path == '/month' %}active{% endif %}" title="{{ tr.get('nav_month','Mjesec') }}">
          <span class="sl-icon">🗓️</span><span>{{ tr.get("nav_month","Mjesec") }}</span>
        </a>
        {% endif %}
        <a href="/documents" class="sidebar-link {% if request.path.startswith('/documents') %}active{% endif %}" title="{{ tr.get('documents','Dokumenti') }}">
          <span class="sl-icon">📁</span><span>{{ tr.get("documents","Dokumenti") }}</span>
        </a>
        {% if session.get('role') == 'admin' %}
        <div class="sidebar-divider"></div>
        <a href="/workers" class="sidebar-link {% if request.path == '/workers' %}active{% endif %}" title="{{ tr.get('workers','Radnici') }}">
          <span class="sl-icon">👷</span><span>{{ tr.get("workers","Radnici") }}</span>
        </a>
        <a href="/clients" class="sidebar-link {% if request.path == '/clients' %}active{% endif %}" title="{{ tr.get('clients','Klijenti') }}">
          <span class="sl-icon">🏢</span><span>{{ tr.get("clients","Klijenti") }}</span>
        </a>
        <a href="/invoices" class="sidebar-link {% if request.path.startswith('/invoices') %}active{% endif %}" title="{{ tr.get('invoices','Fakture') }}">
          <span class="sl-icon">🧾</span><span>{{ tr.get("invoices","Fakture") }}</span>
        </a>
        <a href="/payroll" class="sidebar-link {% if request.path.startswith('/payroll') %}active{% endif %}" title="{{ tr.get('nav_payroll','Plate') }}">
          <span class="sl-icon">💰</span><span>{{ tr.get("nav_payroll","Plate") }}</span>
        </a>
        <a href="/diagram" class="sidebar-link {% if request.path == '/diagram' %}active{% endif %}" title="{{ tr.get('nav_diagram','Dijagram') }}">
          <span class="sl-icon">📊</span><span>{{ tr.get("nav_diagram","Dijagram") }}</span>
        </a>
        <a href="/route_optimizer" class="sidebar-link {% if request.path == '/route_optimizer' %}active{% endif %}" title="{{ tr.get('nav_route','Ruta') }}">
          <span class="sl-icon">🗺️</span><span>{{ tr.get("nav_route","Ruta") }}</span>
        </a>
        <div class="sidebar-divider"></div>
        <a href="/admin" class="sidebar-link {% if request.path == '/admin' %}active{% endif %}" title="Admin">
          <span class="sl-icon">🔧</span><span>Admin</span>
        </a>
        <a href="/backup" class="sidebar-link {% if request.path == '/backup' %}active{% endif %}" title="Backup">
          <span class="sl-icon">💾</span><span>Backup</span>
        </a>
        {% endif %}
      </nav>
      <div class="sidebar-bottom">
        <div class="sidebar-user" title="{{ session['user'] }} ({{ session.get('role','') }})">
          <strong>{{ session['user'][0]|upper }}</strong>
          <span>{{ session['user'][:6] }}</span>
        </div>
        <button onclick="openSettingsSheet()" class="sidebar-link" type="button" title="{{ tr.get('nav_settings','Postavke') }}">
          <svg class="sl-icon" xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
            <path d="M19.43 12.98c.04-.32.07-.64.07-.98s-.03-.66-.07-.98l2.11-1.65c.19-.15.24-.42.12-.64l-2-3.46c-.12-.22-.39-.3-.61-.22l-2.49 1c-.52-.4-1.08-.73-1.69-.98l-.38-2.65C14.46 2.18 14.25 2 14 2h-4c-.25 0-.46.18-.49.42l-.38 2.65c-.61.25-1.17.59-1.69.98l-2.49-1c-.23-.09-.49 0-.61.22l-2 3.46c-.13.22-.07.49.12.64l2.11 1.65c-.04.32-.07.65-.07.98s.03.66.07.98l-2.11 1.65c-.19.15-.24.42-.12.64l2 3.46c.12.22.39.3.61.22l2.49-1c.52.4 1.08.73 1.69.98l.38 2.65c.03.24.24.42.49.42h4c.25 0 .46-.18.49-.42l.38-2.65c.61-.25 1.17-.59 1.69-.98l2.49 1c.23.09.49 0 .61-.22l2-3.46c.12-.22.07-.49-.12-.64l-2.11-1.65zM12 15.5c-1.93 0-3.5-1.57-3.5-3.5s1.57-3.5 3.5-3.5 3.5 1.57 3.5 3.5-1.57 3.5-3.5 3.5z"/>
          </svg>
          <span>{{ tr.get("nav_settings","Postavke") }}</span>
        </button>
        <a href="/logout" class="sidebar-link danger" title="{{ tr['logout'] }}">
          <span class="sl-icon">🚪</span><span>{{ tr["logout"] }}</span>
        </a>
      </div>
    </aside>
    {% endif %}

    {% if session.get('user') and session.get('role') != 'admin' %}
    <script>
    (function(){
      function applyWorkerApp(){ if(document.body){ document.body.classList.add('wapp'); } }
      document.documentElement.classList.add('wapp-root');
      applyWorkerApp();
      if(document.readyState === 'loading'){ document.addEventListener('DOMContentLoaded', applyWorkerApp); }
      var lastTouchEnd = 0;
      document.addEventListener('touchend', function(ev){
        var now = Date.now();
        if(now - lastTouchEnd <= 300){ ev.preventDefault(); }
        lastTouchEnd = now;
      }, {passive:false});
      document.addEventListener('gesturestart', function(ev){ ev.preventDefault(); }, {passive:false});
    })();
    </script>
    <header class="wapp-hdr">
      <div class="wapp-pill">
        <div class="wapp-page-title">
          {% if request.path == '/week' %}{{ tr.get("nav_week","Sedmica") }}
          {% elif request.path == '/route_optimizer' %}{{ tr.get("nav_route","Ruta") }}
          {% else %}{{ tr.get("today_shifts","Danas") }}{% endif %}
        </div>
        <div class="wapp-page-sub">
          {% if request.path == '/week' %}{{ session.get('user') }}
          {% elif request.path == '/route_optimizer' %}Google Maps
          {% else %}{{ tr.get("logged_as","Prijavljen") }}: {{ session.get('user') }}{% endif %}
        </div>
      </div>
      <div class="wapp-menu-wrap">
        <button class="wapp-menu-btn" type="button" onclick="toggleWorkerMenu(event)" aria-expanded="false" aria-controls="wappMenu" aria-label="{{ tr.get('nav_tools','Alati') }}">☰</button>
        <div class="wapp-menu-panel" id="wappMenu">
          <a href="javascript:void(0)" onclick="closeWorkerMenu();openWorkerLeaveSheet();"><span>☀</span><div>{{ tr.get("leave_request","Zahtjev za odsustvo") }}<small>{{ tr.get("leave_send","Posalji zahtjev") }}</small></div></a>
          <a href="/week_pdf" target="_blank" rel="noopener" onclick="closeWorkerMenu()"><span>PDF</span><div>{{ tr.get("week_calendar","Sedmicni kalendar") }}<small>{{ tr.get("download","Preuzmi") }}</small></div></a>
          <a href="/month_pdf" target="_blank" rel="noopener" onclick="closeWorkerMenu()"><span>PDF</span><div>{{ tr.get("month_calendar","Mjesecni kalendar") }}<small>{{ tr.get("download","Preuzmi") }}</small></div></a>
        </div>
      </div>
    </header>
    <nav class="wapp-nav" aria-label="{{ tr.get('nav_navigation','Navigacija') }}">
      <a href="/" class="wapp-btn {% if request.path == '/' %}wactive{% endif %}">
        <span class="wapp-bubble"><span class="wb-icon">🏠</span><span class="wb-label">{{ tr.get("nav_plan","Plan") }}</span></span>
      </a>
      <a href="/week" class="wapp-btn {% if request.path == '/week' %}wactive{% endif %}">
        <span class="wapp-bubble"><span class="wb-icon">📅</span><span class="wb-label">{{ tr.get("nav_week","Sedmica") }}</span></span>
      </a>
      <a href="/route_optimizer" class="wapp-btn {% if request.path == '/route_optimizer' %}wactive{% endif %}">
        <span class="wapp-bubble"><span class="wb-icon">🗺️</span><span class="wb-label">{{ tr.get("nav_route","Ruta") }}</span></span>
      </a>
      <button class="wapp-btn" type="button" onclick="openSettingsSheet()" aria-label="{{ tr.get('nav_settings','Postavke') }}">
        <span class="wapp-bubble"><span class="wb-icon">👤</span><span class="wb-label">{{ tr.get("nav_settings","Postavke") }}</span></span>
      </button>
    </nav>
    <div class="wapp-leave-sheet" id="wappLeaveSheet" onclick="if(event.target===this)closeWorkerLeaveSheet();">
      <div class="wapp-leave-inner">
        <button class="wapp-leave-close" type="button" onclick="closeWorkerLeaveSheet()">×</button>
        <h3>{{ tr.get("leave_request","Zahtjev za odsustvo") }}</h3>
        <form method="post" action="/leave_request">
          <select name="type">
            <option value="vacation">{{ tr["leave_type_vacation"] }}</option>
            <option value="sick">{{ tr["leave_type_sick"] }}</option>
            <option value="other">{{ tr["leave_type_other"] }}</option>
          </select>
          <label>{{ tr["leave_date_from"] }}</label>
          <input type="date" name="date_from" required>
          <label>{{ tr["leave_date_to"] }}</label>
          <input type="date" name="date_to" required>
          <input type="text" name="note" placeholder="{{ tr['leave_note'] }}">
          <button>{{ tr["leave_send"] }}</button>
        </form>
      </div>
    </div>
    <script>
    function toggleWorkerMenu(ev){
      if(ev) ev.stopPropagation();
      var menu = document.getElementById('wappMenu');
      var btn = document.querySelector('.wapp-menu-btn');
      if(!menu) return;
      var open = !menu.classList.contains('open');
      menu.classList.toggle('open', open);
      if(btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    function closeWorkerMenu(){
      var menu = document.getElementById('wappMenu');
      var btn = document.querySelector('.wapp-menu-btn');
      if(menu) menu.classList.remove('open');
      if(btn) btn.setAttribute('aria-expanded', 'false');
    }
    function openWorkerLeaveSheet(){
      var sheet = document.getElementById('wappLeaveSheet');
      if(sheet) sheet.classList.add('open');
    }
    function closeWorkerLeaveSheet(){
      var sheet = document.getElementById('wappLeaveSheet');
      if(sheet) sheet.classList.remove('open');
    }
    document.addEventListener('click', function(ev){
      var wrap = document.querySelector('.wapp-menu-wrap');
      if(wrap && !wrap.contains(ev.target)) closeWorkerMenu();
    });
    document.addEventListener('keydown', function(ev){
      if(ev.key === 'Escape'){ closeWorkerMenu(); closeWorkerLeaveSheet(); }
    });
    </script>
    {% endif %}

    <!-- ═══ Desktop topbar action icons ═══ -->
    {% if session.get('user') %}
    <header class="topbar" id="mainTopbar">
      <!-- Search -->
      <button class="topicon-btn" onclick="openSearch()" title="Pretraga" aria-label="Pretraga">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
      </button>
      <!-- Theme toggle -->
      {% if dark %}
      <a class="topicon-btn" href="/set_theme/light" title="Prebaci na svijetlu temu" aria-label="Tema">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
          <line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
        </svg>
      </a>
      {% else %}
      <a class="topicon-btn" href="/set_theme/dark" title="Prebaci na tamnu temu" aria-label="Tema">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
        </svg>
      </a>
      {% endif %}
      <!-- Language -->
      <div style="position:relative;">
        <button class="topicon-btn topicon-lang" onclick="toggleLangDrop()" title="Jezik / Langue / Language" id="langBtn">
          {{ hdr_lang[:2] }}
        </button>
        <div class="lang-dropdown" id="langDrop">
          <a class="lang-option {{ 'curr' if hdr_lang == 'FR' else '' }}" href="/set_lang/fr">🇫🇷 Français</a>
          <a class="lang-option {{ 'curr' if hdr_lang == 'BOS' else '' }}" href="/set_lang/bos">🇧🇦 Bosanski</a>
          <a class="lang-option {{ 'curr' if hdr_lang == 'EN' else '' }}" href="/set_lang/en">🇬🇧 English</a>
          <a class="lang-option {{ 'curr' if hdr_lang == 'DE' else '' }}" href="/set_lang/de">🇩🇪 Deutsch</a>
          <a class="lang-option {{ 'curr' if hdr_lang == 'PT' else '' }}" href="/set_lang/pt">🇵🇹 Português</a>
        </div>
      </div>
      <!-- Notification bell -->
      <div style="position:relative;">
        <button class="topicon-btn {{ 'active' if hdr_pending_count > 0 else '' }}"
                onclick="toggleNotifDrop()" title="Notifikacije" id="bellBtn"
                style="{{ 'background:#ef4444; border-color:#ef4444; color:white;' if hdr_pending_count > 0 else '' }}">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
            <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
          </svg>
          {% if hdr_pending_count > 0 %}
          <span class="notif-badge">{{ hdr_pending_count }}</span>
          {% endif %}
        </button>
        <!-- Notification dropdown -->
        <div class="notif-dropdown" id="notifDrop">
          <div class="notif-header">
            <span>🔔 Zahtjevi radnika</span>
            <span style="font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }};">{{ hdr_pending_count }} na čekanju</span>
          </div>
          {% if hdr_pending_items %}
            {% for item in hdr_pending_items %}
            <div class="notif-item">
              <div class="notif-worker">👤 {{ item.worker }}</div>
              <div class="notif-type">
                {% if item.type == 'vacation' %}🏖️ Godišnji odmor
                {% elif item.type == 'sick' %}🤒 Bolovanje
                {% elif item.type == 'unpaid' %}📋 Neplaćeni dopust
                {% else %}📝 {{ item.type }}{% endif %}
                · {{ item.dfrom }} → {{ item.dto }}
              </div>
              {% if item.note %}<div style="font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; margin-bottom:4px;">{{ item.note }}</div>{% endif %}
              <div class="notif-actions">
                <form method="post" action="/leave_request/approve/{{ item.id }}" style="display:inline;">
                  <button type="submit" class="notif-approve">✓ Odobri</button>
                </form>
                <form method="post" action="/leave_request/reject/{{ item.id }}" style="display:inline;">
                  <button type="submit" class="notif-reject">✗ Odbij</button>
                </form>
              </div>
            </div>
            {% endfor %}
          {% else %}
            <div style="padding:20px 16px; text-align:center; color:{{ '#94a3b8' if dark else '#64748b' }}; font-size:13px;">
              ✅ Nema zahtjeva na čekanju
            </div>
          {% endif %}
        </div>
      </div>
    </header>

    <!-- Search overlay -->
    <div class="search-overlay" id="searchOverlay" onclick="if(event.target===this)closeSearch()">
      <div class="search-box">
        <div class="search-input-row">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="{{ '#94a3b8' if dark else '#64748b' }}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input type="text" id="searchInput" placeholder="Pretraži radnike, klijente, fakture…"
                 oninput="runSearch(this.value)" autocomplete="off" spellcheck="false">
          <button onclick="closeSearch()" style="background:none;border:none;cursor:pointer;color:{{ '#94a3b8' if dark else '#64748b' }};font-size:18px;padding:0;">✕</button>
        </div>
        <div class="search-results" id="searchResults">
          <div style="padding:16px; text-align:center; color:{{ '#94a3b8' if dark else '#64748b' }}; font-size:13px;">
            Počni tipkati za pretragu…
          </div>
        </div>
      </div>
    </div>
    {% endif %}

    <script>
    // ── Notification dropdown ──
    function toggleNotifDrop() {
      var d = document.getElementById('notifDrop');
      if (!d) return;
      var isOpen = d.classList.contains('open');
      closeAllDropdowns();
      if (!isOpen) d.classList.add('open');
    }
    // ── Language dropdown ──
    function toggleLangDrop() {
      var d = document.getElementById('langDrop');
      if (!d) return;
      var isOpen = d.classList.contains('open');
      closeAllDropdowns();
      if (!isOpen) d.classList.add('open');
    }
    function closeAllDropdowns() {
      document.querySelectorAll('.notif-dropdown,.lang-dropdown').forEach(function(el){ el.classList.remove('open'); });
    }
    document.addEventListener('click', function(e) {
      if (!e.target.closest('#bellBtn') && !e.target.closest('#notifDrop') &&
          !e.target.closest('#langBtn')  && !e.target.closest('#langDrop')) {
        closeAllDropdowns();
      }
    });
    // ── Search ──
    function openSearch() {
      document.getElementById('searchOverlay').classList.add('open');
      setTimeout(function(){ var i=document.getElementById('searchInput'); if(i){i.focus();i.value='';} }, 80);
      document.getElementById('searchResults').innerHTML = '<div style="padding:16px;text-align:center;color:#94a3b8;font-size:13px;">Počni tipkati za pretragu…</div>';
    }
    function closeSearch() { document.getElementById('searchOverlay').classList.remove('open'); }
    document.addEventListener('keydown', function(e){ if(e.key==='Escape'){ closeSearch(); closeAllDropdowns(); } });
    // ── Search logic ──
    var _srTimer = null;
    function runSearch(q) {
      q = q.trim();
      if (q.length < 2) {
        document.getElementById('searchResults').innerHTML = '<div style="padding:16px;text-align:center;color:#94a3b8;font-size:13px;">Unesi najmanje 2 slova…</div>';
        return;
      }
      clearTimeout(_srTimer);
      _srTimer = setTimeout(function(){
        fetch('/api/search?q=' + encodeURIComponent(q))
          .then(function(r){ return r.json(); })
          .then(function(data){ renderSearchResults(data); });
      }, 220);
    }
    function renderSearchResults(data) {
      var html = '';
      var icons   = { shifts:'📅', workers:'👷', clients:'🏢', invoices:'🧾' };
      var labels  = { shifts:{{ tr.get("search_shifts","Smjene")|tojson }}, workers:{{ tr.get("workers","Radnici")|tojson }}, clients:{{ tr.get("clients","Klijenti")|tojson }}, invoices:{{ tr.get("invoices","Fakture")|tojson }} };
      ['shifts','workers','clients','invoices'].forEach(function(cat){
        var items = data[cat] || [];
        if (!items.length) return;
        html += '<div class="search-result-cat">' + labels[cat] + '</div>';
        items.forEach(function(item){
          html += '<a class="search-result-item" href="' + item.url + '" onclick="closeSearch();">'
                + '<span style="font-size:20px;">' + icons[cat] + '</span>'
                + '<div><div style="font-weight:600;">' + escHtml(item.name) + '</div>'
                + (item.sub ? '<div style="font-size:11px;opacity:0.65;">' + escHtml(item.sub) + '</div>' : '')
                + '</div></a>';
        });
      });
      if (!html) html = '<div style="padding:20px;text-align:center;color:#94a3b8;font-size:13px;">' + {{ tr.get("no_results","Nema rezultata.")|tojson }} + '</div>';
      document.getElementById('searchResults').innerHTML = html;
    }
    function escHtml(s){ var d=document.createElement('div'); d.textContent=s||''; return d.innerHTML; }
    </script>

    <!-- Mobile top bar (hidden on tablet+desktop via CSS) -->
    <div class="brandbar">
        <div class="brandleft">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="Luxmann Logo">
            <div>
                <div class="brandtitle">Luxmann Planner</div>
                {% if session.get('user') %}<div class="muted">{{ tr["logged_as"] }}: <b>{{ session['user'] }}</b></div>{% endif %}
            </div>
        </div>
        <div>
            <div class="langbar">
                <a href="/set_lang/fr">FR</a><a href="/set_lang/en">EN</a><a href="/set_lang/bos">BOS</a><a href="/set_lang/de">DE</a><a href="/set_lang/pt">PT</a>
            </div>
            <div class="theme-links" style="text-align:right; margin-top:8px;">
                {{ tr["theme"] }}: <a href="/set_theme/light">{{ tr["light_theme"] }}</a><a href="/set_theme/dark">{{ tr["dark_theme"] }}</a>{% if session.get('user') %}<a href="/logout">{{ tr["logout"] }}</a>{% endif %}
            </div>
        </div>
    </div>
    <div id="plannerAlertBackdrop" class="alert-backdrop" onclick="if(event.target===this)closePlannerAlert();">
        <div class="alert-dialog" role="dialog" aria-modal="true">
            <h3>{{ tr["status"] }}</h3>
            <p id="plannerAlertText"></p>
            <button id="plannerAlertOk" type="button" onclick="closePlannerAlert()">OK</button>
            <div style="clear:both;"></div>
        </div>
    </div>
    <nav class="bottom-nav" aria-label="{{ tr.get('nav_navigation','Navigacija') }}">
        <a href="/" class="bottom-nav-item {% if request.path == '/' %}active{% endif %}">
            <span>🏠</span><small>{{ tr.get("nav_plan","Plan") }}</small>
        </a>
        <a href="/week" class="bottom-nav-item {% if request.path == '/week' %}active{% endif %}">
            <span>📅</span><small>{{ tr.get("nav_week","Sedmica") }}</small>
        </a>
        {% if session.get('role') == 'admin' %}
        <a href="/month" class="bottom-nav-item {% if request.path == '/month' %}active{% endif %}">
            <span>🗓️</span><small>{{ tr.get("nav_month","Mjesec") }}</small>
        </a>
        {% endif %}
        {% if session.get('role') == 'admin' %}
        <a href="/workers" class="bottom-nav-item {% if request.path == '/workers' %}active{% endif %}">
            <span>👥</span><small>{{ tr.get("workers","Radnici") }}</small>
        </a>
        <a href="/documents" class="bottom-nav-item {% if request.path == '/documents' %}active{% endif %}">
            <span>📁</span><small>{{ tr.get("nav_docs_short","Dok.") }}</small>
        </a>
        {% endif %}
        <button class="bottom-nav-item" onclick="openSettingsSheet()" aria-label="{{ tr.get('nav_settings','Postavke') }}">
            <span>⚙️</span><small>{{ tr.get("nav_settings","Postavke") }}</small>
        </button>
    </nav>

    <!-- Settings bottom sheet -->
    <div id="settingsSheet" class="settings-sheet" onclick="if(event.target===this)closeSettingsSheet();" role="dialog" aria-modal="true" aria-label="{{ tr.get('nav_settings','Postavke') }}">
      <div class="settings-inner">
        <div class="settings-handle"></div>

        <!-- Tema -->
        <div class="settings-section">
          <h4>🎨 {{ tr["theme"] }}</h4>
          <div class="settings-pills">
            <a href="/set_theme/light" class="settings-pill {% if not dark %}current{% endif %}">☀️ {{ tr["light_theme"] }}</a>
            <a href="/set_theme/dark"  class="settings-pill {% if dark %}current{% endif %}">🌙 {{ tr["dark_theme"] }}</a>
          </div>
        </div>

        <!-- Jezik -->
        <div class="settings-section">
          <h4>🌐 {{ tr.get("nav_language","Jezik") }}</h4>
          <div class="settings-pills">
            <a href="/set_lang/bos" class="settings-pill {% if session.get('lang','bos')=='bos' %}current{% endif %}">🇧🇦 BOS</a>
            <a href="/set_lang/fr"  class="settings-pill {% if session.get('lang')=='fr' %}current{% endif %}">🇫🇷 FR</a>
            <a href="/set_lang/en"  class="settings-pill {% if session.get('lang')=='en' %}current{% endif %}">🇬🇧 EN</a>
            <a href="/set_lang/de"  class="settings-pill {% if session.get('lang')=='de' %}current{% endif %}">🇩🇪 DE</a>
            <a href="/set_lang/pt"  class="settings-pill {% if session.get('lang')=='pt' %}current{% endif %}">🇵🇹 PT</a>
          </div>
        </div>

        <!-- Alati -->
        <div class="settings-section">
          <h4>🛠️ {{ tr.get("nav_tools","Alati") }}</h4>
          <a href="/route_optimizer" class="settings-navlink">
            <span class="settings-navlink-icon">🗺️</span>
            <div><div style="font-weight:600;">{{ tr.get("nav_route","Ruta") }}</div></div>
          </a>
          {% if session.get('role') == 'admin' %}
          <a href="/invoices" class="settings-navlink">
            <span class="settings-navlink-icon">🧾</span>
            <div><div style="font-weight:600;">{{ tr.get("invoices","Fakture") }}</div></div>
          </a>
          {% endif %}
        </div>

        {% if session.get('role') == 'admin' %}
        <!-- Administracija -->
        <div class="settings-section">
          <h4>🔧 {{ tr.get("nav_admin_section","Administracija") }}</h4>
          <a href="/admin" class="settings-navlink">
            <span class="settings-navlink-icon">👤</span>
            <div><div style="font-weight:600;">{{ tr.get("nav_users","Korisnici i lozinka") }}</div></div>
          </a>
          <a href="/workers" class="settings-navlink">
            <span class="settings-navlink-icon">👷</span>
            <div><div style="font-weight:600;">{{ tr.get("workers","Radnici") }}</div></div>
          </a>
          <a href="/clients" class="settings-navlink">
            <span class="settings-navlink-icon">🏢</span>
            <div><div style="font-weight:600;">{{ tr.get("clients","Klijenti") }}</div></div>
          </a>
          <a href="/backup" class="settings-navlink">
            <span class="settings-navlink-icon">💾</span>
            <div><div style="font-weight:600;">Backup &amp; Restore</div></div>
          </a>
        </div>
        {% endif %}

        <!-- Račun & odjava -->
        <div class="settings-section">
          <h4>👤 {{ tr.get("nav_account","Racun") }}</h4>
          {% if session.get('user') %}
          <div class="settings-navlink" style="cursor:default;">
            <span class="settings-navlink-icon">🙍</span>
            <div><div style="font-weight:600;">{{ session['user'] }}</div><div style="font-size:12px;opacity:0.6;">{{ session.get('role','') }}</div></div>
          </div>
          {% endif %}
          <a href="/logout" class="settings-navlink danger">
            <span class="settings-navlink-icon">🚪</span>
            <div style="font-weight:600;">{{ tr["logout"] }}</div>
          </a>
        </div>
      </div>
    </div>
    {% if session.get('copied_shift_id') %}
    <a href="/clear_copy" id="copyOffBtn"
       style="position:fixed;bottom:calc(74px + env(safe-area-inset-bottom,0));right:16px;z-index:450;
              background:#22c55e;color:white;border-radius:28px;padding:11px 20px;font-weight:800;
              font-size:13px;text-decoration:none;display:flex;align-items:center;gap:7px;
              letter-spacing:0.02em;border:2px solid #ff2244;
              animation:copyRgb 1.4s linear infinite;">
        ✕ Copy Off
    </a>
    <style>
    @keyframes copyRgb {
        0%   { border-color:#ff2244; box-shadow:0 0 10px 2px #ff2244, 0 4px 18px rgba(34,197,94,0.45); }
        16%  { border-color:#ff8800; box-shadow:0 0 10px 2px #ff8800, 0 4px 18px rgba(34,197,94,0.45); }
        33%  { border-color:#ffe600; box-shadow:0 0 10px 2px #ffe600, 0 4px 18px rgba(34,197,94,0.45); }
        50%  { border-color:#00dd55; box-shadow:0 0 10px 2px #00dd55, 0 4px 18px rgba(34,197,94,0.45); }
        66%  { border-color:#00ccff; box-shadow:0 0 10px 2px #00ccff, 0 4px 18px rgba(34,197,94,0.45); }
        83%  { border-color:#cc00ff; box-shadow:0 0 10px 2px #cc00ff, 0 4px 18px rgba(34,197,94,0.45); }
        100% { border-color:#ff2244; box-shadow:0 0 10px 2px #ff2244, 0 4px 18px rgba(34,197,94,0.45); }
    }
    </style>
    {% endif %}
    <script>
    function openSettingsSheet(){document.getElementById('settingsSheet').classList.add('open');}
    function closeSettingsSheet(){document.getElementById('settingsSheet').classList.remove('open');}
    </script>
    """


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(_error):
    tr = t()
    if request.path.startswith("/documents/"):
        return redirect("/documents?notice=" + urllib.parse.quote(tr["upload_too_large"]))
    return tr["upload_too_large"], 413


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
        if user and verify_password(user[1], password):
            if not is_password_hash(user[1]):
                c.execute("UPDATE users SET password = ? WHERE username = ?", (hash_password(password), user[0]))
                conn.commit()
            conn.close()
            session["user"] = user[0]
            session["role"] = user[2]
            return redirect("/")
        conn.close()
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
        c.execute("UPDATE users SET password = ? WHERE username = ?", (hash_password(new_password), session["user"]))
        conn.commit()
        conn.close()
    return redirect("/admin")


def load_index_data():
    is_admin = session.get("role") == "admin"
    current_user = session.get("user")
    conn = get_conn()
    c = conn.cursor()
    workers = c.execute("SELECT name, address, contract_type, contract_end_date FROM workers ORDER BY name").fetchall()
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
    base_query += " ORDER BY date, time, id"
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

    if is_admin:
        pending_leave = c.execute("SELECT id, worker, type, date_from, date_to, note, status, created_at FROM leave_requests WHERE status = 'pending' ORDER BY created_at DESC").fetchall()
    else:
        pending_leave = []
    my_leave_requests = [] if is_admin else c.execute("SELECT id, worker, type, date_from, date_to, note, status, created_at FROM leave_requests WHERE worker = ? ORDER BY created_at DESC LIMIT 10", (current_user,)).fetchall()

    client_cities = client_city_map(clients)
    client_addresses = {c[0]: (c[1] or "") for c in clients}
    today_iso = today.strftime("%Y-%m-%d")
    worker_scope_shifts = sorted(all_shifts_for_hours, key=lambda s: (s[3], s[4], s[0]))
    worker_today_shifts = [s for s in worker_scope_shifts if s[3] == today_iso]
    worker_upcoming_shifts = [s for s in worker_scope_shifts if s[3] >= today_iso][:8]
    worker_today_hours = sum(parse_shift_hours(s[4]) for s in worker_today_shifts)
    worker_week_hours = sum(calculate_hours_for_user(week_shifts, None if is_admin else current_user).values())
    worker_month_hours = sum(calculate_hours_for_user(month_shifts, None if is_admin else current_user).values())
    first_of_this_month = datetime(today.year, today.month, 1).strftime("%Y-%m-%d")
    archive_dict = {}
    for s in worker_scope_shifts:
        if s[3] < first_of_this_month:
            ym = s[3][:7]
            if ym not in archive_dict:
                archive_dict[ym] = []
            archive_dict[ym].append(s)
    worker_archive_months = sorted(archive_dict.items(), key=lambda x: x[0], reverse=True)
    current_plan_shifts = [s for s in shifts if s[3] >= first_of_this_month]
    admin_archive_dict = {}
    for s in shifts:
        if s[3] < first_of_this_month:
            ym = s[3][:7]
            admin_archive_dict.setdefault(ym, []).append(s)
    admin_archive_months = []
    for ym in sorted(admin_archive_dict.keys(), reverse=True):
        arc = admin_archive_dict[ym]
        weeks_dict = {}
        for s in arc:
            try:
                d = datetime.strptime(s[3], "%Y-%m-%d")
                wk = (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
                weeks_dict.setdefault(wk, []).append(s)
            except Exception:
                pass
        admin_archive_months.append((ym, len(arc), sorted(weeks_dict.items())))
    conn.close()
    return {
        "is_admin": is_admin, "current_user": current_user, "workers": workers, "clients": clients,
        "client_cities": client_cities, "client_addresses": client_addresses, "db_users": db_users, "worker_colors": worker_colors, "shifts": shifts,
        "selected_date": selected_date, "worker_filter": worker_filter, "client_filter": client_filter,
        "weekly_hours": calculate_hours_for_user(week_shifts, None if is_admin else current_user),
        "monthly_hours": calculate_hours_for_user(month_shifts, None if is_admin else current_user),
        "week_period": f"{format_date(week_start.strftime('%Y-%m-%d'))} - {format_date(week_end.strftime('%Y-%m-%d'))}",
        "month_period": today.strftime("%m/%Y"), "weeks_grouped": group_shifts_by_week(current_plan_shifts),
        "admin_archive_months": admin_archive_months,
        "absences": absences, "absence_summary": absence_summary,
        "today_shift_count": len([s for s in all_shifts_for_hours if s[3] == today.strftime("%Y-%m-%d")]),
        "worker_count": len([w for w in workers if w[0] != "admin"]),
        "client_count": len(clients),
        "month_total_hours": sum(calculate_hours_for_user(month_shifts, None if is_admin else current_user).values()),
        "contract_reminders": contract_reminders(workers) if is_admin else [],
        "pending_leave": pending_leave,
        "my_leave_requests": my_leave_requests,
        "today_iso": today_iso,
        "today_label": format_date(today_iso),
        "worker_today_shifts": worker_today_shifts,
        "worker_upcoming_shifts": worker_upcoming_shifts,
        "worker_today_hours": worker_today_hours,
        "worker_week_hours": worker_week_hours,
        "worker_month_hours": worker_month_hours,
        "worker_archive_months": worker_archive_months,
    }


@app.route("/")
def index():
    if "user" not in session:
        return redirect("/login")
    tr = t()
    dark = get_theme() == "dark"
    data = load_index_data()

    return render_template_string(BASE_STYLE + header_html() + """
    <div class="page-content">
            {% if not is_admin %}
            <div class="wapp-home">
                <section class="wapp-shift-hero">
                    <div class="wapp-kicker">{{ tr.get("today_shifts","Današnje smjene") }}</div>
                    {% if worker_today_shifts %}
                        {% set hero_status = get_auto_status(worker_today_shifts[0][3], worker_today_shifts[0][4]) %}
                        <div class="wapp-shift-time">{{ worker_today_shifts[0][4] }}</div>
                        <span class="wapp-chip" style="background:{{ status_colors.get(hero_status, '#6b7280') }};color:white;--chip-dot:rgba(255,255,255,.88);">{{ get_status_label(hero_status, tr) }}</span>
                    {% else %}
                        <div class="wapp-shift-time">{{ tr.get("no_shifts","Nema smjena") }}</div>
                        <span class="wapp-chip">{{ today_label }}</span>
                    {% endif %}
                    <div class="wapp-hero-grid">
                        <div class="wapp-hero-stat">
                            <div class="wapp-hero-val">{{ "%.2f"|format(worker_today_hours) }}</div>
                            <div class="wapp-hero-sub">{{ tr.get("hours","sati") }} {{ tr.get("today_shifts","danas")|lower }}</div>
                        </div>
                        <div class="wapp-hero-stat">
                            <div class="wapp-hero-val">{{ worker_today_shifts|length }}</div>
                            <div class="wapp-hero-sub">{{ tr.get("today_shifts","Današnje smjene") }}</div>
                        </div>
                    </div>
                </section>

                <div class="wapp-sec">{{ tr.get("today_shifts","Današnje smjene") }}</div>
                <section class="wapp-list-card">
                    {% if worker_today_shifts %}
                        {% for s in worker_today_shifts %}
                        {% set addr = client_addresses.get(s[2], s[2]) %}
                        {% set auto_status = get_auto_status(s[3], s[4]) %}
                        <div class="wapp-shift-row">
                            <div class="wapp-time">{{ s[4].split('-')[0].strip() }}</div>
                            <div>
                                <div class="wapp-client">{{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}</div>
                                <div class="wapp-address">{{ addr }}</div>
                                <span class="wapp-status-badge" style="background:{{ status_colors.get(auto_status, '#6b7280') }};color:white;">{{ get_status_label(auto_status, tr) }}</span>
                            </div>
                            <a class="wapp-map" href="https://www.google.com/maps/search/?api=1&query={{ addr|urlencode }}" target="_blank" rel="noopener" title="Google Maps">➜</a>
                        </div>
                        {% endfor %}
                    {% else %}
                        <div class="wapp-shift-row" style="grid-template-columns:1fr;">
                            <div class="wapp-address">{{ tr.get("no_shifts","Nema smjena") }}</div>
                        </div>
                    {% endif %}
                </section>

                <div class="wapp-sec">{{ tr.get("week_calendar","Sedmicni kalendar") }}</div>
                <div class="wapp-mini-grid">
                    <div class="wapp-mini-card"><b>{{ "%.1f"|format(worker_week_hours) }}</b><span>{{ tr.get("weekly_hours","Sedmicni sati") }}</span></div>
                    <div class="wapp-mini-card"><b>{{ "%.1f"|format(worker_month_hours) }}</b><span>{{ tr.get("monthly_hours","Mjesecni sati") }}</span></div>
                </div>

                <div class="wapp-sec">{{ tr.get("route_optimizer","Optimizacija rute") }}</div>
                <section class="wapp-list-card">
                    {% if worker_upcoming_shifts %}
                        {% for s in worker_upcoming_shifts[:4] %}
                        {% set addr = client_addresses.get(s[2], s[2]) %}
                        <div class="wapp-shift-row wroute">
                            <div class="wapp-time">{{ format_date(s[3]) }}</div>
                            <div>
                                <div class="wapp-client">{{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}</div>
                                <div class="wapp-address">{{ s[4] }} · {{ addr }}</div>
                            </div>
                            <a class="wapp-map" href="https://www.google.com/maps/search/?api=1&query={{ addr|urlencode }}" target="_blank" rel="noopener" title="Google Maps">➜</a>
                        </div>
                        {% endfor %}
                    {% else %}
                        <div class="wapp-shift-row" style="grid-template-columns:1fr;">
                            <div class="wapp-address">{{ tr.get("no_shifts","Nema smjena") }}</div>
                        </div>
                    {% endif %}
                </section>

                {% if worker_archive_months %}
                <div class="wapp-sec">{{ tr.get("archive","Arhiva") }}</div>
                {% set ns = namespace(prev_year='') %}
                {% for ym, arc_shifts in worker_archive_months %}
                {% set yr = ym[:4] %}
                {% set mo = ym[5:]|int %}
                {% if yr != ns.prev_year %}
                {% set ns.prev_year = yr %}
                <div class="wapp-archive-year">{{ yr }}</div>
                {% endif %}
                <div class="wapp-archive-wrap">
                <a class="wapp-archive-pdf" href="/month_pdf?year={{ yr }}&month={{ '%02d'|format(mo) }}" target="_blank" rel="noopener">PDF</a>
                <details class="wapp-archive-card">
                  <summary>
                    <span>{{ format_month_year(yr|int, mo) }}</span>
                    <span class="wapp-archive-count">{{ arc_shifts|length }} {{ tr.get("shift_singular","smjena") if arc_shifts|length == 1 else tr.get("shifts","smjena") }}</span>
                  </summary>
                  <div class="wapp-archive-body">
                    {% for s in arc_shifts %}
                    {% set addr = client_addresses.get(s[2], s[2]) %}
                    {% set auto_status = get_auto_status(s[3], s[4]) %}
                    <div class="wapp-shift-row">
                      <div class="wapp-time">{{ format_date(s[3]) }}</div>
                      <div>
                        <div class="wapp-client">{{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}</div>
                        <div class="wapp-address">{{ s[4] }}</div>
                        <span class="wapp-status-badge" style="background:{{ status_colors.get(auto_status,'#6b7280') }};color:white;">{{ get_status_label(auto_status, tr) }}</span>
                      </div>
                      <a class="wapp-map" href="https://www.google.com/maps/search/?api=1&query={{ addr|urlencode }}" target="_blank" rel="noopener" title="Google Maps">➜</a>
                    </div>
                    {% endfor %}
                  </div>
                </details>
                </div>
                {% endfor %}
                {% endif %}

            </div>
            {% else %}
            {% if is_admin %}<div class="hero">
                <h1>{{ tr["dashboard"] }}</h1>
                <div class="muted">Luxmann Planner · {{ tr["overview"] }}</div>
            </div>{% endif %}
            {% if is_admin %}
            <div class="stats-grid">
                <div class="stat-card stat-today"><div class="muted">{{ tr["today_shifts"] }}</div><div class="stat-number">{{ today_shift_count }}</div></div>
                <div class="stat-card stat-workers"><div class="muted">{{ tr["active_workers"] }}</div><div class="stat-number">{{ worker_count }}</div></div>
                <div class="stat-card stat-clients"><div class="muted">{{ tr["registered_clients"] }}</div><div class="stat-number">{{ client_count }}</div></div>
                <div class="stat-card stat-hours"><div class="muted">{{ tr["this_month_hours"] }}</div><div class="stat-number">{{ "%.1f"|format(month_total_hours) }}</div></div>
            </div>
            {% endif %}
            {% if is_admin and (contract_reminders or pending_leave) %}
            <div class="card" style="border-left:6px solid #f59e0b; margin-bottom:16px;">
                <h3>{{ tr["contract_reminders"] }}</h3>
                {% if pending_leave %}
                    <div style="margin-bottom:10px;"><b style="color:#8b5cf6;">{{ tr["leave_requests_pending"] }} ({{ pending_leave|length }})</b></div>
                    {% for r in pending_leave %}
                    <div class="user-row" style="border-left:4px solid #8b5cf6; padding-left:10px; margin-bottom:10px;">
                        <b>{{ r[1] }}</b> — <span style="color:#8b5cf6;">{{ tr.get('leave_type_' + r[2], r[2]) }}</span><br>
                        <small>{{ format_date(r[3]) }} → {{ format_date(r[4]) }}</small>
                        {% if r[5] %}<br><small style="opacity:0.7;">{{ r[5] }}</small>{% endif %}
                        <div style="display:flex;gap:8px;margin-top:6px;">
                            <form method="post" action="/leave_request/approve/{{ r[0] }}" style="display:inline;">
                                <button style="background:#16a34a;color:white;border:none;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:12px;">✓ {{ tr["leave_approve"] }}</button>
                            </form>
                            <form method="post" action="/leave_request/reject/{{ r[0] }}" style="display:inline;">
                                <button style="background:#ef4444;color:white;border:none;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:12px;">✗ {{ tr["leave_reject"] }}</button>
                            </form>
                        </div>
                    </div>
                    {% endfor %}
                    {% if contract_reminders %}<hr style="margin:12px 0;">{% endif %}
                {% endif %}
                {% for r in contract_reminders %}
                    <div class="user-row">
                        <b>{{ r.worker }}</b> - {{ r.contract_type or tr["contract_type"] }}<br>
                        <small>{{ tr["contract_end_date"] }}: {{ format_date(r.contract_end) }}</small><br>
                        <span style="color:#ef4444;font-weight:bold;">{{ tr["contract_expired"] if r.status == "expired" else tr["contract_expires_soon"] }}{% if r.status == "soon" %}: {{ r.days_left }} {{ tr["days"] }}{% endif %}</span>
                    </div>
                {% endfor %}
            </div>
            {% endif %}
            <div class="section-title"><h2>{{ tr["quick_actions"] }}</h2></div>

    <div class="grid">
        {% if is_admin %}
        <div class="card dashboard-panel panel-worker"><h3>{{ tr["add_worker"] }}</h3><form method="post" action="/add_worker" autocomplete="off"><input name="worker_name" placeholder="{{ tr['worker_name'] }}" required autocomplete="off"><input name="address" placeholder="{{ tr['address'] }}" autocomplete="off"><input name="contract_type" placeholder="{{ tr['contract_type'] }}" autocomplete="off"><label>{{ tr["contract_end_date"] }}</label><input name="contract_end_date" type="date"><button>{{ tr["add_worker"] }}</button></form></div>
        <div class="card dashboard-panel panel-client"><h3>{{ tr["add_client"] }}</h3><form method="post" action="/add_client" autocomplete="off"><input name="client_name" placeholder="{{ tr['client_name'] }}" required autocomplete="off"><input name="address" placeholder="{{ tr['address'] }}" required autocomplete="off"><button>{{ tr["add_client"] }}</button></form></div>

        <div class="card dashboard-panel panel-shift">
            <h3>{{ tr["add_shift"] }}</h3>
            <form method="post" action="/add_shift">
                <label>{{ tr["choose_worker"] }}</label>
                {% for w in workers %}{% if w[0] != 'admin' %}<label class="check-row"><input type="checkbox" name="workers" value="{{ w[0] }}">{{ w[0] }}</label>{% endif %}{% endfor %}
                <div class="client-search-wrapper"><input type="text" id="csInputDash" class="client-search-input" placeholder="{{ tr['search_placeholder'] }}" autocomplete="off"><input type="hidden" name="client" id="csHiddenDash" required><div class="client-search-dropdown" id="csListDash"></div></div>
                <div style="text-align:right;margin-top:2px;"><a href="/clients" style="font-size:11px;color:#3b82f6;text-decoration:none;opacity:0.8;">🏢 {{ tr["clients"] }} →</a></div>
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
            <form method="get"><input type="date" name="date" value="{{ request.args.get('date', '') }}"><select name="worker"><option value="">{{ tr["all_workers"] }}</option>{% for w in workers %}<option value="{{ w[0] }}" {% if worker_filter == w[0] %}selected{% endif %}>{{ w[0] }}</option>{% endfor %}</select><div class="client-search-wrapper" style="display:inline-block;vertical-align:middle;"><input type="text" id="csInputFilt" class="client-search-input" value="{{ client_filter }}" placeholder="{{ tr['all_clients'] }}" autocomplete="off" style="width:160px;"><input type="hidden" name="client" id="csHiddenFilt" value="{{ client_filter }}"><div class="client-search-dropdown" id="csListFilt"></div></div><input name="q" value="{{ request.args.get('q', '') }}" placeholder="{{ tr['search_placeholder'] }}"><button>{{ tr["filter_btn"] }}</button></form><a class="reset-link" href="/">{{ tr["reset"] }}</a>
        </div>

        <div class="card dashboard-panel panel-absence">
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

        {% if not is_admin %}
        <div class="card dashboard-panel" style="border-left:4px solid #8b5cf6;">
            <h3>🏖️ {{ tr["leave_request"] }}</h3>
            <form method="post" action="/leave_request">
                <select name="type">
                    <option value="vacation">{{ tr["leave_type_vacation"] }}</option>
                    <option value="sick">{{ tr["leave_type_sick"] }}</option>
                    <option value="other">{{ tr["leave_type_other"] }}</option>
                </select>
                <label>{{ tr["leave_date_from"] }}</label>
                <input type="date" name="date_from" required>
                <label>{{ tr["leave_date_to"] }}</label>
                <input type="date" name="date_to" required>
                <input type="text" name="note" placeholder="{{ tr['leave_note'] }}">
                <button>{{ tr["leave_send"] }}</button>
            </form>
            {% if my_leave_requests %}
            <h4 style="margin-top:14px;">{{ tr["leave_my_requests"] }}</h4>
            {% for r in my_leave_requests %}
            <div class="user-row" style="border-left:3px solid {% if r[6]=='approved' %}#16a34a{% elif r[6]=='rejected' %}#ef4444{% else %}#f59e0b{% endif %};padding-left:8px;">
                <b>{{ tr.get('leave_type_' + r[2], r[2]) }}</b>
                <span style="float:right;font-size:11px;font-weight:bold;color:{% if r[6]=='approved' %}#16a34a{% elif r[6]=='rejected' %}#ef4444{% else %}#f59e0b{% endif %};">
                    {% if r[6]=='approved' %}✓ {{ tr["leave_approved"] }}{% elif r[6]=='rejected' %}✗ {{ tr["leave_rejected"] }}{% else %}⏳ {{ tr["leave_pending"] }}{% endif %}
                </span><br>
                <small>{{ format_date(r[3]) }} → {{ format_date(r[4]) }}</small>
                {% if r[5] %}<br><small style="opacity:0.65;">{{ r[5] }}</small>{% endif %}
            </div>
            {% endfor %}
            {% endif %}
        </div>
        {% endif %}

        <div class="card dashboard-panel panel-week-hours"><h3>{{ tr["weekly_hours"] }}</h3><div class="muted">{{ tr["week_period"] }}: {{ week_period }}</div>{% for worker, hours in weekly_hours.items() %}<div class="hours-row"><span>{{ worker }}</span><span>{{ "%.2f"|format(hours) }} {{ tr["hours"] }}</span></div>{% endfor %}{% if weekly_hours|length == 0 %}<div class="muted">0 {{ tr["hours"] }}</div>{% endif %}</div>
        <div class="card dashboard-panel panel-month-hours"><h3>{{ tr["monthly_hours"] }}</h3><div class="muted">{{ month_period }}</div>{% for worker, hours in monthly_hours.items() %}<div class="hours-row"><span>{{ worker }}</span><span>{{ "%.2f"|format(hours) }} {{ tr["hours"] }}</span></div>{% endfor %}{% if monthly_hours|length == 0 %}<div class="muted">0 {{ tr["hours"] }}</div>{% endif %}{% if session.get('role') == 'admin' %}<br><a class="pdf-link" href="/month_pdf" target="_blank">{{ tr["month_pdf"] }}</a>{% endif %}</div>
        <div class="card dashboard-panel panel-absence-summary"><h3>{{ tr["monthly_absence_days"] }}</h3><div class="muted">{{ month_period }}</div>{% for a, days in absence_summary %}<div class="hours-row"><b>{{ a[1] }}</b> - {{ tr.get(a[2], a[2]) }}: {{ days }} {{ tr["days"] }}<br><small>{{ format_date(a[3]) }} - {{ format_date(a[4]) }}</small></div>{% endfor %}{% if absence_summary|length == 0 %}<div class="muted">0 {{ tr["days"] }}</div>{% endif %}</div>
    </div>

    <div class="card" style="margin-top:20px;">
        <h2>{{ tr["plan"] }}</h2>
        {% if weeks_grouped|length == 0 %}<div class="muted">{{ tr["no_shifts"] }}</div>{% endif %}
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(360px, 1fr)); gap:18px;">
        {% for week_start_key, week_shifts in weeks_grouped.items() %}
            {% set week_end_key = (datetime.strptime(week_start_key, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d") %}
            <div class="card" style="padding:12px;"><h3 style="border-bottom:2px solid #1f4f82; padding-bottom:8px; margin-top:0;">{{ format_date(week_start_key) }} - {{ format_date(week_end_key) }}</h3>
            {% for s in week_shifts %}{% set auto_status = get_auto_status(s[3], s[4]) %}<div class="shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="border-left:6px solid {{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#1f4f82') }}"><b>{{ format_date(s[3]) }}</b> | {{ s[4] }}<span class="status-badge" style="background:{{ status_colors.get(auto_status, '#6b7280') }};">{{ get_status_label(auto_status, tr) }}</span><br><br><b>{{ tr["team"] }}:</b> {{ s[1] }}<br><b>{{ tr["pdf_client"] }}:</b> {{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}{% if is_admin %}<a class="action-link edit-link" href="/edit_shift/{{ s[0] }}">{{ tr["edit"] }}</a><a class="action-link delete-link"
   href="/delete_shift/{{ s[0] }}"
   onclick="return confirm('Da li ste sigurni?');">
   {{ tr["delete"] }}
</a><a class="action-link copy-link" href="/copy_shift/{{ s[0] }}">{{ tr["copy"] }}</a>{% endif %}</div>{% endfor %}</div>
        {% endfor %}
        </div>
        <a class="week-link" href="/week">{{ tr["week_calendar"] }}</a>{% if session.get('role') == 'admin' %}<a class="week-link" href="/month">{{ tr["month_calendar"] }}</a>{% endif %}<a class="week-link" href="/route_optimizer">{{ tr["route_optimizer"] }}</a><a class="pdf-link" href="/export_pdf{% if request.args.get('date') %}?date={{ request.args.get('date') }}{% endif %}" target="_blank">{{ tr["pdf"] }}</a>
    </div>

    {% if is_admin and admin_archive_months %}
    <div class="card" style="margin-top:16px;">
        <h2>{{ tr.get("archive","Arhiva") }}</h2>
        {% set ns = namespace(prev_year='') %}
        {% for ym, arc_count, arc_weeks in admin_archive_months %}
        {% set yr = ym[:4] %}
        {% set mo = ym[5:]|int %}
        {% if yr != ns.prev_year %}
        {% set ns.prev_year = yr %}
        <h3 style="margin:18px 0 8px; color:{{ '#93c5fd' if dark else '#1f4f82' }}; border-bottom:1px solid {{ '#2c2c30' if dark else '#e5e7eb' }}; padding-bottom:6px;">{{ yr }}</h3>
        {% endif %}
        <div style="position:relative; margin-bottom:10px;">
        <a href="/month_pdf?year={{ yr }}&month={{ '%02d'|format(mo) }}" target="_blank" rel="noopener" class="pdf-link" style="position:absolute; top:9px; right:38px; font-size:11px; padding:3px 10px; z-index:2;">PDF</a>
        <details>
          <summary style="display:flex; align-items:center; justify-content:space-between; padding:10px 14px; padding-right:110px; cursor:pointer; background:{{ '#1e1e20' if dark else '#f8fafc' }}; border-radius:10px; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; list-style:none; user-select:none;">
            <span style="font-weight:800; font-size:15px;">{{ format_month_year(yr|int, mo) }}</span>
            <span style="display:flex; align-items:center; gap:12px;">
              <span style="font-size:12px; color:{{ '#94a3b8' if dark else '#64748b' }};">{{ arc_count }} {{ tr.get("shift_singular","smjena") if arc_count == 1 else tr.get("shifts","smjena") }}</span>
              <span style="font-size:18px; color:{{ '#94a3b8' if dark else '#64748b' }};">›</span>
            </span>
          </summary>
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(340px, 1fr)); gap:14px; padding:14px 0 4px;">
          {% for wk, wk_shifts in arc_weeks %}
          {% set wk_end = (datetime.strptime(wk, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d") %}
          <div class="card" style="padding:12px;">
            <h3 style="border-bottom:2px solid #1f4f82; padding-bottom:6px; margin-top:0; font-size:13px;">{{ format_date(wk) }} – {{ format_date(wk_end) }}</h3>
            {% for s in wk_shifts %}{% set auto_status = get_auto_status(s[3], s[4]) %}
            <div class="shift" style="border-left:6px solid {{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#1f4f82') }}">
              <b>{{ format_date(s[3]) }}</b> | {{ s[4] }}<span class="status-badge" style="background:{{ status_colors.get(auto_status, '#6b7280') }};">{{ get_status_label(auto_status, tr) }}</span><br><br>
              <b>{{ tr["team"] }}:</b> {{ s[1] }}<br>
              <b>{{ tr["pdf_client"] }}:</b> {{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}
              <a class="action-link edit-link" href="/edit_shift/{{ s[0] }}">{{ tr["edit"] }}</a>
              <a class="action-link delete-link" href="/delete_shift/{{ s[0] }}" onclick="return confirm('Da li ste sigurni?');">{{ tr["delete"] }}</a>
              <a class="action-link copy-link" href="/copy_shift/{{ s[0] }}">{{ tr["copy"] }}</a>
            </div>
            {% endfor %}
          </div>
          {% endfor %}
          </div>
        </details>
        </div>
        {% endfor %}
    </div>
    {% endif %}
    {% endif %}
    </div>

  <script>
function dragShift(ev, shiftId){
    ev.dataTransfer.setData('shift_id', shiftId);
}

function filterClientOptions(inputId, selectId){
    var input = document.getElementById(inputId);
    var select = document.getElementById(selectId);
    if(!input || !select){return;}
    var query = input.value.trim().toLowerCase();
    var firstVisible = null;
    Array.prototype.forEach.call(select.options, function(option, index){
        if(index === 0){
            option.hidden = false;
            return;
        }
        var text = option.text.toLowerCase();
        var match = !query || text.indexOf(query) === 0;
        option.hidden = !match;
        if(match && !firstVisible){
            firstVisible = option;
        }
    });
    if(firstVisible){
        select.value = firstVisible.value;
    } else {
        select.value = "";
    }
}

document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('a.delete-link').forEach(function(link){
        link.addEventListener('click', function(e){
            var ok = confirm('Da li ste sigurni da želite obrisati?');
            if(!ok){ e.preventDefault(); return false; }
        });
    });
    var CD=[{% for c in clients %}{"name":{{c[0]|tojson}},"addr":{{(c[1] or '')|tojson}}}{% if not loop.last %},{% endif %}{% endfor %}];
    initClientSearch('csInputDash','csHiddenDash','csListDash',CD);
    var CDf=[{"name":"","addr":"{{ tr['all_clients'] }}"}].concat(CD);
    initClientSearch('csInputFilt','csHiddenFilt','csListFilt',CDf);
});
</script>
    """, tr=tr, dark=dark, datetime=datetime, timedelta=timedelta, format_date=format_date,
       format_month_year=format_month_year,
       time_hours=time_hours(), time_minutes=time_minutes(), status_colors=STATUS_COLORS,
       get_status_label=get_status_label, get_auto_status=get_auto_status, split_workers=split_workers, **data)


@app.route("/copy_shift/<int:id>")
def copy_shift(id):
    if "user" not in session or session.get("role") != "admin":
        return redirect("/")
    session["copied_shift_id"] = id
    return redirect(request.referrer or "/month")


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
        if duplicate_shift_exists(conn, worker, client, date, time):
            conn.close()
            notice = urllib.parse.quote(t()["duplicate_shift_warning"])
            try:
                d = datetime.strptime(date, "%Y-%m-%d")
                return redirect(f"/month?year={d.year}&month={d.month}&notice={notice}")
            except Exception:
                return redirect((request.referrer or "/month") + ("&" if "?" in (request.referrer or "/month") else "?") + f"notice={notice}")
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
    return redirect(request.referrer or "/month")


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
    existing = c.execute("SELECT worker, client, time FROM shifts WHERE id = ?", (shift_id,)).fetchone()
    if existing and duplicate_shift_exists(conn, existing[0], existing[1], new_date, existing[2], exclude_id=shift_id):
        conn.close()
        return (t()["duplicate_shift_warning"], 409)
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


@app.route("/leave_request", methods=["POST"])
def submit_leave_request():
    if "user" not in session or session.get("role") == "admin":
        return redirect("/")
    worker = session.get("user")
    leave_type = request.form.get("type", "vacation").strip()
    date_from = request.form.get("date_from", "").strip()
    date_to = request.form.get("date_to", "").strip()
    note = request.form.get("note", "").strip()
    if worker and date_from and date_to:
        conn = get_conn(); c = conn.cursor()
        c.execute("INSERT INTO leave_requests (worker, type, date_from, date_to, note, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                  (worker, leave_type, date_from, date_to, note, lux_now().strftime("%Y-%m-%d %H:%M")))
        conn.commit(); conn.close()
    return redirect("/")


@app.route("/leave_request/approve/<int:req_id>", methods=["POST"])
def approve_leave_request(req_id):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn(); c = conn.cursor()
    row = c.execute("SELECT worker, type, date_from, date_to, note FROM leave_requests WHERE id = ?", (req_id,)).fetchone()
    if row:
        c.execute("UPDATE leave_requests SET status = 'approved' WHERE id = ?", (req_id,))
        c.execute("INSERT INTO absences (worker, type, date_from, date_to, note) VALUES (?, ?, ?, ?, ?)",
                  (row[0], row[1], row[2], row[3], row[4] or ""))
        conn.commit()
    conn.close()
    return redirect("/")


@app.route("/leave_request/reject/<int:req_id>", methods=["POST"])
def reject_leave_request(req_id):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE leave_requests SET status = 'rejected' WHERE id = ?", (req_id,))
    conn.commit(); conn.close()
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
    today_dt = datetime.today()
    worker_start = datetime(today_dt.year, today_dt.month, 1)
    worker_year_end = datetime(today_dt.year, 12, 31)
    worker_day_count = max(1, (worker_year_end.date() - worker_start.date()).days + 1)
    worker_days_dt = [worker_start + timedelta(days=i) for i in range(worker_day_count)]
    worker_days = [d.strftime("%Y-%m-%d") for d in worker_days_dt]
    query_start, query_end = (worker_days[0], worker_days[-1]) if not is_admin else (week_days[0], week_days[-1])
    shifts = c.execute("SELECT * FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time, id", (query_start, query_end)).fetchall()
    if not is_admin:
        shifts = [s for s in shifts if worker_in_shift(current_user, s[1])]
    holiday_years = {d.year for d in worker_days_dt} | {start_week.year, week_end.year}
    holidays_map = get_all_holidays(conn, holiday_years)
    clients_raw = c.execute("SELECT name, address FROM clients ORDER BY name").fetchall()
    client_cities = client_city_map(clients_raw)
    clients = clients_raw
    workers = c.execute("SELECT name FROM workers ORDER BY name").fetchall()
    conn.close()
    day_names = [tr["monday"], tr["tuesday"], tr["wednesday"], tr["thursday"], tr["friday"], tr["saturday"], tr["sunday"]]
    today_iso = datetime.today().strftime("%Y-%m-%d")
    selected_week_day = request.args.get("day", "").strip()
    if selected_week_day not in worker_days:
        selected_week_day = today_iso if today_iso in worker_days else week_days[0]
    day_shift_counts = {day: len([s for s in shifts if s[3] == day]) for day in worker_days}
    day_month_labels = {d.strftime("%Y-%m-%d"): format_month_year(d.year, d.month) for d in worker_days_dt}

    return render_template_string(BASE_STYLE + header_html() + """
    {% if not is_admin %}
    <div class="page-content">
      <div class="wapp-week-shell">
        <div class="wapp-month-label" id="wappMonthLabel">{{ day_month_labels.get(selected_week_day, "") }}</div>
        <div class="wapp-date-strip" id="wappDateStrip" aria-label="{{ tr.get('week_calendar','Sedmicni kalendar') }}">
          {% for day in worker_days %}
          <button type="button" class="wapp-date-bubble {% if day == selected_week_day %}active{% endif %}" data-day="{{ day }}" data-month="{{ day_month_labels.get(day, '') }}" onclick="selectWappDay('{{ day }}', this)" aria-label="{{ format_date(day) }}">
            {{ day[8:10] }}
          </button>
          {% endfor %}
        </div>
        <div class="wapp-day-panels">
          {% for day in worker_days %}
          <section class="wapp-day-panel {% if day == selected_week_day %}active{% endif %}" id="wappDay{{ day|replace('-', '') }}">
            {% if day_shift_counts.get(day, 0) > 0 %}
            <div class="wapp-week-shifts">
              {% for s in shifts %}
                {% if s[3] == day %}
                {% set auto_status = get_auto_status(s[3], s[4]) %}
                <article class="wapp-week-card" style="--worker-color:{{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#2563eb') }};">
                  <div class="wapp-week-time">{{ s[4] }}</div>
                  <div class="wapp-week-client">{{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}</div>
                  <div class="wapp-week-worker">{{ s[1] }}</div>
                  <span class="wapp-status-badge" style="background:{{ status_colors.get(auto_status, '#6b7280') }};color:white;">{{ get_status_label(auto_status, tr) }}</span>
                </article>
                {% endif %}
              {% endfor %}
            </div>
            {% else %}
            <div class="wapp-empty-day">{{ tr["no_shifts"] }}</div>
            {% endif %}
          </section>
          {% endfor %}
        </div>
      </div>
    </div>
    <script>
    function selectWappDay(day, btn){
      document.querySelectorAll('.wapp-date-bubble').forEach(function(b){ b.classList.remove('active'); });
      if(btn) btn.classList.add('active');
      var monthLabel = document.getElementById('wappMonthLabel');
      if(monthLabel && btn && btn.dataset.month) monthLabel.textContent = btn.dataset.month;
      document.querySelectorAll('.wapp-day-panel').forEach(function(p){ p.classList.remove('active'); });
      var panel = document.getElementById('wappDay' + day.split('-').join(''));
      if(panel){
        panel.classList.add('active');
        var scroller = panel.querySelector('.wapp-week-shifts');
        if(scroller) scroller.scrollTo({left:0, behavior:'smooth'});
      }
      if(btn) btn.scrollIntoView({behavior:'smooth', inline:'center', block:'nearest'});
    }
    document.addEventListener('DOMContentLoaded', function(){
      var active = document.querySelector('.wapp-date-bubble.active');
      if(active) active.scrollIntoView({inline:'center', block:'nearest'});
      var strip = document.getElementById('wappDateStrip');
      var monthLabel = document.getElementById('wappMonthLabel');
      var ticking = false;
      function updateMonthFromScroll(){
        if(!strip || !monthLabel) return;
        var buttons = Array.prototype.slice.call(strip.querySelectorAll('.wapp-date-bubble'));
        if(!buttons.length) return;
        var center = strip.getBoundingClientRect().left + strip.clientWidth / 2;
        var closest = buttons.reduce(function(best, btn){
          var rect = btn.getBoundingClientRect();
          var dist = Math.abs((rect.left + rect.width / 2) - center);
          return (!best || dist < best.dist) ? {btn:btn, dist:dist} : best;
        }, null);
        if(closest && closest.btn.dataset.month) monthLabel.textContent = closest.btn.dataset.month;
      }
      if(strip){
        strip.addEventListener('scroll', function(){
          if(ticking) return;
          ticking = true;
          window.requestAnimationFrame(function(){ updateMonthFromScroll(); ticking = false; });
        }, {passive:true});
        updateMonthFromScroll();
      }
    });
    </script>
    {% else %}
    <h1>{{ tr["week_calendar"] }}</h1>
    <div>
        <a class="back-button" href="/">{{ tr["back"] }}</a>
        {% if session.get('role') == 'admin' %}<a href="/month?year={{ start_year }}&month={{ start_month }}">{{ tr["month_calendar"] }}</a>{% endif %}
        <a class="pdf-link" href="/week_pdf?start={{ week_days[0] }}" target="_blank">PDF {{ tr["week_calendar"] }}</a>
    </div>
    <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin:16px 0; flex-wrap:wrap;">
        <a href="/week?start={{ prev_week }}">{{ tr["prev_week"] }}</a><strong>{{ format_date(week_days[0]) }} - {{ format_date(week_days[-1]) }}</strong><a href="/week?start={{ next_week }}">{{ tr["next_week"] }}</a><a href="/week?start={{ current_week }}">{{ tr["current_week"] }}</a>
    </div>
    <div class="calendar-board week-calendar-grid" style="display:flex; gap:12px; flex-wrap:wrap;">
        {% for day in week_days %}
            {% set holiday_name = holidays_map.get(day) %}
            <div class="card calendar-day-card {% if holiday_name %}holiday-soft{% endif %} {% if is_weekend(day) %}weekend-soft{% endif %}" style="width:180px; min-height:130px; position:relative;" ondragover="allowDrop(event)" ondragleave="clearDrop(event)" ondrop="dropShift(event, '{{ day }}')">
                <a class="week-day-heading" href="{% if is_admin %}javascript:void(0){% else %}/?selected_date={{ day }}{% endif %}" {% if is_admin %}onclick="openHolidayModal('{{ day }}')"{% endif %}>{{ day_names[loop.index0] }}<br>{{ format_date(day) }}</a>
                {% if is_admin %}<div class="day-menu-wrapper" style="position:absolute;top:4px;right:4px;"><button onclick="toggleDayMenu(this)" title="{{ tr['add_shift'] }}" style="background:none;border:none;font-size:20px;font-weight:bold;cursor:pointer;padding:2px 5px;line-height:1;width:auto;margin:0;color:{% if dark %}#4ade80{% else %}#1f4f82{% endif %};opacity:{% if dark %}0.9{% else %}0.7{% endif %};" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='{% if dark %}0.9{% else %}0.7{% endif %}'">+</button><div class="day-mini-menu" style="display:none;position:absolute;right:0;top:28px;z-index:300;min-width:155px;border-radius:8px;overflow:hidden;box-shadow:0 4px 18px rgba(0,0,0,0.18);background:{% if dark %}#1d1d1f{% else %}white{% endif %};border:1px solid {% if dark %}#2c2c30{% else %}#dbeafe{% endif %};"><a href="javascript:void(0)" onclick="openAddShiftModal('{{ day }}')" style="display:block;padding:10px 15px;text-decoration:none;color:{% if dark %}#93c5fd{% else %}#1f4f82{% endif %};font-size:13px;font-weight:600;white-space:nowrap;" onmouseover="this.style.background='{% if dark %}#2c2c30{% else %}#eef4ff{% endif %}'" onmouseout="this.style.background='transparent'">+ {{ tr['add_shift'] }}</a></div></div>{% endif %}
                {% if holiday_name %}<small class="holiday-note">{{ holiday_name }}</small>{% endif %}
                {% for s in shifts %}{% if s[3] == day %}<div class="mini-shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="--shift-accent:{{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#7aa7df') }};"><b>{{ s[1] }}</b><br>{{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}<br>{{ s[4] }}{% if is_admin %}<div style="display:flex;flex-wrap:nowrap;gap:2px;margin-top:4px;"><a class="mini-link edit-link" style="flex:1;min-width:0;padding:3px 4px;font-size:11px;justify-content:center;" href="javascript:void(0)" data-eid="{{ s[0] }}" data-ew="{{ s[1]|e }}" data-ecl="{{ s[2]|e }}" data-edt="{{ s[3]|e }}" data-etm="{{ s[4]|e }}" data-est="{{ s[5]|e }}" onclick="openEditModalW(this)">{{ tr["edit"] }}</a><a class="mini-link delete-link" style="flex:1;min-width:0;padding:3px 4px;font-size:11px;justify-content:center;" href="/delete_shift/{{ s[0] }}">{{ tr["delete"] }}</a><a class="mini-link copy-link" style="flex:1;min-width:0;padding:3px 4px;font-size:11px;justify-content:center;" href="/copy_shift/{{ s[0] }}">{{ tr["copy"] }}</a></div>{% endif %}</div>{% endif %}{% endfor %}
            </div>
        {% endfor %}
    </div>
    {% if is_admin %}<div id="holidayModal" class="modal-backdrop"><div class="modal-card"><h3>{{ tr["add_holiday"] }}</h3><form method="post" action="/add_holiday"><input type="date" name="date" id="holidayDate" required><input type="text" name="name" placeholder="{{ tr['holiday_name'] }}" required><button>{{ tr["save"] }}</button></form><button type="button" onclick="closeHolidayModal()">{{ tr["cancel"] }}</button></div></div>{% endif %}
    {% if is_admin %}
    <div id="addShiftModal" class="modal-backdrop" style="display:none;">
      <div class="modal-card" style="max-width:400px;width:95%;max-height:90vh;overflow-y:auto;">
        <h3 id="shiftModalTitleW">+ {{ tr['add_shift'] }} — <span id="addShiftModalDate"></span></h3>
        <form method="post" action="/add_shift" id="shiftModalFormW">
          <input type="hidden" name="return_to" id="shiftReturnToW" value="">
          <label>{{ tr['choose_worker'] }}</label>
          {% for w in workers %}{% if w[0] != 'admin' %}<label class="check-row"><input type="checkbox" name="workers" value="{{ w[0] }}">{{ w[0] }}</label>{% endif %}{% endfor %}
          <div class="client-search-wrapper"><input type="text" id="csInputWeek" class="client-search-input" placeholder="{{ tr['search_placeholder'] }}" autocomplete="off"><input type="hidden" name="client" id="csHiddenWeek" required><div class="client-search-dropdown" id="csListWeek"></div></div>
          <input id="addShiftDate" name="date" type="date" required>
          <label>{{ tr['start_time'] }}</label>
          <div style="display:flex;gap:6px;"><select name="start_hour">{% for h in time_hours %}<option value="{{ h }}" {% if h=='07' %}selected{% endif %}>{{ h }}</option>{% endfor %}</select><select name="start_minute"><option value="00" selected>00</option><option value="15">15</option><option value="30">30</option><option value="45">45</option></select></div>
          <label>{{ tr['end_time'] }}</label>
          <div style="display:flex;gap:6px;"><select name="end_hour">{% for h in time_hours %}<option value="{{ h }}" {% if h=='15' %}selected{% endif %}>{{ h }}</option>{% endfor %}</select><select name="end_minute"><option value="00" selected>00</option><option value="15">15</option><option value="30">30</option><option value="45">45</option></select></div>
          <select name="status"><option value="planned">{{ tr['status_planned'] }}</option><option value="in_progress">{{ tr['status_in_progress'] }}</option><option value="done">{{ tr['status_done'] }}</option></select>
          <button type="submit" id="shiftModalSaveBtnW">{{ tr['add_shift'] }}</button>
        </form>
        <button type="button" onclick="closeAddShiftModal()" style="margin-top:8px;width:100%;">{{ tr['cancel'] }}</button>
      </div>
    </div>
    {% endif %}
    <script>
    function openHolidayModal(dateStr){var m=document.getElementById('holidayModal');var d=document.getElementById('holidayDate');if(m&&d){d.value=dateStr;m.style.display='block';}}
    function closeHolidayModal(){var m=document.getElementById('holidayModal');if(m){m.style.display='none';}}
    function openAddShiftModal(dateStr){
      var form=document.getElementById('shiftModalFormW');
      form.action='/add_shift';
      var rt=document.getElementById('shiftReturnToW');if(rt)rt.value='';
      var titleEl=document.getElementById('shiftModalTitleW');
      if(titleEl)titleEl.innerHTML='+ {{ tr["add_shift"] }} — <span id="addShiftModalDate"></span>';
      document.getElementById('addShiftModalDate').textContent=dateStr;
      var btn=document.getElementById('shiftModalSaveBtnW');if(btn)btn.textContent='{{ tr["add_shift"] }}';
      document.getElementById('addShiftDate').value=dateStr;
      var ci=document.getElementById('csInputWeek');var ch=document.getElementById('csHiddenWeek');
      if(ci)ci.value='';if(ch)ch.value='';
      form.querySelectorAll('input[name="workers"]').forEach(function(cb){cb.checked=false;});
      document.getElementById('addShiftModal').style.display='block';
      document.querySelectorAll('.day-mini-menu').forEach(function(m){m.style.display='none';});
    }
    function openEditModalW(el){
      var id=el.dataset.eid;
      var workers=el.dataset.ew||'';
      var client=el.dataset.ecl||'';
      var date=el.dataset.edt||'';
      var timeRange=el.dataset.etm||'07:00-15:00';
      var status=el.dataset.est||'planned';
      var form=document.getElementById('shiftModalFormW');
      form.action='/edit_shift/'+id;
      var rt=document.getElementById('shiftReturnToW');if(rt)rt.value=window.location.href;
      var titleEl=document.getElementById('shiftModalTitleW');
      if(titleEl)titleEl.textContent='✏️ {{ tr["edit_shift"] }}';
      var btn=document.getElementById('shiftModalSaveBtnW');if(btn)btn.textContent='{{ tr["save"] }}';
      document.getElementById('addShiftDate').value=date;
      var dateSpan=document.getElementById('addShiftModalDate');if(dateSpan)dateSpan.textContent=date;
      var workerList=workers.split(', ');
      form.querySelectorAll('input[name="workers"]').forEach(function(cb){cb.checked=workerList.indexOf(cb.value)>=0;});
      var ci=document.getElementById('csInputWeek');var ch=document.getElementById('csHiddenWeek');
      if(ci)ci.value=client;if(ch)ch.value=client;
      var parts=timeRange.split('-');
      var sp=(parts[0]||'07:00').split(':');var ep=(parts[1]||'15:00').split(':');
      var shS=form.querySelector('select[name="start_hour"]');var smS=form.querySelector('select[name="start_minute"]');
      var shE=form.querySelector('select[name="end_hour"]');var smE=form.querySelector('select[name="end_minute"]');
      if(shS)shS.value=sp[0]||'07';if(smS)smS.value=sp[1]||'00';
      if(shE)shE.value=ep[0]||'15';if(smE)smE.value=ep[1]||'00';
      var stSel=form.querySelector('select[name="status"]');if(stSel)stSel.value=status;
      document.getElementById('addShiftModal').style.display='block';
    }
    function closeAddShiftModal(){document.getElementById('addShiftModal').style.display='none';}
    document.getElementById('addShiftModal') && document.getElementById('addShiftModal').addEventListener('click',function(e){if(e.target===this)closeAddShiftModal();});
    function dragShift(ev, shiftId){ev.dataTransfer.setData('shift_id', shiftId);} function allowDrop(ev){ev.preventDefault();ev.currentTarget.classList.add('drop-target');} function clearDrop(ev){ev.currentTarget.classList.remove('drop-target');}
    function dropShift(ev, dateStr){ev.preventDefault();ev.currentTarget.classList.remove('drop-target');var shiftId=ev.dataTransfer.getData('shift_id');if(!shiftId)return;fetch('/move_shift',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'shift_id='+encodeURIComponent(shiftId)+'&date='+encodeURIComponent(dateStr)}).then(function(resp){if(resp.status===409){return resp.text().then(function(msg){showPlannerAlert(msg);});}window.location.reload();});}
    function toggleDayMenu(btn){var menu=btn.nextElementSibling;document.querySelectorAll('.day-mini-menu').forEach(function(m){if(m!==menu)m.style.display='none';});menu.style.display=menu.style.display==='none'?'block':'none';}
    document.addEventListener('click',function(e){if(!e.target.closest('.day-menu-wrapper')&&!e.target.closest('#addShiftModal .modal-card')){document.querySelectorAll('.day-mini-menu').forEach(function(m){m.style.display='none';});}});
    document.addEventListener('DOMContentLoaded',function(){
      var CD=[{% for cl in clients %}{"name":{{cl[0]|tojson}},"addr":{{(cl[1] or '')|tojson}}}{% if not loop.last %},{% endif %}{% endfor %}];
      initClientSearch('csInputWeek','csHiddenWeek','csListWeek',CD);
      /* On mobile: snap-scroll to today's day card */
      if(window.innerWidth<=700){
        var wkg=document.querySelector('.week-calendar-grid');
        if(wkg){
          var today=new Date().toISOString().slice(0,10);
          var cards=Array.from(wkg.querySelectorAll('.calendar-day-card'));
          var todayIdx=cards.findIndex(function(card){return card.innerHTML.indexOf(today)!==-1;});
          if(todayIdx>0){wkg.scrollLeft=todayIdx*wkg.offsetWidth;}
        }
      }
    });
    </script>
    {% endif %}
    """, tr=tr, dark=dark, week_days=week_days, worker_days=worker_days, shifts=shifts, worker_colors=worker_colors, client_cities=client_cities, format_date=format_date, holidays_map=holidays_map, day_names=day_names, status_colors=STATUS_COLORS, get_status_label=get_status_label, get_auto_status=get_auto_status, split_workers=split_workers, is_weekend=is_weekend, is_admin=is_admin, prev_week=prev_week, next_week=next_week, current_week=current_week, start_year=start_week.year, start_month=start_week.month, workers=workers, clients=clients, time_hours=time_hours(), selected_week_day=selected_week_day, day_shift_counts=day_shift_counts, day_month_labels=day_month_labels)


@app.route("/month")
def month_view():
    if "user" not in session:
        return redirect("/login")
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"; is_admin = session.get("role") == "admin"; current_user = session.get("user"); copied_shift_id = session.get("copied_shift_id")
    year = request.args.get("year", type=int) or datetime.today().year
    month = request.args.get("month", type=int) or datetime.today().month
    prev_year, prev_month = month_navigation(year, month, -1); next_year, next_month = month_navigation(year, month, 1)
    conn = get_conn(); c = conn.cursor(); worker_colors = get_worker_colors(conn)
    start_date = f"{year:04d}-{month:02d}-01"; end_date = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    shifts = c.execute("SELECT * FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time, id", (start_date, end_date)).fetchall()
    if not is_admin: shifts = [s for s in shifts if worker_in_shift(current_user, s[1])]
    cal = calendar.Calendar(firstweekday=0); month_days = cal.monthdatescalendar(year, month)
    holiday_years = {d.year for wk in month_days for d in wk}; holidays_map = get_all_holidays(conn, holiday_years); clients_raw = c.execute("SELECT name, address FROM clients ORDER BY name").fetchall(); client_cities = client_city_map(clients_raw); clients = clients_raw; workers = c.execute("SELECT name FROM workers ORDER BY name").fetchall(); conn.close()
    shifts_by_date = {}; [shifts_by_date.setdefault(s[3], []).append(s) for s in shifts]
    day_names = [tr["monday"], tr["tuesday"], tr["wednesday"], tr["thursday"], tr["friday"], tr["saturday"], tr["sunday"]]
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
        /* Weekday bar sits OUTSIDE the grid → sticky works properly */
        #monthWdBar {
            position: sticky;
            top: 52px;   /* below fixed topbar on desktop */
            z-index: 50;
            display: grid;
            grid-template-columns: repeat(7, 1fr);
            gap: 10px;
            padding: 0 11px;
            box-sizing: border-box;
            background: {{ '#0c0c0e' if dark else '#eef3fb' }};
            margin-bottom: 4px;
        }
        @media (max-width:600px) { #monthWdBar { top: 0; } }
        @media (max-width:700px) { #monthWdBar { gap:2px; padding:0 11px; } }
        .month-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:10px; align-items:start; }
        @keyframes rgbLed {
            0%   { border-color:#ff2244; box-shadow:0 0 7px 1px #ff2244, 0 8px 18px rgba(0,0,0,0.14); }
            14%  { border-color:#ff8800; box-shadow:0 0 7px 1px #ff8800, 0 8px 18px rgba(0,0,0,0.14); }
            28%  { border-color:#ffe600; box-shadow:0 0 7px 1px #ffe600, 0 8px 18px rgba(0,0,0,0.14); }
            42%  { border-color:#00dd55; box-shadow:0 0 7px 1px #00dd55, 0 8px 18px rgba(0,0,0,0.14); }
            57%  { border-color:#00ccff; box-shadow:0 0 7px 1px #00ccff, 0 8px 18px rgba(0,0,0,0.14); }
            71%  { border-color:#4477ff; box-shadow:0 0 7px 1px #4477ff, 0 8px 18px rgba(0,0,0,0.14); }
            85%  { border-color:#cc00ff; box-shadow:0 0 7px 1px #cc00ff, 0 8px 18px rgba(0,0,0,0.14); }
            100% { border-color:#ff2244; box-shadow:0 0 7px 1px #ff2244, 0 8px 18px rgba(0,0,0,0.14); }
        }
        .month-weekday {
            min-height:auto;
            text-align:center;
            font-weight:bold;
            background:{{ '#1e2124' if dark else '#d9e6f8' }} !important;
            border:2px solid #ff2244;
            color:{{ '#dbeafe' if dark else '#173b63' }};
            animation: rgbLed 1.4s linear infinite;
        }
        #monthWdBar .month-weekday:nth-child(1) { animation-delay:  0.0s; }
        #monthWdBar .month-weekday:nth-child(2) { animation-delay: -0.2s; }
        #monthWdBar .month-weekday:nth-child(3) { animation-delay: -0.4s; }
        #monthWdBar .month-weekday:nth-child(4) { animation-delay: -0.6s; }
        #monthWdBar .month-weekday:nth-child(5) { animation-delay: -0.8s; }
        #monthWdBar .month-weekday:nth-child(6) { animation-delay: -1.0s; }
        #monthWdBar .month-weekday:nth-child(7) { animation-delay: -1.2s; }
    </style>
    <div><a class="back-button" href="/">{{ tr["back"] }}</a><a href="/week">{{ tr["week_calendar"] }}</a><a href="/month_pdf?year={{ year }}&month={{ month }}" target="_blank">{{ tr["month_pdf"] }}</a></div>
    <div style="display:flex; justify-content:space-between; align-items:center; margin:16px 0; gap:12px;"><a href="/month?year={{ prev_year }}&month={{ prev_month }}">{{ tr["prev_month"] }}</a><h2>{{ tr["month_calendar"] }} - {{ format_month_year(year, month) }}</h2><a href="/month?year={{ next_year }}&month={{ next_month }}">{{ tr["next_month"] }}</a></div>
    <div id="monthWdBar">
        {% for dn in day_names %}<div class="card month-weekday">{{ dn }}</div>{% endfor %}
    </div>
    <div class="calendar-board month-grid">
        {% for week in month_days %}{% for day in week %}{% set daystr = day.strftime('%Y-%m-%d') %}{% set holiday_name = holidays_map.get(daystr) %}
            <div class="card calendar-day-card {% if holiday_name %}holiday-soft{% endif %} {% if day.weekday() >= 5 %}weekend-soft{% endif %}" style="min-height:120px; position:relative;" ondragover="allowDrop(event)" ondragleave="clearDrop(event)" ondrop="dropShift(event, '{{ daystr }}')">
                {% if is_admin %}<div class="day-menu-wrapper" style="position:absolute;top:4px;right:4px;"><button onclick="toggleDayMenu(this)" title="{{ tr['add_shift'] }}" style="background:none;border:none;font-size:20px;font-weight:bold;cursor:pointer;padding:2px 5px;line-height:1;width:auto;margin:0;color:{% if dark %}#4ade80{% else %}#1f4f82{% endif %};opacity:{% if dark %}0.9{% else %}0.7{% endif %};" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='{% if dark %}0.9{% else %}0.7{% endif %}'">+</button><div class="day-mini-menu" style="display:none;position:absolute;right:0;top:28px;z-index:300;min-width:155px;border-radius:8px;overflow:hidden;box-shadow:0 4px 18px rgba(0,0,0,0.18);background:{% if dark %}#1d1d1f{% else %}white{% endif %};border:1px solid {% if dark %}#2c2c30{% else %}#dbeafe{% endif %};"><a href="javascript:void(0)" onclick="openAddShiftModal('{{ daystr }}')" style="display:block;padding:10px 15px;text-decoration:none;color:{% if dark %}#93c5fd{% else %}#1f4f82{% endif %};font-size:13px;font-weight:600;white-space:nowrap;" onmouseover="this.style.background='{% if dark %}#2c2c30{% else %}#eef4ff{% endif %}'" onmouseout="this.style.background='transparent'">+ {{ tr['add_shift'] }}</a></div></div>{% endif %}
                <div style="font-weight:bold; margin-bottom:8px;"><a class="month-day-date" data-short="{{ day.strftime('%d') }}" href="{% if is_admin %}javascript:void(0){% else %}/?selected_date={{ daystr }}{% endif %}" {% if is_admin %}onclick="openHolidayModal('{{ daystr }}')"{% endif %} style="{% if day.weekday() >= 5 %}color:#ef4444;{% endif %}">{{ day.strftime('%d/%m/%Y') }}</a>{% if is_admin and copied_shift_id %}<br><a style="display:inline-block;margin-top:6px;padding:4px 7px;border-radius:6px;background:#16a34a;color:white!important;font-size:11px;" href="/paste_shift/{{ daystr }}">{{ tr["paste"] }}</a>{% endif %}</div>
                {% if holiday_name %}<small class="holiday-note">{{ holiday_name }}</small>{% endif %}
                {% for s in shifts_by_date.get(daystr, []) %}<div class="mini-shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="--shift-accent:{{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#7aa7df') }};" data-w="{{ s[1]|e }}" data-c="{{ s[2]|e }}" data-city="{{ client_cities.get(s[2], '')|e }}" data-t="{{ s[4]|e }}"><b>{{ s[1] }}</b><br>{{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}<br>{{ s[4] }}{% if is_admin %}<br><a class="mini-link edit-link" href="javascript:void(0)" data-eid="{{ s[0] }}" data-ew="{{ s[1]|e }}" data-ecl="{{ s[2]|e }}" data-edt="{{ s[3]|e }}" data-etm="{{ s[4]|e }}" data-est="{{ s[5]|e }}" onclick="openEditModalM(this)">{{ tr["edit"] }}</a><a class="mini-link delete-link" href="/delete_shift/{{ s[0] }}">{{ tr["delete"] }}</a><a class="mini-link copy-link" href="/copy_shift/{{ s[0] }}">{{ tr["copy"] }}</a>{% endif %}</div>{% endfor %}
            </div>
        {% endfor %}{% endfor %}
    </div>
    {% if is_admin %}<div id="holidayModal" class="modal-backdrop"><div class="modal-card"><h3>{{ tr["add_holiday"] }}</h3><form method="post" action="/add_holiday"><input type="date" name="date" id="holidayDate" required><input type="text" name="name" placeholder="{{ tr['holiday_name'] }}" required><button>{{ tr["save"] }}</button></form><button type="button" onclick="closeHolidayModal()">{{ tr["cancel"] }}</button></div></div>{% endif %}
    {% if is_admin %}
    <div id="addShiftModal" class="modal-backdrop" style="display:none;">
      <div class="modal-card" style="max-width:400px;width:95%;max-height:90vh;overflow-y:auto;">
        <h3 id="shiftModalTitleM">+ {{ tr['add_shift'] }} — <span id="addShiftModalDate"></span></h3>
        <form method="post" action="/add_shift" id="shiftModalFormM">
          <input type="hidden" name="return_to" id="shiftReturnToM" value="">
          <label>{{ tr['choose_worker'] }}</label>
          {% for w in workers %}{% if w[0] != 'admin' %}<label class="check-row"><input type="checkbox" name="workers" value="{{ w[0] }}">{{ w[0] }}</label>{% endif %}{% endfor %}
          <div class="client-search-wrapper"><input type="text" id="csInputMonth" class="client-search-input" placeholder="{{ tr['search_placeholder'] }}" autocomplete="off"><input type="hidden" name="client" id="csHiddenMonth" required><div class="client-search-dropdown" id="csListMonth"></div></div>
          <input id="addShiftDate" name="date" type="date" required>
          <label>{{ tr['start_time'] }}</label>
          <div style="display:flex;gap:6px;"><select name="start_hour">{% for h in time_hours %}<option value="{{ h }}" {% if h=='07' %}selected{% endif %}>{{ h }}</option>{% endfor %}</select><select name="start_minute"><option value="00" selected>00</option><option value="15">15</option><option value="30">30</option><option value="45">45</option></select></div>
          <label>{{ tr['end_time'] }}</label>
          <div style="display:flex;gap:6px;"><select name="end_hour">{% for h in time_hours %}<option value="{{ h }}" {% if h=='15' %}selected{% endif %}>{{ h }}</option>{% endfor %}</select><select name="end_minute"><option value="00" selected>00</option><option value="15">15</option><option value="30">30</option><option value="45">45</option></select></div>
          <select name="status"><option value="planned">{{ tr['status_planned'] }}</option><option value="in_progress">{{ tr['status_in_progress'] }}</option><option value="done">{{ tr['status_done'] }}</option></select>
          <button type="submit" id="shiftModalSaveBtnM">{{ tr['add_shift'] }}</button>
        </form>
        <button type="button" onclick="closeAddShiftModal()" style="margin-top:8px;width:100%;">{{ tr['cancel'] }}</button>
      </div>
    </div>
    {% endif %}
    <script>
    function openHolidayModal(dateStr){var m=document.getElementById('holidayModal');var d=document.getElementById('holidayDate');if(m&&d){d.value=dateStr;m.style.display='block';}} function closeHolidayModal(){var m=document.getElementById('holidayModal');if(m){m.style.display='none';}}
    function openAddShiftModal(dateStr){
      var form=document.getElementById('shiftModalFormM');
      form.action='/add_shift';
      var rt=document.getElementById('shiftReturnToM');if(rt)rt.value='';
      var titleEl=document.getElementById('shiftModalTitleM');
      if(titleEl)titleEl.innerHTML='+ {{ tr["add_shift"] }} — <span id="addShiftModalDate"></span>';
      document.getElementById('addShiftModalDate').textContent=dateStr;
      var btn=document.getElementById('shiftModalSaveBtnM');if(btn)btn.textContent='{{ tr["add_shift"] }}';
      document.getElementById('addShiftDate').value=dateStr;
      var ci=document.getElementById('csInputMonth');var ch=document.getElementById('csHiddenMonth');
      if(ci)ci.value='';if(ch)ch.value='';
      form.querySelectorAll('input[name="workers"]').forEach(function(cb){cb.checked=false;});
      document.getElementById('addShiftModal').style.display='block';
      document.querySelectorAll('.day-mini-menu').forEach(function(m){m.style.display='none';});
    }
    function openEditModalM(el){
      var id=el.dataset.eid;
      var workers=el.dataset.ew||'';
      var client=el.dataset.ecl||'';
      var date=el.dataset.edt||'';
      var timeRange=el.dataset.etm||'07:00-15:00';
      var status=el.dataset.est||'planned';
      var form=document.getElementById('shiftModalFormM');
      form.action='/edit_shift/'+id;
      var rt=document.getElementById('shiftReturnToM');if(rt)rt.value=window.location.href;
      var titleEl=document.getElementById('shiftModalTitleM');
      if(titleEl)titleEl.textContent='✏️ {{ tr["edit_shift"] }}';
      var btn=document.getElementById('shiftModalSaveBtnM');if(btn)btn.textContent='{{ tr["save"] }}';
      document.getElementById('addShiftDate').value=date;
      var dateSpan=document.getElementById('addShiftModalDate');if(dateSpan)dateSpan.textContent=date;
      var workerList=workers.split(', ');
      form.querySelectorAll('input[name="workers"]').forEach(function(cb){cb.checked=workerList.indexOf(cb.value)>=0;});
      var ci=document.getElementById('csInputMonth');var ch=document.getElementById('csHiddenMonth');
      if(ci)ci.value=client;if(ch)ch.value=client;
      var parts=timeRange.split('-');
      var sp=(parts[0]||'07:00').split(':');var ep=(parts[1]||'15:00').split(':');
      var shS=form.querySelector('select[name="start_hour"]');var smS=form.querySelector('select[name="start_minute"]');
      var shE=form.querySelector('select[name="end_hour"]');var smE=form.querySelector('select[name="end_minute"]');
      if(shS)shS.value=sp[0]||'07';if(smS)smS.value=sp[1]||'00';
      if(shE)shE.value=ep[0]||'15';if(smE)smE.value=ep[1]||'00';
      var stSel=form.querySelector('select[name="status"]');if(stSel)stSel.value=status;
      document.getElementById('addShiftModal').style.display='block';
    }
    function closeAddShiftModal(){document.getElementById('addShiftModal').style.display='none';}
    document.getElementById('addShiftModal') && document.getElementById('addShiftModal').addEventListener('click',function(e){if(e.target===this)closeAddShiftModal();});
    function dragShift(ev, shiftId){ev.dataTransfer.setData('shift_id', shiftId);} function allowDrop(ev){ev.preventDefault();ev.currentTarget.classList.add('drop-target');} function clearDrop(ev){ev.currentTarget.classList.remove('drop-target');}
    function dropShift(ev, dateStr){ev.preventDefault();ev.currentTarget.classList.remove('drop-target');var shiftId=ev.dataTransfer.getData('shift_id');if(!shiftId)return;fetch('/move_shift',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'shift_id='+encodeURIComponent(shiftId)+'&date='+encodeURIComponent(dateStr)}).then(function(resp){if(resp.status===409){return resp.text().then(function(msg){showPlannerAlert(msg);});}window.location.reload();});}
    function toggleDayMenu(btn){var menu=btn.nextElementSibling;document.querySelectorAll('.day-mini-menu').forEach(function(m){if(m!==menu)m.style.display='none';});menu.style.display=menu.style.display==='none'?'block':'none';}
    document.addEventListener('click',function(e){if(!e.target.closest('.day-menu-wrapper')&&!e.target.closest('#addShiftModal .modal-card')){document.querySelectorAll('.day-mini-menu').forEach(function(m){m.style.display='none';});}});
    document.addEventListener('DOMContentLoaded',function(){
      document.querySelectorAll('a.delete-link').forEach(function(link){link.addEventListener('click',function(e){if(!confirm('Da li ste sigurni da želite obrisati?')){e.preventDefault();return false;}});});
      var CD=[{% for cl in clients %}{"name":{{cl[0]|tojson}},"addr":{{(cl[1] or '')|tojson}}}{% if not loop.last %},{% endif %}{% endfor %}];
      initClientSearch('csInputMonth','csHiddenMonth','csListMonth',CD);
      /* Shorten date display to just day number on small screens */
      if(window.innerWidth<=600){
        document.querySelectorAll('a.month-day-date').forEach(function(a){var s=a.getAttribute('data-short');if(s)a.textContent=s;});
        /* Compact mini-shift: radnik / 1. riječ klijenta / vrijeme */
        document.querySelectorAll('.month-grid .mini-shift').forEach(function(el){
          var w=(el.getAttribute('data-w')||'').trim();
          var c=el.getAttribute('data-c')||'';
          var city=el.getAttribute('data-city')||'';
          var t=el.getAttribute('data-t')||'';
          var cHtml=c?(c+(city?' <span class="ms-city">'+city+'</span>':'')):'';
          el.innerHTML=
            (w?'<div class="ms-w">'+w+'</div>':'')+
            (cHtml?'<div class="ms-c">'+cHtml+'</div>':'')+
            (t?'<div class="ms-t">'+t+'</div>':'');
        });
      }
    });
    </script>
    """, tr=tr, dark=dark, year=year, month=month, prev_year=prev_year, prev_month=prev_month, next_year=next_year, next_month=next_month, month_days=month_days, day_names=day_names, shifts_by_date=shifts_by_date, worker_colors=worker_colors, client_cities=client_cities, holidays_map=holidays_map, is_admin=is_admin, copied_shift_id=copied_shift_id, get_auto_status=get_auto_status, split_workers=split_workers, format_month_year=format_month_year, workers=workers, clients=clients, time_hours=time_hours())



@app.route("/route_optimizer", methods=["GET", "POST"])
def route_optimizer():
    if "user" not in session:
        return redirect("/login")
    tr = t()
    dark = get_theme() == "dark"
    is_admin = session.get("role") == "admin"
    current_user = session.get("user")

    conn = get_conn()
    c = conn.cursor()
    workers = c.execute("SELECT name, address, contract_type, contract_end_date FROM workers ORDER BY name").fetchall()
    worker_lookup = {w[0]: w[1] for w in workers}

    selected_date = request.values.get("date", lux_now().strftime("%Y-%m-%d")).strip()
    if is_admin:
        selected_worker = request.values.get("worker", "").strip()
    else:
        selected_worker = current_user
    default_start_address = worker_lookup.get(selected_worker) or "Wiltz, Luxembourg"
    start_address = request.values.get("start_address", default_start_address).strip()
    result = None
    error = ""

    if request.method == "POST":
        if not os.environ.get("ORS_API_KEY", "").strip():
            error = tr["api_missing"]
        elif not start_address:
            error = tr["missing_address"] + ": " + tr["start_address"]
        elif not selected_worker:
            error = tr["choose_worker"]
        else:
            shifts = c.execute("SELECT * FROM shifts WHERE date = ? ORDER BY time, id", (selected_date,)).fetchall()
            shifts = [s for s in shifts if worker_in_shift(selected_worker, s[1])]

            if not shifts:
                error = tr["no_route_shifts"]
            else:
                start_coords = get_coords(start_address)
                if not start_coords:
                    error = f"{tr['geocode_failed']}: {start_address}"
                else:
                    stops = []
                    bad_addresses = []
                    for s in shifts:
                        client_name = s[2]
                        client = c.execute("SELECT name, address FROM clients WHERE name = ?", (client_name,)).fetchone()
                        client_address = client[1] if client and len(client) > 1 else ""
                        if not client_address:
                            bad_addresses.append(f"{client_name} ({tr['missing_address']})")
                            continue
                        coords = get_coords(client_address)
                        if not coords:
                            bad_addresses.append(f"{client_name} - {client_address}")
                            continue
                        stops.append({
                            "client": client_name,
                            "address": client_address,
                            "time": s[4],
                            "coords": coords,
                            "maps_url": google_maps_navigation_url(client_address),
                        })

                    if not stops:
                        error = tr["geocode_failed"] + ": " + ", ".join(bad_addresses)
                    else:
                        ordered, total_km = optimize_nearest_neighbor(start_coords, stops)
                        # Sort by shift start time (chronological order)
                        ordered = sorted(ordered, key=lambda s: (s.get("time") or "").split("-")[0])
                        result = {
                            "ordered": ordered,
                            "total_km": total_km,
                            "bad_addresses": bad_addresses,
                            "maps_url": google_maps_directions_url(start_address, ordered),
                            "embed_url": google_maps_embed_url(start_address, ordered),
                        }
    conn.close()

    return render_template_string(BASE_STYLE + header_html() + """
    <h1>{{ tr["route_title"] }}</h1>
    <a class="back-button" href="/">{{ tr["back"] }}</a>
    <div class="card" style="margin-top:16px; max-width:900px;">
        <p class="muted">{{ tr["route_desc"] }}</p>
        <form method="post">
            <label>{{ tr["pdf_date"] }}</label>
            <input type="date" name="date" value="{{ selected_date }}" required>
            {% if is_admin %}
                <label>{{ tr["choose_worker"] }}</label>
                <select name="worker" required>
                    <option value="">{{ tr["choose_worker"] }}</option>
                    {% for w in workers %}
                        {% if w[0] != 'admin' %}<option value="{{ w[0] }}" {% if selected_worker == w[0] %}selected{% endif %}>{{ w[0] }}</option>{% endif %}
                    {% endfor %}
                </select>
            {% else %}
                <label>{{ tr["pdf_worker"] }}</label>
                <input value="{{ selected_worker }}" readonly>
            {% endif %}
            <label>{{ tr["start_address"] }}</label>
            <input name="start_address" value="{{ start_address }}" placeholder="{{ tr['start_address_help'] }}" required>
            <button>{{ tr["optimize_route"] }}</button>
        </form>
        {% if error %}<div style="margin-top:15px; color:#ef4444; font-weight:bold;">{{ error }}</div>{% endif %}
    </div>

    {% if result %}
    <div class="card" style="margin-top:16px; max-width:900px;">
        <h2>{{ tr["optimized_order"] }}</h2>
        <p><b>{{ tr["route_distance_return"] }}:</b> {{ "%.1f"|format(result.total_km) }} km</p>
        <ol style="padding-left:22px; margin:0;">
            {% for stop in result.ordered %}
                <li style="margin-bottom:14px; padding:10px 12px; border-radius:10px; background:{{ '#1d1d1f' if dark else '#f8fafc' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; list-style-position:outside;">
                    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
                        <div style="min-width:0;">
                            <div style="font-weight:700; font-size:15px;">{{ stop.client }}</div>
                            <div style="font-size:13px; color:{{ '#93c5fd' if dark else '#1f4f82' }}; font-weight:600; margin:2px 0;">🕐 {{ stop.time }}</div>
                            <div class="muted" style="font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">📍 {{ stop.address }}</div>
                        </div>
                        <a href="{{ stop.maps_url }}" target="_blank" title="{{ tr['navigate_to_address'] }}"
                           style="flex-shrink:0; display:flex; align-items:center; justify-content:center; width:48px; height:48px; border-radius:12px; background:#1f4f82; color:white; font-size:24px; text-decoration:none; box-shadow:0 2px 8px rgba(31,79,130,0.35);">🧭</a>
                    </div>
                </li>
            {% endfor %}
        </ol>
        {% if result.bad_addresses %}
            <div style="color:#f59e0b; font-weight:bold; margin:12px 0;">
                {{ tr["geocode_failed"] }}:<br>
                {% for bad in result.bad_addresses %}• {{ bad }}<br>{% endfor %}
            </div>
        {% endif %}
        <p class="muted" style="margin-top:12px;">{{ tr["route_warning"] }}</p>
    </div>
    {% endif %}
    """, tr=tr, dark=dark, workers=workers, selected_date=selected_date,
       selected_worker=selected_worker, start_address=start_address, result=result, error=error, is_admin=is_admin)


def document_row(row):
    return {
        "id": row[0], "original_name": document_display_name(row[1]) or safe_document_name(row[1]),
        "stored_name": row[2], "mime_type": row[3] or "",
        "file_size": int(row[4] or 0), "category": row[5] or "other", "folder_id": row[6],
        "note": row[7] or "", "uploaded_at": row[8] or "", "uploaded_by": row[9] or "",
    }


def get_document_record(conn, document_id):
    row = conn.cursor().execute("""
        SELECT id, original_name, stored_name, mime_type, file_size, category, folder_id, note, uploaded_at, uploaded_by
        FROM documents WHERE id = ?
    """, (document_id,)).fetchone()
    return document_row(row) if row else None


def get_shared_document(conn, token):
    row = conn.cursor().execute("""
        SELECT d.id, d.original_name, d.stored_name, d.mime_type, d.file_size, d.category, d.folder_id, d.note, d.uploaded_at, d.uploaded_by,
               s.expires_at, s.allow_download, s.revoked
        FROM document_shares s
        JOIN documents d ON d.id = s.document_id
        WHERE s.token = ?
    """, (token,)).fetchone()
    if not row or not share_is_active(row[10] or "", row[12]):
        return None
    document = document_row(row[:10])
    document["expires_at"] = row[10] or ""
    document["allow_download"] = bool(row[11])
    return document


@app.route("/documents")
def documents():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    dark = get_theme() == "dark"
    query = request.args.get("q", "").strip().lower()
    category = request.args.get("category", "").strip()
    view = request.args.get("view", "all").strip()
    folder_id = document_parent_id(request.args.get("folder"))
    conn = get_conn()
    current_folder = None
    if folder_id:
        current_folder = conn.cursor().execute("SELECT id, name, parent_id FROM document_folders WHERE id = ?", (folder_id,)).fetchone()
        if not current_folder:
            folder_id = None
    folder_rows = []
    if not query and view in ("all", "folders"):
        if folder_id:
            folder_rows = conn.cursor().execute("""
                SELECT id, name, parent_id, created_at, created_by
                FROM document_folders WHERE parent_id = ? ORDER BY name
            """, (folder_id,)).fetchall()
        elif view == "folders":
            folder_rows = conn.cursor().execute("""
                SELECT id, name, parent_id, created_at, created_by
                FROM document_folders ORDER BY name
            """).fetchall()
        else:
            folder_rows = conn.cursor().execute("""
                SELECT id, name, parent_id, created_at, created_by
                FROM document_folders WHERE parent_id IS NULL ORDER BY name
            """).fetchall()
    rows = conn.cursor().execute("""
        SELECT id, original_name, stored_name, mime_type, file_size, category, folder_id, note, uploaded_at, uploaded_by
        FROM documents ORDER BY uploaded_at DESC, id DESC
    """).fetchall()
    docs = []
    for row in rows:
        document = document_row(row)
        haystack = f"{document['original_name']} {document['category']} {document['note']} {document['uploaded_by']}".lower()
        if query and query not in haystack:
            continue
        if not query and folder_id and document["folder_id"] != folder_id:
            continue
        if not query and not folder_id and view == "all" and document["folder_id"] is not None:
            continue
        if category and document["category"] != category:
            continue
        if view == "pdf" and not document["mime_type"].startswith("application/pdf") and not document["original_name"].lower().endswith(".pdf"):
            continue
        if view == "images" and not document["mime_type"].startswith("image/"):
            continue
        if view == "folders":
            continue
        docs.append(document)
    breadcrumbs = folder_breadcrumb(conn, folder_id)
    total_size = sum(row[0] or 0 for row in conn.cursor().execute("SELECT file_size FROM documents").fetchall())
    share_rows = conn.cursor().execute("""
        SELECT token, document_id, created_at, expires_at, allow_download, revoked
        FROM document_shares ORDER BY created_at DESC
    """).fetchall()
    folder_share_rows = conn.cursor().execute("""
        SELECT token, folder_id, created_at, expires_at, revoked
        FROM folder_shares ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    shares_by_document = {}
    for token, document_id, created_at, expires_at, allow_download, revoked in share_rows:
        if not share_is_active(expires_at or "", revoked):
            continue
        shares_by_document.setdefault(document_id, []).append({
            "token": token,
            "created_at": created_at or "",
            "expires_at": expires_at or "",
            "allow_download": bool(allow_download),
            "url": url_for("shared_document", token=token, _external=True),
        })
    shares_by_folder = {}
    for token, fid, created_at, expires_at, revoked in folder_share_rows:
        if not share_is_active(expires_at or "", revoked):
            continue
        shares_by_folder.setdefault(fid, []).append({
            "token": token,
            "created_at": created_at or "",
            "expires_at": expires_at or "",
            "url": url_for("shared_folder", token=token, _external=True),
        })
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
        .file-manager { display:grid; grid-template-columns:245px minmax(0,1fr); min-height:72vh; overflow:hidden; border-radius:12px; background:{{ '#161618' if dark else 'white' }}; box-shadow:0 4px 14px rgba(0,0,0,0.08); }
        .file-nav { border-right:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; padding:18px 12px; display:flex; flex-direction:column; gap:6px; }
        .file-nav a { display:flex; align-items:center; gap:10px; padding:11px 12px; border-radius:10px; margin:0; color:inherit; font-weight:600; }
        .file-nav a.active { background:{{ '#1d3557' if dark else '#e8f1ff' }}; color:{{ '#bfdbfe' if dark else '#2563eb' }}; }
        .file-main { padding:26px 30px; min-width:0; }
        .file-head { display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; }
        .file-search { width:min(520px,100%); display:flex; gap:8px; border:1px solid {{ '#2c2c30' if dark else '#dbe3ee' }}; border-radius:999px; padding:2px 10px; background:{{ '#111113' if dark else '#f4f6f8' }}; }
        .file-search input { border:0; box-shadow:none; background:transparent; margin:0; }
        .file-search button { width:auto; border-radius:999px; margin:4px 0; padding:8px 12px; }
        .file-toolbar { display:flex; gap:9px; align-items:center; flex-wrap:wrap; margin:18px 0; }
        .toolbar-button, .toolbar-link { width:auto; display:inline-flex; align-items:center; gap:8px; margin:0; padding:11px 14px; border-radius:9px; }
        .toolbar-link { border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }}; color:inherit; }
        .upload-drawer { display:none; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; margin-bottom:16px; }
        .upload-drawer.open { display:grid; }
        .upload-panel { padding:14px; border-radius:10px; border:1px solid {{ '#2c2c30' if dark else '#dbe3ee' }}; background:{{ '#191919' if dark else '#f8fbff' }}; }
        .breadcrumb { display:flex; gap:7px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
        .breadcrumb a { margin:0; } .storage-meter { margin-top:auto; padding:18px 10px 4px; }
        .storage-track { height:7px; border-radius:999px; background:{{ '#2c2c30' if dark else '#e5e7eb' }}; overflow:hidden; margin:8px 0; }
        .storage-fill { height:100%; min-width:4px; background:#3b82f6; width:{{ storage_percent }}%; }
        .document-table { width:100%; border-collapse:collapse; }
        .document-table th, .document-table td { padding:6px 8px; border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; text-align:left; vertical-align:middle; }
        .document-table th { font-size:12px; text-transform:uppercase; color:{{ '#cbd5e1' if dark else '#475569' }}; }
        .file-name { display:flex; gap:12px; align-items:center; min-width:250px; }
        .file-icon { display:inline-grid; place-items:center; width:32px; height:32px; border-radius:8px; font-size:20px; background:{{ '#1d1d1f' if dark else '#eff6ff' }}; }
        .file-row:hover { background:{{ '#191919' if dark else '#f8fbff' }}; }
        .document-actions { display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }
        .document-actions a, .document-actions button { width:auto; margin:0; padding:4px 8px; font-size:11px; }
        .folder-actions { display:flex; align-items:center; justify-content:flex-end; gap:6px; }
        .share-toggle { background:{{ '#1e3a5f' if dark else '#e8f1ff' }} !important; color:{{ '#93c5fd' if dark else '#2563eb' }} !important; border:1px solid {{ '#2c2c30' if dark else '#bfdbfe' }} !important; font-size:13px !important; padding:3px 7px !important; line-height:1; }
        .share-inline-form { display:none; gap:6px; align-items:center; margin-top:4px; flex-wrap:wrap; }
        .folder-delete-button { color:#dc2626!important; border:1px solid {{ '#7f1d1d' if dark else '#fecaca' }}!important; background:{{ '#2f1519' if dark else '#fff1f2' }}!important; min-width:36px; min-height:36px; padding:6px!important; font-size:18px!important; line-height:1; }
        .share-row { padding:8px; margin-top:7px; border-radius:8px; background:{{ '#191919' if dark else '#eef5ff' }}; }
        .share-row input { margin:0 0 5px; font-size:12px; }
        .inline-form { display:inline; } .inline-form button { display:inline-block; }
        @media (max-width:900px) { .file-manager { grid-template-columns:1fr; } .file-nav { border-right:0; border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; } .file-main { padding:18px; } .document-table { display:block; overflow-x:auto; } }
    </style>
    <h1>{{ tr["documents"] }}</h1><a class="back-button" href="/">{{ tr["back"] }}</a>
    <div class="file-manager" style="margin-top:16px;">
        <aside class="file-nav">
            <a class="{% if view == 'all' and not category %}active{% endif %}" href="/documents">&#128193; {{ tr["all_files"] }}</a>
            <a class="{% if view == 'folders' %}active{% endif %}" href="/documents?view=folders">&#128193; {{ tr["folders"] }}</a>
            <a class="{% if view == 'pdf' %}active{% endif %}" href="/documents?view=pdf">&#128196; {{ tr["pdf_documents"] }}</a>
            <a class="{% if view == 'images' %}active{% endif %}" href="/documents?view=images">&#128444; {{ tr["images"] }}</a>
            {% for key, label in categories %}<a class="{% if category == key %}active{% endif %}" href="/documents?category={{ key }}">&#9679; {{ label }}</a>{% endfor %}
            <div class="storage-meter">
                <small>{{ size_label(total_size) }} / 10 GB</small>
                <div class="storage-track"><div class="storage-fill"></div></div>
            </div>
        </aside>
        <section class="file-main">
            <div class="file-head">
                <div><h2 style="margin:0;">{{ current_folder[1] if current_folder else tr["all_files"] }}</h2></div>
                <form class="file-search" method="get">
                    {% if folder_id %}<input type="hidden" name="folder" value="{{ folder_id }}">{% endif %}
                    {% if view != 'all' %}<input type="hidden" name="view" value="{{ view }}">{% endif %}
                    {% if category %}<input type="hidden" name="category" value="{{ category }}">{% endif %}
                    <input name="q" value="{{ query }}" placeholder="{{ tr['document_search'] }}">
                    <button aria-label="{{ tr['document_search'] }}">&#128269;</button>
                </form>
            </div>
            <div class="file-toolbar">
                <button class="toolbar-button" type="button" onclick="document.getElementById('uploadDrawer').classList.toggle('open');">&#8679; {{ tr["upload_documents"] }}</button>
                <button class="toolbar-button" type="button" onclick="document.getElementById('newFolderForm').style.display='flex';">+ {{ tr["new_folder"] }}</button>
                <a class="toolbar-link" href="/documents">&#8635;</a>
                <form class="inline-form" method="post" action="/documents/cleanup" onsubmit="return confirm({{ tr['cleanup_confirm']|tojson }});"><button class="toolbar-button" type="submit" style="background:#7f1d1d;color:#fca5a5;border:none;">&#128465; {{ tr['cleanup_orphans'] }}</button></form>
                <form id="newFolderForm" method="post" action="/documents/folder" style="display:none;gap:6px;align-items:center;">
                    {% if folder_id %}<input type="hidden" name="parent_id" value="{{ folder_id }}">{% endif %}
                    <input name="name" placeholder="{{ tr['folder_name'] }}" required>
                    <button style="width:auto;">{{ tr["save"] }}</button>
                </form>
            </div>
            <div class="breadcrumb">
                <a href="/documents">{{ tr["root_folder"] }}</a>{% for crumb in breadcrumbs %}<span>/</span><a href="/documents?folder={{ crumb.id }}">{{ crumb.name }}</a>{% endfor %}
            </div>
            <div id="uploadDrawer" class="upload-drawer">
                <form class="upload-panel" method="post" action="/documents/upload" enctype="multipart/form-data">
                    {% if folder_id %}<input type="hidden" name="folder_id" value="{{ folder_id }}">{% endif %}
                    <h3>{{ tr["single_document"] }}</h3>
                    <input type="file" name="file" required accept=".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx,.xls,.xlsx,.csv,.txt">
                    <input name="display_name" placeholder="{{ tr['document_name'] }}">
                    <select name="category">{% for key, label in categories %}<option value="{{ key }}">{{ label }}</option>{% endfor %}</select>
                    <textarea name="note" placeholder="{{ tr['document_note'] }}" style="width:100%;min-height:60px;"></textarea>
                    <button>{{ tr["upload_document"] }}</button>
                </form>
                <form id="folderUploadForm" class="upload-panel" method="post" action="/documents/upload" enctype="multipart/form-data" onsubmit="syncFolderUploadPaths();">
                    {% if folder_id %}<input type="hidden" name="folder_id" value="{{ folder_id }}">{% endif %}
                    <h3>{{ tr["folder_documents"] }}</h3>
                    <label>{{ tr["multiple_documents"] }}</label><input type="file" name="files" multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx,.xls,.xlsx,.csv,.txt">
                    <label>{{ tr["folder_documents"] }}</label><input id="folderFilesInput" type="file" name="folder_files" webkitdirectory directory multiple onchange="syncFolderUploadPaths();">
                    <div id="folderPathFields"></div>
                    <select name="category">{% for key, label in categories %}<option value="{{ key }}">{{ label }}</option>{% endfor %}</select>
                    <textarea name="note" placeholder="{{ tr['document_note'] }}" style="width:100%;min-height:60px;"></textarea>
                    <button>{{ tr["upload_documents"] }}</button>
                </form>
            </div>
            <p class="muted">PDF, JPG, PNG, WEBP, Word, Excel, CSV, TXT. Max {{ max_upload_mb }} MB.</p>
            <table class="document-table">
                <tr><th>{{ tr["document_name"] }}</th><th>{{ tr["addition_time"] }}</th><th>{{ tr["file_size"] }}</th><th></th></tr>
                {% for folder in folders %}
                <tr class="file-row">
                    <td><a class="file-name" href="/documents?folder={{ folder[0] }}"><span class="file-icon">&#128193;</span><b>{{ folder[1] }}</b></a></td>
                    <td>{{ folder[3][8:10] }}.{{ folder[3][5:7] }}.{{ folder[3][0:4] }}</td><td>-</td>
                    <td>
                        <div class="folder-actions">
                            <a href="/documents?folder={{ folder[0] }}" style="font-size:11px;padding:4px 8px;">{{ tr["open_folder"] }}</a>
                            <button type="button" class="share-toggle" onclick="var f=document.getElementById('fsf{{ folder[0] }}');f.style.display=f.style.display==='flex'?'none':'flex';" title="{{ tr['create_share_link'] }}">&#128279;</button>
                            <form class="inline-form" method="post" action="/documents/folder/delete/{{ folder[0] }}" onsubmit='return confirm({{ tr["delete_folder_confirm"]|tojson }});'>
                                <button class="folder-delete-button" type="submit" title="{{ tr["delete_folder"] }}" aria-label="{{ tr["delete_folder"] }}">&#128465;</button>
                            </form>
                        </div>
                        <form id="fsf{{ folder[0] }}" class="share-inline-form" method="post" action="/documents/folder/share/{{ folder[0] }}">
                            {% if folder_id %}<input type="hidden" name="parent_id" value="{{ folder_id }}">{% endif %}
                            <select name="days" style="width:auto;font-size:11px;padding:3px 4px;">
                                <option value="7">7 {{ tr["expires_days"] }}</option>
                                <option value="30">30 {{ tr["expires_days"] }}</option>
                                <option value="1">1 {{ tr["expires_days"] }}</option>
                                <option value="0">{{ tr["expires_never"] }}</option>
                            </select>
                            <button style="width:auto;font-size:11px;padding:4px 8px;">{{ tr["create_share_link"] }}</button>
                        </form>
                        {% for share in shares_by_folder.get(folder[0], []) %}
                        <div class="share-row">
                            <small>{{ tr["share_link"] }}{% if share.expires_at %} | {{ tr["expires_in"] }} {{ share.expires_at[8:10] }}.{{ share.expires_at[5:7] }}.{{ share.expires_at[0:4] }}{% endif %}</small>
                            <input value="{{ share.url }}" readonly onclick="this.select();">
                            <form class="inline-form" method="post" action="/documents/folder/share/revoke/{{ share.token }}"><button>{{ tr["revoke_link"] }}</button></form>
                        </div>
                        {% endfor %}
                    </td>
                </tr>
                {% endfor %}
                {% for doc in docs %}
                <tr class="file-row">
                    <td><a class="file-name" href="/documents/view/{{ doc.id }}"><span class="file-icon">{% if doc.mime_type.startswith('image/') %}&#128444;{% else %}&#128196;{% endif %}</span><span><b>{{ doc.original_name }}</b>{% if doc.note %}<br><small class="muted">{{ doc.note }}</small>{% endif %}</span></a></td>
                    <td style="white-space:nowrap;">{{ doc.uploaded_at[8:10] }}.{{ doc.uploaded_at[5:7] }}.{{ doc.uploaded_at[0:4] }}</td>
                    <td style="white-space:nowrap;">{{ size_label(doc.file_size) }}</td>
                    <td>
                        <div class="document-actions">
                            <a href="/documents/view/{{ doc.id }}">{{ tr["preview"] }}</a>
                            <a href="/documents/file/{{ doc.id }}?download=1">{{ tr["download"] }}</a>
                            <button type="button" class="share-toggle" onclick="var f=document.getElementById('sf{{ doc.id }}');f.style.display=f.style.display==='flex'?'none':'flex';" title="{{ tr['create_share_link'] }}">&#128279;</button>
                            <form class="inline-form" method="post" action="/documents/delete/{{ doc.id }}" onsubmit="return confirm({{ tr['doc_delete_confirm']|tojson }});"><button>{{ tr["delete"] }}</button></form>
                        </div>
                        <form id="sf{{ doc.id }}" class="share-inline-form" method="post" action="/documents/share/{{ doc.id }}">
                            <select name="days" style="width:auto;font-size:11px;padding:3px 4px;">
                                <option value="7">7 {{ tr["expires_days"] }}</option>
                                <option value="30">30 {{ tr["expires_days"] }}</option>
                                <option value="1">1 {{ tr["expires_days"] }}</option>
                                <option value="0">{{ tr["expires_never"] }}</option>
                            </select>
                            <button style="width:auto;font-size:11px;padding:4px 8px;">{{ tr["create_share_link"] }}</button>
                        </form>
                        {% for share in shares_by_document.get(doc.id, []) %}
                        <div class="share-row">
                            <small>{{ tr["share_link"] }}{% if share.expires_at %} | {{ tr["expires_in"] }} {{ share.expires_at[8:10] }}.{{ share.expires_at[5:7] }}.{{ share.expires_at[0:4] }}{% endif %}</small>
                            <input value="{{ share.url }}" readonly onclick="this.select();">
                            <form class="inline-form" method="post" action="/documents/share/revoke/{{ share.token }}"><button>{{ tr["revoke_link"] }}</button></form>
                        </div>
                        {% endfor %}
                    </td>
                </tr>
                {% endfor %}
            </table>
            {% if docs|length == 0 and folders|length == 0 %}<p class="muted">{{ tr["document_missing"] }}</p>{% endif %}
        </section>
    </div>
    <script>
    window.syncFolderUploadPaths = function() {
        const fileInput = document.getElementById('folderFilesInput');
        const pathFields = document.getElementById('folderPathFields');
        if (!fileInput || !pathFields) return;
        pathFields.replaceChildren();
        Array.from(fileInput.files || []).forEach((file) => {
            const relativePath = file.webkitRelativePath || file.name;
            const field = document.createElement('input');
            field.type = 'hidden';
            field.name = 'folder_paths';
            field.value = relativePath;
            pathFields.appendChild(field);
        });
    };
    </script>
    """, tr=tr, dark=dark, docs=docs, folders=folder_rows, query=query, category=category, view=view,
       folder_id=folder_id, current_folder=current_folder, breadcrumbs=breadcrumbs, total_size=total_size,
       storage_percent=min(100, round((total_size / (10 * 1024 * 1024 * 1024)) * 100, 2)),
       categories=document_categories(tr),
       category_labels=dict(document_categories(tr)), shares_by_document=shares_by_document,
       shares_by_folder=shares_by_folder,
       size_label=document_size_label, max_upload_mb=app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024))


@app.route("/documents/cleanup", methods=["POST"])
def documents_cleanup():
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    rows = conn.cursor().execute(
        "SELECT id, stored_name FROM documents").fetchall()
    deleted = 0
    for doc_id, stored_name in rows:
        path = document_path(stored_name)
        if not os.path.exists(path):
            conn.cursor().execute(
                "DELETE FROM document_shares WHERE document_id = ?", (doc_id,))
            conn.cursor().execute(
                "DELETE FROM documents WHERE id = ?", (doc_id,))
            deleted += 1
    conn.commit()
    conn.close()
    return redirect(f"/documents?notice={urllib.parse.quote(f'Obrisano {deleted} izgubljenih zapisa.')}")


@app.route("/documents/upload", methods=["POST"])
def documents_upload():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    uploads = []
    single_upload = request.files.get("file")
    if single_upload and single_upload.filename:
        uploads.append((single_upload, request.form.get("display_name", "").strip() or single_upload.filename, None))
    for upload in request.files.getlist("files"):
        if upload and upload.filename:
            uploads.append((upload, upload.filename, None))
    folder_paths = request.form.getlist("folder_paths")
    for index, upload in enumerate(request.files.getlist("folder_files")):
        if upload and upload.filename:
            relative_path = folder_paths[index] if index < len(folder_paths) else upload.filename
            uploads.append((upload, upload.filename, relative_path))
    if not uploads:
        return redirect("/documents?notice=" + urllib.parse.quote(tr["document_upload_error"]))
    category_keys = {key for key, _ in document_categories(tr)}
    category = request.form.get("category", "other").strip()
    if category not in category_keys:
        category = "other"
    note = request.form.get("note", "").strip()
    conn = get_conn()
    folder_id = document_parent_id(request.form.get("folder_id"))
    if folder_id and not conn.cursor().execute("SELECT id FROM document_folders WHERE id = ?", (folder_id,)).fetchone():
        folder_id = None
    saved_count = 0
    skipped_count = 0
    for upload, original_name, relative_path in uploads:
        upload_folder_id = uploaded_document_folder(conn, folder_id, relative_path) if relative_path else folder_id
        if save_uploaded_document(conn, upload, original_name, category, note, upload_folder_id):
            saved_count += 1
        else:
            skipped_count += 1
    if saved_count:
        conn.commit()
    conn.close()
    if not saved_count:
        return redirect("/documents?notice=" + urllib.parse.quote(tr["file_type_error"]))
    notice = f"{tr['documents_uploaded']}: {saved_count}"
    if skipped_count:
        notice += f". {tr['documents_skipped']}: {skipped_count}"
    redirect_url = f"/documents?folder={folder_id}" if folder_id else "/documents"
    return redirect(redirect_url + ("&" if "?" in redirect_url else "?") + "notice=" + urllib.parse.quote(notice))


@app.route("/documents/folder", methods=["POST"])
def documents_folder_create():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    name = request.form.get("name", "").strip()
    parent_id = document_parent_id(request.form.get("parent_id"))
    conn = get_conn()
    c = conn.cursor()
    if parent_id and not c.execute("SELECT id FROM document_folders WHERE id = ?", (parent_id,)).fetchone():
        parent_id = None
    redirect_url = f"/documents?folder={parent_id}" if parent_id else "/documents"
    if not name:
        conn.close()
        return redirect(redirect_url)
    clean_name = re.sub(r"\s+", " ", name).strip()[:140]
    existing = c.execute(
        "SELECT id FROM document_folders WHERE name = ? AND " + ("parent_id = ?" if parent_id else "parent_id IS NULL"),
        (clean_name, parent_id) if parent_id else (clean_name,),
    ).fetchone()
    if existing:
        conn.close()
        return redirect(redirect_url + ("&" if "?" in redirect_url else "?") + "notice=" + urllib.parse.quote(tr["folder_exists"]))
    c.execute("""
        INSERT INTO document_folders (name, parent_id, created_at, created_by)
        VALUES (?, ?, ?, ?)
    """, (clean_name, parent_id, lux_now().strftime("%Y-%m-%d %H:%M:%S"), session.get("user", "")))
    conn.commit()
    conn.close()
    return redirect(redirect_url + ("&" if "?" in redirect_url else "?") + "notice=" + urllib.parse.quote(tr["folder_created"]))


@app.route("/documents/folder/delete/<int:folder_id>", methods=["POST"])
def documents_folder_delete(folder_id):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    c = conn.cursor()
    folder = c.execute("SELECT id, parent_id FROM document_folders WHERE id = ?", (folder_id,)).fetchone()
    if not folder:
        conn.close()
        return redirect("/documents")
    parent_id = document_parent_id(folder[1])
    folder_ids = document_folder_tree_ids(conn, folder_id)
    for current_id in folder_ids:
        rows = c.execute("""
            SELECT id, original_name, stored_name, mime_type, file_size, category, folder_id, note, uploaded_at, uploaded_by
            FROM documents WHERE folder_id = ?
        """, (current_id,)).fetchall()
        for row in rows:
            document = document_row(row)
            path = document_path(document["stored_name"])
            if os.path.exists(path):
                os.remove(path)
            c.execute("DELETE FROM document_shares WHERE document_id = ?", (document["id"],))
            c.execute("DELETE FROM documents WHERE id = ?", (document["id"],))
    for current_id in reversed(folder_ids):
        c.execute("DELETE FROM folder_shares WHERE folder_id = ?", (current_id,))
        c.execute("DELETE FROM document_folders WHERE id = ?", (current_id,))
    conn.commit()
    conn.close()
    return redirect(f"/documents?folder={parent_id}" if parent_id else "/documents")


@app.route("/documents/view/<int:document_id>")
def documents_view(document_id):
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    dark = get_theme() == "dark"
    conn = get_conn()
    document = get_document_record(conn, document_id)
    conn.close()
    if not document or not os.path.exists(document_path(document["stored_name"])):
        return redirect("/documents?notice=" + urllib.parse.quote(tr["document_missing"]))
    return render_template_string(BASE_STYLE + header_html() + """
    <h1>{{ doc.original_name }}</h1>
    <a class="back-button" href="/documents">{{ tr["back"] }}</a>
    <a class="pdf-link" href="/documents/file/{{ doc.id }}?download=1">{{ tr["download"] }}</a>
    <div class="card" style="margin-top:16px;">
        {% if inline %}
            <iframe src="/documents/file/{{ doc.id }}" title="{{ doc.original_name }}" style="width:100%;height:78vh;border:0;background:white;border-radius:8px;"></iframe>
        {% else %}
            <p>{{ tr["open_document"] }}: <a href="/documents/file/{{ doc.id }}?download=1">{{ doc.original_name }}</a></p>
        {% endif %}
    </div>
    """, tr=tr, dark=dark, doc=document, inline=document_inline_allowed(document["mime_type"]))


@app.route("/documents/file/<int:document_id>")
def documents_file(document_id):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    document = get_document_record(conn, document_id)
    conn.close()
    if not document or not os.path.exists(document_path(document["stored_name"])):
        return ("Not found", 404)
    download = request.args.get("download") == "1" or not document_inline_allowed(document["mime_type"])
    return send_file(document_path(document["stored_name"]), as_attachment=download, download_name=document["original_name"], mimetype=document["mime_type"] or None)


@app.route("/documents/delete/<int:document_id>", methods=["POST"])
def documents_delete(document_id):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    document = get_document_record(conn, document_id)
    if document:
        path = document_path(document["stored_name"])
        if os.path.exists(path):
            os.remove(path)
        conn.cursor().execute("DELETE FROM document_shares WHERE document_id = ?", (document_id,))
        conn.cursor().execute("DELETE FROM documents WHERE id = ?", (document_id,))
        conn.commit()
    conn.close()
    return redirect("/documents")


@app.route("/documents/share/<int:document_id>", methods=["POST"])
def documents_share(document_id):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    document = get_document_record(conn, document_id)
    if not document or not os.path.exists(document_path(document["stored_name"])):
        conn.close()
        return redirect("/documents?notice=" + urllib.parse.quote(t()["document_missing"]))
    token = secrets.token_urlsafe(32)
    conn.cursor().execute("""
        INSERT INTO document_shares (token, document_id, created_at, expires_at, allow_download, revoked)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (token, document_id, lux_now().strftime("%Y-%m-%d %H:%M:%S"), share_expiry(request.form.get("days", "7")), 1, 0))
    conn.commit()
    conn.close()
    return redirect("/documents")


@app.route("/documents/share/revoke/<token>", methods=["POST"])
def documents_share_revoke(token):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    conn.cursor().execute("UPDATE document_shares SET revoked = 1 WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return redirect("/documents")


@app.route("/documents/folder/share/<int:folder_id>", methods=["POST"])
def folder_share_create(folder_id):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    folder = conn.cursor().execute("SELECT id FROM document_folders WHERE id = ?", (folder_id,)).fetchone()
    if not folder:
        conn.close()
        return redirect("/documents")
    token = secrets.token_urlsafe(32)
    conn.cursor().execute("""
        INSERT INTO folder_shares (token, folder_id, created_at, expires_at, revoked)
        VALUES (?, ?, ?, ?, ?)
    """, (token, folder_id, lux_now().strftime("%Y-%m-%d %H:%M:%S"),
          share_expiry(request.form.get("days", "7")), 0))
    conn.commit()
    conn.close()
    parent_id = request.form.get("parent_id", "").strip()
    if parent_id:
        return redirect(f"/documents?folder={parent_id}")
    return redirect("/documents")


@app.route("/documents/folder/share/revoke/<token>", methods=["POST"])
def folder_share_revoke(token):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn()
    conn.cursor().execute("UPDATE folder_shares SET revoked = 1 WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return redirect("/documents")


def _shared_folder_validate(conn, token):
    """Returns (root_folder_id, expires_at, folder_name) or None if invalid."""
    row = conn.cursor().execute("""
        SELECT fs.folder_id, fs.expires_at, fs.revoked, df.name
        FROM folder_shares fs
        JOIN document_folders df ON df.id = fs.folder_id
        WHERE fs.token = ?
    """, (token,)).fetchone()
    if not row or not share_is_active(row[1] or "", row[2]):
        return None
    return row[0], row[1] or "", row[3]


def _folder_tree_ids(conn, root_id):
    """Returns set of all folder IDs in the subtree (including root)."""
    ids = {root_id}
    queue = [root_id]
    while queue:
        parent = queue.pop()
        for (cid,) in conn.cursor().execute(
                "SELECT id FROM document_folders WHERE parent_id = ?", (parent,)).fetchall():
            if cid not in ids:
                ids.add(cid)
                queue.append(cid)
    return ids


def _folder_breadcrumb_shared(conn, root_id, current_id, token):
    """Breadcrumb list from root to current folder for shared view."""
    crumbs = []
    fid = current_id
    while fid and fid != root_id:
        row = conn.cursor().execute(
            "SELECT id, name, parent_id FROM document_folders WHERE id = ?", (fid,)).fetchone()
        if not row:
            break
        crumbs.insert(0, {"id": row[0], "name": row[1]})
        fid = row[2]
    return crumbs


SHARED_FOLDER_TMPL = """
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ current_name }} – Luxmann</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:system-ui,sans-serif;
     background:{{ '#111113' if dark else '#f1f5f9' }};
     color:{{ '#e2e8f0' if dark else '#1e293b' }};min-height:100vh;}
a{color:inherit;text-decoration:none;}
/* Top bar */
.sf-topbar{display:flex;align-items:center;justify-content:space-between;
           padding:10px 20px;
           background:{{ '#0c0c0e' if dark else '#1e3a5f' }};
           color:white;gap:12px;flex-wrap:wrap;}
.sf-brand{font-size:18px;font-weight:800;color:#93c5fd;display:flex;align-items:center;gap:8px;}
.sf-controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.sf-lang{display:flex;gap:4px;}
.sf-lang a{padding:4px 9px;border-radius:6px;font-size:12px;font-weight:700;
           color:#cbd5e1;border:1px solid #2c2c30;}
.sf-lang a.sf-active{background:#2563eb;color:white;border-color:#2563eb;}
.sf-lang a:hover{background:#1e3a5f;color:white;}
.sf-theme-btn{padding:5px 10px;border-radius:6px;font-size:16px;
              background:{{ '#1d1d1f' if dark else '#e2e8f0' }};
              color:{{ '#fbbf24' if dark else '#1e3a5f' }};
              border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};cursor:pointer;}
/* Wrap */
.sf-wrap{max-width:960px;margin:0 auto;padding:20px 16px;}
.sf-head{margin:16px 0 4px;}
.sf-head h2{font-size:20px;font-weight:700;}
.sf-meta{color:{{ '#94a3b8' if dark else '#64748b' }};font-size:13px;
         margin:6px 0 14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
/* Breadcrumb */
.sf-crumb{display:flex;gap:6px;align-items:center;flex-wrap:wrap;
          margin-bottom:14px;font-size:13px;
          color:{{ '#94a3b8' if dark else '#64748b' }};}
.sf-crumb a{color:#3b82f6;}
.sf-crumb a:hover{text-decoration:underline;}
/* Table */
.sf-table{width:100%;border-collapse:collapse;}
.sf-table th{text-align:left;padding:8px 10px;
             border-bottom:2px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
             font-size:11px;text-transform:uppercase;
             color:{{ '#94a3b8' if dark else '#64748b' }};}
.sf-table td{padding:7px 10px;
             border-bottom:1px solid {{ '#1d1d1f' if dark else '#e2e8f0' }};
             vertical-align:middle;}
.sf-table tr:hover td{background:{{ '#1d1d1f' if dark else '#f8fafc' }};}
.sf-folder-link{display:flex;align-items:center;gap:8px;font-weight:600;}
.sf-folder-link:hover{color:#3b82f6;}
.sf-file-link{display:flex;align-items:center;gap:8px;}
.sf-file-link:hover{color:#3b82f6;}
/* Buttons */
.sf-btn{display:inline-block;padding:5px 11px;border-radius:6px;font-size:12px;
        background:{{ '#1e3a5f' if dark else '#e8f1ff' }};
        color:{{ '#93c5fd' if dark else '#2563eb' }};
        border:1px solid {{ '#2c2c30' if dark else '#bfdbfe' }};margin-left:5px;}
.sf-btn:hover{background:#2563eb;color:white;border-color:#2563eb;}
.sf-btn-zip{background:{{ '#064e3b' if dark else '#ecfdf5' }};
            color:{{ '#6ee7b7' if dark else '#065f46' }};
            border-color:{{ '#065f46' if dark else '#a7f3d0' }};}
.sf-btn-zip:hover{background:#065f46;color:white;border-color:#065f46;}
.sf-empty{color:{{ '#94a3b8' if dark else '#475569' }};padding:24px 0;text-align:center;}
.sf-size{color:{{ '#94a3b8' if dark else '#64748b' }};font-size:12px;}
@media(max-width:600px){
  .sf-table th:nth-child(2),.sf-table td:nth-child(2){display:none;}
  .sf-btn{padding:5px 8px;font-size:11px;}
}
</style>
</head>
<body>
<div class="sf-topbar">
  <div class="sf-brand">&#128193; Luxmann Services</div>
  <div class="sf-controls">
    <div class="sf-lang">
      <a href="/set_lang/bos" class="{% if lang=='bos' %}sf-active{% endif %}">BOS</a>
      <a href="/set_lang/en"  class="{% if lang=='en'  %}sf-active{% endif %}">EN</a>
      <a href="/set_lang/de"  class="{% if lang=='de'  %}sf-active{% endif %}">DE</a>
      <a href="/set_lang/fr"  class="{% if lang=='fr'  %}sf-active{% endif %}">FR</a>
      <a href="/set_lang/pt"  class="{% if lang=='pt'  %}sf-active{% endif %}">PT</a>
    </div>
    <a class="sf-theme-btn" href="/set_theme/{% if dark %}light{% else %}dark{% endif %}">
      {% if dark %}☀️{% else %}🌙{% endif %}
    </a>
  </div>
</div>
<div class="sf-wrap">
  <div class="sf-head"><h2>&#128193; {{ current_name }}</h2></div>
  <div class="sf-meta">
    {% if expires_at %}
      {{ tr["expires_in"] }}: {{ expires_at[8:10] }}.{{ expires_at[5:7] }}.{{ expires_at[0:4] }}
      &nbsp;|&nbsp;
    {% endif %}
    <a class="sf-btn sf-btn-zip" href="/share/folder/{{ token }}/zip{% if current_id != root_id %}/{{ current_id }}{% endif %}">
      &#8681; {{ tr["download"] }} ZIP
    </a>
  </div>
  <div class="sf-crumb">
    <a href="/share/folder/{{ token }}">{{ root_name }}</a>
    {% for crumb in breadcrumbs %}
      <span>/</span><a href="/share/folder/{{ token }}/sub/{{ crumb.id }}">{{ crumb.name }}</a>
    {% endfor %}
    {% if current_id != root_id %}<span>/</span><span>{{ current_name }}</span>{% endif %}
  </div>
  <table class="sf-table">
    <tr>
      <th>{{ tr["document_name"] }}</th>
      <th>{{ tr["file_size"] }}</th>
      <th></th>
    </tr>
    {% for sub in subfolders %}
    <tr>
      <td><a class="sf-folder-link" href="/share/folder/{{ token }}/sub/{{ sub[0] }}">
        &#128193; {{ sub[1] }}
      </a></td>
      <td class="sf-size">—</td>
      <td style="text-align:right;">
        <a class="sf-btn sf-btn-zip" href="/share/folder/{{ token }}/zip/{{ sub[0] }}">&#8681; ZIP</a>
      </td>
    </tr>
    {% endfor %}
    {% for doc in docs %}
    <tr>
      <td><a class="sf-file-link" href="/share/folder/{{ token }}/file/{{ doc.id }}">
        {% if doc.mime_type.startswith('image/') %}&#128444;{% else %}&#128196;{% endif %}
        {{ doc.original_name }}
      </a></td>
      <td class="sf-size">{{ size_label(doc.file_size) }}</td>
      <td style="text-align:right;white-space:nowrap;">
        <a class="sf-btn" href="/share/folder/{{ token }}/file/{{ doc.id }}?download=1">&#8681; {{ tr["download"] }}</a>
        {% if doc.mime_type.startswith('application/pdf') or doc.mime_type.startswith('image/') %}
        <a class="sf-btn" href="/share/folder/{{ token }}/file/{{ doc.id }}">&#128065; {{ tr["preview"] }}</a>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
    {% if subfolders|length == 0 and docs|length == 0 %}
    <tr><td colspan="3" class="sf-empty">{{ tr["document_missing"] }}</td></tr>
    {% endif %}
  </table>
</div>
</body>
</html>
"""


def _shared_folder_render(token, root_id, expires_at, root_name,
                          current_id, current_name, breadcrumbs,
                          subfolders, docs):
    tr = t()
    dark = get_theme() == "dark"
    lang = get_lang()
    return render_template_string(SHARED_FOLDER_TMPL, token=token,
        root_id=root_id, current_id=current_id,
        root_name=root_name, current_name=current_name,
        expires_at=expires_at, breadcrumbs=breadcrumbs,
        subfolders=subfolders, docs=docs,
        size_label=document_size_label, tr=tr, dark=dark, lang=lang)


@app.route("/share/folder/<token>")
def shared_folder(token):
    conn = get_conn()
    info = _shared_folder_validate(conn, token)
    if not info:
        conn.close()
        tr = t()
        dark = get_theme() == "dark"
        return render_template_string(SHARED_FOLDER_TMPL.split("<table")[0] + """
        <div style="text-align:center;padding:48px 16px;">
          <p style="color:{{ '#94a3b8' if dark else '#64748b' }};">{{ tr["share_expired"] }}</p>
        </div></body></html>""", tr=tr, dark=dark, lang=get_lang()), 404
    root_id, expires_at, root_name = info
    subfolders = conn.cursor().execute(
        "SELECT id, name FROM document_folders WHERE parent_id = ? ORDER BY name", (root_id,)).fetchall()
    doc_rows = conn.cursor().execute("""
        SELECT id, original_name, stored_name, mime_type, file_size, category, folder_id, note, uploaded_at, uploaded_by
        FROM documents WHERE folder_id = ? ORDER BY original_name
    """, (root_id,)).fetchall()
    conn.close()
    docs = [document_row(r) for r in doc_rows]
    return _shared_folder_render(token, root_id, expires_at, root_name,
                                 root_id, root_name, [], subfolders, docs)


@app.route("/share/folder/<token>/sub/<int:sub_id>")
def shared_folder_sub(token, sub_id):
    tr = t()
    conn = get_conn()
    info = _shared_folder_validate(conn, token)
    if not info:
        conn.close()
        return _share_error_page("🔗", tr.get("share_link_invalid", "Link nije validan"), tr.get("share_link_invalid", "Ovaj link je istekao ili nije validan."), 404)
    root_id, expires_at, root_name = info
    tree_ids = _folder_tree_ids(conn, root_id)
    if sub_id not in tree_ids:
        conn.close()
        return _share_error_page("📁", tr.get("folder_unavailable", "Folder nedostupan"), tr.get("folder_unavailable", "Ovaj folder nije dostupan putem ovog linka."), 403)
    sub_row = conn.cursor().execute(
        "SELECT id, name FROM document_folders WHERE id = ?", (sub_id,)).fetchone()
    if not sub_row:
        conn.close()
        return _share_error_page("📁", tr.get("folder_not_found_pub", "Folder nije pronađen"), tr.get("folder_not_found_pub", "Traženi folder ne postoji."), 404)
    subfolders = conn.cursor().execute(
        "SELECT id, name FROM document_folders WHERE parent_id = ? ORDER BY name", (sub_id,)).fetchall()
    doc_rows = conn.cursor().execute("""
        SELECT id, original_name, stored_name, mime_type, file_size, category, folder_id, note, uploaded_at, uploaded_by
        FROM documents WHERE folder_id = ? ORDER BY original_name
    """, (sub_id,)).fetchall()
    breadcrumbs = _folder_breadcrumb_shared(conn, root_id, sub_id, token)
    conn.close()
    docs = [document_row(r) for r in doc_rows]
    return _shared_folder_render(token, root_id, expires_at, root_name,
                                 sub_id, sub_row[1], breadcrumbs, subfolders, docs)


@app.route("/share/folder/<token>/file/<int:doc_id>")
def shared_folder_file(token, doc_id):
    tr = t()
    conn = get_conn()
    info = _shared_folder_validate(conn, token)
    if not info:
        conn.close()
        return _share_error_page("🔗",
            tr.get("share_link_invalid", "Link istekao ili nije validan."),
            tr.get("share_link_invalid", "Ovaj link nije validan ili je istekao."),
            404)
    root_id = info[0]
    tree_ids = _folder_tree_ids(conn, root_id)
    doc_row = conn.cursor().execute("""
        SELECT id, original_name, stored_name, mime_type, file_size, category, folder_id, note, uploaded_at, uploaded_by
        FROM documents WHERE id = ?
    """, (doc_id,)).fetchone()
    conn.close()
    if not doc_row or doc_row[6] not in tree_ids:
        return _share_error_page("📄",
            tr.get("file_unavail_title", "Fajl privremeno nije dostupan"),
            tr.get("file_unavail_body", "Traženi fajl trenutno nije dostupan. Molite administratora."),
            404)
    document = document_row(doc_row)
    path = document_path(document["stored_name"])
    if not os.path.exists(path):
        return _share_error_page("📂",
            tr.get("file_unavail_title", "Fajl privremeno nije dostupan"),
            tr.get("file_unavail_body", "Traženi fajl trenutno nije dostupan. Molite administratora."),
            503)
    as_download = request.args.get("download") == "1"
    return send_file(path, mimetype=document["mime_type"] or "application/octet-stream",
                     as_attachment=as_download,
                     download_name=document["original_name"] if as_download else None)


def _share_error_page(icon, title, body, status=503):
    safe_icon  = _html.escape(str(icon))
    safe_title = _html.escape(str(title))
    safe_body  = _html.escape(str(body))
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>" + safe_title + "</title>"
        "<style>body{font-family:sans-serif;max-width:520px;margin:80px auto;"
        "padding:20px;text-align:center;}"
        ".icon{font-size:48px;margin-bottom:16px;}"
        "h2{color:#1f4f82;margin-bottom:8px;}p{color:#64748b;line-height:1.6;}"
        "small{display:block;margin-top:24px;font-size:11px;color:#94a3b8;}"
        "</style></head><body>"
        "<div class='icon'>" + safe_icon + "</div>"
        "<h2>" + safe_title + "</h2>"
        "<p>" + safe_body + "</p>"
        "<small>Luxmann Planner</small></body></html>"
    )
    from flask import Response
    return Response(page, status=status, mimetype="text/html")


def _zip_add_folder(conn, zf, folder_id, rel_prefix, errors=None):
    """Recursively add documents from folder_id and subfolders. Returns count added."""
    if errors is None:
        errors = []
    added = 0
    doc_rows = conn.cursor().execute("""
        SELECT id, original_name, stored_name, mime_type, file_size, category, folder_id, note, uploaded_at, uploaded_by
        FROM documents WHERE folder_id = ? ORDER BY original_name
    """, (folder_id,)).fetchall()
    for row in doc_rows:
        doc = document_row(row)
        src = document_path(doc["stored_name"])
        arc = rel_prefix + doc["original_name"]
        if not os.path.exists(src):
            errors.append(("NOT_ON_DISK", doc["original_name"], src))
            continue
        try:
            zf.write(src, arc)
            added += 1
        except Exception as e:
            errors.append(("WRITE_ERR", doc["original_name"], str(e)))
    sub_rows = conn.cursor().execute(
        "SELECT id, name FROM document_folders WHERE parent_id = ? ORDER BY name",
        (folder_id,)).fetchall()
    for sub_id, sub_name in sub_rows:
        safe_sub = re.sub(r'[\\/:*?"<>|]', "_", sub_name)
        added += _zip_add_folder(conn, zf, sub_id, rel_prefix + safe_sub + "/", errors)
    return added


@app.route("/share/folder/<token>/zip")
@app.route("/share/folder/<token>/zip/<int:sub_id>")
def shared_folder_zip(token, sub_id=None):
    tr = t()
    conn = get_conn()
    info = _shared_folder_validate(conn, token)
    if not info:
        conn.close()
        return _share_error_page("🔗", tr.get("share_link_invalid", "Link nije validan"), tr.get("share_link_invalid", "Ovaj link je istekao ili nije validan."), 404)
    root_id, expires_at, root_name = info
    tree_ids = _folder_tree_ids(conn, root_id)
    zip_root = sub_id if sub_id else root_id
    if zip_root not in tree_ids:
        conn.close()
        return _share_error_page("📁", tr.get("folder_unavailable", "Folder nedostupan"), tr.get("folder_unavailable", "Ovaj folder nije dostupan putem ovog linka."), 403)
    folder_name_row = conn.cursor().execute(
        "SELECT name FROM document_folders WHERE id = ?", (zip_root,)).fetchone()
    zip_folder_name = folder_name_row[0] if folder_name_row else "folder"
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(tmp_fd)
    try:
        errors = []
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            added = _zip_add_folder(conn, zf, zip_root, "", errors)
        conn.close()
        if added == 0:
            os.unlink(tmp_path)
            if errors:
                return _share_error_page("📂",
                    tr.get("zip_unavail_title", "Dokumenti privremeno nisu dostupni"),
                    tr.get("zip_unavail_body", "Zatraženi fajlovi trenutno nisu dostupni. Molite administratora."),
                    503)
            return _share_error_page("📁",
                tr.get("folder_empty_title", "Folder ne sadrži dokumente"),
                tr.get("folder_empty_body", "Nema fajlova za preuzimanje u ovom folderu."),
                404)
        if errors:
            missing_txt = "Sljedeci fajlovi nisu bili dostupni i nisu ukljuceni u ZIP:\n\n" + \
                "\n".join(err[1] for err in errors)
            with zipfile.ZipFile(tmp_path, "a") as zf:
                zf.writestr("_missing_files.txt", missing_txt)
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", zip_folder_name) or "folder"

        from flask import after_this_request as _atr
        @_atr
        def _del_tmp(response, _p=tmp_path):
            try: os.unlink(_p)
            except: pass
            return response

        return send_file(
            tmp_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{safe_name}.zip",
        )
    except Exception:
        conn.close()
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


@app.route("/share/document/<token>")
def shared_document(token):
    tr = t()
    dark = False
    conn = get_conn()
    document = get_shared_document(conn, token)
    conn.close()
    if not document or not os.path.exists(document_path(document["stored_name"])):
        return render_template_string(BASE_STYLE + """
        <div class="card" style="max-width:620px;margin:12vh auto;text-align:center;"><h1>Luxmann Services</h1><p>{{ tr["share_expired"] }}</p></div>
        """, tr=tr, dark=dark), 404
    return render_template_string(BASE_STYLE + """
    <div class="card" style="max-width:1100px;margin:24px auto;">
        <h1>Luxmann Services</h1>
        <h2>{{ doc.original_name }}</h2>
        <p class="muted">{{ tr["accountant_access"] }}</p>
        {% if doc.allow_download %}<a class="pdf-link" href="/share/document/{{ token }}/file?download=1">{{ tr["download"] }}</a>{% endif %}
        {% if inline %}
            <iframe src="/share/document/{{ token }}/file" title="{{ doc.original_name }}" style="display:block;width:100%;height:78vh;border:0;background:white;border-radius:8px;margin-top:14px;"></iframe>
        {% endif %}
    </div>
    """, tr=tr, dark=dark, doc=document, token=token, inline=document_inline_allowed(document["mime_type"]))


@app.route("/share/document/<token>/file")
def shared_document_file(token):
    conn = get_conn()
    document = get_shared_document(conn, token)
    conn.close()
    if not document or not os.path.exists(document_path(document["stored_name"])):
        return ("Not found", 404)
    download = request.args.get("download") == "1" or not document_inline_allowed(document["mime_type"])
    if download and not document["allow_download"]:
        return ("Forbidden", 403)
    return send_file(document_path(document["stored_name"]), as_attachment=download, download_name=document["original_name"], mimetype=document["mime_type"] or None)


@app.route("/invoices")
def invoices():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    default_from, default_to = previous_month_range()
    date_from = request.args.get("date_from", default_from).strip()
    date_to = request.args.get("date_to", default_to).strip()
    invoice_date = request.args.get("invoice_date", lux_now().strftime("%Y-%m-%d")).strip()
    conn = get_conn()
    settings = get_invoice_settings(conn)
    profiles = get_invoice_profiles(conn)
    rows = fetch_invoice_records(conn)
    conn.close()
    profiles_json = json.dumps(profiles)
    paid_rows = [r for r in rows if r.get("paid")]
    unpaid_rows = [r for r in rows if not r.get("paid")]
    total_paid = sum(r["total"] for r in paid_rows)
    total_unpaid = sum(r["total"] for r in unpaid_rows)
    total_all = sum(r["total"] for r in rows)
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
        .invoice-shell { background:#2b2b2b; color:white; border-radius:10px; padding:0 0 22px 0; overflow:hidden; }
        .invoice-top { display:flex; align-items:center; justify-content:space-between; gap:18px; padding:18px 22px; background:#3d3d3d; }
        .invoice-brand { font-size:26px; font-weight:800; }
        .invoice-brand span { background:#ffd429; color:#111; border-radius:6px; padding:2px 6px; }
        .invoice-search { flex:1; display:flex; max-width:720px; }
        .invoice-search input { border-radius:0; margin:0; }
        .invoice-search button { width:130px; margin:0; border-radius:0; background:#111; }
        .invoice-panel { max-width:1280px; margin:34px auto 0 auto; background:#4a4a4a; border-radius:8px; padding:22px 30px; }
        .invoice-tabs { display:flex; gap:6px; flex-wrap:wrap; margin:14px 0 22px; }
        .invoice-tab { padding:12px 16px; background:#737373; color:white; border-radius:8px 8px 0 0; font-weight:bold; }
        .invoice-tab.active { background:#5a5a5a; }
        .pill { display:inline-block; margin-left:6px; padding:2px 8px; border-radius:999px; font-size:12px; color:#111; background:#e5e7eb; }
        .pill.red { background:#fb7185; color:white; } .pill.green { background:#34d399; }
        .invoice-table { width:100%; border-collapse:collapse; color:white; }
        .invoice-table th, .invoice-table td { padding:14px 10px; border-bottom:1px solid #9ca3af; text-align:left; }
        .invoice-table th { font-size:13px; text-transform:uppercase; color:#f3f4f6; }
        .paid-text { color:#34d399; font-weight:bold; } .unpaid-text { color:#fb7185; font-weight:bold; }
        .sent-badge { display:inline-block; padding:4px 8px; border-radius:999px; font-size:12px; font-weight:bold; color:#111; }
        .sent-badge.sent { background:#34d399; } .sent-badge.unsent { background:#fb7185; color:white; }
        .invoice-totals { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-top:16px; }
        .invoice-total-card { background:#3d3d3d; border-radius:8px; padding:14px; }
        .invoice-actions { display:flex; gap:10px; flex-wrap:wrap; margin:16px 0; }
        .invoice-actions a { background:#111; color:white; padding:10px 14px; border-radius:6px; }
        .settings-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px; margin-top:18px; }
    </style>
    <div class="invoice-shell">
        <div class="invoice-top">
            <div class="invoice-brand">Luxmann <span>Factures</span></div>
            <form class="invoice-search" method="get" action="/invoices">
                <input type="hidden" name="date_from" value="{{ date_from }}">
                <input type="hidden" name="date_to" value="{{ date_to }}">
                <input type="hidden" name="invoice_date" value="{{ invoice_date }}">
                <input id="invoiceDashboardSearch" name="q" value="{{ request.args.get('q', '') }}" placeholder="{{ tr['search_client'] }}, adresse ou numero">
                <button>{{ tr["filter_btn"] }}</button>
            </form>
            <a class="back-button" href="/">{{ tr["back"] }}</a>
        </div>

        <div class="invoice-panel">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;">
                <h1 style="color:white;margin:0;">{{ tr["invoices"] }}</h1>
                <div class="invoice-actions">
                    <a href="/invoices/export_options?type=all">{{ tr["download_all_invoices"] }}</a>
                    <a href="/invoices/export_options?type=certificate">{{ tr["annual_certificate"] }}</a>
                    <a href="/invoices/export_options?type=list">Lista faktura PDF</a>
                </div>
            </div>

            {% with msgs = get_flashed_messages(with_categories=true) %}
            {% for cat, msg in msgs %}
            <div style="background:{% if cat=='error' %}#ef4444{% else %}#22c55e{% endif %};color:{% if cat=='error' %}white{% else %}#111{% endif %};padding:10px 18px;border-radius:8px;font-weight:700;font-size:14px;margin-bottom:12px;">{{ msg }}</div>
            {% endfor %}
            {% endwith %}
            <form method="post" action="/invoices/generate" class="settings-grid">
                <div><label>{{ tr["date_from"] }}</label><input type="date" name="date_from" value="{{ date_from }}"></div>
                <div><label>{{ tr["date_to"] }}</label><input type="date" name="date_to" value="{{ date_to }}"></div>
                <div><label>{{ tr["invoice_date"] }}</label><input type="date" name="invoice_date" value="{{ invoice_date }}"></div>
                <div style="align-self:end;"><button style="background:#22c55e;color:#111;font-weight:800;">{{ tr["generate_invoice"] }}</button></div>
            </form>

            <div class="invoice-tabs">
                <a class="invoice-tab active" href="#">{{ tr["total_invoices"] }} <span class="pill">{{ rows|length }}</span></a>
                <a class="invoice-tab" href="#" onclick="filterInvoiceStatus('unpaid', this);return false;">{{ tr["unpaid"] }} <span class="pill red">{{ unpaid_rows|length }}</span></a>
                <a class="invoice-tab" href="#" onclick="filterInvoiceStatus('paid', this);return false;">{{ tr["paid"] }} <span class="pill green">{{ paid_rows|length }}</span></a>
                <a class="invoice-tab" href="/invoices/quote">{{ tr["quote"] }}</a>
                <a class="invoice-tab" href="/invoices/manual" style="background:#22c55e;color:#111;">✏️ {{ tr.get("mi_title","Facture manuelle") }}</a>
            </div>

            <table class="invoice-table">
                <tr><th></th><th>{{ tr["client_name"] }}</th><th>Document</th><th>{{ tr["invoice_number"] }}</th><th>{{ tr["invoice_date"] }}</th><th>{{ tr["payment_status"] }}</th><th>{{ tr["sent_status"] }}</th><th>{{ tr["amount_with_vat"] }}</th><th>PDF</th><th></th></tr>
                {% for row in rows %}
                <tr class="invoice-row" data-paid="{{ 1 if row.paid else 0 }}" data-search="{{ (row.client ~ ' ' ~ row.invoice_number)|lower }}">
                    <td><input type="checkbox" style="width:auto;"></td>
                    <td><a href="/invoices/client?client={{ row.client|urlencode }}&date_from={{ date_from }}&date_to={{ date_to }}" style="color:white;text-decoration:underline;">{{ row.client }}</a></td>
                    <td>{{ tr["invoices"] }}{% if row.source == 'manual' %} <span style="font-size:10px;background:#22c55e;color:#111;padding:1px 5px;border-radius:4px;">✏️</span>{% endif %}</td>
                    <td>
                      {% if row.source == 'manual' %}
                        <a href="/invoices/manual?invoice_number={{ row.invoice_number }}" style="color:#ffd429;text-decoration:underline;">{{ row.invoice_number }}</a>
                      {% else %}
                        <a href="/invoices/view?invoice_number={{ row.invoice_number }}" style="color:white;text-decoration:underline;">{{ row.invoice_number }}</a>
                      {% endif %}
                    </td>
                    <td>{{ format_date(row.invoice_date) }}</td>
                    <td>
                        <span class="payment-label {{ 'paid-text' if row.paid else 'unpaid-text' }}">{{ tr["paid"] if row.paid else tr["unpaid"] }}</span><br>
                        <a class="ajax-invoice-toggle" data-kind="paid" data-paid-label="{{ tr['paid'] }}" data-unpaid-label="{{ tr['unpaid'] }}" data-mark-paid="{{ tr['mark_paid'] }}" data-mark-unpaid="{{ tr['mark_unpaid'] }}" href="/invoices/mark_paid?invoice_number={{ row.invoice_number }}&paid={{ 0 if row.paid else 1 }}&client={{ row.client|urlencode }}&date_from={{ row.date_from }}&date_to={{ row.date_to }}&invoice_date={{ row.invoice_date }}&amount={{ row.amount }}&vat_amount={{ row.vat_amount }}&total={{ row.total }}&ajax=1" style="color:#e5e7eb;">{{ tr["mark_unpaid"] if row.paid else tr["mark_paid"] }}</a>
                    </td>
                    <td>
                        <span class="sent-badge {{ 'sent' if row.sent else 'unsent' }}">{{ tr["sent_yes"] if row.sent else tr["sent_no"] }}</span><br>
                        <a class="ajax-invoice-toggle" data-kind="sent" data-sent-label="{{ tr['sent_yes'] }}" data-unsent-label="{{ tr['sent_no'] }}" data-mark-sent="{{ tr['mark_sent'] }}" data-mark-unsent="{{ tr['mark_unsent'] }}" href="/invoices/mark_sent?invoice_number={{ row.invoice_number }}&sent={{ 0 if row.sent else 1 }}&ajax=1" style="color:#e5e7eb;">{{ tr["mark_unsent"] if row.sent else tr["mark_sent"] }}</a>
                    </td>
                    <td><b>{{ "%.2f"|format(row.total) }} EUR</b></td>
                    <td>
                      {% if row.source == 'manual' %}
                        <a href="/invoices/manual/pdf?invoice_number={{ row.invoice_number }}" style="color:#93c5fd;">PDF</a>
                        <a href="/invoices/manual?invoice_number={{ row.invoice_number }}" style="color:#ffd429;margin-left:6px;font-size:11px;">✏️</a>
                      {% else %}
                        <a href="/invoices/download?invoice_number={{ row.invoice_number }}&client={{ row.client|urlencode }}&date_from={{ row.date_from }}&date_to={{ row.date_to }}&invoice_date={{ row.invoice_date }}" style="color:#93c5fd;">PDF</a>
                        <a href="/invoices/manual?load_auto={{ row.invoice_number }}" style="color:#ffd429;margin-left:6px;font-size:11px;" title="Uredi ručno">✏️</a>
                      {% endif %}
                    </td>
                    <td><a href="/invoices/delete?invoice_number={{ row.invoice_number }}" onclick="return confirm('Obrisati fakturu?');" style="color:#fb7185;">{{ tr["delete"] }}</a></td>
                </tr>
                {% endfor %}
            </table>
            {% if rows|length == 0 %}<div class="muted">{{ tr["no_shifts"] }}</div>{% endif %}

            <div class="invoice-totals">
                <div class="invoice-total-card"><div class="muted">{{ tr["paid"] }}</div><div class="paid-text">{{ "%.2f"|format(total_paid) }} EUR</div></div>
                <div class="invoice-total-card"><div class="muted">{{ tr["unpaid"] }}</div><div class="unpaid-text">{{ "%.2f"|format(total_unpaid) }} EUR</div></div>
                <div class="invoice-total-card"><div class="muted">{{ tr["total_invoices"] }}</div><div><b>{{ "%.2f"|format(total_all) }} EUR</b></div></div>
            </div>
        </div>
    </div>

    <div class="grid" style="margin-top:16px;">
        <div class="card">
            <h3>{{ tr["invoice_settings"] }}</h3>
            <form method="post" action="/invoices/settings">
                <button type="button" onclick="var box=document.getElementById('companySettingsBox'); box.style.display = box.style.display === 'none' ? 'block' : 'none';">{{ tr["company_settings"] }}</button>
                <div id="companySettingsBox" style="display:none;">
                    <label>{{ tr["company_name"] }}</label><input name="company_name" value="{{ settings.company_name }}">
                    <label>{{ tr["company_address"] }}</label><textarea name="company_address" style="width:100%;min-height:70px;">{{ settings.company_address }}</textarea>
                    <label>{{ tr["company_phone"] }}</label><input name="company_phone" value="{{ settings.company_phone }}">
                    <label>{{ tr["company_email"] }}</label><input name="company_email" value="{{ settings.company_email }}">
                    <label>{{ tr["company_vat"] }}</label><input name="company_vat" value="{{ settings.company_vat }}">
                </div>
                <label>{{ tr["invoice_template"] }}</label>
                <select name="invoice_template">
                    <option value="orange" {% if settings.invoice_template == 'orange' %}selected{% endif %}>{{ tr["template_orange"] }}</option>
                    <option value="blue" {% if settings.invoice_template == 'blue' %}selected{% endif %}>{{ tr["template_blue"] }}</option>
                    <option value="green" {% if settings.invoice_template == 'green' %}selected{% endif %}>{{ tr["template_green"] }}</option>
                </select>
                <label>{{ tr["invoice_start_number"] }}</label>
                <input type="number" min="1" name="invoice_start_number" value="{{ settings.invoice_start_number }}">
                <label>{{ tr["payment_terms"] }}</label>
                <textarea name="payment_terms" style="width:100%;min-height:70px;">{{ settings.payment_terms }}</textarea>
                <input type="hidden" name="invoice_text" value="">
                <input type="hidden" name="bank_account" value="">
                <button>{{ tr["save_settings"] }}</button>
            </form>
        </div>
    <div class="card" style="margin-top:16px;">
        <h3>{{ tr["invoice_profiles"] }}</h3>
        <form method="post" action="/invoices/profile">
            <label>{{ tr["search_client"] }}</label>
            <input id="invoiceClientSearch" list="invoiceClientList" placeholder="{{ tr['search_client'] }}" autocomplete="off" oninput="fillInvoiceProfile()">
            <datalist id="invoiceClientList">{% for p in profiles %}<option value="{{ p.client }}"></option>{% endfor %}</datalist>
            <input type="hidden" id="invoiceClientName" name="client_name">
            <input id="invoiceCustomAddress" name="custom_address" placeholder="{{ tr['address'] }}">
            <input id="invoiceEmail" name="email" placeholder="{{ tr['email'] }}">
            <select name="client_type">
                <option value="private">{{ tr["private_client"] }} - 8%</option>
                <option value="pro">{{ tr["pro_client"] }} - 17%</option>
            </select>
            <input id="invoiceHourlyRate" type="number" step="0.01" name="hourly_rate" placeholder="{{ tr['hourly_rate'] }}">
            <button>{{ tr["save_client_profile"] }}</button>
        </form>
    </div>
    <script>
    var invoiceProfiles = {{ profiles_json|safe }};
    var currentInvoiceStatus = "all";
    function filterInvoiceStatus(status, el){
        currentInvoiceStatus = status;
        document.querySelectorAll('.invoice-tab').forEach(function(tab){tab.classList.remove('active');});
        if(el){el.classList.add('active');}
        filterInvoiceRows();
    }
    function filterInvoiceRows(){
        var queryInput = document.getElementById('invoiceDashboardSearch');
        var query = queryInput ? queryInput.value.trim().toLowerCase() : "";
        document.querySelectorAll('.invoice-row').forEach(function(row){
            var paid = row.getAttribute('data-paid') === '1';
            var statusOk = currentInvoiceStatus === 'all' || (currentInvoiceStatus === 'paid' && paid) || (currentInvoiceStatus === 'unpaid' && !paid);
            var textOk = !query || (row.getAttribute('data-search') || '').indexOf(query) !== -1;
            row.style.display = statusOk && textOk ? '' : 'none';
        });
    }
    function fillInvoiceProfile(){
        var name = document.getElementById('invoiceClientSearch').value;
        var profile = invoiceProfiles.find(function(p){ return p.client === name; });
        document.getElementById('invoiceClientName').value = profile ? profile.client : "";
        if(!profile){return;}
        document.getElementById('invoiceCustomAddress').value = profile.address || "";
        document.getElementById('invoiceEmail').value = profile.email || "";
        document.querySelector('select[name="client_type"]').value = profile.client_type || "private";
        document.getElementById('invoiceHourlyRate').value = profile.hourly_rate || 0;
    }
    document.addEventListener('DOMContentLoaded', function(){
        var search = document.getElementById('invoiceDashboardSearch');
        if(search){search.addEventListener('input', filterInvoiceRows); filterInvoiceRows();}
        document.querySelectorAll('.ajax-invoice-toggle').forEach(function(link){
            link.addEventListener('click', function(event){
                event.preventDefault();
                var row = link.closest('.invoice-row');
                fetch(link.href, {headers:{'X-Requested-With':'fetch'}})
                    .then(function(resp){ return resp.json(); })
                    .then(function(data){
                        if(!data.ok){ window.location.href = link.href.replace('&ajax=1', ''); return; }
                        if(link.dataset.kind === 'sent'){
                            var badge = row.querySelector('.sent-badge');
                            badge.textContent = data.sent ? link.dataset.sentLabel : link.dataset.unsentLabel;
                            badge.classList.toggle('sent', data.sent);
                            badge.classList.toggle('unsent', !data.sent);
                            link.textContent = data.sent ? link.dataset.markUnsent : link.dataset.markSent;
                            link.href = link.href.replace(/sent=[01]/, 'sent=' + (data.sent ? '0' : '1'));
                        } else if(link.dataset.kind === 'paid'){
                            var label = row.querySelector('.payment-label');
                            label.textContent = data.paid ? link.dataset.paidLabel : link.dataset.unpaidLabel;
                            label.classList.toggle('paid-text', data.paid);
                            label.classList.toggle('unpaid-text', !data.paid);
                            row.setAttribute('data-paid', data.paid ? '1' : '0');
                            link.textContent = data.paid ? link.dataset.markUnpaid : link.dataset.markPaid;
                            link.href = link.href.replace(/paid=[01]/, 'paid=' + (data.paid ? '0' : '1'));
                            filterInvoiceRows();
                        }
                    })
                    .catch(function(){ window.location.href = link.href.replace('&ajax=1', ''); });
            });
        });
    });
    </script>
    """, tr=tr, dark=dark, settings=settings, profiles=profiles, profiles_json=profiles_json, rows=rows, paid_rows=paid_rows, unpaid_rows=unpaid_rows, total_paid=total_paid, total_unpaid=total_unpaid, total_all=total_all, format_date=format_date, date_from=date_from, date_to=date_to, invoice_date=invoice_date)


@app.route("/invoices/generate", methods=["POST"])
def invoices_generate():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    date_from = request.form.get("date_from", "").strip()
    date_to = request.form.get("date_to", "").strip()
    invoice_date = request.form.get("invoice_date", lux_now().strftime("%Y-%m-%d")).strip()
    if not date_from or not date_to:
        flash(tr.get("generate_invoice", "Generiši fakturu") + ": datum nedostaje.", "error")
        return redirect("/invoices")
    conn = get_conn()
    c = conn.cursor()
    settings = get_invoice_settings(conn)
    raw_rows = build_invoice_rows(conn, date_from, date_to, None, settings)
    generated = 0
    skipped_exists = 0
    skipped_no_rate = []
    try:
        c.execute("BEGIN IMMEDIATE")
        for row in raw_rows:
            if row.get("hourly_rate", 0) == 0 and row.get("amount", 0) == 0:
                skipped_no_rate.append(row["client"])
                continue
            existing = c.execute(
                "SELECT invoice_number FROM invoice_records WHERE client_name=? AND date_from=? AND date_to=? AND COALESCE(deleted,0)=0",
                (row["client"], date_from, date_to)
            ).fetchone()
            if existing:
                skipped_exists += 1
                continue
            inv_num = next_invoice_number(conn)
            try:
                c.execute("""INSERT OR IGNORE INTO invoice_records
                    (invoice_number, client_name, date_from, date_to, invoice_date,
                     amount, vat_amount, total, paid, sent, deleted, source)
                    VALUES (?,?,?,?,?,?,?,?,0,0,0,'auto')""",
                    (inv_num, row["client"], date_from, date_to, invoice_date,
                     row["amount"], row["vat_amount"], row["total"]))
                if c.rowcount == 1:
                    generated += 1
                else:
                    skipped_exists += 1
            except Exception:
                skipped_exists += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        flash(tr.get("generate_invoice", "Generiši fakturu") + f": greška — {e}", "error")
        return redirect("/invoices")
    conn.close()
    parts = []
    if generated:
        parts.append(tr.get("inv_gen_ok", "{n} faktura generisano").replace("{n}", str(generated)))
    if skipped_exists:
        parts.append(tr.get("inv_gen_exists", "{n} već postoji za ovaj period").replace("{n}", str(skipped_exists)))
    if skipped_no_rate:
        parts.append(tr.get("inv_gen_no_rate", "Bez postavljene cijene") + ": " + ", ".join(skipped_no_rate))
    if not parts:
        parts.append(tr.get("inv_gen_empty", "Nema smjena ili klijenata sa postavljenom cijenom."))
    flash("; ".join(parts), "error" if generated == 0 else "ok")
    return redirect(f"/invoices?date_from={date_from}&date_to={date_to}&invoice_date={invoice_date}")


@app.route("/invoices/client")
def invoices_client():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    client = request.args.get("client", "").strip()
    default_from, default_to = previous_month_range()
    date_from = request.args.get("date_from", default_from).strip()
    date_to = request.args.get("date_to", default_to).strip()
    status = request.args.get("status", "all").strip()
    conn = get_conn()
    rows = fetch_invoice_records(conn, date_from, date_to, client, status)
    conn.close()
    total_paid = sum(r["total"] for r in rows if r["paid"])
    total_unpaid = sum(r["total"] for r in rows if not r["paid"])
    total_all = sum(r["total"] for r in rows)
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
        .invoice-shell { background:#2b2b2b; color:white; border-radius:10px; padding:24px; }
        .invoice-panel { max-width:1280px; margin:0 auto; background:#4a4a4a; border-radius:8px; padding:22px 30px; }
        .doc-tabs { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:16px; }
        .doc-tab { background:#777; color:white; padding:12px 16px; border-radius:8px 8px 0 0; font-weight:bold; }
        .doc-tab.active { background:#4a4a4a; }
        .invoice-table { width:100%; border-collapse:collapse; color:white; }
        .invoice-table th, .invoice-table td { padding:14px 10px; border-bottom:1px solid #a3a3a3; text-align:left; }
        .invoice-table th { text-transform:uppercase; font-size:13px; }
        .paid-text { color:#34d399; font-weight:bold; } .unpaid-text { color:#fb7185; font-weight:bold; }
        .filters { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; margin:18px 0 28px; }
        .totals { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-top:18px; }
        .total-card { background:#3d3d3d; border-radius:8px; padding:14px; }
    </style>
    <div class="invoice-shell">
        <div class="doc-tabs">
            <a class="doc-tab" href="/invoices">Mes documents</a>
            <a class="doc-tab" href="/invoices#invoice-profiles">Mes clients</a>
            <a class="doc-tab" href="/invoices">Mes rapports</a>
            <span class="doc-tab active">{{ client }} <a href="/invoices" style="color:white;margin-left:8px;">x</a></span>
        </div>
        <div class="invoice-panel">
            <div style="display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap;">
                <h2 style="margin:0;color:white;">Documents de {{ client }} <span style="background:#111;border-radius:999px;padding:2px 8px;font-size:13px;">{{ rows|length }}</span></h2>
                <a href="/invoices/client_statement?client={{ client|urlencode }}&date_from={{ date_from }}&date_to={{ date_to }}" style="background:#888;color:white;padding:10px 14px;border-radius:6px;">Releve de compte client PDF</a>
            </div>
            <form class="filters" method="get" action="/invoices/client">
                <input type="hidden" name="client" value="{{ client }}">
                <div><label>Date du</label><input type="date" name="date_from" value="{{ date_from }}"></div>
                <div><label>Date au</label><input type="date" name="date_to" value="{{ date_to }}"></div>
                <div><label>Statut</label><select name="status"><option value="all" {% if status == 'all' %}selected{% endif %}>--Tous--</option><option value="paid" {% if status == 'paid' %}selected{% endif %}>{{ tr["paid"] }}</option><option value="unpaid" {% if status == 'unpaid' %}selected{% endif %}>{{ tr["unpaid"] }}</option></select></div>
                <div style="align-self:end;"><button>Rechercher</button></div>
            </form>
            <table class="invoice-table">
                <tr><th></th><th>Client</th><th>Document</th><th>Numero</th><th>Date</th><th>Paye</th><th>Montant</th></tr>
                {% for row in rows %}
                <tr>
                    <td><input type="checkbox" style="width:auto;"></td>
                    <td>{{ row.client }}</td>
                    <td>Facture</td>
                    <td><a href="/invoices/view?invoice_number={{ row.invoice_number }}" style="color:white;text-decoration:underline;">{{ row.invoice_number }}</a></td>
                    <td>{{ format_date(row.invoice_date) }}</td>
                    <td class="{{ 'paid-text' if row.paid else 'unpaid-text' }}">{{ "%.2f"|format(row.total if row.paid else 0) }} EUR</td>
                    <td>{{ "%.2f"|format(row.total) }} EUR</td>
                </tr>
                {% endfor %}
            </table>
            {% if rows|length == 0 %}<p class="muted">Nema faktura za izabrani period.</p>{% endif %}
            <div class="totals">
                <div class="total-card"><div class="muted">{{ tr["paid"] }}</div><div class="paid-text">{{ "%.2f"|format(total_paid) }} EUR</div></div>
                <div class="total-card"><div class="muted">{{ tr["unpaid"] }}</div><div class="unpaid-text">{{ "%.2f"|format(total_unpaid) }} EUR</div></div>
                <div class="total-card"><div class="muted">Ukupno</div><b>{{ "%.2f"|format(total_all) }} EUR</b></div>
            </div>
        </div>
    </div>
    """, tr=tr, dark=dark, client=client, rows=rows, date_from=date_from, date_to=date_to, status=status, format_date=format_date, total_paid=total_paid, total_unpaid=total_unpaid, total_all=total_all)


@app.route("/invoices/view")
def invoices_view():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    invoice_number = request.args.get("invoice_number", "").strip()
    conn = get_conn(); c = conn.cursor()
    record_row = c.execute("""
        SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date
        FROM invoice_records WHERE invoice_number = ? AND COALESCE(deleted, 0) = 0
    """, (invoice_number,)).fetchone()
    if not record_row:
        conn.close()
        return redirect("/invoices")
    record = invoice_record_to_dict(record_row)
    row, settings = get_invoice_row_for_record(conn, record)
    conn.close()
    if not row:
        return redirect("/invoices")
    pdf_url = f"/invoices/preview_pdf?invoice_number={urllib.parse.quote(invoice_number)}"
    download_url = f"/invoices/download?client={urllib.parse.quote(row['client'])}&date_from={record['date_from']}&date_to={record['date_to']}&invoice_date={record['invoice_date']}"
    paid_url = f"/invoices/mark_paid?invoice_number={urllib.parse.quote(invoice_number)}&paid={0 if record['paid'] else 1}&client={urllib.parse.quote(row['client'])}&date_from={record['date_from']}&date_to={record['date_to']}&invoice_date={record['invoice_date']}&amount={row['amount']}&vat_amount={row['vat_amount']}&total={row['total']}&next={urllib.parse.quote('/invoices/view?invoice_number=' + invoice_number)}"
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
        .viewer-shell { background:#2b2b2b; color:white; border-radius:10px; padding:24px; }
        .viewer-panel { max-width:1280px; margin:0 auto; background:#555; border-radius:8px; padding:22px 30px; }
        .doc-tabs { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:0; }
        .doc-tab { background:#777; color:white; padding:12px 16px; border-radius:8px 8px 0 0; font-weight:bold; }
        .doc-tab.active { background:#555; }
        .toolbar { display:flex; flex-wrap:wrap; gap:4px; margin:22px 0 0; }
        .tool { background:#888; color:white; border-radius:8px 8px 0 0; padding:12px 16px; font-weight:bold; }
        .tool.active { background:white; color:#111; }
        .tool.pay { background:{{ '#16a34a' if record.paid else '#ef4444' }}; }
        .pdf-frame { background:white; width:100%; height:900px; border:0; }
    </style>
    <div class="viewer-shell">
        <div class="doc-tabs">
            <a class="doc-tab" href="/invoices">Mes documents</a>
            <a class="doc-tab" href="/invoices/client?client={{ row.client|urlencode }}"> {{ row.client }}</a>
            <span class="doc-tab active">{{ row.invoice_number }} <a href="/invoices/client?client={{ row.client|urlencode }}" style="color:white;margin-left:8px;">x</a></span>
        </div>
        <div class="viewer-panel">
            <div class="toolbar">
                <span class="tool active">Facture</span>
                <a class="tool" href="/invoices/devis_pdf?invoice_number={{ row.invoice_number }}">{{ tr["quote"] }}</a>
                <a class="tool" href="/invoices#invoice-profiles">Modifier</a>
                <a class="tool" href="/invoices#invoice-settings">Modeles</a>
                <span class="tool">E-mail</span>
                <span class="tool">Dupliquer</span>
                <a class="tool" href="/invoices/delete?invoice_number={{ row.invoice_number }}" onclick="return confirm('Obrisati fakturu?');">Supprimer</a>
                <a class="tool pay" href="{{ paid_url }}">{{ tr["mark_unpaid"] if record.paid else tr["mark_paid"] }}</a>
                <span class="tool">Recurrent</span>
                <a class="tool" href="{{ download_url }}">Telecharger</a>
            </div>
            <iframe class="pdf-frame" src="{{ pdf_url }}"></iframe>
        </div>
    </div>
    """, tr=tr, dark=dark, row=row, record=record, pdf_url=pdf_url, download_url=download_url, paid_url=paid_url)


@app.route("/invoices/preview_pdf")
def invoices_preview_pdf():
    if session.get("role") != "admin":
        return redirect("/")
    invoice_number = request.args.get("invoice_number", "").strip()
    conn = get_conn(); c = conn.cursor()
    record_row = c.execute("""
        SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date
        FROM invoice_records WHERE invoice_number = ? AND COALESCE(deleted, 0) = 0
    """, (invoice_number,)).fetchone()
    if not record_row:
        conn.close()
        return redirect("/invoices")
    record = invoice_record_to_dict(record_row)
    row, settings = get_invoice_row_for_record(conn, record)
    conn.close()
    if not row:
        return redirect("/invoices")
    pdf = build_invoice_pdf(row, settings, record["invoice_date"], record["date_from"], record["date_to"])
    return send_file(pdf, as_attachment=False, download_name=f"facture_{invoice_number}.pdf", mimetype="application/pdf")


@app.route("/invoices/client_statement")
def invoices_client_statement():
    if session.get("role") != "admin":
        return redirect("/")
    client = request.args.get("client", "").strip()
    default_from, default_to = previous_month_range()
    date_from = request.args.get("date_from", default_from).strip()
    date_to = request.args.get("date_to", default_to).strip()
    conn = get_conn()
    records = fetch_invoice_records(conn, date_from, date_to, client, "all")
    conn.close()
    pdf = build_client_statement_pdf(client, records, date_from, date_to)
    return send_file(pdf, as_attachment=True, download_name=f"releve_{client}_{date_from}_{date_to}.pdf", mimetype="application/pdf")


@app.route("/invoices/export_options")
def invoices_export_options():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    export_type = request.args.get("type", "all").strip()
    default_from, default_to = previous_month_range()
    conn = get_conn()
    profiles = get_invoice_profiles(conn)
    conn.close()
    title = {
        "certificate": tr["annual_certificate"],
        "list": "Lista faktura PDF",
    }.get(export_type, tr["download_all_invoices"])
    action = {
        "certificate": "/invoices/client_statement",
        "list": "/invoices/list_pdf",
    }.get(export_type, "/invoices/download_all")
    return render_template_string(BASE_STYLE + header_html() + """
    <div class="card" style="max-width:760px;margin:auto;">
        <h2>{{ title }}</h2>
        <p class="muted">Izaberi tacan period i po potrebi klijenta.</p>
        <form method="get" action="{{ action }}">
            <label>{{ tr["date_from"] }}</label><input type="date" name="date_from" value="{{ default_from }}" required>
            <label>{{ tr["date_to"] }}</label><input type="date" name="date_to" value="{{ default_to }}" required>
            {% if export_type == 'certificate' %}
                <label>{{ tr["search_client"] }}</label>
                <input name="client" list="invoiceExportClients" required placeholder="{{ tr['search_client'] }}">
            {% else %}
                <label>{{ tr["search_client"] }}</label>
                <input name="client" list="invoiceExportClients" placeholder="{{ tr['all_clients'] }}">
            {% endif %}
            {% if export_type == 'list' %}
                <label>{{ tr["payment_status"] }}</label>
                <select name="status">
                    <option value="all">Sve</option>
                    <option value="paid">{{ tr["paid"] }}</option>
                    <option value="unpaid">{{ tr["unpaid"] }}</option>
                </select>
            {% endif %}
            <datalist id="invoiceExportClients">{% for p in profiles %}<option value="{{ p.client }}"></option>{% endfor %}</datalist>
            <button>{{ tr["download_all_invoices"] if export_type == 'all' else title }}</button>
        </form>
        <br><a class="back-button" href="/invoices">{{ tr["back"] }}</a>
    </div>
    """, tr=tr, dark=dark, title=title, action=action, export_type=export_type, profiles=profiles, default_from=default_from, default_to=default_to)


@app.route("/invoices/list_pdf")
def invoices_list_pdf():
    if session.get("role") != "admin":
        return redirect("/")
    default_from, default_to = previous_month_range()
    date_from = request.args.get("date_from", default_from).strip()
    date_to = request.args.get("date_to", default_to).strip()
    client = request.args.get("client", "").strip()
    status = request.args.get("status", "all").strip()
    conn = get_conn()
    records = fetch_invoice_records(conn, date_from, date_to, client or None, status)
    conn.close()
    pdf = build_invoice_list_pdf(records, date_from, date_to)
    return send_file(pdf, as_attachment=True, download_name=f"liste_factures_{date_from}_{date_to}.pdf", mimetype="application/pdf")


@app.route("/invoices/quote", methods=["GET", "POST"])
def invoices_quote():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    conn = get_conn()
    settings = get_invoice_settings(conn)
    profiles = get_invoice_profiles(conn)
    conn.close()
    if request.method == "POST":
        data = {
            "quote_number": request.form.get("quote_number", "").strip() or f"DEV-{lux_now().strftime('%Y%m%d')}",
            "quote_date": request.form.get("quote_date", lux_now().strftime("%Y-%m-%d")).strip(),
            "client_name": request.form.get("client_name", "").strip(),
            "client_address": request.form.get("client_address", "").strip(),
            "client_email": request.form.get("client_email", "").strip(),
            "amount": request.form.get("amount", "0").strip(),
            "vat_rate": request.form.get("vat_rate", "0.08").strip(),
            "quote_text": request.form.get("quote_text", "").strip(),
            "document_title": tr["quote"].upper(),
        }
        if not data["client_name"]:
            data["client_name"] = "Client"
        pdf = build_quote_pdf(data, settings)
        filename = safe_pdf_name(data["quote_number"], data["client_name"])
        return send_file(pdf, as_attachment=True, download_name=f"{filename}.pdf", mimetype="application/pdf")
    profiles_json = json.dumps(profiles)
    return render_template_string(BASE_STYLE + header_html() + """
    <div class="card" style="max-width:820px;margin:auto;">
        <h2>{{ tr["quote"] }}</h2>
        <p class="muted">Ponuda je odvojena od smjena i radnika. Unesi podatke klijenta, tekst usluge i cijenu.</p>
        <form method="post">
            <div class="grid">
                <div>
                    <label>{{ tr["quote_number"] }}</label>
                    <input name="quote_number" value="DEV-{{ now_code }}" required>
                </div>
                <div>
                    <label>{{ tr["quote_date"] }}</label>
                    <input type="date" name="quote_date" value="{{ today }}" required>
                </div>
            </div>
            <label>{{ tr["search_client"] }}</label>
            <input id="quoteClientSearch" list="quoteClientList" placeholder="{{ tr['search_client'] }}" oninput="fillQuoteClient()" autocomplete="off">
            <datalist id="quoteClientList">{% for p in profiles %}<option value="{{ p.client }}"></option>{% endfor %}</datalist>
            <label>{{ tr["client_name"] }}</label>
            <input id="quoteClientName" name="client_name" required>
            <label>{{ tr["address"] }}</label>
            <textarea id="quoteClientAddress" name="client_address" style="width:100%;min-height:70px;" required></textarea>
            <label>{{ tr["client_email"] }}</label>
            <input id="quoteClientEmail" name="client_email">
            <div class="grid">
                <div>
                    <label>{{ tr["quote_price"] }}</label>
                    <input type="number" step="0.01" name="amount" required>
                </div>
                <div>
                    <label>{{ tr["vat_rate"] }}</label>
                    <select name="vat_rate">
                        <option value="0.08">8%</option>
                        <option value="0.17">17%</option>
                        <option value="0">0%</option>
                    </select>
                </div>
            </div>
            <label>{{ tr["quote_text"] }}</label>
            <textarea name="quote_text" style="width:100%;min-height:150px;" required>Entretien et nettoyage de la maison.</textarea>
            <button>{{ tr["generate_quote"] }}</button>
        </form>
        <br><a class="back-button" href="/invoices">{{ tr["back"] }}</a>
    </div>
    <script>
    var quoteProfiles = {{ profiles_json|safe }};
    function fillQuoteClient(){
        var name = document.getElementById('quoteClientSearch').value;
        var profile = quoteProfiles.find(function(p){ return p.client === name; });
        if(!profile){ return; }
        document.getElementById('quoteClientName').value = profile.client || "";
        document.getElementById('quoteClientAddress').value = profile.address || "";
        document.getElementById('quoteClientEmail').value = profile.email || "";
    }
    </script>
    """, tr=tr, dark=dark, profiles=profiles, profiles_json=profiles_json, today=lux_now().strftime("%Y-%m-%d"), now_code=lux_now().strftime("%Y%m%d"))


@app.route("/invoices/manual", methods=["GET", "POST"])
def invoices_manual():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    conn = get_conn(); c = conn.cursor()
    settings = get_invoice_settings(conn)
    profiles = get_invoice_profiles(conn)
    templates = c.execute(
        "SELECT id, designation, default_amount, default_vat FROM manual_item_templates ORDER BY sort_order, id"
    ).fetchall()

    if request.method == "POST":
        action = request.form.get("action", "save")

        # ── Save article template ─────────────────────────────────────────
        if action == "save_template":
            desig  = request.form.get("tpl_designation", "").strip()
            amount = float(request.form.get("tpl_amount", 0) or 0)
            vat    = float(request.form.get("tpl_vat", 17) or 17)
            if desig:
                c.execute(
                    "INSERT INTO manual_item_templates (designation, default_amount, default_vat) VALUES (?,?,?)",
                    (desig, amount, vat),
                )
                conn.commit()
            conn.close()
            return redirect("/invoices/manual")

        # ── Delete article template ───────────────────────────────────────
        if action == "delete_template":
            tpl_id = request.form.get("tpl_id", "")
            c.execute("DELETE FROM manual_item_templates WHERE id=?", (tpl_id,))
            conn.commit()
            conn.close()
            return redirect("/invoices/manual")

        # ── Save invoice ──────────────────────────────────────────────────
        inv_num       = request.form.get("invoice_number", "").strip()
        form_mode     = request.form.get("mode", "create")   # 'create' | 'edit'
        client_name   = request.form.get("client_name",   "").strip()
        client_addr   = request.form.get("client_address","").strip()
        inv_date      = request.form.get("invoice_date",  lux_now().strftime("%Y-%m-%d")).strip()
        payment_terms = request.form.get("payment_terms", "").strip()

        # Collect line items from form arrays (safe parse, skip empty rows)
        designations = request.form.getlist("designation[]")
        amounts      = request.form.getlist("amount[]")
        vat_rates    = request.form.getlist("vat_rate[]")
        items = []
        total_ht = 0.0; total_vat = 0.0
        for i, desig in enumerate(designations):
            try:
                raw_a = amounts[i].strip() if i < len(amounts) else ""
                amt = float(raw_a) if raw_a else 0.0
            except (ValueError, TypeError):
                amt = 0.0
            try:
                raw_v = vat_rates[i].strip() if i < len(vat_rates) else ""
                vr = float(raw_v) if raw_v else 0.0
            except (ValueError, TypeError):
                vr = 0.0
            desig = desig.strip()
            if not desig and amt == 0.0:
                continue  # skip entirely blank rows
            items.append({"designation": desig, "amount": amt, "vat_rate": vr})
            total_ht  += amt
            total_vat += amt * vr / 100.0
        total_ttc = total_ht + total_vat
        items_json = json.dumps(items, ensure_ascii=False)

        # ── Reserve invoice number: write invoice_records FIRST, verify ownership,
        #    then write the draft.
        #
        # create mode: the number must not exist at all — reassign if taken.
        # edit mode:   allow updating this specific manual invoice.
        existing_src = c.execute(
            "SELECT source FROM invoice_records WHERE invoice_number=? AND COALESCE(deleted,0)=0",
            (inv_num,)
        ).fetchone()
        convert_from_auto = request.form.get("convert_from_auto") == "1"
        if form_mode == "create" and existing_src and not convert_from_auto:
            # Number already taken — get a fresh one
            inv_num = next_invoice_number(conn)
            existing_src = None
        elif form_mode == "edit" and existing_src and existing_src[0] != "manual" and not convert_from_auto:
            # Editing but number points at an auto invoice (not an explicit conversion)
            inv_num = next_invoice_number(conn)
            existing_src = None

        reserved = False
        for _attempt in range(3):
            if form_mode == "create":
                # CREATE: only insert if the number is completely free.
                # ON CONFLICT DO NOTHING is atomic — rowcount==1 means WE
                # inserted it; rowcount==0 means someone else holds it.
                c.execute("""
                    INSERT INTO invoice_records
                        (invoice_number, client_name, date_from, date_to, invoice_date,
                         amount, vat_amount, total, paid, sent, deleted, source)
                    VALUES (?,?,?,?,?,?,?,?,0,0,0,'manual')
                    ON CONFLICT(invoice_number) DO NOTHING
                """, (inv_num, client_name, inv_date, inv_date, inv_date,
                      total_ht, total_vat, total_ttc))
                conn.commit()
                if c.rowcount == 1:
                    reserved = True
                    break
            else:
                if convert_from_auto:
                    # Converting auto → manual: update source unconditionally
                    c.execute("""
                        UPDATE invoice_records SET
                            client_name=?, invoice_date=?,
                            amount=?, vat_amount=?, total=?, source='manual'
                        WHERE invoice_number=? AND COALESCE(deleted,0)=0
                    """, (client_name, inv_date, total_ht, total_vat, total_ttc, inv_num))
                    conn.commit()
                else:
                    # EDIT: update this manual invoice (WHERE guard blocks auto invoices)
                    c.execute("""
                        INSERT INTO invoice_records
                            (invoice_number, client_name, date_from, date_to, invoice_date,
                             amount, vat_amount, total, paid, sent, deleted, source)
                        VALUES (?,?,?,?,?,?,?,?,0,0,0,'manual')
                        ON CONFLICT(invoice_number) DO UPDATE SET
                            client_name=excluded.client_name, invoice_date=excluded.invoice_date,
                            amount=excluded.amount, vat_amount=excluded.vat_amount,
                            total=excluded.total, source='manual'
                        WHERE invoice_records.source='manual'
                    """, (inv_num, client_name, inv_date, inv_date, inv_date,
                          total_ht, total_vat, total_ttc))
                    conn.commit()
                owned = c.execute(
                    "SELECT 1 FROM invoice_records WHERE invoice_number=? AND COALESCE(source,'auto')='manual'",
                    (inv_num,)
                ).fetchone()
                if owned:
                    reserved = True
                    break
            inv_num = next_invoice_number(conn)  # conflict: try next number

        if not reserved:
            conn.close()
            flash(t().get("mi_reserve_error", "Nije moguce rezervisati broj fakture. Pokusajte ponovo."), "error")
            return redirect("/invoices/manual")

        # Draft saved only after ownership is confirmed
        now_str = lux_now().strftime("%Y-%m-%d %H:%M")
        c.execute("""
            INSERT INTO manual_invoice_drafts
                (invoice_number, client_name, client_address, invoice_date, items_json,
                 payment_terms, total_ht, total_vat, total_ttc, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(invoice_number) DO UPDATE SET
                client_name=excluded.client_name, client_address=excluded.client_address,
                invoice_date=excluded.invoice_date, items_json=excluded.items_json,
                payment_terms=excluded.payment_terms, total_ht=excluded.total_ht,
                total_vat=excluded.total_vat, total_ttc=excluded.total_ttc
        """, (inv_num, client_name, client_addr, inv_date, items_json,
              payment_terms, total_ht, total_vat, total_ttc, now_str))
        conn.commit(); conn.close()

        if request.form.get("download_pdf"):
            return redirect(f"/invoices/manual/pdf?invoice_number={inv_num}")
        return redirect("/invoices")

    # ── GET: show form ────────────────────────────────────────────────────
    # Pre-fill from existing draft if invoice_number given
    load_num = request.args.get("invoice_number", "").strip()
    load_auto = request.args.get("load_auto", "").strip()
    draft = {}
    if load_num:
        row = c.execute(
            "SELECT invoice_number, client_name, client_address, invoice_date, "
            "items_json, payment_terms FROM manual_invoice_drafts WHERE invoice_number=?",
            (load_num,)
        ).fetchone()
        if row:
            draft = {"invoice_number": row[0], "client_name": row[1],
                     "client_address": row[2], "invoice_date": row[3],
                     "items_json": row[4], "payment_terms": row[5]}
    if load_auto and not draft:
        rec = c.execute(
            "SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total "
            "FROM invoice_records WHERE invoice_number=? AND COALESCE(deleted,0)=0",
            (load_auto,)
        ).fetchone()
        if rec:
            prof = c.execute(
                "SELECT custom_address FROM client_invoice_profiles WHERE client_name=?",
                (rec[1],)
            ).fetchone()
            client_addr = (prof[0] if prof else "") or ""
            vr = round(rec[6] / rec[5] * 100, 2) if rec[5] else 17.0
            service_title = invoice_service_title(rec[2], rec[3])
            draft = {
                "invoice_number": rec[0],
                "client_name": rec[1],
                "client_address": client_addr,
                "invoice_date": rec[4],
                "items_json": json.dumps([{"designation": service_title, "amount": round(float(rec[5]), 2), "vat_rate": vr}], ensure_ascii=False),
                "payment_terms": settings.get("payment_terms", ""),
                "_convert_from_auto": True,
            }

    convert_from_auto = draft.pop("_convert_from_auto", False)
    auto_num = draft.get("invoice_number") or next_invoice_number(conn)
    default_terms = settings.get("payment_terms", "")
    conn.close()

    try:
        prefill_items = json.loads(draft.get("items_json") or "[]")
    except Exception:
        prefill_items = []
    if not prefill_items:
        prefill_items = [{"designation": "", "amount": "", "vat_rate": 17}]

    templates_list = [{"id": r[0], "designation": r[1], "amount": r[2], "vat": r[3]}
                      for r in templates]

    return render_template_string(BASE_STYLE + header_html() + r"""
<style>
.mi-shell { background:#2b2b2b; color:white; border-radius:10px; padding:0 0 22px 0; overflow:hidden; }
.mi-top { display:flex; align-items:center; justify-content:space-between; gap:18px;
          padding:18px 22px; background:#3d3d3d; }
.mi-brand { font-size:22px; font-weight:800; }
.mi-brand span { background:#ffd429; color:#111; border-radius:6px; padding:2px 6px; }
.mi-body { max-width:1100px; margin:28px auto; padding:0 24px; display:grid;
           grid-template-columns:1fr 340px; gap:24px; }
.mi-main {}
.mi-sidebar {}
.mi-card { background:#3d3d3d; border-radius:10px; padding:18px; margin-bottom:16px; }
.mi-card h3 { margin:0 0 12px; font-size:14px; color:#ffd429; text-transform:uppercase;
              letter-spacing:.05em; }
.mi-label { font-size:12px; color:#9ca3af; margin:10px 0 3px; display:block; }
.mi-input { width:100%; padding:8px 10px; border-radius:7px; border:1px solid #555;
            background:#2b2b2b; color:white; font-size:14px; box-sizing:border-box;
            margin:0; }
.mi-input::placeholder { color:#6b7280; }
.mi-textarea { width:100%; padding:8px 10px; border-radius:7px; border:1px solid #555;
               background:#2b2b2b; color:white; font-size:13px; box-sizing:border-box;
               resize:vertical; margin:0; min-height:70px; }
.mi-row { display:grid; grid-template-columns:1fr 130px 100px 36px; gap:8px;
          align-items:start; margin-bottom:8px; }
.mi-row-hdr { display:grid; grid-template-columns:1fr 130px 100px 36px; gap:8px;
              font-size:11px; color:#9ca3af; text-transform:uppercase;
              letter-spacing:.05em; margin-bottom:4px; }
.mi-del-btn { background:#ef4444; border:none; color:white; border-radius:6px;
              cursor:pointer; padding:0; height:38px; width:36px; font-size:18px; }
.mi-add-btn { width:100%; padding:10px; background:#1f4f82; color:white;
              border:none; border-radius:7px; cursor:pointer; font-size:14px;
              font-weight:600; margin-top:6px; }
.mi-totals { border-top:1px solid #555; margin-top:14px; padding-top:10px; }
.mi-tot-row { display:flex; justify-content:space-between; padding:4px 0;
              font-size:14px; }
.mi-tot-row.big { font-size:18px; font-weight:800; color:#ffd429; }
.mi-save-btn { width:100%; padding:13px; background:#22c55e; color:#111;
               border:none; border-radius:8px; cursor:pointer; font-size:16px;
               font-weight:800; margin-top:8px; }
.mi-pdf-btn  { width:100%; padding:13px; background:#3b82f6; color:white;
               border:none; border-radius:8px; cursor:pointer; font-size:16px;
               font-weight:700; margin-top:8px; }
.tpl-item { display:flex; align-items:center; gap:8px; padding:7px 0;
            border-bottom:1px solid #4a4a4a; }
.tpl-use  { background:#1f4f82; color:white; border:none; border-radius:5px;
            padding:4px 10px; cursor:pointer; font-size:12px; }
.tpl-del  { background:#ef4444; color:white; border:none; border-radius:5px;
            padding:4px 8px; cursor:pointer; font-size:12px; }
.mi-number-box { background:#1e1e20; border-radius:8px; padding:10px 14px;
                 font-size:22px; font-weight:800; color:#ffd429; margin-bottom:6px; }
@media (max-width:760px){
  .mi-body { grid-template-columns:1fr; }
  .mi-row { grid-template-columns:1fr 110px 80px 36px; }
}
</style>

<div class="mi-shell">
  <div class="mi-top">
    <div class="mi-brand">Luxmann <span>{{ tr.get("mi_title","Facture manuelle") }}</span></div>
    <a class="back-button" href="/invoices">{{ tr["back"] }}</a>
  </div>
  {% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
  <div style="background:{% if cat=='error' %}#ef4444{% else %}#22c55e{% endif %};color:white;
              padding:10px 22px;font-weight:600;font-size:14px;">{{ msg }}</div>
  {% endfor %}
  {% endwith %}
  {% if convert_from_auto %}
  <div style="background:#f59e0b;color:#111;padding:10px 22px;font-weight:700;font-size:14px;">
    ✏️ {{ tr.get("inv_convert_banner","Uređuješ automatski generisanu fakturu br. {num} — sačuvaj da pretvoriš u ručnu fakturu.").replace("{num}", auto_num|string) }}
  </div>
  {% endif %}

  <form id="miForm" method="post" action="/invoices/manual">
    <input type="hidden" name="convert_from_auto" value="{{ '1' if convert_from_auto else '' }}">
    <div class="mi-body">

      <!-- LEFT: main form -->
      <div class="mi-main">

        <!-- Invoice number + date -->
        <div class="mi-card" style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
          <div>
            <div class="mi-label">{{ tr.get("mi_invoice_num","Facture n°") }}</div>
            <div class="mi-number-box" id="dispNum">{{ auto_num }}</div>
            <input type="hidden" name="invoice_number" id="invoiceNumber" value="{{ auto_num }}">
            <input type="hidden" name="mode" value="{{ 'edit' if draft.invoice_number else 'create' }}">
          </div>
          <div>
            <div class="mi-label">{{ tr["invoice_date"] }}</div>
            <input class="mi-input" type="date" name="invoice_date"
                   value="{{ draft.invoice_date or today }}">
          </div>
        </div>

        <!-- Client -->
        <div class="mi-card">
          <h3>👤 {{ tr.get("mi_billed_to","Facturé à") }}</h3>
          <label class="mi-label">{{ tr["search_client"] }}</label>
          <input class="mi-input" id="miClientSearch" list="miClientList"
                 placeholder="{{ tr['search_client'] }}" oninput="fillMiClient()" autocomplete="off">
          <datalist id="miClientList">
            {% for p in profiles %}<option value="{{ p.client }}"></option>{% endfor %}
          </datalist>
          <label class="mi-label">{{ tr["client_name"] }}</label>
          <input class="mi-input" name="client_name" id="miClientName"
                 value="{{ draft.client_name or '' }}" required>
          <label class="mi-label">{{ tr.get("mi_billing_address","Adresse de facturation") }}</label>
          <textarea class="mi-textarea" name="client_address" id="miClientAddress"
                    style="min-height:80px;">{{ draft.client_address or '' }}</textarea>
        </div>

        <!-- Line items -->
        <div class="mi-card">
          <h3>📋 {{ tr.get("mi_items_title","Articles / Prestations") }}</h3>
          <div class="mi-row-hdr">
            <span>{{ tr.get("mi_designation","Désignation") }}</span><span>{{ tr.get("mi_amount_ht","Montant HT (€)") }}</span><span>{{ tr.get("mi_vat_col","TVA (%)") }}</span><span></span>
          </div>
          <div id="itemsContainer"></div>
          <button type="button" class="mi-add-btn" onclick="addItem()">{{ tr.get("mi_add_item","+ Ajouter un article") }}</button>

          <div class="mi-totals">
            <div class="mi-tot-row"><span>Total HT</span><span id="totHT">0.00 €</span></div>
            <div class="mi-tot-row"><span>TVA</span><span id="totVAT">0.00 €</span></div>
            <div class="mi-tot-row big"><span>TOTAL EUR</span><span id="totTTC">0.00 €</span></div>
          </div>
        </div>

        <!-- Payment terms -->
        <div class="mi-card">
          <h3>🏦 {{ tr.get("mi_payment_conditions","Conditions et modalités de paiement") }}</h3>
          <textarea class="mi-textarea" name="payment_terms"
                    style="min-height:100px;">{{ draft.payment_terms or default_terms }}</textarea>
        </div>

      </div><!-- /mi-main -->

      <!-- RIGHT: sidebar -->
      <div class="mi-sidebar">

        <!-- Articles sauvegardés -->
        <div class="mi-card">
          <h3>📂 {{ tr.get("mi_saved_items","Articles sauvegardés") }}</h3>
          <div id="tplList">
            {% for tpl in templates_list %}
            <div class="tpl-item">
              <div style="flex:1;font-size:13px;">
                <div style="font-weight:600;">{{ tpl.designation }}</div>
                <div style="font-size:11px;color:#9ca3af;">
                  {{ "%.2f"|format(tpl.amount) }} € · {{ tr.get("mi_vat_short","TVA") }} {{ tpl.vat }}%
                </div>
              </div>
              <button type="button" class="tpl-use"
                      onclick="useTemplate({{ tpl.designation|tojson }}, {{ tpl.amount }}, {{ tpl.vat }})">
                {{ tr.get("mi_use_item","+ Utiliser") }}
              </button>
              <form method="post" action="/invoices/manual" style="display:inline;">
                <input type="hidden" name="action" value="delete_template">
                <input type="hidden" name="tpl_id" value="{{ tpl.id }}">
                <button type="submit" class="tpl-del"
                        onclick='return confirm({{ tr.get("mi_delete_template_confirm","Supprimer ce modèle ?")|tojson }});'>🗑</button>
              </form>
            </div>
            {% else %}
            <div style="font-size:13px;color:#6b7280;">{{ tr.get("mi_no_templates","Aucun modèle sauvegardé.") }}</div>
            {% endfor %}
          </div>

          <!-- Save new template -->
          <details style="margin-top:14px;">
            <summary style="cursor:pointer;font-size:13px;color:#93c5fd;">
              {{ tr.get("mi_save_template_btn","+ Sauvegarder un modèle") }}
            </summary>
            <div style="margin-top:10px;">
              <form method="post" action="/invoices/manual">
                <input type="hidden" name="action" value="save_template">
                <label class="mi-label">{{ tr.get("mi_designation","Désignation") }}</label>
                <textarea class="mi-textarea" name="tpl_designation"
                          style="min-height:55px;" required></textarea>
                <label class="mi-label">{{ tr.get("mi_default_amount","Montant par défaut (€)") }}</label>
                <input class="mi-input" type="number" step="0.01" name="tpl_amount" value="0">
                <label class="mi-label">{{ tr.get("mi_default_vat","TVA par défaut (%)") }}</label>
                <select class="mi-input" name="tpl_vat" style="padding:6px 10px;">
                  <option value="17">17%</option>
                  <option value="8">8%</option>
                  <option value="3">3%</option>
                  <option value="0">0%</option>
                </select>
                <button type="submit" class="mi-add-btn" style="margin-top:10px;">
                  💾 {{ tr.get("mi_save_template","Sauvegarder le modèle") }}
                </button>
              </form>
            </div>
          </details>
        </div>

        <!-- Save / PDF buttons -->
        <div class="mi-card">
          <h3>💾 {{ tr.get("mi_actions","Actions") }}</h3>
          <button type="submit" name="action" value="save" class="mi-save-btn">
            💾 {{ tr.get("mi_save_invoice","Sauvegarder la facture") }}
          </button>
          <button type="submit" name="download_pdf" value="1" class="mi-pdf-btn">
            📄 {{ tr.get("mi_save_pdf","Sauvegarder + PDF") }}
          </button>
        </div>

      </div><!-- /mi-sidebar -->
    </div>
  </form>
</div>

<script>
var miProfiles = {{ profiles|tojson }};
var prefillItems = {{ prefill_items|tojson }};
var miPlaceholder = {{ tr.get("mi_designation_placeholder","Désignation de la prestation...")|tojson }};

function fillMiClient(){
  var name = document.getElementById('miClientSearch').value;
  var p = miProfiles.find(function(x){ return x.client === name; });
  if(!p) return;
  document.getElementById('miClientName').value = p.client || '';
  document.getElementById('miClientAddress').value = p.address || '';
}

function fmtN(n){ return n.toFixed(2) + ' €'; }

function recalc(){
  var rows = document.querySelectorAll('.mi-item-row');
  var ht=0, vat=0;
  rows.forEach(function(r){
    var a = parseFloat(r.querySelector('.mi-amt').value) || 0;
    var v = parseFloat(r.querySelector('.mi-vat').value) || 0;
    ht  += a;
    vat += a * v / 100;
  });
  document.getElementById('totHT').textContent  = fmtN(ht);
  document.getElementById('totVAT').textContent  = fmtN(vat);
  document.getElementById('totTTC').textContent  = fmtN(ht+vat);
}

function addItem(desig, amt, vat){
  desig = desig || '';
  amt   = (amt !== undefined) ? amt : '';
  vat   = (vat !== undefined) ? vat : 17;
  var c = document.getElementById('itemsContainer');
  var d = document.createElement('div');
  d.className = 'mi-row mi-item-row';
  d.innerHTML =
    '<textarea class="mi-textarea mi-desig" name="designation[]" rows="2"'
    +' placeholder="' + escHtml(miPlaceholder) + '" oninput="recalc()">'
    + escHtml(String(desig)) + '</textarea>'
    + '<input class="mi-input mi-amt" type="number" step="0.01" name="amount[]"'
    +' value="'+(amt===''?'':Number(amt).toFixed(2))+'" placeholder="0.00" oninput="recalc()">'
    + '<select class="mi-input mi-vat" name="vat_rate[]" onchange="recalc()">'
    + '<option value="17"'+(vat==17?' selected':'')+'>17%</option>'
    + '<option value="8"'+(vat==8?' selected':'')+'>8%</option>'
    + '<option value="3"'+(vat==3?' selected':'')+'>3%</option>'
    + '<option value="0"'+(vat==0?' selected':'')+'>0%</option>'
    + '</select>'
    + '<button type="button" class="mi-del-btn" onclick="this.closest(\'.mi-item-row\').remove();recalc();">×</button>';
  c.appendChild(d);
  recalc();
}

function useTemplate(desig, amt, vat){ addItem(desig, amt, vat); }

function escHtml(s){
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
          .replace(/"/g,'&quot;');
}

// Load prefill items on page load
prefillItems.forEach(function(it){
  addItem(it.designation, it.amount !== ''? it.amount : '', it.vat_rate || 17);
});
</script>
""", tr=tr, dark=dark, auto_num=auto_num, today=lux_now().strftime("%Y-%m-%d"),
     profiles=profiles,
     convert_from_auto=convert_from_auto,
     draft=type("D", (), draft)() if draft else type("D", (), {"invoice_number":"","client_name":"","client_address":"","invoice_date":"","payment_terms":""})(),
     default_terms=default_terms,
     templates_list=templates_list,
     prefill_items=prefill_items)


@app.route("/invoices/manual/pdf")
def invoices_manual_pdf():
    if session.get("role") != "admin":
        return redirect("/")
    inv_num = request.args.get("invoice_number", "").strip()
    if not inv_num:
        return redirect("/invoices/manual")
    conn = get_conn(); c = conn.cursor()
    row = c.execute(
        "SELECT invoice_number, client_name, client_address, invoice_date, "
        "items_json, payment_terms FROM manual_invoice_drafts WHERE invoice_number=?",
        (inv_num,)
    ).fetchone()
    settings = get_invoice_settings(conn)
    conn.close()
    if not row:
        return redirect("/invoices/manual")
    draft = {"invoice_number": row[0], "client_name": row[1], "client_address": row[2],
             "invoice_date": row[3], "items_json": row[4], "payment_terms": row[5]}
    pdf = build_manual_invoice_pdf(draft, settings)
    fname = safe_pdf_name(inv_num, row[1] or "manuel")
    return send_file(pdf, as_attachment=True, download_name=f"{fname}.pdf",
                     mimetype="application/pdf")


@app.route("/invoices/devis_pdf")
def invoices_devis_pdf():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    invoice_number = request.args.get("invoice_number", "").strip()
    conn = get_conn(); c = conn.cursor()
    record_row = c.execute("""
        SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date
        FROM invoice_records WHERE invoice_number = ? AND COALESCE(deleted, 0) = 0
    """, (invoice_number,)).fetchone()
    if not record_row:
        conn.close()
        return redirect("/invoices")
    record = invoice_record_to_dict(record_row)
    row, settings = get_invoice_row_for_record(conn, record)
    conn.close()
    if not row:
        return redirect("/invoices")
    pdf = build_invoice_pdf(row, settings, record["invoice_date"], record["date_from"], record["date_to"], "DEVIS")
    filename = safe_pdf_name(tr["quote"], invoice_number, row["client"])
    return send_file(pdf, as_attachment=True, download_name=f"{filename}.pdf", mimetype="application/pdf")


@app.route("/invoices/delete")
def invoices_delete():
    if session.get("role") != "admin":
        return redirect("/")
    invoice_number = request.args.get("invoice_number", "").strip()
    if invoice_number:
        conn = get_conn(); c = conn.cursor()
        c.execute("UPDATE invoice_records SET deleted = 1 WHERE invoice_number = ?", (invoice_number,))
        conn.commit(); conn.close()
    return redirect("/invoices")


@app.route("/invoices/settings", methods=["POST"])
def invoices_settings():
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn(); c = conn.cursor()
    c.execute("""
        INSERT INTO invoice_settings (id, invoice_text, payment_terms, bank_account, company_name, company_address, company_phone, company_email, company_vat, invoice_template, invoice_start_number)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET invoice_text = excluded.invoice_text, payment_terms = excluded.payment_terms,
        bank_account = excluded.bank_account, company_name = excluded.company_name, company_address = excluded.company_address,
        company_phone = excluded.company_phone, company_email = excluded.company_email, company_vat = excluded.company_vat,
        invoice_template = excluded.invoice_template, invoice_start_number = excluded.invoice_start_number
    """, (1, request.form.get("invoice_text", "").strip(), request.form.get("payment_terms", "").strip(), request.form.get("bank_account", "").strip(), request.form.get("company_name", "").strip(), request.form.get("company_address", "").strip(), request.form.get("company_phone", "").strip(), request.form.get("company_email", "").strip(), request.form.get("company_vat", "").strip(), request.form.get("invoice_template", "orange").strip(), request.form.get("invoice_start_number", 1) or 1))
    conn.commit(); conn.close()
    return redirect("/invoices")


@app.route("/invoices/profile", methods=["POST"])
def invoices_profile():
    if session.get("role") != "admin":
        return redirect("/")
    client_name = request.form.get("client_name", "").strip()
    if client_name:
        conn = get_conn(); c = conn.cursor()
        c.execute("""
            INSERT INTO client_invoice_profiles (client_name, email, client_type, hourly_rate, custom_address)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(client_name) DO UPDATE SET email = excluded.email, client_type = excluded.client_type, hourly_rate = excluded.hourly_rate, custom_address = excluded.custom_address
        """, (client_name, request.form.get("email", "").strip(), request.form.get("client_type", "private").strip(), request.form.get("hourly_rate", 0) or 0, request.form.get("custom_address", "").strip()))
        conn.commit(); conn.close()
    return redirect("/invoices")


@app.route("/invoices/mark_paid")
def invoices_mark_paid():
    if session.get("role") != "admin":
        return redirect("/")
    invoice_no = request.args.get("invoice_number", "").strip()
    paid = 1 if request.args.get("paid", "0") == "1" else 0
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    invoice_date = request.args.get("invoice_date", "").strip()
    client = request.args.get("client", "").strip()
    next_url = request.args.get("next", "").strip()
    if invoice_no:
        conn = get_conn(); c = conn.cursor()
        c.execute("""
            INSERT INTO invoice_records (invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_number) DO UPDATE SET client_name = excluded.client_name, date_from = excluded.date_from,
            date_to = excluded.date_to, invoice_date = excluded.invoice_date, amount = excluded.amount,
            vat_amount = excluded.vat_amount, total = excluded.total, paid = excluded.paid, paid_date = excluded.paid_date
        """, (
            invoice_no, client, date_from, date_to, invoice_date,
            request.args.get("amount", 0) or 0, request.args.get("vat_amount", 0) or 0, request.args.get("total", 0) or 0,
            paid, lux_now().strftime("%Y-%m-%d") if paid else "",
        ))
        conn.commit(); conn.close()
    if request.args.get("ajax") == "1":
        return {"ok": True, "paid": bool(paid)}
    if next_url.startswith("/invoices"):
        return redirect(next_url)
    return redirect(f"/invoices?date_from={urllib.parse.quote(date_from)}&date_to={urllib.parse.quote(date_to)}&invoice_date={urllib.parse.quote(invoice_date)}")


@app.route("/invoices/mark_sent")
def invoices_mark_sent():
    if session.get("role") != "admin":
        return redirect("/")
    invoice_no = request.args.get("invoice_number", "").strip()
    sent = 1 if request.args.get("sent", "0") == "1" else 0
    if invoice_no:
        conn = get_conn(); c = conn.cursor()
        c.execute("UPDATE invoice_records SET sent = ?, sent_date = ? WHERE invoice_number = ?", (
            sent, lux_now().strftime("%Y-%m-%d") if sent else "", invoice_no,
        ))
        conn.commit(); conn.close()
    if request.args.get("ajax") == "1":
        return {"ok": True, "sent": bool(sent)}
    return redirect(request.referrer or "/invoices")


@app.route("/invoices/download")
def invoices_download():
    if session.get("role") != "admin":
        return redirect("/")
    date_from = request.args.get("date_from", "").strip(); date_to = request.args.get("date_to", "").strip(); invoice_date = request.args.get("invoice_date", lux_now().strftime("%Y-%m-%d")).strip(); client = request.args.get("client", "").strip()
    invoice_number = request.args.get("invoice_number", "").strip()
    conn = get_conn()
    settings = get_invoice_settings(conn)
    row = None
    if invoice_number:
        records = fetch_invoice_records(conn, client=client or None)
        record = next((r for r in records if str(r["invoice_number"]) == invoice_number), None)
        if record:
            row, settings = get_invoice_row_for_record(conn, record)
            invoice_date = record["invoice_date"]
            date_from = record["date_from"]
            date_to = record["date_to"]
    if not row:
        rows = build_invoice_rows(conn, date_from, date_to, None, settings)
        row = next((r for r in rows if r["client"] == client), None)
    conn.close()
    if not row:
        return redirect("/invoices")
    pdf = build_invoice_pdf(row, settings, invoice_date, date_from, date_to)
    filename = safe_pdf_name(row["invoice_number"], row["client"])
    return send_file(pdf, as_attachment=True, download_name=f"{filename}.pdf", mimetype="application/pdf")


@app.route("/invoices/download_all")
def invoices_download_all():
    if session.get("role") != "admin":
        return redirect("/")
    default_from, default_to = previous_month_range()
    date_from = request.args.get("date_from", default_from).strip()
    date_to = request.args.get("date_to", default_to).strip()
    client = request.args.get("client", "").strip()
    conn = get_conn()
    records = fetch_invoice_records(conn, date_from, date_to, client or None, "all")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for record in records:
            row, settings = get_invoice_row_for_record(conn, record)
            if not row:
                continue
            pdf = build_invoice_pdf(row, settings, record["invoice_date"], record["date_from"], record["date_to"])
            zf.writestr(f"{safe_pdf_name(record['invoice_number'], record['client'])}.pdf", pdf.getvalue())
        list_pdf = build_invoice_list_pdf(records, date_from, date_to)
        zf.writestr(f"liste_factures_{date_from}_{date_to}.pdf", list_pdf.getvalue())
    conn.close()
    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name=f"factures_{date_from}_{date_to}.zip", mimetype="application/zip")


@app.route("/invoices/certificate")
def invoices_certificate():
    if session.get("role") != "admin":
        return redirect("/")
    date_from = request.args.get("date_from", "").strip(); date_to = request.args.get("date_to", "").strip(); invoice_date = request.args.get("invoice_date", lux_now().strftime("%Y-%m-%d")).strip(); fixed_amount = request.args.get("fixed_amount", "").strip()
    conn = get_conn(); settings = get_invoice_settings(conn); rows = build_invoice_rows(conn, date_from, date_to, fixed_amount if fixed_amount else None, settings); conn.close()
    pdf = build_invoice_certificate_pdf(rows, invoice_date, date_from, date_to)
    return send_file(pdf, as_attachment=True, download_name=f"certificat_factures_{date_from}_{date_to}.pdf", mimetype="application/pdf")

@app.route("/export_pdf")
def export_pdf():
    if "user" not in session: return redirect("/login")
    tr = t(); is_admin = session.get("role") == "admin"; current_user = session.get("user")
    conn = get_conn(); c = conn.cursor(); date_filter = request.args.get("date", "").strip()
    shifts = c.execute("SELECT * FROM shifts WHERE date = ? ORDER BY date, time, id", (date_filter,)).fetchall() if date_filter else c.execute("SELECT * FROM shifts ORDER BY date, time, id").fetchall()
    if not is_admin: shifts = [s for s in shifts if worker_in_shift(current_user, s[1])]
    title = tr["pdf_title"] + (f" - {format_date(date_filter)}" if date_filter else "")
    conn.close(); buffer = io.BytesIO(); doc = pdf_doc(buffer, title, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet(); elements = []
    if os.path.exists("static/logo.png"): elements += [Image("static/logo.png", width=4*cm, height=2*cm), Spacer(1, 8)]
    elements += [Paragraph(title, styles["Title"]), Spacer(1, 12), Paragraph(f"{tr['pdf_user']}: {session['user']} ({session['role']})", styles["Normal"]), Spacer(1, 12)]
    table_data = [[tr["pdf_date"], tr["pdf_time"], tr["pdf_worker"], tr["pdf_client"], tr["status"]]]
    for s in shifts: table_data.append([format_date(s[3]), s[4], s[1], s[2], get_status_label(get_auto_status(s[3], s[4]), tr)])
    if not shifts: table_data.append(["-", "-", "-", "-", tr["pdf_no_shifts"]])
    table = Table(table_data, colWidths=[2.8*cm, 2.8*cm, 4.0*cm, 4.8*cm, 3.0*cm]); table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4f82")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), 0.5, colors.grey), ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.HexColor("#eaf2fb")]), ("FONTSIZE", (0,0), (-1,-1), 10)])); elements.append(table); doc.build(elements); buffer.seek(0)
    filename = safe_pdf_name("plan", date_filter or "all")
    return send_file(buffer, as_attachment=True, download_name=f"{filename}.pdf", mimetype="application/pdf")


@app.route("/week_pdf")
def week_pdf():
    if "user" not in session:
        return redirect("/login")
    tr = t()
    is_admin = session.get("role") == "admin"
    current_user = session.get("user")
    start_week = get_week_start_from_request()
    end_week = start_week + timedelta(days=6)
    start_date = start_week.strftime("%Y-%m-%d")
    end_date = end_week.strftime("%Y-%m-%d")
    conn = get_conn()
    c = conn.cursor()
    shifts = c.execute("SELECT * FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time, id", (start_date, end_date)).fetchall()
    conn.close()
    if not is_admin:
        shifts = [s for s in shifts if worker_in_shift(current_user, s[1])]
    title = f"{tr['week_calendar']} {format_date(start_date)} - {format_date(end_date)}"
    buffer = io.BytesIO()
    doc = pdf_doc(buffer, title, pagesize=landscape(A4), rightMargin=1*cm, leftMargin=1*cm, topMargin=1*cm, bottomMargin=1*cm)
    styles = getSampleStyleSheet()
    elements = []
    if os.path.exists("static/logo.png"):
        elements += [Image("static/logo.png", width=4*cm, height=2*cm), Spacer(1, 8)]
    elements += [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    table_data = [[tr["pdf_date"], tr["pdf_time"], tr["pdf_worker"], tr["pdf_client"], tr["status"]]]
    for shift in shifts:
        table_data.append([format_date(shift[3]), shift[4], shift[1], shift[2], get_status_label(get_auto_status(shift[3], shift[4]), tr)])
    if not shifts:
        table_data.append(["-", "-", "-", "-", tr["pdf_no_shifts"]])
    table = Table(table_data, colWidths=[3*cm, 3*cm, 6*cm, 7*cm, 4*cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4f82")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.HexColor("#eaf2fb")]),
        ("FONTSIZE", (0,0), (-1,-1), 9),
    ]))
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    filename = safe_pdf_name("week_calendar", start_date, end_date)
    return send_file(buffer, as_attachment=True, download_name=f"{filename}.pdf", mimetype="application/pdf")


@app.route("/month_pdf")
def month_pdf():
    if "user" not in session: return redirect("/login")
    tr = t(); is_admin = session.get("role") == "admin"; current_user = session.get("user")
    year = request.args.get("year", type=int) or datetime.today().year; month = request.args.get("month", type=int) or datetime.today().month
    start_date = f"{year:04d}-{month:02d}-01"; end_date = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    conn = get_conn(); c = conn.cursor(); shifts = c.execute("SELECT * FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time, id", (start_date, end_date)).fetchall(); absences = c.execute("SELECT id, worker, type, date_from, date_to, note FROM absences ORDER BY worker, date_from").fetchall(); conn.close()
    if not is_admin: shifts = [s for s in shifts if worker_in_shift(current_user, s[1])]; absences = [a for a in absences if a[1] == current_user]
    title = f"{tr['month_calendar']} {format_month_year(year, month)}"
    buffer = io.BytesIO(); doc = pdf_doc(buffer, title, pagesize=landscape(A4), rightMargin=1*cm, leftMargin=1*cm, topMargin=1*cm, bottomMargin=1*cm); styles = getSampleStyleSheet(); elements = [Paragraph(title, styles["Title"]), Spacer(1, 10)]
    month_hours = calculate_hours_for_user(shifts, current_user if not is_admin else None)
    absence_totals = absence_totals_by_worker(absences, year, month)
    summary_workers = sorted(set(month_hours.keys()) | set(absence_totals.keys()))
    if summary_workers:
        summary_data = [[tr["pdf_worker"], tr["worked_hours"], tr["sick"], tr["vacation"], tr["other_absence"]]]
        for worker in summary_workers:
            totals = absence_totals.get(worker, {})
            summary_data.append([
                worker,
                f"{month_hours.get(worker, 0):.2f}",
                str(totals.get("sick", 0)),
                str(totals.get("vacation", 0)),
                str(totals.get("other", 0)),
            ])
        summary_table = Table(summary_data, colWidths=[6*cm, 4*cm, 4*cm, 4*cm, 4*cm])
        summary_table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4f82")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("GRID", (0,0), (-1,-1), 0.5, colors.grey), ("FONTSIZE", (0,0), (-1,-1), 9)]))
        elements += [Paragraph(tr["monthly_hours"], styles["Heading2"]), summary_table, Spacer(1, 12)]
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
        conn = get_conn(); c = conn.cursor(); c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", (username, hash_password(password), role))
        if role == "worker": c.execute("INSERT OR IGNORE INTO workers (name, address) VALUES (?, ?)", (username, "")); c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (username, "#f97316"))
        conn.commit(); conn.close()
    return redirect("/admin")

@app.route("/delete_worker/<path:name>")
def delete_worker(name):
    if session.get("role") != "admin" or name == "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor(); c.execute("DELETE FROM workers WHERE name = ?", (name,)); c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (name,)); conn.commit(); conn.close(); return redirect("/workers")

@app.route("/delete_client/<path:name>")
def delete_client(name):
    if session.get("role") != "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor(); c.execute("DELETE FROM clients WHERE name = ?", (name,)); conn.commit(); conn.close(); return redirect("/clients")

@app.route("/delete_shift/<int:id>")
def delete_shift(id):
    if session.get("role") != "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor(); c.execute("DELETE FROM shifts WHERE id = ?", (id,)); conn.commit(); conn.close(); return redirect(request.referrer or "/")

@app.route("/edit_worker/<path:name>", methods=["GET", "POST"])
def edit_worker(name):
    if session.get("role") != "admin": return redirect("/")
    tr = t(); dark = get_theme() == "dark"; conn = get_conn(); c = conn.cursor()
    if request.method == "POST":
        new_name = request.form["name"].strip(); address = request.form["address"].strip(); contract_type = request.form.get("contract_type", "").strip(); contract_end_date = request.form.get("contract_end_date", "").strip()
        if new_name:
            old_color = c.execute("SELECT color FROM worker_colors WHERE worker_name = ?", (name,)).fetchone(); color_value = old_color[0] if old_color else "#f97316"; c.execute("UPDATE workers SET name = ?, address = ?, contract_type = ?, contract_end_date = ? WHERE name = ?", (new_name, address, contract_type, contract_end_date, name))
            for shift_id, worker_text in c.execute("SELECT id, worker FROM shifts").fetchall(): c.execute("UPDATE shifts SET worker = ? WHERE id = ?", (replace_worker_in_shift(worker_text, name, new_name), shift_id))
            c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (name,)); c.execute("INSERT OR REPLACE INTO worker_colors (worker_name, color) VALUES (?, ?)", (new_name, color_value))
        conn.commit(); conn.close(); return redirect("/workers")
    worker = c.execute("SELECT name, address, contract_type, contract_end_date FROM workers WHERE name = ?", (name,)).fetchone(); conn.close()
    if not worker: return redirect("/workers")
    return render_template_string(BASE_STYLE + """<div class="card" style="max-width:500px;margin:auto;"><h2>{{ tr["workers"] }} - {{ tr["edit"] }}</h2><form method="post"><input name="name" value="{{ worker[0] }}" required><input name="address" value="{{ worker[1] }}" placeholder="{{ tr['address'] }}"><input name="contract_type" value="{{ worker[2] }}" placeholder="{{ tr['contract_type'] }}"><label>{{ tr["contract_end_date"] }}</label><input type="date" name="contract_end_date" value="{{ worker[3] }}"><button>{{ tr["save"] }}</button></form><br><a class="back-button" href="/workers">{{ tr["back"] }}</a></div>""", tr=tr, worker=worker, dark=dark)

@app.route("/edit_client/<path:name>", methods=["GET", "POST"])
def edit_client(name):
    if session.get("role") != "admin": return redirect("/")
    tr = t(); dark = get_theme() == "dark"; conn = get_conn(); c = conn.cursor()
    if request.method == "POST":
        new_name = request.form["name"].strip(); address = request.form["address"].strip()
        if new_name: c.execute("UPDATE clients SET name = ?, address = ? WHERE name = ?", (new_name, address, name)); c.execute("UPDATE shifts SET client = ? WHERE client = ?", (new_name, name))
        conn.commit(); conn.close(); return redirect("/clients")
    client = c.execute("SELECT name, address FROM clients WHERE name = ?", (name,)).fetchone(); conn.close()
    if not client: return redirect("/clients")
    return render_template_string(BASE_STYLE + """<div class="card" style="max-width:500px;margin:auto;"><h2>{{ tr["clients"] }} - {{ tr["edit"] }}</h2><form method="post"><input name="name" value="{{ client[0] }}" required><input name="address" value="{{ client[1] }}" placeholder="{{ tr['address'] }}" required><button>{{ tr["save"] }}</button></form><br><a class="back-button" href="/clients">{{ tr["back"] }}</a></div>""", tr=tr, client=client, dark=dark)

@app.route("/edit_shift/<int:id>", methods=["GET", "POST"])
def edit_shift(id):
    if session.get("role") != "admin": return redirect("/")
    tr = t(); dark = get_theme() == "dark"; conn = get_conn(); c = conn.cursor()
    if request.method == "POST":
        worker = join_workers(request.form.getlist("workers")); client = request.form["client"].strip(); date = request.form["date"].strip(); start_time = f"{request.form['start_hour']}:{request.form['start_minute']}"; end_time = f"{request.form['end_hour']}:{request.form['end_minute']}"; status = request.form["status"].strip()
        time_range = f"{start_time}-{end_time}"
        if worker and duplicate_shift_exists(conn, worker, client, date, time_range, exclude_id=id):
            conn.close()
            notice = urllib.parse.quote(tr["duplicate_shift_warning"])
            ref = request.referrer or f"/edit_shift/{id}"
            return redirect(ref + ("&" if "?" in ref else "?") + f"notice={notice}")
        if worker: c.execute("UPDATE shifts SET worker = ?, client = ?, date = ?, time = ?, status = ? WHERE id = ?", (worker, client, date, time_range, status, id)); conn.commit()
        conn.close()
        return_to = request.form.get("return_to", "").strip() or f"/month?year={date[:4]}&month={int(date[5:7])}"
        return redirect(return_to)
    shift = c.execute("SELECT * FROM shifts WHERE id = ?", (id,)).fetchone(); workers = c.execute("SELECT name, address FROM workers ORDER BY name").fetchall(); clients = c.execute("SELECT name, address FROM clients ORDER BY name").fetchall(); conn.close()
    if not shift: return redirect("/")
    start_time, end_time = split_time_range(shift[4]); sh, sm = split_hour_min(start_time); eh, em = split_hour_min(end_time); selected_workers = split_workers(shift[1])
    return_to = request.referrer or f"/month?year={shift[3][:4]}&month={int(shift[3][5:7])}"
    return render_template_string(BASE_STYLE + """<div class="card" style="max-width:520px;margin:auto;"><h2>{{ tr["edit_shift"] }}</h2><form method="post"><input type="hidden" name="return_to" value="{{ return_to }}"><label>{{ tr["choose_worker"] }}</label>{% for w in workers %}{% if w[0] != 'admin' %}<label class="check-row"><input type="checkbox" name="workers" value="{{ w[0] }}" {% if w[0] in selected_workers %}checked{% endif %}>{{ w[0] }}</label>{% endif %}{% endfor %}<div class="client-search-wrapper"><input type="text" id="csInputEdit" class="client-search-input" value="{{ shift[2] }}" placeholder="{{ tr['search_placeholder'] }}" autocomplete="off"><input type="hidden" name="client" id="csHiddenEdit" value="{{ shift[2] }}" required><div class="client-search-dropdown" id="csListEdit"></div></div><input type="date" name="date" value="{{ shift[3] }}" required><label>{{ tr["start_time"] }}</label><div style="display:flex;gap:6px;"><select name="start_hour">{% for h in time_hours %}<option value="{{ h }}" {% if h == sh %}selected{% endif %}>{{ h }}</option>{% endfor %}</select><select name="start_minute">{% for m in time_minutes %}<option value="{{ m }}" {% if m == sm %}selected{% endif %}>{{ m }}</option>{% endfor %}</select></div><label>{{ tr["end_time"] }}</label><div style="display:flex;gap:6px;"><select name="end_hour">{% for h in time_hours %}<option value="{{ h }}" {% if h == eh %}selected{% endif %}>{{ h }}</option>{% endfor %}</select><select name="end_minute">{% for m in time_minutes %}<option value="{{ m }}" {% if m == em %}selected{% endif %}>{{ m }}</option>{% endfor %}</select></div><select name="status"><option value="planned" {% if shift[5] == 'planned' %}selected{% endif %}>{{ tr["status_planned"] }}</option><option value="in_progress" {% if shift[5] == 'in_progress' %}selected{% endif %}>{{ tr["status_in_progress"] }}</option><option value="done" {% if shift[5] == 'done' %}selected{% endif %}>{{ tr["status_done"] }}</option></select><button>{{ tr["save"] }}</button></form><br><a class="back-button" href="/">{{ tr["back"] }}</a></div><script>document.addEventListener('DOMContentLoaded',function(){var CD=[{% for c in clients %}{"name":{{c[0]|tojson}},"addr":{{(c[1] or '')|tojson}}}{% if not loop.last %},{% endif %}{% endfor %}];initClientSearch('csInputEdit','csHiddenEdit','csListEdit',CD);});</script>""", tr=tr, dark=dark, shift=shift, workers=workers, clients=clients, selected_workers=selected_workers, sh=sh, sm=sm, eh=eh, em=em, time_hours=time_hours(), time_minutes=time_minutes(), return_to=return_to)

@app.route("/workers")
def workers_page():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    dark = get_theme() == "dark"
    conn = get_conn()
    c = conn.cursor()
    workers = c.execute("SELECT name, address, contract_type, contract_end_date FROM workers ORDER BY name").fetchall()
    worker_colors = get_worker_colors(conn)
    conn.close()
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
    .workers-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-top:16px;}
    .worker-card{background:{{ '#1d1d1f' if dark else 'white' }};border-radius:12px;padding:16px;
                 border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};display:flex;flex-direction:column;gap:10px;}
    .worker-card-top{display:flex;align-items:center;gap:12px;}
    .worker-avatar{width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;
                   font-weight:800;font-size:18px;color:white;flex-shrink:0;}
    .worker-name{font-weight:700;font-size:16px;}
    .worker-meta{font-size:12px;color:{{ '#94a3b8' if dark else '#64748b' }};}
    .worker-actions{display:flex;gap:8px;margin-top:4px;}
    .worker-actions a,.worker-actions button{width:auto;padding:6px 12px;font-size:12px;margin:0;}
    .worker-color-row{display:flex;align-items:center;gap:8px;}
    .add-worker-card{background:{{ '#191919' if dark else '#f8fbff' }};border:2px dashed {{ '#2c2c30' if dark else '#cbd5e1' }};
                     border-radius:12px;padding:20px;}
    </style>
    <h1>{{ tr["workers"] }}</h1>
    <a class="back-button" href="/">{{ tr["back"] }}</a>
    <div class="add-worker-card" style="margin-top:16px;max-width:500px;">
        <h3 style="margin:0 0 12px;">+ {{ tr["add_worker"] }}</h3>
        <form method="post" action="/add_worker" style="display:flex;flex-direction:column;gap:8px;">
            <input name="worker_name" placeholder="{{ tr['worker_name'] }}" required>
            <input name="address" placeholder="{{ tr['address'] }}">
            <input name="contract_type" placeholder="{{ tr['contract_type'] }}">
            <label style="font-size:13px;">{{ tr["contract_end_date"] }}</label>
            <input type="date" name="contract_end_date">
            <button style="width:auto;align-self:flex-start;">{{ tr["add_worker"] }}</button>
        </form>
    </div>
    <div class="workers-grid">
        {% for w in workers %}
        {% if w[0] != 'admin' %}
        {% set wcolor = worker_colors.get(w[0], '#f97316') %}
        <div class="worker-card">
            <div class="worker-card-top">
                <div class="worker-avatar" style="background:{{ wcolor }};">{{ w[0][0]|upper }}</div>
                <div>
                    <div class="worker-name">{{ w[0] }}</div>
                    {% if w[1] %}<div class="worker-meta">{{ w[1] }}</div>{% endif %}
                    {% if w[2] %}<div class="worker-meta">{{ w[2] }}{% if w[3] %} · {{ w[3][8:10] }}.{{ w[3][5:7] }}.{{ w[3][0:4] }}{% endif %}</div>{% endif %}
                </div>
            </div>
            <div class="worker-color-row">
                <span style="font-size:12px;color:{{ '#94a3b8' if dark else '#64748b' }};">{{ tr.get("color_label","Boja:") }}</span>
                <form method="post" action="/update_worker_color" style="display:flex;align-items:center;gap:6px;">
                    <input type="hidden" name="worker_name" value="{{ w[0] }}">
                    <input type="color" name="color" value="{{ wcolor }}"
                           onchange="this.form.submit()"
                           style="width:32px;height:32px;border:none;background:none;cursor:pointer;padding:0;">
                </form>
            </div>
            <div class="worker-actions">
                <a href="/edit_worker/{{ w[0]|urlencode }}">{{ tr["edit"] }}</a>
                <a href="/delete_worker/{{ w[0]|urlencode }}"
                   onclick='return confirm({{ (tr.get("delete_worker_confirm","Obrisati radnika") ~ " " ~ w[0] ~ "?")|tojson }})'
                   style="color:#dc2626;border:1px solid #fecaca;background:#fff1f2;">{{ tr["delete"] }}</a>
            </div>
        </div>
        {% endif %}
        {% endfor %}
    </div>
    """, tr=tr, dark=dark, workers=workers, worker_colors=worker_colors)


@app.route("/add_worker", methods=["POST"])
def add_worker():
    if session.get("role") != "admin": return redirect("/")
    name = request.form["worker_name"].strip(); address = request.form.get("address", "").strip(); contract_type = request.form.get("contract_type", "").strip(); contract_end_date = request.form.get("contract_end_date", "").strip()
    if name:
        conn = get_conn(); c = conn.cursor(); c.execute("INSERT OR IGNORE INTO workers (name, address, contract_type, contract_end_date) VALUES (?, ?, ?, ?)", (name, address, contract_type, contract_end_date)); c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (name, "#f97316")); conn.commit(); conn.close()
    ref = request.referrer or "/workers"
    return redirect("/workers" if "/workers" in ref else ref)

@app.route("/add_client", methods=["POST"])
def add_client():
    if session.get("role") != "admin": return redirect("/")
    name = request.form["client_name"].strip(); address = request.form.get("address", "").strip()
    if name and address:
        conn = get_conn(); c = conn.cursor(); c.execute("INSERT OR IGNORE INTO clients (name, address) VALUES (?, ?)", (name, address)); conn.commit(); conn.close()
    return redirect("/clients")

@app.route("/add_shift", methods=["POST"])
def add_shift():
    if "user" not in session or session.get("role") != "admin": return redirect("/")
    worker = join_workers(request.form.getlist("workers")); client = request.form["client"].strip(); date = request.form["date"].strip(); start_time = f"{request.form['start_hour']}:{request.form['start_minute']}"; end_time = f"{request.form['end_hour']}:{request.form['end_minute']}"; status = request.form["status"].strip()
    if worker and client and date:
        time_range = f"{start_time}-{end_time}"
        conn = get_conn(); c = conn.cursor()
        if duplicate_shift_exists(conn, worker, client, date, time_range):
            conn.close()
            notice = urllib.parse.quote(t()["duplicate_shift_warning"])
            ref = request.referrer or "/"
            return redirect(ref + ("&" if "?" in ref else "?") + f"notice={notice}")
        c.execute("INSERT INTO shifts (worker, client, date, time, status) VALUES (?, ?, ?, ?, ?)", (worker, client, date, time_range, status)); conn.commit(); conn.close()
    return redirect(request.referrer or "/")


@app.route("/manifest.json")
def pwa_manifest():
    data = {
        "name": "Luxmann Planner",
        "short_name": "Luxmann",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#111113",
        "theme_color": "#1e3a5f",
        "icons": [
            {"src": "/static/logo.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/logo.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    }
    resp = app.response_class(json.dumps(data), mimetype="application/json")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/sw.js")
def service_worker():
    sw = """
const CACHE = 'luxmann-v1';
self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/'])));
    self.skipWaiting();
});
self.addEventListener('activate', e => {
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ));
    self.clients.claim();
});
self.addEventListener('fetch', e => {
    if (e.request.method !== 'GET') return;
    e.respondWith(
        fetch(e.request).catch(() => caches.match(e.request))
    );
});
"""
    resp = app.response_class(sw, mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/admin")
def admin_page():
    if session.get("role") != "admin": return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    conn = get_conn(); c = conn.cursor()
    db_users = c.execute("SELECT id, username, role FROM users ORDER BY username").fetchall()
    workers = c.execute("SELECT name FROM workers ORDER BY name").fetchall()
    worker_colors = get_worker_colors(conn)
    conn.close()
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
    .admin-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-top:20px;}
    .admin-user-row{display:flex;align-items:center;justify-content:space-between;padding:8px 0;
                    border-bottom:1px solid {{ '#2c2c30' if dark else '#f1f5f9' }};}
    .admin-user-row:last-child{border-bottom:none;}
    </style>
    <h1>🔧 Administracija</h1>
    <a class="back-button" href="/">{{ tr["back"] }}</a>
    <div class="admin-grid">

      <div class="card">
        <h3>🔑 {{ tr["change_password"] }}</h3>
        <form method="post" action="/change_password">
          <input name="new_password" type="password" placeholder="{{ tr['new_password'] }}" required>
          <button>{{ tr["save"] }}</button>
        </form>
      </div>

      <div class="card">
        <h3>➕ {{ tr["add_user"] }}</h3>
        <form method="post" action="/add_user">
          <input name="username" placeholder="{{ tr['username'] }}" required>
          <input name="password" type="password" placeholder="{{ tr['password'] }}" required>
          <select name="role">
            <option value="admin">{{ tr['role_admin'] }}</option>
            <option value="worker">{{ tr['role_worker'] }}</option>
          </select>
          <button>{{ tr["add_user"] }}</button>
        </form>
      </div>

      <div class="card">
        <h3>👥 {{ tr["existing_users"] }}</h3>
        {% for u in db_users %}
        <div class="admin-user-row">
          <div><b>{{ u[1] }}</b> <span style="font-size:12px;opacity:0.6;">({{ u[2] }})</span></div>
          {% if u[1] != 'admin' %}
          <a class="delete-link" href="/delete_user/{{ u[0] }}"
             onclick="return confirm('Obrisati korisnika {{ u[1] }}?')"
             style="width:auto;padding:4px 10px;font-size:12px;">{{ tr["delete"] }}</a>
          {% endif %}
        </div>
        {% endfor %}
      </div>

      <div class="card">
        <h3>🎨 {{ tr["worker_colors"] }}</h3>
        {% for w in workers %}
        {% if w[0] != 'admin' %}
        <form method="post" action="/update_worker_color"
              style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
          <input type="hidden" name="worker_name" value="{{ w[0] }}">
          <div style="min-width:120px;font-size:14px;">{{ w[0] }}</div>
          <input type="color" name="color" value="{{ worker_colors.get(w[0], '#1f4f82') }}"
                 onchange="this.form.submit()"
                 style="width:32px;height:32px;border:none;background:none;cursor:pointer;padding:0;">
        </form>
        {% endif %}
        {% endfor %}
      </div>

    </div>
    """, tr=tr, dark=dark, db_users=db_users, workers=workers, worker_colors=worker_colors)


@app.route("/clients")
def clients_page():
    if session.get("role") != "admin": return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    conn = get_conn(); c = conn.cursor()
    clients = c.execute("SELECT name, address FROM clients ORDER BY name").fetchall()
    conn.close()
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
    .clients-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-top:20px;}
    .client-card{background:{{ '#1d1d1f' if dark else 'white' }};border-radius:12px;padding:14px;
                 border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};display:flex;flex-direction:column;gap:8px;}
    .client-card-name{font-weight:700;font-size:15px;}
    .client-card-addr{font-size:12px;color:{{ '#94a3b8' if dark else '#64748b' }};}
    .client-card-actions{display:flex;gap:8px;}
    .client-card-actions a{width:auto;padding:5px 12px;font-size:12px;margin:0;}
    .add-client-card{background:{{ '#191919' if dark else '#f8fbff' }};
                     border:2px dashed {{ '#2c2c30' if dark else '#cbd5e1' }};
                     border-radius:12px;padding:20px;max-width:480px;}
    </style>
    <h1>🏢 {{ tr["clients"] }}</h1>
    <a class="back-button" href="/">{{ tr["back"] }}</a>

    <div class="add-client-card" style="margin-top:16px;">
      <h3 style="margin:0 0 12px;">+ {{ tr["add_client"] }}</h3>
      <form method="post" action="/add_client" style="display:flex;flex-direction:column;gap:8px;">
        <input name="client_name" placeholder="{{ tr['client_name'] }}" required>
        <input name="address" placeholder="{{ tr['address'] }}" required>
        <button style="width:auto;align-self:flex-start;">{{ tr["add_client"] }}</button>
      </form>
    </div>

    <div class="clients-grid">
      {% for cl in clients %}
      <div class="client-card">
        <div class="client-card-name">🏢 {{ cl[0] }}</div>
        {% if cl[1] %}<div class="client-card-addr">📍 {{ cl[1] }}</div>{% endif %}
        <div class="client-card-actions">
          <a href="/edit_client/{{ cl[0]|urlencode }}">{{ tr["edit"] }}</a>
          <a href="/delete_client/{{ cl[0]|urlencode }}"
             onclick="return confirm('Obrisati klijenta: {{ cl[0] }}?')"
             style="color:#dc2626;border:1px solid #fecaca;background:#fff1f2;">{{ tr["delete"] }}</a>
        </div>
      </div>
      {% endfor %}
      {% if clients|length == 0 %}
      <div class="muted" style="padding:20px;">Nema unesenih klijenata.</div>
      {% endif %}
    </div>
    """, tr=tr, dark=dark, clients=clients)


@app.route("/backup")
def backup_page():
    if session.get("role") != "admin": return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    notice = request.args.get("notice", "")
    backups = []
    if os.path.isdir(BACKUP_ROOT):
        for fname in sorted(os.listdir(BACKUP_ROOT), reverse=True):
            if fname.endswith(".zip"):
                fpath = os.path.join(BACKUP_ROOT, fname)
                try:
                    fsize = os.path.getsize(fpath)
                    mtime = os.path.getmtime(fpath)
                    date_str = datetime.fromtimestamp(mtime).strftime("%d.%m.%Y %H:%M")
                except Exception:
                    fsize = 0; date_str = ""
                backups.append({"name": fname, "size": document_size_label(fsize), "date": date_str})
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
    .backup-list{margin-top:20px;display:flex;flex-direction:column;gap:10px;max-width:700px;}
    .backup-item{background:{{ '#1d1d1f' if dark else 'white' }};border-radius:10px;padding:14px 16px;
                 border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
                 display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
    .backup-item-info{flex:1;min-width:0;}
    .backup-item-name{font-weight:600;font-size:14px;word-break:break-all;}
    .backup-item-meta{font-size:12px;color:{{ '#94a3b8' if dark else '#64748b' }};margin-top:2px;}
    .backup-item-actions{display:flex;gap:8px;flex-shrink:0;}
    .backup-item-actions a,.backup-item-actions button{width:auto;padding:5px 12px;font-size:12px;margin:0;}
    .backup-create-card{background:{{ '#191919' if dark else '#f0f9ff' }};
                        border:2px dashed {{ '#2c2c30' if dark else '#bae6fd' }};
                        border-radius:12px;padding:20px;max-width:500px;margin-top:16px;}
    </style>
    <h1>💾 Backup &amp; Restore</h1>
    <a class="back-button" href="/">{{ tr["back"] }}</a>

    {% if notice %}
    <div style="background:#dcfce7;color:#166534;border:1px solid #bbf7d0;border-radius:8px;
                padding:10px 14px;margin-top:14px;max-width:600px;">{{ notice }}</div>
    {% endif %}

    <!-- Create backup -->
    <div class="backup-create-card">
      <h3 style="margin:0 0 8px;">📦 {{ tr.get("backup_create_new","Kreiraj novi backup") }}</h3>
      <p style="font-size:13px;opacity:0.7;margin:0 0 12px;">
        {{ tr.get("backup_create_desc","Kreira ZIP arhivu koja sadrzi bazu podataka i sve uploadovane dokumente. Backup se cuva na persistentnom disku.") }}
      </p>
      <form method="post" action="/backup/create">
        <button style="width:auto;background:#0ea5e9;border-color:#0ea5e9;">💾 {{ tr.get("backup_create_btn","Kreiraj backup sada") }}</button>
      </form>
    </div>

    <!-- Backup list -->
    <h3 style="margin-top:28px;margin-bottom:8px;">📋 {{ tr.get("backup_list_title","Sacuvani backupi") }}</h3>
    {% if backups %}
    <div class="backup-list">
      {% for b in backups %}
      <div class="backup-item">
        <div style="font-size:24px;">🗜️</div>
        <div class="backup-item-info">
          <div class="backup-item-name">{{ b.name }}</div>
          <div class="backup-item-meta">{{ b.date }} · {{ b.size }}</div>
        </div>
        <div class="backup-item-actions">
          <a href="/backup/download/{{ b.name }}">⬇️ {{ tr["download"] }}</a>
          <form method="post" action="/backup/restore/{{ b.name }}"
                onsubmit='return confirm({{ (tr.get("backup_restore_confirm","Restore backup {name}? Baza podataka i dokumenti ce biti zamijenjeni podacima iz backup-a.").replace("{name}", b.name))|tojson }});'
                style="display:inline;">
            <button style="background:#f59e0b;border-color:#f59e0b;">🔄 {{ tr.get("backup_restore_btn","Restore backup") }}</button>
          </form>
          <form method="post" action="/backup/delete/{{ b.name }}"
                onsubmit='return confirm({{ (tr.get("backup_delete_confirm","Obrisati backup {name}?").replace("{name}", b.name))|tojson }});'
                style="display:inline;">
            <button style="background:#ef4444;border-color:#ef4444;">🗑</button>
          </form>
        </div>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="muted" style="padding:16px;">{{ tr.get("backup_empty","Nema sacuvanih backupa. Kreirajte prvi backup gore.") }}</div>
    {% endif %}

    <div style="margin-top:24px;padding:12px 16px;background:{{ '#191919' if dark else '#fffbeb' }};
                border:1px solid {{ '#2c2c30' if dark else '#fde68a' }};border-radius:8px;
                font-size:12px;max-width:600px;">
      ℹ️ <b>{{ tr["note"] }}:</b> {{ tr.get("backup_note_restore","Restore vraca bazu podataka i uploadovane dokumente. Redoslijed: (1) dokumenti se ekstraktuju, (2) baza se importuje — ako ne uspije, rollback. (3) Fajlovi premjesteni tek nakon uspjesnog importa. Greske prijavljene odvojeno.") }}
    </div>
    """, tr=tr, dark=dark, notice=notice, backups=backups)


def _backup_export_db(conn):
    """Export all tables to a dict {table: {columns, rows}} — works on SQLite and PostgreSQL."""
    c = conn.cursor()
    if USE_POSTGRES:
        c.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'"
        )
        tables = [r[0] for r in c.fetchall()]
    else:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [r[0] for r in c.fetchall()]
    export = {}
    errors = []
    for tbl in tables:
        try:
            c.execute(f"SELECT * FROM {tbl}")
            rows = c.fetchall()
            # c.description is now exposed on _PgCursor via property
            if not c.description:
                errors.append(f"WARN: no description for table {tbl}")
                continue
            cols = [d[0] for d in c.description]
            export[tbl] = {"columns": cols, "rows": [list(r) for r in rows]}
        except Exception as ex:
            errors.append(f"ERROR exporting {tbl}: {ex}")
    if errors:
        raise RuntimeError("Backup DB export failed:\n" + "\n".join(errors))
    return export


def _backup_import_db(conn, export):
    """Re-import table data exported by _backup_export_db.
    Atomic: rolls back if any DELETE/INSERT fails, commits only on clean import.
    Sequence reset (PostgreSQL) is best-effort after commit.
    Returns a list of sequence-reset warnings (may be empty) so callers
    can surface them without treating them as a fatal error."""
    c = conn.cursor()
    errors = []

    # Phase 1: delete + insert — must succeed completely or rollback
    for tbl, data in export.items():
        cols = data.get("columns", [])
        rows = data.get("rows", [])
        if not cols:
            continue
        try:
            c.execute(f"DELETE FROM {tbl}")
        except Exception as ex:
            errors.append(f"DELETE {tbl}: {ex}")
            continue
        col_str = ", ".join(cols)
        placeholders = ", ".join(["?"] * len(cols))
        for i, row in enumerate(rows):
            try:
                c.execute(f"INSERT INTO {tbl} ({col_str}) VALUES ({placeholders})", row)
            except Exception as ex:
                errors.append(f"INSERT {tbl}[{i}]: {ex}")

    if errors:
        try:
            conn.rollback()
        except Exception:
            pass
        raise RuntimeError("Restore aborted — rolled back:\n" + "\n".join(errors))

    conn.commit()  # Commit only if no errors

    # Phase 2: reset PostgreSQL sequences — best-effort, RETURN warnings, never raise
    seq_warnings = []
    if USE_POSTGRES:
        try:
            c.execute("""
                SELECT 'SELECT SETVAL(' || quote_literal(seq.relname) ||
                       ', COALESCE(MAX(' || quote_ident(col.attname) || '), 1)) FROM ' ||
                       quote_ident(tbl.relname) || ';'
                FROM pg_class seq
                JOIN pg_depend dep ON dep.objid = seq.oid
                JOIN pg_class tbl ON tbl.oid = dep.refobjid
                JOIN pg_attribute col ON col.attrelid = tbl.oid AND col.attnum = dep.refobjsubid
                WHERE seq.relkind = 'S'
            """)
            stmts = [r[0] for r in c.fetchall()]
            for stmt in stmts:
                try:
                    c.execute(stmt)
                except Exception as ex:
                    seq_warnings.append(f"SETVAL: {ex}")
            conn.commit()
        except Exception as ex:
            seq_warnings.append(f"Sequence reset: {ex}")
    return seq_warnings  # caller decides how to surface these


@app.route("/backup/create", methods=["POST"])
def backup_create():
    if session.get("role") != "admin": return redirect("/")
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    db_label = "postgres" if USE_POSTGRES else "sqlite"
    backup_name = f"Backup_{ts}_{db_label}.zip"
    backup_path = os.path.join(BACKUP_ROOT, backup_name)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(tmp_fd)
    try:
        conn = get_conn()
        db_export = _backup_export_db(conn)
        conn.close()
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("db_export.json",
                        json.dumps(db_export, ensure_ascii=False, default=str))
            if os.path.isdir(DOCUMENT_ROOT):
                for root_dir, dirs, files in os.walk(DOCUMENT_ROOT):
                    for fname in files:
                        fpath = os.path.join(root_dir, fname)
                        arcname = "documents/" + os.path.relpath(
                            fpath, DOCUMENT_ROOT).replace("\\", "/")
                        zf.write(fpath, arcname)
        os.replace(tmp_path, backup_path)
        msg = f"Backup kreiran: {backup_name}"
    except Exception as e:
        try: os.unlink(tmp_path)
        except: pass
        msg = f"Greška pri kreiranju backupa: {e}"
    return redirect("/backup?notice=" + urllib.parse.quote(msg))


@app.route("/backup/download/<path:filename>")
def backup_download(filename):
    if session.get("role") != "admin": return redirect("/")
    safe_name = os.path.basename(filename)
    fpath = os.path.join(BACKUP_ROOT, safe_name)
    if not os.path.exists(fpath):
        return "Backup nije pronađen.", 404
    return send_file(fpath, as_attachment=True, download_name=safe_name, mimetype="application/zip")


@app.route("/backup/restore/<path:filename>", methods=["POST"])
def backup_restore(filename):
    """Restore order (maximally atomic):
    1. Extract all document files into a temp staging dir.
    2. Import DB (rolls back on any error).
    3. Only if both 1+2 succeed, move staged files to final locations.
    If anything in steps 1-2 fails the DB is rolled back and staging is
    cleaned up — storage is never touched. Step 3 file-move failures are
    reported accurately; the DB was already committed at that point."""
    import shutil as _shutil
    if session.get("role") != "admin": return redirect("/")
    safe_name = os.path.basename(filename)
    fpath = os.path.join(BACKUP_ROOT, safe_name)
    if not os.path.exists(fpath):
        return redirect("/backup?notice=" + urllib.parse.quote("Backup nije pronađen."))
    staging_dir = None
    conn = None
    try:
        with zipfile.ZipFile(fpath, "r") as zf:
            names = zf.namelist()

            # ── Step 1: stage document files to temp dir ──────────────────
            doc_files = [n for n in names if n.startswith("documents/") and not n.endswith("/")]
            staging_dir = tempfile.mkdtemp(dir=STORAGE_ROOT, prefix="restore_staging_")
            staging_real = os.path.realpath(staging_dir)
            doc_real     = os.path.realpath(DOCUMENT_ROOT)
            staged = []  # (staged_path, final_dest)
            for arc in doc_files:
                rel = arc[len("documents/"):]
                if not rel:
                    continue
                # Containment check: normalise and assert path stays inside staging_dir
                staged_path = os.path.realpath(os.path.join(staging_dir, rel.replace("/", os.sep)))
                if not staged_path.startswith(staging_real + os.sep):
                    raise ValueError(f"Unsafe path in backup archive: {arc!r}")
                # Also validate final destination stays inside DOCUMENT_ROOT
                final_dest = os.path.realpath(os.path.join(DOCUMENT_ROOT, rel.replace("/", os.sep)))
                if not final_dest.startswith(doc_real + os.sep):
                    raise ValueError(f"Unsafe destination path for: {arc!r}")
                os.makedirs(os.path.dirname(staged_path), exist_ok=True)
                with zf.open(arc) as src, open(staged_path, "wb") as dst:
                    _shutil.copyfileobj(src, dst)
                staged.append((staged_path, final_dest))

            # ── Step 2: import DB (atomic, rolls back on error) ───────────
            if "db_export.json" in names:
                db_export = json.loads(zf.read("db_export.json").decode("utf-8"))
                conn = get_conn()
                seq_warnings = _backup_import_db(conn, db_export)  # raises + rollback on error
                conn.close(); conn = None
                db_msg = "JSON export"
            elif "db.sqlite" in names and not USE_POSTGRES:
                db_data = zf.read("db.sqlite")
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite", dir=STORAGE_ROOT)
                os.close(tmp_fd)
                with open(tmp_path, "wb") as f:
                    f.write(db_data)
                os.replace(tmp_path, SQLITE_PATH)
                db_msg = "SQLite"
                seq_warnings = []
            else:
                raise ValueError("Backup ne sadrži bazu podataka (db_export.json).")

        # ── Step 3: move staged files to final locations ───────────────────
        # DB is committed at this point. File-move errors are reported but
        # do NOT roll back the DB (two separate systems).
        move_errors = []
        for staged_path, final_dest in staged:
            try:
                os.makedirs(os.path.dirname(final_dest), exist_ok=True)
                _shutil.move(staged_path, final_dest)
            except Exception as ex:
                move_errors.append(f"{final_dest}: {ex}")

        _shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir = None

        moved_ok = len(staged) - len(move_errors)
        if move_errors:
            doc_status = f"{moved_ok}/{len(staged)} dokument(a) premješteno"
        else:
            doc_status = f"{len(staged)} dokument(a) obnovljeno"
        parts = [f"Baza ({db_msg}) i {doc_status} iz: {safe_name}"]
        if seq_warnings:
            parts.append(f"⚠️ Sequence reset upozorenja ({len(seq_warnings)}): "
                         + "; ".join(seq_warnings[:2]))
        if move_errors:
            parts.append(f"⚠️ {len(move_errors)} fajl(ova) nije premješten: "
                         + "; ".join(move_errors[:2]))
        msg = " | ".join(parts)

    except Exception as e:
        if conn:
            try: conn.rollback()
            except Exception: pass
            try: conn.close()
            except Exception: pass
        if staging_dir:
            _shutil.rmtree(staging_dir, ignore_errors=True)
        msg = f"Greška pri restauraciji (rollback): {e}"

    return redirect("/backup?notice=" + urllib.parse.quote(msg))


@app.route("/backup/delete/<path:filename>", methods=["POST"])
def backup_delete_file(filename):
    if session.get("role") != "admin": return redirect("/")
    safe_name = os.path.basename(filename)
    fpath = os.path.join(BACKUP_ROOT, safe_name)
    if os.path.exists(fpath):
        os.unlink(fpath)
    return redirect("/backup")


@app.route("/diagram")
def diagram_page():
    import traceback as _tb
    if session.get("role") != "admin": return redirect("/")
    try:
     return _diagram_page_inner()
    except Exception as _e:
     return "<pre style='padding:20px;font-size:13px;'><b>DIAGRAM ERROR:</b>\n" + _tb.format_exc() + "</pre>", 500

def _diagram_page_inner():
    tr = t(); dark = get_theme() == "dark"
    conn = get_conn(); c = conn.cursor()

    # Available years from invoice_records
    year_rows = c.execute(
        "SELECT DISTINCT strftime('%Y', invoice_date) as y FROM invoice_records WHERE deleted=0 AND invoice_date != '' ORDER BY y DESC"
    ).fetchall()
    available_years = [r[0] for r in year_rows if r[0]]
    current_year_str = str(lux_now().year)
    if current_year_str not in available_years:
        available_years.insert(0, current_year_str)
    sel_year = request.args.get("year", current_year_str)
    if sel_year not in available_years:
        sel_year = available_years[0] if available_years else current_year_str

    # Monthly data for selected year
    monthly_rows = c.execute("""
        SELECT
            CAST(strftime('%m', invoice_date) AS INTEGER) as m,
            COALESCE(SUM(amount), 0) as ht,
            COALESCE(SUM(total),  0) as ttc,
            COALESCE(SUM(CASE WHEN paid=1 THEN total ELSE 0 END), 0) as paid_ttc,
            COALESCE(SUM(CASE WHEN paid=0 THEN total ELSE 0 END), 0) as unpaid_ttc,
            COUNT(*) as cnt
        FROM invoice_records
        WHERE deleted=0 AND invoice_date != '' AND strftime('%Y', invoice_date) = ?
        GROUP BY m ORDER BY m
    """, (sel_year,)).fetchall()

    # Build full 12-month arrays (0 for missing months)
    month_ht      = [0.0] * 12
    month_ttc     = [0.0] * 12
    month_paid    = [0.0] * 12
    month_unpaid  = [0.0] * 12
    month_count   = [0]   * 12
    for row in monthly_rows:
        idx = int(row[0]) - 1
        if 0 <= idx < 12:
            month_ht[idx]     = round(row[1], 2)
            month_ttc[idx]    = round(row[2], 2)
            month_paid[idx]   = round(row[3], 2)
            month_unpaid[idx] = round(row[4], 2)
            month_count[idx]  = int(row[5])

    # Cumulative TTC
    cumul = []
    running = 0.0
    for v in month_ttc:
        running += v
        cumul.append(round(running, 2))

    # Totals
    total_ht     = round(sum(month_ht), 2)
    total_ttc    = round(sum(month_ttc), 2)
    total_paid   = round(sum(month_paid), 2)
    total_unpaid = round(sum(month_unpaid), 2)
    total_inv    = sum(month_count)
    best_month_idx  = month_ttc.index(max(month_ttc)) if any(month_ttc) else -1
    active_months   = sum(1 for v in month_ttc if v > 0)
    avg_monthly     = round(total_ttc / max(active_months, 1), 2)

    # Per-client breakdown for the year
    client_rows = c.execute("""
        SELECT client_name, COALESCE(SUM(total),0) as ttc, COUNT(*) as cnt
        FROM invoice_records
        WHERE deleted=0 AND invoice_date != '' AND strftime('%Y', invoice_date) = ?
        GROUP BY client_name ORDER BY ttc DESC LIMIT 12
    """, (sel_year,)).fetchall()
    client_names  = [r[0] or '—' for r in client_rows]
    client_totals = [round(r[1], 2) for r in client_rows]

    # Previous year comparison
    prev_year = str(int(sel_year) - 1)
    prev_row = c.execute(
        "SELECT COALESCE(SUM(total),0) FROM invoice_records WHERE deleted=0 AND invoice_date!='' AND strftime('%Y',invoice_date)=?",
        (prev_year,)
    ).fetchone()
    prev_total = round((prev_row[0] or 0), 2)
    yoy_pct = round(((total_ttc - prev_total) / prev_total * 100) if prev_total > 0 else 0, 1)

    MONTH_NAMES = ['Jan','Feb','Mar','Apr','Maj','Jun','Jul','Aug','Sep','Okt','Nov','Dec']

    conn.close()

    return render_template_string(BASE_STYLE + header_html() + """
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
.kpi-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; margin-bottom:22px; }
.kpi-card { padding:18px 16px; border-radius:14px; background:{{ '#161618' if dark else 'white' }};
    border:1px solid {{ '#1d1d1f' if dark else '#e2e8f0' }}; box-shadow:0 2px 8px rgba(0,0,0,0.06); }
.kpi-label { font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.05em;
    color:{{ '#94a3b8' if dark else '#64748b' }}; margin-bottom:6px; }
.kpi-value { font-size:24px; font-weight:800; line-height:1.1; }
.kpi-sub { font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; margin-top:4px; }
.chart-card { background:{{ '#161618' if dark else 'white' }}; border-radius:16px;
    border:1px solid {{ '#1d1d1f' if dark else '#e2e8f0' }}; padding:20px; margin-bottom:20px;
    box-shadow:0 2px 8px rgba(0,0,0,0.06); }
.chart-title { font-size:14px; font-weight:700; margin-bottom:16px;
    color:{{ '#e2e8f0' if dark else '#1e293b' }}; }
.year-selector { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.year-btn { padding:7px 18px; border-radius:20px; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};
    background:{{ '#1d1d1f' if dark else '#f8fafc' }}; color:{{ '#94a3b8' if dark else '#64748b' }};
    text-decoration:none; font-size:13px; font-weight:600; transition:all 0.15s; }
.year-btn.active, .year-btn:hover { background:#1f4f82; color:white; border-color:#1f4f82; }
.month-table { width:100%; border-collapse:collapse; font-size:13px; margin-top:6px; }
.month-table th { padding:9px 12px; text-align:right; background:{{ '#1d1d1f' if dark else '#f1f5f9' }};
    color:{{ '#94a3b8' if dark else '#475569' }}; font-size:11px; font-weight:600;
    text-transform:uppercase; letter-spacing:0.04em; }
.month-table th:first-child { text-align:left; }
.month-table td { padding:9px 12px; border-bottom:1px solid {{ '#1d1d1f' if dark else '#f8fafc' }};
    text-align:right; }
.month-table td:first-child { text-align:left; font-weight:600; }
.month-table tfoot td { font-weight:700; background:{{ '#141416' if dark else '#eff6ff' }};
    border-top:2px solid {{ '#3b82f6' if dark else '#6366f1' }}; }
.bar-inline { display:inline-block; height:6px; border-radius:3px; background:#3b82f6;
    vertical-align:middle; margin-left:6px; }
.trend-up { color:#4ade80; } .trend-down { color:#f87171; } .trend-flat { color:#94a3b8; }
</style>

<div class="page-content">
  <div class="hero">
    <h1>📊 {{ tr.get("diagram_title","Dijagram zarade") }}</h1>
    <div class="muted">{{ tr.get("diagram_subtitle","Prihod iz faktura") }} — {{ sel_year }}</div>
  </div>

  <!-- Year selector -->
  <div class="chart-card" style="margin-bottom:18px; padding:14px 20px;">
    <div class="year-selector">
      <span style="font-size:13px; font-weight:600; color:{{ '#94a3b8' if dark else '#64748b' }};">{{ tr.get("diagram_year_label","Godina:") }}</span>
      {% for y in available_years %}
      <a href="/diagram?year={{ y }}" class="year-btn {{ 'active' if y == sel_year else '' }}">{{ y }}</a>
      {% endfor %}
    </div>
  </div>

  <!-- KPI cards -->
  <div class="kpi-grid">
    <div class="kpi-card" style="border-left:4px solid #3b82f6;">
      <div class="kpi-label">{{ tr.get("diagram_total_ht","Ukupno HT") }}</div>
      <div class="kpi-value" style="color:{{ '#93c5fd' if dark else '#2563eb' }};">{{ '%.2f'|format(total_ht) }} €</div>
      <div class="kpi-sub">{{ tr.get("diagram_without_vat_note","Bez TVA") }} — {{ total_inv }} faktura</div>
    </div>
    <div class="kpi-card" style="border-left:4px solid #8b5cf6;">
      <div class="kpi-label">{{ tr.get("diagram_total_ttc","Ukupno TTC") }}</div>
      <div class="kpi-value" style="color:{{ '#c4b5fd' if dark else '#7c3aed' }};">{{ '%.2f'|format(total_ttc) }} €</div>
      <div class="kpi-sub">{{ tr.get("diagram_with_vat_note","Sa TVA") }}
        {% if yoy_pct != 0 %}
        · <span class="{{ 'trend-up' if yoy_pct > 0 else 'trend-down' }}">
          {{ '+' if yoy_pct > 0 else '' }}{{ yoy_pct }}% vs {{ prev_year }}
        </span>
        {% endif %}
      </div>
    </div>
    <div class="kpi-card" style="border-left:4px solid #22c55e;">
      <div class="kpi-label">{{ tr.get("diagram_paid_label","Naplaceno") }}</div>
      <div class="kpi-value" style="color:{{ '#4ade80' if dark else '#16a34a' }};">{{ '%.2f'|format(total_paid) }} €</div>
      <div class="kpi-sub">{{ '%.0f'|format(total_paid/total_ttc*100) if total_ttc > 0 else 0 }}% {{ tr.get("diagram_pct_of_ttc","od TTC") }}</div>
    </div>
    <div class="kpi-card" style="border-left:4px solid #f59e0b;">
      <div class="kpi-label">{{ tr.get("diagram_unpaid_label","Neplaceno") }}</div>
      <div class="kpi-value" style="color:{{ '#fbbf24' if dark else '#d97706' }};">{{ '%.2f'|format(total_unpaid) }} €</div>
      <div class="kpi-sub">{{ tr.get("diagram_open_invoices","Otvorene fakture") }}</div>
    </div>
    {% if best_month_idx >= 0 %}
    <div class="kpi-card" style="border-left:4px solid #ec4899;">
      <div class="kpi-label">{{ tr.get("diagram_best_month","Najbolji mjesec") }}</div>
      <div class="kpi-value" style="color:{{ '#f9a8d4' if dark else '#be185d' }};">{{ month_names[best_month_idx] }}</div>
      <div class="kpi-sub">{{ '%.2f'|format(month_ttc[best_month_idx]) }} € TTC</div>
    </div>
    {% endif %}
    <div class="kpi-card" style="border-left:4px solid #06b6d4;">
      <div class="kpi-label">{{ tr.get("diagram_avg_month","Prosjek / mj") }}</div>
      <div class="kpi-value" style="color:{{ '#67e8f9' if dark else '#0891b2' }};">
        {{ '%.2f'|format(avg_monthly) }} €
      </div>
      <div class="kpi-sub">TTC, {{ tr.get("diagram_active_months_abbr","aktivni mj") }}: {{ active_months }}</div>
    </div>
  </div>

  <!-- Main bar + line chart -->
  <div class="chart-card">
    <div class="chart-title">📈 {{ tr.get("diagram_revenue_by_month","Prihod po mjesecima") }} — {{ sel_year }}</div>
    <canvas id="mainChart" style="max-height:340px;"></canvas>
  </div>

  <!-- Naplaćeno vs Nenaplaćeno stacked -->
  <div class="chart-card">
    <div class="chart-title">💳 {{ tr.get("diagram_paid_vs_unpaid_ttc","Naplaceno vs Neplaceno (TTC)") }}</div>
    <canvas id="paidChart" style="max-height:260px;"></canvas>
  </div>

  <!-- Per-client horizontal bar -->
  {% if client_names %}
  <div class="chart-card">
    <div class="chart-title">🏢 {{ tr.get("diagram_revenue_by_client","Prihod po klijentu") }} — {{ sel_year }} (Top {{ client_names|length }})</div>
    <canvas id="clientChart" style="max-height:{{ [client_names|length * 44, 360]|min }}px;"></canvas>
  </div>
  {% endif %}

  <!-- Monthly data table -->
  <div class="chart-card">
    <div class="chart-title">📋 {{ tr.get("diagram_details_by_month","Detalji po mjesecima") }}</div>
    <div style="overflow-x:auto;">
    <table class="month-table">
      <thead>
        <tr>
          <th>{{ tr.get("diagram_month_col","Mj.") }}</th>
          <th>HT (€)</th>
          <th>TVA (€)</th>
          <th>TTC (€)</th>
          <th>{{ tr.get("diagram_paid_label","Naplaceno") }}</th>
          <th>{{ tr.get("diagram_unpaid_label","Neplaceno") }}</th>
          <th>{{ tr.get("diagram_num_invoices_abbr","Br. fakt.") }}</th>
          <th>{{ tr.get("diagram_cumulative","Kumulativ") }}</th>
        </tr>
      </thead>
      <tbody>
      {% for i in range(12) %}
      {% set pct = (month_ttc[i]/total_ttc*100)|round(0)|int if total_ttc > 0 else 0 %}
      <tr style="{{ 'opacity:0.4;' if month_ttc[i] == 0 else '' }}{{ 'background:' + ('#141416' if dark else '#eff6ff') + ';' if i == best_month_idx else '' }}">
        <td>
          {{ month_names[i] }}
          {% if i == best_month_idx and month_ttc[i] > 0 %}
          <span style="font-size:10px; background:#ec4899; color:white; padding:1px 5px; border-radius:4px; margin-left:4px;">{{ tr.get("diagram_best_badge","★ best") }}</span>
          {% endif %}
        </td>
        <td>{{ '%.2f'|format(month_ht[i]) if month_ht[i] > 0 else '—' }}</td>
        <td style="color:{{ '#94a3b8' if dark else '#64748b' }}; font-size:12px;">{{ '%.2f'|format(month_ttc[i] - month_ht[i]) if month_ttc[i] > 0 else '—' }}</td>
        <td style="font-weight:700; color:{{ '#c4b5fd' if dark else '#7c3aed' }};">{{ '%.2f'|format(month_ttc[i]) if month_ttc[i] > 0 else '—' }}</td>
        <td style="color:{{ '#4ade80' if dark else '#16a34a' }};">{{ '%.2f'|format(month_paid[i]) if month_paid[i] > 0 else '—' }}</td>
        <td style="color:{{ '#fbbf24' if dark else '#d97706' }};">{{ '%.2f'|format(month_unpaid[i]) if month_unpaid[i] > 0 else '—' }}</td>
        <td style="text-align:center;">{{ month_count[i] if month_count[i] > 0 else '—' }}</td>
        <td style="color:{{ '#94a3b8' if dark else '#64748b' }}; font-size:12px;">
          {% if cumul[i] > 0 %}{{ '%.2f'|format(cumul[i]) }}{% else %}—{% endif %}
        </td>
      </tr>
      {% endfor %}
      </tbody>
      <tfoot>
        <tr>
          <td>{{ tr.get("diagram_total_row","UKUPNO") }}</td>
          <td>{{ '%.2f'|format(total_ht) }} €</td>
          <td style="font-size:12px;">{{ '%.2f'|format(total_ttc - total_ht) }} €</td>
          <td style="color:{{ '#c4b5fd' if dark else '#7c3aed' }};">{{ '%.2f'|format(total_ttc) }} €</td>
          <td style="color:{{ '#4ade80' if dark else '#16a34a' }};">{{ '%.2f'|format(total_paid) }} €</td>
          <td style="color:{{ '#fbbf24' if dark else '#d97706' }};">{{ '%.2f'|format(total_unpaid) }} €</td>
          <td style="text-align:center;">{{ total_inv }}</td>
          <td></td>
        </tr>
      </tfoot>
    </table>
    </div>
  </div>
</div>

<script>
(function(){
  var isDark = {{ 'true' if dark else 'false' }};
  var textColor  = isDark ? '#94a3b8' : '#475569';
  var gridColor  = isDark ? '#1d1d1f' : '#f1f5f9';
  var tooltipBg  = isDark ? '#1d1d1f' : '#ffffff';
  var tooltipTxt = isDark ? '#e2e8f0' : '#1e293b';

  var months    = {{ month_names | tojson }};
  var ht        = {{ month_ht | tojson }};
  var ttc       = {{ month_ttc | tojson }};
  var paid      = {{ month_paid | tojson }};
  var unpaid    = {{ month_unpaid | tojson }};
  var cumul     = {{ cumul | tojson }};
  var cliNames  = {{ client_names | tojson }};
  var cliTotals = {{ client_totals | tojson }};

  var commonOpts = {
    responsive: true,
    plugins: {
      legend: { labels: { color: textColor, font: { size: 12 } } },
      tooltip: {
        backgroundColor: tooltipBg, titleColor: tooltipTxt, bodyColor: tooltipTxt,
        borderColor: isDark ? '#2c2c30' : '#e2e8f0', borderWidth: 1,
        callbacks: { label: function(ctx){ return ' ' + ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(2) + ' €'; } }
      }
    },
    scales: {
      x: { ticks: { color: textColor }, grid: { color: gridColor } },
      y: { ticks: { color: textColor, callback: function(v){ return v.toLocaleString('fr-LU') + ' €'; } },
           grid: { color: gridColor } }
    }
  };

  /* ── Main chart: HT bars + TTC line + cumulative line ── */
  new Chart(document.getElementById('mainChart'), {
    data: {
      labels: months,
      datasets: [
        { type:'bar', label:'HT (€)', data: ht, backgroundColor: isDark ? 'rgba(59,130,246,0.55)' : 'rgba(59,130,246,0.45)',
          borderColor: '#3b82f6', borderWidth:1.5, borderRadius:6, order:2 },
        { type:'bar', label:'TTC (€)', data: ttc, backgroundColor: isDark ? 'rgba(139,92,246,0.45)' : 'rgba(139,92,246,0.35)',
          borderColor: '#8b5cf6', borderWidth:1.5, borderRadius:6, order:2 },
        { type:'line', label:{{ tr.get("diagram_cumulative_ttc","Kumulativ TTC")|tojson }}, data: cumul, borderColor:'#f59e0b', backgroundColor:'transparent',
          borderWidth:2.5, pointBackgroundColor:'#f59e0b', pointRadius:3, tension:0.3,
          yAxisID:'yCumul', order:1 }
      ]
    },
    options: Object.assign({}, commonOpts, {
      plugins: Object.assign({}, commonOpts.plugins, { legend: { labels: { color: textColor } } }),
      scales: {
        x: commonOpts.scales.x,
        y: Object.assign({}, commonOpts.scales.y, { position:'left' }),
        yCumul: { position:'right', ticks: { color:'#f59e0b', callback: function(v){ return v.toLocaleString('fr-LU') + ' €'; } },
                  grid: { drawOnChartArea: false } }
      }
    })
  });

  /* ── Paid vs Unpaid stacked bar ── */
  new Chart(document.getElementById('paidChart'), {
    type:'bar',
    data: {
      labels: months,
      datasets: [
        { label:{{ tr.get("diagram_paid_label","Naplaceno")|tojson }}, data: paid, backgroundColor: isDark ? 'rgba(34,197,94,0.6)' : 'rgba(22,163,74,0.5)',
          borderColor:'#22c55e', borderWidth:1.5, borderRadius:4, stack:'s' },
        { label:{{ tr.get("diagram_unpaid_label","Neplaceno")|tojson }}, data: unpaid, backgroundColor: isDark ? 'rgba(245,158,11,0.5)' : 'rgba(217,119,6,0.4)',
          borderColor:'#f59e0b', borderWidth:1.5, borderRadius:4, stack:'s' }
      ]
    },
    options: Object.assign({}, commonOpts, {
      plugins: Object.assign({}, commonOpts.plugins, {
        tooltip: Object.assign({}, commonOpts.plugins.tooltip, {
          callbacks: { label: function(ctx){ return ' ' + ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(2) + ' €'; } }
        })
      })
    })
  });

  /* ── Per-client horizontal bar ── */
  {% if client_names %}
  new Chart(document.getElementById('clientChart'), {
    type:'bar',
    data: {
      labels: cliNames,
      datasets: [{
        label:'TTC (€)', data: cliTotals,
        backgroundColor: cliNames.map(function(_,i){
          var palette = ['#3b82f6','#8b5cf6','#ec4899','#f59e0b','#22c55e','#06b6d4',
                         '#f97316','#a855f7','#14b8a6','#ef4444','#84cc16','#64748b'];
          var c = palette[i % palette.length];
          return c + (isDark ? '99' : '77');
        }),
        borderColor: cliNames.map(function(_,i){
          var palette = ['#3b82f6','#8b5cf6','#ec4899','#f59e0b','#22c55e','#06b6d4',
                         '#f97316','#a855f7','#14b8a6','#ef4444','#84cc16','#64748b'];
          return palette[i % palette.length];
        }),
        borderWidth: 1.5, borderRadius: 6
      }]
    },
    options: {
      indexAxis:'y',
      responsive:true,
      plugins: {
        legend:{ display:false },
        tooltip:{ backgroundColor:tooltipBg, titleColor:tooltipTxt, bodyColor:tooltipTxt,
          borderColor: isDark ? '#2c2c30' : '#e2e8f0', borderWidth:1,
          callbacks:{ label: function(ctx){ return ' ' + ctx.parsed.x.toFixed(2) + ' €'; } } }
      },
      scales: {
        x: { ticks:{ color:textColor, callback: function(v){ return v.toLocaleString('fr-LU') + ' €'; } },
             grid:{ color:gridColor } },
        y: { ticks:{ color:textColor, font:{ size:12 } }, grid:{ color:gridColor } }
      }
    }
  });
  {% endif %}
})();
</script>
""", tr=tr, dark=dark, sel_year=sel_year, available_years=available_years,
     month_ht=month_ht, month_ttc=month_ttc, month_paid=month_paid,
     month_unpaid=month_unpaid, month_count=month_count, cumul=cumul,
     total_ht=total_ht, total_ttc=total_ttc, total_paid=total_paid,
     total_unpaid=total_unpaid, total_inv=total_inv,
     best_month_idx=best_month_idx, yoy_pct=yoy_pct, prev_year=prev_year,
     month_names=MONTH_NAMES, client_names=client_names, client_totals=client_totals,
     active_months=active_months, avg_monthly=avg_monthly)


@app.route("/api/search")
def api_search():
    if "user" not in session:
        return {"shifts": [], "workers": [], "clients": [], "invoices": []}
    q = request.args.get("q", "").strip().lower()
    if len(q) < 2:
        return {"shifts": [], "workers": [], "clients": [], "invoices": []}
    from flask import jsonify
    conn = get_conn(); c = conn.cursor()
    is_admin = session.get("role") == "admin"
    current_user = session.get("user")
    _like = f"%{q}%"
    if is_admin:
        shifts_rows = c.execute(
            "SELECT worker, client, date, time FROM shifts"
            " WHERE LOWER(worker) LIKE ? OR LOWER(client) LIKE ?"
            " ORDER BY date DESC LIMIT 8",
            (_like, _like),
        ).fetchall()
    else:
        # Delimiter-aware exact match in SQL so LIMIT 8 applies after the user
        # restriction. Escape LIKE metacharacters (%, _, !) in the username so
        # special chars never act as wildcards. '!' is the escape character —
        # it avoids backslash handling differences between SQLite and PostgreSQL.
        _ue = current_user.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        _user_pattern = f"%,{_ue},%"
        shifts_rows = c.execute(
            "SELECT worker, client, date, time FROM shifts"
            " WHERE ',' || REPLACE(LOWER(worker), ', ', ',') || ',' LIKE ? ESCAPE '!'"
            " AND (LOWER(worker) LIKE ? OR LOWER(client) LIKE ?)"
            " ORDER BY date DESC LIMIT 8",
            (_user_pattern, _like, _like),
        ).fetchall()
        shifts_rows = [r for r in shifts_rows if worker_in_shift(current_user, r[0])]
    shifts = [{"name": r[1], "sub": f"{r[0]} · {r[2]}", "url": f"/week?start={r[2]}"}
              for r in shifts_rows]
    workers = [{"name": r[0], "sub": r[1] or "", "url": "/workers"}
               for r in c.execute("SELECT name, address FROM workers WHERE LOWER(name) LIKE ? LIMIT 6",
                                  (_like,)).fetchall()]
    clients = [{"name": r[0], "sub": r[1] or "", "url": "/clients"}
               for r in c.execute("SELECT name, address FROM clients WHERE LOWER(name) LIKE ? OR LOWER(address) LIKE ? LIMIT 6",
                                  (_like, _like)).fetchall()]
    invoices = [{"name": r[0], "sub": f"{r[1]} · {r[2]} €", "url": "/invoices"}
                for r in c.execute("SELECT invoice_number, client_name, total FROM invoice_records WHERE deleted=0 AND (LOWER(invoice_number) LIKE ? OR LOWER(client_name) LIKE ?) LIMIT 6",
                                   (_like, _like)).fetchall()]
    conn.close()
    return jsonify({"shifts": shifts, "workers": workers, "clients": clients, "invoices": invoices})


@app.route("/payroll/save_settings", methods=["POST"])
def payroll_save_settings():
    if session.get("role") != "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor()
    names       = request.form.getlist("wname[]")
    sal_types   = request.form.getlist("wsaltype[]")
    rates       = request.form.getlist("wrate[]")
    fixeds      = request.form.getlist("wfixed[]")
    classes     = request.form.getlist("wclass[]")
    children    = request.form.getlist("wchildren[]")
    notes_l     = request.form.getlist("wnotes[]")
    for i, name in enumerate(names):
        sal_type = sal_types[i] if i < len(sal_types) else 'hourly'
        try:    rate = float(rates[i] if i < len(rates) else 0)
        except: rate = 0.0
        try:    fixed_g = float(fixeds[i] if i < len(fixeds) else 0)
        except: fixed_g = 0.0
        try:    kids = int(children[i] if i < len(children) else 0)
        except: kids = 0
        tc    = classes[i] if i < len(classes) else '1'
        notes = notes_l[i] if i < len(notes_l) else ''
        c.execute("""
            INSERT INTO worker_payroll_settings
                (worker, salary_type, hourly_rate, fixed_gross, tax_class, num_children, notes)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(worker) DO UPDATE SET
                salary_type=excluded.salary_type, hourly_rate=excluded.hourly_rate,
                fixed_gross=excluded.fixed_gross, tax_class=excluded.tax_class,
                num_children=excluded.num_children, notes=excluded.notes
        """, (name, sal_type, rate, fixed_g, tc, kids, notes))
    conn.commit(); conn.close()
    return redirect("/payroll")


@app.route("/payroll", methods=["GET", "POST"])
def payroll_page():
    if session.get("role") != "admin": return redirect("/")
    tr   = t(); dark = get_theme() == "dark"
    conn = get_conn(); c = conn.cursor()

    workers_list = [r[0] for r in c.execute("SELECT name FROM workers ORDER BY name").fetchall()]
    raw_settings = {r[0]: {'salary_type': r[1], 'hourly_rate': r[2], 'fixed_gross': r[3], 'tax_class': r[4], 'num_children': r[5], 'notes': r[6] or ''}
                    for r in c.execute("SELECT worker, salary_type, hourly_rate, fixed_gross, tax_class, num_children, notes FROM worker_payroll_settings").fetchall()}
    settings = {}
    for w in workers_list:
        settings[w] = raw_settings.get(w, {'salary_type': 'hourly', 'hourly_rate': LUX_SSM_HORAIRE, 'fixed_gross': 0.0, 'tax_class': '1', 'num_children': 0, 'notes': ''})

    results  = []
    date_from = request.form.get("date_from", "")
    date_to   = request.form.get("date_to", "")

    if request.method == "POST" and request.form.get("action") == "calculate" and date_from and date_to:
        all_shifts = c.execute("SELECT worker, time, date FROM shifts WHERE date >= ? AND date <= ?", (date_from, date_to)).fetchall()
        for w in workers_list:
            ws       = settings.get(w, {})
            sal_type = ws.get('salary_type', 'hourly')
            tc       = ws.get('tax_class', '1')
            total_h  = 0.0
            for sw, stime, _ in all_shifts:
                if w in split_workers(sw):
                    total_h += parse_shift_hours(stime)
            if sal_type == 'fixed':
                fixed_base = float(ws.get('fixed_gross') or 0)
                if fixed_base <= 0 or total_h <= 0: continue
                # Prorata: fiksni_bruto × (odradjeni_sati / 173.33h)
                gross = round(fixed_base * total_h / LUX_STD_MONTHLY_HOURS, 2)
                result = calc_lux_payroll(gross, tc, total_h)
                result['worker']     = w
                result['sal_type']   = 'fixed'
                result['rate']       = None
                result['fixed_base'] = fixed_base  # originalni fiksni bruto/mj
                result['tax_class']  = tc
            else:
                rate = float(ws.get('hourly_rate') or 0)
                if rate <= 0 or total_h <= 0: continue
                gross  = total_h * rate
                result = calc_lux_payroll(gross, tc, total_h)
                result['worker']     = w
                result['sal_type']   = 'hourly'
                result['rate']       = rate
                result['fixed_base'] = None
                result['tax_class']  = tc
            results.append(result)

    conn.close()
    tot = lambda key: sum(r[key] for r in results)

    return render_template_string(BASE_STYLE + header_html() + """
<style>
.payroll-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:14px; margin-bottom:20px; }
.payroll-worker-card { background:{{ '#1d1d1f' if dark else 'white' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; border-radius:12px; padding:16px; }
.pw-name { font-weight:700; font-size:15px; margin-bottom:12px; display:flex; align-items:center; gap:8px; }
.pw-field { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:8px; }
.pw-field label { font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; margin-bottom:2px; display:block; }
.pw-field input, .pw-field select { width:100%; padding:7px 10px; border-radius:8px; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }}; background:{{ '#111113' if dark else '#f8fafc' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }}; font-size:13px; box-sizing:border-box; }
.result-table { width:100%; border-collapse:collapse; font-size:13px; }
.result-table th { padding:10px 12px; text-align:left; background:{{ '#1d1d1f' if dark else '#f1f5f9' }}; color:{{ '#94a3b8' if dark else '#475569' }}; font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:0.04em; border-bottom:2px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
.result-table td { padding:10px 12px; border-bottom:1px solid {{ '#1d1d1f' if dark else '#f1f5f9' }}; vertical-align:top; }
.result-table tr:hover td { background:{{ '#1d1d1f' if dark else '#f8fafc' }}; }
.result-table tfoot td { font-weight:700; background:{{ '#141416' if dark else '#eef2ff' }}; border-top:2px solid {{ '#3b82f6' if dark else '#6366f1' }}; }
.deduction-row { display:flex; justify-content:space-between; font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; margin:1px 0; }
.deduction-row.bold { font-weight:700; color:{{ '#ef4444' if dark else '#dc2626' }}; font-size:12px; }
.net-amount { font-weight:800; font-size:15px; color:{{ '#4ade80' if dark else '#16a34a' }}; }
.employer-amount { font-size:11px; color:{{ '#fbbf24' if dark else '#d97706' }}; }
.legend-box { display:flex; flex-wrap:wrap; gap:10px; margin:14px 0; font-size:12px; }
.legend-item { display:flex; align-items:center; gap:5px; }
.legend-dot { width:10px; height:10px; border-radius:50%; }
</style>
<div class="page-content">
  <div class="hero">
    <h1>💰 {{ tr.get("payroll_title","Obracun plata — Luksemburg") }}</h1>
    <div class="muted">CCSS (maladie 3.05% · pension 8% · dépendance 1.4%) · Retenue d'impôt · 2025</div>
  </div>

  <!-- Settings card -->
  <div class="card" style="margin-bottom:20px;">
    <div class="section-title"><h3>⚙️ {{ tr.get("payroll_settings_per_worker","Podesavanja po radniku") }}</h3></div>
    <form method="post" action="/payroll/save_settings">
      <div class="payroll-grid">
        {% for w in workers_list %}
        {% set ws = settings[w] %}
        {% set wi = loop.index0 %}
        <div class="payroll-worker-card" id="pwcard_{{ wi }}">
          <div class="pw-name">👤 {{ w }}</div>
          <input type="hidden" name="wname[]" value="{{ w }}">

          <!-- Tip plate: satnica ili fiksna -->
          <div style="margin-bottom:10px;">
            <label style="font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; display:block; margin-bottom:6px;">{{ tr.get("payroll_salary_type_label","Tip plate") }}</label>
            <div style="display:flex; gap:0; border-radius:8px; overflow:hidden; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};">
              <label style="flex:1; text-align:center; padding:7px 4px; cursor:pointer; font-size:12px; font-weight:600;
                background:{% if ws.salary_type == 'hourly' %}{{ '#1f4f82' if dark else '#1f4f82' }}{% else %}{{ '#111113' if dark else '#f1f5f9' }}{% endif %};
                color:{% if ws.salary_type == 'hourly' %}white{% else %}{{ '#94a3b8' if dark else '#64748b' }}{% endif %};"
                id="lbl_hourly_{{ wi }}">
                <input type="radio" name="wsaltype[]" value="hourly"
                  {% if ws.salary_type != 'fixed' %}checked{% endif %}
                  onchange="toggleSalType({{ wi }}, 'hourly')" style="display:none;">
                🕐 {{ tr.get("payroll_hourly_label","Satnica") }}
              </label>
              <label style="flex:1; text-align:center; padding:7px 4px; cursor:pointer; font-size:12px; font-weight:600;
                background:{% if ws.salary_type == 'fixed' %}{{ '#1f4f82' if dark else '#1f4f82' }}{% else %}{{ '#111113' if dark else '#f1f5f9' }}{% endif %};
                color:{% if ws.salary_type == 'fixed' %}white{% else %}{{ '#94a3b8' if dark else '#64748b' }}{% endif %};"
                id="lbl_fixed_{{ wi }}">
                <input type="radio" name="wsaltype[]" value="fixed"
                  {% if ws.salary_type == 'fixed' %}checked{% endif %}
                  onchange="toggleSalType({{ wi }}, 'fixed')" style="display:none;">
                💼 {{ tr.get("payroll_fixed_label","Fiksna bruto") }}
              </label>
            </div>
          </div>

          <!-- Satnica (vidljivo samo za hourly) -->
          <div class="pw-field pw-hourly-row" id="row_hourly_{{ wi }}"
               style="{{ 'display:none;' if ws.salary_type == 'fixed' else '' }}">
            <div>
              <label>{{ tr.get("payroll_hourly_rate_input","Satnica (EUR/h)") }}</label>
              <input type="number" name="wrate[]" value="{{ ws.hourly_rate }}" step="0.01" min="0" placeholder="{{ '%.2f'|format(lux_ssm_h) }}">
            </div>
            <div style="display:flex; align-items:flex-end; padding-bottom:1px;">
              <div style="font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; padding:8px 10px; border-radius:8px; background:{{ '#1d1d1f' if dark else '#f8fafc' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; line-height:1.5;">
                SSM 2025<br><b>{{ '%.2f'|format(lux_ssm_h) }} €/h</b>
              </div>
            </div>
          </div>

          <!-- Fiksna bruto plata (vidljivo samo za fixed) -->
          <div class="pw-field pw-fixed-row" id="row_fixed_{{ wi }}"
               style="{{ '' if ws.salary_type == 'fixed' else 'display:none;' }}">
            <div>
              <label>{{ tr.get("payroll_fixed_gross_input","Fiksna bruto plata (EUR/mj)") }}</label>
              <input type="number" name="wfixed[]" value="{{ ws.fixed_gross }}" step="0.01" min="0" placeholder="{{ tr.get('payroll_eg_placeholder','npr. 2800.00') }}">
            </div>
            <div style="display:flex; align-items:flex-end; padding-bottom:1px;">
              <div style="font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; padding:8px 10px; border-radius:8px; background:{{ '#1d1d1f' if dark else '#f8fafc' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; line-height:1.5;">
                {{ tr.get("payroll_independent_hours","Neovisno od sati") }}
              </div>
            </div>
          </div>

          <div class="pw-field">
            <div>
              <label>{{ tr.get("payroll_tax_class_label","Klasa d'impot") }}</label>
              <select name="wclass[]">
                <option value="1" {% if ws.tax_class == '1' %}selected{% endif %}>{{ tr.get("payroll_single_option","1 – Samac") }}</option>
                <option value="1a" {% if ws.tax_class == '1a' %}selected{% endif %}>{{ tr.get("payroll_single_parent_option","1a – Monoparental") }}</option>
                <option value="2" {% if ws.tax_class == '2' %}selected{% endif %}>{{ tr.get("payroll_married_option","2 – Bracni par") }}</option>
              </select>
            </div>
            <div>
              <label>{{ tr.get("payroll_children_label","Broj djece") }}</label>
              <input type="number" name="wchildren[]" value="{{ ws.num_children }}" min="0" max="20">
            </div>
          </div>
          <div>
            <label style="font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; display:block; margin-bottom:4px;">{{ tr["note"] }}</label>
            <input type="text" name="wnotes[]" value="{{ ws.notes }}" placeholder="frontalier, CDD, étudiant…"
              style="width:100%; padding:7px 10px; border-radius:8px; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }}; background:{{ '#111113' if dark else '#f8fafc' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }}; font-size:13px; box-sizing:border-box;">
          </div>
        </div>
        {% endfor %}
      </div>
      <button type="submit" class="btn" style="margin-top:4px;">💾 {{ tr["save_settings"] }}</button>
    </form>
  </div>

  <!-- Calculate card -->
  <div class="card" style="margin-bottom:20px;">
    <div class="section-title"><h3>📅 {{ tr.get("payroll_period_title","Period obracuna") }}</h3></div>
    <form method="post" action="/payroll">
      <input type="hidden" name="action" value="calculate">
      <div style="display:flex; gap:14px; flex-wrap:wrap; align-items:flex-end;">
        <div>
          <label style="font-size:12px; color:{{ '#94a3b8' if dark else '#64748b' }}; display:block; margin-bottom:4px;">{{ tr["date_from"] }}</label>
          <input type="date" name="date_from" value="{{ date_from }}" required style="padding:9px 12px; border-radius:8px; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }}; background:{{ '#111113' if dark else '#f8fafc' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }};">
        </div>
        <div>
          <label style="font-size:12px; color:{{ '#94a3b8' if dark else '#64748b' }}; display:block; margin-bottom:4px;">{{ tr["date_to"] }}</label>
          <input type="date" name="date_to" value="{{ date_to }}" required style="padding:9px 12px; border-radius:8px; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }}; background:{{ '#111113' if dark else '#f8fafc' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }};">
        </div>
        <button type="submit" class="btn" style="background:#16a34a; color:white;">🧮 {{ tr.get("payroll_calculate_btn","Izracunaj plate") }}</button>
      </div>
    </form>
  </div>

  <!-- Results -->
  {% if results %}
  <div class="card">
    <div class="section-title">
      <h3>📊 {{ tr.get("payroll_results_title","Rezultati:") }} {{ date_from }} → {{ date_to }}</h3>
    </div>
    <div class="legend-box">
      <div class="legend-item"><div class="legend-dot" style="background:#3b82f6;"></div> {{ tr.get("payroll_gross_legend","Brut (EUR)") }}</div>
      <div class="legend-item"><div class="legend-dot" style="background:#ef4444;"></div> {{ tr.get("payroll_deductions_legend","Odbitci CCSS + impot") }}</div>
      <div class="legend-item"><div class="legend-dot" style="background:#16a34a;"></div> Net (€)</div>
      <div class="legend-item"><div class="legend-dot" style="background:#f59e0b;"></div> {{ tr.get("payroll_employer_legend","Cijena za poslodavca") }}</div>
    </div>
    <div style="overflow-x:auto;">
    <table class="result-table">
      <thead>
        <tr>
          <th>{{ tr.get("payroll_worker_col","Radnik") }}</th>
          <th>{{ tr.get("payroll_hours_col","Sati") }}</th>
          <th>{{ tr.get("payroll_gross_legend","Brut (EUR)") }}</th>
          <th>{{ tr.get("payroll_ccss_col","CCSS odbitci") }}</th>
          <th>{{ tr.get("payroll_tax_col","Porez") }}</th>
          <th>Net (€)</th>
          <th>{{ tr.get("payroll_employer_col","Cijena poslodc.") }}</th>
        </tr>
      </thead>
      <tbody>
      {% for r in results %}
      <tr>
        <td>
          <div style="font-weight:700;">{{ r.worker }}</div>
          <div style="font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }};">
            {% if r.sal_type == 'fixed' %}
              <span style="background:{{ '#1e3a5f' if dark else '#dbeafe' }}; color:{{ '#93c5fd' if dark else '#1d4ed8' }}; padding:1px 6px; border-radius:4px; font-weight:600;">💼 {{ tr.get("payroll_fix_gross_badge","Fix bruto") }}</span>
            {% else %}
              {{ r.rate }} €/h
            {% endif %}
            · klasa {{ r.tax_class }}
          </div>
        </td>
        <td style="font-weight:700;">
          {{ '%.2f'|format(r.hours) }} h
          {% if r.sal_type == 'fixed' %}
            <div style="font-size:10px; color:{{ '#94a3b8' if dark else '#64748b' }};">
              / {{ '%.2f'|format(lux_std_h) }} h std
            </div>
          {% endif %}
        </td>
        <td style="color:{{ '#93c5fd' if dark else '#2563eb' }}; font-weight:700;">
          {{ '%.2f'|format(r.gross) }} €
          {% if r.sal_type == 'fixed' and r.fixed_base %}
            <div style="font-size:10px; color:{{ '#94a3b8' if dark else '#64748b' }}; font-weight:400;">
              {{ '%.2f'|format(r.fixed_base) }}€ × {{ '%.2f'|format(r.hours) }}/{{ '%.2f'|format(lux_std_h) }}h
            </div>
          {% endif %}
        </td>
        <td>
          <div class="deduction-row"><span>C. Maladie Soins (2.80%)</span><span>−{{ '%.2f'|format(r.maladie_soins) }} €</span></div>
          <div class="deduction-row"><span>C. Maladie Espèces (0.25%)</span><span>−{{ '%.2f'|format(r.maladie_especes) }} €</span></div>
          <div class="deduction-row"><span>C. Pension (8.00%)</span><span>−{{ '%.2f'|format(r.pension) }} €</span></div>
          <div class="deduction-row"><span>C. Dépendance (1.40%)</span><span>−{{ '%.2f'|format(r.dependency) }} €</span></div>
          <div class="deduction-row bold"><span>{{ tr.get("payroll_total_ccss","Ukupno CCSS") }}</span><span>−{{ '%.2f'|format(r.total_ccss) }} €</span></div>
        </td>
        <td>
          <div style="font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }};">
            {{ tr.get("payroll_tax_base_abbr","Baza:") }} {{ '%.2f'|format(r.taxable_m) }} €/{{ tr.get("payroll_per_month_abbr","mj") }}<br>
            <span style="font-weight:700; color:{{ '#fca5a5' if dark else '#dc2626' }};">
              −{{ '%.2f'|format(r.income_tax) }} €
            </span>
          </div>
        </td>
        <td>
          <div class="net-amount">{{ '%.2f'|format(r.net) }} €</div>
        </td>
        <td>
          <div class="employer-amount">
            {{ '%.2f'|format(r.employer_total) }} €<br>
            <span style="font-size:10px; opacity:0.8;">
              +maladie {{ '%.2f'|format(r.emp_health) }} €<br>
              +pension {{ '%.2f'|format(r.emp_pension) }} €<br>
              +accident {{ '%.2f'|format(r.emp_accident) }} €
            </span>
          </div>
        </td>
      </tr>
      {% endfor %}
      </tbody>
      <tfoot>
        <tr>
          <td><b>{{ tr.get("payroll_total_row","UKUPNO") }} ({{ results|length }} {{ tr.get("payroll_worker_singular","radnik") if results|length == 1 else tr.get("payroll_worker_plural","radnika") }})</b></td>
          <td><b>{{ '%.2f'|format(tot('hours')) }} h</b></td>
          <td><b>{{ '%.2f'|format(tot('gross')) }} €</b></td>
          <td><b>−{{ '%.2f'|format(tot('total_ccss')) }} €</b></td>
          <td><b>−{{ '%.2f'|format(tot('income_tax')) }} €</b></td>
          <td><b style="color:{{ '#4ade80' if dark else '#16a34a' }};">{{ '%.2f'|format(tot('net')) }} €</b></td>
          <td><b style="color:{{ '#fbbf24' if dark else '#d97706' }};">{{ '%.2f'|format(tot('employer_total')) }} €</b></td>
        </tr>
      </tfoot>
    </table>
    </div>

    <!-- Info box -->
    <div style="margin-top:16px; padding:12px 16px; border-radius:10px; background:{{ '#141416' if dark else '#eff6ff' }}; border:1px solid {{ '#1e3a5f' if dark else '#bfdbfe' }}; font-size:12px; color:{{ '#93c5fd' if dark else '#1e40af' }}; line-height:1.7;">
      <b>ℹ️ {{ tr.get("payroll_calculation_note","Napomena o obracunu:") }}</b><br>
      CCSS 2025 (salarié): C. Maladie Soins 2.80% · C. Maladie Espèces 0.25% · C. Pension 8.00% · C. Dépendance 1.40% ({{ tr.get("payroll_note_franchise_abbr","franšiza") }} {{ '%.2f'|format(dep_franchise) }} €/{{ tr.get("payroll_per_month_abbr","mj") }}).<br>
      {{ tr.get("payroll_note_tax_line","Porez: progresivni razredi ACD + impôt de solidarité (7% kl.1/1a · 9% kl.2). Odbitna stavka: maladie + pension + forfait frais d'obtention 45 €/mj.") }}<br>
      <b>{{ tr.get("payroll_note_disclaimer","Ovaj obračun je informativan — provjerite sa fiduciaire ili CCSS za tačne iznose.") }}</b>
    </div>
  </div>
  {% elif request.method == 'POST' and request.form.get('action') == 'calculate' %}
  <div class="card" style="text-align:center; padding:32px; color:{{ '#94a3b8' if dark else '#64748b' }};">
    ⚠️ {{ tr.get("payroll_no_results","Nema evidentiranih smjena za odabrani period ili nijedan radnik nema unesenu satnicu.") }}
  </div>
  {% endif %}

</div>
<script>
function toggleSalType(idx, type) {
  var hRow = document.getElementById('row_hourly_' + idx);
  var fRow = document.getElementById('row_fixed_' + idx);
  var lH   = document.getElementById('lbl_hourly_' + idx);
  var lF   = document.getElementById('lbl_fixed_' + idx);
  var activeStyle = 'background:#1f4f82; color:white;';
  var inactiveStyleD = 'background:#111113; color:#94a3b8;';
  var inactiveStyleL = 'background:#f1f5f9; color:#64748b;';
  var isDark = {{ 'true' if dark else 'false' }};
  if (type === 'hourly') {
    if (hRow) hRow.style.display = '';
    if (fRow) fRow.style.display = 'none';
    if (lH) lH.style.cssText = activeStyle + 'flex:1;text-align:center;padding:7px 4px;cursor:pointer;font-size:12px;font-weight:600;';
    if (lF) lF.style.cssText = (isDark ? inactiveStyleD : inactiveStyleL) + 'flex:1;text-align:center;padding:7px 4px;cursor:pointer;font-size:12px;font-weight:600;';
  } else {
    if (hRow) hRow.style.display = 'none';
    if (fRow) fRow.style.display = '';
    if (lF) lF.style.cssText = activeStyle + 'flex:1;text-align:center;padding:7px 4px;cursor:pointer;font-size:12px;font-weight:600;';
    if (lH) lH.style.cssText = (isDark ? inactiveStyleD : inactiveStyleL) + 'flex:1;text-align:center;padding:7px 4px;cursor:pointer;font-size:12px;font-weight:600;';
  }
}
</script>
""", tr=tr, dark=dark, workers_list=workers_list, settings=settings, results=results,
     date_from=date_from, date_to=date_to, tot=tot,
     lux_ssm_h=LUX_SSM_HORAIRE, lux_std_h=LUX_STD_MONTHLY_HOURS,
     dep_franchise=LUX_CCSS_DEP_FRANCHISE,
     split_workers=split_workers, parse_shift_hours=parse_shift_hours)


@app.route("/delete_user/<int:user_id>")
def delete_user(user_id):
    if session.get("role") != "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor(); user = c.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if user and user[0] != "admin": c.execute("DELETE FROM users WHERE id = ?", (user_id,)); c.execute("DELETE FROM workers WHERE name = ?", (user[0],)); c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (user[0],))
    conn.commit(); conn.close(); return redirect("/admin")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
