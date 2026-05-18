from flask import Flask, request, redirect, render_template_string, session, send_file, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
import sqlite3
import re
import io
import os
import calendar
import json
import math
import zipfile
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
    "sick": "Maladie", "vacation": "Conge", "sick_vacation": "Maladie / Conge",
})
TRANSLATIONS["en"].update({
    "login_title": "Login", "login_btn": "Login", "logout": "Logout",
    "title": "WORK SCHEDULE", "add_worker": "Add worker", "add_client": "Add client",
    "add_shift": "Add shift", "workers": "Workers", "clients": "Clients",
    "week_calendar": "Weekly calendar", "month_calendar": "Monthly calendar",
    "monthly_hours": "Monthly hours", "weekly_hours": "Weekly hours",
    "back": "Back", "save": "Save", "delete": "Delete", "edit": "Edit",
    "sick": "Sick leave", "vacation": "Vacation", "sick_vacation": "Sick leave / Vacation",
})
TRANSLATIONS["de"].update({
    "login_title": "Anmeldung", "login_btn": "Anmelden", "logout": "Abmelden",
    "title": "ARBEITSPLAN", "add_worker": "Mitarbeiter hinzufugen", "add_client": "Kunde hinzufugen",
    "add_shift": "Einsatz hinzufugen", "workers": "Mitarbeiter", "clients": "Kunden",
    "week_calendar": "Wochenkalender", "month_calendar": "Monatskalender",
    "monthly_hours": "Monatsstunden", "weekly_hours": "Wochenstunden",
    "back": "Zuruck", "save": "Speichern", "delete": "Loschen", "edit": "Bearbeiten",
    "sick": "Krankheit", "vacation": "Urlaub", "sick_vacation": "Krankheit / Urlaub",
})
TRANSLATIONS["pt"].update({
    "login_title": "Entrar", "login_btn": "Entrar", "logout": "Sair",
    "title": "PLANO DE TRABALHO", "add_worker": "Adicionar trabalhador", "add_client": "Adicionar cliente",
    "add_shift": "Adicionar turno", "workers": "Trabalhadores", "clients": "Clientes",
    "week_calendar": "Calendario semanal", "month_calendar": "Calendario mensal",
    "monthly_hours": "Horas mensais", "weekly_hours": "Horas semanais",
    "back": "Voltar", "save": "Guardar", "delete": "Apagar", "edit": "Editar",
    "sick": "Baixa medica", "vacation": "Ferias", "sick_vacation": "Baixa / Ferias",
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
    "contract_reminders": "Podsjetnik za ugovore", "contract_expired": "Ugovor je istekao",
    "contract_expires_soon": "Ugovor uskoro istice", "worked_hours": "Odradjeni sati",
})
TRANSLATIONS["en"].update({
    "contract_type": "Contract type", "contract_end_date": "Contract end date",
    "contract_reminders": "Contract reminders", "contract_expired": "Contract expired",
    "contract_expires_soon": "Contract expires soon", "worked_hours": "Worked hours",
})
TRANSLATIONS["fr"].update({
    "contract_type": "Type de contrat", "contract_end_date": "Fin du contrat",
    "contract_reminders": "Rappels contrats", "contract_expired": "Contrat expire",
    "contract_expires_soon": "Contrat bientot expire", "worked_hours": "Heures travaillees",
})
TRANSLATIONS["de"].update({
    "contract_type": "Vertragsart", "contract_end_date": "Vertragsende",
    "contract_reminders": "Vertragserinnerungen", "contract_expired": "Vertrag abgelaufen",
    "contract_expires_soon": "Vertrag laeuft bald ab", "worked_hours": "Geleistete Stunden",
})
TRANSLATIONS["pt"].update({
    "contract_type": "Tipo de contrato", "contract_end_date": "Fim do contrato",
    "contract_reminders": "Lembretes de contrato", "contract_expired": "Contrato expirado",
    "contract_expires_soon": "Contrato termina em breve", "worked_hours": "Horas trabalhadas",
})

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


def safe_pdf_name(*parts):
    raw = "_".join(str(part or "").strip() for part in parts if str(part or "").strip())
    normalized = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized).strip("._")
    return cleaned or "document"


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
    """Automatski status po datumu i vremenu smjene."""
    try:
        start_str, end_str = [x.strip() for x in time_range.split("-")]
        start_dt = datetime.strptime(f"{shift_date} {start_str}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"{shift_date} {end_str}", "%Y-%m-%d %H:%M")
        now = datetime.now()

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
    shifts = c.execute("SELECT client, time, worker, date FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time", (date_from, date_to)).fetchall()
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
        row[0]: {"paid": int(row[1] or 0), "paid_date": row[2] or "", "deleted": int(row[3] or 0)}
        for row in c.execute("SELECT invoice_number, paid, paid_date, COALESCE(deleted, 0) FROM invoice_records").fetchall()
    }
    for row in rows:
        invoice_number = str(row["invoice_number"])
        previous = existing.get(invoice_number, {"paid": 0, "paid_date": ""})
        if previous.get("deleted"):
            continue
        c.execute("""
            INSERT INTO invoice_records (invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date, deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(invoice_number) DO UPDATE SET client_name = excluded.client_name, date_from = excluded.date_from,
            date_to = excluded.date_to, invoice_date = excluded.invoice_date, amount = excluded.amount,
            vat_amount = excluded.vat_amount, total = excluded.total, paid = excluded.paid, paid_date = excluded.paid_date
        """, (
            invoice_number, row["client"], date_from, date_to, invoice_date,
            row["amount"], row["vat_amount"], row["total"], previous["paid"], previous["paid_date"],
        ))
        row["paid"] = bool(previous["paid"])
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
        SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date
        FROM invoice_records
        {where}
        ORDER BY invoice_date DESC, CAST(invoice_number AS INTEGER) DESC
    """
    return [invoice_record_to_dict(row) for row in c.execute(query, params).fetchall()]


def invoice_number_from_index(settings, index):
    return str(int(settings.get("invoice_start_number") or 1) + index)


def build_invoice_pdf(row, settings, invoice_date, date_from, date_to, document_title="FACTURE"):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
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
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
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
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
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
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
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


def build_invoice_list_pdf(records, date_from, date_to):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=1.2*cm, leftMargin=1.2*cm, topMargin=1.2*cm, bottomMargin=1.2*cm)
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
            deleted INTEGER DEFAULT 0
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

    .app-shell { display:grid; grid-template-columns:240px 1fr; gap:18px; align-items:start; }
    .sidebar { position:sticky; top:18px; padding:16px; }
    .sidebar-title { font-weight:800; font-size:18px; margin-bottom:14px; color:{{ '#93c5fd' if dark else '#1f4f82' }}; }
    .nav-link { display:block; padding:11px 12px; border-radius:10px; margin:6px 0; background:{{ '#1f2937' if dark else '#f8fafc' }}; color:{{ '#e5e7eb' if dark else '#1f4f82' }} !important; }
    .nav-link:hover { transform:translateX(2px); box-shadow:0 3px 10px rgba(0,0,0,0.08); }
    .main-content { min-width:0; }
    .hero { padding:22px; border-radius:16px; background:{{ 'linear-gradient(135deg,#111827,#1f2937)' if dark else 'linear-gradient(135deg,#ffffff,#eaf2fb)' }}; margin-bottom:18px; }
    .hero h1 { margin:0 0 6px 0; }
    .stats-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; margin:14px 0 18px 0; }
    .stat-card { padding:16px; border-radius:14px; background:{{ '#111827' if dark else 'white' }}; box-shadow:0 4px 14px rgba(0,0,0,0.06); border-left:5px solid #1f4f82; }
    .stat-number { font-size:26px; font-weight:800; margin-top:6px; }
    .section-title { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:22px 0 12px; }
    .big-map-button { display:inline-block; padding:16px 26px; border-radius:14px; background:#16a34a; color:white !important; font-size:18px; font-weight:800; text-decoration:none; box-shadow:0 6px 18px rgba(0,0,0,0.18); }
    @media (max-width: 900px) { .app-shell { grid-template-columns:1fr; } .sidebar { position:static; } body { margin:12px; } }
</style>
"""


def header_html():
    return """
    <div class="brandbar">
        <div class="brandleft">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="Luxmann Logo">
            <div>
                <div class="brandtitle">Luxmann Planner</div>
                {% if session.get('user') %}<div class="muted">{{ tr["logged_as"] }}: <b>{{ session['user'] }}</b> ({{ session['role'] }})</div>{% endif %}
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
    return redirect("/")


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
        "today_shift_count": len([s for s in all_shifts_for_hours if s[3] == today.strftime("%Y-%m-%d")]),
        "worker_count": len([w for w in workers if w[0] != "admin"]),
        "client_count": len(clients),
        "month_total_hours": sum(calculate_hours_for_user(month_shifts, None if is_admin else current_user).values()),
        "contract_reminders": contract_reminders(workers) if is_admin else [],
    }


@app.route("/")
def index():
    if "user" not in session:
        return redirect("/login")
    tr = t()
    dark = get_theme() == "dark"
    data = load_index_data()

    return render_template_string(BASE_STYLE + header_html() + """
    <div class="app-shell">
        <aside class="card sidebar">
            <div class="sidebar-title">{{ tr["professional_menu"] }}</div>
            <a class="nav-link" href="/">🏠 {{ tr["dashboard"] }}</a>
            <a class="nav-link" href="/week">📅 {{ tr["week_calendar"] }}</a>
            <a class="nav-link" href="/month">🗓️ {{ tr["month_calendar"] }}</a>
            {% if is_admin %}<a class="nav-link" href="/route_optimizer">🧭 {{ tr["route_optimizer"] }}</a>{% endif %}
            {% if is_admin %}<a class="nav-link" href="/invoices">📄 {{ tr["invoices"] }}</a>{% endif %}
            <a class="nav-link" href="/month_pdf" target="_blank">📄 {{ tr["month_pdf"] }}</a>
        </aside>
        <main class="main-content">
            <div class="hero">
                <h1>{{ tr["dashboard"] }}</h1>
                <div class="muted">Luxmann Planner · {{ tr["overview"] }}</div>
            </div>
            <div class="stats-grid">
                <div class="stat-card"><div class="muted">{{ tr["today_shifts"] }}</div><div class="stat-number">{{ today_shift_count }}</div></div>
                <div class="stat-card"><div class="muted">{{ tr["active_workers"] }}</div><div class="stat-number">{{ worker_count }}</div></div>
                <div class="stat-card"><div class="muted">{{ tr["registered_clients"] }}</div><div class="stat-number">{{ client_count }}</div></div>
                <div class="stat-card"><div class="muted">{{ tr["this_month_hours"] }}</div><div class="stat-number">{{ "%.1f"|format(month_total_hours) }}</div></div>
            </div>
            {% if is_admin and contract_reminders %}
            <div class="card" style="border-left:6px solid #f59e0b; margin-bottom:16px;">
                <h3>{{ tr["contract_reminders"] }}</h3>
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
        <div class="card" style="grid-column:1/-1;">
            <button onclick="toggleMenu()" type="button">☰ {{ tr["menu"] }}</button>
            <div id="menuBox" style="display:none; margin-top:15px;">
                <div class="grid">
                    <div class="card"><h3>{{ tr["change_password"] }}</h3><form method="post" action="/change_password"><input name="new_password" type="password" placeholder="{{ tr['new_password'] }}" required><button>{{ tr["save"] }}</button></form></div>
                    <div class="card"><h3>{{ tr["user_mgmt"] }}</h3><form method="post" action="/add_user"><input name="username" placeholder="{{ tr['username'] }}" required><input name="password" placeholder="{{ tr['password'] }}" required><select name="role"><option value="admin">{{ tr['role_admin'] }}</option><option value="worker">{{ tr['role_worker'] }}</option></select><button>{{ tr["add_user"] }}</button></form></div>
                    <div class="card"><h3>{{ tr["existing_users"] }}</h3>{% for u in db_users %}<div class="user-row"><b>{{ u[1] }}</b> ({{ u[2] }}){% if u[1] != 'admin' %}<a class="delete-link" href="/delete_user/{{ u[0] }}">{{ tr["delete"] }}</a>{% endif %}</div>{% endfor %}</div>
                    <div class="card"><h3>{{ tr["worker_colors"] }}</h3>{% for w in workers %}<form method="post" action="/update_worker_color"><input type="hidden" name="worker_name" value="{{ w[0] }}"><div style="display:flex; gap:10px; align-items:center;"><div style="min-width:110px;">{{ w[0] }}</div><input type="color" name="color" value="{{ worker_colors.get(w[0], '#1f4f82') }}"><button>{{ tr["update_color"] }}</button></div></form>{% endfor %}</div>
                    <div class="card"><h3>{{ tr["workers"] }}</h3>{% for w in workers %}<div class="user-row"><b>{{ w[0] }}</b><br><small>{{ w[1] }}</small>{% if w[2] or w[3] %}<br><small>{{ tr["contract_type"] }}: {{ w[2] or "-" }}{% if w[3] %} | {{ tr["contract_end_date"] }}: {{ format_date(w[3]) }}{% endif %}</small>{% endif %}<br><a class="edit-link" href="/edit_worker/{{ w[0] }}">{{ tr["edit"] }}</a>{% if w[0] != 'admin' %}<a class="delete-link"
   href="/delete_worker/{{ w[0] }}"
   onclick="return confirm('Da li ste sigurni?');">
   {{ tr["delete"] }}
</a>{% endif %}</div>{% endfor %}</div>
                    <div class="card"><h3>{{ tr["clients"] }}</h3>{% for c in clients %}<div class="user-row"><b>{{ c[0] }}</b><br><small>{{ c[1] }}</small><br><a class="edit-link" href="/edit_client/{{ c[0] }}">{{ tr["edit"] }}</a><a class="delete-link" href="/delete_client/{{ c[0] }}">{{ tr["delete"] }}</a></div>{% endfor %}</div>
                </div>
            </div>
        </div>

        <div class="card"><h3>{{ tr["add_worker"] }}</h3><form method="post" action="/add_worker" autocomplete="off"><input name="worker_name" placeholder="{{ tr['worker_name'] }}" required autocomplete="off"><input name="address" placeholder="{{ tr['address'] }}" autocomplete="off"><input name="contract_type" placeholder="{{ tr['contract_type'] }}" autocomplete="off"><label>{{ tr["contract_end_date"] }}</label><input name="contract_end_date" type="date"><button>{{ tr["add_worker"] }}</button></form></div>
        <div class="card"><h3>{{ tr["add_client"] }}</h3><form method="post" action="/add_client" autocomplete="off"><input name="client_name" placeholder="{{ tr['client_name'] }}" required autocomplete="off"><input name="address" placeholder="{{ tr['address'] }}" required autocomplete="off"><button>{{ tr["add_client"] }}</button></form></div>

        <div class="card">
            <h3>{{ tr["add_shift"] }}</h3>
            <form method="post" action="/add_shift">
                <label>{{ tr["choose_worker"] }}</label>
                {% for w in workers %}{% if w[0] != 'admin' %}<label class="check-row"><input type="checkbox" name="workers" value="{{ w[0] }}">{{ w[0] }}</label>{% endif %}{% endfor %}
                <input id="clientSearchAddShift" placeholder="{{ tr['search_placeholder'] }}" autocomplete="off" oninput="filterClientOptions('clientSearchAddShift', 'clientSelectAddShift')">
                <select id="clientSelectAddShift" name="client" required><option value="">{{ tr["choose_client"] }}</option>{% for c in clients %}<option value="{{ c[0] }}">{{ c[0] }}</option>{% endfor %}</select>
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
            {% for s in week_shifts %}{% set auto_status = get_auto_status(s[3], s[4]) %}<div class="shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="border-left:6px solid {{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#1f4f82') }}"><b>{{ format_date(s[3]) }}</b> | {{ s[4] }}<span class="status-badge" style="background:{{ status_colors.get(auto_status, '#6b7280') }};">{{ get_status_label(auto_status, tr) }}</span><br><br><b>{{ tr["team"] }}:</b> {{ s[1] }}<br><b>{{ tr["pdf_client"] }}:</b> {{ s[2] }}{% if is_admin %}<a class="action-link edit-link" href="/edit_shift/{{ s[0] }}">{{ tr["edit"] }}</a><a class="action-link delete-link"
   href="/delete_shift/{{ s[0] }}"
   onclick="return confirm('Da li ste sigurni?');">
   {{ tr["delete"] }}
</a><a class="action-link copy-link" href="/copy_shift/{{ s[0] }}">{{ tr["copy"] }}</a>{% endif %}</div>{% endfor %}</div>
        {% endfor %}
        </div>
        <a class="week-link" href="/week">{{ tr["week_calendar"] }}</a><a class="week-link" href="/month">{{ tr["month_calendar"] }}</a><a class="week-link" href="/route_optimizer">{{ tr["route_optimizer"] }}</a><a class="pdf-link" href="/export_pdf{% if request.args.get('date') %}?date={{ request.args.get('date') }}{% endif %}" target="_blank">{{ tr["pdf"] }}</a>
    </div>
        </main>
    </div>

  <script>
function toggleMenu(){
    var m=document.getElementById('menuBox');
    if(m){m.style.display=(m.style.display==='none')?'block':'none';}
}

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
            if(!ok){
                e.preventDefault();
                return false;
            }
        });
    });
});
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
                {% for s in shifts %}{% if s[3] == day %}<div class="mini-shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="border-left:5px solid {{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#1f4f82') }};"><b>{{ s[1] }}</b><br>{{ s[2] }}<br>{{ s[4] }}{% if is_admin %}<br><a class="mini-link edit-link" href="/edit_shift/{{ s[0] }}">{{ tr["edit"] }}</a><a class="mini-link delete-link" href="/delete_shift/{{ s[0] }}">{{ tr["delete"] }}</a><a class="mini-link copy-link" href="/copy_shift/{{ s[0] }}">{{ tr["copy"] }}</a>{% endif %}</div>{% endif %}{% endfor %}
            </div>
        {% endfor %}
    </div>
    {% if is_admin %}<div id="holidayModal" class="modal-backdrop"><div class="modal-card"><h3>{{ tr["add_holiday"] }}</h3><form method="post" action="/add_holiday"><input type="date" name="date" id="holidayDate" required><input type="text" name="name" placeholder="{{ tr['holiday_name'] }}" required><button>{{ tr["save"] }}</button></form><button type="button" onclick="closeHolidayModal()">{{ tr["cancel"] }}</button></div></div>{% endif %}
    <script>
    function openHolidayModal(dateStr){var m=document.getElementById('holidayModal');var d=document.getElementById('holidayDate');if(m&&d){d.value=dateStr;m.style.display='block';}}
    function closeHolidayModal(){var m=document.getElementById('holidayModal');if(m){m.style.display='none';}}
    function dragShift(ev, shiftId){ev.dataTransfer.setData('shift_id', shiftId);} function allowDrop(ev){ev.preventDefault();ev.currentTarget.classList.add('drop-target');} function clearDrop(ev){ev.currentTarget.classList.remove('drop-target');}
    function dropShift(ev, dateStr){ev.preventDefault();ev.currentTarget.classList.remove('drop-target');var shiftId=ev.dataTransfer.getData('shift_id');if(!shiftId)return;fetch('/move_shift',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'shift_id='+encodeURIComponent(shiftId)+'&date='+encodeURIComponent(dateStr)}).then(function(){window.location.reload();});}
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
    {% if is_admin and copied_shift_id %}<div style="background:#16a34a;color:white;padding:8px 12px;border-radius:8px;display:inline-block;margin:10px 0;font-weight:bold;">{{ tr["copy_active"] }} <a style="color:white;" href="/clear_copy">{{ tr["clear"] }}</a></div>{% endif %}
    <div style="display:flex; justify-content:space-between; align-items:center; margin:16px 0; gap:12px;"><a href="/month?year={{ prev_year }}&month={{ prev_month }}">{{ tr["prev_month"] }}</a><h2>{{ tr["month_calendar"] }} - {{ format_month_year(year, month) }}</h2><a href="/month?year={{ next_year }}&month={{ next_month }}">{{ tr["next_month"] }}</a></div>
    <div style="display:grid; grid-template-columns:repeat(7,1fr); gap:10px;">
        {% for dn in day_names %}<div class="card" style="min-height:auto; text-align:center; font-weight:bold;">{{ dn }}</div>{% endfor %}
        {% for week in month_days %}{% for day in week %}{% set daystr = day.strftime('%Y-%m-%d') %}{% set holiday_name = holidays_map.get(daystr) %}
            <div class="card {% if holiday_name %}holiday-soft{% endif %} {% if day.weekday() >= 5 %}weekend-soft{% endif %}" style="min-height:120px;" ondragover="allowDrop(event)" ondragleave="clearDrop(event)" ondrop="dropShift(event, '{{ daystr }}')">
                <div style="font-weight:bold; margin-bottom:8px;"><a href="{% if is_admin %}javascript:void(0){% else %}/?selected_date={{ daystr }}{% endif %}" {% if is_admin %}onclick="openHolidayModal('{{ daystr }}')"{% endif %} style="{% if day.weekday() >= 5 %}color:#ef4444;{% endif %}">{{ day.strftime('%d/%m/%Y') }}</a>{% if is_admin and copied_shift_id %}<br><a style="display:inline-block;margin-top:6px;padding:4px 7px;border-radius:6px;background:#16a34a;color:white!important;font-size:11px;" href="/paste_shift/{{ daystr }}">{{ tr["paste"] }}</a>{% endif %}</div>
                {% if holiday_name %}<small class="holiday-note">{{ holiday_name }}</small>{% endif %}
                {% for s in shifts_by_date.get(daystr, []) %}<div class="mini-shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="border-left:5px solid {{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#1f4f82') }};"><b>{{ s[1] }}</b><br>{{ s[2] }}<br>{{ s[4] }}{% if is_admin %}<br><a class="mini-link edit-link" href="/edit_shift/{{ s[0] }}">{{ tr["edit"] }}</a><a class="mini-link delete-link" href="/delete_shift/{{ s[0] }}">{{ tr["delete"] }}</a><a class="mini-link copy-link" href="/copy_shift/{{ s[0] }}">{{ tr["copy"] }}</a>{% endif %}</div>{% endfor %}
            </div>
        {% endfor %}{% endfor %}
    </div>
    {% if is_admin %}<div id="holidayModal" class="modal-backdrop"><div class="modal-card"><h3>{{ tr["add_holiday"] }}</h3><form method="post" action="/add_holiday"><input type="date" name="date" id="holidayDate" required><input type="text" name="name" placeholder="{{ tr['holiday_name'] }}" required><button>{{ tr["save"] }}</button></form><button type="button" onclick="closeHolidayModal()">{{ tr["cancel"] }}</button></div></div>{% endif %}
    <script>
    function openHolidayModal(dateStr){var m=document.getElementById('holidayModal');var d=document.getElementById('holidayDate');if(m&&d){d.value=dateStr;m.style.display='block';}} function closeHolidayModal(){var m=document.getElementById('holidayModal');if(m){m.style.display='none';}}
    function dragShift(ev, shiftId){ev.dataTransfer.setData('shift_id', shiftId);} function allowDrop(ev){ev.preventDefault();ev.currentTarget.classList.add('drop-target');} function clearDrop(ev){ev.currentTarget.classList.remove('drop-target');}
    function dropShift(ev, dateStr){ev.preventDefault();ev.currentTarget.classList.remove('drop-target');var shiftId=ev.dataTransfer.getData('shift_id');if(!shiftId)return;fetch('/move_shift',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'shift_id='+encodeURIComponent(shiftId)+'&date='+encodeURIComponent(dateStr)}).then(function(){window.location.reload();});}
    document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('a.delete-link').forEach(function(link){
        link.addEventListener('click', function(e){
            var ok = confirm('Da li ste sigurni da želite obrisati?');
            if(!ok){
                e.preventDefault();
                return false;
            }
        });
    });
});
    </script>
    """, tr=tr, dark=dark, year=year, month=month, prev_year=prev_year, prev_month=prev_month, next_year=next_year, next_month=next_month, month_days=month_days, day_names=day_names, shifts_by_date=shifts_by_date, worker_colors=worker_colors, holidays_map=holidays_map, is_admin=is_admin, copied_shift_id=copied_shift_id, get_auto_status=get_auto_status, split_workers=split_workers, format_month_year=format_month_year)



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
            shifts = c.execute("SELECT * FROM shifts WHERE date = ? ORDER BY time", (selected_date,)).fetchall()
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
    <a href="/">{{ tr["back"] }}</a>
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
        <ol>
            {% for stop in result.ordered %}
                <li style="margin-bottom:10px;">
                    <b>{{ stop.client }}</b> — {{ stop.time }}<br>
                    <span class="muted">{{ tr["client_address"] }}: {{ stop.address }}</span><br>
                    <a class="big-map-button" style="display:inline-block;margin-top:8px;padding:8px 12px;font-size:13px;" href="{{ stop.maps_url }}" target="_blank">{{ tr["navigate_to_address"] }}</a>
                </li>
            {% endfor %}
        </ol>
        {% if result.bad_addresses %}
            <div style="color:#f59e0b; font-weight:bold; margin:12px 0;">
                {{ tr["geocode_failed"] }}:<br>
                {% for bad in result.bad_addresses %}• {{ bad }}<br>{% endfor %}
            </div>
        {% endif %}
        <p class="muted">{{ tr["route_warning"] }}</p>
        <div style="margin-top:20px; text-align:center;">
            <a class="big-map-button" href="{{ result.maps_url }}" target="_blank">🗺️ {{ tr["open_in_maps"] }}</a>
        </div>
    </div>
    {% endif %}
    """, tr=tr, dark=dark, workers=workers, selected_date=selected_date,
       selected_worker=selected_worker, start_address=start_address, result=result, error=error, is_admin=is_admin)

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
    generated_rows = build_invoice_rows(conn, date_from, date_to, None, settings)
    save_invoice_records(conn, generated_rows, date_from, date_to, invoice_date)
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
            <a href="/" style="color:white;">{{ tr["back"] }}</a>
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

            <form method="get" action="/invoices" class="settings-grid">
                <div><label>{{ tr["date_from"] }}</label><input type="date" name="date_from" value="{{ date_from }}"></div>
                <div><label>{{ tr["date_to"] }}</label><input type="date" name="date_to" value="{{ date_to }}"></div>
                <div><label>{{ tr["invoice_date"] }}</label><input type="date" name="invoice_date" value="{{ invoice_date }}"></div>
                <div style="align-self:end;"><button>{{ tr["generate_invoice"] }}</button></div>
            </form>

            <div class="invoice-tabs">
                <a class="invoice-tab active" href="#">{{ tr["total_invoices"] }} <span class="pill">{{ rows|length }}</span></a>
                <a class="invoice-tab" href="#" onclick="filterInvoiceStatus('unpaid', this);return false;">{{ tr["unpaid"] }} <span class="pill red">{{ unpaid_rows|length }}</span></a>
                <a class="invoice-tab" href="#" onclick="filterInvoiceStatus('paid', this);return false;">{{ tr["paid"] }} <span class="pill green">{{ paid_rows|length }}</span></a>
                <a class="invoice-tab" href="#" onclick="filterInvoiceStatus('all', this);return false;">{{ tr["sent"] }} <span class="pill">{{ rows|length }}</span></a>
                <a class="invoice-tab" href="/invoices/quote">{{ tr["quote"] }}</a>
            </div>

            <table class="invoice-table">
                <tr><th></th><th>{{ tr["client_name"] }}</th><th>Document</th><th>{{ tr["invoice_number"] }}</th><th>{{ tr["invoice_date"] }}</th><th>{{ tr["payment_status"] }}</th><th>{{ tr["amount_with_vat"] }}</th><th>PDF</th><th></th></tr>
                {% for row in rows %}
                <tr class="invoice-row" data-paid="{{ 1 if row.paid else 0 }}" data-search="{{ (row.client ~ ' ' ~ row.invoice_number)|lower }}">
                    <td><input type="checkbox" style="width:auto;"></td>
                    <td><a href="/invoices/client?client={{ row.client|urlencode }}&date_from={{ date_from }}&date_to={{ date_to }}" style="color:white;text-decoration:underline;">{{ row.client }}</a></td>
                    <td>{{ tr["invoices"] }}</td>
                    <td><a href="/invoices/view?invoice_number={{ row.invoice_number }}" style="color:white;text-decoration:underline;">{{ row.invoice_number }}</a></td>
                    <td>{{ format_date(row.invoice_date) }}</td>
                    <td>
                        <span class="{{ 'paid-text' if row.paid else 'unpaid-text' }}">{{ tr["paid"] if row.paid else tr["unpaid"] }}</span><br>
                        <a href="/invoices/mark_paid?invoice_number={{ row.invoice_number }}&paid={{ 0 if row.paid else 1 }}&client={{ row.client|urlencode }}&date_from={{ row.date_from }}&date_to={{ row.date_to }}&invoice_date={{ row.invoice_date }}&amount={{ row.amount }}&vat_amount={{ row.vat_amount }}&total={{ row.total }}" style="color:#e5e7eb;">{{ tr["mark_unpaid"] if row.paid else tr["mark_paid"] }}</a>
                    </td>
                    <td><b>{{ "%.2f"|format(row.total) }} EUR</b></td>
                    <td><a href="/invoices/download?client={{ row.client|urlencode }}&date_from={{ row.date_from }}&date_to={{ row.date_to }}&invoice_date={{ row.invoice_date }}" style="color:#93c5fd;">PDF</a></td>
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
    });
    </script>
    """, tr=tr, dark=dark, settings=settings, profiles=profiles, profiles_json=profiles_json, rows=rows, paid_rows=paid_rows, unpaid_rows=unpaid_rows, total_paid=total_paid, total_unpaid=total_unpaid, total_all=total_all, format_date=format_date, date_from=date_from, date_to=date_to, invoice_date=invoice_date)


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
        <br><a href="/invoices">{{ tr["back"] }}</a>
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
        <br><a href="/invoices">{{ tr["back"] }}</a>
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
    if next_url.startswith("/invoices"):
        return redirect(next_url)
    return redirect(f"/invoices?date_from={urllib.parse.quote(date_from)}&date_to={urllib.parse.quote(date_to)}&invoice_date={urllib.parse.quote(invoice_date)}")


@app.route("/invoices/download")
def invoices_download():
    if session.get("role") != "admin":
        return redirect("/")
    date_from = request.args.get("date_from", "").strip(); date_to = request.args.get("date_to", "").strip(); invoice_date = request.args.get("invoice_date", lux_now().strftime("%Y-%m-%d")).strip(); client = request.args.get("client", "").strip()
    conn = get_conn(); settings = get_invoice_settings(conn); rows = build_invoice_rows(conn, date_from, date_to, None, settings); conn.close()
    row = next((r for r in rows if r["client"] == client), None)
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
    buffer = io.BytesIO(); doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=1*cm, leftMargin=1*cm, topMargin=1*cm, bottomMargin=1*cm); styles = getSampleStyleSheet(); elements = [Paragraph(f"{tr['month_calendar']} {format_month_year(year, month)}", styles["Title"]), Spacer(1, 10)]
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
        new_name = request.form["name"].strip(); address = request.form["address"].strip(); contract_type = request.form.get("contract_type", "").strip(); contract_end_date = request.form.get("contract_end_date", "").strip()
        if new_name:
            old_color = c.execute("SELECT color FROM worker_colors WHERE worker_name = ?", (name,)).fetchone(); color_value = old_color[0] if old_color else "#f97316"; c.execute("UPDATE workers SET name = ?, address = ?, contract_type = ?, contract_end_date = ? WHERE name = ?", (new_name, address, contract_type, contract_end_date, name))
            for shift_id, worker_text in c.execute("SELECT id, worker FROM shifts").fetchall(): c.execute("UPDATE shifts SET worker = ? WHERE id = ?", (replace_worker_in_shift(worker_text, name, new_name), shift_id))
            c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (name,)); c.execute("INSERT OR REPLACE INTO worker_colors (worker_name, color) VALUES (?, ?)", (new_name, color_value))
        conn.commit(); conn.close(); return redirect("/")
    worker = c.execute("SELECT name, address, contract_type, contract_end_date FROM workers WHERE name = ?", (name,)).fetchone(); conn.close()
    if not worker: return redirect("/")
    return render_template_string(BASE_STYLE + """<div class="card" style="max-width:500px;margin:auto;"><h2>{{ tr["workers"] }} - {{ tr["edit"] }}</h2><form method="post"><input name="name" value="{{ worker[0] }}" required><input name="address" value="{{ worker[1] }}" placeholder="{{ tr['address'] }}"><input name="contract_type" value="{{ worker[2] }}" placeholder="{{ tr['contract_type'] }}"><label>{{ tr["contract_end_date"] }}</label><input type="date" name="contract_end_date" value="{{ worker[3] }}"><button>{{ tr["save"] }}</button></form><br><a href="/">{{ tr["back"] }}</a></div>""", tr=tr, worker=worker, dark=dark)

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
    return render_template_string(BASE_STYLE + """<div class="card" style="max-width:500px;margin:auto;"><h2>{{ tr["clients"] }} - {{ tr["edit"] }}</h2><form method="post"><input name="name" value="{{ client[0] }}" required><input name="address" value="{{ client[1] }}" placeholder="{{ tr['address'] }}" required><button>{{ tr["save"] }}</button></form><br><a href="/">{{ tr["back"] }}</a></div>""", tr=tr, client=client, dark=dark)

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
    name = request.form["worker_name"].strip(); address = request.form.get("address", "").strip(); contract_type = request.form.get("contract_type", "").strip(); contract_end_date = request.form.get("contract_end_date", "").strip()
    if name:
        conn = get_conn(); c = conn.cursor(); c.execute("INSERT OR IGNORE INTO workers (name, address, contract_type, contract_end_date) VALUES (?, ?, ?, ?)", (name, address, contract_type, contract_end_date)); c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (name, "#f97316")); conn.commit(); conn.close()
    return redirect("/")

@app.route("/add_client", methods=["POST"])
def add_client():
    if session.get("role") != "admin": return redirect("/")
    name = request.form["client_name"].strip(); address = request.form.get("address", "").strip()
    if name and address:
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
