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
import smtplib
import ssl
import imaplib
import hashlib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr, make_msgid
from datetime import datetime, timedelta, date as dt_date
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2

# ── SMTP / Email configuration (read from Render env vars) ─────────────────
SMTP_HOST      = os.environ.get("SMTP_HOST", "").strip()
try:
    SMTP_PORT  = int((os.environ.get("SMTP_PORT", "") or "587").strip())
except (TypeError, ValueError):
    SMTP_PORT  = 587   # safe default if env is empty / malformed
SMTP_USE_SSL   = os.environ.get("SMTP_USE_SSL", "0").strip().lower() in ("1", "true", "yes")
SMTP_ALLOW_INSECURE = os.environ.get("SMTP_ALLOW_INSECURE", "").strip().lower() in ("1", "true", "yes")
SMTP_USER      = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD  = os.environ.get("SMTP_PASSWORD", "")    # never log this
SMTP_FROM      = os.environ.get("SMTP_FROM", "").strip() or SMTP_USER
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Luxmann Services").strip()
EMAIL_SCHEDULER_SECRET = os.environ.get("EMAIL_SCHEDULER_SECRET", "").strip()

# ── IMAP archive ("Save copy to Sent folder") ──────────────────────────────
# Pure SMTP never puts a copy into the mailbox's Sent folder — that folder
# lives on IMAP and only IMAP clients write to it. If these are configured
# we APPEND a copy of every successfully sent email into IMAP_SENT_FOLDER,
# so the admin sees outgoing messages in Outlook / webmail just like
# manually composed ones. Failure to APPEND never fails the SMTP send.
IMAP_HOST        = os.environ.get("IMAP_HOST", "").strip()
try:
    IMAP_PORT    = int((os.environ.get("IMAP_PORT", "") or "993").strip())
except (TypeError, ValueError):
    IMAP_PORT    = 993
IMAP_USER        = os.environ.get("IMAP_USER", "").strip() or SMTP_USER
IMAP_PASSWORD    = os.environ.get("IMAP_PASSWORD", "") or SMTP_PASSWORD
IMAP_SENT_FOLDER = (os.environ.get("IMAP_SENT_FOLDER", "").strip() or "Sent")
# Belt-and-suspenders: optional silent Bcc to a fixed mailbox on every
# outbound email. Independent of IMAP — useful as a fallback archive when
# IMAP is unreachable or the server's Sent-folder name differs.
EMAIL_ARCHIVE_BCC = os.environ.get("EMAIL_ARCHIVE_BCC", "").strip()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Default templates seeded on init (one per language).
# Variables: {client_name} {invoice_number} {invoice_month} {invoice_date}
# {total_ttc} {company_name} {company_address} {company_phone} {company_email}
DEFAULT_INVOICE_EMAIL_SUBJECT = "Facture du mois de {invoice_month}"
DEFAULT_INVOICE_EMAIL_BODY = (
    "Léiwe Client,\n\n"
    "Mir soen Iech villmools Merci fir Äert Vertrauen an Är Zesummenaarbecht.\n"
    "Am Uschloss fannt Dir Är Rechnung.\n"
    "Mir bieden Iech, d'Rechnung am Uschloss virum Bezuelen ze kontrolléieren.\n"
    "Wann Dir e Feeler oder eng Onstëmmegkeet bemierkt, gitt eis w.e.g. direkt Bescheed.\n\n"
    "------------------------------------------------------------\n\n"
    "Cher Client,\n\n"
    "Nous vous remercions de votre confiance et de votre collaboration.\n"
    "Veuillez trouver ci-joint votre facture.\n"
    "Nous vous prions de bien vouloir vérifier la facture ci-jointe avant le paiement.\n"
    "En cas d'erreur ou d'incohérence, merci de nous en informer immédiatement.\n\n"
    "------------------------------------------------------------\n\n"
    "Lieber Kunde,\n\n"
    "Vielen Dank für Ihr Vertrauen und Ihre Zusammenarbeit.\n"
    "Anbei finden Sie Ihre Rechnung.\n"
    "Bitte überprüfen Sie die beigefügte Rechnung vor der Zahlung.\n"
    "Sollten Sie einen Fehler oder eine Unstimmigkeit feststellen, informieren Sie uns bitte umgehend.\n\n"
    "Mit freundlichen Grüßen,\n\n"
    "Luxmann Services\n"
    "32 rue Aneschbach\n"
    "WILTZ L-9511\n"
    "Tel: +352691642003"
)
DEFAULT_EMAIL_TEMPLATES = [
    (lang, DEFAULT_INVOICE_EMAIL_SUBJECT, DEFAULT_INVOICE_EMAIL_BODY)
    for lang in ("fr", "en", "bos", "de", "pt")
]

# ── Payment-reminder PDF strings (one block per language) ─────────────────
REMINDER_PDF_STRINGS = {
    "fr": {
        "doc_title":  "RAPPEL",
        "to":         "Destinataire",
        "date_label": "Date du rappel",
        "greeting":   "Madame, Monsieur,",
        "body_intro": ("Sauf erreur ou omission de notre part, nous constatons que la (les) "
                       "facture(s) ci-dessous reste(nt) impayée(s) à ce jour."),
        "col_number": "Facture n°",
        "col_date":   "Date",
        "col_amount": "Montant TTC",
        "total_due":  "Total dû",
        "body_pay_request": ("Nous vous remercions de bien vouloir procéder au règlement "
                              "dans les meilleurs délais."),
        "body_already_paid": ("Si le paiement a déjà été effectué, veuillez considérer "
                                "ce courrier comme nul et non avenu."),
        "closing":    "Avec nos remerciements anticipés, nous vous prions d'agréer nos salutations distinguées.",
        "payment_block_title": "Modalités de paiement",
    },
    "en": {
        "doc_title":  "REMINDER",
        "to":         "To",
        "date_label": "Reminder date",
        "greeting":   "Dear Customer,",
        "body_intro": ("Unless there is an error on our part, we note that the following invoice(s) "
                       "remain(s) unpaid as of today."),
        "col_number": "Invoice no.",
        "col_date":   "Date",
        "col_amount": "Amount (incl. VAT)",
        "total_due":  "Total due",
        "body_pay_request": "We kindly ask you to settle the payment at your earliest convenience.",
        "body_already_paid": "If payment has already been made, please disregard this notice.",
        "closing":    "Thank you in advance. Yours sincerely,",
        "payment_block_title": "Payment instructions",
    },
    "bos": {
        "doc_title":  "PODSJETNIK",
        "to":         "Primalac",
        "date_label": "Datum podsjetnika",
        "greeting":   "Poštovani,",
        "body_intro": ("Ukoliko nije došlo do propusta s naše strane, primjećujemo da sljedeća "
                       "faktura/e ostaje/u neplaćena/e do današnjeg datuma."),
        "col_number": "Faktura br.",
        "col_date":   "Datum",
        "col_amount": "Iznos sa TVA",
        "total_due":  "Ukupno duguje",
        "body_pay_request": "Molimo Vas da izmirite navedeni iznos u najkraćem mogućem roku.",
        "body_already_paid": "Ako je plaćanje već izvršeno, molimo Vas da ovaj dopis smatrate bespredmetnim.",
        "closing":    "Unaprijed zahvaljujemo i šaljemo srdačan pozdrav,",
        "payment_block_title": "Uslovi plaćanja",
    },
    "de": {
        "doc_title":  "MAHNUNG",
        "to":         "Empfänger",
        "date_label": "Datum",
        "greeting":   "Sehr geehrte Damen und Herren,",
        "body_intro": ("Sofern uns kein Versehen unterlaufen ist, stellen wir fest, dass die "
                       "folgende(n) Rechnung(en) bis heute unbeglichen ist/sind."),
        "col_number": "Rechnung Nr.",
        "col_date":   "Datum",
        "col_amount": "Betrag inkl. MwSt",
        "total_due":  "Gesamtbetrag offen",
        "body_pay_request": "Wir bitten Sie höflich, den Betrag schnellstmöglich zu überweisen.",
        "body_already_paid": "Sollten Sie bereits gezahlt haben, betrachten Sie dieses Schreiben bitte als gegenstandslos.",
        "closing":    "Mit freundlichen Grüßen,",
        "payment_block_title": "Zahlungshinweise",
    },
    "pt": {
        "doc_title":  "AVISO DE COBRANÇA",
        "to":         "Destinatário",
        "date_label": "Data",
        "greeting":   "Caro(a) Cliente,",
        "body_intro": ("Salvo erro da nossa parte, verificamos que a(s) seguinte(s) fatura(s) "
                       "permanece(m) por liquidar até à data de hoje."),
        "col_number": "Fatura n.º",
        "col_date":   "Data",
        "col_amount": "Valor c/ IVA",
        "total_due":  "Total em dívida",
        "body_pay_request": "Solicitamos a regularização do pagamento o mais brevemente possível.",
        "body_already_paid": "Caso o pagamento já tenha sido efetuado, considere este aviso sem efeito.",
        "closing":    "Com os melhores cumprimentos,",
        "payment_block_title": "Condições de pagamento",
    },
}

# Default reminder-email templates seeded on init (one per language).
DEFAULT_REMINDER_SUBJECT = {
    "fr":  "Rappel de paiement — Facture {invoice_number}",
    "en":  "Payment reminder — Invoice {invoice_number}",
    "bos": "Podsjetnik za plaćanje — Faktura {invoice_number}",
    "de":  "Zahlungserinnerung — Rechnung {invoice_number}",
    "pt":  "Aviso de cobrança — Fatura {invoice_number}",
}
DEFAULT_REMINDER_BODY = {
    "fr": ("Madame, Monsieur,\n\n"
           "Sauf erreur de notre part, nous constatons que la facture n° {invoice_number} "
           "du {invoice_date}, d'un montant de {total_ttc}, reste impayée à ce jour.\n\n"
           "Nous vous remercions de bien vouloir procéder au règlement dans les meilleurs délais.\n"
           "Si le paiement a déjà été effectué, veuillez considérer ce courrier comme nul et non avenu.\n\n"
           "Vous trouverez ci-joint le rappel détaillé au format PDF.\n\n"
           "Avec nos remerciements anticipés,\n"
           "{company_name}"),
    "en": ("Dear Customer,\n\n"
           "Unless there is an error on our part, we note that invoice no. {invoice_number} "
           "dated {invoice_date}, in the amount of {total_ttc}, remains unpaid.\n\n"
           "We kindly ask you to settle the payment at your earliest convenience.\n"
           "If payment has already been made, please disregard this notice.\n\n"
           "Please find the detailed reminder attached as PDF.\n\n"
           "Best regards,\n"
           "{company_name}"),
    "bos": ("Poštovani,\n\n"
            "Ukoliko nije došlo do propusta s naše strane, faktura br. {invoice_number} "
            "od {invoice_date} u iznosu od {total_ttc} ostaje neplaćena do današnjeg dana.\n\n"
            "Molimo Vas da izmirite navedeni iznos u najkraćem mogućem roku.\n"
            "Ako je plaćanje već izvršeno, molimo Vas da ovaj dopis smatrate bespredmetnim.\n\n"
            "U prilogu šaljemo detaljan podsjetnik u PDF formatu.\n\n"
            "Srdačan pozdrav,\n"
            "{company_name}"),
    "de": ("Sehr geehrte Damen und Herren,\n\n"
           "Sofern uns kein Versehen unterlaufen ist, stellen wir fest, dass die Rechnung "
           "Nr. {invoice_number} vom {invoice_date} über den Betrag von {total_ttc} "
           "bis heute unbeglichen ist.\n\n"
           "Wir bitten Sie höflich, den Betrag schnellstmöglich zu überweisen.\n"
           "Sollten Sie bereits gezahlt haben, betrachten Sie dieses Schreiben bitte als gegenstandslos.\n\n"
           "Die detaillierte Mahnung finden Sie als PDF im Anhang.\n\n"
           "Mit freundlichen Grüßen,\n"
           "{company_name}"),
    "pt": ("Caro(a) Cliente,\n\n"
           "Salvo erro da nossa parte, verificamos que a fatura n.º {invoice_number} de "
           "{invoice_date}, no valor de {total_ttc}, permanece por liquidar.\n\n"
           "Solicitamos a regularização do pagamento o mais brevemente possível.\n"
           "Caso o pagamento já tenha sido efetuado, considere este aviso sem efeito.\n\n"
           "Em anexo encontrará o aviso detalhado em PDF.\n\n"
           "Com os melhores cumprimentos,\n"
           "{company_name}"),
}

# Bulk reminders (multiple unpaid invoices for one client) get a separate
# subject/body so the text reads naturally — it uses {invoice_count} and
# {total_due} instead of a single {invoice_number}/{invoice_date}.
DEFAULT_REMINDER_BULK_SUBJECT = {
    "fr":  "Rappel — factures impayées",
    "en":  "Reminder — unpaid invoices",
    "bos": "Podsjetnik — neplaćene fakture",
    "de":  "Mahnung — unbezahlte Rechnungen",
    "pt":  "Aviso — faturas em dívida",
}
DEFAULT_REMINDER_BULK_BODY = {
    "fr": ("Madame, Monsieur,\n\n"
           "Sauf erreur de notre part, nous constatons que {invoice_count} facture(s) "
           "reste(nt) impayée(s) à ce jour, pour un montant total de {total_due}.\n\n"
           "Le détail des factures concernées figure dans le PDF joint.\n\n"
           "Nous vous remercions de bien vouloir procéder au règlement dans les meilleurs délais.\n"
           "Si les paiements ont déjà été effectués, veuillez considérer ce courrier comme nul et non avenu.\n\n"
           "Avec nos remerciements anticipés,\n"
           "{company_name}"),
    "en": ("Dear Customer,\n\n"
           "Unless there is an error on our part, we note that {invoice_count} invoice(s) "
           "remain unpaid as of today, for a total amount of {total_due}.\n\n"
           "Details of the invoices are listed in the attached PDF.\n\n"
           "We kindly ask you to settle the payment at your earliest convenience.\n"
           "If payments have already been made, please disregard this notice.\n\n"
           "Best regards,\n"
           "{company_name}"),
    "bos": ("Poštovani,\n\n"
            "Ukoliko nije došlo do propusta s naše strane, {invoice_count} faktura/e "
            "ostaje/u neplaćena/e do današnjeg dana, u ukupnom iznosu od {total_due}.\n\n"
            "Detalji faktura nalaze se u priloženom PDF dokumentu.\n\n"
            "Molimo Vas da izmirite navedene iznose u najkraćem mogućem roku.\n"
            "Ako su plaćanja već izvršena, molimo Vas da ovaj dopis smatrate bespredmetnim.\n\n"
            "Srdačan pozdrav,\n"
            "{company_name}"),
    "de": ("Sehr geehrte Damen und Herren,\n\n"
           "Sofern uns kein Versehen unterlaufen ist, stellen wir fest, dass {invoice_count} "
           "Rechnung(en) bis heute unbeglichen ist/sind, im Gesamtbetrag von {total_due}.\n\n"
           "Die Details der betroffenen Rechnungen finden Sie in der angehängten PDF.\n\n"
           "Wir bitten Sie höflich, die Beträge schnellstmöglich zu überweisen.\n"
           "Sollten Sie bereits gezahlt haben, betrachten Sie dieses Schreiben bitte als gegenstandslos.\n\n"
           "Mit freundlichen Grüßen,\n"
           "{company_name}"),
    "pt": ("Caro(a) Cliente,\n\n"
           "Salvo erro da nossa parte, verificamos que {invoice_count} fatura(s) "
           "permanece(m) por liquidar até hoje, num valor total de {total_due}.\n\n"
           "Os detalhes das faturas constam do PDF em anexo.\n\n"
           "Solicitamos a regularização dos pagamentos o mais brevemente possível.\n"
           "Caso os pagamentos já tenham sido efetuados, considere este aviso sem efeito.\n\n"
           "Com os melhores cumprimentos,\n"
           "{company_name}"),
}

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
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
    # "Remember me" path: when the user opts in at login we set
    # session.permanent=True so the cookie picks up this lifetime
    # (30 days) instead of being a browser-session cookie. Without
    # the checkbox the cookie still expires on browser close.
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    # Refresh the cookie's Expires header on every request so a
    # daily user keeps a rolling 30-day window — they only get
    # bounced to /login after 30 full days of inactivity.
    SESSION_REFRESH_EACH_REQUEST=True,
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
    "remember_me": "Zapamti me na ovom uredjaju",
        "title": "PLAN RADNIKA", "logged_as": "Logovan kao", "logout": "Odjava",
        "add_worker": "Dodaj radnika", "add_client": "Dodaj klijenta", "add_shift": "Dodaj smjenu",
        "worker_name": "Ime radnika", "client_name": "Naziv klijenta", "address": "Adresa",
        "choose_worker": "Izaberi radnike", "choose_client": "Izaberi klijenta",
        "filter_btn": "Filtriraj", "reset": "Reset", "plan": "PLAN",
        "no_shifts": "Trenutno nema unesenih smjena.", "edit": "Izmijeni", "delete": "Obrisi",
        "copy": "Copy", "copy_shift": "Kopiraj smjenu", "paste": "+ Paste",
        "week_calendar": "Sedmicni kalendar", "month_calendar": "Mjesecni kalendar", "pdf": "PDF raspored",
        "month_pdf": "PDF mjesecni kalendar", "back": "Nazad", "edit_shift": "Izmijeni smjenu", "save": "Sacuvaj",
        "clients_pdf": "PDF lista klijenata",
        "invoices_download_none": "Nema faktura za izabrani period ili sve nisu mogle biti generisane.",
        "date_filter_basis": "Filtriraj po",
        "export_pick_period_hint": "Izaberi tacan period i po potrebi klijenta.",
        "status_all": "Sve",
        "invoice_list_pdf": "Lista faktura PDF",
        "invoice_date_basis": "Datum fakture",
        "work_period_basis": "Period rada",
        "clients_pdf_title": "Lista klijenata",
        "notes": "Zabiljeske",
        "city_or_place": "Mjesto",
        "pdf_title": "Raspored radnika", "pdf_user": "Korisnik", "pdf_date": "Datum",
        "pdf_time": "Vrijeme", "pdf_worker": "Radnici", "pdf_client": "Klijent",
        "pdf_no_shifts": "Nema smjena",
    "billable_hours": "Sati (naplativi)", "user_mgmt": "Upravljanje korisnicima",
    "worker_hours": "Sati radnika",
    "worker_hours_pdf": "Moji sati PDF",
    "worker_hours_pdf_hint": "Preuzmi izvjestaj po periodu",
    "invoice_plan_mismatch_title": "Ova rucna faktura se ne poklapa sa trenutnim planom za ovaj period.",
    "invoice_plan_mismatch_text_only_title": "Iznos je isti, ali tekst/detalji fakture se razlikuju od trenutnog plana.",
    "invoice_reason_ht": "HT razlika",
    "invoice_reason_hours": "Sati razlika",
    "invoice_reason_text": "Tekst/detalji fakture",
    "invoice_view_diff": "Vidi razlike",
    "invoice_diff_saved": "Faktura sacuvana",
    "invoice_diff_plan": "Trenutni plan",
    "invoice_plan_mismatch_stored": "Sacuvano na fakturi",
    "invoice_plan_mismatch_plan": "Trenutni plan",
    "invoice_plan_mismatch_confirm": "Obnoviti prvu (uslugu) stavku iz trenutnog plana? Dodatne rucne stavke (odbici, dodatne usluge, napomene) ostaju sacuvane. Broj fakture, datum izdavanja i status placanja/slanja ostaju nepromijenjeni.",
    "invoice_rebuild_from_plan": "Obnovi stavke iz plana",
    "invoice_rebuild_paid_sent_warn": "Faktura je vec placena/poslana - obnavljanje zamijenjuje stavke ali cuva paid/sent status.",
    "mi_rebuild_ok": "Stavke fakture #{n} obnovljene iz trenutnog plana. Sati: {h}, TTC: {t} EUR",
    "mi_rebuild_not_manual": "Obnavljanje iz plana radi samo na rucnim fakturama.",
    "mi_rebuild_no_period": "Faktura nema period rada (date_from/date_to) - postavi ga u editoru prije obnavljanja.",
    "mi_rebuild_no_shifts": "Nema smjena u planu za ovog klijenta u zadatom periodu.",
    "invoice_cannot_rebuild": "Faktura #{n} se ne moze rekonstruisati iz trenutnog plana (klijent nema smjena u periodu). Provjerite plan ili obrisite fakturu i regenerisite je.",
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
    "remember_me": "Se souvenir de moi sur cet appareil",
    "title": "PLAN DE TRAVAIL", "add_worker": "Ajouter employe", "add_client": "Ajouter client",
    "add_shift": "Ajouter mission", "workers": "Employes", "clients": "Clients",
    "week_calendar": "Calendrier hebdomadaire", "month_calendar": "Calendrier mensuel",
    "monthly_hours": "Heures mensuelles", "weekly_hours": "Heures hebdomadaires",
    "back": "Retour", "save": "Enregistrer", "delete": "Supprimer", "edit": "Modifier",
    "clients_pdf": "PDF liste clients",
    "invoices_download_none": "Aucune facture pour la periode selectionnee ou aucune n'a pu etre generee.",
    "date_filter_basis": "Filtrer par",
    "export_pick_period_hint": "Choisissez la periode exacte et, si besoin, le client.",
    "status_all": "Tous",
    "invoice_list_pdf": "Liste des factures PDF",
    "invoice_date_basis": "Date de facture",
    "work_period_basis": "Periode de travail",
    "clients_pdf_title": "Liste des clients",
    "notes": "Notes",
    "city_or_place": "Localite",
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
    "remember_me": "Remember me on this device",
    "title": "WORK SCHEDULE", "add_worker": "Add worker", "add_client": "Add client",
    "add_shift": "Add shift", "workers": "Workers", "clients": "Clients",
    "nav_plan": "Plan", "nav_week": "Week", "nav_month": "Month",
    "nav_payroll": "Payroll", "nav_diagram": "Chart", "nav_route": "Route", "billable_hours": "Billable hours",
    "worker_hours": "Worker hours",
    "worker_hours_pdf": "My hours PDF",
    "worker_hours_pdf_hint": "Download a report for a period",
    "invoice_plan_mismatch_title": "This manual invoice no longer matches the current plan for its period.",
    "invoice_plan_mismatch_text_only_title": "Amount matches, but the invoice text/details differ from the current plan.",
    "invoice_reason_ht": "HT differs",
    "invoice_reason_hours": "Hours differ",
    "invoice_reason_text": "Text/details differ",
    "invoice_view_diff": "View differences",
    "invoice_diff_saved": "Saved invoice",
    "invoice_diff_plan": "Current plan",
    "invoice_plan_mismatch_stored": "Saved on invoice",
    "invoice_plan_mismatch_plan": "Current plan",
    "invoice_plan_mismatch_confirm": "Rebuild the first (service) line from the current plan? Extra manual rows (deductions, add-ons, notes) are kept. Invoice number, issue date and paid/sent status stay unchanged.",
    "invoice_rebuild_from_plan": "Rebuild from plan",
    "invoice_rebuild_paid_sent_warn": "Invoice is already paid/sent — the rebuild replaces line items but keeps paid/sent status.",
    "mi_rebuild_ok": "Invoice #{n} rebuilt from current plan. Hours: {h}, TTC: {t} EUR",
    "mi_rebuild_not_manual": "Rebuild from plan works only on manual invoices.",
    "mi_rebuild_no_period": "Invoice has no work period (date_from/date_to) — set it in the editor before rebuilding.",
    "mi_rebuild_no_shifts": "No shifts in the plan for this client in the requested period.",
    "invoice_cannot_rebuild": "Invoice #{n} cannot be rebuilt from the current plan (the client has no shifts in this period). Check the plan or delete the invoice and regenerate it.",
    "nav_settings": "Settings", "nav_docs_short": "Docs",
    "nav_language": "Language", "nav_tools": "Tools",
    "nav_admin_section": "Administration", "nav_account": "Account",
    "nav_users": "Users & password", "nav_navigation": "Navigation",
    "week_calendar": "Weekly calendar", "month_calendar": "Monthly calendar",
    "monthly_hours": "Monthly hours", "weekly_hours": "Weekly hours",
    "back": "Back", "save": "Save", "delete": "Delete", "edit": "Edit",
    "clients_pdf": "Clients PDF",
    "invoices_download_none": "No invoices for the selected period, or none could be generated.",
    "date_filter_basis": "Filter by",
    "export_pick_period_hint": "Choose the exact period and, if needed, the client.",
    "status_all": "All",
    "invoice_list_pdf": "Invoice list PDF",
    "invoice_date_basis": "Invoice date",
    "work_period_basis": "Work period",
    "clients_pdf_title": "Clients list",
    "notes": "Notes",
    "city_or_place": "City",
    "sick": "Sick leave", "vacation": "Vacation", "sick_vacation": "Sick leave / Vacation",
    "duplicate_shift_warning": "This shift with the same workers, time and client already exists.",
})
TRANSLATIONS["de"].update({
    "login_title": "Anmeldung", "login_btn": "Anmelden", "logout": "Abmelden",
    "remember_me": "Auf diesem Geraet angemeldet bleiben",
    "title": "ARBEITSPLAN", "add_worker": "Mitarbeiter hinzufugen", "add_client": "Kunde hinzufugen",
    "add_shift": "Einsatz hinzufugen", "workers": "Mitarbeiter", "clients": "Kunden",
    "week_calendar": "Wochenkalender", "month_calendar": "Monatskalender",
    "monthly_hours": "Monatsstunden", "weekly_hours": "Wochenstunden",
    "back": "Zuruck", "save": "Speichern", "delete": "Loschen", "edit": "Bearbeiten",
    "clients_pdf": "Kundenliste PDF",
    "invoices_download_none": "Keine Rechnungen fuer den gewaehlten Zeitraum oder keine konnte erstellt werden.",
    "date_filter_basis": "Filtern nach",
    "export_pick_period_hint": "Waehlen Sie den genauen Zeitraum und ggf. den Kunden.",
    "status_all": "Alle",
    "invoice_list_pdf": "Rechnungsliste PDF",
    "invoice_date_basis": "Rechnungsdatum",
    "work_period_basis": "Arbeitszeitraum",
    "clients_pdf_title": "Kundenliste",
    "notes": "Notizen",
    "city_or_place": "Ort",
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
    "remember_me": "Manter sessao iniciada neste dispositivo",
    "title": "PLANO DE TRABALHO", "add_worker": "Adicionar trabalhador", "add_client": "Adicionar cliente",
    "add_shift": "Adicionar turno", "workers": "Trabalhadores", "clients": "Clientes",
    "week_calendar": "Calendario semanal", "month_calendar": "Calendario mensal",
    "monthly_hours": "Horas mensais", "weekly_hours": "Horas semanais",
    "back": "Voltar", "save": "Guardar", "delete": "Apagar", "edit": "Editar",
    "clients_pdf": "PDF lista de clientes",
    "invoices_download_none": "Sem faturas para o periodo selecionado ou nenhuma pode ser gerada.",
    "date_filter_basis": "Filtrar por",
    "export_pick_period_hint": "Escolha o periodo exato e, se necessario, o cliente.",
    "status_all": "Todos",
    "invoice_list_pdf": "Lista de faturas PDF",
    "invoice_date_basis": "Data da fatura",
    "work_period_basis": "Periodo de trabalho",
    "clients_pdf_title": "Lista de clientes",
    "notes": "Notas",
    "city_or_place": "Cidade",
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
        "pdf_client": "Client", "pdf_no_shifts": "Aucune mission", "billable_hours": "Heures facturables", "user_mgmt": "Gestion des utilisateurs",
    "worker_hours": "Heures de l'employe",
    "worker_hours_pdf": "Mes heures PDF",
    "worker_hours_pdf_hint": "Telecharger un rapport pour la periode",
    "invoice_plan_mismatch_title": "Cette facture manuelle ne correspond plus au plan actuel pour cette periode.",
    "invoice_plan_mismatch_text_only_title": "Le montant est identique, mais le texte / les details de la facture different du plan actuel.",
    "invoice_reason_ht": "HT different",
    "invoice_reason_hours": "Heures differentes",
    "invoice_reason_text": "Texte/details differents",
    "invoice_view_diff": "Voir les differences",
    "invoice_diff_saved": "Facture sauvegardee",
    "invoice_diff_plan": "Plan actuel",
    "invoice_plan_mismatch_stored": "Enregistre sur la facture",
    "invoice_plan_mismatch_plan": "Plan actuel",
    "invoice_plan_mismatch_confirm": "Reconstruire la premiere ligne (service) a partir du plan actuel ? Les lignes manuelles supplementaires (deductions, extras, notes) sont conservees. Le n° de facture, la date et le statut paye/envoye restent inchanges.",
    "invoice_rebuild_from_plan": "Reconstruire depuis le plan",
    "invoice_rebuild_paid_sent_warn": "Facture deja payee/envoyee - la reconstruction remplace les lignes mais conserve le statut paye/envoye.",
    "mi_rebuild_ok": "Facture #{n} reconstruite depuis le plan actuel. Heures: {h}, TTC: {t} EUR",
    "mi_rebuild_not_manual": "La reconstruction fonctionne uniquement sur les factures manuelles.",
    "mi_rebuild_no_period": "La facture n'a pas de periode de travail (date_from/date_to) - definis-la dans l'editeur avant reconstruction.",
    "mi_rebuild_no_shifts": "Aucune mission dans le plan pour ce client sur la periode demandee.",
    "invoice_cannot_rebuild": "La facture #{n} ne peut pas etre reconstruite depuis le plan actuel (le client n'a aucune mission sur cette periode). Verifiez le plan ou supprimez la facture puis regenerez-la.",
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
        "team": "Team", "menu": "Menü",
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
        "team": "Equipa", "menu": "Menu",
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
        "pdf_no_shifts": "Keine Einsaetze",
    "billable_hours": "Abrechenbare Stunden", "user_mgmt": "Benutzerverwaltung", "add_user": "Benutzer hinzufuegen",
    "worker_hours": "Mitarbeiterstunden",
    "worker_hours_pdf": "Meine Stunden PDF",
    "worker_hours_pdf_hint": "Bericht fuer einen Zeitraum herunterladen",
    "invoice_plan_mismatch_title": "Diese manuelle Rechnung stimmt nicht mehr mit dem aktuellen Plan fuer diesen Zeitraum ueberein.",
    "invoice_plan_mismatch_text_only_title": "Der Betrag stimmt, aber der Rechnungstext/-Details weichen vom aktuellen Plan ab.",
    "invoice_reason_ht": "HT weicht ab",
    "invoice_reason_hours": "Stunden weichen ab",
    "invoice_reason_text": "Text/Details weichen ab",
    "invoice_view_diff": "Unterschiede anzeigen",
    "invoice_diff_saved": "Gespeicherte Rechnung",
    "invoice_diff_plan": "Aktueller Plan",
    "invoice_plan_mismatch_stored": "Auf Rechnung gespeichert",
    "invoice_plan_mismatch_plan": "Aktueller Plan",
    "invoice_plan_mismatch_confirm": "Erste (Leistungs-)Position aus dem aktuellen Plan neu erstellen? Zusaetzliche manuelle Positionen (Abzuege, Extras, Notizen) bleiben erhalten. Rechnungsnummer, Rechnungsdatum und Bezahlt/Gesendet-Status bleiben unveraendert.",
    "invoice_rebuild_from_plan": "Aus Plan neu erstellen",
    "invoice_rebuild_paid_sent_warn": "Rechnung bereits bezahlt/gesendet - Neuaufbau ersetzt Positionen, behaelt jedoch Bezahlt/Gesendet-Status.",
    "mi_rebuild_ok": "Rechnung #{n} aus aktuellem Plan neu erstellt. Stunden: {h}, TTC: {t} EUR",
    "mi_rebuild_not_manual": "Neuaufbau aus Plan funktioniert nur bei manuellen Rechnungen.",
    "mi_rebuild_no_period": "Rechnung hat keinen Arbeitszeitraum (date_from/date_to) - im Editor festlegen vor Neuaufbau.",
    "mi_rebuild_no_shifts": "Keine Einsaetze im Plan fuer diesen Kunden im angeforderten Zeitraum.",
    "invoice_cannot_rebuild": "Rechnung #{n} kann aus dem aktuellen Plan nicht neu erstellt werden (der Kunde hat in diesem Zeitraum keine Einsaetze). Plan pruefen oder Rechnung loeschen und neu erstellen.",
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
        "pdf_no_shifts": "Sem turnos",
    "billable_hours": "Horas faturaveis", "user_mgmt": "Gestao de utilizadores", "add_user": "Adicionar utilizador",
    "worker_hours": "Horas do trabalhador",
    "worker_hours_pdf": "As minhas horas PDF",
    "worker_hours_pdf_hint": "Descarregar relatorio por periodo",
    "invoice_plan_mismatch_title": "Esta fatura manual ja nao corresponde ao plano atual deste periodo.",
    "invoice_plan_mismatch_text_only_title": "O montante e igual, mas o texto/detalhes da fatura diferem do plano atual.",
    "invoice_reason_ht": "HT diferente",
    "invoice_reason_hours": "Horas diferentes",
    "invoice_reason_text": "Texto/detalhes diferentes",
    "invoice_view_diff": "Ver diferencas",
    "invoice_diff_saved": "Fatura guardada",
    "invoice_diff_plan": "Plano atual",
    "invoice_plan_mismatch_stored": "Guardado na fatura",
    "invoice_plan_mismatch_plan": "Plano atual",
    "invoice_plan_mismatch_confirm": "Reconstruir a primeira linha (servico) a partir do plano atual? As linhas manuais extra (deducoes, extras, notas) sao mantidas. Numero da fatura, data de emissao e estado pago/enviado ficam inalterados.",
    "invoice_rebuild_from_plan": "Reconstruir do plano",
    "invoice_rebuild_paid_sent_warn": "Fatura ja paga/enviada - a reconstrucao substitui as linhas mas mantem o estado pago/enviado.",
    "mi_rebuild_ok": "Fatura #{n} reconstruida a partir do plano atual. Horas: {h}, TTC: {t} EUR",
    "mi_rebuild_not_manual": "Reconstruir do plano funciona apenas em faturas manuais.",
    "mi_rebuild_no_period": "Fatura sem periodo de trabalho (date_from/date_to) - define-o no editor antes de reconstruir.",
    "mi_rebuild_no_shifts": "Sem turnos no plano para este cliente no periodo solicitado.",
    "invoice_cannot_rebuild": "A fatura #{n} nao pode ser reconstruida a partir do plano atual (o cliente nao tem turnos neste periodo). Verifique o plano ou apague a fatura e volte a gera-la.",
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
    "inv_gen_preview_title": "Pregled prije generisanja faktura",
    "inv_gen_will_generate": "Bice generisano",
    "inv_gen_exact_skip": "Vec postoji (preskoci)",
    "inv_gen_overlap_warn": "Preklapanje sa postojecom fakturom",
    "inv_gen_overlap_block": "Nije generisano zbog preklapanja",
    "inv_gen_confirm": "Potvrdi generisanje",
    "inv_gen_force": "Generisi ipak (sa preklapanjima)",
    "inv_gen_cancel": "Odustani",
    "inv_gen_nothing_to_do": "Nema klijenata za generisanje u ovom periodu.",
    "inv_gen_overlap_msg": "Za ovog klijenta vec postoji faktura ciji se period preklapa sa izabranim.",
    "inv_gen_force_confirm": "Stvarno generisati fakture iako postoji preklapanje perioda?",
    "list_from": "Lista od",
    "phone": "Telefon",
    "contract_signed": "Ugovor potpisan",
    "contract_from": "Ugovor od",
    "contract_to": "Ugovor do",
    "contact": "Kontakt",
    "contract": "Ugovor",
    "notes": "Napomena",
    "details": "Detalji",
    "client_details": "Detalji klijenta",
    "copy_mailing_label": "Kopiraj naljepnicu za kovertu",
    "more_actions": "Vise akcija",
    "client_not_found": "Klijent nije pronadjen.",
    "list_to": "do",
    "filter_list": "Filtriraj listu",
    "clear_filter": "Ocisti filter",
    "filter_active": "Filter aktivan",
    "showing_all": "Prikaz svih sacuvanih faktura",
    "inv_gen_no_rate": "Bez postavljene cijene", "inv_gen_empty": "Nema smjena ili klijenata sa postavljenom cijenom.",
    "inv_gen_failed": "Nije uspjelo upisivanje",
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
    "inv_gen_preview_title": "Review before generating invoices",
    "inv_gen_will_generate": "Will be generated",
    "inv_gen_exact_skip": "Already exists (skipped)",
    "inv_gen_overlap_warn": "Overlaps with existing invoice",
    "inv_gen_overlap_block": "Blocked due to overlap",
    "inv_gen_confirm": "Confirm generation",
    "inv_gen_force": "Generate anyway (including overlaps)",
    "inv_gen_cancel": "Cancel",
    "inv_gen_nothing_to_do": "No clients to generate for this period.",
    "inv_gen_overlap_msg": "This client already has an invoice whose period overlaps with the selected one.",
    "inv_gen_force_confirm": "Really generate invoices despite the overlapping period?",
    "list_from": "List from",
    "phone": "Phone",
    "contract_signed": "Contract signed",
    "contract_from": "Contract from",
    "contract_to": "Contract to",
    "contact": "Contact",
    "contract": "Contract",
    "notes": "Notes",
    "details": "Details",
    "client_details": "Client details",
    "copy_mailing_label": "Copy mailing label",
    "more_actions": "More actions",
    "client_not_found": "Client not found.",
    "list_to": "to",
    "filter_list": "Filter list",
    "clear_filter": "Clear filter",
    "filter_active": "Filter active",
    "showing_all": "Showing all saved invoices",
    "inv_gen_no_rate": "No rate set", "inv_gen_empty": "No shifts or clients with a rate in this period.",
    "inv_gen_failed": "Could not save",
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
    "inv_gen_preview_title": "Apercu avant generation des factures",
    "inv_gen_will_generate": "Sera genere",
    "inv_gen_exact_skip": "Existe deja (ignore)",
    "inv_gen_overlap_warn": "Chevauchement avec une facture existante",
    "inv_gen_overlap_block": "Non genere a cause du chevauchement",
    "inv_gen_confirm": "Confirmer la generation",
    "inv_gen_force": "Generer quand meme (avec chevauchements)",
    "inv_gen_cancel": "Annuler",
    "inv_gen_nothing_to_do": "Aucun client a generer pour cette periode.",
    "inv_gen_overlap_msg": "Ce client a deja une facture dont la periode chevauche celle selectionnee.",
    "inv_gen_force_confirm": "Generer quand meme les factures malgre le chevauchement de periode ?",
    "list_from": "Lister du",
    "phone": "Telephone",
    "contract_signed": "Contrat signe",
    "contract_from": "Contrat du",
    "contract_to": "Contrat au",
    "contact": "Contact",
    "contract": "Contrat",
    "notes": "Notes",
    "details": "Details",
    "client_details": "Details du client",
    "copy_mailing_label": "Copier l'etiquette d'envoi",
    "more_actions": "Plus d'actions",
    "client_not_found": "Client introuvable.",
    "list_to": "au",
    "filter_list": "Filtrer la liste",
    "clear_filter": "Effacer le filtre",
    "filter_active": "Filtre actif",
    "showing_all": "Toutes les factures enregistrees",
    "inv_gen_no_rate": "Tarif non defini", "inv_gen_empty": "Aucune prestation ou tarif client absent.",
    "inv_gen_failed": "Enregistrement impossible",
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
    "inv_gen_preview_title": "Ueberpruefung vor der Rechnungserstellung",
    "inv_gen_will_generate": "Wird erstellt",
    "inv_gen_exact_skip": "Bereits vorhanden (uebersprungen)",
    "inv_gen_overlap_warn": "Ueberschneidung mit vorhandener Rechnung",
    "inv_gen_overlap_block": "Wegen Ueberschneidung nicht erstellt",
    "inv_gen_confirm": "Erstellung bestaetigen",
    "inv_gen_force": "Trotzdem erstellen (mit Ueberschneidungen)",
    "inv_gen_cancel": "Abbrechen",
    "inv_gen_nothing_to_do": "Keine Kunden fuer diesen Zeitraum zu erstellen.",
    "inv_gen_overlap_msg": "Fuer diesen Kunden existiert bereits eine Rechnung, deren Zeitraum sich mit dem gewaehlten ueberschneidet.",
    "inv_gen_force_confirm": "Rechnungen trotz Zeitraum-Ueberschneidung wirklich erstellen?",
    "list_from": "Liste von",
    "phone": "Telefon",
    "contract_signed": "Vertrag unterzeichnet",
    "contract_from": "Vertrag von",
    "contract_to": "Vertrag bis",
    "contact": "Kontakt",
    "contract": "Vertrag",
    "notes": "Notizen",
    "details": "Details",
    "client_details": "Kundendetails",
    "copy_mailing_label": "Versandetikett kopieren",
    "more_actions": "Weitere Aktionen",
    "client_not_found": "Kunde nicht gefunden.",
    "list_to": "bis",
    "filter_list": "Liste filtern",
    "clear_filter": "Filter loeschen",
    "filter_active": "Filter aktiv",
    "showing_all": "Alle gespeicherten Rechnungen",
    "inv_gen_no_rate": "Kein Tarif festgelegt", "inv_gen_empty": "Keine Schichten oder Tarife fuer diesen Zeitraum.",
    "inv_gen_failed": "Speichern nicht moeglich",
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
    "inv_gen_preview_title": "Pre-visualizacao antes de gerar faturas",
    "inv_gen_will_generate": "Sera gerada",
    "inv_gen_exact_skip": "Ja existe (ignorada)",
    "inv_gen_overlap_warn": "Sobreposicao com fatura existente",
    "inv_gen_overlap_block": "Nao gerada devido a sobreposicao",
    "inv_gen_confirm": "Confirmar geracao",
    "inv_gen_force": "Gerar mesmo assim (com sobreposicoes)",
    "inv_gen_cancel": "Cancelar",
    "inv_gen_nothing_to_do": "Sem clientes para gerar neste periodo.",
    "inv_gen_overlap_msg": "Este cliente ja tem uma fatura cujo periodo se sobrepoe ao selecionado.",
    "inv_gen_force_confirm": "Realmente gerar faturas apesar da sobreposicao de periodo?",
    "list_from": "Lista de",
    "phone": "Telefone",
    "contract_signed": "Contrato assinado",
    "contract_from": "Contrato de",
    "contract_to": "Contrato ate",
    "contact": "Contacto",
    "contract": "Contrato",
    "notes": "Notas",
    "details": "Detalhes",
    "client_details": "Detalhes do cliente",
    "copy_mailing_label": "Copiar etiqueta de envio",
    "more_actions": "Mais acoes",
    "client_not_found": "Cliente nao encontrado.",
    "list_to": "ate",
    "filter_list": "Filtrar lista",
    "clear_filter": "Limpar filtro",
    "filter_active": "Filtro ativo",
    "showing_all": "Todas as faturas guardadas",
    "inv_gen_no_rate": "Tarifa nao definida", "inv_gen_empty": "Sem servicos ou tarifas definidas para este periodo.",
    "inv_gen_failed": "Nao foi possivel guardar",
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
        "diagram_avg_active_month": "Prosjek aktivnih mjeseci",
        "diagram_avg_formula": "TTC ukupno / aktivni mjeseci",
        "diagram_avg_tooltip": "Racuna se samo prosjek mjeseci koji imaju fakture, ne svih 12 mjeseci.",
        "diagram_month_detail": "Detalj po mjesecu",
        "diagram_view_details": "Vidi detalje",
        "diagram_invoice_count": "Broj faktura",
        "diagram_work_period": "Period rada",
        "diagram_issue_date": "Datum izdavanja",
        "diagram_no_invoices_month": "Nema faktura za ovaj mjesec.",
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
        "mi_period_from": "Period rada od",
        "mi_period_to": "Period rada do",
        "mi_period_hint": "Period u kojem je rad obavljen. Koristi se za /diagram i izvjestaje. Ako se ostavi prazno, pada na datum fakture.",
        "mi_period_apply": "Primijeni",
        "mi_period_suggest": "Designation pominje {month}. Predlazem period rada {from} -> {to}.",
        "mi_period_changed_flash": "Period rada fakture #{n} je promijenjen na {from} - {to}.",
        "diagram_fix_period": "Ispravi period rada",
        "mi_billed_to": "Fakturisi na",
        "mi_billing_address": "Adresa fakturiranja",
        "mi_items_title": "Stavke / Usluge",
        "mi_designation": "Opis",
        "mi_amount_ht": "Iznos HT (EUR)",
        "mi_add_item": "+ Dodaj stavku",
        "mi_payment_conditions": "Uslovi placanja",
        "mi_saved_items": "Sacuvane stavke",
        "mi_save_invoice": "Sacuvaj fakturu",
        "mi_save_pdf": "Sacuvaj + PDF",
        "mi_designation_placeholder": "Opis usluge...",
        "mi_reserve_error": "Nije moguce rezervisati broj fakture. Pokusajte ponovo.",
        "mi_vat_col": "TVA (%)",
        "mi_vat_short": "TVA",
        "mi_add_vat_label": "Ajouter taxe...",
        "mi_add_vat_prompt": "Saisir le nouveau taux de TVA (%)",
        "mi_invalid_vat": "Taux de TVA invalide.",
        "mi_modal_title": "Ajouter des articles sauvegardés",
        "mi_modal_search": "Rechercher par désignation ou montant",
        "mi_modal_recent": "Éléments récents",
        "mi_modal_archived": "Éléments archivés",
        "mi_modal_net": "Net à payer",
        "mi_modal_archives": "Archives",
        "mi_modal_no_items": "Aucun article correspondant.",
        "mi_modal_archive": "Archiver",
        "mi_modal_unarchive": "Restaurer",
        "mi_add_vat_label": "Dodaj taksu...",
        "mi_add_vat_prompt": "Unesite novu stopu TVA (%)",
        "mi_invalid_vat": "Neispravna stopa TVA.",
        "mi_modal_title": "Dodaj sačuvane stavke",
        "mi_modal_search": "Pretrazi po nazivu ili iznosu",
        "mi_modal_recent": "Skorasnje stavke",
        "mi_modal_archived": "Arhivirane stavke",
        "mi_modal_net": "Iznos",
        "mi_modal_archives": "Arhiva",
        "mi_modal_no_items": "Nema sacuvanih stavki.",
        "mi_modal_archive": "Arhiviraj",
        "mi_modal_unarchive": "Vrati",
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
        "diagram_avg_active_month": "Average active month",
        "diagram_avg_formula": "Total TTC / active months",
        "diagram_avg_tooltip": "Calculated only over months with invoices, not all 12 months of the year.",
        "diagram_month_detail": "Month detail",
        "diagram_view_details": "View details",
        "diagram_invoice_count": "Invoice count",
        "diagram_work_period": "Work period",
        "diagram_issue_date": "Issue date",
        "diagram_no_invoices_month": "No invoices for this month.",
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
        "mi_period_from": "Service period from",
        "mi_period_to": "Service period to",
        "mi_period_hint": "Period the work was actually performed. Drives /diagram and report bucketing. Falls back to the invoice date if left empty.",
        "mi_period_apply": "Apply",
        "mi_period_suggest": "Designation mentions {month}. Suggested work period {from} → {to}.",
        "mi_period_changed_flash": "Work period of invoice #{n} changed to {from} - {to}.",
        "diagram_fix_period": "Fix work period",
        "mi_billed_to": "Bill to",
        "mi_billing_address": "Billing address",
        "mi_items_title": "Items / Services",
        "mi_designation": "Description",
        "mi_amount_ht": "Amount HT (EUR)",
        "mi_add_item": "+ Add item",
        "mi_payment_conditions": "Payment conditions",
        "mi_saved_items": "Saved items",
        "mi_save_invoice": "Save invoice",
        "mi_save_pdf": "Save + PDF",
        "mi_designation_placeholder": "Description of service...",
        "mi_reserve_error": "Could not reserve invoice number. Please try again.",
        "mi_vat_col": "VAT (%)",
        "mi_vat_short": "VAT",
        "mi_add_vat_label": "Add tax...",
        "mi_add_vat_prompt": "Enter the new VAT rate (%)",
        "mi_invalid_vat": "Invalid VAT rate.",
        "mi_modal_title": "Add saved items",
        "mi_modal_search": "Search by description or amount",
        "mi_modal_recent": "Recent items",
        "mi_modal_archived": "Archived items",
        "mi_modal_net": "Net amount",
        "mi_modal_archives": "Archive",
        "mi_modal_no_items": "No saved items.",
        "mi_modal_archive": "Archive",
        "mi_modal_unarchive": "Restore",
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
        "diagram_avg_active_month": "Moyenne des mois facturés",
        "diagram_avg_formula": "Total TTC / mois facturés",
        "diagram_avg_tooltip": "Calculée uniquement sur les mois ayant des factures, pas sur les 12 mois de l'annee.",
        "diagram_month_detail": "Detail par mois",
        "diagram_view_details": "Voir details",
        "diagram_invoice_count": "Nb. factures",
        "diagram_work_period": "Periode de travail",
        "diagram_issue_date": "Date d'emission",
        "diagram_no_invoices_month": "Aucune facture pour ce mois.",
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
        "mi_period_from": "Periode du",
        "mi_period_to": "Periode au",
        "mi_period_hint": "Periode pendant laquelle le travail a ete effectue. Utilisee pour /diagram et les rapports. Defaut: la date de facture si laisse vide.",
        "mi_period_apply": "Appliquer",
        "mi_period_suggest": "La designation mentionne {month}. Periode de travail suggeree {from} → {to}.",
        "mi_period_changed_flash": "Periode de travail de la facture #{n} mise a jour: {from} - {to}.",
        "diagram_fix_period": "Corriger la periode",
        "mi_billed_to": "Facturé à",
        "mi_billing_address": "Adresse de facturation",
        "mi_items_title": "Articles / Prestations",
        "mi_designation": "Désignation",
        "mi_amount_ht": "Montant HT (€)",
        "mi_add_item": "+ Ajouter un article",
        "mi_payment_conditions": "Conditions et modalités de paiement",
        "mi_saved_items": "Articles sauvegardés",
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
        "diagram_avg_active_month": "Durchschnitt aktiver Monate",
        "diagram_avg_formula": "TTC gesamt / aktive Monate",
        "diagram_avg_tooltip": "Berechnet nur ueber Monate mit Rechnungen, nicht ueber alle 12 Monate.",
        "diagram_month_detail": "Monatsdetail",
        "diagram_view_details": "Details ansehen",
        "diagram_invoice_count": "Anzahl Rechnungen",
        "diagram_work_period": "Arbeitszeitraum",
        "diagram_issue_date": "Ausstellungsdatum",
        "diagram_no_invoices_month": "Keine Rechnungen fuer diesen Monat.",
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
        "mi_period_from": "Leistungszeitraum von",
        "mi_period_to": "Leistungszeitraum bis",
        "mi_period_hint": "Zeitraum, in dem die Arbeit ausgefuehrt wurde. Steuert /diagram und Berichte. Faellt auf das Rechnungsdatum zurueck, wenn leer.",
        "mi_period_apply": "Anwenden",
        "mi_period_suggest": "Bezeichnung erwaehnt {month}. Vorgeschlagener Leistungszeitraum {from} → {to}.",
        "mi_period_changed_flash": "Leistungszeitraum der Rechnung #{n} geaendert auf {from} - {to}.",
        "diagram_fix_period": "Leistungszeitraum korrigieren",
        "mi_billed_to": "Rechnungsempfanger",
        "mi_billing_address": "Rechnungsadresse",
        "mi_items_title": "Positionen / Leistungen",
        "mi_designation": "Bezeichnung",
        "mi_amount_ht": "Betrag HT (EUR)",
        "mi_add_item": "+ Position hinzufuegen",
        "mi_payment_conditions": "Zahlungsbedingungen",
        "mi_saved_items": "Gespeicherte Positionen",
        "mi_save_invoice": "Rechnung speichern",
        "mi_save_pdf": "Speichern + PDF",
        "mi_designation_placeholder": "Leistungsbeschreibung...",
        "mi_reserve_error": "Rechnungsnummer konnte nicht reserviert werden. Bitte erneut versuchen.",
        "mi_vat_col": "MwSt (%)",
        "mi_vat_short": "MwSt",
        "mi_add_vat_label": "Steuersatz hinzufuegen...",
        "mi_add_vat_prompt": "Neuen MwSt-Satz eingeben (%)",
        "mi_invalid_vat": "Ungueltiger MwSt-Satz.",
        "mi_modal_title": "Gespeicherte Positionen hinzufuegen",
        "mi_modal_search": "Suche nach Bezeichnung oder Betrag",
        "mi_modal_recent": "Letzte Positionen",
        "mi_modal_archived": "Archivierte Positionen",
        "mi_modal_net": "Netto-Betrag",
        "mi_modal_archives": "Archiv",
        "mi_modal_no_items": "Keine gespeicherten Positionen.",
        "mi_modal_archive": "Archivieren",
        "mi_modal_unarchive": "Wiederherstellen",
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
        "diagram_avg_active_month": "Media de meses ativos",
        "diagram_avg_formula": "TTC total / meses ativos",
        "diagram_avg_tooltip": "Calculada apenas sobre meses com faturas, nao sobre os 12 meses do ano.",
        "diagram_month_detail": "Detalhe por mes",
        "diagram_view_details": "Ver detalhes",
        "diagram_invoice_count": "Nº faturas",
        "diagram_work_period": "Periodo de trabalho",
        "diagram_issue_date": "Data de emissao",
        "diagram_no_invoices_month": "Sem faturas neste mes.",
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
        "mi_period_from": "Periodo de servico de",
        "mi_period_to": "Periodo de servico ate",
        "mi_period_hint": "Periodo em que o trabalho foi realizado. Usado em /diagram e relatorios. Em branco, usa a data da fatura.",
        "mi_period_apply": "Aplicar",
        "mi_period_suggest": "A designacao menciona {month}. Periodo de servico sugerido {from} → {to}.",
        "mi_period_changed_flash": "Periodo de servico da fatura #{n} alterado para {from} - {to}.",
        "diagram_fix_period": "Corrigir periodo",
        "mi_billed_to": "Faturar a",
        "mi_billing_address": "Endereco de faturacao",
        "mi_items_title": "Artigos / Servicos",
        "mi_designation": "Descricao",
        "mi_amount_ht": "Montante HT (EUR)",
        "mi_add_item": "+ Adicionar artigo",
        "mi_payment_conditions": "Condicoes de pagamento",
        "mi_saved_items": "Artigos guardados",
        "mi_save_invoice": "Guardar fatura",
        "mi_save_pdf": "Guardar + PDF",
        "mi_designation_placeholder": "Descricao do servico...",
        "mi_reserve_error": "Nao foi possivel reservar o numero da fatura. Tente novamente.",
        "mi_vat_col": "IVA (%)",
        "mi_vat_short": "IVA",
        "mi_add_vat_label": "Adicionar taxa...",
        "mi_add_vat_prompt": "Inserir nova taxa de IVA (%)",
        "mi_invalid_vat": "Taxa de IVA invalida.",
        "mi_modal_title": "Adicionar artigos guardados",
        "mi_modal_search": "Procurar por descricao ou montante",
        "mi_modal_recent": "Artigos recentes",
        "mi_modal_archived": "Artigos arquivados",
        "mi_modal_net": "Valor liquido",
        "mi_modal_archives": "Arquivo",
        "mi_modal_no_items": "Sem artigos guardados.",
        "mi_modal_archive": "Arquivar",
        "mi_modal_unarchive": "Restaurar",
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
    "my_documents": "Moji dokumenti",
    "my_clients": "Moji klijenti",
    "my_reports": "Moji izvjestaji",
    "client_statement": "Stanje racuna klijenta",
    "client_statement_pdf": "Stanje racuna klijenta PDF",
    "no_invoices_period": "Nema faktura za izabrani period.",
    "invoice_not_found": "Faktura nije pronadjena.",
    "invoice_delete_confirm": "Obrisati ovu fakturu?",
    "send_reminder": "Podsjetnik",
    "send_reminder_email": "Poslati podsjetnik emailom",
    "download_reminder": "Podsjetnik PDF",
    "reminder_no_unpaid": "Nema neplacenih faktura za podsjetnik.",
    "reminder_send_now_only": "Podsjetnici se mogu samo odmah poslati.",
    "smtp_not_configured": "SMTP nije konfigurisan.",
    "smtp_not_configured_drafted": "SMTP nije konfigurisan. Sacuvano kao nacrt.",
    "bulk_selected": "odabrano",
    "select_all": "Odaberi sve",
    "download_selected_pdf": "Preuzmi PDF (ZIP)",
    "mark_selected_paid": "Oznaci kao placene",
    "mark_selected_unpaid": "Oznaci kao neplacene",
    "mark_selected_sent": "Oznaci kao poslate",
    "mark_selected_unsent": "Oznaci kao neposlate",
    "delete_selected": "Obrisi odabrane",
    "delete_selected_confirm": "Obrisati {n} odabranih faktura?",
    "bulk_action_done": "Akcija izvrsena",
    "bulk_no_selection": "Niste odabrali ni jednu fakturu.",
    "pagination_previous": "Prethodna",
    "pagination_next": "Sljedeca",
    "send_email": "Posalji emailom",
    "email_to": "Primalac",
    "subject": "Naslov",
    "body": "Tekst poruke",
    "send_now": "Posalji odmah",
    "schedule": "Zakazi",
    "schedule_at": "Zakazi za",
    "save_draft": "Sacuvaj nacrt",
    "test_smtp": "Test SMTP",
    "pdf_attached": "PDF prilog",
    "template_vars": "Promjenljive",
    "email_sent_ok": "Email poslat.",
    "email_send_failed": "Slanje nije uspjelo",
    "email_scheduled": "Email je zakazan.",
    "email_send_later": "Posalji kasnije",
    "email_today": "Danas",
    "email_tomorrow": "Sutra",
    "email_select_date": "Izaberi datum",
    "email_time": "Vrijeme",
    "email_clear": "Ocisti",
    "email_set": "Postavi",
    "email_scheduled_for": "Zakazano za",
    "email_planned_for": "Zakazano za",
    "email_cancel_scheduled": "Otkazi zakazano slanje",
    "email_selected_schedule": "Izabrano",
    "shift_delete_confirm": "Obrisati ovu smjenu?",
    "user_delete_confirm": "Obrisati ovog korisnika?",
    "client_delete_confirm": "Obrisati ovog klijenta?",
    "absence_delete_confirm": "Obrisati ovo odsustvo?",
    "email_schedule_pick_first": "Prvo odaberi datum slanja prije nego zakazes.",
    "email_drafted": "Nacrt je sacuvan.",
    "email_test_ok": "Test email poslat.",
    "email_test_fail": "Test SMTP nije uspio",
    "invalid_email": "Neispravna email adresa.",
    "balance_due": "Saldo duga",
    "amount_paid": "Naplaceni iznos",
    "amount_total": "Ukupan iznos",
    "document": "Dokument",
    "all_filter": "--Sve--",
    "search_btn": "Pretrazi",
    "client_documents_of": "Dokumenti klijenta",
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
    "my_documents": "My documents",
    "my_clients": "My clients",
    "my_reports": "My reports",
    "client_statement": "Client account statement",
    "client_statement_pdf": "Client statement PDF",
    "no_invoices_period": "No invoices for the selected period.",
    "invoice_not_found": "Invoice not found.",
    "invoice_delete_confirm": "Delete this invoice?",
    "send_reminder": "Reminder",
    "send_reminder_email": "Send reminder by email",
    "download_reminder": "Reminder PDF",
    "reminder_no_unpaid": "No unpaid invoices to remind about.",
    "reminder_send_now_only": "Reminders can only be sent immediately.",
    "smtp_not_configured": "SMTP not configured.",
    "smtp_not_configured_drafted": "SMTP not configured. Saved as draft.",
    "bulk_selected": "selected",
    "select_all": "Select all",
    "download_selected_pdf": "Download PDF (ZIP)",
    "mark_selected_paid": "Mark as paid",
    "mark_selected_unpaid": "Mark as unpaid",
    "mark_selected_sent": "Mark as sent",
    "mark_selected_unsent": "Mark as unsent",
    "delete_selected": "Delete selected",
    "delete_selected_confirm": "Delete {n} selected invoices?",
    "bulk_action_done": "Action completed",
    "bulk_no_selection": "No invoices selected.",
    "pagination_previous": "Previous",
    "pagination_next": "Next",
    "send_email": "Send by email",
    "email_to": "Recipient",
    "subject": "Subject",
    "body": "Message",
    "send_now": "Send now",
    "schedule": "Schedule",
    "schedule_at": "Schedule for",
    "save_draft": "Save draft",
    "test_smtp": "Test SMTP",
    "pdf_attached": "PDF attached",
    "template_vars": "Variables",
    "email_sent_ok": "Email sent.",
    "email_send_failed": "Send failed",
    "email_scheduled": "Email scheduled.",
    "email_send_later": "Send later",
    "email_today": "Today",
    "email_tomorrow": "Tomorrow",
    "email_select_date": "Pick a date",
    "email_time": "Time",
    "email_clear": "Clear",
    "email_set": "Set",
    "email_scheduled_for": "Scheduled for",
    "email_planned_for": "Scheduled for",
    "email_cancel_scheduled": "Cancel scheduled send",
    "email_selected_schedule": "Selected",
    "shift_delete_confirm": "Delete this shift?",
    "user_delete_confirm": "Delete this user?",
    "client_delete_confirm": "Delete this client?",
    "absence_delete_confirm": "Delete this absence?",
    "email_schedule_pick_first": "Pick a date first before scheduling.",
    "email_drafted": "Draft saved.",
    "email_test_ok": "Test email sent.",
    "email_test_fail": "SMTP test failed",
    "invalid_email": "Invalid email address.",
    "balance_due": "Balance due",
    "amount_paid": "Amount paid",
    "amount_total": "Total amount",
    "document": "Document",
    "all_filter": "--All--",
    "search_btn": "Search",
    "client_documents_of": "Documents of",
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
    "my_documents": "Mes documents",
    "my_clients": "Mes clients",
    "my_reports": "Mes rapports",
    "client_statement": "Releve de compte client",
    "client_statement_pdf": "Releve de compte client PDF",
    "no_invoices_period": "Aucune facture pour la periode selectionnee.",
    "invoice_not_found": "Facture introuvable.",
    "invoice_delete_confirm": "Supprimer cette facture ?",
    "send_reminder": "Rappel",
    "send_reminder_email": "Envoyer rappel par email",
    "download_reminder": "Rappel PDF",
    "reminder_no_unpaid": "Aucune facture impayée pour le rappel.",
    "reminder_send_now_only": "Les rappels ne peuvent qu'être envoyés immédiatement.",
    "smtp_not_configured": "SMTP non configuré.",
    "smtp_not_configured_drafted": "SMTP non configuré. Enregistré comme brouillon.",
    "bulk_selected": "selectionnees",
    "select_all": "Tout selectionner",
    "download_selected_pdf": "Telecharger PDF (ZIP)",
    "mark_selected_paid": "Marquer payees",
    "mark_selected_unpaid": "Marquer non payees",
    "mark_selected_sent": "Marquer envoyees",
    "mark_selected_unsent": "Marquer non envoyees",
    "delete_selected": "Supprimer la selection",
    "delete_selected_confirm": "Supprimer {n} factures selectionnees ?",
    "bulk_action_done": "Action terminee",
    "bulk_no_selection": "Aucune facture selectionnee.",
    "pagination_previous": "Précédent",
    "pagination_next": "Suivant",
    "send_email": "Envoyer par email",
    "email_to": "Destinataire",
    "subject": "Objet",
    "body": "Message",
    "send_now": "Envoyer maintenant",
    "schedule": "Planifier",
    "schedule_at": "Planifier pour",
    "save_draft": "Enregistrer brouillon",
    "test_smtp": "Test SMTP",
    "pdf_attached": "PDF joint",
    "template_vars": "Variables",
    "email_sent_ok": "Email envoye.",
    "email_send_failed": "L'envoi a echoue",
    "email_scheduled": "Email planifie.",
    "email_send_later": "Envoyer plus tard",
    "email_today": "Aujourd'hui",
    "email_tomorrow": "Demain",
    "email_select_date": "Choisir une date",
    "email_time": "Heure",
    "email_clear": "Effacer",
    "email_set": "Definir",
    "email_scheduled_for": "Programme pour",
    "email_planned_for": "Planifie pour",
    "email_cancel_scheduled": "Annuler l'envoi programme",
    "email_selected_schedule": "Selectionne",
    "shift_delete_confirm": "Supprimer ce service ?",
    "user_delete_confirm": "Supprimer cet utilisateur ?",
    "client_delete_confirm": "Supprimer ce client ?",
    "absence_delete_confirm": "Supprimer cette absence ?",
    "email_schedule_pick_first": "Choisis une date avant de programmer.",
    "email_drafted": "Brouillon enregistre.",
    "email_test_ok": "Email de test envoye.",
    "email_test_fail": "Echec du test SMTP",
    "invalid_email": "Adresse email invalide.",
    "balance_due": "Solde du",
    "amount_paid": "Montant paye",
    "amount_total": "Montant total",
    "document": "Document",
    "all_filter": "--Tous--",
    "search_btn": "Rechercher",
    "client_documents_of": "Documents de",
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
    "my_documents": "Meine Dokumente",
    "my_clients": "Meine Kunden",
    "my_reports": "Meine Berichte",
    "client_statement": "Kundenkontoauszug",
    "client_statement_pdf": "Kundenkontoauszug PDF",
    "no_invoices_period": "Keine Rechnungen fuer den gewaehlten Zeitraum.",
    "invoice_not_found": "Rechnung nicht gefunden.",
    "invoice_delete_confirm": "Diese Rechnung loeschen?",
    "send_reminder": "Mahnung",
    "send_reminder_email": "Mahnung per E-Mail senden",
    "download_reminder": "Mahnung PDF",
    "reminder_no_unpaid": "Keine unbezahlten Rechnungen fuer eine Mahnung.",
    "reminder_send_now_only": "Mahnungen koennen nur sofort gesendet werden.",
    "smtp_not_configured": "SMTP nicht konfiguriert.",
    "smtp_not_configured_drafted": "SMTP nicht konfiguriert. Als Entwurf gespeichert.",
    "bulk_selected": "ausgewaehlt",
    "select_all": "Alle auswaehlen",
    "download_selected_pdf": "PDF herunterladen (ZIP)",
    "mark_selected_paid": "Als bezahlt markieren",
    "mark_selected_unpaid": "Als unbezahlt markieren",
    "mark_selected_sent": "Als gesendet markieren",
    "mark_selected_unsent": "Als nicht gesendet markieren",
    "delete_selected": "Auswahl loeschen",
    "delete_selected_confirm": "{n} ausgewaehlte Rechnungen loeschen?",
    "bulk_action_done": "Aktion ausgefuehrt",
    "bulk_no_selection": "Keine Rechnung ausgewaehlt.",
    "pagination_previous": "Zurueck",
    "pagination_next": "Weiter",
    "send_email": "Per E-Mail senden",
    "email_to": "Empfaenger",
    "subject": "Betreff",
    "body": "Nachricht",
    "send_now": "Jetzt senden",
    "schedule": "Planen",
    "schedule_at": "Planen fuer",
    "save_draft": "Entwurf speichern",
    "test_smtp": "SMTP-Test",
    "pdf_attached": "PDF-Anhang",
    "template_vars": "Variablen",
    "email_sent_ok": "E-Mail gesendet.",
    "email_send_failed": "Senden fehlgeschlagen",
    "email_scheduled": "E-Mail geplant.",
    "email_send_later": "Spaeter senden",
    "email_today": "Heute",
    "email_tomorrow": "Morgen",
    "email_select_date": "Datum waehlen",
    "email_time": "Uhrzeit",
    "email_clear": "Loeschen",
    "email_set": "Uebernehmen",
    "email_scheduled_for": "Geplant fuer",
    "email_planned_for": "Geplant fuer",
    "email_cancel_scheduled": "Geplantes Senden abbrechen",
    "email_selected_schedule": "Ausgewaehlt",
    "shift_delete_confirm": "Diese Schicht loeschen?",
    "user_delete_confirm": "Diesen Benutzer loeschen?",
    "client_delete_confirm": "Diesen Kunden loeschen?",
    "absence_delete_confirm": "Diese Abwesenheit loeschen?",
    "email_schedule_pick_first": "Waehle zuerst ein Datum, bevor du planst.",
    "email_drafted": "Entwurf gespeichert.",
    "email_test_ok": "Test-E-Mail gesendet.",
    "email_test_fail": "SMTP-Test fehlgeschlagen",
    "invalid_email": "Ungueltige E-Mail-Adresse.",
    "balance_due": "Faelliger Saldo",
    "amount_paid": "Bezahlter Betrag",
    "amount_total": "Gesamtbetrag",
    "document": "Dokument",
    "all_filter": "--Alle--",
    "search_btn": "Suchen",
    "client_documents_of": "Dokumente von",
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
    "my_documents": "Os meus documentos",
    "my_clients": "Os meus clientes",
    "my_reports": "Os meus relatorios",
    "client_statement": "Extrato de conta do cliente",
    "client_statement_pdf": "Extrato de cliente PDF",
    "no_invoices_period": "Sem faturas para o periodo selecionado.",
    "invoice_not_found": "Fatura nao encontrada.",
    "invoice_delete_confirm": "Eliminar esta fatura?",
    "send_reminder": "Aviso",
    "send_reminder_email": "Enviar aviso por email",
    "download_reminder": "Aviso PDF",
    "reminder_no_unpaid": "Sem faturas em divida para enviar aviso.",
    "reminder_send_now_only": "Avisos so podem ser enviados imediatamente.",
    "smtp_not_configured": "SMTP nao configurado.",
    "smtp_not_configured_drafted": "SMTP nao configurado. Guardado como rascunho.",
    "bulk_selected": "selecionadas",
    "select_all": "Selecionar todas",
    "download_selected_pdf": "Descarregar PDF (ZIP)",
    "mark_selected_paid": "Marcar como pagas",
    "mark_selected_unpaid": "Marcar como nao pagas",
    "mark_selected_sent": "Marcar como enviadas",
    "mark_selected_unsent": "Marcar como nao enviadas",
    "delete_selected": "Eliminar selecao",
    "delete_selected_confirm": "Eliminar {n} faturas selecionadas?",
    "bulk_action_done": "Acao concluida",
    "bulk_no_selection": "Nenhuma fatura selecionada.",
    "pagination_previous": "Anterior",
    "pagination_next": "Seguinte",
    "send_email": "Enviar por email",
    "email_to": "Destinatario",
    "subject": "Assunto",
    "body": "Mensagem",
    "send_now": "Enviar agora",
    "schedule": "Agendar",
    "schedule_at": "Agendar para",
    "save_draft": "Guardar rascunho",
    "test_smtp": "Teste SMTP",
    "pdf_attached": "PDF anexo",
    "template_vars": "Variaveis",
    "email_sent_ok": "Email enviado.",
    "email_send_failed": "Falha no envio",
    "email_scheduled": "Email agendado.",
    "email_send_later": "Enviar mais tarde",
    "email_today": "Hoje",
    "email_tomorrow": "Amanha",
    "email_select_date": "Escolher data",
    "email_time": "Hora",
    "email_clear": "Limpar",
    "email_set": "Definir",
    "email_scheduled_for": "Agendado para",
    "email_planned_for": "Agendado para",
    "email_cancel_scheduled": "Cancelar envio agendado",
    "email_selected_schedule": "Selecionado",
    "shift_delete_confirm": "Eliminar este turno?",
    "user_delete_confirm": "Eliminar este utilizador?",
    "client_delete_confirm": "Eliminar este cliente?",
    "absence_delete_confirm": "Eliminar esta ausencia?",
    "email_schedule_pick_first": "Escolhe uma data antes de agendar.",
    "email_drafted": "Rascunho guardado.",
    "email_test_ok": "Email de teste enviado.",
    "email_test_fail": "Falha no teste SMTP",
    "invalid_email": "Endereco de email invalido.",
    "balance_due": "Saldo em divida",
    "amount_paid": "Valor pago",
    "amount_total": "Valor total",
    "document": "Documento",
    "all_filter": "--Todos--",
    "search_btn": "Pesquisar",
    "client_documents_of": "Documentos de",
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


def shift_billable_hours(shift):
    """Real billable hours for a shift row = duration × #workers.

    A 08:00–09:30 shift with two workers "Edita, Izeta" bills the
    client for 3.0h, not 1.5h. parse_shift_hours() returns duration
    only; every consumer that displays or sums BILLABLE hours must
    multiply by the worker count, otherwise "search shifts PDF"
    totals disagree with what the invoice actually charges.

    shift is a row tuple in the same shape returned by SELECT *
    FROM shifts (id, worker, client, date, time, status, ...).
    """
    try:
        time_str = shift[4]
        worker_text = shift[1]
    except (IndexError, TypeError):
        return 0.0
    return parse_shift_hours(time_str) * max(1, len(split_workers(worker_text)))


def shift_search_pdf_hours(shift, single_worker_scope=False):
    """Hours to display for a shift row on /shifts_search_pdf.

    Two very different mental models sit behind the same PDF:

      - "How much do we bill for this shift?"  → multiply duration
        by worker count. A 10:00–13:00 shift with two workers is
        6.0 billable hours.
      - "How much did THIS worker work on this shift?" → just
        parse the duration. The same shift is 3.0 hours from
        Izeta's timesheet's point of view.

    When the PDF is scoped to a specific worker (the ?worker=…
    filter or a non-admin worker viewing their own PDF), the row
    should read the personal-timesheet number, otherwise the
    admin-facing billing number. Pass single_worker_scope=True to
    switch modes; the invoice pipeline is untouched (build_invoice
    _rows keeps multiplying by worker count).
    """
    try:
        time_str = shift[4]
    except (IndexError, TypeError):
        return 0.0
    if single_worker_scope:
        return parse_shift_hours(time_str)
    return shift_billable_hours(shift)


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
    """Return the Monday of the requested week.

    With ?start=YYYY-MM-DD: snap back to that date's Monday.
    Without: snap TODAY to its Monday.

    Historic bug: the no-arg branch used to return the 1st of the
    current month. That happens to be a Monday sometimes (e.g.
    2026-06-01), but for any month where the 1st isn't a Monday
    the /week view mislabelled every column (day_names[0] = "Mon"
    was rendered next to a Wednesday date etc.) and the range
    covered days 1-7 instead of the current week.
    """
    week_start_str = request.args.get("start", "").strip()
    if week_start_str:
        try:
            d = datetime.strptime(week_start_str, "%Y-%m-%d")
            return d - timedelta(days=d.weekday())
        except Exception:
            pass
    today = datetime.today()
    # Normalize to midnight so callers that store or compare the raw
    # datetime don't carry the request-time hh:mm:ss around; every
    # existing caller only reads .strftime("%Y-%m-%d") so this is a
    # no-op today, but makes the return value predictable.
    today = datetime(today.year, today.month, today.day)
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


_INVOICE_ADDR_POSTCODE_RE = re.compile(r"\bL[-\s]?(\d{4})\b", re.IGNORECASE)


def format_invoice_address(address):
    """Turn a single-line client address into a clean postal block.

    Rules:
      - If the input already has newlines, respect the admin's manual
        layout as-is (only strip trailing whitespace on each line).
      - Otherwise look for a Luxembourg postcode "L-9674" / "L 9674" /
        "L9674". If found, break the string in two: the street part
        before the postcode goes on line 1, the postcode + locality
        goes on line 2.
      - Drop a trailing ", Luxembourg" that follows the postcode part
        (redundant for a Luxembourg-based invoice).

    Handled examples:
      "1 um Buren Nocher L-9674, Luxembourg"
        → "1 um Buren\nNocher L-9674"
      "23 Salzbaach L-9559 WILTZ"
        → "23 Salzbaach\nL-9559 WILTZ"
      "1 um Buren\nNocher L-9674"  (already multi-line)
        → unchanged
    """
    if not address:
        return address or ""
    raw = address.replace("\r", "")
    if "\n" in raw:
        return "\n".join(line.rstrip() for line in raw.split("\n") if line.strip())
    s = raw.strip()
    # Strip trailing ", Luxembourg" (any case, allow spaces)
    s = re.sub(r",\s*Luxembourg\.?\s*$", "", s, flags=re.IGNORECASE)
    m = _INVOICE_ADDR_POSTCODE_RE.search(s)
    if not m:
        return s
    postcode_start = m.start()
    street = s[:postcode_start].rstrip().rstrip(",").rstrip()
    postal = s[postcode_start:].strip().lstrip(",").lstrip()
    # "Nocher L-9674" — the locality often sits just before the
    # postcode as a single word. Move it down onto the postal line
    # so the street line is just "1 um Buren".
    parts = street.split()
    if len(parts) >= 2 and not any(ch.isdigit() for ch in parts[-1]):
        locality = parts[-1]
        # Only pull it down when the postal line is just the bare
        # postcode ("L-9674"). If the postcode is already followed by
        # a locality on that side ("L-9559 WILTZ") the address is
        # already well-formed and pulling another word from the street
        # would produce "…\nSalzbaach L-9559 WILTZ".
        postal_tokens = postal.split()
        if len(postal_tokens) == 1:
            street = " ".join(parts[:-1]).rstrip()
            postal = f"{locality} {postal}".strip()
    return f"{street}\n{postal}" if street and postal else (street or postal)


def invoice_service_title(date_from, date_to):
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d")
    except Exception:
        start = lux_now()
    month = month_name(start.month, "fr")
    prefix = "d'" if month[:1].lower() in "aeiou" else "de "
    return f"Entretien et nettoyage de la maison pour le mois {prefix}{month}'{str(start.year)[-2:]}"


def invoice_designation_lines(row):
    lines = [row.get("service_title") or invoice_service_title(row.get("date_from", ""), row.get("date_to", ""))]
    for detail in row.get("details", []):
        lines.append(f"{format_date(detail['date'])[:5]} {_compact_number(detail['hours'])}h")
    if "hours" in row:
        lines.append(f"Total {_compact_number(row['hours'])}h")
    if "hourly_rate" in row:
        lines.append(f"Prix {_compact_number(row['hourly_rate'])}€ l'heure")
    return lines


def invoice_designation_text(row):
    return "\n".join(invoice_designation_lines(row))


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
    """Rebuild the invoice preview row for a persisted invoice_records
    entry, using the CURRENT plan.

    Identity is the persisted (client_name, date_from, date_to) pair,
    NOT the invoice_number. build_invoice_rows() re-derives its
    invoice_number for every client in the window from the current
    settings.invoice_start_number + the sort index; that generated
    number can collide with a completely different persisted invoice.
    Pre-fix, the primary match was on invoice_number, so #4385 stored
    for "Astrid Kohl" happily grabbed a rebuilt "TELUS INDUSTRY" row
    that had been renumbered to 4385 in the current pass and served
    the admin the wrong client name and address on /invoices/view.

    New rule:
      1. Match strictly on client_name — that is the stable identity
         for an auto invoice within a work period.
      2. Never fall back to invoice_number matching. If no row for
         this client exists in the window (client was removed, the
         work period changed, whatever), return (None, settings) so
         the caller renders a "cannot be rebuilt from plan" state
         instead of silently swapping in someone else's data.

    Persisted fields (invoice_number, amount, vat_amount, total,
    paid, sent) still get layered on top of the rebuilt row so the
    UI shows what the customer was actually billed — only the
    designation / details / address are pulled from the current
    plan.
    """
    settings = get_invoice_settings(conn)
    rows = build_invoice_rows(conn, record["date_from"], record["date_to"], None, settings)
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


def plan_summary_for_record(conn, record):
    """Return {"hours", "amount", "vat_amount", "total", "shifts",
    "row"} representing what the CURRENT plan would produce for this
    record's client + date_from..date_to window.

    Used by /invoices/view to detect stale manual invoices whose
    stored designation no longer matches the current shift schedule.
    The 'row' key carries the full build_invoice_rows() dict so the
    rebuild endpoint can reuse it to regenerate designation text
    without doing the math twice.

    Returns None when the record has no usable date range or no
    matching client in the current plan (nothing to compare against).
    """
    date_from = record.get("date_from") or ""
    date_to   = record.get("date_to")   or ""
    client    = record.get("client")    or ""
    if not (date_from and date_to and client):
        return None
    try:
        rows = build_invoice_rows(conn, date_from, date_to)
    except Exception:
        return None
    match = next((r for r in rows if r["client"] == client), None)
    if not match:
        return None
    return {
        "hours":      round(float(match.get("hours") or 0), 2),
        "amount":     round(float(match.get("amount") or 0), 2),
        "vat_amount": round(float(match.get("vat_amount") or 0), 2),
        "total":      round(float(match.get("total") or 0), 2),
        "shifts":     match.get("details", []),
        "row":        match,
    }


def invoice_number_sort_key(value):
    """Sort key for invoice numbers that works for both pure-digit
    modern numbers ("4385") and legacy/manual tags ("INV-2024-1",
    "4385-A"). Digits sort numerically after strings, so the SQL
    ORDER BY invoice_number DESC + this Python resort produces
    "4385, 4384, ..., INV-2024-1" — newest numeric first, then
    stringy legacy IDs. Crucially it never CASTs to INTEGER, which
    is what blew up /invoices/download_all on PostgreSQL when a
    non-digit invoice_number was in range."""
    s = str(value or "")
    return (1, int(s)) if s.isdigit() else (0, s.lower())


def fetch_invoice_records(conn, date_from=None, date_to=None, client=None,
                          status="all", date_basis="invoice_date"):
    """Read invoice_records with optional date / client / status filters.

    ``date_basis`` controls which date column the date_from/date_to
    window applies to AND the ORDER BY column on the result:

      - ``"invoice_date"`` (default): the issuance date on the
        invoice_records row. Used by:
          - the main /invoices listing
          - /invoices/download_all and /invoices/list_pdf when the
            admin picks "Datum fakture" in the export form (the
            default) — "give me every invoice ISSUED in this range"
        Mental model: "find invoices whose paper date falls in
        this window".
      - ``"work_period"``: the work-period start (``date_from``
        column on invoice_records). Used by:
          - /diagram (revenue by service month)
          - /invoices/client and /invoices/client_statement
          - /invoices/download_all and /invoices/list_pdf when the
            admin picks "Period rada" in the export form
        Mental model: "show me what was WORKED in this window",
        so a May-shift invoice issued in June lands in the May
        bucket.

    Anything else falls back to invoice_date so a typo can't widen
    the result silently.
    """
    if date_basis not in ("invoice_date", "work_period"):
        date_basis = "invoice_date"
    date_column = "date_from" if date_basis == "work_period" else "invoice_date"
    c = conn.cursor()
    conditions = []
    params = []
    conditions.append("COALESCE(deleted, 0) = 0")
    if date_from:
        conditions.append(f"{date_column} >= ?")
        params.append(date_from)
    if date_to:
        conditions.append(f"{date_column} <= ?")
        params.append(date_to)
    if client:
        conditions.append("client_name = ?")
        params.append(client)
    if status == "paid":
        conditions.append("paid = 1")
    elif status == "unpaid":
        conditions.append("paid = 0")
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    # Sort by whichever date column the caller asked to filter on, so
    # a work-period filter produces a work-period-ordered result and
    # not a result sorted by issuance date.
    order_col = "date_from" if date_basis == "work_period" else "invoice_date"
    query = f"""
        SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date, COALESCE(sent, 0), COALESCE(sent_date, ''), COALESCE(source, 'auto')
        FROM invoice_records
        {where}
        ORDER BY {order_col} DESC, invoice_number DESC
    """
    rows = [invoice_record_to_dict(row) for row in c.execute(query, params).fetchall()]
    rows.sort(
        key=lambda r: (r.get(order_col) or "", invoice_number_sort_key(r.get("invoice_number"))),
        reverse=True,
    )
    return rows


def fetch_invoice_records_for_work_period(conn, date_from, date_to, invoice_date=None):
    c = conn.cursor()
    # Auto invoices carry a date_from..date_to work period and are matched
    # exactly on that pair. Manual invoices have NO work period — only an
    # invoice_date — so the old "date_from = ? AND date_to = ?" filter
    # silently dropped any manual whose dates didn't coincide with the
    # listing's work period. Bug symptom: admin saved a manual invoice
    # (invoice_records row + next_invoice_number bumped) but it never
    # appeared on /invoices because the default listing filters by last
    # month while the manual was dated today.
    #
    # Fix: keep the strict work-period match for auto invoices, and OR-in
    # manual invoices whose invoice_date matches either the listing's
    # invoice_date filter or falls inside the work-period range. The
    # fallback below still covers the all-empty case.
    inv_match = invoice_date or ""
    query = """
        SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date, COALESCE(sent, 0), COALESCE(sent_date, ''), COALESCE(source, 'auto')
        FROM invoice_records
        WHERE COALESCE(deleted, 0) = 0
          AND (
            (date_from = ? AND date_to = ?)
            OR (COALESCE(source, 'auto') = 'manual'
                AND (invoice_date = ?
                     OR (invoice_date >= ? AND invoice_date <= ?)))
          )
        ORDER BY invoice_number DESC, invoice_date DESC
    """
    rows = [invoice_record_to_dict(row) for row in c.execute(query, (date_from, date_to, inv_match, date_from, date_to)).fetchall()]
    rows.sort(
        key=lambda r: (invoice_number_sort_key(r.get("invoice_number")), r.get("invoice_date") or ""),
        reverse=True,
    )
    if rows or not invoice_date:
        return rows
    fallback_query = """
        SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date, COALESCE(sent, 0), COALESCE(sent_date, ''), COALESCE(source, 'auto')
        FROM invoice_records
        WHERE COALESCE(deleted, 0) = 0 AND invoice_date = ?
        ORDER BY invoice_number DESC, invoice_date DESC
    """
    fallback = [invoice_record_to_dict(row) for row in c.execute(fallback_query, (invoice_date,)).fetchall()]
    fallback.sort(
        key=lambda r: (invoice_number_sort_key(r.get("invoice_number")), r.get("invoice_date") or ""),
        reverse=True,
    )
    return fallback


def invoice_number_from_index(settings, index):
    return str(int(settings.get("invoice_start_number") or 1) + index)


def _compact_number(value):
    try:
        number = float(value or 0)
    except Exception:
        number = 0.0
    if abs(number - round(number)) < 0.005:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _invoice_payment_terms_html(settings, override_terms=None):
    raw = (override_terms if override_terms is not None else settings.get("payment_terms", ""))
    raw = (raw or "").strip()
    normalized = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii").lower()
    use_standard = (
        not raw
        or (
            "paiement a 15 jours" in normalized
            and "post luxembourg" in normalized
            and "facture" in normalized
        )
    )
    if use_standard:
        terms = "<br/>".join([
            "Paiement \u00e0 15 jours d\u00e8s r\u00e9ception de la facture.",
            "Post Luxembourg BIC (CCPLLULL) LU60 1111 7815 3607 0000",
            "Lors du virement, veuillez indiquer r\u00e9f\u00e9rence suivante: ***Facture n\u00b0***",
        ])
    else:
        terms = _html.escape(raw).replace("\n", "<br/>")
    vat = (settings.get("company_vat") or "").strip()
    if vat:
        terms += "<br/>" + _html.escape(vat)
    return terms


def _invoice_view_context(conn, record):
    """Build a uniform dict for the HTML invoice preview that mirrors
    the PDF layout (build_invoice_pdf + build_manual_invoice_pdf).
    Returns None if data is missing for a manual invoice.
    """
    settings = get_invoice_settings(conn)
    is_manual = record.get("source") == "manual"
    template_colors = {"orange": "#ff7a2f", "blue": "#1f4f82", "green": "#2f7d32"}
    accent = template_colors.get(settings.get("invoice_template", "orange"), "#ff7a2f")

    ctx = {
        "invoice_number": record["invoice_number"],
        "invoice_date":   format_date(record.get("invoice_date") or ""),
        "is_manual":      is_manual,
        "accent":         accent,
        "company_name":    settings.get("company_name", "") or "",
        "company_address": settings.get("company_address", "") or "",
        "company_phone":   settings.get("company_phone", "") or "",
        "company_email":   settings.get("company_email", "") or "",
        "payment_terms_html": _invoice_payment_terms_html(settings),
        "client_name":    "",
        "client_address": "",
        "client_email":   "",
        "items":          [],     # list of {designation, amount, vat_pct, ttc}
        "total_ht":       0.0,
        "total_vat":      0.0,
        "total_ttc":      0.0,
        "vat_label":      "TVA",  # e.g. "TVA 17.0%"
        "show_vat_pct":   True,
    }

    if is_manual:
        c = conn.cursor()
        drow = c.execute(
            "SELECT client_name, client_address, items_json, payment_terms "
            "FROM manual_invoice_drafts WHERE invoice_number=?",
            (record["invoice_number"],),
        ).fetchone()
        if not drow:
            return None
        client_name, client_addr, items_json, pterms = drow
        ctx["client_name"]    = client_name or record.get("client", "")
        ctx["client_address"] = format_invoice_address(client_addr) if client_addr else ""
        try:
            items = json.loads(items_json or "[]")
        except Exception:
            items = []
        # Match the new build_manual_invoice_pdf: ONE ROW PER ITEM with its
        # own amount. Auto-converted invoices still produce a single row.
        rates_seen = set()
        for it in items:
            amt = float(it.get("amount") or 0)
            vr_pct = float(it.get("vat_rate") or 0)
            vat = amt * vr_pct / 100.0
            if abs(vr_pct) > 0.0001:
                rates_seen.add(round(vr_pct, 2))
            ctx["items"].append({
                "designation": (it.get("designation") or "").strip() or "-",
                "amount":      amt,
                "vat_pct":     vr_pct,
                "ttc":         amt + vat,
            })
            ctx["total_ht"]  += amt
            ctx["total_vat"] += vat
        ctx["total_ttc"] = ctx["total_ht"] + ctx["total_vat"]
        # Safety: never render an item-less table. Matches the PDF builder.
        if not ctx["items"]:
            ctx["items"].append({"designation": "-", "amount": 0.0,
                                  "vat_pct": 0, "ttc": 0.0})
        if len(rates_seen) == 1:
            ctx["vat_label"] = f"TVA {next(iter(rates_seen)):.1f}%"
        else:
            ctx["vat_label"] = "TVA"
        # Override payment terms only if the manual draft had a custom value
        if pterms:
            ctx["payment_terms_html"] = _invoice_payment_terms_html(settings, pterms)
    else:
        row, _set = get_invoice_row_for_record(conn, record)
        if not row:
            return None
        ctx["client_name"]    = row.get("client", "") or record.get("client", "")
        _raw_addr = row.get("address", "") or ""
        ctx["client_address"] = format_invoice_address(_raw_addr) if _raw_addr else "-"
        # Email is intentionally NOT surfaced to the invoice view — the
        # billing block should stay a clean postal address only. The
        # email lives in client_invoice_profiles for the send-by-email
        # flow.
        ctx["client_email"]   = ""
        # Auto invoice: one designation block (service title + dates + total + price)
        designation_text = "\n".join(invoice_designation_lines(row))
        ctx["items"].append({
            "designation": designation_text,
            "amount":      float(row.get("amount") or 0),
            "vat_pct":     float(row.get("vat_rate") or 0) * 100,
            "ttc":         float(row.get("total") or 0),
        })
        ctx["total_ht"]  = float(row.get("amount") or 0)
        ctx["total_vat"] = float(row.get("vat_amount") or 0)
        ctx["total_ttc"] = float(row.get("total") or 0)
        rate = float(row.get("vat_rate") or 0) * 100
        ctx["vat_label"] = f"TVA {rate:.1f}%"
    return ctx


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
    title_left = ParagraphStyle("InvoiceTitleLeft", parent=styles["Title"], alignment=TA_LEFT)
    title_right = ParagraphStyle("InvoiceTitleRight", parent=styles["Title"], alignment=TA_RIGHT)
    header = Table([[Paragraph(f"<b>{settings['company_name']}</b>", title_left), Paragraph(f"<b>{document_title}</b>", title_right)]], colWidths=[12.5*cm, 5*cm])
    header.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), colors.HexColor(accent)), ("TEXTCOLOR", (0,0), (-1,-1), colors.white), ("ALIGN", (0,0), (0,0), "LEFT"), ("ALIGN", (1,0), (1,0), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("LEFTPADDING", (0,0), (-1,-1), 8), ("RIGHTPADDING", (0,0), (-1,-1), 8)]))
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

    # Format the raw one-line address into a proper postal block and
    # drop the email \u2014 the client's email stays in the invoice profile
    # for the "Send via email" flow but no longer clutters the printed
    # invoice's "Factur\u00e9 \u00e0" panel.
    _addr = format_invoice_address(row["address"]) if row.get("address") else "-"
    billing = Paragraph(f"<b>Factur\u00e9 \u00e0</b><br/>{row['client']}<br/>{_addr.replace(chr(10), '<br/>')}", normal)
    meta = Paragraph(f"<b>Facture n\u00b0</b>&nbsp;&nbsp;&nbsp; {row['invoice_number']}<br/><b>Date</b>&nbsp;&nbsp;&nbsp; {format_date(invoice_date)}", normal)
    elements += [Table([[billing, meta]], colWidths=[10*cm, 7.5*cm], style=[("ALIGN", (1,0), (1,0), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "TOP")]), Spacer(1, 28)]

    detail_lines = invoice_designation_lines(row)
    if settings.get("invoice_text"):
        pass

    invoice_table = Table([
        [Paragraph("<b>D\u00c9SIGNATION</b>", normal), Paragraph("<b>MONTANT</b>", normal)],
        [Paragraph("<br/>".join(detail_lines), normal), Paragraph(f"{row['amount']:.2f}", normal)],
        [Paragraph("Total HT", normal), Paragraph(f"{row['amount']:.2f}", normal)],
        [Paragraph(f"TVA {row['vat_rate']*100:.1f}%", normal), Paragraph(f"{row['vat_amount']:.2f}", normal)],
        [Paragraph("<b>TOTAL TTC</b>", styles["Heading2"]), Paragraph(f"<b>{row['total']:.2f} \u20ac</b>", styles["Heading2"])],
    ], colWidths=[12.8*cm, 4.7*cm])
    invoice_table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey), ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("ALIGN", (1,1), (1,-1), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("ALIGN", (0,2), (0,4), "RIGHT"), ("BACKGROUND", (1,4), (1,4), colors.whitesmoke),
        ("MINROWHEIGHT", (0,1), (-1,1), 4.2*cm),
    ]))
    elements += [invoice_table, Spacer(1, 90)]

    elements += [
        Paragraph("<b>Conditions et modalit\u00e9s de paiement</b>", normal),
        Paragraph(_invoice_payment_terms_html(settings), normal),
    ]
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
    title_left = ParagraphStyle("InvoiceTitleLeft", parent=styles["Title"], alignment=TA_LEFT)
    title_right = ParagraphStyle("InvoiceTitleRight", parent=styles["Title"], alignment=TA_RIGHT)
    header = Table([[Paragraph(f"<b>{settings['company_name']}</b>", title_left), Paragraph(f"<b>{document_title}</b>", title_right)]], colWidths=[12.5*cm, 5*cm])
    header.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), colors.HexColor(accent)), ("TEXTCOLOR", (0,0), (-1,-1), colors.white), ("ALIGN", (0,0), (0,0), "LEFT"), ("ALIGN", (1,0), (1,0), "RIGHT"), ("LEFTPADDING", (0,0), (-1,-1), 8), ("RIGHTPADDING", (0,0), (-1,-1), 8)]))
    company_lines = [settings.get("company_address", "").replace("\n", "<br/>")]
    if settings.get("company_phone"):
        company_lines.append(f"Tel: {settings['company_phone']}")
    if settings.get("company_email"):
        company_lines.append(settings["company_email"])
    logo_cell = Image("static/logo.png", width=4.5*cm, height=2.4*cm) if os.path.exists("static/logo.png") else ""
    _quote_addr = format_invoice_address(data.get('client_address', '') or '')
    # Same policy as the invoice PDFs: postal block only, no email.
    # The client email still travels through the composer / send-by-
    # email flow, but the printed quote itself stays a clean address.
    client_block = f"<b>Devis pour</b><br/>{data['client_name']}<br/>{_quote_addr.replace(chr(10), '<br/>')}"
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
    # Only invoice numbers still present in invoice_records are reserved.
    # Hard-deleted test invoices can be regenerated with the same start number.
    rows = c.execute("SELECT invoice_number FROM invoice_records").fetchall()
    nums = []
    for (n,) in rows:
        try:
            nums.append(int("".join(filter(str.isdigit, str(n)))))
        except Exception:
            pass
    return str(max(max(nums) + 1, start) if nums else start)


# ═══════════════════════════════════════════════════════════════════════════
#  EMAIL — SMTP, PDF attachment, template rendering, queue scheduler
# ═══════════════════════════════════════════════════════════════════════════

def _is_valid_email(addr):
    return bool(addr and EMAIL_RE.match(addr.strip()))


def _split_email_list(s):
    """Parse a comma- or semicolon-separated email list, dropping invalid ones."""
    if not s:
        return []
    parts = re.split(r"[,;\s]+", s)
    return [p.strip() for p in parts if p.strip() and _is_valid_email(p.strip())]


def _email_body_to_html(body):
    """Render editable plain-text email body as safe HTML for nicer signatures."""
    html_lines = []
    for raw_line in (body or "").splitlines():
        line = str(raw_line or "")
        stripped = line.strip()
        if not stripped:
            html_lines.append("<br>")
            continue
        if re.fullmatch(r"-{10,}", stripped):
            html_lines.append(
                '<hr style="border:none;border-top:1px solid #d1d5db;'
                'margin:22px 0;">'
            )
            continue
        safe = _html.escape(line)
        if stripped == "Luxmann Services":
            safe = '<strong style="color:#16a34a;font-weight:700;">Luxmann Services</strong>'
        elif stripped.lower().startswith("tel:"):
            label, number = line.split(":", 1)
            safe = f"{_html.escape(label)}: <strong>{_html.escape(number.strip())}</strong>"
        html_lines.append(f"<div>{safe}</div>")
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;'
        'font-size:15px;line-height:1.55;color:#111827;">'
        + "\n".join(html_lines)
        + "</div>"
    )


def _smtp_send(to_addrs, subject, body, pdf_bytes=None, pdf_name="facture.pdf",
               cc=None, bcc=None):
    """Send an email via configured SMTP.

    Returns ``(ok, error_string, info)`` where ``info`` is a dict carrying
    forensic metadata about the attempt:

      - ``message_id``  : the stable RFC 2822 Message-ID we stamped on the
                          email before sending. Always present (even on
                          failure) so callers can log it for traceability.
      - ``raw``         : the raw MIME bytes of the sent message, suitable
                          for IMAP APPEND into the Sent folder. Present
                          only on successful SMTP send.
    """
    if not SMTP_HOST or not SMTP_FROM:
        return False, "SMTP not configured (SMTP_HOST / SMTP_FROM missing)", {}

    cc = list(cc or [])
    bcc = list(bcc or [])
    if isinstance(to_addrs, str):
        to_addrs = [to_addrs]

    valid_to = [a for a in to_addrs if _is_valid_email(a)]
    if not valid_to:
        return False, "No valid recipient address", {}

    # Fallback archive: BCC every outbound to the configured archive
    # mailbox. Independent of IMAP — even if IMAP append fails (wrong
    # folder name, blocked port, etc.) we still get a copy in our inbox.
    if EMAIL_ARCHIVE_BCC and _is_valid_email(EMAIL_ARCHIVE_BCC) \
            and EMAIL_ARCHIVE_BCC not in bcc and EMAIL_ARCHIVE_BCC not in cc \
            and EMAIL_ARCHIVE_BCC not in valid_to:
        bcc.append(EMAIL_ARCHIVE_BCC)

    msg = EmailMessage()
    from_addr = formataddr((SMTP_FROM_NAME or "", SMTP_FROM))
    msg["From"] = from_addr
    msg["Reply-To"] = from_addr
    msg["To"] = ", ".join(valid_to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject or "(no subject)"
    # Stamp a stable Message-ID BEFORE send so the queue/log row and the
    # message that actually left the server can be cross-referenced later
    # (e.g. when looking at the IMAP Sent folder or chasing a bounce).
    try:
        msgid_domain = SMTP_FROM.split("@", 1)[1] if "@" in SMTP_FROM else "planer.local"
    except Exception:
        msgid_domain = "planer.local"
    msg_id = make_msgid(domain=msgid_domain)
    msg["Message-ID"] = msg_id
    msg.set_content(body or "")
    if body:
        msg.add_alternative(_email_body_to_html(body), subtype="html")

    if pdf_bytes:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                           filename=pdf_name)

    all_rcpts = list(dict.fromkeys(valid_to + list(cc) + list(bcc)))
    info = {"message_id": msg_id}

    refused = {}
    try:
        if SMTP_USE_SSL or SMTP_PORT == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as s:
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASSWORD)
                refused = s.send_message(msg, from_addr=SMTP_FROM, to_addrs=all_rcpts) or {}
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
                s.ehlo()
                # STARTTLS is REQUIRED on plain ports unless admin explicitly
                # opted in to insecure mode via SMTP_ALLOW_INSECURE=1. We never
                # send credentials over an unencrypted connection silently.
                try:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                except smtplib.SMTPException as e:
                    if not SMTP_ALLOW_INSECURE:
                        return False, ("STARTTLS not available on this server. "
                                       "Set SMTP_ALLOW_INSECURE=1 to bypass "
                                       f"(at your own risk). Detail: {str(e)[:120]}"), info
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASSWORD)
                refused = s.send_message(msg, from_addr=SMTP_FROM, to_addrs=all_rcpts) or {}
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {e.__class__.__name__}: {str(e)[:900]}", info
    except (OSError, ssl.SSLError) as e:
        return False, f"Network/SSL error: {e.__class__.__name__}: {str(e)[:900]}", info
    except Exception as e:
        return False, f"{e.__class__.__name__}: {str(e)[:900]}", info

    # send_message() returns a dict {addr: (code, msg)} of recipients the
    # server refused even though the SMTP transaction itself succeeded.
    # With EMAIL_ARCHIVE_BCC in the mix we MUST distinguish:
    #   - a real recipient was refused  → this is a failed send, do NOT
    #     mark the invoice as sent and do NOT write a Sent-folder copy
    #     claiming we sent something the client never received.
    #   - only EMAIL_ARCHIVE_BCC was refused → the customer email is fine;
    #     just warn so the admin can fix their archive mailbox.
    if refused:
        # Email addresses are case-insensitive in practice (domain always,
        # mailbox by convention on every mainstream server). Normalize on
        # both sides so a server that bounces back "Client@Example.COM"
        # for our "client@example.com" still triggers the hard-fail path.
        real_set = {x.lower() for x in (list(valid_to) + list(cc))}
        real_refused = {a: refused[a] for a in refused
                        if (a or "").lower() in real_set}
        if real_refused:
            return False, f"Recipient refused: {real_refused}", info
        # Only the archive BCC bounced — surface it in logs but still
        # treat the send to the client as successful.
        try:
            app.logger.warning(
                "EMAIL_ARCHIVE_BCC refused by server (send still OK): %s",
                refused,
            )
        except Exception:
            pass

    # Success — serialize the raw MIME ONCE so callers can hand it to
    # _imap_append_sent() without rebuilding the message.
    try:
        info["raw"] = bytes(msg)
    except Exception:
        # Some payloads (rare) can't be re-encoded as bytes() in one shot;
        # fall back to as_string() which is always defined.
        info["raw"] = msg.as_string().encode("utf-8", "replace")
    return True, "", info


def _imap_append_sent(raw_bytes):
    """Best-effort: APPEND a sent message into the IMAP Sent folder.

    SMTP send does NOT touch the mailbox's Sent folder; only an IMAP
    client does. We piggy-back on the same mailbox credentials used by
    SMTP and write the raw MIME there, so the admin sees outgoing mail
    in Outlook / webmail just like manually composed messages.

    Failure modes (no IMAP config, login fail, wrong folder name, network
    drop, server returning NO/BAD) are ALL non-fatal: the SMTP send has
    already succeeded and the user gets a clear ``imap_saved=0`` +
    ``imap_error=...`` row in invoice_email_logs. Never raises.

    Returns ``(ok, error_string)``.
    """
    if not (IMAP_HOST and IMAP_USER and IMAP_PASSWORD):
        return False, "IMAP not configured"
    if not raw_bytes:
        return False, "no raw message bytes"
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30) as imap:
            imap.login(IMAP_USER, IMAP_PASSWORD)
            # \Seen so the archived copy doesn't show up as "new mail"
            # in the admin's inbox view. date_time=None → server uses
            # the current time as INTERNALDATE, which is what we want.
            typ, data = imap.append(IMAP_SENT_FOLDER, r"(\Seen)", None, raw_bytes)
            if typ != "OK":
                detail = ""
                try:
                    detail = (data[0] if data else b"").decode("utf-8", "replace")
                except Exception:
                    pass
                return False, f"IMAP APPEND not OK: {detail[:300]}"
            try:
                imap.logout()
            except Exception:
                # logout fail after a good APPEND is purely cosmetic
                pass
    except imaplib.IMAP4.error as e:
        return False, f"IMAP error: {str(e)[:300]}"
    except (OSError, ssl.SSLError) as e:
        return False, f"IMAP network/SSL: {e.__class__.__name__}: {str(e)[:300]}"
    except Exception as e:
        return False, f"IMAP {e.__class__.__name__}: {str(e)[:300]}"
    return True, ""


def _render_email_template(text, ctx):
    """Substitute {key} placeholders. Unknown vars left as-is to avoid KeyError."""
    if not text:
        return ""
    out = text
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", str(v) if v is not None else "")
    return out


def _build_invoice_pdf_for_email(conn, invoice_number):
    """Generate the PDF for an invoice (auto or manual). Returns (bytes, filename)."""
    c = conn.cursor()
    record_row = c.execute("""
        SELECT invoice_number, client_name, date_from, date_to, invoice_date,
               amount, vat_amount, total, paid, paid_date,
               COALESCE(sent,0), COALESCE(sent_date,''), COALESCE(source,'auto')
        FROM invoice_records WHERE invoice_number = ? AND COALESCE(deleted,0)=0
    """, (invoice_number,)).fetchone()
    if not record_row:
        return None, None
    record = invoice_record_to_dict(record_row)
    settings = get_invoice_settings(conn)
    client_safe = re.sub(r"[^A-Za-z0-9_-]+", "_", (record["client"] or "")).strip("_")[:40]
    fname = f"{invoice_number}-{client_safe or 'facture'}.pdf"
    if record.get("source") == "manual":
        draft_row = c.execute(
            "SELECT invoice_number, client_name, client_address, invoice_date, "
            "items_json, payment_terms FROM manual_invoice_drafts WHERE invoice_number=?",
            (invoice_number,)
        ).fetchone()
        if not draft_row:
            return None, None
        draft = {"invoice_number": draft_row[0], "client_name": draft_row[1],
                 "client_address": draft_row[2], "invoice_date": draft_row[3],
                 "items_json": draft_row[4], "payment_terms": draft_row[5]}
        buf = build_manual_invoice_pdf(draft, settings)
    else:
        row, _set = get_invoice_row_for_record(conn, record)
        if not row:
            return None, None
        buf = build_invoice_pdf(row, settings, record["invoice_date"],
                                 record["date_from"], record["date_to"])
    return buf.getvalue(), fname


def _invoice_email_context(conn, invoice_number):
    """Build the {key} context dict for a given invoice."""
    c = conn.cursor()
    rec = c.execute("""
        SELECT invoice_number, client_name, date_from, date_to, invoice_date,
               amount, vat_amount, total
        FROM invoice_records WHERE invoice_number = ? AND COALESCE(deleted,0)=0
    """, (invoice_number,)).fetchone()
    settings = get_invoice_settings(conn)
    if not rec:
        return {}
    # Build a French month name for the worked period, not the invoice issue date.
    try:
        period_date = rec[2] or rec[3] or rec[4]
        d = datetime.strptime(period_date, "%Y-%m-%d")
        month_str = month_name(d.month, "fr") + " " + str(d.year)
    except Exception:
        month_str = rec[2] or rec[3] or rec[4] or ""
    return {
        "client_name":    rec[1] or "",
        "invoice_number": rec[0] or "",
        "invoice_month":  month_str,
        "invoice_date":   format_date(rec[4] or ""),
        "total_ttc":      f"{float(rec[7] or 0):.2f} EUR",
        "company_name":   settings.get("company_name", "") or "",
        "company_address": settings.get("company_address", "") or "",
        "company_phone":  settings.get("company_phone", "") or "",
        "company_email":  settings.get("company_email", "") or "",
    }


def build_manual_invoice_pdf(draft, settings):
    """Build a ReportLab PDF for a manually created multi-line-item invoice.

    Visual style matches build_invoice_pdf (auto invoice) so both look identical.
    """
    buffer = io.BytesIO()
    inv_num     = draft["invoice_number"]
    client_name = draft.get("client_name", "") or "-"
    client_addr = draft.get("client_address", "") or ""
    inv_date    = draft.get("invoice_date", "")

    doc = pdf_doc(
        buffer, f"FACTURE {inv_num} - {client_name[:40]}",
        pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )
    styles = getSampleStyleSheet()
    template_colors = {"orange": "#ff7a2f", "blue": "#1f4f82", "green": "#2f7d32"}
    accent = template_colors.get(settings.get("invoice_template", "orange"), "#ff7a2f")
    normal = styles["Normal"]
    title_left = ParagraphStyle("InvoiceTitleLeft", parent=styles["Title"], alignment=TA_LEFT)
    title_right = ParagraphStyle("InvoiceTitleRight", parent=styles["Title"], alignment=TA_RIGHT)

    # ── Header bar (identical to auto invoice) ──────────────────────────────────
    header = Table([
        [Paragraph(f"<b>{settings.get('company_name','')}</b>", title_left),
         Paragraph("<b>FACTURE</b>", title_right)]
    ], colWidths=[12.5*cm, 5*cm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor(accent)),
        ("TEXTCOLOR",  (0,0), (-1,-1), colors.white),
        ("ALIGN",      (0,0), (0,0),   "LEFT"),
        ("ALIGN",      (1,0), (1,0),   "RIGHT"),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING",  (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
    ]))
    elements = [header, Spacer(1, 18)]

    # ── Company info + logo (identical to auto invoice) ────────────────────────────
    company_lines = [settings.get("company_address", "").replace("\n", "<br/>")]
    if settings.get("company_phone"):
        company_lines.append(f"Tel: {settings['company_phone']}")
    if settings.get("company_email"):
        company_lines.append(settings["company_email"])
    logo_cell = ""
    if os.path.exists("static/logo.png"):
        logo_cell = Image("static/logo.png", width=4.5*cm, height=2.4*cm)
    company_table = Table(
        [[Paragraph("<br/>".join([x for x in company_lines if x]), normal), logo_cell]],
        colWidths=[10*cm, 7.5*cm],
    )
    company_table.setStyle(TableStyle([
        ("ALIGN",  (1,0), (1,0),   "RIGHT"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    elements += [company_table, Spacer(1, 34)]

    # ── Billing block (client name + address) — matches auto invoice ─────────────
    _addr_fmt = format_invoice_address(client_addr) if client_addr else ""
    addr_html = _addr_fmt.replace("\n", "<br/>") if _addr_fmt else "-"
    billing = Paragraph(
        f"<b>Facturé à</b><br/>{client_name}<br/>{addr_html}",
        normal,
    )
    meta = Paragraph(
        f"<b>Facture n°</b>&nbsp;&nbsp;&nbsp; {inv_num}<br/>"
        f"<b>Date</b>&nbsp;&nbsp;&nbsp; {format_date(inv_date)}",
        normal,
    )
    elements += [
        Table([[billing, meta]], colWidths=[10*cm, 7.5*cm],
              style=[("ALIGN",(1,0),(1,0),"RIGHT"),("VALIGN",(0,0),(-1,-1),"TOP")]),
        Spacer(1, 28),
    ]

    # ── Line items table — 2 cols (DÉSIGNATION | MONTANT) — matches auto invoice 1:1
    try:
        items = json.loads(draft.get("items_json") or "[]")
    except Exception:
        items = []

    # ── ONE ROW PER ITEM (correct layout for multi-item manual invoices)
    # Each item keeps its own designation + its own amount; totals are summed
    # at the bottom. Auto-converted invoices with a single multi-line item
    # still render as a single row — same visual result as before.
    table_data = [
        [Paragraph("<b>DÉSIGNATION</b>", normal),
         Paragraph("<b>MONTANT</b>",     normal)],
    ]
    total_ht = 0.0
    total_vat = 0.0
    vat_rates_seen = set()
    for item in items:
        amt = float(item.get("amount") or 0)
        vr_pct = float(item.get("vat_rate") or 0)
        vr = vr_pct / 100.0
        total_ht  += amt
        total_vat += amt * vr
        if abs(vr_pct) > 0.0001:
            vat_rates_seen.add(round(vr_pct, 2))
        desig_html = (item.get("designation") or "").replace("\n", "<br/>") or "-"
        table_data.append([
            Paragraph(desig_html, normal),
            Paragraph(f"{amt:.2f}", normal),
        ])
    if not items:   # safety: never produce an item-less table
        table_data.append([Paragraph("-", normal), Paragraph("0.00", normal)])
    total_ttc = total_ht + total_vat
    if len(vat_rates_seen) == 1:
        vat_label = f"TVA {next(iter(vat_rates_seen)):.1f}%"
    else:
        vat_label = "TVA"

    n_header   = 1
    n_items    = max(1, len(items))
    n_body_end = n_header + n_items - 1     # last item row index
    n_thtt     = n_body_end + 1
    n_tvat     = n_body_end + 2
    n_total    = n_body_end + 3

    table_data += [
        [Paragraph("Total HT", normal), Paragraph(f"{total_ht:.2f}", normal)],
        [Paragraph(vat_label, normal), Paragraph(f"{total_vat:.2f}", normal)],
        [Paragraph("<b>TOTAL TTC</b>", styles["Heading2"]),
         Paragraph(f"<b>{total_ttc:.2f} €</b>", styles["Heading2"])],
    ]
    invoice_table = Table(table_data, colWidths=[12.8*cm, 4.7*cm])
    _style = [
        ("GRID",        (0, 0),         (-1, -1),         0.5, colors.grey),
        ("BACKGROUND",  (0, 0),         (-1, 0),          colors.whitesmoke),
        ("ALIGN",       (1, 1),         (1, -1),          "RIGHT"),
        ("VALIGN",      (0, 0),         (-1, -1),         "TOP"),
        # Totals labels sit in the left column, aligned next to the amount block.
        ("ALIGN",       (0, n_thtt),    (0, n_total),     "RIGHT"),
        # Match the auto invoice model: only the final amount cell is shaded.
        ("BACKGROUND",  (1, n_total),   (1, n_total),     colors.whitesmoke),
    ]
    # Single-item case (auto-converted or one-line manual): keep the body row
    # at the same 4.2cm minimum height as the auto invoice so the layout
    # matches the existing paper look. Multi-item invoices let each row
    # size to content to avoid huge empty boxes per row.
    if n_items == 1:
        _style.append(("MINROWHEIGHT", (0, 1), (-1, 1), 4.2*cm))
    invoice_table.setStyle(TableStyle(_style))
    items_tbl = invoice_table
    elements += [items_tbl, Spacer(1, 90)]

    # ── Payment terms (identical to auto invoice) ────────────────────────
    elements += [
        Paragraph("<b>Conditions et modalités de paiement</b>", normal),
        Paragraph(_invoice_payment_terms_html(settings, draft.get("payment_terms")), normal),
    ]
    doc.build(elements)
    buffer.seek(0)
    return buffer


def _unpaid_invoices_for_client(conn, client_name):
    """Return list of unpaid invoice_records dicts for a client, newest first."""
    c = conn.cursor()
    rows = c.execute(
        "SELECT invoice_number, client_name, date_from, date_to, invoice_date, "
        "amount, vat_amount, total, paid, paid_date, COALESCE(sent,0), "
        "COALESCE(sent_date,''), COALESCE(source,'auto') "
        "FROM invoice_records "
        "WHERE client_name = ? AND COALESCE(deleted,0)=0 AND COALESCE(paid,0)=0 "
        "ORDER BY invoice_date DESC, invoice_number DESC",
        (client_name,),
    ).fetchall()
    records = [invoice_record_to_dict(r) for r in rows]
    records.sort(
        key=lambda r: (r.get("invoice_date") or "", invoice_number_sort_key(r.get("invoice_number"))),
        reverse=True,
    )
    return records


def _reminder_address_block(record):
    """Best-effort client address block: prefer manual_invoice_drafts.client_address,
    fall back to client_invoice_profiles.custom_address, else the bare client name."""
    return record.get("_client_address_html") or (record.get("client") or "-")


def build_reminder_pdf(records, settings, language="fr"):
    """Render a payment-reminder PDF covering one or more unpaid invoices.

    Args:
      records  – list of invoice_record dicts (must all be the SAME client)
      settings – invoice_settings row
      language – ISO short ('fr'|'en'|'bos'|'de'|'pt') to pick translated body

    Returns a BytesIO buffer.
    """
    if not records:
        return None
    client = records[0]["client"] or "-"
    address_html = format_invoice_address(records[0].get("_client_address_html") or "")
    today = lux_now().strftime("%Y-%m-%d")

    buffer = io.BytesIO()
    doc = pdf_doc(buffer, f"RAPPEL {client[:40]} {today}",
                  pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm,
                  topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    template_colors = {"orange": "#ff7a2f", "blue": "#1f4f82", "green": "#2f7d32"}
    accent = template_colors.get(settings.get("invoice_template", "orange"), "#ff7a2f")
    normal = styles["Normal"]

    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    title_left = ParagraphStyle(name="ReminderTitleLeft", parent=styles["Title"],
                                alignment=TA_LEFT, textColor=colors.white)
    title_right = ParagraphStyle(name="ReminderTitleRight", parent=styles["Title"],
                                 alignment=TA_RIGHT, textColor=colors.white)

    # ── Localised strings ─────────────────────────────────────────────────
    L = REMINDER_PDF_STRINGS.get(language, REMINDER_PDF_STRINGS["fr"])

    # ── Header bar ────────────────────────────────────────────────────────
    header = Table([[
        Paragraph(f"<b>{settings.get('company_name','')}</b>", title_left),
        Paragraph(f"<b>{L['doc_title']}</b>", title_right),
    ]], colWidths=[12.5*cm, 5*cm])
    header.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,-1), colors.HexColor(accent)),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING",  (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
    ]))
    elements = [header, Spacer(1, 18)]

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
        ("ALIGN",  (1,0), (1,0),   "RIGHT"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    elements += [co_tbl, Spacer(1, 30)]

    # ── Client + date ─────────────────────────────────────────────────────
    addr_html = (address_html or "-").replace("\n", "<br/>")
    billing = Paragraph(
        f"<b>{L['to']}</b><br/>{client}<br/>{addr_html}", normal
    )
    meta = Paragraph(
        f"<b>{L['date_label']}</b>&nbsp;&nbsp;&nbsp; {format_date(today)}", normal
    )
    elements += [
        Table([[billing, meta]], colWidths=[10*cm, 7.5*cm],
              style=[("ALIGN",(1,0),(1,0),"RIGHT"), ("VALIGN",(0,0),(-1,-1),"TOP")]),
        Spacer(1, 22),
    ]

    # ── Greeting + body intro ─────────────────────────────────────────────
    elements += [
        Paragraph(L["greeting"], normal),
        Spacer(1, 8),
        Paragraph(L["body_intro"], normal),
        Spacer(1, 14),
    ]

    # ── Table of unpaid invoices ──────────────────────────────────────────
    table_data = [[
        Paragraph(f"<b>{L['col_number']}</b>", normal),
        Paragraph(f"<b>{L['col_date']}</b>",   normal),
        Paragraph(f"<b>{L['col_amount']}</b>", normal),
    ]]
    grand_total = 0.0
    for r in records:
        amt = float(r.get("total") or 0)
        grand_total += amt
        table_data.append([
            Paragraph(str(r.get("invoice_number","")), normal),
            Paragraph(format_date(r.get("invoice_date","")), normal),
            Paragraph(f"{amt:.2f} €", normal),
        ])
    table_data.append([
        Paragraph(f"<b>{L['total_due']}</b>", styles["Heading3"]),
        "",
        Paragraph(f"<b>{grand_total:.2f} €</b>", styles["Heading3"]),
    ])
    n_total_row = len(table_data) - 1
    invoice_tbl = Table(table_data, colWidths=[5*cm, 5*cm, 7.5*cm])
    invoice_tbl.setStyle(TableStyle([
        ("GRID",        (0,0), (-1,-1),         0.5, colors.grey),
        ("BACKGROUND",  (0,0), (-1,0),          colors.whitesmoke),
        ("ALIGN",       (2,1), (2,-1),          "RIGHT"),
        ("VALIGN",      (0,0), (-1,-1),         "TOP"),
        ("SPAN",        (0, n_total_row), (1, n_total_row)),
        ("ALIGN",       (0, n_total_row), (0, n_total_row), "LEFT"),
        ("BACKGROUND",  (0, n_total_row), (-1, n_total_row), colors.whitesmoke),
        ("FONTNAME",    (0, n_total_row), (-1, n_total_row), "Helvetica-Bold"),
    ]))
    elements += [invoice_tbl, Spacer(1, 22)]

    # ── Closing paragraphs ────────────────────────────────────────────────
    elements += [
        Paragraph(L["body_pay_request"], normal),
        Spacer(1, 8),
        Paragraph(L["body_already_paid"], normal),
        Spacer(1, 14),
        Paragraph(L["closing"], normal),
        Spacer(1, 4),
        Paragraph(f"<b>{settings.get('company_name','') or '-'}</b>", normal),
        Spacer(1, 28),
    ]

    # ── Payment instructions block (bank info from settings) ──────────────
    elements += [
        Paragraph(f"<b>{L['payment_block_title']}</b>", normal),
        Paragraph(_invoice_payment_terms_html(settings), normal),
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
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            contract_signed_at TEXT DEFAULT '',
            contract_from TEXT DEFAULT '',
            contract_to TEXT DEFAULT '',
            notes TEXT DEFAULT ''
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
            date_from TEXT DEFAULT '',
            date_to TEXT DEFAULT '',
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
            sort_order INTEGER DEFAULT 0,
            auto_saved INTEGER DEFAULT 0,
            archived INTEGER DEFAULT 0,
            last_used_at TEXT DEFAULT ''
        )
    """)
    # Persistent extra VAT rates that admin adds via the manual-invoice
    # form (when official tax rates change).
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoice_custom_vat_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rate REAL NOT NULL,
            created_at TEXT DEFAULT ''
        )
    """)
    # ── Email: templates / queue / logs ────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoice_email_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            body TEXT DEFAULT '',
            language TEXT DEFAULT 'fr',
            is_default INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoice_email_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT,
            recipient TEXT,
            cc TEXT DEFAULT '',
            bcc TEXT DEFAULT '',
            subject TEXT,
            body TEXT,
            scheduled_at TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            error TEXT DEFAULT '',
            sent_at TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            claimed_at TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoice_email_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT,
            recipient TEXT,
            subject TEXT,
            status TEXT,
            error TEXT DEFAULT '',
            sent_at TEXT DEFAULT '',
            message_id TEXT DEFAULT '',
            attachment_sha256 TEXT DEFAULT '',
            imap_saved INTEGER DEFAULT 0,
            imap_error TEXT DEFAULT ''
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
    # Extended contact + contract fields (phone, email, contract dates).
    # email lives here as the source of truth; client_invoice_profiles.email
    # is still populated for invoice email sending via write-through so the
    # email-sending code path doesn't have to change.
    for _col, _ddl in [
        ("phone",              "TEXT DEFAULT ''"),
        ("email",              "TEXT DEFAULT ''"),
        ("contract_signed_at", "TEXT DEFAULT ''"),
        ("contract_from",      "TEXT DEFAULT ''"),
        ("contract_to",        "TEXT DEFAULT ''"),
        ("notes",              "TEXT DEFAULT ''"),
    ]:
        if _col not in client_cols:
            c.execute(f"ALTER TABLE clients ADD COLUMN {_col} {_ddl}")
    # One-time cleanup: prior to 415c8ed, delete_client() removed the
    # clients row but left client_invoice_profiles in place. Any row in
    # client_invoice_profiles whose client_name no longer exists in
    # clients is an orphan from that era — sweep them out so a future
    # admin re-adding the same client name doesn't silently inherit
    # the old rate / custom_address / client_type. Idempotent.
    try:
        # NOT EXISTS instead of NOT IN: NULL-safe (a stray
        # clients.name=NULL would turn NOT IN into UNKNOWN and skip
        # every row), and the planner picks the same join path on
        # both SQLite and PostgreSQL.
        c.execute(
            "DELETE FROM client_invoice_profiles "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM clients "
            "  WHERE clients.name = client_invoice_profiles.client_name"
            ")"
        )
    except Exception as _orphan_err:
        app.logger.warning("client_invoice_profiles orphan cleanup failed: %s",
                           _orphan_err)
        # PostgreSQL leaves the transaction in an aborted state after
        # any SQL error; subsequent migrations in this init_db() would
        # then fail on "current transaction is aborted". Roll back so
        # the rest of init_db() can proceed cleanly.
        try:
            conn.rollback()
        except Exception:
            pass
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
    # Migrate invoice_email_queue: add claimed_at if missing
    try:
        queue_cols = [r[1] for r in c.execute("PRAGMA table_info(invoice_email_queue)").fetchall()]
        if "claimed_at" not in queue_cols:
            c.execute("ALTER TABLE invoice_email_queue ADD COLUMN claimed_at TEXT DEFAULT ''")
    except Exception:
        pass
    # Migrate invoice_email_logs: add proof/archive columns (Message-ID,
    # attachment hash, IMAP-archive flag/error) for the email-proof feature.
    try:
        log_cols = [r[1] for r in c.execute("PRAGMA table_info(invoice_email_logs)").fetchall()]
        if "message_id" not in log_cols:
            c.execute("ALTER TABLE invoice_email_logs ADD COLUMN message_id TEXT DEFAULT ''")
        if "attachment_sha256" not in log_cols:
            c.execute("ALTER TABLE invoice_email_logs ADD COLUMN attachment_sha256 TEXT DEFAULT ''")
        if "imap_saved" not in log_cols:
            c.execute("ALTER TABLE invoice_email_logs ADD COLUMN imap_saved INTEGER DEFAULT 0")
        if "imap_error" not in log_cols:
            c.execute("ALTER TABLE invoice_email_logs ADD COLUMN imap_error TEXT DEFAULT ''")
    except Exception:
        pass
    # Migrate manual_invoice_drafts: add date_from/date_to so manual
    # invoices can carry a real service window separate from the
    # invoice_date. Without this, manual invoices were collapsing
    # date_from = date_to = invoice_date, which silently put them in
    # the wrong /diagram and report bucket whenever they were issued
    # in a month different from the month of work.
    try:
        mid_cols = [r[1] for r in c.execute("PRAGMA table_info(manual_invoice_drafts)").fetchall()]
        if "date_from" not in mid_cols:
            c.execute("ALTER TABLE manual_invoice_drafts ADD COLUMN date_from TEXT DEFAULT ''")
        if "date_to" not in mid_cols:
            c.execute("ALTER TABLE manual_invoice_drafts ADD COLUMN date_to TEXT DEFAULT ''")
    except Exception:
        pass
    # Migrate manual_item_templates: add auto_saved / archived / last_used_at
    try:
        tpl_cols = [r[1] for r in c.execute("PRAGMA table_info(manual_item_templates)").fetchall()]
        if "auto_saved" not in tpl_cols:
            c.execute("ALTER TABLE manual_item_templates ADD COLUMN auto_saved INTEGER DEFAULT 0")
        if "archived" not in tpl_cols:
            c.execute("ALTER TABLE manual_item_templates ADD COLUMN archived INTEGER DEFAULT 0")
        if "last_used_at" not in tpl_cols:
            c.execute("ALTER TABLE manual_item_templates ADD COLUMN last_used_at TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("DROP INDEX IF EXISTS idx_invoice_client_period")
        c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_client_period
            ON invoice_records(client_name, date_from, date_to)
            WHERE COALESCE(deleted, 0) = 0 AND COALESCE(source, 'auto') = 'auto'
        """)
    except Exception as _idx_err:
        app.logger.warning("idx_invoice_client_period not created: %s", _idx_err)
    try:
        c.execute("""
            DELETE FROM manual_invoice_drafts
            WHERE invoice_number IN (
                SELECT invoice_number FROM invoice_records WHERE COALESCE(deleted, 0) = 1
            )
        """)
        c.execute("DELETE FROM invoice_records WHERE COALESCE(deleted, 0) = 1")
    except Exception as _purge_err:
        app.logger.warning("soft-deleted invoice cleanup failed: %s", _purge_err)
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
    c.execute("INSERT OR IGNORE INTO invoice_settings (id, invoice_text, payment_terms, bank_account, company_name, company_address, company_phone, company_email, company_vat, invoice_template, invoice_start_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (1, "", "Paiement \u00e0 15 jours d\u00e8s r\u00e9ception de la facture.\nPost Luxembourg BIC (CCPLLULL) LU60 1111 7815 3607 0000\nLors du virement, veuillez indiquer r\u00e9f\u00e9rence suivante: ***Facture n\u00b0***", "", "Luxmann Services", "32, rue Aneschbach\nWiltz L-9511", "+352691642003", "lux@mann.lu", "TVA: LU33673043", "orange", 1))

    for worker_name, color in DEFAULT_WORKER_COLORS.items():
        c.execute("INSERT OR IGNORE INTO worker_colors (worker_name, color) VALUES (?, ?)", (worker_name, color))

    # ── Seed default invoice email templates (idempotent) ──────────────────
    _now = lux_now().strftime("%Y-%m-%d %H:%M:%S") if 'lux_now' in globals() else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _existing_tpls = c.execute("SELECT language FROM invoice_email_templates WHERE is_default=1").fetchall()
    _have_langs = {r[0] for r in _existing_tpls}
    for lang, subject, body in DEFAULT_EMAIL_TEMPLATES:
        if lang in _have_langs:
            c.execute("""
                UPDATE invoice_email_templates
                SET subject=?, body=?, updated_at=?
                WHERE language=? AND is_default=1
            """, (subject, body, _now, lang))
            continue
        c.execute("""
            INSERT INTO invoice_email_templates (name, subject, body, language, is_default, updated_at)
            VALUES (?, ?, ?, ?, 1, ?)
        """, (f"Default {lang.upper()}", subject, body, lang, _now))

    conn.commit()
    conn.close()


init_db()


BASE_STYLE = """
<title>Luxmann Planner</title>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Luxmann">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#1f4f82">
<!-- Favicon + Apple touch icon. v=2 cache-bust so the browser drops
     the old generic icon when the user reloads the deployed app. -->
<link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}?v=3">
<link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon-32x32.png') }}?v=3">
<link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon-16x16.png') }}?v=3">
<link rel="apple-touch-icon" sizes="180x180" href="{{ url_for('static', filename='apple-touch-icon.png') }}?v=3">
<link rel="manifest" href="/manifest.json">
<script>if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(function(){});}</script>
<style>
    html { -webkit-text-size-adjust:100%; text-size-adjust:100%; }
    body { font-family: Arial, sans-serif; margin:24px; background: {{ '#111113' if dark else '#f4f6f8' }}; color: {{ '#e5e7eb' if dark else '#1f2937' }}; touch-action:pan-y; overflow-x:hidden; }
    /* Visually hidden but still announced by screen readers — used for
       form <label> tags that the design hides but accessibility tools
       still need. Standard "sr-only" pattern. */
    .sr-only { position:absolute !important; width:1px !important; height:1px !important; padding:0 !important; margin:-1px !important; overflow:hidden !important; clip:rect(0,0,0,0) !important; white-space:nowrap !important; border:0 !important; }
    /* /invoices/view mismatch banner — collapse the "Voir differences"
       side-by-side diff into a single column on narrow viewports so
       the <pre> boxes don't get squeezed to unreadable widths. */
    @media (max-width:720px) {
        .invmm-diff-grid { grid-template-columns:1fr !important; }
    }
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
    .mini-shift { margin-top:6px; padding:6px; border-radius:8px; font-size:12px; background: {{ '#1e1e20' if dark else '#f8fafc' }}; position:relative; }
    /* Default: actions render inline via display:contents on
       non-month surfaces; ⋯ toggle hidden. The month-grid override
       at @media (min-width:601px) below switches the wrapper to
       inline-flex so the three icons sit side-by-side without the
       global button { width:100% } stretching the delete button. */
    .mini-actions { display:contents; }
    .mini-actions-toggle { display:none; }

    /* /month desktop + tablet horizontal action row: square 34x32
       icon buttons, side-by-side, with light-theme chip colors so
       the icons read clearly against the tinted day tile. Mobile
       (≤600px) keeps the existing ⋯ popover — this block is
       behind a min-width:601px gate. */
    @media (min-width:601px) {
        .month-grid .mini-actions {
            display:inline-flex; flex-direction:row; flex-wrap:nowrap;
            align-items:center; gap:4px;
            margin-top:4px; max-width:100%;
        }
        .month-grid .mini-actions .mini-link {
            display:inline-flex !important;
            flex:0 0 34px; width:34px !important; min-width:34px !important;
            height:32px;  min-height:32px !important;
            padding:0 !important; margin:0 !important;
            align-items:center; justify-content:center;
            font-size:16px; line-height:1;
            border:1px solid transparent; border-radius:7px;
            box-sizing:border-box;
        }
        .month-grid .mini-actions .inline-delete-form {
            display:inline-flex;
            flex:0 0 34px; width:34px; min-width:34px;
            margin:0; padding:0;
        }
        .month-grid .mini-actions .inline-delete-form .mini-link {
            width:34px !important;
        }
        {% if not dark %}
        .month-grid .mini-actions .edit-link {
            background:#dbeafe !important; color:#1d4ed8 !important;
            border-color:#93c5fd !important;
        }
        .month-grid .mini-actions .delete-link {
            background:#fee2e2 !important; color:#dc2626 !important;
            border-color:#fca5a5 !important;
        }
        .month-grid .mini-actions .copy-link {
            background:#dcfce7 !important; color:#15803d !important;
            border-color:#86efac !important;
        }
        {% else %}
        .month-grid .mini-actions .edit-link   { background:rgba(59,130,246,.18) !important; border-color:rgba(59,130,246,.35) !important; }
        .month-grid .mini-actions .delete-link { background:rgba(239,68,68,.18)  !important; border-color:rgba(239,68,68,.35)  !important; }
        .month-grid .mini-actions .copy-link   { background:rgba(34,197,94,.18)  !important; border-color:rgba(34,197,94,.35)  !important; }
        {% endif %}
    }

    /* Tablet 601–900px: a 7-column month grid leaves each day tile
       around ~100px wide. The desktop rule above renders three fixed
       34px icons + 2×4px gap = 110px, which spills out of the card.
       Re-grid the action row to 3 fluid columns so the icons shrink
       to fit; ≥901px keeps the fixed 34×32 layout. */
    @media (min-width:601px) and (max-width:900px) {
        .month-grid .mini-actions {
            display:grid; grid-template-columns:repeat(3, minmax(0,1fr));
            width:100%; gap:3px;
        }
        .month-grid .mini-actions .mini-link,
        .month-grid .mini-actions .inline-delete-form {
            width:auto !important; min-width:0 !important; flex:none;
        }
        .month-grid .mini-actions .inline-delete-form .mini-link {
            width:100% !important;
        }
    }
    /* Dashboard /Plan shift card actions: icon row always sits on its
       own line BELOW the client line, so long names/cities can't push
       the actions onto an awkward second row mid-block. Used by both
       the main weekly group and the archive accordion. */
    .plan-client-line { display:block; margin-top:4px; overflow-wrap:anywhere; }
    .plan-shift-actions {
        display:flex; flex-direction:row; flex-wrap:nowrap;
        align-items:center; gap:6px; margin-top:10px;
    }
    .plan-shift-actions .psa-btn {
        display:inline-flex !important;
        align-items:center; justify-content:center;
        flex:0 0 40px;
        width:40px !important; min-width:40px !important;
        height:38px;            min-height:38px !important;
        padding:0 !important;   margin:0 !important;
        font-size:18px; line-height:1;
        border:1px solid transparent; border-radius:8px;
        box-sizing:border-box;
    }
    .plan-shift-actions .inline-delete-form {
        display:inline-flex; width:40px; min-width:40px;
        margin:0; padding:0;
    }
    /* Let the form drive the delete button width (40px desktop,
       42px touch) instead of hard-coding it here — otherwise the
       child !important rule would beat the touch @media override
       and the trash button would stay 40px on phones. */
    .plan-shift-actions .inline-delete-form .psa-btn {
        width:100% !important; min-width:100% !important;
    }
    {% if not dark %}
    .plan-shift-actions .edit-link.psa-btn {
        background:#dbeafe !important; color:#1d4ed8 !important;
        border-color:#93c5fd !important;
    }
    .plan-shift-actions .delete-link.psa-btn {
        background:#fee2e2 !important; color:#dc2626 !important;
        border-color:#fca5a5 !important;
    }
    .plan-shift-actions .copy-link.psa-btn {
        background:#dcfce7 !important; color:#15803d !important;
        border-color:#86efac !important;
    }
    {% else %}
    .plan-shift-actions .edit-link.psa-btn   { background:rgba(59,130,246,.18) !important; border-color:rgba(59,130,246,.35) !important; }
    .plan-shift-actions .delete-link.psa-btn { background:rgba(239,68,68,.18)  !important; border-color:rgba(239,68,68,.35)  !important; }
    .plan-shift-actions .copy-link.psa-btn   { background:rgba(34,197,94,.18)  !important; border-color:rgba(34,197,94,.35)  !important; }
    {% endif %}
    /* Touch viewports: lift to 42px square so the targets are
       finger-friendly even when the icons are tucked into a card. */
    @media (max-width:1024px) {
        .plan-shift-actions .psa-btn {
            width:42px !important; min-width:42px !important;
            height:42px;             min-height:42px !important;
        }
        .plan-shift-actions .inline-delete-form { width:42px; min-width:42px; }
    }

    /* Worker-only pin on /week mini-shift tiles. Workers don't see
       the admin action chips, so this sits where those chips would
       otherwise be — a single green 📍 that opens turn-by-turn
       directions to the client's saved address. */
    .week-map-link {
        display:inline-flex; align-items:center; justify-content:center;
        width:32px; height:32px; padding:0; border-radius:8px;
        text-decoration:none; font-size:16px; line-height:1;
        background:{{ 'rgba(34,197,94,.18)' if dark else '#dcfce7' }};
        color:{{ '#86efac' if dark else '#15803d' }};
        border:1px solid {{ 'rgba(34,197,94,.35)' if dark else '#86efac' }};
    }
    @media (max-width:1024px) {
        .week-map-link { width:42px; height:42px; font-size:18px; }
    }

    /* Week-view shift action row: horizontal icon-only buttons with
       guaranteed flex:1 sizing and clear light-theme chip colors so
       the icons aren't washed out against the tinted shift tile. */
    .week-shift-actions .wsa-btn {
        flex:1; min-width:0;
        display:flex !important; align-items:center; justify-content:center;
        padding:6px 4px !important; margin:0 !important;
        height:32px; min-height:32px !important;
        font-size:16px; line-height:1;
        border:1px solid transparent; border-radius:7px;
        box-sizing:border-box;
    }
    /* Touch viewports: 32px is fine for mouse hits but tight for
       fingers. Lift to 40px (matches the .mini-link tablet rule the
       wsa-btn !important previously pre-empted). */
    @media (max-width:1024px) {
        .week-shift-actions .wsa-btn { height:40px; min-height:40px !important; }
    }
    {% if not dark %}
    .week-shift-actions .edit-link.wsa-btn {
        background:#dbeafe !important; color:#1d4ed8 !important;
        border-color:#93c5fd !important;
    }
    .week-shift-actions .delete-link.wsa-btn {
        background:#fee2e2 !important; color:#dc2626 !important;
        border-color:#fca5a5 !important;
    }
    .week-shift-actions .copy-link.wsa-btn {
        background:#dcfce7 !important; color:#15803d !important;
        border-color:#86efac !important;
    }
    {% else %}
    .week-shift-actions .edit-link.wsa-btn   { background:rgba(59,130,246,.18) !important; border-color:rgba(59,130,246,.35) !important; }
    .week-shift-actions .delete-link.wsa-btn { background:rgba(239,68,68,.18)  !important; border-color:rgba(239,68,68,.35)  !important; }
    .week-shift-actions .copy-link.wsa-btn   { background:rgba(34,197,94,.18)  !important; border-color:rgba(34,197,94,.35)  !important; }
    {% endif %}
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
    /* Buttons styled as action/delete/mini links: reset UA chrome so they
       look identical to the surrounding <a> links. Needed because we
       converted destructive GET <a> tags into <form method="post"> +
       <button> to stop prefetch/CSRF from flipping state. */
    button.action-link, button.mini-link, button.delete-link, .inline-delete-form button {
        border:none; cursor:pointer; font-family:inherit; line-height:normal;
    }
    .inline-delete-form { display:inline; margin:0; padding:0; }
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
        .mini-link { padding:7px 11px; min-height:40px; font-size:13px; }
        .day-menu-wrapper button { min-width:34px; min-height:34px; padding:2px 6px !important; }
        /* iPad: date / datetime inputs default to ~28px which is
           painful to tap and shows no visible affordance. Force a
           real touch target and pad the native calendar picker so
           the icon isn't a 12px dot in the corner. */
        input[type="date"],
        input[type="datetime-local"],
        input[type="time"] {
            min-height:44px !important;
            padding:10px 12px !important;
            font-size:15px !important;
        }
        input[type="date"]::-webkit-calendar-picker-indicator,
        input[type="datetime-local"]::-webkit-calendar-picker-indicator,
        input[type="time"]::-webkit-calendar-picker-indicator {
            padding:6px; cursor:pointer; opacity:0.7;
        }
        /* Same for select controls that share the row (VAT rate,
           status, worker/client dropdowns) — 44px keeps them
           tap-friendly alongside the date fields. */
        select { min-height:44px !important; }
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
        /* Mobile month grid: three icons can't fit in a ~50px-wide day
           tile. Hide the inline action group by default and show a
           single ⋯ toggle that opens a small popover with all three
           actions side by side (column-aware alignment below). */
        .month-grid .mini-actions:not(.open) { display:none !important; }
        .month-grid .mini-actions-toggle {
            display:inline-flex !important;
            align-items:center; justify-content:center;
            background:rgba(127,127,127,.18); border:none;
            color:inherit; border-radius:6px;
            /* Pin against the global mobile button { padding:13px } rule
               so the ⋯ stays compact in the day tile. */
            width:30px !important; min-width:30px !important;
            height:28px;            min-height:28px !important;
            padding:0 !important;   margin-top:4px;
            font-size:14px; line-height:1; cursor:pointer;
            font-family:inherit;
        }
        /* When a popover is open, undo overflow:hidden on the two
           ancestor containers so the absolute-positioned menu isn't
           clipped at the day-tile edge. JS adds .actions-open to
           both .mini-shift and .calendar-day-card on toggle. */
        .month-grid .mini-shift.actions-open,
        .month-grid .calendar-day-card.actions-open {
            overflow:visible !important;
            position:relative;
            z-index:60;
        }
        .month-grid .mini-actions.open {
            display:flex !important; flex-direction:row;
            align-items:center; gap:5px;
            /* Default: open toward the left (right:0) so the menu
               doesn't push off the right edge. nth-child overrides
               below flip the first three weekday columns to open
               toward the right (left:0) so they don't fall off the
               LEFT edge of a 360px phone — the popover is ~146px
               wide and a day tile is ~50px, so it would otherwise
               overflow whichever screen edge it's pinned to. */
            position:absolute; right:0; left:auto; top:100%;
            min-width:auto; width:max-content; padding:5px;
            background:{{ '#1d1d1f' if dark else '#ffffff' }};
            border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};
            border-radius:10px;
            box-shadow:0 10px 24px rgba(0,0,0,.35);
            z-index:60;
        }
        .month-grid .calendar-day-card:nth-child(7n+1) .mini-actions.open,
        .month-grid .calendar-day-card:nth-child(7n+2) .mini-actions.open,
        .month-grid .calendar-day-card:nth-child(7n+3) .mini-actions.open {
            left:0; right:auto;
        }
        .month-grid .calendar-day-card:nth-child(7n+4) .mini-actions.open,
        .month-grid .calendar-day-card:nth-child(7n+5) .mini-actions.open,
        .month-grid .calendar-day-card:nth-child(7n+6) .mini-actions.open,
        .month-grid .calendar-day-card:nth-child(7n+7) .mini-actions.open {
            left:auto; right:0;
        }
        /* Square equal-sized action buttons, side-by-side. */
        .month-grid .mini-actions.open .mini-link {
            display:flex !important;
            justify-content:center !important;
            align-items:center !important;
            width:42px  !important; min-width:42px !important;
            height:42px;             min-height:42px !important;
            padding:0   !important;  margin:0       !important;
            font-size:18px !important; line-height:1;
            border:1px solid transparent; border-radius:8px;
            box-sizing:border-box;
        }
        /* Light-theme distinct colors for each action so the icon
           kind is obvious at a glance. Dark theme keeps the
           translucent neutral background it already had. */
        {% if not dark %}
        .month-grid .mini-actions.open .edit-link {
            background:#dbeafe !important; color:#1d4ed8 !important;
            border-color:#93c5fd !important;
        }
        .month-grid .mini-actions.open .delete-link {
            background:#fee2e2 !important; color:#dc2626 !important;
            border-color:#fca5a5 !important;
        }
        .month-grid .mini-actions.open .copy-link {
            background:#dcfce7 !important; color:#15803d !important;
            border-color:#86efac !important;
        }
        {% endif %}
        .month-grid .mini-actions.open .inline-delete-form {
            display:block; width:auto; margin:0;
        }
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
        position:relative;
    }
    /* Navigation pin pinned to the top-right of the worker week card
       so it stays in a consistent reachable spot regardless of how
       many lines the client/worker text wraps to. */
    .wapp-week-map { position:absolute; top:12px; right:12px; }
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
          <a href="javascript:void(0)" onclick="closeWorkerMenu();openWorkerHoursSheet();"><span>⏱</span><div>{{ tr.get("worker_hours_pdf","Moji sati PDF") }}<small>{{ tr.get("worker_hours_pdf_hint","Preuzmi izvještaj po periodu") }}</small></div></a>
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
    <div class="wapp-leave-sheet" id="wappHoursSheet" onclick="if(event.target===this)closeWorkerHoursSheet();">
      <div class="wapp-leave-inner">
        <button class="wapp-leave-close" type="button" onclick="closeWorkerHoursSheet()">×</button>
        <h3>⏱ {{ tr.get("worker_hours_pdf","Moji sati PDF") }}</h3>
        <form method="get" action="/worker/hours_pdf" target="_blank" rel="noopener"
              onsubmit="closeWorkerHoursSheet();">
          <label for="whFrom">{{ tr.get("date_from","Datum od") }}</label>
          <input type="date" id="whFrom" name="date_from" required>
          <label for="whTo">{{ tr.get("date_to","Datum do") }}</label>
          <input type="date" id="whTo" name="date_to" required>
          <button type="submit">📄 {{ tr.get("download","Preuzmi") }} PDF</button>
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
    function openWorkerHoursSheet(){
      var sheet = document.getElementById('wappHoursSheet');
      if(!sheet) return;
      // Default the range to "current month, 1st → today" on every
      // open so the sheet is one-tap-to-submit for the common case.
      // pad2 keeps the strings in ISO YYYY-MM-DD.
      function pad2(n){ return (n<10?'0':'') + n; }
      var now = new Date();
      var y = now.getFullYear(), m = pad2(now.getMonth() + 1), d = pad2(now.getDate());
      var from = document.getElementById('whFrom');
      var to   = document.getElementById('whTo');
      if(from) from.value = y + '-' + m + '-01';
      if(to)   to.value   = y + '-' + m + '-' + d;
      sheet.classList.add('open');
    }
    function closeWorkerHoursSheet(){
      var sheet = document.getElementById('wappHoursSheet');
      if(sheet) sheet.classList.remove('open');
    }
    document.addEventListener('click', function(ev){
      var wrap = document.querySelector('.wapp-menu-wrap');
      if(wrap && !wrap.contains(ev.target)) closeWorkerMenu();
    });
    document.addEventListener('keydown', function(ev){
      if(ev.key === 'Escape'){ closeWorkerMenu(); closeWorkerLeaveSheet(); closeWorkerHoursSheet(); }
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
            # "Remember me" → permanent session (cookie picks up the
            # PERMANENT_SESSION_LIFETIME=30d). Without the checkbox
            # Flask falls back to a browser-session cookie. Failed
            # logins NEVER reach this branch, so a bad password
            # can't accidentally pin a permanent session.
            session.permanent = (request.form.get("remember") == "1")
            return redirect("/")
        conn.close()
        error = tr["login_error"]

    return render_template_string(BASE_STYLE + """
    <style>
        .login-card { max-width:420px; margin:auto; text-align:center; padding:30px; }
        .login-card h2 { margin:0 0 14px; }
        .login-field { margin:0 0 10px; text-align:left; }
        .login-field input { width:100%; box-sizing:border-box; }
        .login-remember {
            display:flex; align-items:center; gap:8px;
            margin:6px 0 14px; font-size:13px; text-align:left;
            color:{{ '#94a3b8' if dark else '#64748b' }};
        }
        .login-remember input { width:auto; margin:0; cursor:pointer; }
    </style>
    <div class="langbar" style="max-width:420px; margin:0 auto 12px auto; text-align:right;">
        <a href="/set_lang/fr">FR</a><a href="/set_lang/en">EN</a><a href="/set_lang/bos">BOS</a><a href="/set_lang/de">DE</a><a href="/set_lang/pt">PT</a>
    </div>
    <div class="card login-card">
        <img src="{{ url_for('static', filename='logo.png') }}" alt="Luxmann Logo" style="height:70px; margin-bottom:12px;">
        <h2>{{ tr["login_title"] }}</h2>
        <form method="post" autocomplete="on">
            <div class="login-field">
                <label class="sr-only" for="username">{{ tr['username'] }}</label>
                <input id="username" name="username" type="text"
                       autocomplete="username" autocapitalize="none"
                       autocorrect="off" spellcheck="false"
                       placeholder="{{ tr['username'] }}" required>
            </div>
            <div class="login-field">
                <label class="sr-only" for="password">{{ tr['password'] }}</label>
                <input id="password" name="password" type="password"
                       autocomplete="current-password"
                       placeholder="{{ tr['password'] }}" required>
            </div>
            <label class="login-remember">
                <input type="checkbox" name="remember" value="1">
                {{ tr.get("remember_me","Remember me on this device") }}
            </label>
            <button type="submit">{{ tr["login_btn"] }}</button>
        </form>
        {% if error %}<div style="color:#ef4444; margin-top:10px;">{{ error }}</div>{% endif %}
    </div>
    """, tr=tr, error=error, dark=dark)


@app.route("/logout")
def logout():
    # Wipe everything — both the session dict and the permanent flag —
    # so a "Remember me" cookie can't outlive an explicit logout.
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
    search_date_from = request.args.get("search_date_from", "").strip()
    search_date_to = request.args.get("search_date_to", "").strip()
    selected_date = request.args.get("selected_date", "").strip()
    worker_filter = request.args.get("worker", "").strip() if is_admin else current_user
    client_filter = request.args.get("client", "").strip()
    search_query = request.args.get("q", "").strip().lower()

    base_query = "SELECT * FROM shifts WHERE 1=1"
    params = []
    if date_filter:
        base_query += " AND date = ?"
        params.append(date_filter)
    if search_date_from:
        base_query += " AND date >= ?"
        params.append(search_date_from)
    if search_date_to:
        base_query += " AND date <= ?"
        params.append(search_date_to)
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
        "search_date_from": search_date_from, "search_date_to": search_date_to,
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
                            {% set _real_addr = client_addresses.get(s[2], '') %}{% if _real_addr %}<a class="wapp-map" href="https://www.google.com/maps/dir/?api=1&destination={{ _real_addr|urlencode }}&travelmode=driving&dir_action=navigate" target="_blank" rel="noopener" title="{{ tr.get('open_in_maps','Open in Google Maps') }}" aria-label="{{ tr.get('open_in_maps','Open in Google Maps') }}">➜</a>{% endif %}
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
                            {% set _real_addr = client_addresses.get(s[2], '') %}{% if _real_addr %}<a class="wapp-map" href="https://www.google.com/maps/dir/?api=1&destination={{ _real_addr|urlencode }}&travelmode=driving&dir_action=navigate" target="_blank" rel="noopener" title="{{ tr.get('open_in_maps','Open in Google Maps') }}" aria-label="{{ tr.get('open_in_maps','Open in Google Maps') }}">➜</a>{% endif %}
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
                      {% set _real_addr = client_addresses.get(s[2], '') %}{% if _real_addr %}<a class="wapp-map" href="https://www.google.com/maps/dir/?api=1&destination={{ _real_addr|urlencode }}&travelmode=driving&dir_action=navigate" target="_blank" rel="noopener" title="{{ tr.get('open_in_maps','Open in Google Maps') }}" aria-label="{{ tr.get('open_in_maps','Open in Google Maps') }}">➜</a>{% endif %}
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
                <label for="addShiftDateDash">{{ tr["date"] }}</label>
                <input id="addShiftDateDash" name="date" type="date" value="{{ selected_date }}" required>
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
            <form method="get">
                <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end;">
                    <div><label style="font-size:12px;display:block;margin-bottom:3px;">{{ tr["date_from"] }}</label><input type="date" name="search_date_from" value="{{ search_date_from }}" style="margin:0;"></div>
                    <div><label style="font-size:12px;display:block;margin-bottom:3px;">{{ tr["date_to"] }}</label><input type="date" name="search_date_to" value="{{ search_date_to }}" style="margin:0;"></div>
                    <div><label style="font-size:12px;display:block;margin-bottom:3px;">{{ tr["choose_worker"] }}</label><select name="worker" style="margin:0;"><option value="">{{ tr["all_workers"] }}</option>{% for w in workers %}<option value="{{ w[0] }}" {% if worker_filter == w[0] %}selected{% endif %}>{{ w[0] }}</option>{% endfor %}</select></div>
                    <div class="client-search-wrapper"><input type="text" id="csInputFilt" class="client-search-input" value="{{ client_filter }}" placeholder="{{ tr['all_clients'] }}" autocomplete="off" style="width:160px;"><input type="hidden" name="client" id="csHiddenFilt" value="{{ client_filter }}"><div class="client-search-dropdown" id="csListFilt"></div></div>
                    <div><input name="q" value="{{ request.args.get('q', '') }}" placeholder="{{ tr['search_placeholder'] }}" style="margin:0;"></div>
                    <button>{{ tr["filter_btn"] }}</button>
                </div>
            </form>
            <a class="reset-link" href="/">{{ tr["reset"] }}</a>
            {% if search_date_from or search_date_to or client_filter or request.args.get('q') or (is_admin and worker_filter) %}
            <div style="margin-top:12px;">
                <a href="/shifts_search_pdf?search_date_from={{ search_date_from|urlencode }}&search_date_to={{ search_date_to|urlencode }}&worker={{ worker_filter|urlencode }}&client={{ client_filter|urlencode }}&q={{ request.args.get('q','')|urlencode }}" target="_blank" style="display:inline-flex;align-items:center;gap:6px;padding:8px 16px;background:#1f4f82;color:white;border-radius:8px;font-weight:700;text-decoration:none;">
                    📄 {{ tr.get("pdf","PDF") }} — {{ tr["search_shifts"] }}
                </a>
            </div>
            {% endif %}
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
            {% for a in absences[:8] %}<div class="user-row"><b>{{ a[1] }}</b> - {{ tr.get(a[2], a[2]) }}<br><small>{{ format_date(a[3]) }} - {{ format_date(a[4]) }} {{ a[5] }}</small><form class="inline-delete-form" method="post" action="/delete_absence/{{ a[0] }}" onsubmit='return confirm({{ tr.get("absence_delete_confirm","Delete this absence?")|tojson }});'><button type="submit" class="delete-link">{{ tr["delete"] }}</button></form></div>{% endfor %}
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
            {% for s in week_shifts %}{% set auto_status = get_auto_status(s[3], s[4]) %}<div class="shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="border-left:6px solid {{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#1f4f82') }}"><b>{{ format_date(s[3]) }}</b> | {{ s[4] }}<span class="status-badge" style="background:{{ status_colors.get(auto_status, '#6b7280') }};">{{ get_status_label(auto_status, tr) }}</span><br><br><b>{{ tr["team"] }}:</b> {{ s[1] }}<div class="plan-client-line"><b>{{ tr["pdf_client"] }}:</b> {{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}</div>{% if is_admin %}<div class="plan-shift-actions"><a class="action-link edit-link psa-btn" href="/edit_shift/{{ s[0] }}" title="{{ tr['edit'] }}" aria-label="{{ tr['edit'] }}">✏️</a><form class="inline-delete-form" method="post" action="/delete_shift/{{ s[0] }}" onsubmit='return confirm({{ tr.get("shift_delete_confirm","Delete this shift?")|tojson }});'><button type="submit" class="action-link delete-link psa-btn" title="{{ tr['delete'] }}" aria-label="{{ tr['delete'] }}">🗑️</button></form><a class="action-link copy-link psa-btn" href="/copy_shift/{{ s[0] }}" title="{{ tr['copy'] }}" aria-label="{{ tr['copy'] }}">📋</a></div>{% endif %}</div>{% endfor %}</div>
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
              <b>{{ tr["team"] }}:</b> {{ s[1] }}
              <div class="plan-client-line"><b>{{ tr["pdf_client"] }}:</b> {{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}</div>
              <div class="plan-shift-actions">
                <a class="action-link edit-link psa-btn" href="/edit_shift/{{ s[0] }}" title="{{ tr['edit'] }}" aria-label="{{ tr['edit'] }}">✏️</a>
                <form class="inline-delete-form" method="post" action="/delete_shift/{{ s[0] }}" onsubmit='return confirm({{ tr.get("shift_delete_confirm","Delete this shift?")|tojson }});'><button type="submit" class="action-link delete-link psa-btn" title="{{ tr['delete'] }}" aria-label="{{ tr['delete'] }}">🗑️</button></form>
                <a class="action-link copy-link psa-btn" href="/copy_shift/{{ s[0] }}" title="{{ tr['copy'] }}" aria-label="{{ tr['copy'] }}">📋</a>
              </div>
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
    // Legacy safety net: most delete-link anchors are now <form>+<button>
    // with onsubmit confirm, but this hook covers any remaining <a> just
    // in case (and is harmless when there are none).
    document.querySelectorAll('a.delete-link').forEach(function(link){
        link.addEventListener('click', function(e){
            var ok = confirm({{ tr.get("shift_delete_confirm","Delete this?")|tojson }});
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


@app.route("/paste_shift/<date>", methods=["POST"])
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


@app.route("/delete_absence/<int:id>", methods=["POST"])
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
    # Workers and admins share the same /week template now, so the SQL
    # range must match the requested week for both. Pre-fix the worker
    # branch loaded the entire current month through Dec 31, which
    # ignored ?start=… for past months and silently broke across the
    # year boundary.
    shifts = c.execute(
        "SELECT * FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time, id",
        (week_days[0], week_days[-1]),
    ).fetchall()
    if not is_admin:
        shifts = [s for s in shifts if worker_in_shift(current_user, s[1])]
    holidays_map = get_all_holidays(conn, {start_week.year, week_end.year})
    clients_raw = c.execute("SELECT name, address FROM clients ORDER BY name").fetchall()
    client_cities = client_city_map(clients_raw)
    client_addresses = {row[0]: (row[1] or "") for row in clients_raw}
    clients = clients_raw
    workers = c.execute("SELECT name FROM workers ORDER BY name").fetchall()
    conn.close()
    day_names = [tr["monday"], tr["tuesday"], tr["wednesday"], tr["thursday"], tr["friday"], tr["saturday"], tr["sunday"]]

    return render_template_string(BASE_STYLE + header_html() + """
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
                {% for s in shifts %}{% if s[3] == day %}{% set _waddr = client_addresses.get(s[2], '') %}<div class="mini-shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="--shift-accent:{{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#7aa7df') }};"><b>{{ s[1] }}</b><br>{{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}<br>{{ s[4] }}{% if is_admin %}<div class="week-shift-actions" style="display:flex;flex-direction:row;flex-wrap:nowrap;align-items:center;gap:4px;margin-top:6px;"><a class="mini-link edit-link wsa-btn" href="javascript:void(0)" data-eid="{{ s[0] }}" data-ew="{{ s[1]|e }}" data-ecl="{{ s[2]|e }}" data-edt="{{ s[3]|e }}" data-etm="{{ s[4]|e }}" data-est="{{ s[5]|e }}" onclick="openEditModalW(this)" title="{{ tr['edit'] }}" aria-label="{{ tr['edit'] }}">✏️</a><form class="inline-delete-form" style="flex:1;min-width:0;margin:0;" method="post" action="/delete_shift/{{ s[0] }}" onsubmit='return confirm({{ tr.get("shift_delete_confirm","Delete this shift?")|tojson }});'><button type="submit" class="mini-link delete-link wsa-btn" style="width:100%;" title="{{ tr['delete'] }}" aria-label="{{ tr['delete'] }}">🗑️</button></form><a class="mini-link copy-link wsa-btn" href="/copy_shift/{{ s[0] }}" title="{{ tr['copy'] }}" aria-label="{{ tr['copy'] }}">📋</a></div>{% elif _waddr %}<div style="margin-top:6px;"><a class="week-map-link" href="https://www.google.com/maps/dir/?api=1&destination={{ _waddr|urlencode }}&travelmode=driving&dir_action=navigate" target="_blank" rel="noopener" title="{{ tr.get('open_in_maps','Open in Google Maps') }}" aria-label="{{ tr.get('open_in_maps','Open in Google Maps') }}">📍</a></div>{% endif %}</div>{% endif %}{% endfor %}
            </div>
        {% endfor %}
    </div>
    {% if is_admin %}<div id="holidayModal" class="modal-backdrop"><div class="modal-card"><h3>{{ tr["add_holiday"] }}</h3><form method="post" action="/add_holiday"><label for="holidayDateW">{{ tr["date"] }}</label><input type="date" name="date" id="holidayDateW" required><input type="text" name="name" placeholder="{{ tr['holiday_name'] }}" required><button>{{ tr["save"] }}</button></form><button type="button" onclick="closeHolidayModal()">{{ tr["cancel"] }}</button></div></div>{% endif %}
    {% if is_admin %}
    <div id="addShiftModal" class="modal-backdrop" style="display:none;">
      <div class="modal-card" style="max-width:400px;width:95%;max-height:90vh;overflow-y:auto;">
        <h3 id="shiftModalTitleW">+ {{ tr['add_shift'] }} — <span id="addShiftModalDate"></span></h3>
        <form method="post" action="/add_shift" id="shiftModalFormW">
          <input type="hidden" name="return_to" id="shiftReturnToW" value="">
          <label>{{ tr['choose_worker'] }}</label>
          {% for w in workers %}{% if w[0] != 'admin' %}<label class="check-row"><input type="checkbox" name="workers" value="{{ w[0] }}">{{ w[0] }}</label>{% endif %}{% endfor %}
          <div class="client-search-wrapper"><input type="text" id="csInputWeek" class="client-search-input" placeholder="{{ tr['search_placeholder'] }}" autocomplete="off"><input type="hidden" name="client" id="csHiddenWeek" required><div class="client-search-dropdown" id="csListWeek"></div></div>
          <label for="addShiftDateW">{{ tr["date"] }}</label><input id="addShiftDateW" name="date" type="date" required>
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
    function openHolidayModal(dateStr){var m=document.getElementById('holidayModal');var d=document.getElementById('holidayDateW');if(m&&d){d.value=dateStr;m.style.display='block';}}
    function closeHolidayModal(){var m=document.getElementById('holidayModal');if(m){m.style.display='none';}}
    function openAddShiftModal(dateStr){
      var form=document.getElementById('shiftModalFormW');
      form.action='/add_shift';
      var rt=document.getElementById('shiftReturnToW');if(rt)rt.value='';
      var titleEl=document.getElementById('shiftModalTitleW');
      if(titleEl)titleEl.innerHTML='+ {{ tr["add_shift"] }} — <span id="addShiftModalDate"></span>';
      document.getElementById('addShiftModalDate').textContent=dateStr;
      var btn=document.getElementById('shiftModalSaveBtnW');if(btn)btn.textContent='{{ tr["add_shift"] }}';
      document.getElementById('addShiftDateW').value=dateStr;
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
      document.getElementById('addShiftDateW').value=date;
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
    function _closeMiniActions(menu){
      menu.classList.remove('open');
      var ms = menu.closest('.mini-shift');
      var dc = menu.closest('.calendar-day-card');
      if (ms) ms.classList.remove('actions-open');
      if (dc) dc.classList.remove('actions-open');
    }
    function toggleMiniActions(btn){
      var menu = btn.nextElementSibling;
      if (!menu || !menu.classList.contains('mini-actions')) return;
      var willOpen = !menu.classList.contains('open');
      document.querySelectorAll('.mini-actions.open').forEach(function(m){
        if (m !== menu) _closeMiniActions(m);
      });
      if (willOpen) {
        menu.classList.add('open');
        var ms = menu.closest('.mini-shift');
        var dc = menu.closest('.calendar-day-card');
        if (ms) ms.classList.add('actions-open');
        if (dc) dc.classList.add('actions-open');
      } else {
        _closeMiniActions(menu);
      }
    }
    document.addEventListener('click',function(e){if(!e.target.closest('.day-menu-wrapper')&&!e.target.closest('#addShiftModal .modal-card')){document.querySelectorAll('.day-mini-menu').forEach(function(m){m.style.display='none';});}
      if (!e.target.closest('.mini-actions-toggle') && !e.target.closest('.mini-actions.open')) {
        document.querySelectorAll('.mini-actions.open').forEach(function(m){ _closeMiniActions(m); });
      }
    });
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
    """, tr=tr, dark=dark, week_days=week_days, shifts=shifts, worker_colors=worker_colors, client_cities=client_cities, client_addresses=client_addresses, format_date=format_date, holidays_map=holidays_map, day_names=day_names, status_colors=STATUS_COLORS, get_status_label=get_status_label, get_auto_status=get_auto_status, split_workers=split_workers, is_weekend=is_weekend, is_admin=is_admin, prev_week=prev_week, next_week=next_week, current_week=current_week, start_year=start_week.year, start_month=start_week.month, workers=workers, clients=clients, time_hours=time_hours())


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
                <div style="font-weight:bold; margin-bottom:8px;"><a class="month-day-date" data-short="{{ day.strftime('%d') }}" href="{% if is_admin %}javascript:void(0){% else %}/?selected_date={{ daystr }}{% endif %}" {% if is_admin %}onclick="openHolidayModal('{{ daystr }}')"{% endif %} style="{% if day.weekday() >= 5 %}color:#ef4444;{% endif %}">{{ day.strftime('%d/%m/%Y') }}</a>{% if is_admin and copied_shift_id %}<br><form class="inline-delete-form" method="post" action="/paste_shift/{{ daystr }}" style="display:inline-block;margin-top:6px;"><button type="submit" style="padding:4px 7px;border-radius:6px;background:#16a34a;color:white;font-size:11px;border:none;cursor:pointer;font-family:inherit;">{{ tr["paste"] }}</button></form>{% endif %}</div>
                {% if holiday_name %}<small class="holiday-note">{{ holiday_name }}</small>{% endif %}
                {% for s in shifts_by_date.get(daystr, []) %}<div class="mini-shift" draggable="{{ 'true' if is_admin else 'false' }}" ondragstart="dragShift(event, '{{ s[0] }}')" style="--shift-accent:{{ worker_colors.get(split_workers(s[1])[0] if split_workers(s[1]) else s[1], '#7aa7df') }};" data-w="{{ s[1]|e }}" data-c="{{ s[2]|e }}" data-city="{{ client_cities.get(s[2], '')|e }}" data-t="{{ s[4]|e }}"><b>{{ s[1] }}</b><br>{{ s[2] }}{% if client_cities.get(s[2]) %} <strong class="client-city">{{ client_cities.get(s[2]) }}</strong>{% endif %}<br>{{ s[4] }}{% if is_admin %}<br><button type="button" class="mini-actions-toggle" onclick="toggleMiniActions(this)" aria-label="{{ tr.get('more_actions','Vise akcija') }}" title="{{ tr.get('more_actions','Vise akcija') }}">⋯</button><div class="mini-actions"><a class="mini-link edit-link" href="javascript:void(0)" data-eid="{{ s[0] }}" data-ew="{{ s[1]|e }}" data-ecl="{{ s[2]|e }}" data-edt="{{ s[3]|e }}" data-etm="{{ s[4]|e }}" data-est="{{ s[5]|e }}" onclick="openEditModalM(this)" title="{{ tr['edit'] }}" aria-label="{{ tr['edit'] }}">✏️</a><form class="inline-delete-form" method="post" action="/delete_shift/{{ s[0] }}" onsubmit='return confirm({{ tr.get("shift_delete_confirm","Delete this shift?")|tojson }});'><button type="submit" class="mini-link delete-link" title="{{ tr['delete'] }}" aria-label="{{ tr['delete'] }}">🗑️</button></form><a class="mini-link copy-link" href="/copy_shift/{{ s[0] }}" title="{{ tr['copy'] }}" aria-label="{{ tr['copy'] }}">📋</a></div>{% endif %}</div>{% endfor %}
            </div>
        {% endfor %}{% endfor %}
    </div>
    {% if is_admin %}<div id="holidayModal" class="modal-backdrop"><div class="modal-card"><h3>{{ tr["add_holiday"] }}</h3><form method="post" action="/add_holiday"><label for="holidayDateM">{{ tr["date"] }}</label><input type="date" name="date" id="holidayDateM" required><input type="text" name="name" placeholder="{{ tr['holiday_name'] }}" required><button>{{ tr["save"] }}</button></form><button type="button" onclick="closeHolidayModal()">{{ tr["cancel"] }}</button></div></div>{% endif %}
    {% if is_admin %}
    <div id="addShiftModal" class="modal-backdrop" style="display:none;">
      <div class="modal-card" style="max-width:400px;width:95%;max-height:90vh;overflow-y:auto;">
        <h3 id="shiftModalTitleM">+ {{ tr['add_shift'] }} — <span id="addShiftModalDate"></span></h3>
        <form method="post" action="/add_shift" id="shiftModalFormM">
          <input type="hidden" name="return_to" id="shiftReturnToM" value="">
          <label>{{ tr['choose_worker'] }}</label>
          {% for w in workers %}{% if w[0] != 'admin' %}<label class="check-row"><input type="checkbox" name="workers" value="{{ w[0] }}">{{ w[0] }}</label>{% endif %}{% endfor %}
          <div class="client-search-wrapper"><input type="text" id="csInputMonth" class="client-search-input" placeholder="{{ tr['search_placeholder'] }}" autocomplete="off"><input type="hidden" name="client" id="csHiddenMonth" required><div class="client-search-dropdown" id="csListMonth"></div></div>
          <label for="addShiftDateM">{{ tr["date"] }}</label><input id="addShiftDateM" name="date" type="date" required>
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
    function openHolidayModal(dateStr){var m=document.getElementById('holidayModal');var d=document.getElementById('holidayDateM');if(m&&d){d.value=dateStr;m.style.display='block';}} function closeHolidayModal(){var m=document.getElementById('holidayModal');if(m){m.style.display='none';}}
    function openAddShiftModal(dateStr){
      var form=document.getElementById('shiftModalFormM');
      form.action='/add_shift';
      var rt=document.getElementById('shiftReturnToM');if(rt)rt.value='';
      var titleEl=document.getElementById('shiftModalTitleM');
      if(titleEl)titleEl.innerHTML='+ {{ tr["add_shift"] }} — <span id="addShiftModalDate"></span>';
      document.getElementById('addShiftModalDate').textContent=dateStr;
      var btn=document.getElementById('shiftModalSaveBtnM');if(btn)btn.textContent='{{ tr["add_shift"] }}';
      document.getElementById('addShiftDateM').value=dateStr;
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
      document.getElementById('addShiftDateM').value=date;
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
    function _closeMiniActions(menu){
      menu.classList.remove('open');
      var ms = menu.closest('.mini-shift');
      var dc = menu.closest('.calendar-day-card');
      if (ms) ms.classList.remove('actions-open');
      if (dc) dc.classList.remove('actions-open');
    }
    function toggleMiniActions(btn){
      var menu = btn.nextElementSibling;
      if (!menu || !menu.classList.contains('mini-actions')) return;
      var willOpen = !menu.classList.contains('open');
      document.querySelectorAll('.mini-actions.open').forEach(function(m){
        if (m !== menu) _closeMiniActions(m);
      });
      if (willOpen) {
        menu.classList.add('open');
        var ms = menu.closest('.mini-shift');
        var dc = menu.closest('.calendar-day-card');
        if (ms) ms.classList.add('actions-open');
        if (dc) dc.classList.add('actions-open');
      } else {
        _closeMiniActions(menu);
      }
    }
    document.addEventListener('click',function(e){if(!e.target.closest('.day-menu-wrapper')&&!e.target.closest('#addShiftModal .modal-card')){document.querySelectorAll('.day-mini-menu').forEach(function(m){m.style.display='none';});}
      if (!e.target.closest('.mini-actions-toggle') && !e.target.closest('.mini-actions.open')) {
        document.querySelectorAll('.mini-actions.open').forEach(function(m){ _closeMiniActions(m); });
      }
    });
    document.addEventListener('DOMContentLoaded',function(){
      document.querySelectorAll('a.delete-link').forEach(function(link){link.addEventListener('click',function(e){if(!confirm({{ tr.get("shift_delete_confirm","Delete this?")|tojson }})){e.preventDefault();return false;}});});
      var CD=[{% for cl in clients %}{"name":{{cl[0]|tojson}},"addr":{{(cl[1] or '')|tojson}}}{% if not loop.last %},{% endif %}{% endfor %}];
      initClientSearch('csInputMonth','csHiddenMonth','csListMonth',CD);
      /* Shorten date display to just day number on small screens */
      if(window.innerWidth<=600){
        document.querySelectorAll('a.month-day-date').forEach(function(a){var s=a.getAttribute('data-short');if(s)a.textContent=s;});
        /* Compact mini-shift: radnik / 1. riječ klijenta / vrijeme.
           Build the summary with createElement + textContent so a
           client name containing HTML can't break out into markup,
           and preserve the admin .mini-actions-toggle / .mini-actions
           children (otherwise the new ⋯ popover is wiped on every
           mobile page load). */
        document.querySelectorAll('.month-grid .mini-shift').forEach(function(el){
          var w    = (el.getAttribute('data-w') || '').trim();
          var c    = el.getAttribute('data-c')   || '';
          var city = el.getAttribute('data-city')|| '';
          var t    = el.getAttribute('data-t')   || '';
          var toggle  = el.querySelector('.mini-actions-toggle');
          var actions = el.querySelector('.mini-actions');
          while (el.firstChild) el.removeChild(el.firstChild);
          if (w) {
            var dW = document.createElement('div');
            dW.className = 'ms-w'; dW.textContent = w;
            el.appendChild(dW);
          }
          if (c) {
            var dC = document.createElement('div');
            dC.className = 'ms-c'; dC.textContent = c;
            if (city) {
              var sCity = document.createElement('span');
              sCity.className = 'ms-city';
              sCity.textContent = ' ' + city;
              dC.appendChild(sCity);
            }
            el.appendChild(dC);
          }
          if (t) {
            var dT = document.createElement('div');
            dT.className = 'ms-t'; dT.textContent = t;
            el.appendChild(dT);
          }
          if (toggle)  el.appendChild(toggle);
          if (actions) el.appendChild(actions);
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
<meta name="theme-color" content="#1f4f82">
<link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}?v=3">
<link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon-32x32.png') }}?v=3">
<link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon-16x16.png') }}?v=3">
<link rel="apple-touch-icon" sizes="180x180" href="{{ url_for('static', filename='apple-touch-icon.png') }}?v=3">
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
        "<meta name='theme-color' content='#1f4f82'>"
        "<link rel='icon' href='/static/favicon.ico?v=3'>"
        "<link rel='icon' type='image/png' sizes='32x32' href='/static/favicon-32x32.png?v=3'>"
        "<link rel='icon' type='image/png' sizes='16x16' href='/static/favicon-16x16.png?v=3'>"
        "<link rel='apple-touch-icon' sizes='180x180' href='/static/apple-touch-icon.png?v=3'>"
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
    # Generation form inputs — these drive the POST /invoices/generate
    # work period and invoice_date. They do NOT filter the list anymore;
    # the listing has its own list_date_from/list_date_to params for
    # that. Otherwise a manual invoice dated today would silently
    # disappear when the admin browses /invoices tomorrow.
    default_from, default_to = previous_month_range()
    date_from    = request.args.get("date_from",    default_from).strip()
    date_to      = request.args.get("date_to",      default_to).strip()
    invoice_date = request.args.get("invoice_date", lux_now().strftime("%Y-%m-%d")).strip()
    # Listing filter — separate from the generation period. Empty by
    # default → show every saved invoice. Admin can narrow the list
    # explicitly by passing list_date_from / list_date_to.
    # invoice_date is stored as TEXT 'YYYY-MM-DD' so SQL comparisons
    # are lexicographic — junk like ?list_date_from=abc wouldn't crash
    # but it would silently match nothing. Reject anything that isn't
    # a real ISO date so the UI never shows a "weird empty list" from
    # a typo in the URL bar.
    def _valid_iso_date(s):
        if not s:
            return False
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return True
        except (TypeError, ValueError):
            return False
    _raw_lfrom = (request.args.get("list_date_from", "") or "").strip()
    _raw_lto   = (request.args.get("list_date_to",   "") or "").strip()
    list_date_from = _raw_lfrom if _valid_iso_date(_raw_lfrom) else ""
    list_date_to   = _raw_lto   if _valid_iso_date(_raw_lto)   else ""
    conn = get_conn()
    settings = get_invoice_settings(conn)
    profiles = get_invoice_profiles(conn)
    # Generation is POST-only since 742a60a — no silent GET writes.
    rows = fetch_invoice_records(
        conn,
        date_from=list_date_from or None,
        date_to=list_date_to or None,
        client=None,
        status="all",
    )
    conn.close()
    # NOTE: profiles is serialized in the template with Jinja tojson.
    # Avoid prebuilt JSON strings in script contexts.

    # ── Server-side filtering (must run BEFORE pagination) ─────────────
    q = (request.args.get("q", "") or "").strip().lower()
    status = (request.args.get("status", "all") or "all").strip().lower()
    if status not in ("all", "paid", "unpaid"):
        status = "all"
    if q:
        rows = [r for r in rows
                if q in (r.get("client") or "").lower()
                or q in str(r.get("invoice_number") or "").lower()]
    rows_filtered = rows
    if status == "paid":
        rows_filtered = [r for r in rows if r.get("paid")]
    elif status == "unpaid":
        rows_filtered = [r for r in rows if not r.get("paid")]

    # Stats: global counts for paid/unpaid tabs (always full picture for the
    # period filter so the badges don't shrink as the user narrows results).
    paid_rows = [r for r in rows if r.get("paid")]
    unpaid_rows = [r for r in rows if not r.get("paid")]
    total_paid = sum(r["total"] for r in paid_rows)
    total_unpaid = sum(r["total"] for r in unpaid_rows)
    total_all = sum(r["total"] for r in rows)
    # paginate the (q + status) filtered set
    rows = rows_filtered

    # ── Pagination ─────────────────────────────────────────────────────
    PER_PAGE = 30
    total_records = len(rows)
    total_pages = max(1, (total_records + PER_PAGE - 1) // PER_PAGE)
    try:
        current_page = int(request.args.get("page", "1") or "1")
    except (TypeError, ValueError):
        current_page = 1
    if current_page < 1: current_page = 1
    if current_page > total_pages: current_page = total_pages
    start = (current_page - 1) * PER_PAGE
    page_records = rows[start:start + PER_PAGE]

    # Build a "smart ellipsis" list of page numbers to render
    # Always include 1, last 1-2, current ± 2; insert None where there's a gap.
    def _build_pages(cur, total):
        if total <= 1: return [1]
        keep = set([1, total])
        if total >= 2: keep.add(total - 1)
        for p in range(cur - 2, cur + 3):
            if 1 <= p <= total: keep.add(p)
        out = []
        prev = 0
        for p in sorted(keep):
            if prev and p - prev > 1:
                out.append(None)   # ellipsis marker
            out.append(p); prev = p
        return out
    pages_to_show = _build_pages(current_page, total_pages)

    # Preserve current query params on each page link (drop only 'page')
    _qs_args = {k: v for k, v in request.args.items() if k != "page"}
    def _page_link(p):
        a = dict(_qs_args); a["page"] = str(p)
        return "/invoices?" + urllib.parse.urlencode(a) + "#invoice-list"
    page_link = _page_link

    # Status-tab links: preserve everything but reset page back to 1
    # so user doesn't land on an out-of-range page after narrowing the set.
    _status_args = {k: v for k, v in request.args.items()
                    if k not in ("status", "page")}
    def _status_link(s):
        a = dict(_status_args)
        if s != "all":
            a["status"] = s
        return "/invoices?" + urllib.parse.urlencode(a) + "#invoice-list"
    status_link = _status_link

    # "Clear list filter" chip: drop list_date_from/list_date_to and
    # page (would otherwise resolve to an out-of-range page after the
    # set widens), but preserve status + q so the admin doesn't lose
    # an unrelated narrowing they had applied.
    _clear_args = {k: v for k, v in request.args.items()
                   if k not in ("list_date_from", "list_date_to", "page")}
    clear_list_filter_link = (
        "/invoices?" + urllib.parse.urlencode(_clear_args) + "#invoice-list"
        if _clear_args else "/invoices#invoice-list"
    )
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
        .invoice-shell { background:{{ '#161618' if dark else '#ffffff' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }}; border-radius:10px; padding:0 0 22px 0; overflow:hidden; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
        .invoice-top { display:flex; align-items:center; justify-content:space-between; gap:18px; padding:18px 22px; background:{{ '#1d1d1f' if dark else '#f1f5f9' }}; }
        .invoice-brand { font-size:26px; font-weight:800; color:{{ '#e2e8f0' if dark else '#1e293b' }}; }
        .invoice-brand span { background:#ffd429; color:#111; border-radius:6px; padding:2px 6px; }
        .invoice-search { flex:1; display:flex; max-width:720px; }
        .invoice-search input { border-radius:0; margin:0; }
        .invoice-search button { width:130px; margin:0; border-radius:0; background:#111; color:white; }
        .invoice-panel { max-width:1280px; margin:34px auto 0 auto; background:{{ '#191919' if dark else '#f8fafc' }}; border-radius:8px; padding:22px 30px; color:{{ '#e2e8f0' if dark else '#1e293b' }}; }
        .invoice-panel h1, .invoice-panel h2, .invoice-panel h3, .invoice-panel h4 { color:{{ '#e2e8f0' if dark else '#1e293b' }} !important; }
        .invoice-tabs { display:flex; gap:6px; flex-wrap:wrap; margin:14px 0 22px; }
        .invoice-tab { padding:12px 16px; background:{{ '#222225' if dark else '#cbd5e1' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }}; border-radius:8px 8px 0 0; font-weight:bold; text-decoration:none; }
        .invoice-tab.active { background:{{ '#2c2c30' if dark else '#1f4f82' }}; color:white; }
        .pill { display:inline-block; margin-left:6px; padding:2px 8px; border-radius:999px; font-size:12px; color:#111; background:#e5e7eb; }
        .pill.red { background:#fb7185; color:white; } .pill.green { background:#34d399; }
        .invoice-table { width:100%; border-collapse:collapse; color:{{ '#e2e8f0' if dark else '#1e293b' }}; }
        .invoice-table th, .invoice-table td { padding:14px 10px; border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; text-align:left; }
        .invoice-table th { font-size:13px; text-transform:uppercase; color:{{ '#94a3b8' if dark else '#475569' }}; }
        .paid-text { color:{{ '#4ade80' if dark else '#16a34a' }}; font-weight:bold; } .unpaid-text { color:{{ '#fb7185' if dark else '#dc2626' }}; font-weight:bold; }
        .sent-badge { display:inline-block; padding:4px 8px; border-radius:999px; font-size:12px; font-weight:bold; color:#111; }
        .sent-badge.sent { background:#34d399; } .sent-badge.unsent { background:#fb7185; color:white; }
        .invoice-totals { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-top:16px; }
        .invoice-total-card { background:{{ '#1d1d1f' if dark else '#f1f5f9' }}; border-radius:8px; padding:14px; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
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
                <h1 style="margin:0;">{{ tr["invoices"] }}</h1>
                <div class="invoice-actions">
                    <a href="/invoices/export_options?type=all">{{ tr["download_all_invoices"] }}</a>
                    <a href="/invoices/export_options?type=certificate">{{ tr["annual_certificate"] }}</a>
                    <a href="/invoices/export_options?type=list">{{ tr.get("invoice_list_pdf","Lista faktura PDF") }}</a>
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

            <!-- List filter — explicit, separate from generation period.
                 Empty default = show every saved invoice. Manual invoices
                 don't have a work period so they used to vanish when the
                 generation date range moved — this lets the admin narrow
                 the list when they want to, without ever silently
                 hiding rows. -->
            <form method="get" action="/invoices" style="display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end;margin:6px 0 14px;padding:10px 12px;border-radius:10px;background:{{ '#0f0f10' if dark else '#f8fafc' }};border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};">
              <div style="display:flex;flex-direction:column;gap:2px;">
                <label for="invListFromDate" style="font-size:11px;font-weight:700;color:{{ '#94a3b8' if dark else '#64748b' }};">{{ tr.get("list_from","Lista od") }}</label>
                <input id="invListFromDate" type="date" name="list_date_from" value="{{ list_date_from }}" style="padding:6px 8px;border-radius:6px;border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};background:{{ '#161618' if dark else 'white' }};color:{{ '#e2e8f0' if dark else '#0f172a' }};font-size:13px;">
              </div>
              <div style="display:flex;flex-direction:column;gap:2px;">
                <label for="invListToDate" style="font-size:11px;font-weight:700;color:{{ '#94a3b8' if dark else '#64748b' }};">{{ tr.get("list_to","do") }}</label>
                <input id="invListToDate" type="date" name="list_date_to"   value="{{ list_date_to }}"   style="padding:6px 8px;border-radius:6px;border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};background:{{ '#161618' if dark else 'white' }};color:{{ '#e2e8f0' if dark else '#0f172a' }};font-size:13px;">
              </div>
              {# preserve other params on filter submit #}
              {% if q %}<input type="hidden" name="q" value="{{ q }}">{% endif %}
              {% if status and status != 'all' %}<input type="hidden" name="status" value="{{ status }}">{% endif %}
              <button type="submit" style="padding:7px 14px;border-radius:6px;border:none;background:#1f4f82;color:white;font-weight:700;font-size:13px;cursor:pointer;font-family:inherit;">🔎 {{ tr.get("filter_list","Filtriraj listu") }}</button>
              {% if list_date_from or list_date_to %}
              <a href="{{ clear_list_filter_link }}" style="padding:7px 12px;border-radius:6px;background:#6b7280;color:white;font-weight:700;font-size:13px;text-decoration:none;line-height:24px;">✕ {{ tr.get("clear_filter","Ocisti filter") }}</a>
              {% endif %}
              <div style="flex:1;"></div>
              <small style="color:{{ '#94a3b8' if dark else '#64748b' }};align-self:center;">
                {% if list_date_from or list_date_to %}
                  {{ tr.get("filter_active","Filter aktivan") }}
                {% else %}
                  {{ tr.get("showing_all","Prikaz svih sacuvanih faktura") }}
                {% endif %}
              </small>
            </form>

            <div class="invoice-tabs">
                <a class="invoice-tab {% if status == 'all' %}active{% endif %}" href="{{ status_link('all') }}">{{ tr["total_invoices"] }} <span class="pill">{{ paid_rows|length + unpaid_rows|length }}</span></a>
                <a class="invoice-tab {% if status == 'unpaid' %}active{% endif %}" href="{{ status_link('unpaid') }}">{{ tr["unpaid"] }} <span class="pill red">{{ unpaid_rows|length }}</span></a>
                <a class="invoice-tab {% if status == 'paid' %}active{% endif %}" href="{{ status_link('paid') }}">{{ tr["paid"] }} <span class="pill green">{{ paid_rows|length }}</span></a>
                <a class="invoice-tab" href="/invoices/quote">{{ tr["quote"] }}</a>
                <a class="invoice-tab" href="/invoices/manual" style="background:#22c55e;color:#111;">✏️ {{ tr.get("mi_title","Facture manuelle") }}</a>
            </div>

            <!-- Bulk action bar (hidden until at least one row is selected) -->
            <div id="bulkBar" style="display:none;background:{{ '#0d1117' if dark else '#1f4f82' }};color:white;padding:12px 16px;border-radius:10px;margin:0 0 12px;align-items:center;gap:12px;flex-wrap:wrap;">
              <span style="font-weight:700;font-size:14px;">
                <span id="bulkCount">0</span> {{ tr.get("bulk_selected","selected") }}
                <span style="opacity:.7;margin-left:8px;">· TTC: <span id="bulkTotal">0.00</span> €</span>
              </span>
              <div style="flex:1;"></div>
              <form method="post" action="/invoices/bulk_download" class="bulk-form" style="display:inline;">
                <button type="submit" style="background:#0ea5e9;color:white;font-weight:700;padding:7px 12px;border-radius:6px;border:none;cursor:pointer;font-size:13px;">📥 {{ tr.get("download_selected_pdf","Download PDF (ZIP)") }}</button>
              </form>
              <form method="post" action="/invoices/bulk_action" class="bulk-form" style="display:inline;">
                <input type="hidden" name="action" value="mark_paid">
                <button type="submit" style="background:#16a34a;color:white;font-weight:700;padding:7px 12px;border-radius:6px;border:none;cursor:pointer;font-size:13px;">✓ {{ tr.get("mark_selected_paid","Mark as paid") }}</button>
              </form>
              <form method="post" action="/invoices/bulk_action" class="bulk-form" style="display:inline;">
                <input type="hidden" name="action" value="mark_unpaid">
                <button type="submit" style="background:#f59e0b;color:white;font-weight:700;padding:7px 12px;border-radius:6px;border:none;cursor:pointer;font-size:13px;">↺ {{ tr.get("mark_selected_unpaid","Mark as unpaid") }}</button>
              </form>
              <form method="post" action="/invoices/bulk_action" class="bulk-form" style="display:inline;">
                <input type="hidden" name="action" value="mark_sent">
                <button type="submit" style="background:#16a34a;color:white;font-weight:700;padding:7px 12px;border-radius:6px;border:none;cursor:pointer;font-size:13px;">✉ {{ tr.get("mark_selected_sent","Mark as sent") }}</button>
              </form>
              <form method="post" action="/invoices/bulk_action" class="bulk-form" style="display:inline;">
                <input type="hidden" name="action" value="mark_unsent">
                <button type="submit" style="background:#f59e0b;color:white;font-weight:700;padding:7px 12px;border-radius:6px;border:none;cursor:pointer;font-size:13px;">↺ {{ tr.get("mark_selected_unsent","Mark as unsent") }}</button>
              </form>
              <form method="post" action="/invoices/bulk_action" class="bulk-form" id="bulkDeleteForm" style="display:inline;"
                    onsubmit='var _tpl={{ tr.get("delete_selected_confirm","Delete {n} selected invoices?")|tojson }};return confirm(_tpl.replace("{n}", document.getElementById("bulkCount").textContent));'>
                <input type="hidden" name="action" value="delete">
                <button type="submit" style="background:#ef4444;color:white;font-weight:700;padding:7px 12px;border-radius:6px;border:none;cursor:pointer;font-size:13px;">🗑 {{ tr.get("delete_selected","Delete selected") }}</button>
              </form>
            </div>

            <div style="overflow-x:auto;">
            <table id="invoice-list" class="invoice-table">
                <tr>
                    <th style="width:36px;"><input type="checkbox" id="bulkSelectAll" style="width:auto;cursor:pointer;" title="{{ tr.get('select_all','Select all') }}"></th>
                    <th>{{ tr["client_name"] }}</th><th>Document</th><th>{{ tr["invoice_number"] }}</th><th>{{ tr["invoice_date"] }}</th><th>{{ tr["payment_status"] }}</th><th>{{ tr["sent_status"] }}</th><th>{{ tr["amount_with_vat"] }}</th><th>{{ tr.get("edit","Uredi") }}</th><th>PDF</th><th></th>
                </tr>
                {% for row in page_records %}
                <tr class="invoice-row" data-paid="{{ 1 if row.paid else 0 }}" data-total="{{ row.total }}" data-search="{{ (row.client ~ ' ' ~ row.invoice_number)|lower }}">
                    <td><input type="checkbox" class="invoice-select" value="{{ row.invoice_number }}" style="width:auto;cursor:pointer;"></td>
                    <td>
                      <a href="/invoices/view?invoice_number={{ row.invoice_number }}" style="color:{{ '#93c5fd' if dark else '#1f4f82' }};text-decoration:underline;font-weight:600;">{{ row.client }}</a>
                    </td>
                    <td>{{ tr["invoices"] }}{% if row.source == 'manual' %} <span style="font-size:10px;background:#22c55e;color:#111;padding:1px 5px;border-radius:4px;">✏️</span>{% endif %}</td>
                    <td>
                      <a href="/invoices/view?invoice_number={{ row.invoice_number }}" style="color:{% if row.source == 'manual' %}{{ '#ffd429' if dark else '#b45309' }}{% else %}{{ '#93c5fd' if dark else '#1f4f82' }}{% endif %};text-decoration:underline;font-weight:600;">{{ row.invoice_number }}</a>
                    </td>
                    <td>{{ format_date(row.invoice_date) }}</td>
                    <td>
                        <span class="payment-label {{ 'paid-text' if row.paid else 'unpaid-text' }}">{{ tr["paid"] if row.paid else tr["unpaid"] }}</span><br>
                        <button type="button" class="ajax-invoice-toggle" data-kind="paid" data-action="/invoices/mark_paid" data-paid-label="{{ tr['paid'] }}" data-unpaid-label="{{ tr['unpaid'] }}" data-mark-paid="{{ tr['mark_paid'] }}" data-mark-unpaid="{{ tr['mark_unpaid'] }}" data-fields='{{ {"invoice_number":row.invoice_number,"paid":(0 if row.paid else 1),"client":row.client,"date_from":row.date_from,"date_to":row.date_to,"invoice_date":row.invoice_date,"amount":row.amount,"vat_amount":row.vat_amount,"total":row.total,"ajax":"1"}|tojson }}' style="color:{{ '#93c5fd' if dark else '#1f4f82' }};font-size:12px;text-decoration:underline;background:none;border:none;cursor:pointer;font-family:inherit;padding:0;">{{ tr["mark_unpaid"] if row.paid else tr["mark_paid"] }}</button>
                    </td>
                    <td>
                        <span class="sent-badge {{ 'sent' if row.sent else 'unsent' }}">{{ tr["sent_yes"] if row.sent else tr["sent_no"] }}</span><br>
                        <button type="button" class="ajax-invoice-toggle" data-kind="sent" data-action="/invoices/mark_sent" data-sent-label="{{ tr['sent_yes'] }}" data-unsent-label="{{ tr['sent_no'] }}" data-mark-sent="{{ tr['mark_sent'] }}" data-mark-unsent="{{ tr['mark_unsent'] }}" data-fields='{{ {"invoice_number":row.invoice_number,"sent":(0 if row.sent else 1),"ajax":"1"}|tojson }}' style="color:{{ '#93c5fd' if dark else '#1f4f82' }};font-size:12px;text-decoration:underline;background:none;border:none;cursor:pointer;font-family:inherit;padding:0;">{{ tr["mark_unsent"] if row.sent else tr["mark_sent"] }}</button>
                    </td>
                    <td><b>{{ "%.2f"|format(row.total) }} EUR</b></td>
                    <td>
                      {% if row.source == 'manual' %}
                        <a href="/invoices/manual?invoice_number={{ row.invoice_number }}" style="display:inline-block;padding:5px 12px;background:#ffd429;color:#111;border-radius:6px;font-weight:800;font-size:13px;text-decoration:none;">✏️ {{ tr.get("edit","Uredi") }}</a>
                      {% else %}
                        <a href="/invoices/manual?load_auto={{ row.invoice_number }}" style="display:inline-block;padding:5px 12px;background:#f59e0b;color:#111;border-radius:6px;font-weight:800;font-size:13px;text-decoration:none;">✏️ {{ tr.get("edit","Uredi") }}</a>
                      {% endif %}
                    </td>
                    <td>
                      {% if row.source == 'manual' %}
                        <a href="/invoices/manual/pdf?invoice_number={{ row.invoice_number }}" style="color:{{ '#93c5fd' if dark else '#1f4f82' }};font-weight:600;text-decoration:underline;">PDF</a>
                      {% else %}
                        <a href="/invoices/download?invoice_number={{ row.invoice_number }}&client={{ row.client|urlencode }}&date_from={{ row.date_from }}&date_to={{ row.date_to }}&invoice_date={{ row.invoice_date }}" style="color:{{ '#93c5fd' if dark else '#1f4f82' }};font-weight:600;text-decoration:underline;">PDF</a>
                      {% endif %}
                    </td>
                    <td><form method="post" action="/invoices/delete" style="display:inline;margin:0;" onsubmit='return confirm({{ tr.get("invoice_delete_confirm","Obrisati ovu fakturu?")|tojson }});'><input type="hidden" name="invoice_number" value="{{ row.invoice_number }}"><input type="hidden" name="next" value="/invoices?{{ request.query_string.decode() }}"><button type="submit" style="background:none;border:none;padding:0;cursor:pointer;color:{{ '#fb7185' if dark else '#dc2626' }};font-weight:600;font-family:inherit;font-size:inherit;text-decoration:underline;">{{ tr["delete"] }}</button></form></td>
                </tr>
                {% endfor %}
            </table>
            </div>
            {% if rows|length == 0 %}<div class="muted">{{ tr.get("inv_gen_empty", "Nema faktura za odabrani period.") }}</div>{% endif %}

            {% if total_pages > 1 %}
            <nav class="pagination" aria-label="Pagination" style="display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap;margin:22px 0 8px;">
              <span style="margin-right:10px;font-size:12px;color:{{ '#94a3b8' if dark else '#64748b' }};">{{ start + 1 }}–{{ start + page_records|length }} / {{ total_records }}</span>
              {% set _prev_dis = (current_page == 1) %}
              <a href="{{ page_link(current_page - 1) if not _prev_dis else '#' }}" aria-disabled="{{ _prev_dis|lower }}" style="padding:7px 12px;border-radius:8px;font-weight:700;font-size:13px;text-decoration:none;border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};background:{{ '#1d1d1f' if dark else '#ffffff' }};color:{% if _prev_dis %}{{ '#475569' if dark else '#94a3b8' }}{% else %}{{ '#e2e8f0' if dark else '#1e293b' }}{% endif %};pointer-events:{% if _prev_dis %}none{% else %}auto{% endif %};">← {{ tr.get("pagination_previous","Précédent") }}</a>
              {% for p in pages_to_show %}
                {% if p is none %}
                  <span style="padding:7px 4px;color:{{ '#94a3b8' if dark else '#64748b' }};font-weight:700;">…</span>
                {% elif p == current_page %}
                  <span style="padding:7px 12px;border-radius:8px;font-weight:800;font-size:13px;background:{{ '#2c2c30' if dark else '#1f4f82' }};color:white;">{{ p }}</span>
                {% else %}
                  <a href="{{ page_link(p) }}" style="padding:7px 12px;border-radius:8px;font-weight:700;font-size:13px;text-decoration:none;border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};background:{{ '#1d1d1f' if dark else '#ffffff' }};color:{{ '#e2e8f0' if dark else '#1e293b' }};">{{ p }}</a>
                {% endif %}
              {% endfor %}
              {% set _next_dis = (current_page == total_pages) %}
              <a href="{{ page_link(current_page + 1) if not _next_dis else '#' }}" aria-disabled="{{ _next_dis|lower }}" style="padding:7px 12px;border-radius:8px;font-weight:700;font-size:13px;text-decoration:none;border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};background:{{ '#1d1d1f' if dark else '#ffffff' }};color:{% if _next_dis %}{{ '#475569' if dark else '#94a3b8' }}{% else %}{{ '#e2e8f0' if dark else '#1e293b' }}{% endif %};pointer-events:{% if _next_dis %}none{% else %}auto{% endif %};">{{ tr.get("pagination_next","Suivant") }} →</a>
            </nav>
            {% endif %}

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
    var invoiceProfiles = {{ profiles|tojson }};
    // Search + paid/unpaid tabs are server-side now (pagination-aware).
    /* ── Bulk selection UI ───────────────────────────────────────── */
    function bulkUpdate(){
        var checks = document.querySelectorAll('.invoice-select:checked');
        var count = checks.length;
        var total = 0;
        var nums = [];
        checks.forEach(function(cb){
            nums.push(cb.value);
            var row = cb.closest('.invoice-row');
            if (row) total += parseFloat(row.getAttribute('data-total') || '0') || 0;
        });
        var bar = document.getElementById('bulkBar');
        if (bar) {
            bar.style.display = count > 0 ? 'flex' : 'none';
            document.getElementById('bulkCount').textContent = String(count);
            document.getElementById('bulkTotal').textContent = total.toFixed(2);
        }
        // Sync every bulk-form: inject hidden invoice_numbers[] inputs
        document.querySelectorAll('.bulk-form').forEach(function(f){
            f.querySelectorAll('input[name="invoice_numbers[]"]').forEach(function(x){ x.remove(); });
            nums.forEach(function(n){
                var h = document.createElement('input');
                h.type='hidden'; h.name='invoice_numbers[]'; h.value=n;
                f.appendChild(h);
            });
            // next= back to current page
            var nx = f.querySelector('input[name="next"]');
            if (!nx) {
                nx = document.createElement('input');
                nx.type='hidden'; nx.name='next';
                f.appendChild(nx);
            }
            nx.value = window.location.pathname + window.location.search;
        });
        // select-all checkbox indeterminate / checked sync
        var sa = document.getElementById('bulkSelectAll');
        if (sa) {
            var visible = Array.prototype.filter.call(
                document.querySelectorAll('.invoice-select'),
                function(cb){ var r=cb.closest('.invoice-row'); return r && r.style.display !== 'none'; }
            );
            var visChecked = visible.filter(function(cb){ return cb.checked; });
            sa.checked       = visible.length > 0 && visChecked.length === visible.length;
            sa.indeterminate = visChecked.length > 0 && visChecked.length < visible.length;
        }
    }
    function bulkToggleAll(master){
        document.querySelectorAll('.invoice-select').forEach(function(cb){
            var r = cb.closest('.invoice-row');
            if (r && r.style.display !== 'none') cb.checked = master.checked;
        });
        bulkUpdate();
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
        // Search submits via the GET form; no live filter (server paginates)

        // Bulk selection wiring
        var selAll = document.getElementById('bulkSelectAll');
        if (selAll) selAll.addEventListener('change', function(){ bulkToggleAll(this); });
        document.querySelectorAll('.invoice-select').forEach(function(cb){
            cb.addEventListener('change', bulkUpdate);
        });
        bulkUpdate();
        // ?status=paid|unpaid filter is server-side, so toggling paid via
        // AJAX would leave a now-stale row visible in the wrong tab. When
        // filtering is active, reload to let the server re-render the page.
        var SERVER_STATUS_FILTER = {{ ('paid' if status == 'paid' else ('unpaid' if status == 'unpaid' else ''))|tojson }};
        document.querySelectorAll('.ajax-invoice-toggle').forEach(function(btn){
            btn.addEventListener('click', function(event){
                event.preventDefault();
                var row    = btn.closest('.invoice-row');
                var action = btn.dataset.action;
                // data-fields is a JSON dict of the form payload — no
                // GET href to parse, no params in the URL bar. Backend
                // is POST-only so this is the only safe transport.
                var fields;
                try { fields = JSON.parse(btn.dataset.fields || "{}"); }
                catch(e){ fields = {}; }
                function buildFD(){
                    var fd = new FormData();
                    Object.keys(fields).forEach(function(k){
                        fd.append(k, fields[k]);
                    });
                    // Future-proof: include a CSRF meta token when
                    // the app grows one; harmless when it is absent.
                    var meta = document.querySelector('meta[name="csrf-token"]');
                    if(meta && meta.content){ fd.append('csrf_token', meta.content); }
                    return fd;
                }
                function fallbackPost(){
                    var f = document.createElement('form');
                    f.method = 'post';
                    f.action = action;
                    Object.keys(fields).forEach(function(k){
                        if(k === 'ajax') return;
                        var i = document.createElement('input');
                        i.type = 'hidden'; i.name = k; i.value = fields[k];
                        f.appendChild(i);
                    });
                    var meta = document.querySelector('meta[name="csrf-token"]');
                    if(meta && meta.content){
                        var csrf = document.createElement('input');
                        csrf.type = 'hidden';
                        csrf.name = 'csrf_token';
                        csrf.value = meta.content;
                        f.appendChild(csrf);
                    }
                    document.body.appendChild(f); f.submit();
                }
                fetch(action, {method:"POST", body:buildFD(),
                               headers:{'X-Requested-With':'fetch'}})
                    .then(function(resp){ return resp.json(); })
                    .then(function(data){
                        if(!data.ok){ fallbackPost(); return; }
                        if(btn.dataset.kind === 'paid' && SERVER_STATUS_FILTER){
                            window.location.reload();
                            return;
                        }
                        if(btn.dataset.kind === 'sent'){
                            var badge = row.querySelector('.sent-badge');
                            badge.textContent = data.sent ? btn.dataset.sentLabel : btn.dataset.unsentLabel;
                            badge.classList.toggle('sent',   data.sent);
                            badge.classList.toggle('unsent', !data.sent);
                            btn.textContent = data.sent ? btn.dataset.markUnsent : btn.dataset.markSent;
                            // Flip the next-click intent so a second
                            // press undoes the action.
                            fields.sent = data.sent ? 0 : 1;
                            btn.dataset.fields = JSON.stringify(fields);
                        } else if(btn.dataset.kind === 'paid'){
                            var label = row.querySelector('.payment-label');
                            label.textContent = data.paid ? btn.dataset.paidLabel : btn.dataset.unpaidLabel;
                            label.classList.toggle('paid-text',   data.paid);
                            label.classList.toggle('unpaid-text', !data.paid);
                            row.setAttribute('data-paid', data.paid ? '1' : '0');
                            btn.textContent = data.paid ? btn.dataset.markUnpaid : btn.dataset.markPaid;
                            fields.paid = data.paid ? 0 : 1;
                            btn.dataset.fields = JSON.stringify(fields);
                        }
                    })
                    .catch(function(){ fallbackPost(); });
            });
        });
    });
    </script>
    """, tr=tr, dark=dark, settings=settings, profiles=profiles,
         rows=rows, page_records=page_records,
         current_page=current_page, total_pages=total_pages, total_records=total_records,
         start=start, pages_to_show=pages_to_show, page_link=page_link,
         status=status, status_link=status_link, q=q,
         paid_rows=paid_rows, unpaid_rows=unpaid_rows, total_paid=total_paid,
         total_unpaid=total_unpaid, total_all=total_all, format_date=format_date,
         date_from=date_from, date_to=date_to, invoice_date=invoice_date,
         list_date_from=list_date_from, list_date_to=list_date_to,
         clear_list_filter_link=clear_list_filter_link)


def _find_overlapping_auto_invoice(c, client_name, date_from, date_to):
    """Return (invoice_number, date_from, date_to) of an existing auto
    invoice for this client whose period overlaps with the given one
    (standard interval overlap: A.from <= B.to AND A.to >= B.from),
    EXCLUDING exact matches. None if no such row.

    Exact matches are handled separately by the caller — they are a
    silent skip case, while genuine overlaps are a warn-and-block case
    that requires explicit admin override.
    """
    return c.execute(
        "SELECT invoice_number, date_from, date_to FROM invoice_records "
        "WHERE client_name=? AND COALESCE(deleted,0)=0 "
        "AND COALESCE(source,'auto')='auto' "
        "AND date_from <= ? AND date_to >= ? "
        "AND NOT (date_from=? AND date_to=?) "
        "ORDER BY date_from DESC LIMIT 1",
        (client_name, date_to, date_from, date_from, date_to)
    ).fetchone()


def _classify_generation_targets(conn, date_from, date_to):
    """Pre-flight classification for the generate-invoices preview.

    Buckets every client with shifts in the requested period into one
    of four lists so the admin can review what's about to happen
    BEFORE any DB write:

      will_generate  — has shifts, has rate, no overlapping invoice
      exact_match    — already has an auto invoice with the same
                       (date_from, date_to) pair → safe silent skip
      overlapping    — has an auto invoice whose period overlaps but
                       isn't exact → warn + block unless force=True
      no_rate        — hourly_rate is 0 → cannot price the invoice

    Read-only — no INSERTs happen here. This is the data model the
    preview template renders from.
    """
    c = conn.cursor()
    settings = get_invoice_settings(conn)
    raw_rows = build_invoice_rows(conn, date_from, date_to, None, settings)
    will_generate, exact_match, overlapping, no_rate = [], [], [], []
    for row in raw_rows:
        if row.get("hourly_rate", 0) == 0:
            no_rate.append({"client": row["client"]})
            continue
        exact = c.execute(
            "SELECT invoice_number FROM invoice_records "
            "WHERE client_name=? AND date_from=? AND date_to=? "
            "AND COALESCE(deleted,0)=0 AND COALESCE(source,'auto')='auto'",
            (row["client"], date_from, date_to)
        ).fetchone()
        if exact:
            exact_match.append({
                "client":         row["client"],
                "invoice_number": exact[0],
            })
            continue
        overlap = _find_overlapping_auto_invoice(c, row["client"], date_from, date_to)
        if overlap:
            overlapping.append({
                "client":           row["client"],
                "amount":           row.get("amount", 0),
                "vat_amount":       row.get("vat_amount", 0),
                "total":            row.get("total", 0),
                "existing_invoice": overlap[0],
                "existing_from":    overlap[1],
                "existing_to":      overlap[2],
            })
            continue
        will_generate.append({
            "client":     row["client"],
            "amount":     row.get("amount", 0),
            "vat_amount": row.get("vat_amount", 0),
            "total":      row.get("total", 0),
        })
    return {
        "will_generate": will_generate,
        "exact_match":   exact_match,
        "overlapping":   overlapping,
        "no_rate":       no_rate,
    }


def _generate_invoices(conn, date_from, date_to, invoice_date, force=False):
    """Insert new invoice records for clients with shifts in the period.

    Always skips clients with an exact-match existing auto invoice.
    By default ALSO skips clients whose period merely overlaps an
    existing auto invoice — that case is blocked at the app layer to
    prevent accidental near-duplicates (e.g. admin off-by-one on
    date_to). Pass force=True to override and generate for overlapping
    clients too (exact matches stay skipped — the unique index would
    reject them at the DB layer anyway).

    Returns (generated, skipped_exists, skipped_overlap, no_rate_clients,
             failed_clients, error_message)."""
    c = conn.cursor()
    settings = get_invoice_settings(conn)
    raw_rows = build_invoice_rows(conn, date_from, date_to, None, settings)
    generated = 0
    skipped_exists = 0
    skipped_overlap = 0
    no_rate_clients = []
    failed_clients = []
    attempted_clients = []
    try:
        if not USE_POSTGRES:
            c.execute("BEGIN IMMEDIATE")
        else:
            c.execute("LOCK TABLE invoice_records IN EXCLUSIVE MODE")
        for row in raw_rows:
            if row.get("hourly_rate", 0) == 0:
                no_rate_clients.append(row["client"])
                continue
            attempted_clients.append(row["client"])
            # Match the preview classifier: restrict the exact-match
            # skip to auto invoices, so a manual invoice that happens
            # to share (client, date_from, date_to) doesn't get
            # misreported here as "already exists" when the preview
            # said "will be generated".
            existing = c.execute(
                "SELECT invoice_number FROM invoice_records "
                "WHERE client_name=? AND date_from=? AND date_to=? "
                "AND COALESCE(deleted,0)=0 "
                "AND COALESCE(source,'auto')='auto'",
                (row["client"], date_from, date_to)
            ).fetchone()
            if existing:
                skipped_exists += 1
                continue
            if not force:
                # Overlap guard at write time (not just preview) so a
                # malicious / stale POST that bypasses the preview can't
                # silently create near-duplicates.
                overlap = _find_overlapping_auto_invoice(
                    c, row["client"], date_from, date_to
                )
                if overlap:
                    skipped_overlap += 1
                    continue
            inv_num = next_invoice_number(conn)
            c.execute("""INSERT INTO invoice_records
                (invoice_number, client_name, date_from, date_to, invoice_date,
                 amount, vat_amount, total, paid, sent, deleted, source)
                VALUES (?,?,?,?,?,?,?,?,0,0,0,'auto')""",
                (inv_num, row["client"], date_from, date_to, invoice_date,
                 row["amount"], row["vat_amount"], row["total"]))
            generated += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        app.logger.warning("_generate_invoices error: %s", e)
        failed_clients = [name for name in attempted_clients if name not in no_rate_clients]
        return 0, 0, 0, no_rate_clients, failed_clients, str(e)
    return generated, skipped_exists, skipped_overlap, no_rate_clients, failed_clients, ""


@app.route("/invoices/generate", methods=["POST"])
def invoices_generate():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    date_from    = request.form.get("date_from", "").strip()
    date_to      = request.form.get("date_to", "").strip()
    invoice_date = request.form.get("invoice_date", lux_now().strftime("%Y-%m-%d")).strip()
    action       = (request.form.get("gen_action", "preview") or "preview").strip()
    if not date_from or not date_to:
        flash(tr.get("generate_invoice", "Generiši fakturu") + ": datum nedostaje.", "error")
        return redirect("/invoices")

    # ── Step 1: preview (default) ─────────────────────────────────────
    # Read-only classification of every client with shifts in the
    # period. Admin reviews 4 buckets and chooses Confirm (safe) or
    # Force (include overlaps) — nothing is written yet.
    if action == "preview":
        conn = get_conn()
        buckets = _classify_generation_targets(conn, date_from, date_to)
        conn.close()
        # Nothing to do at all → flash and skip the preview page.
        if not (buckets["will_generate"] or buckets["overlapping"]
                or buckets["exact_match"] or buckets["no_rate"]):
            flash(tr.get("inv_gen_nothing_to_do",
                         "Nema klijenata za generisanje u ovom periodu."), "error")
            return redirect(f"/invoices?date_from={date_from}&date_to={date_to}"
                            f"&invoice_date={invoice_date}#invoice-list")
        return render_template_string(BASE_STYLE + header_html() + """
        <style>
          .gp-shell { max-width:980px; margin:24px auto; padding:0 16px; }
          .gp-card { background:{{ '#161618' if dark else '#ffffff' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; border-radius:14px; padding:22px; box-shadow:0 4px 14px rgba(0,0,0,.08); }
          .gp-meta { font-size:13px; color:{{ '#94a3b8' if dark else '#64748b' }}; margin:6px 0 18px; }
          .gp-section { margin-top:18px; }
          .gp-section h3 { margin:0 0 8px; font-size:14px; }
          .gp-table { width:100%; border-collapse:collapse; font-size:13px; }
          .gp-table th, .gp-table td { padding:8px 10px; border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; text-align:left; vertical-align:top; color:{{ '#e2e8f0' if dark else '#1e293b' }}; }
          .gp-table th { font-weight:700; color:{{ '#94a3b8' if dark else '#64748b' }}; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
          .gp-pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:11px; font-weight:700; margin-left:8px; }
          .gp-pill.ok      { background:#16a34a; color:white; }
          .gp-pill.skip    { background:#6b7280; color:white; }
          .gp-pill.warn    { background:#f59e0b; color:#111; }
          .gp-pill.nor     { background:#9ca3af; color:#111; }
          .gp-note { font-size:13px; padding:10px 14px; border-radius:10px; margin-bottom:14px; }
          .gp-note.warn { background:rgba(245,158,11,.15); color:{{ '#fcd34d' if dark else '#92400e' }}; border:1px solid #f59e0b; }
          .gp-amount { text-align:right; font-variant-numeric:tabular-nums; }
          .gp-actions { display:flex; gap:10px; flex-wrap:wrap; margin-top:22px; }
          .gp-btn { flex:1; min-width:180px; padding:12px; border-radius:10px; border:none; cursor:pointer; font-weight:700; font-size:14px; font-family:inherit; }
          .gp-btn.primary { background:#16a34a; color:white; }
          .gp-btn.force   { background:#dc2626; color:white; }
          .gp-btn.cancel  { background:#6b7280; color:white; text-decoration:none; text-align:center; line-height:24px; }
        </style>
        <div class="gp-shell">
          <div class="gp-card">
            <h2>📋 {{ tr.get("inv_gen_preview_title","Pregled prije generisanja faktura") }}</h2>
            <div class="gp-meta">
              {{ date_from }} → {{ date_to }} · {{ tr.get("invoice_date","Datum fakture") }}: {{ invoice_date }}
            </div>

            {% if overlapping %}
            <div class="gp-note warn">
              ⚠ {{ tr.get("inv_gen_overlap_msg","Za ovog klijenta vec postoji faktura ciji se period preklapa sa izabranim.") }}
            </div>
            {% endif %}

            {% if will_generate %}
            <div class="gp-section">
              <h3>✓ {{ tr.get("inv_gen_will_generate","Bice generisano") }}
                <span class="gp-pill ok">{{ will_generate|length }}</span></h3>
              <table class="gp-table"><thead><tr>
                <th>{{ tr.get("client","Klijent") }}</th>
                <th class="gp-amount">{{ tr.get("amount","Iznos") }}</th>
                <th class="gp-amount">{{ tr.get("amount_total","Ukupno") }}</th>
              </tr></thead><tbody>
                {% for r in will_generate %}
                <tr>
                  <td>{{ r.client }}</td>
                  <td class="gp-amount">{{ "%.2f"|format(r.amount or 0) }} €</td>
                  <td class="gp-amount">{{ "%.2f"|format(r.total or 0) }} €</td>
                </tr>
                {% endfor %}
              </tbody></table>
            </div>
            {% endif %}

            {% if overlapping %}
            <div class="gp-section">
              <h3>⚠ {{ tr.get("inv_gen_overlap_warn","Preklapanje sa postojecom fakturom") }}
                <span class="gp-pill warn">{{ overlapping|length }}</span></h3>
              <table class="gp-table"><thead><tr>
                <th>{{ tr.get("client","Klijent") }}</th>
                <th>{{ tr.get("inv_gen_overlap_block","Postojeca faktura") }}</th>
                <th class="gp-amount">{{ tr.get("amount_total","Ukupno") }}</th>
              </tr></thead><tbody>
                {% for r in overlapping %}
                <tr>
                  <td>{{ r.client }}</td>
                  <td><b>#{{ r.existing_invoice }}</b><br>
                      <small>{{ r.existing_from }} → {{ r.existing_to }}</small></td>
                  <td class="gp-amount">{{ "%.2f"|format(r.total or 0) }} €</td>
                </tr>
                {% endfor %}
              </tbody></table>
            </div>
            {% endif %}

            {% if exact_match %}
            <div class="gp-section">
              <h3>= {{ tr.get("inv_gen_exact_skip","Vec postoji (preskoci)") }}
                <span class="gp-pill skip">{{ exact_match|length }}</span></h3>
              <table class="gp-table"><thead><tr>
                <th>{{ tr.get("client","Klijent") }}</th>
                <th>{{ tr.get("invoice_number","Broj fakture") }}</th>
              </tr></thead><tbody>
                {% for r in exact_match %}
                <tr><td>{{ r.client }}</td><td>#{{ r.invoice_number }}</td></tr>
                {% endfor %}
              </tbody></table>
            </div>
            {% endif %}

            {% if no_rate %}
            <div class="gp-section">
              <h3>∅ {{ tr.get("inv_gen_no_rate","Bez postavljene cijene") }}
                <span class="gp-pill nor">{{ no_rate|length }}</span></h3>
              <table class="gp-table"><tbody>
                {% for r in no_rate %}
                <tr><td>{{ r.client }}</td></tr>
                {% endfor %}
              </tbody></table>
            </div>
            {% endif %}

            <form method="post" action="/invoices/generate" class="gp-actions">
              <input type="hidden" name="date_from"    value="{{ date_from }}">
              <input type="hidden" name="date_to"      value="{{ date_to }}">
              <input type="hidden" name="invoice_date" value="{{ invoice_date }}">

              {% if will_generate %}
              <button type="submit" name="gen_action" value="confirm" class="gp-btn primary">
                ✓ {{ tr.get("inv_gen_confirm","Potvrdi generisanje") }}
                ({{ will_generate|length }})
              </button>
              {% endif %}

              {% if overlapping %}
              <button type="submit" name="gen_action" value="force" class="gp-btn force"
                      onclick='return confirm({{ tr.get("inv_gen_force_confirm","Really generate invoices despite the overlapping period?")|tojson }});'>
                ⚠ {{ tr.get("inv_gen_force","Generisi ipak (sa preklapanjima)") }}
                ({{ (will_generate|length) + (overlapping|length) }})
              </button>
              {% endif %}

              <a class="gp-btn cancel"
                 href="/invoices?date_from={{ date_from }}&date_to={{ date_to }}&invoice_date={{ invoice_date }}&skip_auto=1#invoice-list">
                {{ tr.get("inv_gen_cancel","Odustani") }}
              </a>
            </form>
          </div>
        </div>
        """, tr=tr, dark=dark,
             date_from=date_from, date_to=date_to, invoice_date=invoice_date,
             will_generate=buckets["will_generate"],
             exact_match=buckets["exact_match"],
             overlapping=buckets["overlapping"],
             no_rate=buckets["no_rate"])

    # ── Step 2: confirm or force ──────────────────────────────────────
    force = (action == "force")
    conn = get_conn()
    (generated, skipped_exists, skipped_overlap,
     no_rate_clients, failed_clients, error_message) = _generate_invoices(
         conn, date_from, date_to, invoice_date, force=force
     )
    conn.close()
    parts = []
    if generated:
        parts.append(tr.get("inv_gen_ok", "{n} faktura generisano").replace("{n}", str(generated)))
    if skipped_exists:
        parts.append(tr.get("inv_gen_exists", "{n} već postoji za ovaj period").replace("{n}", str(skipped_exists)))
    if skipped_overlap:
        parts.append(tr.get("inv_gen_overlap_block", "Nije generisano zbog preklapanja")
                     + ": " + str(skipped_overlap))
    if not parts:
        parts.append(tr.get("inv_gen_empty", "Nema smjena u odabranom periodu."))
    if no_rate_clients:
        parts.append(tr.get("inv_gen_no_rate", "Bez postavljene cijene") + ": " + ", ".join(no_rate_clients))
    if failed_clients:
        parts.append(tr.get("inv_gen_failed", "Nije uspjelo upisivanje") + ": " + ", ".join(failed_clients))
    if error_message:
        parts.append("DB: " + error_message[:180])
    flash("; ".join(parts),
          "error" if generated == 0 and (failed_clients or not skipped_exists) else "ok")
    return redirect(f"/invoices?date_from={date_from}&date_to={date_to}&invoice_date={invoice_date}#invoice-list")


@app.route("/invoices/client")
def invoices_client():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    client = request.args.get("client", "").strip()
    # Default: empty date range = show ALL invoices for this client
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    status = request.args.get("status", "all").strip()
    doc_filter = request.args.get("doc", "all").strip()
    conn = get_conn()
    # Align with the "Statement PDF" button on this same page —
    # /invoices/client_statement already uses date_basis="work_period".
    # Without this, picking "May" in the page filter could show a
    # different set of invoices in the table than the PDF the button
    # generates (a June-issued invoice for May's shifts would land in
    # one set and not the other).
    rows = fetch_invoice_records(
        conn, date_from or None, date_to or None, client, status,
        date_basis="work_period",
    )
    conn.close()
    # Document filter (auto = invoice, manual = manual invoice — both render same here)
    if doc_filter == "facture":
        rows = [r for r in rows if r.get("source", "auto") != "manual"]
    elif doc_filter == "manual":
        rows = [r for r in rows if r.get("source", "auto") == "manual"]
    total_paid = sum(r["total"] for r in rows if r["paid"])
    total_unpaid = sum(r["total"] for r in rows if not r["paid"])
    total_all = sum(r["total"] for r in rows)
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
        .invoice-shell { background:{{ '#161618' if dark else '#ffffff' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }}; border-radius:10px; padding:24px; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
        .invoice-panel { max-width:1280px; margin:0 auto; background:{{ '#191919' if dark else '#f8fafc' }}; border-radius:8px; padding:22px 30px; color:{{ '#e2e8f0' if dark else '#1e293b' }}; }
        .invoice-panel h1, .invoice-panel h2, .invoice-panel h3 { color:{{ '#e2e8f0' if dark else '#1e293b' }} !important; }
        .doc-tabs { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:16px; }
        .doc-tab { background:{{ '#222225' if dark else '#cbd5e1' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }}; padding:12px 16px; border-radius:8px 8px 0 0; font-weight:bold; text-decoration:none; }
        .doc-tab.active { background:{{ '#191919' if dark else '#1f4f82' }}; color:white; }
        .invoice-table { width:100%; border-collapse:collapse; color:{{ '#e2e8f0' if dark else '#1e293b' }}; }
        .invoice-table th, .invoice-table td { padding:14px 10px; border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; text-align:left; }
        .invoice-table th { text-transform:uppercase; font-size:13px; color:{{ '#94a3b8' if dark else '#475569' }}; }
        .invoice-table a { color:{{ '#93c5fd' if dark else '#1f4f82' }}; text-decoration:underline; }
        .paid-text { color:{{ '#4ade80' if dark else '#16a34a' }}; font-weight:bold; } .unpaid-text { color:{{ '#fb7185' if dark else '#dc2626' }}; font-weight:bold; }
        .filters { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:14px; margin:18px 0 28px; align-items:end; }
        .filters label { display:block; font-size:12px; opacity:.85; margin-bottom:4px; }
        .filters input, .filters select { width:100%; }
        .totals { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-top:18px; }
        .total-card { background:{{ '#1d1d1f' if dark else '#f1f5f9' }}; border-radius:8px; padding:14px; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
        .total-row { display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; font-size:14px; }
        .total-row:last-child { border-bottom:none; font-weight:bold; }
        .summary-block { margin-top:24px; padding:14px 20px; background:{{ '#1d1d1f' if dark else '#f1f5f9' }}; border-radius:8px; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
    </style>
    <div class="invoice-shell">
        <div class="doc-tabs">
            <a class="doc-tab" href="/documents">{{ tr.get("my_documents","Mes documents") }}</a>
            <a class="doc-tab" href="/clients">{{ tr.get("my_clients","Mes clients") }}</a>
            <a class="doc-tab" href="/invoices/export_options?type=list">{{ tr.get("my_reports","Mes rapports") }}</a>
            <span class="doc-tab active">📁 {{ client }} <a href="/invoices" style="color:white;margin-left:8px;text-decoration:none;">×</a></span>
        </div>
        <div class="invoice-panel">
            <div style="display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap;">
                <h2 style="margin:0;color:white;">📁 {{ tr.get("client_documents_of","Documents de") }} {{ client }}
                    <span style="background:#111;border-radius:999px;padding:2px 10px;font-size:13px;margin-left:6px;">{{ rows|length }}</span>
                </h2>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                  <a href="/invoices/client_statement?client={{ client|urlencode }}&date_from={{ date_from|urlencode }}&date_to={{ date_to|urlencode }}&status={{ status|urlencode }}&doc={{ doc_filter|urlencode }}" style="background:{{ '#1d4ed8' if dark else '#1f4f82' }};color:white;padding:10px 14px;border-radius:6px;text-decoration:none;font-weight:600;">📄 {{ tr.get("client_statement_pdf","Releve de compte client PDF") }}</a>
                  {% if total_unpaid > 0 %}
                  <a href="/invoices/reminder?client={{ client|urlencode }}" style="background:#f97316;color:white;padding:10px 14px;border-radius:6px;text-decoration:none;font-weight:600;">⬇ {{ tr.get("download_reminder","Rappel PDF") }}</a>
                  <a href="/invoices/email?client={{ client|urlencode }}&type=reminder" style="background:#fb923c;color:white;padding:10px 14px;border-radius:6px;text-decoration:none;font-weight:600;">📮 {{ tr.get("send_reminder_email","Poslati Rappel emailom") }}</a>
                  {% endif %}
                </div>
            </div>
            <form class="filters" method="get" action="/invoices/client">
                <input type="hidden" name="client" value="{{ client }}">
                <div>
                    <label>{{ tr.get("date_from","Date du") }}</label>
                    <input type="date" name="date_from" value="{{ date_from }}">
                </div>
                <div>
                    <label>{{ tr.get("date_to","Date au") }}</label>
                    <input type="date" name="date_to" value="{{ date_to }}">
                </div>
                <div>
                    <label>{{ tr.get("payment_status","Statut") }}</label>
                    <select name="status">
                        <option value="all"    {% if status == 'all'    %}selected{% endif %}>{{ tr.get("all_filter","--Tous--") }}</option>
                        <option value="paid"   {% if status == 'paid'   %}selected{% endif %}>{{ tr["paid"] }}</option>
                        <option value="unpaid" {% if status == 'unpaid' %}selected{% endif %}>{{ tr["unpaid"] }}</option>
                    </select>
                </div>
                <div>
                    <label>{{ tr.get("document","Document") }}</label>
                    <select name="doc">
                        <option value="all"     {% if doc_filter == 'all'     %}selected{% endif %}>{{ tr.get("all_filter","--Tous--") }}</option>
                        <option value="facture" {% if doc_filter == 'facture' %}selected{% endif %}>{{ tr.get("invoices","Fakture") }}</option>
                        <option value="manual"  {% if doc_filter == 'manual'  %}selected{% endif %}>✏️ {{ tr.get("mi_title","Facture manuelle") }}</option>
                    </select>
                </div>
                <div><button>{{ tr.get("search_btn","Rechercher") }}</button></div>
            </form>
            <table class="invoice-table">
                <tr>
                    <th>{{ tr.get("client_name","Client") }}</th>
                    <th>{{ tr.get("document","Document") }}</th>
                    <th>{{ tr.get("invoice_number","Numéro") }}</th>
                    <th>{{ tr.get("invoice_date","Date") }}</th>
                    <th>{{ tr.get("paid","Payé") }}</th>
                    <th>{{ tr.get("amount_with_vat","Montant") }}</th>
                </tr>
                {% for row in rows %}
                <tr>
                    <td>{{ row.client }}</td>
                    <td>{{ tr["invoices"] }}{% if row.source == 'manual' %} <span style="font-size:10px;background:#22c55e;color:#111;padding:1px 5px;border-radius:4px;">✏️</span>{% endif %}</td>
                    <td>
                      <a href="/invoices/view?invoice_number={{ row.invoice_number }}" style="color:{% if row.source == 'manual' %}{{ '#ffd429' if dark else '#b45309' }}{% else %}{{ '#93c5fd' if dark else '#1f4f82' }}{% endif %};text-decoration:underline;font-weight:600;">{{ row.invoice_number }}</a>
                    </td>
                    <td>{{ format_date(row.invoice_date) }}</td>
                    <td class="{{ 'paid-text' if row.paid else 'unpaid-text' }}">{{ "%.2f"|format(row.total if row.paid else 0) }} €</td>
                    <td><b>{{ "%.2f"|format(row.total) }} €</b></td>
                </tr>
                {% endfor %}
            </table>
            {% if rows|length == 0 %}<p class="muted" style="padding:24px 4px;">{{ tr.get("no_invoices_period","Nema faktura za izabrani period.") }}</p>{% endif %}

            <!-- Summary block (matches reference invoice client page) -->
            <div class="summary-block">
                <div class="total-row">
                    <span>{{ tr.get("amount_total","Montant total")|upper }}</span>
                    <b>{{ "%.2f"|format(total_all) }} EUR</b>
                </div>
                <div class="total-row">
                    <span>{{ tr.get("amount_paid","Montant payé")|upper }}</span>
                    <span class="paid-text">{{ "%.2f"|format(total_paid) }} EUR</span>
                </div>
                <div class="total-row">
                    <span>{{ tr.get("balance_due","Solde dû")|upper }}</span>
                    <span class="unpaid-text">{{ "%.2f"|format(total_unpaid) }} EUR</span>
                </div>
            </div>
        </div>
    </div>
    """, tr=tr, dark=dark, client=client, rows=rows, date_from=date_from, date_to=date_to, status=status, doc_filter=doc_filter, format_date=format_date, total_paid=total_paid, total_unpaid=total_unpaid, total_all=total_all)


@app.route("/invoices/view")
def invoices_view():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    invoice_number = request.args.get("invoice_number", "").strip()
    conn = get_conn(); c = conn.cursor()
    record_row = c.execute("""
        SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date, COALESCE(sent,0), COALESCE(sent_date,''), COALESCE(source,'auto')
        FROM invoice_records WHERE invoice_number = ? AND COALESCE(deleted, 0) = 0
    """, (invoice_number,)).fetchone()
    if not record_row:
        conn.close()
        return redirect("/invoices")
    record = invoice_record_to_dict(record_row)
    is_manual = record.get("source") == "manual"

    if is_manual:
        # Manual invoice — read draft, no shift reconstruction needed.
        # Bail out cleanly if the draft is missing (e.g. partially deleted),
        # otherwise the iframe would silently redirect to the empty form.
        draft_exists = c.execute(
            "SELECT 1 FROM manual_invoice_drafts WHERE invoice_number=?",
            (invoice_number,)
        ).fetchone()
        if not draft_exists:
            conn.close()
            flash(tr.get("invoice_not_found", "Faktura nije pronadjena."), "error")
            return redirect("/invoices")
        settings = get_invoice_settings(conn)
        conn.close()
        row = {
            "invoice_number": invoice_number,
            "client":         record["client"],
            "amount":         record["amount"],
            "vat_amount":     record["vat_amount"],
            "total":          record["total"],
            "paid":           record["paid"],
            "sent":           record.get("sent", False),
        }
        pdf_url      = f"/invoices/manual/pdf?invoice_number={urllib.parse.quote(invoice_number)}&inline=1"
        download_url = f"/invoices/manual/pdf?invoice_number={urllib.parse.quote(invoice_number)}"
        edit_url     = f"/invoices/manual?invoice_number={urllib.parse.quote(invoice_number)}"
    else:
        row, settings = get_invoice_row_for_record(conn, record)
        conn.close()
        if not row:
            # No current-plan row for this record's client in its work
            # period. Pre-fix we silently swapped in whatever row
            # happened to share the same generated invoice_number (the
            # #4385 → TELUS bug). Tell the admin instead of showing a
            # stranger's invoice.
            flash(
                tr.get(
                    "invoice_cannot_rebuild",
                    "Faktura #{n} se ne moze rekonstruisati iz trenutnog plana "
                    "(klijent nema smjena u periodu). Provjerite plan ili "
                    "obrisite fakturu i regenerisite je."
                ).replace("{n}", str(invoice_number)),
                "error",
            )
            return redirect("/invoices")
        pdf_url      = f"/invoices/preview_pdf?invoice_number={urllib.parse.quote(invoice_number)}"
        download_url = f"/invoices/download?invoice_number={urllib.parse.quote(invoice_number)}&client={urllib.parse.quote(row['client'])}&date_from={record['date_from']}&date_to={record['date_to']}&invoice_date={record['invoice_date']}"
        edit_url     = "/invoices#invoice-profiles"   # auto invoices edit via settings panel

    # Mark paid/sent now go through POST forms instead of GET links so a
    # stray prefetch or third-party rel=preconnect can't silently flip
    # invoice state. The template builds <form> + hidden inputs from these
    # dicts; the same data used to live in URL query params.
    next_back = f"/invoices/view?invoice_number={invoice_number}"
    paid_fields = {
        "invoice_number": invoice_number,
        "paid":           "0" if record['paid'] else "1",
        "client":         row['client'],
        "date_from":      record.get('date_from', ''),
        "date_to":        record.get('date_to', ''),
        "invoice_date":   record.get('invoice_date', ''),
        "amount":         row['amount'],
        "vat_amount":     row['vat_amount'],
        "total":          row['total'],
        "next":           next_back,
    }
    sent_fields = {
        "invoice_number": invoice_number,
        "sent":           "0" if record.get('sent') else "1",
        "next":           next_back,
    }

    # Build HTML preview context (shared data with PDF builders)
    conn2 = get_conn()
    view_ctx = _invoice_view_context(conn2, record)

    # Plan-vs-invoice mismatch detection. Manual invoices carry a
    # frozen items_json snapshot; if the admin edited shifts after
    # saving the manual, the invoice can silently drift from what
    # the current plan says the client actually worked. Three
    # independent triggers so a coincidentally-equal total doesn't
    # mask a wrong-dates draft:
    #   (a) |plan HT - stored HT| > 0.50 EUR
    #   (b) |plan hours - stored hours| > 0.10 h
    #       (hours parsed from the "Total N h" line if present)
    #   (c) stored first-item designation != expected designation
    plan_summary  = plan_summary_for_record(conn2, record) if is_manual else None
    plan_mismatch = False
    stored_hours  = None
    stored_desig  = ""
    expected_desig = ""
    if plan_summary is not None:
        stored_ht = round(float(record.get("amount") or 0), 2)
        plan_ht   = plan_summary["amount"]
        # Pull the manual draft's first-item designation for the
        # designation / hours comparison. Best-effort: any parse
        # failure just falls back to the HT-only trigger.
        try:
            draft_row = conn2.cursor().execute(
                "SELECT items_json FROM manual_invoice_drafts WHERE invoice_number=?",
                (invoice_number,)
            ).fetchone()
            if draft_row and draft_row[0]:
                stored_items = json.loads(draft_row[0])
                if stored_items:
                    stored_desig = (stored_items[0].get("designation") or "").strip()
        except Exception:
            stored_desig = ""
        # "Total 6h" / "Total 6,00h" / "Total 6.00 h" — extract the
        # first number after "Total" if present.
        try:
            m = re.search(
                r"total\s+([0-9]+(?:[.,][0-9]+)?)\s*h",
                stored_desig, re.IGNORECASE
            )
            if m:
                stored_hours = float(m.group(1).replace(",", "."))
        except Exception:
            stored_hours = None
        try:
            expected_desig = invoice_designation_text(plan_summary["row"]).strip()
        except Exception:
            expected_desig = ""
        ht_off       = abs(plan_ht - stored_ht) > 0.5
        hours_off    = (stored_hours is not None
                        and abs(plan_summary["hours"] - stored_hours) > 0.10)
        # Case-fold + collapse runs of whitespace before comparing so
        # a purely cosmetic edit (extra spaces, uppercased month
        # name, trailing newline) doesn't nag the admin forever
        # while HT and hours already match. Real content drifts
        # (different dates, different hours per shift) still trip
        # the comparison because those change actual tokens.
        def _norm(s):
            return " ".join((s or "").split()).lower()
        desig_off    = (bool(stored_desig) and bool(expected_desig)
                        and _norm(stored_desig) != _norm(expected_desig))
        plan_mismatch = ht_off or hours_off or desig_off
        # Expose the individual reasons + raw texts so the template
        # can (a) explain to the admin which field actually drifted
        # and (b) offer a side-by-side "Voir différences" panel.
        # When ONLY the designation differs (HT and hours match) the
        # banner drops to a softer "text differs" tone rather than
        # the loud "invoice no longer matches plan" warning.
        mismatch_info = {
            "ht_off":         ht_off,
            "hours_off":      hours_off,
            "desig_off":      desig_off,
            "text_only":      (desig_off and not ht_off and not hours_off),
            "stored_ht":      stored_ht,
            "plan_ht":        plan_ht,
            "stored_hours":   stored_hours,
            "plan_hours":     plan_summary["hours"],
            "stored_desig":   stored_desig,
            "expected_desig": expected_desig,
        }
    else:
        mismatch_info = None
    # Email proof / archive trail for this invoice — most recent first.
    # We intentionally pull every column we know about so the UI can
    # surface Message-ID and PDF hash as forensic evidence the email
    # actually left the server and contained THIS exact attachment.
    try:
        log_rows = conn2.cursor().execute("""
            SELECT recipient, subject, status, error, sent_at,
                   COALESCE(message_id,''), COALESCE(attachment_sha256,''),
                   COALESCE(imap_saved,0), COALESCE(imap_error,'')
            FROM invoice_email_logs
            WHERE invoice_number = ?
            ORDER BY id DESC
            LIMIT 20
        """, (invoice_number,)).fetchall()
    except Exception as _log_err:
        # Don't 500 the viewer if logs table is mid-migration on a fresh DB
        app.logger.warning("email log fetch failed: %s", _log_err)
        log_rows = []
    conn2.close()
    email_logs = [{
        "recipient": r[0] or "",
        "subject":   r[1] or "",
        "status":    r[2] or "",
        "error":     r[3] or "",
        "sent_at":   r[4] or "",
        "message_id":         r[5] or "",
        "attachment_sha256":  r[6] or "",
        "imap_saved": bool(r[7]),
        "imap_error": r[8] or "",
    } for r in log_rows]

    return render_template_string(BASE_STYLE + header_html() + """
    <style>
        /* Outer shell — neutral page chrome */
        .viewer-shell { background:{{ '#161618' if dark else '#f5f7fa' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }}; border-radius:10px; padding:18px; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
        .viewer-panel { max-width:1280px; margin:0 auto; }
        /* Tabs match invoicehome cream/white style in light theme */
        .doc-tabs { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:0; }
        .doc-tab { background:{{ '#222225' if dark else '#fde68a' }}; color:{{ '#e2e8f0' if dark else '#78350f' }}; padding:10px 14px; border-radius:8px 8px 0 0; font-weight:700; text-decoration:none; font-size:13px; }
        .doc-tab.active { background:{{ '#191919' if dark else '#ffffff' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; border-bottom:none; }
        /* Toolbar buttons row */
        .toolbar { display:flex; flex-wrap:wrap; gap:4px; padding:14px 14px 0; background:{{ '#191919' if dark else '#ffffff' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; border-bottom:none; border-radius:0; }
        .tool { background:{{ '#2c2c30' if dark else '#f1f5f9' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }}; border-radius:8px 8px 0 0; padding:10px 14px; font-weight:700; text-decoration:none; font-size:13px; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; border-bottom:none; }
        button.tool { font-family:inherit; cursor:pointer; line-height:normal; }
        .tool.active { background:{{ '#0f0f10' if dark else '#1f4f82' }}; color:white; }
        .tool.pay { background:{{ '#16a34a' if record.paid else '#ef4444' }}; color:white; border-color:transparent; }
        .tool.send-toggle { background:{{ '#16a34a' if record.sent else '#ef4444' }}; color:white; border-color:transparent; }
        .tool.email-btn { background:#0ea5e9; color:white; border-color:transparent; }
        .tool.dl { background:#1f4f82; color:white; border-color:transparent; }

        /* ── Invoice "paper" preview ──────────────────────────────── */
        .invoice-stage { background:{{ '#0f0f10' if dark else '#e5e7eb' }}; padding:32px 16px; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; border-radius:0 0 10px 10px; }
        .invoice-paper {
            background:#ffffff; color:#111827;
            max-width:920px; margin:0 auto;
            padding:36px 44px;
            border-radius:4px;
            box-shadow:0 12px 36px rgba(0,0,0,0.18), 0 0 0 1px rgba(0,0,0,0.04);
            font-family:'Helvetica Neue', Arial, sans-serif;
            font-size:13px; line-height:1.55;
        }
        .ip-header { background:{{ view_ctx.accent }}; color:#ffffff; padding:18px 24px; border-radius:4px; display:flex; align-items:center; justify-content:space-between; gap:48px; }
        .ip-brand { font-size:22px; font-weight:800; }
        .ip-doc-type { font-size:22px; font-weight:800; }
        .ip-co-row { display:flex; gap:24px; align-items:flex-start; margin:24px 0 30px; }
        .ip-co-info { flex:1; white-space:pre-line; font-size:13px; }
        .ip-co-logo img { max-height:64px; max-width:160px; object-fit:contain; }
        .ip-bill-row { display:flex; gap:24px; margin-bottom:24px; }
        .ip-billed-to, .ip-meta { flex:1; }
        .ip-billed-to b, .ip-meta b { display:inline-block; margin-bottom:4px; color:#111827; font-weight:700; font-size:13px; }
        .ip-meta { text-align:right; }
        .ip-meta .ip-meta-row { display:flex; justify-content:flex-end; gap:14px; margin-bottom:3px; }
        .ip-meta .ip-meta-row b { min-width:80px; text-align:left; }
        .ip-client-name { font-weight:700; }
        .ip-client-addr { white-space:pre-line; }

        .ip-table { width:100%; border-collapse:collapse; margin:20px 0 0; }
        .ip-table th, .ip-table td { padding:12px 14px; text-align:left; border-bottom:1px solid #e5e7eb; vertical-align:top; font-size:13px; }
        .ip-table thead th { background:#f3f4f6; color:#374151; font-weight:700; text-transform:uppercase; font-size:12px; letter-spacing:0.04em; }
        .ip-table .ip-amount-col { text-align:right; min-width:140px; }
        /* Force dark text on the white invoice paper even when the page is
           in dark theme — without this the dark-theme body color (#e5e7eb)
           was cascading down to the tbody rows in some browsers and
           rendering "Désignation" lines as nearly-invisible light gray on
           the otherwise-white invoice preview. */
        .ip-table .ip-desig { white-space:pre-line; color:#111827; }
        .ip-table tbody td { color:#111827; }
        .ip-totals td { padding:8px 14px; }
        .ip-total-label { text-align:right; color:#374151; }
        .ip-total-amount { text-align:right; font-weight:600; min-width:140px; }
        .ip-total-ttc td { font-weight:800; font-size:16px; padding:14px; }
        .ip-total-ttc .ip-total-label { color:#111827; background:#ffffff; }
        .ip-total-ttc .ip-total-amount { color:#111827; background:#f3f4f6; }

        .ip-pay { margin-top:36px; padding-top:18px; border-top:1px solid #e5e7eb; }
        .ip-pay b { display:block; margin-bottom:6px; color:#111827; }
        .ip-pay-body { color:#374151; font-size:12.5px; line-height:1.7; }

        /* Status pill in the toolbar (replaces the old overlay stamp
           which used to land on top of the company logo). */
        .ip-status-pill {
            display:inline-flex; align-items:center; gap:6px;
            margin-left:auto; padding:8px 14px; border-radius:999px;
            font-weight:800; font-size:13px; letter-spacing:0.04em;
            text-transform:uppercase;
        }
        .ip-status-pill.paid   { background:#dcfce7; color:#15803d; border:1px solid #86efac; }
        .ip-status-pill.unpaid { background:#fee2e2; color:#b91c1c; border:1px solid #fca5a5; }

        .ip-download-cta {
            display:block; max-width:920px; margin:18px auto 0;
            background:#1f4f82; color:white;
            padding:14px 18px; border-radius:8px;
            text-align:center; text-decoration:none;
            font-weight:800; font-size:15px;
            box-shadow:0 4px 14px rgba(31,79,130,0.3);
        }
        .ip-download-cta:hover { background:#16395f; }

        @media (max-width:720px){
            .invoice-paper { padding:22px 18px; font-size:12.5px; }
            .ip-bill-row, .ip-co-row { flex-direction:column; }
            .ip-meta { text-align:left; }
            .ip-meta .ip-meta-row { justify-content:flex-start; }
        }
        @media print {
            .doc-tabs, .toolbar, .ip-download-cta, .sidebar, .topbar, .bottom-nav, .brandbar { display:none !important; }
            .invoice-stage, .viewer-shell, .viewer-panel { background:white !important; padding:0 !important; border:none !important; box-shadow:none !important; }
            .invoice-paper { box-shadow:none !important; max-width:none !important; }
        }
    </style>
    <div class="viewer-shell">
        <div class="doc-tabs">
            <a class="doc-tab" href="/documents">{{ tr.get("my_documents","Mes documents") }}</a>
            <a class="doc-tab" href="/clients">{{ tr.get("my_clients","Mes clients") }}</a>
            <a class="doc-tab" href="/invoices/export_options?type=list">{{ tr.get("my_reports","Mes rapports") }}</a>
            <a class="doc-tab" href="/invoices/client?client={{ row.client|urlencode }}">📁 {{ row.client }}</a>
            <span class="doc-tab active">{{ row.invoice_number }} <a href="/invoices/client?client={{ row.client|urlencode }}" style="color:inherit;margin-left:8px;text-decoration:none;opacity:.6;">×</a></span>
        </div>
        <div class="viewer-panel">
            <div class="toolbar">
                <span class="tool active">{{ tr.get("invoices","Facture") }}{% if is_manual %} ✏️{% endif %}</span>
                {% if not is_manual %}
                <a class="tool" href="/invoices/devis_pdf?invoice_number={{ row.invoice_number }}">{{ tr["quote"] }}</a>
                {% endif %}
                <a class="tool" href="{{ edit_url }}">{{ tr.get("edit","Modifier") }}</a>
                <form method="post" action="/invoices/delete" style="display:inline;margin:0;" onsubmit='return confirm({{ tr.get("invoice_delete_confirm","Obrisati ovu fakturu?")|tojson }});'>
                  <input type="hidden" name="invoice_number" value="{{ row.invoice_number }}">
                  <button type="submit" class="tool">{{ tr.get("delete","Supprimer") }}</button>
                </form>
                <form method="post" action="/invoices/mark_paid" style="display:inline;margin:0;">
                  {% for k, v in paid_fields.items() %}<input type="hidden" name="{{ k }}" value="{{ v }}">{% endfor %}
                  <button type="submit" class="tool pay">{{ tr["mark_unpaid"] if record.paid else tr["mark_paid"] }}</button>
                </form>
                <form method="post" action="/invoices/mark_sent" style="display:inline;margin:0;">
                  {% for k, v in sent_fields.items() %}<input type="hidden" name="{{ k }}" value="{{ v }}">{% endfor %}
                  <button type="submit" class="tool send-toggle">{{ tr["mark_unsent"] if record.sent else tr["mark_sent"] }}</button>
                </form>
                <a class="tool email-btn" href="/invoices/email?invoice_number={{ row.invoice_number }}">✉ {{ tr.get("send_email","Envoyer") }}</a>
                {% if not record.paid %}
                <a class="tool" style="background:#f97316;color:white;border-color:transparent;" href="/invoices/email?invoice_number={{ row.invoice_number }}&type=reminder">📮 {{ tr.get("send_reminder","Rappel") }}</a>
                <a class="tool" style="background:#fb923c;color:white;border-color:transparent;" href="/invoices/reminder?invoice_number={{ row.invoice_number }}">⬇ {{ tr.get("download_reminder","Rappel PDF") }}</a>
                {% endif %}
                <a class="tool dl" href="{{ download_url }}">⬇ {{ tr.get("download","Telecharger") }}</a>
                <span class="ip-status-pill {{ 'paid' if record.paid else 'unpaid' }}">
                  {% if record.paid %}● {{ tr["paid"] }}{% else %}○ {{ tr["unpaid"] }}{% endif %}
                </span>
            </div>

            {% if is_manual and plan_mismatch and plan_summary and mismatch_info %}
            {# text_only = only designation differs → softer blue notice.
               Any HT or hours drift → louder yellow warning. #}
            {% set _soft   = mismatch_info.text_only %}
            {% set _bg     = ('rgba(59,130,246,.15)' if dark else '#dbeafe') if _soft else ('rgba(245,158,11,.15)' if dark else '#fef3c7') %}
            {% set _fg     = ('#93c5fd' if dark else '#1e3a8a')             if _soft else ('#fcd34d' if dark else '#92400e') %}
            {% set _border = '#3b82f6' if _soft else '#f59e0b' %}
            {% set _btnbg  = '#3b82f6' if _soft else '#f59e0b' %}
            {% set _icon   = 'ℹ' if _soft else '⚠' %}
            <div style="margin:12px 0; padding:12px 16px; border-radius:10px;
                        background:{{ _bg }}; color:{{ _fg }};
                        border:1px solid {{ _border }}; font-size:13px;">
              <div style="font-weight:700; margin-bottom:6px;">
                {{ _icon }}
                {% if _soft %}
                  {{ tr.get("invoice_plan_mismatch_text_only_title","Iznos je isti, ali tekst/detalji fakture se razlikuju od trenutnog plana.") }}
                {% else %}
                  {{ tr.get("invoice_plan_mismatch_title","Ova ručna faktura se ne poklapa sa trenutnim planom za ovaj period.") }}
                {% endif %}
              </div>

              {# Reason chips: show exactly which field(s) drifted. #}
              <div style="font-size:11px; margin-bottom:8px; display:flex; flex-wrap:wrap; gap:6px;">
                {% if mismatch_info.ht_off %}
                <span style="background:rgba(220,38,38,.15); color:#dc2626; padding:2px 8px; border-radius:999px; font-weight:700;">
                  {{ tr.get("invoice_reason_ht","HT razlika") }}: {{ '%.2f'|format(mismatch_info.stored_ht) }} → {{ '%.2f'|format(mismatch_info.plan_ht) }} €
                </span>
                {% endif %}
                {% if mismatch_info.hours_off %}
                <span style="background:rgba(220,38,38,.15); color:#dc2626; padding:2px 8px; border-radius:999px; font-weight:700;">
                  {{ tr.get("invoice_reason_hours","Sati razlika") }}:
                  {% if mismatch_info.stored_hours is not none %}{{ '%.2f'|format(mismatch_info.stored_hours) }}{% else %}?{% endif %}
                  → {{ '%.2f'|format(mismatch_info.plan_hours) }} h
                </span>
                {% endif %}
                {% if mismatch_info.desig_off %}
                <span style="background:rgba(37,99,235,.15); color:#1d4ed8; padding:2px 8px; border-radius:999px; font-weight:700;">
                  {{ tr.get("invoice_reason_text","Tekst/detalji fakture") }}
                </span>
                {% endif %}
              </div>

              {# Compact totals line, always shown so the admin has HT + hours at a glance. #}
              <div style="font-size:12px; margin-bottom:8px;">
                {{ tr.get("invoice_plan_mismatch_stored","Sacuvano na fakturi") }}:
                <b>{{ '%.2f'|format(record.amount) }} € HT</b>
                &nbsp;·&nbsp;
                {{ tr.get("invoice_plan_mismatch_plan","Trenutni plan") }}:
                <b>{{ '%.2f'|format(plan_summary.amount) }} € HT</b>
                ({{ '%.2f'|format(plan_summary.hours) }} h)
              </div>

              {# "Voir différences" expandable side-by-side. Only worth
                 showing when the designation actually drifted. #}
              {% if mismatch_info.desig_off and mismatch_info.stored_desig and mismatch_info.expected_desig %}
              <details style="margin:0 0 8px;">
                <summary style="cursor:pointer; font-size:12px; font-weight:700;">
                  🔍 {{ tr.get("invoice_view_diff","Vidi razlike") }}
                </summary>
                <div class="invmm-diff-grid" style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px;">
                  <div>
                    <div style="font-size:11px; opacity:.75; margin-bottom:3px;">
                      {{ tr.get("invoice_diff_saved","Faktura sacuvana") }}
                    </div>
                    <pre style="white-space:pre-wrap; font-size:11px; font-family:ui-monospace,Menlo,Consolas,monospace; padding:8px; border-radius:6px; background:rgba(0,0,0,.06); color:{{ '#e2e8f0' if dark else '#0f172a' }}; margin:0; max-height:180px; overflow:auto;">{{ mismatch_info.stored_desig }}</pre>
                  </div>
                  <div>
                    <div style="font-size:11px; opacity:.75; margin-bottom:3px;">
                      {{ tr.get("invoice_diff_plan","Trenutni plan") }}
                    </div>
                    <pre style="white-space:pre-wrap; font-size:11px; font-family:ui-monospace,Menlo,Consolas,monospace; padding:8px; border-radius:6px; background:rgba(0,0,0,.06); color:{{ '#e2e8f0' if dark else '#0f172a' }}; margin:0; max-height:180px; overflow:auto;">{{ mismatch_info.expected_desig }}</pre>
                  </div>
                </div>
              </details>
              {% endif %}

              <form method="post" action="/invoices/manual/rebuild" style="display:inline;"
                    onsubmit='return confirm({{ (tr.get("invoice_plan_mismatch_confirm","Obnoviti prvu (usluga) stavku iz trenutnog plana? Dodatne rucne stavke ostaju sacuvane. Broj fakture, datum izdavanja i status placanja/slanja ostaju nepromijenjeni.") + ((" [PAID/SENT]" if (record.paid or record.sent) else "")))|tojson }});'>
                <input type="hidden" name="invoice_number" value="{{ record.invoice_number }}">
                <button type="submit"
                        style="background:{{ _btnbg }}; color:white; border:none;
                               border-radius:8px; padding:8px 14px; font-weight:700;
                               font-size:13px; cursor:pointer; font-family:inherit;">
                  🔄 {{ tr.get("invoice_rebuild_from_plan","Obnovi stavke iz plana") }}
                </button>
              </form>
              {% if record.paid or record.sent %}
              <div style="margin-top:6px; font-size:11px; color:#dc2626;">
                ⚠ {{ tr.get("invoice_rebuild_paid_sent_warn","Faktura je vec placena/poslana — obnavljanje zamijenjuje stavke ali cuva paid/sent status.") }}
              </div>
              {% endif %}
            </div>
            {% endif %}

            <div class="invoice-stage">
              {% if view_ctx %}
              <article class="invoice-paper">
                <header class="ip-header">
                  <div class="ip-brand">{{ view_ctx.company_name }}</div>
                  <div class="ip-doc-type">FACTURE</div>
                </header>

                <section class="ip-co-row">
                  <div class="ip-co-info">{{ view_ctx.company_address }}{% if view_ctx.company_phone %}
Tel: {{ view_ctx.company_phone }}{% endif %}{% if view_ctx.company_email %}
{{ view_ctx.company_email }}{% endif %}</div>
                  <div class="ip-co-logo">
                    <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo" onerror="this.style.display='none'">
                  </div>
                </section>

                <section class="ip-bill-row">
                  <div class="ip-billed-to">
                    <b>Facturé à</b>
                    <div class="ip-client-name">{{ view_ctx.client_name or '-' }}</div>
                    <div class="ip-client-addr">{{ view_ctx.client_address or '-' }}</div>
                  </div>
                  <div class="ip-meta">
                    <div class="ip-meta-row"><b>Facture n°</b> <span>{{ view_ctx.invoice_number }}</span></div>
                    <div class="ip-meta-row"><b>Date</b> <span>{{ view_ctx.invoice_date }}</span></div>
                  </div>
                </section>

                <table class="ip-table">
                  <thead>
                    <tr><th>DÉSIGNATION</th><th class="ip-amount-col">MONTANT</th></tr>
                  </thead>
                  <tbody>
                    {% for it in view_ctx['items'] %}
                    <tr>
                      <td class="ip-desig">{{ it.designation }}</td>
                      <td class="ip-amount-col">{{ "%.2f"|format(it.amount) }}</td>
                    </tr>
                    {% endfor %}
                  </tbody>
                  <tfoot>
                    <tr class="ip-totals">
                      <td class="ip-total-label">Total HT</td>
                      <td class="ip-total-amount">{{ "%.2f"|format(view_ctx.total_ht) }}</td>
                    </tr>
                    <tr class="ip-totals">
                      <td class="ip-total-label">{{ view_ctx.vat_label }}</td>
                      <td class="ip-total-amount">{{ "%.2f"|format(view_ctx.total_vat) }}</td>
                    </tr>
                    <tr class="ip-totals ip-total-ttc">
                      <td class="ip-total-label">TOTAL TTC</td>
                      <td class="ip-total-amount">{{ "%.2f"|format(view_ctx.total_ttc) }} €</td>
                    </tr>
                  </tfoot>
                </table>

                <section class="ip-pay">
                  <b>Conditions et modalités de paiement</b>
                  <div class="ip-pay-body">{{ view_ctx.payment_terms_html|safe }}</div>
                </section>
              </article>
              {% else %}
              <div class="invoice-paper">
                <p style="color:#dc2626;">{{ tr.get("invoice_not_found","Faktura nije pronadjena.") }}</p>
              </div>
              {% endif %}
            </div>

            <a class="ip-download-cta" href="{{ download_url }}">⬇ {{ tr.get("download","Telecharger") }} PDF</a>

            {% if email_logs %}
            <section class="email-log-card" style="margin-top:18px;background:{{ '#191919' if dark else '#ffffff' }};border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};border-radius:10px;padding:14px 16px;">
              <h3 style="margin:0 0 10px;font-size:15px;color:{{ '#e2e8f0' if dark else '#1e293b' }};">
                📨 {{ tr.get("email_log_title","Trag slanja emaila") }}
              </h3>
              <div style="overflow-x:auto;">
              <table style="width:100%;border-collapse:collapse;font-size:12px;color:{{ '#e2e8f0' if dark else '#1e293b' }};">
                <thead>
                  <tr style="text-align:left;border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};">
                    <th style="padding:6px 8px;">{{ tr.get("sent_at","Vrijeme") }}</th>
                    <th style="padding:6px 8px;">{{ tr.get("recipient","Primalac") }}</th>
                    <th style="padding:6px 8px;">{{ tr.get("status","Status") }}</th>
                    <th style="padding:6px 8px;" title="{{ tr.get('email_log_mailbox_help','Kopija sacuvana u Sent folderu mailbox-a preko IMAP-a') }}">
                      {{ tr.get("email_log_mailbox","Sacuvano u mailbox") }}
                    </th>
                    <th style="padding:6px 8px;" title="Message-ID">ID</th>
                    <th style="padding:6px 8px;" title="SHA-256 PDF-a koji je poslan">{{ tr.get("email_log_pdf_hash","PDF hash") }}</th>
                  </tr>
                </thead>
                <tbody>
                  {% for lg in email_logs %}
                  <tr style="border-bottom:1px solid {{ '#2c2c30' if dark else '#f1f5f9' }};">
                    <td style="padding:6px 8px;white-space:nowrap;">{{ lg.sent_at }}</td>
                    <td style="padding:6px 8px;">{{ lg.recipient }}</td>
                    <td style="padding:6px 8px;">
                      {% if lg.status == 'sent' %}
                        <span style="color:#16a34a;font-weight:700;">✓ {{ lg.status }}</span>
                      {% else %}
                        <span style="color:#dc2626;font-weight:700;" title="{{ lg.error }}">✗ {{ lg.status }}</span>
                      {% endif %}
                    </td>
                    <td style="padding:6px 8px;">
                      {% if lg.status == 'sent' %}
                        {% if lg.imap_saved %}
                          <span style="color:#16a34a;font-weight:700;">✓ {{ tr.get("yes","da") }}</span>
                        {% else %}
                          <span style="color:#b45309;font-weight:700;" title="{{ lg.imap_error }}">✗ {{ tr.get("no","ne") }}</span>
                        {% endif %}
                      {% else %}
                        <span style="color:{{ '#9ca3af' if dark else '#6b7280' }};">—</span>
                      {% endif %}
                    </td>
                    <td style="padding:6px 8px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:{{ '#9ca3af' if dark else '#6b7280' }};">
                      {% if lg.message_id %}
                        <span title="{{ lg.message_id }}">{{ lg.message_id[:24] }}…</span>
                      {% else %}—{% endif %}
                    </td>
                    <td style="padding:6px 8px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:{{ '#9ca3af' if dark else '#6b7280' }};">
                      {% if lg.attachment_sha256 %}
                        <span title="{{ lg.attachment_sha256 }}">{{ lg.attachment_sha256[:16] }}…</span>
                      {% else %}—{% endif %}
                    </td>
                  </tr>
                  {% endfor %}
                </tbody>
              </table>
              </div>
              <p style="margin:10px 0 0;font-size:11px;color:{{ '#9ca3af' if dark else '#6b7280' }};">
                {{ tr.get("email_log_footnote","Message-ID i SHA-256 PDF-a su forenzicki dokaz da je tacno ovaj email i tacno ova faktura prosli kroz SMTP server.") }}
              </p>
            </section>
            {% endif %}
        </div>
    </div>
    """, tr=tr, dark=dark, row=row, record=record, view_ctx=view_ctx,
         pdf_url=pdf_url, download_url=download_url,
         paid_fields=paid_fields, sent_fields=sent_fields,
         is_manual=is_manual, edit_url=edit_url, email_logs=email_logs,
         plan_summary=plan_summary, plan_mismatch=plan_mismatch,
         mismatch_info=mismatch_info)


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
    # Default: empty = ALL history for client (matches /invoices/client default)
    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to",   "").strip()
    status    = request.args.get("status",    "all").strip()
    doc_filter = request.args.get("doc",      "all").strip()
    conn = get_conn()
    records = fetch_invoice_records(
        conn, date_from or None, date_to or None, client, status,
        date_basis="work_period",
    )
    conn.close()
    # Apply the same document filter as /invoices/client so PDF matches table
    if doc_filter == "facture":
        records = [r for r in records if r.get("source", "auto") != "manual"]
    elif doc_filter == "manual":
        records = [r for r in records if r.get("source", "auto") == "manual"]
    pdf = build_client_statement_pdf(client, records, date_from, date_to)
    fname = f"releve_{client}_{date_from or 'all'}_{date_to or 'all'}.pdf"
    return send_file(pdf, as_attachment=True, download_name=fname, mimetype="application/pdf")


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
        "list": tr.get("invoice_list_pdf", "Lista faktura PDF"),
    }.get(export_type, tr["download_all_invoices"])
    action = {
        "certificate": "/invoices/client_statement",
        "list": "/invoices/list_pdf",
    }.get(export_type, "/invoices/download_all")
    return render_template_string(BASE_STYLE + header_html() + """
    <div class="card" style="max-width:760px;margin:auto;">
        <h2>{{ title }}</h2>
        <p class="muted">{{ tr.get("export_pick_period_hint","Izaberi tacan period i po potrebi klijenta.") }}</p>
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
                    <option value="all">{{ tr.get("status_all","Sve") }}</option>
                    <option value="paid">{{ tr["paid"] }}</option>
                    <option value="unpaid">{{ tr["unpaid"] }}</option>
                </select>
            {% endif %}
            {% if export_type in ('all', 'list') %}
                <label>{{ tr.get("date_filter_basis","Filtriraj po") }}</label>
                <select name="date_basis">
                    <option value="invoice_date" selected>{{ tr.get("invoice_date_basis","Datum fakture") }}</option>
                    <option value="work_period">{{ tr.get("work_period_basis","Period rada") }}</option>
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
    date_basis = request.args.get("date_basis", "invoice_date").strip()
    if date_basis not in ("invoice_date", "work_period"):
        date_basis = "invoice_date"
    conn = get_conn()
    records = fetch_invoice_records(conn, date_from, date_to, client or None, status,
                                    date_basis=date_basis)
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
    # profiles is serialized via |tojson in the template (XSS-safe).
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
    var quoteProfiles = {{ profiles|tojson }};
    function fillQuoteClient(){
        var name = document.getElementById('quoteClientSearch').value;
        var profile = quoteProfiles.find(function(p){ return p.client === name; });
        if(!profile){ return; }
        document.getElementById('quoteClientName').value = profile.client || "";
        document.getElementById('quoteClientAddress').value = profile.address || "";
        document.getElementById('quoteClientEmail').value = profile.email || "";
    }
    </script>
    """, tr=tr, dark=dark, profiles=profiles, today=lux_now().strftime("%Y-%m-%d"), now_code=lux_now().strftime("%Y%m%d"))


@app.route("/invoices/manual", methods=["GET", "POST"])
def invoices_manual():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    conn = get_conn(); c = conn.cursor()
    settings = get_invoice_settings(conn)
    profiles = get_invoice_profiles(conn)
    templates = c.execute(
        "SELECT id, designation, default_amount, default_vat, "
        "COALESCE(archived,0), COALESCE(auto_saved,0), COALESCE(last_used_at,'') "
        "FROM manual_item_templates "
        "ORDER BY COALESCE(last_used_at,'') DESC, sort_order, id"
    ).fetchall()
    # Persistent custom VAT rates (admin can add new ones if rates change)
    extra_vat_rates = [r[0] for r in c.execute(
        "SELECT rate FROM invoice_custom_vat_rates ORDER BY rate"
    ).fetchall()]

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

        # ── Archive / unarchive saved item ────────────────────────────────
        if action in ("archive_template", "unarchive_template"):
            tpl_id = request.form.get("tpl_id", "")
            new_state = 1 if action == "archive_template" else 0
            c.execute("UPDATE manual_item_templates SET archived=? WHERE id=?",
                      (new_state, tpl_id))
            conn.commit(); conn.close()
            return redirect("/invoices/manual")

        # ── Save invoice ──────────────────────────────────────────────────
        inv_num       = request.form.get("invoice_number", "").strip()
        form_mode     = request.form.get("mode", "create")   # 'create' | 'edit'
        client_name   = request.form.get("client_name",   "").strip()
        client_addr   = request.form.get("client_address","").strip()
        raw_inv_date  = (request.form.get("invoice_date", "") or "").strip()
        payment_terms = request.form.get("payment_terms", "").strip()

        # Service window — date_from / date_to. These drive the
        # /diagram and report bucketing, so they must be real
        # YYYY-MM-DD strings even when the form submits junk. Fall
        # back to invoice_date (also validated) so a missing or
        # invalid value never writes garbage into invoice_records.
        def _safe_iso(s):
            try:
                datetime.strptime(s, "%Y-%m-%d")
                return s
            except (TypeError, ValueError):
                return ""
        today_iso = lux_now().strftime("%Y-%m-%d")
        # Validate invoice_date with the same helper before letting
        # it become a date_from fallback — a crafted POST with an
        # invalid invoice_date used to leak into the work window.
        inv_date  = _safe_iso(raw_inv_date) or today_iso
        raw_from  = (request.form.get("date_from", "") or "").strip()
        raw_to    = (request.form.get("date_to",   "") or "").strip()
        date_from = _safe_iso(raw_from) or inv_date
        date_to   = _safe_iso(raw_to)   or date_from
        # Order guard: a swapped pair (date_to before date_from) is
        # almost always a typo and would land the invoice in the
        # wrong /diagram bucket. Snap date_to up to date_from so the
        # work window collapses to a single day instead of running
        # backwards.
        if date_to < date_from:
            date_to = date_from
        # Snapshot the existing date_from/date_to BEFORE the writes so
        # we can detect a period repair and flash a specific message.
        prev_period_row = c.execute(
            "SELECT COALESCE(date_from,''), COALESCE(date_to,'') "
            "FROM invoice_records "
            "WHERE invoice_number=? AND COALESCE(deleted,0)=0",
            (inv_num,)
        ).fetchone() if inv_num else None
        prev_date_from = (prev_period_row[0] if prev_period_row else "") or ""
        prev_date_to   = (prev_period_row[1] if prev_period_row else "") or ""

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
                """, (inv_num, client_name, date_from, date_to, inv_date,
                      total_ht, total_vat, total_ttc))
                conn.commit()
                if c.rowcount == 1:
                    reserved = True
                    break
            else:
                if convert_from_auto:
                    # Converting auto → manual: preserve the auto's
                    # original date_from/date_to (the form pre-fills
                    # them in this path) and update everything else.
                    c.execute("""
                        UPDATE invoice_records SET
                            client_name=?, invoice_date=?,
                            date_from=?, date_to=?,
                            amount=?, vat_amount=?, total=?, source='manual'
                        WHERE invoice_number=? AND COALESCE(deleted,0)=0
                    """, (client_name, inv_date, date_from, date_to,
                          total_ht, total_vat, total_ttc, inv_num))
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
                            date_from=excluded.date_from, date_to=excluded.date_to,
                            amount=excluded.amount, vat_amount=excluded.vat_amount,
                            total=excluded.total, source='manual'
                        WHERE invoice_records.source='manual'
                    """, (inv_num, client_name, date_from, date_to, inv_date,
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
                (invoice_number, client_name, client_address, invoice_date,
                 date_from, date_to, items_json,
                 payment_terms, total_ht, total_vat, total_ttc, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(invoice_number) DO UPDATE SET
                client_name=excluded.client_name, client_address=excluded.client_address,
                invoice_date=excluded.invoice_date,
                date_from=excluded.date_from, date_to=excluded.date_to,
                items_json=excluded.items_json,
                payment_terms=excluded.payment_terms, total_ht=excluded.total_ht,
                total_vat=excluded.total_vat, total_ttc=excluded.total_ttc
        """, (inv_num, client_name, client_addr, inv_date,
              date_from, date_to, items_json,
              payment_terms, total_ht, total_vat, total_ttc, now_str))

        # ── Auto-save each item as a reusable template ────────────────────
        # Dedup by case-folded designation: existing rows bump last_used_at,
        # new ones are inserted with auto_saved=1.
        for it in items:
            d = (it.get("designation") or "").strip()
            if not d:
                continue
            existing = c.execute(
                "SELECT id FROM manual_item_templates WHERE LOWER(designation)=LOWER(?) LIMIT 1",
                (d,),
            ).fetchone()
            if existing:
                c.execute(
                    "UPDATE manual_item_templates SET default_amount=?, default_vat=?, "
                    "last_used_at=?, archived=0 WHERE id=?",
                    (it["amount"], it["vat_rate"], now_str, existing[0]),
                )
            else:
                c.execute(
                    "INSERT INTO manual_item_templates "
                    "(designation, default_amount, default_vat, auto_saved, last_used_at) "
                    "VALUES (?,?,?,1,?)",
                    (d, it["amount"], it["vat_rate"], now_str),
                )
        conn.commit(); conn.close()

        if request.form.get("download_pdf"):
            return redirect(f"/invoices/manual/pdf?invoice_number={inv_num}")
        # If this save changed the work-period dates (admin opened the
        # invoice specifically to repair the /diagram bucket), flash an
        # explicit "period changed" message so they get confirmation
        # the right column moved.
        period_changed = (prev_date_from or prev_date_to) and (
            prev_date_from != date_from or prev_date_to != date_to
        )
        if period_changed:
            flash(
                tr.get(
                    "mi_period_changed_flash",
                    "Period rada fakture #{n} je promijenjen na {from} - {to}.",
                ).replace("{n}", str(inv_num))
                 .replace("{from}", format_date(date_from))
                 .replace("{to}",   format_date(date_to)),
                "ok",
            )
        else:
            flash(f"✓ {tr.get('mi_save_invoice','Faktura sačuvana')} #{inv_num}", "ok")
        return redirect(f"/invoices/manual?invoice_number={inv_num}")

    # ── GET: show form ────────────────────────────────────────────────────
    # Pre-fill from existing draft if invoice_number given
    load_num = request.args.get("invoice_number", "").strip()
    load_auto = request.args.get("load_auto", "").strip()
    draft = {}
    if load_num:
        row = c.execute(
            "SELECT invoice_number, client_name, client_address, invoice_date, "
            "COALESCE(date_from,''), COALESCE(date_to,''), "
            "items_json, payment_terms FROM manual_invoice_drafts WHERE invoice_number=?",
            (load_num,)
        ).fetchone()
        if row:
            draft = {"invoice_number": row[0], "client_name": row[1],
                     "client_address": row[2], "invoice_date": row[3],
                     "date_from": row[4], "date_to": row[5],
                     "items_json": row[6], "payment_terms": row[7]}
            # Older manuals don't have date_from/date_to in the draft —
            # pull them from invoice_records as a fallback so the form
            # opens with the real persisted period instead of empty.
            if not (draft["date_from"] or draft["date_to"]):
                rec = c.execute(
                    "SELECT COALESCE(date_from,''), COALESCE(date_to,'') "
                    "FROM invoice_records "
                    "WHERE invoice_number=? AND COALESCE(deleted,0)=0",
                    (row[0],)
                ).fetchone()
                if rec:
                    draft["date_from"] = draft["date_from"] or rec[0]
                    draft["date_to"]   = draft["date_to"]   or rec[1]
    if load_auto and not draft:
        rec = c.execute(
            "SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, "
            "paid, paid_date, COALESCE(sent, 0), COALESCE(sent_date, ''), COALESCE(source, 'auto') "
            "FROM invoice_records WHERE invoice_number=? AND COALESCE(deleted,0)=0",
            (load_auto,)
        ).fetchone()
        if rec:
            record = invoice_record_to_dict(rec)
            auto_row, _ = get_invoice_row_for_record(conn, record)
            prof = c.execute(
                "SELECT custom_address FROM client_invoice_profiles WHERE client_name=?",
                (record["client"],)
            ).fetchone()
            client_addr = (prof[0] if prof else "") or (auto_row.get("address") if auto_row else "") or ""
            vr = round(record["vat_amount"] / record["amount"] * 100, 2) if record["amount"] else 17.0
            designation = invoice_designation_text(auto_row) if auto_row else invoice_service_title(record["date_from"], record["date_to"])
            draft = {
                "invoice_number": record["invoice_number"],
                "client_name": record["client"],
                "client_address": client_addr,
                "invoice_date": record["invoice_date"],
                # Preserve the auto invoice's original work period so a
                # converted manual stays in the same /diagram bucket.
                "date_from": record.get("date_from", "") or "",
                "date_to":   record.get("date_to",   "") or "",
                "items_json": json.dumps([{"designation": designation, "amount": round(float(record["amount"]), 2), "vat_rate": vr}], ensure_ascii=False),
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

    templates_list = [{"id": r[0], "designation": r[1], "amount": r[2], "vat": r[3],
                       "archived": int(r[4] or 0), "auto_saved": int(r[5] or 0),
                       "last_used_at": r[6] or ""}
                      for r in templates]

    return render_template_string(BASE_STYLE + header_html() + r"""
<style>
.mi-shell { background:{{ '#161618' if dark else '#ffffff' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }}; border-radius:10px; padding:0 0 22px 0; overflow:hidden; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
.mi-top { display:flex; align-items:center; justify-content:space-between; gap:18px;
          padding:18px 22px; background:{{ '#1d1d1f' if dark else '#f1f5f9' }}; }
.mi-brand { font-size:22px; font-weight:800; color:{{ '#e2e8f0' if dark else '#1e293b' }}; }
.mi-brand span { background:#ffd429; color:#111; border-radius:6px; padding:2px 6px; }
/* Single-column layout — saved items moved to the modal popup,
   right sidebar removed per user request. */
.mi-body { max-width:780px; margin:28px auto; padding:0 24px; display:block; }
.mi-main {}
.mi-card { background:{{ '#1d1d1f' if dark else '#ffffff' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; border-radius:10px; padding:18px; margin-bottom:16px; }
.mi-card h3 { margin:0 0 12px; font-size:14px; color:{{ '#ffd429' if dark else '#b45309' }}; text-transform:uppercase;
              letter-spacing:.05em; }
.mi-label { font-size:12px; color:{{ '#94a3b8' if dark else '#64748b' }}; margin:10px 0 3px; display:block; }
.mi-input { width:100%; padding:8px 10px; border-radius:7px; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};
            background:{{ '#0f0f10' if dark else '#ffffff' }}; color:{{ '#e2e8f0' if dark else '#0f172a' }}; font-size:14px; box-sizing:border-box;
            margin:0; }
.mi-input::placeholder { color:{{ '#6b7280' if dark else '#94a3b8' }}; }
.mi-textarea { width:100%; padding:8px 10px; border-radius:7px; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};
               background:{{ '#0f0f10' if dark else '#ffffff' }}; color:{{ '#e2e8f0' if dark else '#0f172a' }}; font-size:13px; box-sizing:border-box;
               resize:vertical; margin:0; min-height:70px; }
.mi-row { display:grid; grid-template-columns:1fr 130px 100px 36px; gap:8px;
          align-items:start; margin-bottom:8px; }
.mi-row-hdr { display:grid; grid-template-columns:1fr 130px 100px 36px; gap:8px;
              font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; text-transform:uppercase;
              letter-spacing:.05em; margin-bottom:4px; }
.mi-del-btn { background:#ef4444; border:none; color:white; border-radius:6px;
              cursor:pointer; padding:0; height:38px; width:36px; font-size:18px; }
.mi-add-btn { width:100%; padding:10px; background:#1f4f82; color:white;
              border:none; border-radius:7px; cursor:pointer; font-size:14px;
              font-weight:600; margin-top:6px; }
.mi-totals { border-top:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; margin-top:14px; padding-top:10px; }
.mi-tot-row { display:flex; justify-content:space-between; padding:4px 0;
              font-size:14px; }
.mi-tot-row.big { font-size:18px; font-weight:800; color:{{ '#ffd429' if dark else '#b45309' }}; }
.mi-save-btn { width:100%; padding:13px; background:#22c55e; color:#111;
               border:none; border-radius:8px; cursor:pointer; font-size:16px;
               font-weight:800; margin-top:8px; }
.mi-pdf-btn  { width:100%; padding:13px; background:#3b82f6; color:white;
               border:none; border-radius:8px; cursor:pointer; font-size:16px;
               font-weight:700; margin-top:8px; }
.mi-number-box { background:{{ '#0f0f10' if dark else '#f1f5f9' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; border-radius:8px; padding:10px 14px;
                 font-size:22px; font-weight:800; color:{{ '#ffd429' if dark else '#b45309' }}; margin-bottom:6px; }
@media (max-width:760px){
  .mi-row { grid-template-columns:1fr 110px 80px 36px; }
}

/* ── Saved items modal ──────────────────────────────────────────── */
.si-overlay { display:none; position:fixed; inset:0; z-index:9999;
              background:rgba(0,0,0,.55); align-items:center; justify-content:center; padding:16px; }
.si-modal { background:{{ '#161618' if dark else '#ffffff' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }};
            width:min(900px, 100%); max-height:88vh; border-radius:14px;
            display:flex; flex-direction:column; overflow:hidden;
            box-shadow:0 20px 60px rgba(0,0,0,.4); }
.si-hdr { display:flex; align-items:center; gap:10px; padding:14px 18px;
          background:{{ '#1d1d1f' if dark else '#0f172a' }}; color:#fff; }
.si-hdr h3 { margin:0; font-size:16px; font-weight:700; flex:1; }
.si-close { background:transparent; color:#fff; border:none; font-size:22px; cursor:pointer; padding:4px 8px; }
.si-search-row { display:flex; gap:8px; padding:14px 18px;
                 background:{{ '#191919' if dark else '#f8fafc' }};
                 border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
.si-search-row input { flex:1; padding:9px 12px; border-radius:8px; font-size:14px;
                       border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};
                       background:{{ '#0f0f10' if dark else '#ffffff' }};
                       color:{{ '#e2e8f0' if dark else '#0f172a' }}; }
.si-tabs { display:flex; gap:6px; padding:8px 18px;
           background:{{ '#191919' if dark else '#f8fafc' }};
           border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
.si-tab { padding:8px 14px; border-radius:8px 8px 0 0;
          background:{{ '#0f0f10' if dark else '#e2e8f0' }};
          color:{{ '#e2e8f0' if dark else '#1e293b' }};
          font-weight:700; font-size:13px; border:none; cursor:pointer; }
.si-tab.active { background:{{ '#374151' if dark else '#1f4f82' }}; color:#fff; }
.si-table-hdr { display:grid; grid-template-columns:1fr 140px 110px; gap:12px;
                padding:10px 18px; font-size:11px; font-weight:700;
                color:{{ '#94a3b8' if dark else '#64748b' }};
                text-transform:uppercase; letter-spacing:.06em;
                border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
.si-list { flex:1; overflow-y:auto; padding:0; }
.si-row { display:grid; grid-template-columns:1fr 140px 110px; gap:12px;
          padding:14px 18px; align-items:center;
          border-bottom:1px solid {{ '#2c2c30' if dark else '#f1f5f9' }}; }
.si-row:hover { background:{{ '#1d1d1f' if dark else '#f8fafc' }}; }
.si-desig { font-size:13px; line-height:1.4; white-space:pre-line;
            color:{{ '#93c5fd' if dark else '#1f4f82' }}; text-decoration:none; font-weight:600; }
.si-amt { text-align:right; font-weight:700; font-size:14px; }
.si-arch { background:transparent; color:{{ '#93c5fd' if dark else '#1f4f82' }};
           border:none; cursor:pointer; font-weight:600; text-align:right;
           font-size:13px; padding:4px 6px; }
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

  <div class="mi-body">
    <form id="miForm" method="post" action="/invoices/manual">
    <input type="hidden" name="convert_from_auto" value="{{ '1' if convert_from_auto else '' }}">

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

        <!-- Work period (date_from / date_to). Distinct from
             invoice_date — drives /diagram and report bucketing so an
             invoice issued in June for May's work lands under May. -->
        <div class="mi-card" style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
          <div>
            <div class="mi-label">📅 {{ tr.get("mi_period_from","Période du") }}</div>
            <input class="mi-input" type="date" name="date_from" id="miDateFrom"
                   value="{{ draft.date_from or '' }}">
          </div>
          <div>
            <div class="mi-label">📅 {{ tr.get("mi_period_to","Période au") }}</div>
            <input class="mi-input" type="date" name="date_to" id="miDateTo"
                   value="{{ draft.date_to or '' }}">
          </div>
          <div style="grid-column:1 / -1; font-size:12px; color:{{ '#94a3b8' if dark else '#64748b' }};">
            {{ tr.get("mi_period_hint","Period u kojem je rad obavljen. Koristi se za /diagram i izvjestaje. Ako se ostavi prazno, padne na datum fakture.") }}
          </div>
          <!-- Quick-apply banner: populated by JS at the bottom of the
               page when the item designation mentions a month name like
               "Mai 2026" / "mois de Mai'26". The admin still has to
               click "Apply" — we never silently rewrite the dates. -->
          <div id="miPeriodSuggest" hidden
               style="grid-column:1 / -1; padding:10px 12px; border-radius:10px;
                      background:#fef3c7; color:#92400e; border:1px solid #fde68a;
                      font-size:13px; display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
            <span>💡 <span id="miPeriodSuggestText"></span></span>
            <button type="button" id="miPeriodSuggestApply"
                    style="margin-left:auto; background:#f59e0b; color:white; border:none;
                           border-radius:8px; padding:7px 14px; font-weight:700;
                           cursor:pointer; font-family:inherit; font-size:13px;">
              {{ tr.get("mi_period_apply","Primijeni") }}
            </button>
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
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
            <button type="button" class="mi-add-btn" onclick="addItem()" style="margin-top:6px;">{{ tr.get("mi_add_item","+ Ajouter un article") }}</button>
            <button type="button" class="mi-add-btn" onclick="openSavedItemsModal()" style="margin-top:6px;background:#0ea5e9;">📂 {{ tr.get("mi_saved_items","Articles sauvegardés") }}</button>
          </div>

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

        <!-- ★ Primary save buttons INSIDE the form (guaranteed to submit) -->
        <div class="mi-card" style="display:flex;flex-direction:column;gap:10px;">
          <button type="submit" name="action" value="save" class="mi-save-btn">
            💾 {{ tr.get("mi_save_invoice","Sauvegarder la facture") }}
          </button>
          <button type="submit" name="download_pdf" value="1" class="mi-pdf-btn">
            📄 {{ tr.get("mi_save_pdf","Sauvegarder + PDF") }}
          </button>
        </div>

      </div><!-- /mi-main -->
    </form><!-- /miForm — primary save buttons live inside this form -->

      <!-- Right sidebar removed — saved items live in the modal popup now -->
  </div><!-- /mi-body -->
</div>

<!-- Saved items picker modal -->
<div id="savedItemsModal" class="si-overlay" onclick="if(event.target===this)closeSavedItemsModal();">
  <div class="si-modal">
    <div class="si-hdr">
      <h3>📂 {{ tr.get("mi_modal_title","Ajouter des articles sauvegardés") }}</h3>
      <button class="si-close" type="button" onclick="closeSavedItemsModal()">×</button>
    </div>
    <div class="si-search-row">
      <input id="siSearch" type="search" placeholder="{{ tr.get('mi_modal_search','Rechercher par désignation ou montant') }}" oninput="renderSavedItems()" autocomplete="off">
    </div>
    <div class="si-tabs">
      <button class="si-tab active" data-tab="recent"   type="button" onclick="setSiTab('recent')">📋 {{ tr.get("mi_modal_recent","Éléments récents") }}</button>
      <button class="si-tab"        data-tab="archived" type="button" onclick="setSiTab('archived')">📦 {{ tr.get("mi_modal_archived","Éléments archivés") }}</button>
    </div>
    <div class="si-table-hdr">
      <span>{{ tr.get("mi_designation","Désignation") }}</span>
      <span style="text-align:right;">{{ tr.get("mi_modal_net","Net à payer") }}</span>
      <span style="text-align:right;">{{ tr.get("mi_modal_archives","Archives") }}</span>
    </div>
    <div id="siList" class="si-list"></div>
  </div>
</div>

<script>
var miProfiles = {{ profiles|tojson }};
var prefillItems = {{ prefill_items|tojson }};
var miPlaceholder = {{ tr.get("mi_designation_placeholder","Désignation de la prestation...")|tojson }};
var extraVatRates = {{ extra_vat_rates|tojson }};
var savedItemsAll = {{ templates_list|tojson }};
var miAddVatLabel  = {{ tr.get("mi_add_vat_label","Ajouter taxe...")|tojson }};
var miAddVatPrompt = {{ tr.get("mi_add_vat_prompt","Saisir le nouveau taux de TVA (%)")|tojson }};
var miInvalidVat   = {{ tr.get("mi_invalid_vat","Taux de TVA invalide.")|tojson }};
var miNoItems      = {{ tr.get("mi_modal_no_items","Aucun article correspondant.")|tojson }};
var miArchiveLabel   = {{ tr.get("mi_modal_archive","Archiver")|tojson }};
var miUnarchiveLabel = {{ tr.get("mi_modal_unarchive","Restaurer")|tojson }};

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

function autoGrow(el){
  // Reset then grow to scrollHeight so full content is always visible
  el.style.height = 'auto';
  el.style.height = (el.scrollHeight + 2) + 'px';
}

function _inheritVatFromFirstItem(){
  // When user adds a new item via the "+" button, inherit the VAT rate
  // of the existing first item so a deduction line on an 8% invoice
  // doesn't silently default to 17% (which would mix rates and force
  // the PDF/preview to drop the "TVA 8.0%" label).
  var firstVat = document.querySelector('.mi-item-row .mi-vat');
  if (firstVat) {
    var v = parseFloat(firstVat.value);
    if (!isNaN(v)) return v;
  }
  return 17;  // bare-form default (no items yet)
}

function addItem(desig, amt, vat){
  desig = desig || '';
  amt   = (amt !== undefined) ? amt : '';
  if (vat === undefined) vat = _inheritVatFromFirstItem();
  // Pick initial row count based on content so long imported items
  // (e.g. converted auto-invoice service title with date list) render
  // fully on first paint without needing manual scrolling.
  var lines = String(desig).split('\n').length;
  var initRows = Math.max(3, Math.min(lines + 1, 12));
  // Build VAT options: standard rates + persisted custom rates + "+ Add" entry
  var standard = [17, 8, 3, 0];
  var allRates = standard.concat(extraVatRates || []).map(function(r){ return parseFloat(r); });
  // Make sure the saved item's own rate is present even if it isn't standard/extra
  if (!isNaN(parseFloat(vat)) && allRates.indexOf(parseFloat(vat)) === -1) {
    allRates.push(parseFloat(vat));
  }
  // De-dupe and sort descending
  allRates = Array.from(new Set(allRates)).sort(function(a,b){ return b-a; });
  var vatHtml = '';
  allRates.forEach(function(r){
    var sel = (parseFloat(vat) === r) ? ' selected' : '';
    var label = (Number.isInteger(r) ? r : r.toFixed(2).replace(/\.?0+$/, '')) + '%';
    vatHtml += '<option value="'+r+'"'+sel+'>'+label+'</option>';
  });
  vatHtml += '<option value="__add__">+ ' + escHtml(miAddVatLabel) + '</option>';

  var c = document.getElementById('itemsContainer');
  var d = document.createElement('div');
  d.className = 'mi-row mi-item-row';
  d.innerHTML =
    '<textarea class="mi-textarea mi-desig" name="designation[]" rows="' + initRows + '"'
    +' placeholder="' + escHtml(miPlaceholder) + '"'
    +' oninput="recalc();autoGrow(this);">'
    + escHtml(String(desig)) + '</textarea>'
    + '<input class="mi-input mi-amt" type="number" step="0.01" name="amount[]"'
    +' value="'+(amt===''?'':Number(amt).toFixed(2))+'" placeholder="0.00" oninput="recalc()">'
    // data-prev seeded with the row's current rate so a Cancel on "+ Add"
    // restores THIS row's value instead of falling back to 17%.
    // onfocus/onmousedown keeps it in sync if the user manually changes
    // the rate before opening "+ Add" again.
    + '<select class="mi-input mi-vat" name="vat_rate[]" data-prev="' + vat + '"'
    +' onfocus="this.dataset.prev=this.value" onmousedown="this.dataset.prev=this.value"'
    +' onchange="onVatChange(this)">'
    + vatHtml
    + '</select>'
    + '<button type="button" class="mi-del-btn" onclick="this.closest(\'.mi-item-row\').remove();recalc();">×</button>';
  c.appendChild(d);
  // Trigger auto-grow on the newly inserted textarea so pre-filled
  // multi-line content expands immediately on page load
  var ta = d.querySelector('.mi-desig');
  if (ta) autoGrow(ta);
  recalc();
}

function useTemplate(desig, amt, vat){
  // If there's already an EMPTY item row (designation blank), reuse it
  // instead of creating a new one — otherwise picking a saved item from
  // the modal would leave a stray empty row above the imported one.
  var rows = document.querySelectorAll('.mi-item-row');
  var target = null;
  for (var i = 0; i < rows.length; i++) {
    var dt = rows[i].querySelector('.mi-desig');
    if (dt && !String(dt.value || '').trim()) { target = rows[i]; break; }
  }
  if (!target) {
    addItem(desig, amt, vat);
    return;
  }
  // Fill the existing empty row in place
  var d = target.querySelector('.mi-desig');
  var a = target.querySelector('.mi-amt');
  var v = target.querySelector('.mi-vat');
  if (d) { d.value = String(desig || ''); autoGrow(d); }
  if (a) {
    var amtNum = (amt === '' || amt === undefined || amt === null) ? '' : Number(amt);
    a.value = (amtNum === '' || isNaN(amtNum)) ? '' : amtNum.toFixed(2);
  }
  if (v && vat !== undefined && vat !== null) {
    var vatStr = String(parseFloat(vat));
    // If the saved rate isn't in the dropdown yet, inject it before "+ Add"
    var has = Array.prototype.some.call(v.options, function(o){ return o.value === vatStr; });
    if (!has) {
      var addOpt = v.querySelector('option[value="__add__"]');
      var newOpt = document.createElement('option');
      newOpt.value = vatStr;
      newOpt.textContent = (Number.isInteger(parseFloat(vat)) ? parseFloat(vat)
                                                              : parseFloat(vat).toFixed(2).replace(/\.?0+$/, '')) + '%';
      v.insertBefore(newOpt, addOpt);
    }
    v.value = vatStr;
    v.dataset.prev = vatStr;
  }
  recalc();
}

// VAT dropdown — "+ Add custom rate" sentinel handler
function onVatChange(sel){
  if (sel.value === '__add__') {
    var ask = prompt(miAddVatPrompt, '');
    if (ask === null) { sel.value = sel.dataset.prev || '17'; recalc(); return; }
    var rate = parseFloat(String(ask).replace(',', '.'));
    if (isNaN(rate) || rate < 0 || rate > 100) {
      alert(miInvalidVat);
      sel.value = sel.dataset.prev || '17'; recalc(); return;
    }
    rate = Math.round(rate * 100) / 100;
    // Persist (best-effort) so it shows in dropdowns next time
    var fd = new FormData(); fd.append('rate', rate);
    fetch('/invoices/manual/vat_rate', {method:'POST', body:fd}).catch(function(){});
    if (extraVatRates.indexOf(rate) === -1) extraVatRates.push(rate);
    // Inject the new option into every row's select that doesn't have it yet
    document.querySelectorAll('.mi-vat').forEach(function(s){
      if (Array.from(s.options).some(function(o){ return parseFloat(o.value) === rate; })) return;
      var addOpt = s.querySelector('option[value="__add__"]');
      var newOpt = document.createElement('option');
      newOpt.value = rate;
      newOpt.textContent = (Number.isInteger(rate) ? rate : rate.toFixed(2).replace(/\.?0+$/, '')) + '%';
      s.insertBefore(newOpt, addOpt);
    });
    sel.value = String(rate);
  }
  sel.dataset.prev = sel.value;
  recalc();
}

// ── Saved items modal ─────────────────────────────────────────────────
function openSavedItemsModal(){
  var modal = document.getElementById('savedItemsModal');
  if (modal) { modal.style.display = 'flex'; renderSavedItems(); }
}
function closeSavedItemsModal(){
  var modal = document.getElementById('savedItemsModal');
  if (modal) modal.style.display = 'none';
}
var _siCurrentTab = 'recent';   // 'recent' | 'archived'
function setSiTab(tab){
  _siCurrentTab = tab;
  document.querySelectorAll('.si-tab').forEach(function(t){
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  renderSavedItems();
}
function renderSavedItems(){
  var q = (document.getElementById('siSearch').value || '').trim().toLowerCase();
  var rows = savedItemsAll.filter(function(it){
    var matches = !q
      || (String(it.designation || '').toLowerCase().indexOf(q) !== -1)
      || (String(it.amount || '').indexOf(q) !== -1);
    var isArchived = !!it.archived;
    var tabOk = (_siCurrentTab === 'archived') ? isArchived : !isArchived;
    return matches && tabOk;
  });
  var list = document.getElementById('siList');
  list.innerHTML = '';
  if (!rows.length) {
    var empty = document.createElement('div');
    empty.style.cssText = 'padding:24px;text-align:center;color:#9ca3af;';
    empty.textContent = miNoItems;
    list.appendChild(empty);
    return;
  }
  // Build rows via DOM so we never inline-quote arbitrary user strings
  // (designation may contain ", ', <, >, line breaks, etc.).
  rows.forEach(function(it){
    var amtFmt = Number(it.amount || 0).toFixed(2);
    var archAction = it.archived ? 'unarchive_template' : 'archive_template';
    var archLabel  = it.archived ? miUnarchiveLabel : miArchiveLabel;

    var row = document.createElement('div');
    row.className = 'si-row';

    var a = document.createElement('a');
    a.className = 'si-desig';
    a.href = 'javascript:void(0)';
    a.textContent = String(it.designation || '');
    a.addEventListener('click', function(){
      useTemplate(it.designation, it.amount, it.vat);
      closeSavedItemsModal();
    });

    var amt = document.createElement('div');
    amt.className = 'si-amt';
    amt.textContent = amtFmt;

    var form = document.createElement('form');
    form.method = 'post';
    form.action = '/invoices/manual';
    form.style.display = 'inline';
    form.innerHTML =
        '<input type="hidden" name="action" value="' + archAction + '">'
      + '<input type="hidden" name="tpl_id" value="' + Number(it.id) + '">';
    var btn = document.createElement('button');
    btn.type = 'submit';
    btn.className = 'si-arch';
    btn.textContent = archLabel;
    form.appendChild(btn);

    row.appendChild(a);
    row.appendChild(amt);
    row.appendChild(form);
    list.appendChild(row);
  });
}

function escHtml(s){
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
          .replace(/"/g,'&quot;');
}

// Load prefill items on page load.
// Use explicit undefined/null check (NOT ||) so vat_rate=0 is honoured,
// otherwise '0 || 17' would silently snap a deducted line back to 17%.
prefillItems.forEach(function(it){
  var amt = (it.amount !== '' && it.amount !== undefined && it.amount !== null) ? it.amount : '';
  var vr  = (it.vat_rate !== undefined && it.vat_rate !== null) ? it.vat_rate : 17;
  addItem(it.designation, amt, vr);
});

// Surface validation errors — when 'required' fields are empty, scroll to them
// so user sees the browser tooltip on mobile (where required tooltips can be hidden)
document.getElementById('miForm').addEventListener('invalid', function(e){
  e.target.scrollIntoView({behavior:'smooth', block:'center'});
  e.target.focus({preventScroll:true});
}, true);

// ── Work-period auto-detection from designation text ───────────────
// User report: 4 manual invoices issued in June for May's work landed
// in June on /diagram. The fix is to set their date_from / date_to to
// the right month. Scan the line-item designations for French / English /
// Luxembourgish month names. If we find a single confident match, show
// a yellow banner with the proposed date_from / date_to. Admin still has
// to click "Apply" — we never silently rewrite the dates.
(function(){
  // Month-name → 0-indexed month. Each language packed inline so we
  // don't pull a heavy regex library — these are the spellings the
  // user pointed at in the bug report.
  var MONTHS = [
    // [0-indexed month, regex pattern (case-insensitive)]
    [0,  /\b(janvier|january|jan|januar|janeiro|januar)\b/i],
    [1,  /\b(f[eé]vrier|february|feb|februar|fevereiro)\b/i],
    [2,  /\b(mars|march|maerz|m[aä]rz|mar[çc]o)\b/i],
    [3,  /\b(avril|april|apr|avril|abril)\b/i],
    [4,  /\b(mai|may|maio)\b/i],
    [5,  /\b(juin|june|jun|juni|junho)\b/i],
    [6,  /\b(juillet|july|jul|juli|julho)\b/i],
    [7,  /\b(ao[uû]t|august|aug|agosto)\b/i],
    [8,  /\b(septembre|september|sep|setembro)\b/i],
    [9,  /\b(octobre|october|oct|oktober|outubro)\b/i],
    [10, /\b(novembre|november|nov|novembro)\b/i],
    [11, /\b(d[eé]cembre|december|dec|dezember|dezembro)\b/i],
  ];
  function lastDay(year, monthIdx) {
    return new Date(year, monthIdx + 1, 0).getDate();
  }
  function pad2(n){ return (n<10?"0":"")+n; }
  function detect(){
    var texts = [];
    document.querySelectorAll('textarea[name="designation[]"]').forEach(function(ta){
      if (ta.value) texts.push(ta.value);
    });
    var blob = texts.join(' ');
    if (!blob) return null;
    var matches = [];
    MONTHS.forEach(function(pair){
      if (pair[1].test(blob)) matches.push(pair[0]);
    });
    // Only auto-suggest when exactly ONE month was mentioned —
    // ambiguous designations ("Mai et Juin") should not silently
    // pick a side.
    if (matches.length !== 1) return null;
    var monthIdx = matches[0];
    var monthRe  = MONTHS[monthIdx][1];
    // Year preference:
    //   explicit YYYY  >  apostrophe/curly-apostrophe YY  >
    //   YY immediately following the detected month  >
    //   invoice_date year  >  current year.
    var year = null;
    var ym = blob.match(/\b(20\d{2})\b/);
    if (ym) year = parseInt(ym[1], 10);
    if (!year) {
      // Accept both ASCII apostrophe (') and Unicode curly
      // apostrophe (’ U+2019). "Mai'26" and "Mai’26" both → 2026.
      var apostrophe = blob.match(/[’'](\d{2})\b/);
      if (apostrophe) year = 2000 + parseInt(apostrophe[1], 10);
    }
    if (!year) {
      // "Mai 26" style: a 2-digit number right after the month
      // word with at most a single space, and explicitly NOT
      // followed by anything that would identify it as a quantity
      // (more digits, a colon for times, h/H for hours, €, .).
      var nearRe = new RegExp(monthRe.source + "\\s*(\\d{2})(?![\\d:hH€.,])", "i");
      var near = blob.match(nearRe);
      if (near) year = 2000 + parseInt(near[near.length - 1], 10);
    }
    if (!year) {
      var inv = document.querySelector('input[name="invoice_date"]');
      if (inv && inv.value) year = parseInt(inv.value.slice(0,4), 10);
    }
    if (!year) year = new Date().getFullYear();
    var fromIso = year + "-" + pad2(monthIdx + 1) + "-01";
    var toIso   = year + "-" + pad2(monthIdx + 1) + "-" + pad2(lastDay(year, monthIdx));
    return {monthIdx:monthIdx, year:year, from:fromIso, to:toIso};
  }
  function fmtHuman(iso){
    var p = iso.split('-');
    return p[2] + "/" + p[1] + "/" + p[0];
  }
  function refresh(){
    var banner = document.getElementById('miPeriodSuggest');
    if (!banner) return;
    var d = detect();
    if (!d) { banner.hidden = true; return; }
    var df = document.getElementById('miDateFrom');
    var dt = document.getElementById('miDateTo');
    // Don't nag when the period is already correct.
    if (df && dt && df.value === d.from && dt.value === d.to) {
      banner.hidden = true;
      return;
    }
    var monthNames = [
      "Janvier","Février","Mars","Avril","Mai","Juin",
      "Juillet","Août","Septembre","Octobre","Novembre","Décembre"
    ];
    var label = monthNames[d.monthIdx] + " " + d.year;
    var tplBanner = {{ (tr.get("mi_period_suggest","Designation pominje {month}. Predlažem period rada {from} → {to}."))|tojson }};
    var msg = tplBanner.replace("{month}", label)
                       .replace("{from}",  fmtHuman(d.from))
                       .replace("{to}",    fmtHuman(d.to));
    document.getElementById('miPeriodSuggestText').textContent = msg;
    banner.hidden = false;
    document.getElementById('miPeriodSuggestApply').onclick = function(){
      if (df) df.value = d.from;
      if (dt) dt.value = d.to;
      banner.hidden = true;
    };
  }
  document.addEventListener('DOMContentLoaded', refresh);
  // Re-scan whenever the admin edits a line-item designation —
  // adding "Mai'26" mid-edit should immediately offer the banner.
  document.addEventListener('input', function(e){
    if (e.target && e.target.matches && e.target.matches('textarea[name="designation[]"]')) {
      refresh();
    }
  });
})();
</script>
""", tr=tr, dark=dark, auto_num=auto_num, today=lux_now().strftime("%Y-%m-%d"),
     profiles=profiles,
     convert_from_auto=convert_from_auto,
     draft=type("D", (), draft)() if draft else type("D", (), {"invoice_number":"","client_name":"","client_address":"","invoice_date":"","payment_terms":""})(),
     default_terms=default_terms,
     templates_list=templates_list,
     prefill_items=prefill_items,
     extra_vat_rates=extra_vat_rates)


@app.route("/invoices/manual/rebuild", methods=["POST"])
def invoices_manual_rebuild():
    """Regenerate a manual invoice's items from the CURRENT plan.

    Triggered by the "Obnovi stavke iz plana" button on the mismatch
    banner shown in /invoices/view. Only touches items_json, amount,
    vat_amount and total — invoice_number, invoice_date, paid, sent,
    client and the work window are all preserved. Refuses on auto
    invoices, missing records, or clients that have no shifts in
    the record's window.
    """
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    invoice_number = request.form.get("invoice_number", "").strip()
    if not invoice_number:
        return redirect("/invoices")
    conn = get_conn(); c = conn.cursor()
    rec_row = c.execute(
        "SELECT client_name, date_from, date_to, invoice_date, "
        "COALESCE(source,'auto'), COALESCE(paid,0), COALESCE(sent,0) "
        "FROM invoice_records WHERE invoice_number=? AND COALESCE(deleted,0)=0",
        (invoice_number,)
    ).fetchone()
    if not rec_row:
        conn.close()
        flash(tr.get("invoice_not_found", "Faktura nije pronadjena."), "error")
        return redirect("/invoices")
    if rec_row[4] != "manual":
        conn.close()
        flash(tr.get(
            "mi_rebuild_not_manual",
            "Obnavljanje iz plana radi samo na rucnim fakturama."
        ), "error")
        return redirect(f"/invoices/view?invoice_number={urllib.parse.quote(invoice_number)}")
    client_name, date_from, date_to, invoice_date = rec_row[0], rec_row[1], rec_row[2], rec_row[3]
    if not (date_from and date_to):
        conn.close()
        flash(tr.get(
            "mi_rebuild_no_period",
            "Faktura nema period rada (date_from/date_to) — postavi ga u editoru prije obnavljanja."
        ), "error")
        return redirect(f"/invoices/view?invoice_number={urllib.parse.quote(invoice_number)}")
    rows = build_invoice_rows(conn, date_from, date_to)
    match = next((r for r in rows if r["client"] == client_name), None)
    if not match:
        conn.close()
        flash(tr.get(
            "mi_rebuild_no_shifts",
            "Nema smjena u planu za ovog klijenta u zadatom periodu."
        ), "error")
        return redirect(f"/invoices/view?invoice_number={urllib.parse.quote(invoice_number)}")
    designation = invoice_designation_text(match)
    plan_item = {
        "designation": designation,
        "amount":      round(float(match.get("amount") or 0), 2),
        "vat_rate":    round(float(match.get("vat_rate") or 0) * 100, 2),
    }
    # Preserve extra manual rows (deduction, additional service,
    # custom note, discount, etc.). Convention: item[0] is the
    # plan-generated service line; item[1:] belong to the admin.
    # Replace only the first, keep the tail unchanged so a
    # rebuild doesn't nuke a hand-written "- 20 € (discount)".
    prior_items = []
    try:
        prior_row = c.execute(
            "SELECT items_json FROM manual_invoice_drafts WHERE invoice_number=?",
            (invoice_number,)
        ).fetchone()
        if prior_row and prior_row[0]:
            prior_items = json.loads(prior_row[0]) or []
    except Exception:
        prior_items = []
    # Defensive: legacy or hand-edited items_json might be a dict, a
    # string, or a list containing non-dict garbage. Anything but a
    # list-of-dicts is treated as empty extras so slicing and
    # extra.get(...) below stay safe.
    if not isinstance(prior_items, list):
        prior_items = []
    prior_items = [x for x in prior_items if isinstance(x, dict)]
    extra_items = prior_items[1:] if len(prior_items) > 1 else []
    new_items = [plan_item] + extra_items
    items_json = json.dumps(new_items, ensure_ascii=False)
    # Totals must include the preserved extra rows so the summary
    # on the invoice actually matches the item list.
    total_ht = round(float(match.get("amount") or 0), 2)
    total_vat = round(float(match.get("vat_amount") or 0), 2)
    for extra in extra_items:
        try:
            amt = float(extra.get("amount") or 0)
            vr  = float(extra.get("vat_rate") or 0) / 100.0
            total_ht  += amt
            total_vat += amt * vr
        except (TypeError, ValueError):
            continue
    total_ht  = round(total_ht, 2)
    total_vat = round(total_vat, 2)
    total_ttc = round(total_ht + total_vat, 2)
    now_str   = lux_now().strftime("%Y-%m-%d %H:%M")
    # Draft: swap items + totals; keep client/address/invoice_date.
    # If the draft row is missing (partial delete, DB import gap,
    # race with another admin) bail out cleanly BEFORE writing to
    # invoice_records so we don't end up with a record that's out
    # of sync with a non-existent draft.
    c.execute(
        "UPDATE manual_invoice_drafts SET items_json=?, total_ht=?, total_vat=?, "
        "total_ttc=?, created_at=? WHERE invoice_number=?",
        (items_json, total_ht, total_vat, total_ttc, now_str, invoice_number),
    )
    if c.rowcount != 1:
        conn.rollback(); conn.close()
        flash(tr.get("invoice_not_found", "Faktura nije pronadjena."), "error")
        return redirect("/invoices")
    # Record: update HT/VAT/TTC only. paid/sent/paid_date/sent_date
    # left as-is on purpose so a legitimately paid invoice keeps its
    # payment audit trail even after we resync the line items.
    c.execute(
        "UPDATE invoice_records SET amount=?, vat_amount=?, total=? "
        "WHERE invoice_number=? AND COALESCE(deleted,0)=0",
        (total_ht, total_vat, total_ttc, invoice_number),
    )
    conn.commit(); conn.close()
    flash(
        tr.get(
            "mi_rebuild_ok",
            "Stavke fakture #{n} obnovljene iz trenutnog plana. Sati: {h}, TTC: {t} EUR"
        ).replace("{n}", str(invoice_number))
         .replace("{h}", f"{match.get('hours', 0):.2f}")
         .replace("{t}", f"{total_ttc:.2f}"),
        "ok",
    )
    return redirect(f"/invoices/view?invoice_number={urllib.parse.quote(invoice_number)}")


@app.route("/invoices/manual/vat_rate", methods=["POST"])
def invoices_manual_add_vat_rate():
    """Persist a new custom VAT rate so it appears in the dropdown next time."""
    if session.get("role") != "admin":
        return {"ok": False, "error": "forbidden"}, 403
    try:
        rate = float((request.form.get("rate", "") or "0").replace(",", "."))
    except (ValueError, TypeError):
        return {"ok": False, "error": "invalid rate"}, 400
    if rate < 0 or rate > 100:
        return {"ok": False, "error": "out of range"}, 400
    rate = round(rate, 2)
    conn = get_conn(); c = conn.cursor()
    # Idempotent: don't double-insert the same rate
    exists = c.execute("SELECT 1 FROM invoice_custom_vat_rates WHERE rate=?",
                       (rate,)).fetchone()
    if not exists and rate not in (0, 3, 8, 17):
        c.execute("INSERT INTO invoice_custom_vat_rates (rate, created_at) VALUES (?,?)",
                  (rate, lux_now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    conn.close()
    return {"ok": True, "rate": rate}


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
    # ?inline=1 → render in <iframe> for viewer; otherwise force download
    inline = request.args.get("inline", "").strip() == "1"
    return send_file(pdf, as_attachment=not inline, download_name=f"{fname}.pdf",
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


@app.route("/invoices/delete", methods=["POST"])
def invoices_delete():
    if session.get("role") != "admin":
        return redirect("/")
    invoice_number = request.form.get("invoice_number", "").strip()
    next_url = (request.form.get("next", "") or "").strip()
    redirect_args = {"skip_auto": "1"}
    if invoice_number:
        conn = get_conn(); c = conn.cursor()
        row = c.execute(
            "SELECT date_from, date_to, invoice_date FROM invoice_records WHERE invoice_number = ?",
            (invoice_number,)
        ).fetchone()
        if row:
            if row[0]:
                redirect_args["date_from"] = row[0]
            if row[1]:
                redirect_args["date_to"] = row[1]
            if row[2]:
                redirect_args["invoice_date"] = row[2]
        c.execute("DELETE FROM manual_invoice_drafts WHERE invoice_number = ?", (invoice_number,))
        c.execute("DELETE FROM invoice_records WHERE invoice_number = ?", (invoice_number,))
        conn.commit(); conn.close()
    # Preserve user's page/filter/search if 'next' came from /invoices.
    # Append skip_auto=1 so we don't regenerate on the redirect.
    if next_url.startswith("/invoices"):
        sep = "&" if "?" in next_url else "?"
        # Replace any existing page reference is unnecessary — we just append
        # skip_auto and let the existing page=N stay; clamp will fix overshoot.
        return redirect(f"{next_url}{sep}skip_auto=1#invoice-list")
    return redirect("/invoices?" + urllib.parse.urlencode(redirect_args) + "#invoice-list")


# ═══════════════════════════════════════════════════════════════════════════
#  INVOICE BULK ACTIONS — multi-row select + mass paid/sent/delete/ZIP
# ═══════════════════════════════════════════════════════════════════════════

def _validate_invoice_numbers(conn, raw_list):
    """Return list of invoice numbers that exist and are not soft-deleted.
    Filters out anything not in DB so a crafted POST cannot affect arbitrary rows."""
    if not raw_list:
        return []
    seen = []
    nums = []
    for n in raw_list:
        s = (n or "").strip()
        if s and s not in seen and len(s) < 64:
            seen.append(s); nums.append(s)
    if not nums:
        return []
    c = conn.cursor()
    placeholders = ",".join(["?"] * len(nums))
    rows = c.execute(
        f"SELECT invoice_number FROM invoice_records "
        f"WHERE invoice_number IN ({placeholders}) AND COALESCE(deleted,0)=0",
        tuple(nums),
    ).fetchall()
    return [r[0] for r in rows]


@app.route("/invoices/bulk_action", methods=["POST"])
def invoices_bulk_action():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    action   = request.form.get("action", "").strip()
    raw_nums = request.form.getlist("invoice_numbers[]") or request.form.getlist("invoice_numbers")
    next_url = request.form.get("next", "/invoices").strip()
    if not next_url.startswith("/invoices"):
        next_url = "/invoices"

    conn = get_conn(); c = conn.cursor()
    valid = _validate_invoice_numbers(conn, raw_nums)
    if not valid:
        conn.close()
        flash(tr.get("bulk_no_selection", "No invoices selected."), "error")
        return redirect(next_url)

    placeholders = ",".join(["?"] * len(valid))
    affected = 0

    if action == "mark_paid":
        today = lux_now().strftime("%Y-%m-%d")
        c.execute(
            f"UPDATE invoice_records SET paid=1, paid_date=? "
            f"WHERE invoice_number IN ({placeholders})",
            (today,) + tuple(valid),
        )
        affected = getattr(c, "rowcount", 0) or len(valid)
    elif action == "mark_unpaid":
        c.execute(
            f"UPDATE invoice_records SET paid=0, paid_date='' "
            f"WHERE invoice_number IN ({placeholders})",
            tuple(valid),
        )
        affected = getattr(c, "rowcount", 0) or len(valid)
    elif action == "mark_sent":
        today = lux_now().strftime("%Y-%m-%d")
        c.execute(
            f"UPDATE invoice_records SET sent=1, sent_date=? "
            f"WHERE invoice_number IN ({placeholders})",
            (today,) + tuple(valid),
        )
        affected = getattr(c, "rowcount", 0) or len(valid)
    elif action == "mark_unsent":
        c.execute(
            f"UPDATE invoice_records SET sent=0, sent_date='' "
            f"WHERE invoice_number IN ({placeholders})",
            tuple(valid),
        )
        affected = getattr(c, "rowcount", 0) or len(valid)
    elif action == "delete":
        # Hard delete — same behaviour as the single-row delete
        c.execute(
            f"DELETE FROM manual_invoice_drafts WHERE invoice_number IN ({placeholders})",
            tuple(valid),
        )
        c.execute(
            f"DELETE FROM invoice_records WHERE invoice_number IN ({placeholders})",
            tuple(valid),
        )
        affected = len(valid)
    else:
        conn.close()
        flash("Unknown bulk action: " + (action or "?"), "error")
        return redirect(next_url)

    conn.commit(); conn.close()
    flash(f"{tr.get('bulk_action_done','Action completed')}: {affected}", "ok")
    sep = "&" if "?" in next_url else "?"
    return redirect(f"{next_url}{sep}skip_auto=1#invoice-list")


@app.route("/invoices/bulk_download", methods=["POST"])
def invoices_bulk_download():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    raw_nums = request.form.getlist("invoice_numbers[]") or request.form.getlist("invoice_numbers")
    next_url = request.form.get("next", "/invoices").strip()
    if not next_url.startswith("/invoices"):
        next_url = "/invoices"

    conn = get_conn()
    valid = _validate_invoice_numbers(conn, raw_nums)
    if not valid:
        conn.close()
        flash(tr.get("bulk_no_selection", "No invoices selected."), "error")
        return redirect(next_url)

    tmp = tempfile.NamedTemporaryFile(prefix="luxmann_invoices_", suffix=".zip", delete=False)
    tmp_path = tmp.name
    tmp.close()

    skipped = []
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for inv_num in valid:
                pdf_bytes, fname = _build_invoice_pdf_for_email(conn, inv_num)
                if not pdf_bytes:
                    skipped.append(inv_num)
                    continue
                zf.writestr(fname, pdf_bytes)
    except Exception as e:
        conn.close()
        try: os.remove(tmp_path)
        except Exception: pass
        flash(f"ZIP error: {e.__class__.__name__}: {str(e)[:200]}", "error")
        return redirect(next_url)
    conn.close()

    @after_this_request
    def _cleanup(response):
        try: os.remove(tmp_path)
        except Exception: pass
        return response

    stamp = lux_now().strftime("%Y%m%d_%H%M")
    return send_file(
        tmp_path,
        as_attachment=True,
        download_name=f"factures_{stamp}_{len(valid) - len(skipped)}.zip",
        mimetype="application/zip",
    )


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


@app.route("/invoices/mark_paid", methods=["POST"])
def invoices_mark_paid():
    if session.get("role") != "admin":
        return redirect("/")
    f = request.form
    invoice_no   = f.get("invoice_number", "").strip()
    paid         = 1 if f.get("paid", "0") == "1" else 0
    date_from    = f.get("date_from", "").strip()
    date_to      = f.get("date_to", "").strip()
    invoice_date = f.get("invoice_date", "").strip()
    client       = f.get("client", "").strip()
    next_url     = f.get("next", "").strip()
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
            f.get("amount", 0) or 0, f.get("vat_amount", 0) or 0, f.get("total", 0) or 0,
            paid, lux_now().strftime("%Y-%m-%d") if paid else "",
        ))
        conn.commit(); conn.close()
    if f.get("ajax") == "1":
        return {"ok": True, "paid": bool(paid)}
    if next_url.startswith("/invoices"):
        return redirect(next_url)
    return redirect(f"/invoices?date_from={urllib.parse.quote(date_from)}&date_to={urllib.parse.quote(date_to)}&invoice_date={urllib.parse.quote(invoice_date)}")


@app.route("/invoices/mark_sent", methods=["POST"])
def invoices_mark_sent():
    if session.get("role") != "admin":
        return redirect("/")
    f = request.form
    invoice_no = f.get("invoice_number", "").strip()
    sent = 1 if f.get("sent", "0") == "1" else 0
    if invoice_no:
        conn = get_conn(); c = conn.cursor()
        c.execute("UPDATE invoice_records SET sent = ?, sent_date = ? WHERE invoice_number = ?", (
            sent, lux_now().strftime("%Y-%m-%d") if sent else "", invoice_no,
        ))
        conn.commit(); conn.close()
    if f.get("ajax") == "1":
        return {"ok": True, "sent": bool(sent)}
    next_url = f.get("next", "").strip()
    if next_url.startswith("/invoices"):
        return redirect(next_url)
    return redirect(request.referrer or "/invoices")


# ═══════════════════════════════════════════════════════════════════════════
#  INVOICE EMAIL — admin UI, send, schedule, scheduler endpoint
# ═══════════════════════════════════════════════════════════════════════════

def _get_invoice_email_template(conn, lang):
    """Return (subject, body) — preferred language, or any default, or empty."""
    c = conn.cursor()
    row = c.execute(
        "SELECT subject, body FROM invoice_email_templates "
        "WHERE language=? AND is_default=1 ORDER BY id LIMIT 1",
        (lang or "fr",)
    ).fetchone()
    if row:
        return row[0] or "", row[1] or ""
    row = c.execute(
        "SELECT subject, body FROM invoice_email_templates "
        "WHERE is_default=1 ORDER BY id LIMIT 1"
    ).fetchone()
    return (row[0] if row else "", row[1] if row else "")


# ═══════════════════════════════════════════════════════════════════════════
#  REMINDER (RAPPEL) — PDF + email composer for unpaid invoices
# ═══════════════════════════════════════════════════════════════════════════

def _load_reminder_records(conn, invoice_number=None, client_name=None):
    """Resolve a list of unpaid invoice records for the reminder.
    - invoice_number → single record (regardless of paid state, so an admin
      can still send a 'final notice' on a paid one if needed)
    - client_name    → all UNPAID invoices for that client
    Adds the best-effort client_address to each row for the PDF header.
    """
    c = conn.cursor()
    records = []
    if invoice_number:
        r = c.execute(
            "SELECT invoice_number, client_name, date_from, date_to, invoice_date, "
            "amount, vat_amount, total, paid, paid_date, COALESCE(sent,0), "
            "COALESCE(sent_date,''), COALESCE(source,'auto') "
            "FROM invoice_records WHERE invoice_number=? AND COALESCE(deleted,0)=0",
            (invoice_number,),
        ).fetchone()
        if r:
            records = [invoice_record_to_dict(r)]
    elif client_name:
        records = _unpaid_invoices_for_client(conn, client_name)
    # Pull a client_address from the manual draft (if any) or client profile
    if records:
        client = records[0].get("client", "")
        addr = ""
        manual = c.execute(
            "SELECT client_address FROM manual_invoice_drafts "
            "WHERE LOWER(client_name)=LOWER(?) ORDER BY id DESC LIMIT 1",
            (client,),
        ).fetchone()
        if manual and manual[0]:
            addr = manual[0]
        else:
            prof = c.execute(
                "SELECT custom_address FROM client_invoice_profiles WHERE client_name=?",
                (client,),
            ).fetchone()
            if prof and prof[0]:
                addr = prof[0]
        for rec in records:
            rec["_client_address_html"] = addr
    return records


@app.route("/invoices/reminder")
def invoices_reminder_pdf():
    """Download the reminder PDF.
    Query options:
      ?invoice_number=X  — single invoice
      ?client=NAME       — all UNPAID invoices for that client (consolidated)
    """
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    inv_num = request.args.get("invoice_number", "").strip()
    client  = request.args.get("client", "").strip()
    lang    = (session.get("lang") or "fr")
    if lang not in REMINDER_PDF_STRINGS:
        lang = "fr"

    conn = get_conn()
    records = _load_reminder_records(
        conn, invoice_number=inv_num or None, client_name=client or None
    )
    settings = get_invoice_settings(conn)
    conn.close()
    if not records:
        flash(tr.get("reminder_no_unpaid",
                     "Nema neplaćenih faktura za podsjetnik."), "error")
        return redirect(request.referrer or "/invoices")

    pdf = build_reminder_pdf(records, settings, language=lang)
    fname_client = re.sub(r"[^A-Za-z0-9_-]+", "_",
                          (records[0].get("client") or "").strip())[:40] or "client"
    stamp = lux_now().strftime("%Y%m%d")
    download_name = f"rappel_{fname_client}_{stamp}.pdf"
    return send_file(pdf, as_attachment=True,
                     download_name=download_name, mimetype="application/pdf")


@app.route("/invoices/reminder/preview")
def invoices_reminder_preview_pdf():
    """Same as /invoices/reminder but inline (for iframe / preview)."""
    if session.get("role") != "admin":
        return redirect("/")
    inv_num = request.args.get("invoice_number", "").strip()
    client  = request.args.get("client", "").strip()
    lang    = (session.get("lang") or "fr")
    if lang not in REMINDER_PDF_STRINGS:
        lang = "fr"
    conn = get_conn()
    records = _load_reminder_records(
        conn, invoice_number=inv_num or None, client_name=client or None
    )
    settings = get_invoice_settings(conn)
    conn.close()
    if not records:
        return redirect("/invoices")
    pdf = build_reminder_pdf(records, settings, language=lang)
    return send_file(pdf, as_attachment=False,
                     download_name="rappel.pdf", mimetype="application/pdf")


@app.route("/invoices/email", methods=["GET"])
def invoices_email():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    invoice_number = request.args.get("invoice_number", "").strip()
    bulk_client    = request.args.get("client", "").strip()
    email_type     = (request.args.get("type", "invoice") or "invoice").strip()
    if email_type not in ("invoice", "reminder"):
        email_type = "invoice"
    is_reminder = (email_type == "reminder")
    # Bulk (client-only) mode is reminder-specific: a regular invoice email
    # has nothing to attach without an invoice_number. Drop the client param
    # if someone hits the page with ?client=X&type=invoice.
    if bulk_client and not is_reminder:
        bulk_client = ""
    is_bulk = bool(bulk_client) and not invoice_number
    if not invoice_number and not is_bulk:
        return redirect("/invoices")

    conn = get_conn(); c = conn.cursor()

    if is_bulk:
        # Bulk reminder for all unpaid invoices of a client
        unpaid = _unpaid_invoices_for_client(conn, bulk_client)
        if not unpaid:
            conn.close()
            flash(tr.get("reminder_no_unpaid",
                          "Nema neplaćenih faktura za podsjetnik."), "error")
            return redirect(request.referrer or "/invoices")
        first = unpaid[0]
        rec = (first["invoice_number"], first["client"], first["date_from"],
               first["date_to"], first["invoice_date"], first["amount"],
               first["vat_amount"], first["total"], 0 if first["sent"] is False else 1,
               first.get("sent_date",""), first.get("source","auto"))
        client_for_lookup = bulk_client
    else:
        rec = c.execute("""
            SELECT invoice_number, client_name, date_from, date_to, invoice_date,
                   amount, vat_amount, total, COALESCE(sent,0), COALESCE(sent_date,''), COALESCE(source,'auto')
            FROM invoice_records WHERE invoice_number=? AND COALESCE(deleted,0)=0
        """, (invoice_number,)).fetchone()
        if not rec:
            conn.close()
            flash(tr.get("invoice_not_found", "Faktura nije pronadjena."), "error")
            return redirect("/invoices")
        client_for_lookup = rec[1]

    # Pre-fill recipient from client profile if available
    prof = c.execute(
        "SELECT email FROM client_invoice_profiles WHERE client_name=?",
        (client_for_lookup,)
    ).fetchone()
    recipient = (prof[0] if prof else "") or ""

    lang = session.get("lang", "fr")
    if lang not in ("fr", "en", "bos", "de", "pt"):
        lang = "fr"

    if is_reminder and is_bulk:
        # Bulk reminder: aggregated context across all unpaid invoices.
        # 'unpaid' was already loaded above.
        total_due = sum(float(r.get("total") or 0) for r in unpaid)
        ctx = _invoice_email_context(conn, rec[0])
        ctx.update({
            "invoice_count":   str(len(unpaid)),
            "total_due":       f"{total_due:.2f} EUR",
            # keep {invoice_number} as the most recent one so it still renders
            # but the bulk-specific template will use {invoice_count} instead
        })
        subject_tpl = DEFAULT_REMINDER_BULK_SUBJECT.get(lang, DEFAULT_REMINDER_BULK_SUBJECT["fr"])
        body_tpl    = DEFAULT_REMINDER_BULK_BODY.get(lang,    DEFAULT_REMINDER_BULK_BODY["fr"])
        unpaid_count = len(unpaid)
    elif is_reminder:
        subject_tpl = DEFAULT_REMINDER_SUBJECT.get(lang, DEFAULT_REMINDER_SUBJECT["fr"])
        body_tpl    = DEFAULT_REMINDER_BODY.get(lang,    DEFAULT_REMINDER_BODY["fr"])
        ctx = _invoice_email_context(conn, rec[0])
        unpaid_count = 0
    else:
        subject_tpl, body_tpl = _get_invoice_email_template(conn, lang)
        ctx = _invoice_email_context(conn, invoice_number)
        unpaid_count = 0
    subject = _render_email_template(subject_tpl, ctx)
    body    = _render_email_template(body_tpl, ctx)
    conn.close()

    # BCP-47 locale for the JS calendar's Intl.DateTimeFormat (month label
    # and weekday initials). Keep the mapping conservative so the calendar
    # always renders in the user's UI language even if some Intl data is
    # missing — falling back to fr-FR matches the historical default.
    lang_locale = {
        "bos": "bs-BA", "en": "en-US", "fr": "fr-FR",
        "de":  "de-DE", "pt": "pt-PT",
    }.get(lang, "fr-FR")

    smtp_ready = bool(SMTP_HOST and SMTP_FROM)
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
      .em-shell { max-width:780px; margin:24px auto; padding:0 16px; }
      .em-card { background:{{ '#161618' if dark else 'white' }}; border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; border-radius:14px; padding:22px; box-shadow:0 4px 14px rgba(0,0,0,.08); }
      .em-card h2 { margin:0 0 4px; }
      .em-meta { font-size:13px; color:{{ '#94a3b8' if dark else '#64748b' }}; margin-bottom:18px; }
      .em-label { font-size:12px; font-weight:700; color:{{ '#94a3b8' if dark else '#64748b' }}; margin:14px 0 4px; display:block; }
      .em-input, .em-area { width:100%; padding:10px 12px; border-radius:8px; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }}; background:{{ '#0f0f10' if dark else '#fff' }}; color:{{ '#e2e8f0' if dark else '#0f172a' }}; font-size:14px; box-sizing:border-box; }
      .em-area { min-height:200px; font-family:inherit; resize:vertical; }
      .em-row { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
      .em-pdf { display:flex; align-items:center; gap:10px; padding:10px 14px; border-radius:10px; background:{{ '#1d1d1f' if dark else '#f1f5f9' }}; margin-top:10px; font-size:13px; }
      .em-actions { display:flex; gap:10px; flex-wrap:wrap; margin-top:18px; }
      .em-btn { flex:1; min-width:160px; padding:12px; border-radius:10px; border:none; cursor:pointer; font-weight:700; font-size:14px; }
      .em-btn.primary { background:#16a34a; color:white; }
      .em-btn.draft   { background:#6b7280; color:white; }
      .em-btn.sched   { background:#2563eb; color:white; }
      .em-btn.test    { background:#f59e0b; color:white; }
      .em-warn { padding:10px 14px; border-radius:8px; background:#fef3c7; color:#92400e; border:1px solid #fde68a; font-size:13px; margin-bottom:14px; }
      .em-vars { font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }}; padding:8px 12px; background:{{ '#0f0f10' if dark else '#f8fafc' }}; border-radius:8px; border:1px dashed {{ '#2c2c30' if dark else '#e2e8f0' }}; }

      /* ── Spark-style "Send later" panel ──────────────────────── */
      .em-sl-panel { margin-top:16px; padding:14px; border-radius:12px;
                     background:{{ '#0f0f10' if dark else '#f8fafc' }};
                     border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
      .em-sl-panel [hidden], .em-notice-chip[hidden] { display:none !important; }
      .em-sl-row { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
      .em-sl-chip { padding:8px 14px; border-radius:999px;
                    border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};
                    background:{{ '#1d1d1f' if dark else '#ffffff' }};
                    color:{{ '#e2e8f0' if dark else '#0f172a' }};
                    font-weight:600; font-size:13px; cursor:pointer;
                    transition:background .15s, border-color .15s, color .15s; }
      .em-sl-chip:hover { border-color:#2563eb; }
      .em-sl-chip.active { background:#2563eb; color:#ffffff; border-color:#2563eb; }
      .em-sl-summary { margin-top:10px; font-size:13px; font-weight:600;
                       color:{{ '#86efac' if dark else '#16a34a' }};
                       min-height:18px; display:flex; align-items:center; gap:8px; }
      .em-sl-selected { margin-top:10px; padding:9px 12px; border-radius:10px;
                        background:#2563eb; color:#ffffff; font-size:13px;
                        font-weight:700; display:flex; align-items:center;
                        justify-content:space-between; gap:8px;
                        box-shadow:0 6px 18px rgba(37,99,235,.22); }
      .em-sl-selected small { opacity:.85; font-weight:600; }
      .em-sl-clearx { background:transparent; border:none; cursor:pointer;
                      color:{{ '#94a3b8' if dark else '#64748b' }};
                      font-size:14px; padding:2px 6px; border-radius:4px; }
      .em-sl-clearx:hover { background:{{ '#2c2c30' if dark else '#e2e8f0' }}; }
      .em-sl-warn { margin-top:10px; padding:8px 12px; border-radius:8px;
                    background:#fef3c7; color:#92400e; font-size:13px; font-weight:600;
                    border:1px solid #fde68a; }

      /* ── Calendar modal ─────────────────────────────────────── */
      .em-sl-overlay { position:fixed; inset:0; background:rgba(0,0,0,.5);
                       display:none; align-items:center; justify-content:center;
                       z-index:9999; padding:16px; }
      .em-sl-overlay.open { display:flex; }
      .em-sl-modal { background:{{ '#161618' if dark else '#ffffff' }};
                     color:{{ '#e2e8f0' if dark else '#0f172a' }};
                     border-radius:16px; padding:18px; width:340px; max-width:92vw;
                     box-shadow:0 20px 50px rgba(0,0,0,.4);
                     border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; }
      .em-sl-mhdr { display:flex; justify-content:space-between; align-items:center;
                    margin-bottom:12px; font-weight:700; font-size:15px; }
      .em-sl-mhdr button { background:transparent; border:none;
                           color:{{ '#e2e8f0' if dark else '#0f172a' }};
                           font-size:18px; cursor:pointer; padding:4px 10px;
                           border-radius:6px; }
      .em-sl-mhdr button:hover { background:{{ '#2c2c30' if dark else '#f1f5f9' }}; }
      .em-sl-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:2px;
                    font-size:13px; }
      .em-sl-grid .dow { color:{{ '#94a3b8' if dark else '#64748b' }};
                         padding:6px 0; text-align:center; font-weight:700;
                         font-size:11px; text-transform:uppercase; letter-spacing:.5px; }
      .em-sl-grid .day { padding:9px 0; text-align:center; border-radius:8px;
                         cursor:pointer; user-select:none; }
      .em-sl-grid .day:hover { background:{{ '#2c2c30' if dark else '#e0e7ff' }}; }
      .em-sl-grid .day.muted { color:{{ '#475569' if dark else '#cbd5e1' }}; }
      .em-sl-grid .day.today { box-shadow:inset 0 0 0 1px #2563eb; font-weight:700; }
      .em-sl-grid .day.selected { background:#2563eb; color:#ffffff; font-weight:700; }
      .em-sl-grid .day.disabled { color:{{ '#475569' if dark else '#cbd5e1' }};
                                  cursor:not-allowed; }
      .em-sl-grid .day.disabled:hover { background:transparent; }
      .em-sl-time { margin-top:14px; display:flex; align-items:center; gap:10px;
                    font-size:13px; font-weight:600; }
      .em-sl-time input { flex:1; padding:8px 10px; border-radius:8px;
                          border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};
                          background:{{ '#0f0f10' if dark else '#ffffff' }};
                          color:{{ '#e2e8f0' if dark else '#0f172a' }};
                          font-size:14px; font-family:inherit; }
      .em-sl-mfoot { display:flex; gap:8px; margin-top:14px; }
      .em-sl-mfoot button { flex:1; padding:10px; border-radius:8px; border:none;
                            font-weight:700; font-size:13px; cursor:pointer;
                            font-family:inherit; }
      .em-sl-mfoot .clear { background:#6b7280; color:#ffffff; }
      .em-sl-mfoot .set   { background:#2563eb; color:#ffffff; }

      /* ── Scheduled notice chip (near subject/body) ──────────── */
      .em-notice-chip { display:inline-flex; align-items:center; gap:8px;
                        margin:14px 0 0; padding:8px 14px; border-radius:999px;
                        background:#2563eb; color:#ffffff;
                        font-size:13px; font-weight:600; }
      .em-notice-chip .em-notice-x { background:rgba(255,255,255,.18);
                                     border:none; color:#ffffff; cursor:pointer;
                                     border-radius:999px; width:20px; height:20px;
                                     display:inline-flex; align-items:center;
                                     justify-content:center; font-size:12px;
                                     line-height:1; padding:0; }
      .em-notice-chip .em-notice-x:hover { background:rgba(255,255,255,.28); }

      /* ── Cancel scheduled send (red row in panel) ───────────── */
      .em-sl-cancel { margin-top:10px; padding:10px 14px; border-radius:10px;
                      border:1px solid #dc2626; background:transparent;
                      color:#dc2626; font-size:13px; font-weight:700;
                      cursor:pointer; width:100%; font-family:inherit; }
      .em-sl-cancel:hover { background:rgba(220,38,38,.10); }

      /* ── Custom time dropdown (15-min slots) ────────────────── */
      .em-sl-time { position:relative; }
      .em-sl-tdrop { position:absolute; left:0; right:0; top:100%;
                     margin-top:4px; max-height:170px; overflow-y:auto;
                     background:{{ '#161618' if dark else '#ffffff' }};
                     color:{{ '#e2e8f0' if dark else '#0f172a' }};
                     border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};
                     border-radius:10px; z-index:10;
                     box-shadow:0 8px 24px rgba(0,0,0,.25);
                     display:none; padding:4px; }
      .em-sl-tdrop.open { display:block; }
      .em-sl-tdrop button { display:block; width:100%; text-align:left;
                            padding:8px 12px; border:none; background:transparent;
                            color:inherit; font-size:13px; font-family:inherit;
                            border-radius:6px; cursor:pointer; }
      .em-sl-tdrop button:hover,
      .em-sl-tdrop button.current { background:{{ '#2c2c30' if dark else '#e0e7ff' }}; }
      .em-sl-tdrop button.current { font-weight:700; color:#2563eb; }
    </style>
    <div class="em-shell">
      {% with msgs = get_flashed_messages(with_categories=true) %}
      {% for cat, msg in msgs %}
      <div style="padding:10px 14px;border-radius:8px;margin-bottom:12px;background:{% if cat=='error' %}#ef4444{% else %}#16a34a{% endif %};color:white;font-weight:600;">{{ msg }}</div>
      {% endfor %}
      {% endwith %}
      <div class="em-card">
        <h2>{% if is_reminder %}📮 {{ tr.get("send_reminder","Poslati podsjetnik") }}{% else %}✉ {{ tr.get("send_email","Poslati fakturu emailom") }}{% endif %}</h2>
        <div class="em-meta">
          {% if is_bulk %}
            <b>{{ bulk_client }}</b> — {{ tr.get("unpaid","Nije placena") }}: {{ unpaid_count }}
          {% else %}
            {{ tr.get("invoice_number","Numéro") }} <b>{{ rec[0] }}</b> ·
            {{ rec[1] }} · {{ "%.2f"|format(rec[7]) }} EUR
          {% endif %}
        </div>
        {% if not smtp_ready %}
        <div class="em-warn">⚠ SMTP nije konfigurisan. Postavi SMTP_HOST i SMTP_FROM u Render env vars.</div>
        {% endif %}

        <form method="post" action="/invoices/email/send">
          <input type="hidden" name="invoice_number" value="{{ invoice_number }}">
          <input type="hidden" name="email_type" value="{{ email_type }}">
          {% if is_bulk %}<input type="hidden" name="client" value="{{ bulk_client }}">{% endif %}

          <label class="em-label">{{ tr.get("email_to","Primalac") }} <span style="color:#ef4444;">*</span></label>
          <input class="em-input" type="email" name="recipient" value="{{ recipient }}" required>

          <div class="em-row">
            <div>
              <label class="em-label">CC</label>
              <input class="em-input" type="text" name="cc" value="" placeholder="email1@x.com, email2@x.com">
            </div>
            <div>
              <label class="em-label">BCC</label>
              <input class="em-input" type="text" name="bcc" value="">
            </div>
          </div>

          {% if not is_reminder %}
          <div class="em-notice-chip" id="emScheduledNotice" hidden>
            <span>✈</span>
            <span id="emScheduledNoticeText"></span>
            <button type="button" class="em-notice-x" id="emScheduledNoticeX"
                    aria-label="{{ tr.get('email_cancel_scheduled','Cancel scheduled send') }}">✕</button>
          </div>
          {% endif %}

          <label class="em-label">{{ tr.get("subject","Naslov") }}</label>
          <input class="em-input" type="text" name="subject" value="{{ subject }}" required>

          <label class="em-label">{{ tr.get("body","Tekst poruke") }}</label>
          <textarea class="em-area" name="body" required>{{ body }}</textarea>

          <div class="em-vars">
            {{ tr.get("template_vars","Promjenljive") }}:
            <code>{client_name}</code> <code>{invoice_number}</code>
            <code>{invoice_month}</code> <code>{invoice_date}</code>
            <code>{total_ttc}</code> <code>{company_name}</code>
            <code>{company_address}</code> <code>{company_phone}</code>
            {% if is_bulk %}<code>{invoice_count}</code> <code>{total_due}</code>{% endif %}
          </div>

          <div class="em-pdf">📎 {{ pdf_name }} ({{ tr.get("pdf_attached","PDF prilog") }})</div>

          {% if not is_reminder %}
          <div class="em-sl-panel" id="emSlPanel">
            <label class="em-label" style="margin-top:0;" id="emSlHeader"
                   data-base="⏰ {{ tr.get('email_send_later','Send later') }}">
              ⏰ {{ tr.get("email_send_later","Send later") }}
            </label>
            <div class="em-sl-row">
              <button type="button" class="em-sl-chip" data-preset="today18">
                {{ tr.get("email_today","Today") }} 18:00
              </button>
              <button type="button" class="em-sl-chip" data-preset="tomorrow10">
                {{ tr.get("email_tomorrow","Tomorrow") }} 10:00
              </button>
              <button type="button" class="em-sl-chip em-sl-pick" id="emSlPickBtn"
                      data-base="📅 {{ tr.get('email_select_date','Pick a date') }}">
                📅 {{ tr.get("email_select_date","Pick a date") }}
              </button>
            </div>
            <div class="em-sl-selected" id="emSlSelectedBadge" hidden>
              <span id="emSlSelectedText"></span>
              <small>{{ tr.get("email_selected_schedule","Selected") }}</small>
            </div>
            <div class="em-sl-summary" id="emSlSummary" hidden>
              <span id="emSlSummaryText"></span>
              <button type="button" class="em-sl-clearx" id="emSlClearBtn"
                      aria-label="{{ tr.get('email_clear','Clear') }}">✕</button>
            </div>
            <div class="em-sl-warn" id="emSlWarn" hidden>
              ⚠ {{ tr.get("email_schedule_pick_first","Pick a date first before scheduling.") }}
            </div>
            <button type="button" class="em-sl-cancel" id="emSlCancelBtn" hidden>
              ✕ {{ tr.get("email_cancel_scheduled","Cancel scheduled send") }}
            </button>
            <input type="hidden" name="scheduled_at" id="scheduledAt" value="">
          </div>
          {% endif %}

          <div class="em-actions">
            <button class="em-btn primary"  name="action" value="send_now"
                    {% if not smtp_ready %}disabled title="SMTP not configured" style="opacity:.5;cursor:not-allowed;"{% endif %}>📤 {{ tr.get("send_now","Pošalji odmah") }}</button>
            {% if not is_reminder %}
            <button class="em-btn sched"    name="action" value="schedule"
                    {% if not smtp_ready %}disabled title="SMTP not configured" style="opacity:.5;cursor:not-allowed;"{% endif %}>⏰ {{ tr.get("schedule","Zakaži") }}</button>
            <button class="em-btn draft"    name="action" value="draft">💾 {{ tr.get("save_draft","Sačuvaj nacrt") }}</button>
            {% endif %}
            <a class="em-btn" href="{% if is_bulk %}/invoices/client?client={{ bulk_client|urlencode }}{% else %}/invoices/view?invoice_number={{ invoice_number }}{% endif %}" style="background:#6b7280;color:white;text-decoration:none;text-align:center;line-height:24px;">{{ tr["back"] }}</a>
          </div>
        </form>

        <form method="post" action="/invoices/email/test" style="margin-top:14px;">
          <button class="em-btn test">🧪 {{ tr.get("test_smtp","Test SMTP (na SMTP_FROM)") }}</button>
        </form>
      </div>

      {% if not is_reminder %}
      <!-- Calendar modal (Spark-style date/time picker) -->
      <div class="em-sl-overlay" id="emSlOverlay" role="dialog" aria-modal="true"
           aria-labelledby="emSlMonthLabel">
        <div class="em-sl-modal" id="emSlModal">
          <div class="em-sl-mhdr">
            <button type="button" id="emSlPrev" aria-label="prev">‹</button>
            <span id="emSlMonthLabel"></span>
            <button type="button" id="emSlNext" aria-label="next">›</button>
          </div>
          <div class="em-sl-grid" id="emSlGrid"></div>
          <div class="em-sl-time">
            <label for="emSlTime">{{ tr.get("email_time","Time") }}</label>
            <input type="text" id="emSlTime" autocomplete="off" readonly
                   inputmode="numeric" pattern="[0-9]{2}:[0-9]{2}"
                   placeholder="HH:MM" aria-haspopup="listbox"
                   aria-controls="emSlTimeDrop"
                   style="cursor:pointer;">
            <div class="em-sl-tdrop" id="emSlTimeDrop" role="listbox"
                 aria-label="{{ tr.get('email_time','Time') }}"></div>
          </div>
          <div class="em-sl-mfoot">
            <button type="button" class="clear" id="emSlModalClear">
              {{ tr.get("email_clear","Clear") }}
            </button>
            <button type="button" class="set" id="emSlModalSet">
              {{ tr.get("email_set","Set") }}
            </button>
          </div>
        </div>
      </div>

      <script>
      (function(){
        var LANG_LOCALE = {{ lang_locale|tojson }};
        var TXT = {
          scheduled_for: {{ (tr.get("email_scheduled_for","Scheduled for"))|tojson }},
          planned_for:   {{ (tr.get("email_planned_for","Scheduled for"))|tojson }},
          today:         {{ (tr.get("email_today","Today"))|tojson }},
          tomorrow:      {{ (tr.get("email_tomorrow","Tomorrow"))|tojson }}
        };
        // Monday-first day-of-week initials, localized via Intl
        function dowInitials() {
          var out = [];
          // pick a reference Monday and walk forward 7 days
          var ref = new Date(2024, 0, 1); // Mon 2024-01-01
          var fmt = new Intl.DateTimeFormat(LANG_LOCALE, {weekday:"short"});
          for (var i=0; i<7; i++){
            var d = new Date(ref); d.setDate(ref.getDate()+i);
            out.push(fmt.format(d).slice(0,3));
          }
          return out;
        }
        function monthLabel(y, m) {
          var fmt = new Intl.DateTimeFormat(LANG_LOCALE, {month:"long", year:"numeric"});
          return fmt.format(new Date(y, m, 1));
        }
        function pad2(n){ return (n<10?"0":"")+n; }
        function fmtHidden(d){
          return d.getFullYear()+"-"+pad2(d.getMonth()+1)+"-"+pad2(d.getDate())+
                 "T"+pad2(d.getHours())+":"+pad2(d.getMinutes());
        }
        function fmtHuman(d){
          return pad2(d.getDate())+"/"+pad2(d.getMonth()+1)+"/"+d.getFullYear()+
                 " "+pad2(d.getHours())+":"+pad2(d.getMinutes());
        }
        function startOfToday(){
          var d = new Date(); d.setHours(0,0,0,0); return d;
        }
        function sameDay(a, b){
          return a.getFullYear()===b.getFullYear() &&
                 a.getMonth()===b.getMonth() &&
                 a.getDate()===b.getDate();
        }
        function roundUp15(d){
          var m = d.getMinutes();
          var add = (15 - (m % 15)) % 15;
          if (add === 0) add = 15;
          d.setMinutes(m + add, 0, 0);
          return d;
        }

        var panel    = document.getElementById("emSlPanel");
        if (!panel) return;
        var hidden     = document.getElementById("scheduledAt");
        var header     = document.getElementById("emSlHeader");
        var headerBase = (header && header.getAttribute("data-base")) || "";
        var summary    = document.getElementById("emSlSummary");
        var sumText    = document.getElementById("emSlSummaryText");
        var clearX     = document.getElementById("emSlClearBtn");
        var cancelBtn  = document.getElementById("emSlCancelBtn");
        var warn       = document.getElementById("emSlWarn");
        var chips      = panel.querySelectorAll(".em-sl-chip[data-preset]");
        var pickBtn    = document.getElementById("emSlPickBtn");
        var notice     = document.getElementById("emScheduledNotice");
        var noticeText = document.getElementById("emScheduledNoticeText");
        var noticeX    = document.getElementById("emScheduledNoticeX");
        var selectedBadge = document.getElementById("emSlSelectedBadge");
        var selectedText  = document.getElementById("emSlSelectedText");
        var pickBase      = (pickBtn && pickBtn.getAttribute("data-base")) ||
                            (pickBtn ? pickBtn.textContent : "");
        var overlay    = document.getElementById("emSlOverlay");
        var modal      = document.getElementById("emSlModal");
        var monthLbl   = document.getElementById("emSlMonthLabel");
        var grid       = document.getElementById("emSlGrid");
        var timeIn     = document.getElementById("emSlTime");
        var timeDrop   = document.getElementById("emSlTimeDrop");
        var prevBtn    = document.getElementById("emSlPrev");
        var nextBtn    = document.getElementById("emSlNext");
        var setBtn     = document.getElementById("emSlModalSet");
        var clrBtn     = document.getElementById("emSlModalClear");

        // Spark-style "month-day, HH:MM" — uses Intl so labels are
        // localized per session language (e.g. FR "juin 6, 16:13").
        function fmtNotice(d){
          var df = new Intl.DateTimeFormat(LANG_LOCALE,
                                           {month:"long", day:"numeric"});
          return df.format(d) + ", " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
        }

        var current = null;          // current Date | null
        var viewY, viewM;            // currently displayed month
        var picked = null;           // tentative date in modal

        function paintChipFromPreset(preset){
          chips.forEach(function(b){
            b.classList.toggle("active", b.getAttribute("data-preset") === preset);
          });
          pickBtn.classList.toggle("active", preset === "custom");
        }
        function setSchedule(d, preset){
          current = d;
          hidden.value = fmtHidden(d);
          // Single source of truth for the human-friendly label —
          // same formatter as the notice chip so panel + chip read
          // identically (e.g. "juin 6, 16:13" in FR).
          var pretty = fmtNotice(d);
          var planned = TXT.planned_for + " " + pretty;
          warn.hidden = true;
          paintChipFromPreset(preset || "custom");
          // The .em-sl-summary line is redundant with the new blue
          // .em-sl-selected badge — keep it permanently hidden but
          // leave the DOM in place in case we want it back later.
          summary.hidden = true;
          if (selectedBadge && selectedText) {
            selectedText.textContent = planned;
            selectedBadge.hidden = false;
          }
          // Only the custom-date chip should reflect the picked
          // datetime in its label. Quick presets (Today 18:00 /
          // Tomorrow 10:00) keep their own static text so users can
          // tell which preset is active at a glance.
          if (pickBtn && preset === "custom") pickBtn.textContent = pretty;
          // Header label gets the time appended: "Send later (16:30)"
          if (header) {
            header.textContent = headerBase + " (" +
                                 pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ")";
          }
          // Localized notice chip near subject/body
          if (notice) {
            noticeText.textContent = planned;
            notice.hidden = false;
          }
          // Red "Cancel scheduled send" row replaces the small ✕
          if (cancelBtn) cancelBtn.hidden = false;
        }
        function clearSchedule(){
          current = null;
          hidden.value = "";
          summary.hidden = true;
          chips.forEach(function(b){ b.classList.remove("active"); });
          pickBtn.classList.remove("active");
          // Restore the original "📅 Pick a date" label (with emoji)
          // from the data-base attribute captured at boot.
          if (pickBtn) pickBtn.textContent = pickBase;
          if (selectedBadge) selectedBadge.hidden = true;
          if (header) header.textContent = headerBase;
          if (notice) notice.hidden = true;
          if (cancelBtn) cancelBtn.hidden = true;
          if (warn) warn.hidden = true;
        }

        // Quick-preset buttons
        chips.forEach(function(btn){
          btn.addEventListener("click", function(){
            var preset = btn.getAttribute("data-preset");
            var d = new Date();
            if (preset === "today18") {
              // If already past 18:00, slide to tomorrow at 18:00.
              if (d.getHours() >= 18) d.setDate(d.getDate()+1);
              d.setHours(18, 0, 0, 0);
            } else if (preset === "tomorrow10") {
              d.setDate(d.getDate()+1);
              d.setHours(10, 0, 0, 0);
            }
            setSchedule(d, preset);
          });
        });
        clearX.addEventListener("click", clearSchedule);
        if (cancelBtn) cancelBtn.addEventListener("click", clearSchedule);
        if (noticeX)   noticeX.addEventListener("click", clearSchedule);

        // ── Custom time dropdown (15-min slots) ──────────────────
        // Browsers style <input type="time"> very differently and many
        // mobile UAs offer no dropdown at all. Render our own list
        // anchored under the input, seeded around the currently picked
        // time so the most likely choice is one click away.
        function renderTimeDrop(){
          if (!timeDrop) return;
          timeDrop.innerHTML = "";
          var base = new Date();
          var v = (timeIn.value || "").split(":");
          if (v.length === 2) {
            base.setHours(parseInt(v[0],10)||0, parseInt(v[1],10)||0, 0, 0);
          } else {
            base = roundUp15(new Date());
          }
          // Start one slot before the current value so user can scroll back
          // a bit; show 20 slots forward = 5 hours of options.
          base.setMinutes(base.getMinutes() - 15);
          for (var i=0; i<20; i++){
            var t = new Date(base);
            t.setMinutes(t.getMinutes() + i*15);
            var hh = pad2(t.getHours()), mm = pad2(t.getMinutes());
            var label = hh + ":" + mm;
            var btn = document.createElement("button");
            btn.type = "button";
            btn.setAttribute("role", "option");
            btn.textContent = label;
            if (label === timeIn.value) btn.classList.add("current");
            btn.addEventListener("click", function(lbl){
              return function(){
                timeIn.value = lbl;
                closeTimeDrop();
              };
            }(label));
            timeDrop.appendChild(btn);
          }
        }
        function openTimeDrop(){
          renderTimeDrop();
          timeDrop.classList.add("open");
          // Scroll the .current option into view if any
          var cur = timeDrop.querySelector("button.current");
          if (cur) cur.scrollIntoView({block:"center"});
        }
        function closeTimeDrop(){ if (timeDrop) timeDrop.classList.remove("open"); }
        if (timeIn && timeDrop) {
          // Input is readonly + type="text" so iOS/Safari won't pop the
          // native time picker over our custom dropdown. Custom dropdown
          // is the only way to change the value, so no input/keyboard
          // typing listener is needed.
          timeIn.addEventListener("focus", openTimeDrop);
          timeIn.addEventListener("click", openTimeDrop);
          document.addEventListener("click", function(e){
            if (!timeDrop.classList.contains("open")) return;
            if (e.target === timeIn) return;
            if (timeDrop.contains(e.target)) return;
            closeTimeDrop();
          });
        }

        // Modal calendar
        function renderGrid(){
          monthLbl.textContent = monthLabel(viewY, viewM);
          grid.innerHTML = "";
          var dows = dowInitials();
          dows.forEach(function(t){
            var el = document.createElement("div");
            el.className = "dow"; el.textContent = t; grid.appendChild(el);
          });
          var first = new Date(viewY, viewM, 1);
          // Monday-first offset
          var lead = (first.getDay() + 6) % 7;
          var prevMonthLast = new Date(viewY, viewM, 0).getDate();
          var daysInMonth = new Date(viewY, viewM+1, 0).getDate();
          var today = startOfToday();

          // leading muted days from previous month
          for (var i=lead-1; i>=0; i--){
            var d = new Date(viewY, viewM-1, prevMonthLast-i);
            grid.appendChild(makeDay(d, true));
          }
          for (var dd=1; dd<=daysInMonth; dd++){
            grid.appendChild(makeDay(new Date(viewY, viewM, dd), false));
          }
          // trailing muted days to fill 6 rows × 7 cols = 42 cells
          var totalCells = lead + daysInMonth;
          var trail = (7 - (totalCells % 7)) % 7;
          for (var k=1; k<=trail; k++){
            grid.appendChild(makeDay(new Date(viewY, viewM+1, k), true));
          }
          function makeDay(d, muted){
            var el = document.createElement("div");
            el.className = "day" + (muted ? " muted" : "");
            el.textContent = d.getDate();
            var dayMidnight = new Date(d); dayMidnight.setHours(0,0,0,0);
            if (dayMidnight < today) {
              el.classList.add("disabled");
            } else {
              el.addEventListener("click", function(){
                picked = new Date(d);
                renderGrid(); // re-render to update .selected
              });
            }
            if (sameDay(d, new Date())) el.classList.add("today");
            if (picked && sameDay(d, picked)) el.classList.add("selected");
            return el;
          }
        }
        function openModal(){
          var seed = current || new Date();
          viewY = seed.getFullYear();
          viewM = seed.getMonth();
          picked = current ? new Date(current) : null;
          var t = current || roundUp15(new Date());
          timeIn.value = pad2(t.getHours())+":"+pad2(t.getMinutes());
          renderGrid();
          overlay.classList.add("open");
          // focus first interactive element for keyboard users
          setTimeout(function(){ prevBtn.focus(); }, 0);
        }
        function closeModal(){
          overlay.classList.remove("open");
          closeTimeDrop();
        }

        pickBtn.addEventListener("click", openModal);
        prevBtn.addEventListener("click", function(){
          if (viewM === 0) { viewM = 11; viewY -= 1; } else { viewM -= 1; }
          renderGrid();
        });
        nextBtn.addEventListener("click", function(){
          if (viewM === 11) { viewM = 0; viewY += 1; } else { viewM += 1; }
          renderGrid();
        });
        setBtn.addEventListener("click", function(){
          if (!picked) { closeModal(); return; }
          var parts = (timeIn.value || "10:00").split(":");
          var d = new Date(picked);
          d.setHours(parseInt(parts[0]||"10",10), parseInt(parts[1]||"0",10), 0, 0);
          var now = new Date();
          if (d <= now) {
            // refuse past datetimes; snap to next 15 min from now
            d = roundUp15(new Date());
          }
          setSchedule(d, "custom");
          closeModal();
        });
        clrBtn.addEventListener("click", function(){
          clearSchedule();
          closeModal();
        });
        overlay.addEventListener("click", function(e){
          if (e.target === overlay) closeModal();
        });
        document.addEventListener("keydown", function(e){
          if (e.key !== "Escape") return;
          if (timeDrop && timeDrop.classList.contains("open")) {
            closeTimeDrop();
            return;
          }
          if (overlay.classList.contains("open")) closeModal();
        });

        // Form submit guard: block schedule click without a date.
        // e.submitter is the modern path (Chrome, Firefox, Safari 15.4+).
        // Older Safari leaves submitter undefined → we track the last
        // clicked action button manually so the inline warning still
        // fires there. The backend has the same guard as a final net.
        var form = panel.closest("form");
        if (form) {
          var lastSubmitter = null;
          form.querySelectorAll('button[name="action"]').forEach(function(b){
            b.addEventListener("click", function(){ lastSubmitter = b; });
          });
          form.addEventListener("submit", function(e){
            var s = e.submitter || lastSubmitter;
            if (s && s.name === "action" && s.value === "schedule" && !hidden.value) {
              e.preventDefault();
              warn.hidden = false;
              panel.scrollIntoView({behavior:"smooth", block:"center"});
            }
          });
        }
      })();
      </script>
      {% endif %}
    </div>
    """, tr=tr, dark=dark, rec=rec, invoice_number=invoice_number, recipient=recipient,
         subject=subject, body=body, smtp_ready=smtp_ready,
         is_reminder=is_reminder, is_bulk=is_bulk, bulk_client=bulk_client,
         email_type=email_type, unpaid_count=unpaid_count,
         lang_locale=lang_locale,
         pdf_name=(f"rappel_{invoice_number or bulk_client}.pdf"
                   if is_reminder
                   else f"{invoice_number}-facture.pdf"))


@app.route("/invoices/email/send", methods=["POST"])
def invoices_email_send():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    invoice_number = request.form.get("invoice_number", "").strip()
    bulk_client    = request.form.get("client", "").strip()
    email_type     = (request.form.get("email_type", "invoice") or "invoice").strip()
    if email_type not in ("invoice", "reminder"):
        email_type = "invoice"
    is_reminder = (email_type == "reminder")
    # Same rule as the GET composer: client-only submission makes sense
    # only for reminders. Drop a stray client= on a plain invoice POST.
    if bulk_client and not is_reminder:
        bulk_client = ""
    action         = request.form.get("action", "draft").strip()
    recipient      = request.form.get("recipient", "").strip()
    cc_raw         = request.form.get("cc", "").strip()
    bcc_raw        = request.form.get("bcc", "").strip()
    subject        = request.form.get("subject", "").strip()
    body           = request.form.get("body", "").strip()
    scheduled_at   = request.form.get("scheduled_at", "").strip()

    if not invoice_number and not bulk_client:
        return redirect("/invoices")

    def _back_url():
        if invoice_number:
            return (f"/invoices/email?invoice_number={urllib.parse.quote(invoice_number)}"
                    + (f"&type={email_type}" if is_reminder else ""))
        return (f"/invoices/email?client={urllib.parse.quote(bulk_client)}"
                + (f"&type={email_type}" if is_reminder else ""))

    if not _is_valid_email(recipient):
        flash(tr.get("invalid_email", "Neispravna email adresa."), "error")
        return redirect(_back_url())

    # Reminders are send_now-only (queue schema has no email_type/client).
    # Run this BEFORE the SMTP-downgrade check so a missing SMTP cleanly
    # surfaces "SMTP not configured" instead of trying to send and logging
    # a generic failure.
    if is_reminder and action != "send_now":
        flash(tr.get("reminder_send_now_only",
                     "Podsjetnici se mogu samo odmah poslati."), "error")
        action = "send_now"

    # Server-side enforce: send_now / schedule require SMTP config.
    # For reminders we can only send_now → if SMTP is missing, flash and
    # bail out instead of silently downgrading to draft (a reminder draft
    # would be misleading, and the queue can't handle it anyway).
    if action in ("send_now", "schedule") and not (SMTP_HOST and SMTP_FROM):
        if is_reminder:
            flash(tr.get("smtp_not_configured", "SMTP not configured."), "error")
            return redirect(_back_url())
        flash(tr.get("smtp_not_configured_drafted", "SMTP not configured. Saved as draft."), "error")
        action = "draft"

    # Backend guard: schedule without a scheduled_at would silently fall
    # through to draft on line 11216 ("scheduled" if action == "schedule"
    # and scheduled_at else "draft"). The JS guard catches this in modern
    # browsers via e.submitter, but a missing submitter (older Safari,
    # direct POST, automation) would let a "Schedule" click become a
    # nameless draft. Belt-and-suspenders: refuse here too.
    if action == "schedule" and not scheduled_at:
        flash(tr.get("email_schedule_pick_first",
                     "Pick a date first before scheduling."), "error")
        return redirect(_back_url())

    cc_list  = _split_email_list(cc_raw)
    bcc_list = _split_email_list(bcc_raw)
    now_str  = lux_now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_conn(); c = conn.cursor()

    if action == "send_now":
        if is_reminder:
            settings = get_invoice_settings(conn)
            lang_for_pdf = session.get("lang", "fr")
            if lang_for_pdf not in REMINDER_PDF_STRINGS:
                lang_for_pdf = "fr"
            records = _load_reminder_records(
                conn, invoice_number=invoice_number or None,
                client_name=bulk_client or None,
            )
            if not records:
                conn.close()
                flash(tr.get("reminder_no_unpaid",
                              "Nema neplaćenih faktura za podsjetnik."), "error")
                return redirect(_back_url())
            buf = build_reminder_pdf(records, settings, language=lang_for_pdf)
            pdf_bytes = buf.getvalue() if buf else None
            safe_who = re.sub(r"[^A-Za-z0-9_-]+", "_",
                              (records[0].get("client") or ""))[:40] or "client"
            pdf_name = f"rappel_{safe_who}_{lux_now().strftime('%Y%m%d')}.pdf"
        else:
            pdf_bytes, pdf_name = _build_invoice_pdf_for_email(conn, invoice_number)
        if not pdf_bytes:
            conn.close()
            flash(tr.get("invoice_not_found", "Faktura nije pronadjena."), "error")
            return redirect(_back_url())
        ok, err, send_info = _smtp_send(recipient, subject, body, pdf_bytes, pdf_name,
                                        cc=cc_list, bcc=bcc_list)
        msg_id = send_info.get("message_id", "") if send_info else ""
        pdf_sha = hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes else ""
        imap_saved = 0
        imap_error = ""
        # Only attempt IMAP archive on successful SMTP send — appending a
        # never-sent message into Sent would be misleading proof.
        if ok and send_info and send_info.get("raw"):
            iok, ierr = _imap_append_sent(send_info["raw"])
            imap_saved = 1 if iok else 0
            if not iok:
                imap_error = ierr
                app.logger.warning(
                    "IMAP APPEND failed for %s: %s",
                    invoice_number or bulk_client, ierr
                )
        c.execute("""
            INSERT INTO invoice_email_logs
                (invoice_number, recipient, subject, status, error, sent_at,
                 message_id, attachment_sha256, imap_saved, imap_error)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (invoice_number or bulk_client, recipient, subject,
              "sent" if ok else "failed", err if not ok else "", now_str,
              msg_id, pdf_sha, imap_saved, imap_error))
        # Only mark invoice 'sent' for real invoice emails, not reminders
        if ok and invoice_number and not is_reminder:
            c.execute(
                "UPDATE invoice_records SET sent=1, sent_date=? WHERE invoice_number=?",
                (lux_now().strftime("%Y-%m-%d"), invoice_number)
            )
        conn.commit(); conn.close()
        if ok:
            flash(tr.get("email_sent_ok", "Email poslat."), "ok")
            if invoice_number:
                return redirect(f"/invoices/view?invoice_number={urllib.parse.quote(invoice_number)}")
            return redirect(f"/invoices/client?client={urllib.parse.quote(bulk_client)}")
        flash(tr.get("email_send_failed", "Slanje nije uspjelo") + ": " + (err or "?"), "error")
        return redirect(_back_url())

    # schedule or draft → insert queue row
    status = "scheduled" if action == "schedule" and scheduled_at else "draft"
    sched_db = scheduled_at.replace("T", " ") if scheduled_at else ""
    c.execute("""
        INSERT INTO invoice_email_queue
            (invoice_number, recipient, cc, bcc, subject, body,
             scheduled_at, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (invoice_number, recipient, ",".join(cc_list), ",".join(bcc_list),
          subject, body, sched_db, status, now_str))
    conn.commit(); conn.close()
    if status == "scheduled":
        flash(tr.get("email_scheduled", "Email je zakazan."), "ok")
    else:
        flash(tr.get("email_drafted", "Nacrt je sačuvan."), "ok")
    return redirect(f"/invoices/view?invoice_number={urllib.parse.quote(invoice_number)}")


@app.route("/invoices/email/test", methods=["POST"])
def invoices_email_test():
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    if not (SMTP_HOST and SMTP_FROM):
        flash(tr.get("smtp_not_configured", "SMTP not configured."), "error")
        return redirect("/invoices")
    ok, err, _send_info = _smtp_send(
        SMTP_FROM,
        "Luxmann SMTP test",
        "This is a test email from your Luxmann Planner instance. SMTP works.",
    )
    # Test pings don't need IMAP archiving — we're just checking SMTP works
    # and the user can verify the test message landed in their inbox.
    if ok:
        flash(tr.get("email_test_ok", f"Test email poslat na {SMTP_FROM}"), "ok")
    else:
        flash(tr.get("email_test_fail", "Test SMTP nije uspio") + ": " + (err or "?"), "error")
    return redirect(request.referrer or "/invoices")


@app.route("/tasks/send_scheduled_emails", methods=["GET", "POST"])
def task_send_scheduled_emails():
    """Cron-protected endpoint — sends queued emails whose scheduled_at <= now.
    Call from cron-job.org / Render Cron every 5 minutes:
        /tasks/send_scheduled_emails?secret=<EMAIL_SCHEDULER_SECRET>"""
    secret = request.args.get("secret", "") or request.form.get("secret", "")
    if not EMAIL_SCHEDULER_SECRET or secret != EMAIL_SCHEDULER_SECRET:
        return ("forbidden", 403)

    now_str = lux_now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn(); c = conn.cursor()

    # Recovery sweep: any row stuck in 'sending' for > 30 minutes is
    # considered abandoned (process crash, Render restart, SMTP hang).
    # We revert it to 'scheduled' so the next cron tick retries it.
    stale_cutoff = (lux_now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        "UPDATE invoice_email_queue "
        "SET status='scheduled' "
        "WHERE status='sending' AND claimed_at != '' AND claimed_at < ?",
        (stale_cutoff,)
    )
    conn.commit()
    recovered = getattr(c, "rowcount", 0)

    candidate_rows = c.execute("""
        SELECT id, invoice_number, recipient, cc, bcc, subject, body
        FROM invoice_email_queue
        WHERE status='scheduled' AND scheduled_at != '' AND scheduled_at <= ?
        ORDER BY scheduled_at LIMIT 50
    """, (now_str,)).fetchall()

    # Claim-pattern: atomic UPDATE...WHERE status='scheduled' guarantees that
    # only one cron call grabs each row, even if two crons fire simultaneously
    # (cron-job.org + Render Cron + manual call). claimed_at lets the
    # recovery sweep above detect abandoned rows.
    rows = []
    for row in candidate_rows:
        qid = row[0]
        c.execute(
            "UPDATE invoice_email_queue SET status='sending', claimed_at=? "
            "WHERE id=? AND status='scheduled'",
            (now_str, qid)
        )
        conn.commit()
        if getattr(c, "rowcount", 0) == 1:
            rows.append(row)
        # else: another worker already claimed it — skip silently

    processed = 0; ok_count = 0; fail_count = 0; skipped = len(candidate_rows) - len(rows)
    for row in rows:
        qid, inv_num, rcpt, cc_s, bcc_s, subj, bdy = row
        cc_list  = [x for x in (cc_s  or "").split(",") if x]
        bcc_list = [x for x in (bcc_s or "").split(",") if x]
        # Wrap each row in try/except so an unexpected exception (PDF build
        # crash, DB hiccup) never leaves the row stuck in 'sending'.
        try:
            pdf_bytes, pdf_name = _build_invoice_pdf_for_email(conn, inv_num)
            if not pdf_bytes:
                c.execute("UPDATE invoice_email_queue SET status='failed', error=?, sent_at=? WHERE id=?",
                          ("invoice not found", now_str, qid))
                conn.commit()
                fail_count += 1
                processed += 1
                continue
            ok, err, send_info = _smtp_send(rcpt, subj, bdy, pdf_bytes, pdf_name,
                                            cc=cc_list, bcc=bcc_list)
            msg_id = send_info.get("message_id", "") if send_info else ""
            pdf_sha = hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes else ""
            imap_saved = 0
            imap_error = ""
            # Mirror the interactive send_now path: archive only on success,
            # never let an IMAP hiccup re-fail an otherwise-sent queue row.
            if ok and send_info and send_info.get("raw"):
                iok, ierr = _imap_append_sent(send_info["raw"])
                imap_saved = 1 if iok else 0
                if not iok:
                    imap_error = ierr
                    app.logger.warning(
                        "IMAP APPEND failed for queued %s: %s", inv_num, ierr
                    )
            c.execute("""
                UPDATE invoice_email_queue SET status=?, error=?, sent_at=? WHERE id=?
            """, ("sent" if ok else "failed", err if not ok else "", now_str, qid))
            c.execute("""
                INSERT INTO invoice_email_logs
                    (invoice_number, recipient, subject, status, error, sent_at,
                     message_id, attachment_sha256, imap_saved, imap_error)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (inv_num, rcpt, subj, "sent" if ok else "failed",
                  err if not ok else "", now_str,
                  msg_id, pdf_sha, imap_saved, imap_error))
            if ok:
                c.execute("UPDATE invoice_records SET sent=1, sent_date=? WHERE invoice_number=?",
                          (lux_now().strftime("%Y-%m-%d"), inv_num))
                ok_count += 1
            else:
                fail_count += 1
            conn.commit()
        except Exception as ex:
            # Revert to 'scheduled' so the next cron tick retries the row.
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                c.execute(
                    "UPDATE invoice_email_queue SET status='scheduled', "
                    "error=? WHERE id=?",
                    (f"retry after error: {ex.__class__.__name__}: {str(ex)[:400]}", qid)
                )
                conn.commit()
            except Exception:
                pass
            fail_count += 1
        processed += 1
    conn.commit(); conn.close()
    return {"ok": True, "processed": processed, "sent": ok_count,
            "failed": fail_count, "skipped_claimed": skipped,
            "recovered_stale": recovered}


@app.route("/invoices/download")
def invoices_download():
    if session.get("role") != "admin":
        return redirect("/")
    date_from = request.args.get("date_from", "").strip(); date_to = request.args.get("date_to", "").strip(); invoice_date = request.args.get("invoice_date", lux_now().strftime("%Y-%m-%d")).strip(); client = request.args.get("client", "").strip()
    invoice_number = request.args.get("invoice_number", "").strip()
    conn = get_conn()
    settings = get_invoice_settings(conn)
    row = None
    # invoice_number path: strict. Never fall through to the
    # client/date query params — the whole reason we care about the
    # invoice_number is that it identifies exactly one persisted
    # record. Pre-fix, when get_invoice_row_for_record returned None
    # (client has no shifts in the window) the route dropped into
    # the legacy "look up by client query param" branch and could
    # ship a completely different invoice's PDF.
    if invoice_number:
        rec_row = conn.cursor().execute(
            "SELECT invoice_number, client_name, date_from, date_to, invoice_date, amount, vat_amount, total, paid, paid_date, COALESCE(sent,0), COALESCE(sent_date,''), COALESCE(source,'auto') "
            "FROM invoice_records WHERE invoice_number=? AND COALESCE(deleted,0)=0",
            (invoice_number,)
        ).fetchone()
        if not rec_row:
            conn.close()
            return redirect("/invoices")
        record = invoice_record_to_dict(rec_row)
        row, settings = get_invoice_row_for_record(conn, record)
        invoice_date = record["invoice_date"]
        date_from = record["date_from"]
        date_to = record["date_to"]
        conn.close()
        if not row:
            # Persisted invoice exists but the current plan can't
            # rebuild its row (client has no shifts in the window).
            # Do NOT ship a different invoice — flash and bounce.
            tr = t()
            flash(
                tr.get(
                    "invoice_cannot_rebuild",
                    "Invoice #{n} cannot be rebuilt from the current plan."
                ).replace("{n}", str(invoice_number)),
                "error",
            )
            return redirect("/invoices")
    else:
        # Legacy path: no invoice_number, look up by client query
        # param + date range. Kept only because old bookmarks /
        # emails still linger; new code should always pass
        # invoice_number.
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
    """Bulk ZIP of invoice PDFs (auto + manual) for a period.

    Each record's PDF is built via _build_invoice_pdf_for_email,
    which already picks the right source: manual drafts go through
    build_manual_invoice_pdf, auto invoices through build_invoice_pdf
    reconstructed from the plan. That fixes the old crash where a
    manual invoice was fed to get_invoice_row_for_record and the
    route returned Internal Server Error mid-way through the ZIP.

    One broken invoice does not sink the whole export: failures are
    caught per record, listed in _export_errors.txt inside the ZIP,
    and logged for Render. If every single invoice fails, the user
    gets a readable HTML message instead of a 500.
    """
    if session.get("role") != "admin":
        return redirect("/")
    default_from, default_to = previous_month_range()
    date_from = request.args.get("date_from", default_from).strip()
    date_to = request.args.get("date_to", default_to).strip()
    client = request.args.get("client", "").strip()
    # Default to invoice_date because the export form is "download all
    # invoices in this range" from the admin's perspective — the range
    # they type is the paper date they want on the shelf. work_period
    # is still available for the "give me everything worked in June"
    # request and can be picked in the selector.
    date_basis = request.args.get("date_basis", "invoice_date").strip()
    if date_basis not in ("invoice_date", "work_period"):
        date_basis = "invoice_date"
    conn = get_conn()
    try:
        # Diagnostic: log how many rows each basis would produce so we
        # can see, from Render logs, why an export looked short.
        try:
            _diag_c = conn.cursor()
            n_by_invoice = _diag_c.execute(
                "SELECT COUNT(*) FROM invoice_records "
                "WHERE COALESCE(deleted,0)=0 AND invoice_date >= ? AND invoice_date <= ?"
                + (" AND client_name = ?" if client else ""),
                ((date_from, date_to, client) if client else (date_from, date_to)),
            ).fetchone()[0]
            n_by_work = _diag_c.execute(
                "SELECT COUNT(*) FROM invoice_records "
                "WHERE COALESCE(deleted,0)=0 AND date_from >= ? AND date_from <= ?"
                + (" AND client_name = ?" if client else ""),
                ((date_from, date_to, client) if client else (date_from, date_to)),
            ).fetchone()[0]
            app.logger.info(
                "invoices_download_all diag: basis=%s range=%s..%s client=%r "
                "count_by_invoice_date=%d count_by_work_period=%d",
                date_basis, date_from, date_to, client, n_by_invoice, n_by_work,
            )
        except Exception:
            app.logger.exception("invoices_download_all: diagnostic count failed")

        try:
            records = fetch_invoice_records(conn, date_from, date_to, client or None, "all",
                                            date_basis=date_basis)
        except Exception:
            app.logger.exception("invoices_download_all: failed before ZIP build")
            tr = t(); dark = get_theme() == "dark"
            return render_template_string(
                BASE_STYLE + header_html() +
                "<h1>📦 " + tr.get("invoices", "Fakture") + "</h1>"
                "<a class='back-button' href='/invoices/export_options?type=all'>"
                + tr.get("back", "Nazad") + "</a>"
                "<div style='margin-top:20px;padding:20px;border-radius:12px;"
                "background:{{ '#4a1414' if dark else '#fee2e2' }};"
                "color:{{ '#fecaca' if dark else '#7f1d1d' }};'>"
                "<b>" + _html.escape(tr.get(
                    "invoices_download_none",
                    "Nema faktura za izabrani period ili sve nisu mogle biti generisane.",
                )) + "</b></div>",
                tr=tr, dark=dark,
            )
        exported = []
        errors = []
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for record in records:
                inv_num = record.get("invoice_number") or ""
                client_name = record.get("client") or ""
                try:
                    pdf_bytes, _fname = _build_invoice_pdf_for_email(conn, inv_num)
                    if not pdf_bytes:
                        errors.append((inv_num, client_name,
                                       "PDF source missing (no plan rows or no manual draft)"))
                        continue
                    zf.writestr(
                        f"{safe_pdf_name(inv_num, client_name)}.pdf",
                        pdf_bytes,
                    )
                    exported.append(record)
                except Exception as exc:
                    app.logger.exception(
                        "invoices_download_all: failed to build PDF for %s (%s)",
                        inv_num, client_name,
                    )
                    errors.append((inv_num, client_name, f"{type(exc).__name__}: {exc}"))

            if not exported:
                # No invoices to send back — return a friendly page
                # instead of a ZIP containing only an errors file.
                tr = t(); dark = get_theme() == "dark"
                msg = tr.get("invoices_download_none",
                             "Nema faktura za izabrani period ili sve nisu mogle biti generisane.")
                detail = ""
                if errors:
                    detail = "<ul style='margin-top:12px;text-align:left;'>" + "".join(
                        f"<li><b>{_html.escape(str(n))}</b> — "
                        f"{_html.escape(str(c))}: {_html.escape(str(r))}</li>"
                        for (n, c, r) in errors
                    ) + "</ul>"
                return render_template_string(
                    BASE_STYLE + header_html() +
                    "<h1>📦 " + tr.get("invoices", "Fakture") + "</h1>"
                    "<a class='back-button' href='/invoices/export_options?type=all'>"
                    + tr.get("back", "Nazad") + "</a>"
                    "<div style='margin-top:20px;padding:20px;border-radius:12px;"
                    "background:{{ '#4a3a10' if dark else '#fef3c7' }};"
                    "color:{{ '#fde68a' if dark else '#78350f' }};'>"
                    "<b>" + _html.escape(msg) + "</b>" + detail +
                    "</div>",
                    tr=tr, dark=dark,
                )

            try:
                list_pdf = build_invoice_list_pdf(exported, date_from, date_to)
                zf.writestr(f"liste_factures_{date_from}_{date_to}.pdf", list_pdf.getvalue())
            except Exception as exc:
                app.logger.exception("invoices_download_all: failed to build list PDF")
                errors.append(("liste_factures", "-", f"{type(exc).__name__}: {exc}"))

            if errors:
                report = ["Fakture koje nisu ušle u ZIP:\n"]
                for n, c, r in errors:
                    report.append(f"- {n} | {c} | {r}\n")
                zf.writestr("_export_errors.txt", "".join(report))
    finally:
        conn.close()

    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True,
                     download_name=f"factures_{date_from}_{date_to}.zip",
                     mimetype="application/zip")


@app.route("/invoices/certificate")
def invoices_certificate():
    if session.get("role") != "admin":
        return redirect("/")
    date_from = request.args.get("date_from", "").strip(); date_to = request.args.get("date_to", "").strip(); invoice_date = request.args.get("invoice_date", lux_now().strftime("%Y-%m-%d")).strip(); fixed_amount = request.args.get("fixed_amount", "").strip()
    conn = get_conn(); settings = get_invoice_settings(conn); rows = build_invoice_rows(conn, date_from, date_to, fixed_amount if fixed_amount else None, settings); conn.close()
    pdf = build_invoice_certificate_pdf(rows, invoice_date, date_from, date_to)
    return send_file(pdf, as_attachment=True, download_name=f"certificat_factures_{date_from}_{date_to}.pdf", mimetype="application/pdf")

@app.route("/worker/hours_pdf")
def worker_hours_pdf():
    """Worker-only personal timesheet PDF.

    Guards on session.role == "worker", filters shifts to the
    logged-in user via worker_in_shift(), and ALWAYS reports raw
    duration per shift (never billable hours) so a worker sharing
    a 10:00-13:00 shift with a colleague sees 3.00 h — the number
    they actually worked, not the client's bill line.

    Query params:
      date_from, date_to (YYYY-MM-DD). If either is missing or
      invalid we fall back to "1st of current month → today" so a
      malformed URL still produces a sensible PDF.

    A worker cannot ever request another worker's PDF — there is
    no ?worker= query param on this route, and the SQL filter
    only keeps rows where worker_in_shift(current_user, ...) is
    true.
    """
    if "user" not in session:
        return redirect("/login")
    if session.get("role") != "worker":
        return redirect("/")
    tr = t()
    current_user = session.get("user")
    def _iso(v):
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except (TypeError, ValueError):
            return ""
    date_from = _iso((request.args.get("date_from") or "").strip())
    date_to   = _iso((request.args.get("date_to")   or "").strip())
    if not date_from:
        today = lux_now()
        date_from = today.replace(day=1).strftime("%Y-%m-%d")
    if not date_to:
        date_to = lux_now().strftime("%Y-%m-%d")
    if date_to < date_from:
        date_to = date_from
    conn = get_conn(); c = conn.cursor()
    all_shifts = c.execute(
        "SELECT * FROM shifts WHERE date >= ? AND date <= ? ORDER BY date, time, id",
        (date_from, date_to)
    ).fetchall()
    conn.close()
    shifts = [s for s in all_shifts if worker_in_shift(current_user, s[1])]

    title = f"{tr.get('worker_hours_pdf','Moji sati')} — {current_user}  ·  " \
            f"{format_date(date_from)} – {format_date(date_to)}"
    buffer = io.BytesIO()
    doc = pdf_doc(buffer, title, pagesize=A4,
                  rightMargin=1.5*cm, leftMargin=1.5*cm,
                  topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    elements = []
    if os.path.exists("static/logo.png"):
        elements += [Image("static/logo.png", width=4*cm, height=2*cm), Spacer(1, 8)]
    elements += [Paragraph(title, styles["Title"]), Spacer(1, 10)]

    # Raw duration only — the worker's own timesheet is never the
    # billable line (2 workers × 3 h = 6 h).
    total_hours = sum(parse_shift_hours(s[4]) for s in shifts)
    elements.append(Paragraph(
        f"{tr.get('worker_hours','Sati radnika')}: {total_hours:.2f}",
        styles["Normal"],
    ))
    elements.append(Spacer(1, 8))

    table_data = [[tr["pdf_date"], tr["pdf_time"], tr["pdf_client"],
                   tr.get("worker_hours", "Sati radnika"), tr["status"]]]
    for s in shifts:
        table_data.append([
            format_date(s[3]), s[4], s[2],
            f"{parse_shift_hours(s[4]):.2f}",
            get_status_label(get_auto_status(s[3], s[4]), tr),
        ])
    if not shifts:
        table_data.append(["-", "-", "-", "-", tr["pdf_no_shifts"]])
    table = Table(table_data, colWidths=[2.6*cm, 2.6*cm, 7*cm, 2.6*cm, 3.4*cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4f82")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0,1), (-1,-1),
         [colors.whitesmoke, colors.HexColor("#eaf2fb")]),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
    ]))
    elements.append(table)
    doc.build(elements); buffer.seek(0)
    filename = safe_pdf_name("moji_sati", current_user, date_from, date_to)
    return send_file(buffer, as_attachment=True,
                     download_name=f"{filename}.pdf",
                     mimetype="application/pdf")


@app.route("/clients/pdf")
def clients_pdf():
    """Admin-only printable client worksheet.

    One row per client, sorted alphabetically. The rightmost
    column is intentionally empty ruled lines so the admin can
    walk the list with a pen (phone calls, site notes, etc.).
    City is extracted from the stored address via
    client_city_from_address(); if we can't parse a postal-code
    town reliably we leave the cell blank rather than dumping
    the raw street back into the "city" column.
    """
    if session.get("role") != "admin":
        return redirect("/")
    tr = t()
    conn = get_conn(); c = conn.cursor()
    clients = c.execute(
        "SELECT name, COALESCE(address,'') FROM clients ORDER BY LOWER(name)"
    ).fetchall()
    conn.close()

    title = tr.get("clients_pdf_title", "Lista klijenata")
    buffer = io.BytesIO()
    doc = pdf_doc(buffer, title, pagesize=A4,
                  rightMargin=1.2*cm, leftMargin=1.2*cm,
                  topMargin=1.2*cm, bottomMargin=1.2*cm)
    styles = getSampleStyleSheet()
    elements = []
    if os.path.exists("static/logo.png"):
        elements += [Image("static/logo.png", width=4*cm, height=2*cm), Spacer(1, 6)]
    elements += [
        Paragraph(title, styles["Title"]),
        Paragraph(format_date(lux_now().strftime("%Y-%m-%d")), styles["Normal"]),
        Spacer(1, 10),
    ]

    # Empty ruled lines inside the Notes cell — three underscores
    # give the admin enough writing space (~1.6cm row height).
    notes_lines = "________________________________________<br/>" \
                  "________________________________________<br/>" \
                  "________________________________________"
    cell_style = ParagraphStyle(
        "clientsPdfCell", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10, leading=13,
    )
    header_style = ParagraphStyle(
        "clientsPdfHead", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=10, leading=12,
        textColor=colors.white,
    )
    notes_style = ParagraphStyle(
        "clientsPdfNotes", parent=styles["Normal"],
        fontName="Helvetica", fontSize=9, leading=14,
        textColor=colors.HexColor("#94a3b8"),
    )

    table_data = [[
        Paragraph(tr.get("client_name", "Klijent"), header_style),
        Paragraph(tr.get("city_or_place", "Mjesto"), header_style),
        Paragraph(tr.get("notes", "Zabiljeske"), header_style),
    ]]
    for name, address in clients:
        city = client_city_from_address(address)
        table_data.append([
            Paragraph(_html.escape(name or ""), cell_style),
            Paragraph(_html.escape(city or ""), cell_style),
            Paragraph(notes_lines, notes_style),
        ])
    if len(table_data) == 1:
        table_data.append([Paragraph("-", cell_style),
                           Paragraph("-", cell_style),
                           Paragraph("-", cell_style)])

    table = Table(
        table_data,
        colWidths=[6*cm, 4*cm, 8.6*cm],
        rowHeights=[0.9*cm] + [1.7*cm]*(len(table_data) - 1),
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0), colors.HexColor("#1f4f82")),
        ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("ALIGN",       (0,0), (-1,0), "LEFT"),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
        ("GRID",        (0,0), (-1,-1), 0.5, colors.grey),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING",(0,0), (-1,-1), 6),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 5),
    ]))
    elements.append(table)
    doc.build(elements); buffer.seek(0)
    filename = safe_pdf_name("lista_klijenata", lux_now().strftime("%Y-%m-%d"))
    return send_file(buffer, as_attachment=False,
                     download_name=f"{filename}.pdf",
                     mimetype="application/pdf")


@app.route("/shifts_search_pdf")
def shifts_search_pdf():
    if "user" not in session: return redirect("/login")
    tr = t(); is_admin = session.get("role") == "admin"; current_user = session.get("user")
    search_date_from = request.args.get("search_date_from", "").strip()
    search_date_to   = request.args.get("search_date_to",   "").strip()
    worker_filter    = request.args.get("worker", "").strip()
    client_filter    = request.args.get("client", "").strip()
    search_query     = request.args.get("q", "").strip().lower()
    def _parse_ymd(v):
        try: return datetime.strptime(v, "%Y-%m-%d")
        except Exception: return None
    dt_from = _parse_ymd(search_date_from)
    dt_to   = _parse_ymd(search_date_to)
    if search_date_from and not dt_from: search_date_from = ""
    if search_date_to   and not dt_to:   search_date_to   = ""
    if not search_date_from and not search_date_to and not worker_filter and not client_filter and not search_query:
        return redirect("/")
    if not dt_from and not dt_to:
        search_date_to   = lux_now().strftime("%Y-%m-%d")
        search_date_from = (lux_now() - timedelta(days=90)).strftime("%Y-%m-%d")
    elif dt_from and not dt_to:
        search_date_to = (dt_from + timedelta(days=90)).strftime("%Y-%m-%d")
    elif dt_to and not dt_from:
        search_date_from = (dt_to - timedelta(days=90)).strftime("%Y-%m-%d")
    conn = get_conn(); c = conn.cursor()
    base = "SELECT * FROM shifts WHERE 1=1"; params = []
    if search_date_from: base += " AND date >= ?"; params.append(search_date_from)
    if search_date_to:   base += " AND date <= ?"; params.append(search_date_to)
    if client_filter:    base += " AND client = ?"; params.append(client_filter)
    base += " ORDER BY date, time, id"
    all_shifts = c.execute(base, tuple(params)).fetchall()
    if not is_admin: all_shifts = [s for s in all_shifts if worker_in_shift(current_user, s[1])]
    shifts = []
    for s in all_shifts:
        if is_admin and worker_filter and not worker_in_shift(worker_filter, s[1]): continue
        if search_query and search_query not in f"{s[1]} {s[2]} {s[3]} {s[4]} {s[5]}".lower(): continue
        shifts.append(s)
    worker_colors = get_worker_colors(conn); conn.close()
    parts = []
    if worker_filter: parts.append(worker_filter)
    if client_filter: parts.append(client_filter)
    if search_date_from or search_date_to:
        parts.append(f"{format_date(search_date_from) if search_date_from else '...'} – {format_date(search_date_to) if search_date_to else '...'}")
    title = tr["search_shifts"] + (f" — {', '.join(parts)}" if parts else "")
    buffer = io.BytesIO()
    doc = pdf_doc(buffer, title, pagesize=landscape(A4), rightMargin=1*cm, leftMargin=1*cm, topMargin=1*cm, bottomMargin=1*cm)
    styles = getSampleStyleSheet(); elements = []
    if os.path.exists("static/logo.png"): elements += [Image("static/logo.png", width=4*cm, height=2*cm), Spacer(1, 8)]
    elements += [Paragraph(title, styles["Title"]), Spacer(1, 10)]
    # Row/total hours depend on scope. When the PDF is filtered to
    # ONE worker (admin picked ?worker=... or a non-admin worker is
    # viewing their own timesheet), rows show duration only — the
    # "how much did this person work" number. Otherwise the PDF is
    # showing the whole team on a shift, so rows use billable hours
    # (duration × worker count) to match invoice generation.
    single_worker_scope = bool(worker_filter) or not is_admin
    row_hours = lambda sh: shift_search_pdf_hours(sh, single_worker_scope)
    hours_label = (
        tr.get("worker_hours", "Sati radnika")
        if single_worker_scope
        else tr.get("billable_hours", "Sati (naplativi)")
    )
    total_hours = sum(row_hours(s) for s in shifts)
    elements.append(Paragraph(f"{hours_label}: {total_hours:.2f}", styles["Normal"])); elements.append(Spacer(1, 8))
    table_data = [[tr["pdf_date"], tr["pdf_time"], tr["pdf_worker"], tr["pdf_client"], hours_label, tr["status"]]]
    for s in shifts:
        table_data.append([format_date(s[3]), s[4], s[1], s[2], f"{row_hours(s):.2f}", get_status_label(get_auto_status(s[3], s[4]), tr)])
    if not shifts: table_data.append(["-", "-", "-", "-", "-", tr["pdf_no_shifts"]])
    table = Table(table_data, colWidths=[2.6*cm, 2.6*cm, 5*cm, 6*cm, 2.4*cm, 3.4*cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4f82")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.HexColor("#eaf2fb")]),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
    ]))
    elements.append(table); doc.build(elements); buffer.seek(0)
    filename = safe_pdf_name("smjene", worker_filter or "svi", search_date_from or "", search_date_to or "")
    return send_file(buffer, as_attachment=True, download_name=f"{filename}.pdf", mimetype="application/pdf")


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

@app.route("/delete_worker/<path:name>", methods=["POST"])
def delete_worker(name):
    if session.get("role") != "admin" or name == "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor(); c.execute("DELETE FROM workers WHERE name = ?", (name,)); c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (name,)); conn.commit(); conn.close(); return redirect("/workers")

@app.route("/delete_client/<path:name>", methods=["POST"])
def delete_client(name):
    if session.get("role") != "admin":
        return redirect("/")
    conn = get_conn(); c = conn.cursor()
    c.execute("DELETE FROM clients WHERE name = ?", (name,))
    # Also remove the matching invoice profile so a later re-add of the
    # same client name doesn't inherit a stale email / hourly_rate /
    # custom_address from the previous incarnation. Without this an
    # admin who deletes "ACME" and re-adds "ACME" two months later
    # would see ACME's old email auto-populated in the email composer.
    c.execute("DELETE FROM client_invoice_profiles WHERE client_name = ?", (name,))
    conn.commit(); conn.close()
    return redirect("/clients")

@app.route("/delete_shift/<int:id>", methods=["POST"])
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
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    conn = get_conn(); c = conn.cursor()
    if request.method == "POST":
        f = request.form
        new_name = f.get("name", "").strip()
        address  = f.get("address", "").strip()
        phone    = f.get("phone", "").strip()
        email    = f.get("email", "").strip()
        csigned  = f.get("contract_signed_at", "").strip()
        cfrom    = f.get("contract_from", "").strip()
        cto      = f.get("contract_to", "").strip()
        notes    = f.get("notes", "").strip()
        if new_name:
            c.execute(
                "UPDATE clients SET name=?, address=?, phone=?, email=?, "
                "contract_signed_at=?, contract_from=?, contract_to=?, notes=? "
                "WHERE name=?",
                (new_name, address, phone, email, csigned, cfrom, cto, notes, name),
            )
            # Cascade rename through any related rows so the new display
            # name is the single source of truth across shifts and the
            # invoice profile.
            if new_name != name:
                c.execute("UPDATE shifts SET client=? WHERE client=?", (new_name, name))
                c.execute(
                    "UPDATE client_invoice_profiles SET client_name=? WHERE client_name=?",
                    (new_name, name),
                )
            # Write-through email to invoice profile so the email-sending
            # path keeps working unchanged.
            c.execute(
                "INSERT INTO client_invoice_profiles (client_name, email) "
                "VALUES (?, ?) ON CONFLICT(client_name) DO UPDATE SET "
                "email = excluded.email",
                (new_name, email),
            )
        conn.commit(); conn.close()
        return redirect("/clients/view/" + urllib.parse.quote(new_name or name))
    row = c.execute(
        "SELECT name, address, COALESCE(phone,''), COALESCE(email,''), "
        "COALESCE(contract_signed_at,''), COALESCE(contract_from,''), "
        "COALESCE(contract_to,''), COALESCE(notes,'') "
        "FROM clients WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    if not row:
        return redirect("/clients")
    client = {
        "name": row[0], "address": row[1], "phone": row[2], "email": row[3],
        "contract_signed_at": row[4], "contract_from": row[5],
        "contract_to": row[6], "notes": row[7],
    }
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
      .cf-card { max-width:640px; margin:24px auto; background:{{ '#161618' if dark else 'white' }};
                 color:{{ '#e2e8f0' if dark else '#1e293b' }};
                 border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
                 border-radius:14px; padding:22px;
                 box-shadow:0 4px 14px rgba(0,0,0,.08); }
      .cf-row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
      .cf-label { display:block; font-size:12px; font-weight:700;
                  color:{{ '#94a3b8' if dark else '#64748b' }}; margin:14px 0 4px; }
      .cf-input, .cf-textarea {
        width:100%; padding:10px 12px; border-radius:8px;
        border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};
        background:{{ '#0f0f10' if dark else '#fff' }};
        color:{{ '#e2e8f0' if dark else '#0f172a' }};
        font-size:14px; box-sizing:border-box; font-family:inherit; }
      .cf-textarea { min-height:80px; resize:vertical; }
      .cf-actions { display:flex; gap:10px; margin-top:18px; flex-wrap:wrap; }
      .cf-btn { flex:1; min-width:140px; padding:11px; border-radius:10px;
                border:none; cursor:pointer; font-weight:700; font-size:14px;
                font-family:inherit; }
      .cf-btn.primary { background:#16a34a; color:white; }
      .cf-btn.cancel  { background:#6b7280; color:white; text-decoration:none;
                        text-align:center; line-height:24px; }
    </style>
    <div class="cf-card">
      <h2>🏢 {{ tr.get("clients","Klijenti") }} — {{ tr.get("edit","Uredi") }}</h2>
      <form method="post">
        <label class="cf-label">{{ tr.get("client_name","Naziv klijenta") }} *</label>
        <input class="cf-input" name="name" value="{{ client.name }}" required>

        <label class="cf-label">{{ tr.get("address","Adresa") }} *</label>
        <textarea class="cf-textarea" name="address" required>{{ client.address }}</textarea>

        <div class="cf-row">
          <div>
            <label class="cf-label">📞 {{ tr.get("phone","Telefon") }}</label>
            <input class="cf-input" name="phone" value="{{ client.phone }}">
          </div>
          <div>
            <label class="cf-label">✉ Email</label>
            <input class="cf-input" type="email" name="email" value="{{ client.email }}">
          </div>
        </div>

        <div class="cf-row">
          <div>
            <label class="cf-label">📅 {{ tr.get("contract_signed","Ugovor potpisan") }}</label>
            <input class="cf-input" type="date" name="contract_signed_at" value="{{ client.contract_signed_at }}">
          </div>
          <div></div>
        </div>

        <div class="cf-row">
          <div>
            <label class="cf-label">{{ tr.get("contract_from","Ugovor od") }}</label>
            <input class="cf-input" type="date" name="contract_from" value="{{ client.contract_from }}">
          </div>
          <div>
            <label class="cf-label">{{ tr.get("contract_to","Ugovor do") }}</label>
            <input class="cf-input" type="date" name="contract_to" value="{{ client.contract_to }}">
          </div>
        </div>

        <label class="cf-label">📝 {{ tr.get("notes","Napomena") }}</label>
        <textarea class="cf-textarea" name="notes">{{ client.notes }}</textarea>

        <div class="cf-actions">
          <button type="submit" class="cf-btn primary">💾 {{ tr.get("save","Sacuvaj") }}</button>
          <a class="cf-btn cancel" href="/clients">{{ tr.get("back","Nazad") }}</a>
        </div>
      </form>
    </div>
    """, tr=tr, dark=dark, client=client)

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
    return render_template_string(BASE_STYLE + """<div class="card" style="max-width:520px;margin:auto;"><h2>{{ tr["edit_shift"] }}</h2><form method="post"><input type="hidden" name="return_to" value="{{ return_to }}"><label>{{ tr["choose_worker"] }}</label>{% for w in workers %}{% if w[0] != 'admin' %}<label class="check-row"><input type="checkbox" name="workers" value="{{ w[0] }}" {% if w[0] in selected_workers %}checked{% endif %}>{{ w[0] }}</label>{% endif %}{% endfor %}<div class="client-search-wrapper"><input type="text" id="csInputEdit" class="client-search-input" value="{{ shift[2] }}" placeholder="{{ tr['search_placeholder'] }}" autocomplete="off"><input type="hidden" name="client" id="csHiddenEdit" value="{{ shift[2] }}" required><div class="client-search-dropdown" id="csListEdit"></div></div><label for="editShiftDate">{{ tr["date"] }}</label><input id="editShiftDate" type="date" name="date" value="{{ shift[3] }}" required><label>{{ tr["start_time"] }}</label><div style="display:flex;gap:6px;"><select name="start_hour">{% for h in time_hours %}<option value="{{ h }}" {% if h == sh %}selected{% endif %}>{{ h }}</option>{% endfor %}</select><select name="start_minute">{% for m in time_minutes %}<option value="{{ m }}" {% if m == sm %}selected{% endif %}>{{ m }}</option>{% endfor %}</select></div><label>{{ tr["end_time"] }}</label><div style="display:flex;gap:6px;"><select name="end_hour">{% for h in time_hours %}<option value="{{ h }}" {% if h == eh %}selected{% endif %}>{{ h }}</option>{% endfor %}</select><select name="end_minute">{% for m in time_minutes %}<option value="{{ m }}" {% if m == em %}selected{% endif %}>{{ m }}</option>{% endfor %}</select></div><select name="status"><option value="planned" {% if shift[5] == 'planned' %}selected{% endif %}>{{ tr["status_planned"] }}</option><option value="in_progress" {% if shift[5] == 'in_progress' %}selected{% endif %}>{{ tr["status_in_progress"] }}</option><option value="done" {% if shift[5] == 'done' %}selected{% endif %}>{{ tr["status_done"] }}</option></select><button>{{ tr["save"] }}</button></form><br><a class="back-button" href="/">{{ tr["back"] }}</a></div><script>document.addEventListener('DOMContentLoaded',function(){var CD=[{% for c in clients %}{"name":{{c[0]|tojson}},"addr":{{(c[1] or '')|tojson}}}{% if not loop.last %},{% endif %}{% endfor %}];initClientSearch('csInputEdit','csHiddenEdit','csListEdit',CD);});</script>""", tr=tr, dark=dark, shift=shift, workers=workers, clients=clients, selected_workers=selected_workers, sh=sh, sm=sm, eh=eh, em=em, time_hours=time_hours(), time_minutes=time_minutes(), return_to=return_to)

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
                <form class="inline-delete-form" method="post" action="/delete_worker/{{ w[0]|urlencode }}"
                      onsubmit='return confirm({{ (tr.get("delete_worker_confirm","Obrisati radnika") ~ " " ~ w[0] ~ "?")|tojson }})'>
                  <button type="submit" style="color:#dc2626;border:1px solid #fecaca;background:#fff1f2;cursor:pointer;font-family:inherit;padding:5px 8px;border-radius:6px;font-weight:bold;font-size:12px;">{{ tr["delete"] }}</button>
                </form>
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
    if session.get("role") != "admin":
        return redirect("/")
    f = request.form
    name    = f.get("client_name", "").strip()
    address = f.get("address", "").strip()
    phone   = f.get("phone", "").strip()
    email   = f.get("email", "").strip()
    csigned = f.get("contract_signed_at", "").strip()
    cfrom   = f.get("contract_from", "").strip()
    cto     = f.get("contract_to", "").strip()
    notes   = f.get("notes", "").strip()
    if name and address:
        conn = get_conn(); c = conn.cursor()
        c.execute(
            "INSERT OR IGNORE INTO clients "
            "(name, address, phone, email, contract_signed_at, "
            " contract_from, contract_to, notes) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (name, address, phone, email, csigned, cfrom, cto, notes),
        )
        # Write-through: keep client_invoice_profiles.email in sync so
        # the email-sending pipeline (which reads from there) picks up
        # the new address. Always upsert — even when email is empty —
        # so a re-add of a previously-deleted client doesn't inherit
        # a stale profile email.
        c.execute(
            "INSERT INTO client_invoice_profiles (client_name, email) "
            "VALUES (?, ?) ON CONFLICT(client_name) DO UPDATE SET "
            "email = excluded.email",
            (name, email),
        )
        conn.commit(); conn.close()
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
        "theme_color": "#1f4f82",
        "icons": [
            {"src": "/static/icon-192.png?v=3", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icon-512.png?v=3", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
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
        <form method="post" action="/change_password" autocomplete="off">
          <label class="sr-only" for="cp_new_password">{{ tr['new_password'] }}</label>
          <input id="cp_new_password" name="new_password" type="password"
                 autocomplete="new-password"
                 placeholder="{{ tr['new_password'] }}" required>
          <button>{{ tr["save"] }}</button>
        </form>
      </div>

      <div class="card">
        <h3>➕ {{ tr["add_user"] }}</h3>
        <form method="post" action="/add_user" autocomplete="off">
          <label class="sr-only" for="au_username">{{ tr['username'] }}</label>
          <input id="au_username" name="username" type="text"
                 autocomplete="off" autocapitalize="none"
                 autocorrect="off" spellcheck="false"
                 placeholder="{{ tr['username'] }}" required>
          <label class="sr-only" for="au_password">{{ tr['password'] }}</label>
          <input id="au_password" name="password" type="password"
                 autocomplete="new-password"
                 placeholder="{{ tr['password'] }}" required>
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
          <form class="inline-delete-form" method="post" action="/delete_user/{{ u[0] }}"
                onsubmit='return confirm({{ tr.get("user_delete_confirm","Delete this user?")|tojson }})'>
            <button type="submit" class="delete-link" style="width:auto;padding:4px 10px;font-size:12px;">{{ tr["delete"] }}</button>
          </form>
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


@app.route("/clients/view/<path:name>")
def client_detail(name):
    """Read-only client detail view with copy-to-clipboard helpers.

    Several clients have no email — the admin sends invoices by post.
    The copy buttons next to each field make it one click to paste
    the address into a printable envelope label.
    """
    if session.get("role") != "admin":
        return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    conn = get_conn(); c = conn.cursor()
    row = c.execute(
        "SELECT name, COALESCE(address,''), COALESCE(phone,''), "
        "COALESCE(email,''), COALESCE(contract_signed_at,''), "
        "COALESCE(contract_from,''), COALESCE(contract_to,''), "
        "COALESCE(notes,'') "
        "FROM clients WHERE name=?", (name,)
    ).fetchone()
    # Pull the invoice profile email as a fallback — older clients only
    # have it there, not on the new clients.email column.
    prof_email = ""
    if row:
        p = c.execute(
            "SELECT COALESCE(email,'') FROM client_invoice_profiles "
            "WHERE client_name=?", (row[0],)
        ).fetchone()
        if p:
            prof_email = p[0] or ""
    conn.close()
    if not row:
        flash(tr.get("client_not_found", "Klijent nije pronadjen."), "error")
        return redirect("/clients")
    client = {
        "name": row[0], "address": row[1], "phone": row[2],
        "email": row[3] or prof_email,
        "contract_signed_at": row[4], "contract_from": row[5],
        "contract_to": row[6], "notes": row[7],
    }
    # Compact mailing-label block — joined for the "copy whole label"
    # button so the admin can paste straight into the envelope template.
    mailing_parts = [client["name"]]
    if client["address"]:
        mailing_parts.append(client["address"])
    mailing_label = "\n".join(mailing_parts)
    return render_template_string(BASE_STYLE + header_html() + """
    <style>
      .cd-shell { max-width:720px; margin:24px auto; padding:0 16px; }
      .cd-card  { background:{{ '#161618' if dark else '#ffffff' }};
                  border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
                  border-radius:14px; padding:22px;
                  color:{{ '#e2e8f0' if dark else '#1e293b' }};
                  box-shadow:0 4px 14px rgba(0,0,0,.08); }
      /* 3-col grid: fixed label width + value (min-width:0 so long
         text wraps instead of pushing siblings to zero width) + auto
         copy button. Switched off flex because the global BASE_STYLE
         button { width:100% } rule was stretching .cd-copy to fill
         the row and squeezing the value down to one character per
         line (vertical text bug). */
      .cd-row { display:grid; grid-template-columns:140px minmax(0,1fr) auto;
                align-items:center; gap:10px;
                padding:10px 12px; border-radius:10px;
                background:{{ '#0f0f10' if dark else '#f8fafc' }};
                border:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};
                margin-top:10px; }
      .cd-row.multiline { align-items:start; }
      .cd-row .cd-label { font-size:11px; font-weight:700;
                          color:{{ '#94a3b8' if dark else '#64748b' }};
                          text-transform:uppercase; letter-spacing:.04em; }
      .cd-row .cd-value { min-width:0; font-size:14px;
                          overflow-wrap:break-word; word-break:normal;
                          white-space:pre-wrap; }
      .cd-row .cd-value.empty { color:{{ '#475569' if dark else '#94a3b8' }};
                                font-style:italic; }
      .cd-copy { width:auto; min-width:40px; flex:0 0 auto;
                 background:transparent;
                 border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }};
                 color:{{ '#e2e8f0' if dark else '#1e293b' }};
                 border-radius:8px; padding:6px 10px; cursor:pointer;
                 font-size:12px; font-family:inherit;
                 white-space:nowrap; }
      .cd-copy:hover { background:{{ '#2c2c30' if dark else '#e0e7ff' }}; }
      /* Narrow viewports: 140px uppercase label is too greedy alongside a
         long address/email. Stack label above value, keep copy button
         pinned to the right edge of the value row. */
      @media (max-width:520px) {
        .cd-card { padding:16px; }
        .cd-row  { grid-template-columns:minmax(0,1fr) auto; }
        .cd-row .cd-label { grid-column:1 / -1; margin-bottom:2px; }
        .cd-row .cd-value { grid-column:1; }
        .cd-copy          { grid-column:2; grid-row:2; align-self:start; }
      }
      .cd-copy.copied { background:#16a34a; color:white; border-color:#16a34a; }
      .cd-actions { display:flex; gap:10px; margin-top:18px; flex-wrap:wrap; }
      .cd-btn { padding:11px 16px; border-radius:10px; border:none;
                cursor:pointer; font-weight:700; font-size:14px;
                font-family:inherit; text-decoration:none; line-height:24px; }
      .cd-btn.edit { background:#2563eb; color:white; }
      .cd-btn.back { background:#6b7280; color:white; }
      .cd-btn.label { background:#16a34a; color:white; }
      .cd-section-title { margin:18px 0 4px; font-size:13px; font-weight:700;
                          color:{{ '#94a3b8' if dark else '#64748b' }};
                          text-transform:uppercase; letter-spacing:.04em; }
    </style>
    <div class="cd-shell">
      <div class="cd-card">
        <h2 style="margin:0 0 6px;">🏢 {{ client.name }}</h2>
        <div style="font-size:13px;color:{{ '#94a3b8' if dark else '#64748b' }};margin-bottom:10px;">
          {{ tr.get("client_details","Detalji klijenta") }}
        </div>

        <div class="cd-section-title">{{ tr.get("contact","Kontakt") }}</div>
        <div class="cd-row multiline">
          <div class="cd-label">{{ tr.get("address","Adresa") }}</div>
          <div class="cd-value {% if not client.address %}empty{% endif %}"
               id="cdF_address">{{ client.address or "—" }}</div>
          {% if client.address %}
          <button type="button" class="cd-copy" data-copy-from="cdF_address">📋</button>
          {% endif %}
        </div>
        <div class="cd-row">
          <div class="cd-label">📞 {{ tr.get("phone","Telefon") }}</div>
          <div class="cd-value {% if not client.phone %}empty{% endif %}"
               id="cdF_phone">{{ client.phone or "—" }}</div>
          {% if client.phone %}
          <button type="button" class="cd-copy" data-copy-from="cdF_phone">📋</button>
          {% endif %}
        </div>
        <div class="cd-row">
          <div class="cd-label">✉ Email</div>
          <div class="cd-value {% if not client.email %}empty{% endif %}"
               id="cdF_email">{{ client.email or "—" }}</div>
          {% if client.email %}
          <button type="button" class="cd-copy" data-copy-from="cdF_email">📋</button>
          {% endif %}
        </div>

        <div class="cd-section-title">{{ tr.get("contract","Ugovor") }}</div>
        <div class="cd-row">
          <div class="cd-label">📅 {{ tr.get("contract_signed","Potpisan") }}</div>
          <div class="cd-value {% if not client.contract_signed_at %}empty{% endif %}">
            {{ client.contract_signed_at or "—" }}
          </div>
        </div>
        <div class="cd-row">
          <div class="cd-label">{{ tr.get("contract_from","Od") }}</div>
          <div class="cd-value {% if not client.contract_from %}empty{% endif %}">
            {{ client.contract_from or "—" }}
          </div>
        </div>
        <div class="cd-row">
          <div class="cd-label">{{ tr.get("contract_to","Do") }}</div>
          <div class="cd-value {% if not client.contract_to %}empty{% endif %}">
            {{ client.contract_to or "—" }}
          </div>
        </div>

        {% if client.notes %}
        <div class="cd-section-title">📝 {{ tr.get("notes","Napomena") }}</div>
        <div class="cd-row multiline">
          <div class="cd-label">📝</div>
          <div class="cd-value">{{ client.notes }}</div>
          <button type="button" class="cd-copy" data-copy-text="{{ client.notes }}">📋</button>
        </div>
        {% endif %}

        <div class="cd-actions">
          <button type="button" class="cd-btn label"
                  data-copy-text="{{ mailing_label }}">
            📨 {{ tr.get("copy_mailing_label","Kopiraj naljepnicu za kovertu") }}
          </button>
          <a class="cd-btn edit" href="/edit_client/{{ client.name|urlencode }}">
            ✏️ {{ tr.get("edit","Uredi") }}
          </a>
          <a class="cd-btn back" href="/clients">{{ tr.get("back","Nazad") }}</a>
        </div>
      </div>
    </div>
    <script>
      function cdFlash(btn){
        var prev = btn.textContent;
        btn.classList.add('copied');
        btn.textContent = '✓';
        setTimeout(function(){ btn.classList.remove('copied'); btn.textContent = prev; }, 1200);
      }
      function cdLegacyCopy(text, btn){
        // Fallback for non-HTTPS / older browsers / Clipboard API
        // rejections (some Safari versions reject writeText when the
        // page isn't focused or the gesture chain is broken).
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position='fixed'; ta.style.opacity='0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); cdFlash(btn); } catch(e){}
        document.body.removeChild(ta);
      }
      function cdCopy(text, btn){
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(
            function(){ cdFlash(btn); },
            function(){ cdLegacyCopy(text, btn); }
          );
          return;
        }
        cdLegacyCopy(text, btn);
      }
      document.querySelectorAll('.cd-copy, .cd-btn.label').forEach(function(btn){
        btn.addEventListener('click', function(){
          var t = btn.dataset.copyText;
          if (!t) {
            var src = document.getElementById(btn.dataset.copyFrom);
            if (src) t = src.textContent.trim();
          }
          if (t) cdCopy(t, btn);
        });
      });
    </script>
    """, tr=tr, dark=dark, client=client, mailing_label=mailing_label)


@app.route("/clients")
def clients_page():
    if session.get("role") != "admin": return redirect("/")
    tr = t(); dark = get_theme() == "dark"
    conn = get_conn(); c = conn.cursor()
    clients = c.execute(
        "SELECT name, address, COALESCE(phone,''), COALESCE(email,''), "
        "COALESCE(contract_signed_at,''), COALESCE(contract_from,''), "
        "COALESCE(contract_to,'') "
        "FROM clients ORDER BY name"
    ).fetchall()
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
    <a class="back-button" href="/clients/pdf" target="_blank" rel="noopener"
       style="margin-left:8px;">📄 {{ tr.get("clients_pdf","PDF lista klijenata") }}</a>

    <div class="add-client-card" style="margin-top:16px;">
      <h3 style="margin:0 0 12px;">+ {{ tr["add_client"] }}</h3>
      <form method="post" action="/add_client" style="display:flex;flex-direction:column;gap:8px;">
        <input name="client_name" placeholder="{{ tr['client_name'] }}" required>
        <input name="address" placeholder="{{ tr['address'] }}" required>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
          <input name="phone" placeholder="📞 {{ tr.get('phone','Telefon') }}">
          <input name="email" type="email" placeholder="✉ Email">
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;">
          <div style="display:flex;flex-direction:column;gap:4px;">
            <label for="clientContractSigned" style="font-size:11px;font-weight:700;color:{{ '#94a3b8' if dark else '#64748b' }};">📅 {{ tr.get('contract_signed','Ugovor potpisan') }}</label>
            <input id="clientContractSigned" name="contract_signed_at" type="date">
          </div>
          <div style="display:flex;flex-direction:column;gap:4px;">
            <label for="clientContractFrom" style="font-size:11px;font-weight:700;color:{{ '#94a3b8' if dark else '#64748b' }};">{{ tr.get('contract_from','Ugovor od') }}</label>
            <input id="clientContractFrom" name="contract_from" type="date">
          </div>
          <div style="display:flex;flex-direction:column;gap:4px;">
            <label for="clientContractTo" style="font-size:11px;font-weight:700;color:{{ '#94a3b8' if dark else '#64748b' }};">{{ tr.get('contract_to','Ugovor do') }}</label>
            <input id="clientContractTo" name="contract_to" type="date">
          </div>
        </div>
        <button style="width:auto;align-self:flex-start;">{{ tr["add_client"] }}</button>
      </form>
    </div>

    <div class="clients-grid">
      {% for cl in clients %}
      <div class="client-card">
        <a class="client-card-name" href="/clients/view/{{ cl[0]|urlencode }}"
           style="text-decoration:none;color:inherit;">🏢 {{ cl[0] }}</a>
        {% if cl[1] %}<div class="client-card-addr">📍 {{ cl[1] }}</div>{% endif %}
        {% if cl[2] %}<div class="client-card-addr">📞 {{ cl[2] }}</div>{% endif %}
        {% if cl[3] %}<div class="client-card-addr">✉ {{ cl[3] }}</div>{% endif %}
        {% if cl[5] and cl[6] %}<div class="client-card-addr">📅 {{ cl[5] }} → {{ cl[6] }}</div>{% endif %}
        <div class="client-card-actions">
          <a href="/clients/view/{{ cl[0]|urlencode }}">{{ tr.get("details","Detalji") }}</a>
          <a href="/edit_client/{{ cl[0]|urlencode }}">{{ tr["edit"] }}</a>
          <form class="inline-delete-form" method="post" action="/delete_client/{{ cl[0]|urlencode }}"
                onsubmit='return confirm({{ tr.get("client_delete_confirm","Delete this client?")|tojson }})'>
            <button type="submit" style="color:#dc2626;border:1px solid #fecaca;background:#fff1f2;cursor:pointer;font-family:inherit;padding:5px 8px;border-radius:6px;font-weight:bold;font-size:12px;">{{ tr["delete"] }}</button>
          </form>
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

    # Revenue is bucketed by the WORK-PERIOD month (date_from), not the
    # invoice issuance date. Reason: an admin generating last-month's
    # invoices on the 1st-3rd of the next month would otherwise see
    # May's revenue spike on the June bar. For manual invoices
    # date_from = date_to = invoice_date so the bucket matches the
    # invoice's own date — same as before for that source.
    year_rows = c.execute(
        "SELECT DISTINCT strftime('%Y', date_from) as y FROM invoice_records "
        "WHERE COALESCE(deleted,0)=0 AND date_from != '' ORDER BY y DESC"
    ).fetchall()
    available_years = [r[0] for r in year_rows if r[0]]
    current_year_str = str(lux_now().year)
    if current_year_str not in available_years:
        available_years.insert(0, current_year_str)
    sel_year = request.args.get("year", current_year_str)
    if sel_year not in available_years:
        sel_year = available_years[0] if available_years else current_year_str

    # Monthly data for selected year — grouped by date_from month so
    # the chart reflects when the WORK was done, not when it was billed.
    monthly_rows = c.execute("""
        SELECT
            CAST(strftime('%m', date_from) AS INTEGER) as m,
            COALESCE(SUM(amount), 0) as ht,
            COALESCE(SUM(total),  0) as ttc,
            COALESCE(SUM(CASE WHEN paid=1 THEN total ELSE 0 END), 0) as paid_ttc,
            COALESCE(SUM(CASE WHEN paid=0 THEN total ELSE 0 END), 0) as unpaid_ttc,
            COUNT(*) as cnt
        FROM invoice_records
        WHERE COALESCE(deleted,0)=0 AND date_from != ''
              AND strftime('%Y', date_from) = ?
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

    # Per-invoice rows for the drill-down section, grouped into 12
    # buckets by date_from month. The user wanted to be able to expand
    # any month (e.g. June 870 € HT / 1017.90 € TTC) and see exactly
    # which invoices contribute. Uses the same date_from-based window
    # as the bars above so the totals match line-for-line.
    invoice_rows_year = c.execute("""
        SELECT
            CAST(strftime('%m', date_from) AS INTEGER) as m,
            invoice_number, client_name, date_from, date_to, invoice_date,
            COALESCE(amount, 0), COALESCE(vat_amount, 0), COALESCE(total, 0),
            COALESCE(paid, 0), COALESCE(source, 'auto')
        FROM invoice_records
        WHERE COALESCE(deleted,0)=0 AND date_from != ''
              AND strftime('%Y', date_from) = ?
        ORDER BY date_from, invoice_number
    """, (sel_year,)).fetchall()
    invoice_rows_year = sorted(
        invoice_rows_year,
        key=lambda row: (row[3] or "", invoice_number_sort_key(row[1])),
    )
    month_invoices = {m: [] for m in range(1, 13)}
    for row in invoice_rows_year:
        m = int(row[0]) if row[0] is not None else 0
        if 1 <= m <= 12:
            month_invoices[m].append({
                "invoice_number": row[1] or "",
                "client_name":    row[2] or "",
                "date_from":      row[3] or "",
                "date_to":        row[4] or "",
                "invoice_date":   row[5] or "",
                "amount":         float(row[6] or 0),
                "vat_amount":     float(row[7] or 0),
                "total":          float(row[8] or 0),
                "paid":           bool(row[9]),
                "source":         row[10] or "auto",
            })

    # Per-client breakdown for the year — same date_from bucketing as
    # the monthly chart so the totals add up consistently.
    client_rows = c.execute("""
        SELECT client_name, COALESCE(SUM(total),0) as ttc, COUNT(*) as cnt
        FROM invoice_records
        WHERE COALESCE(deleted,0)=0 AND date_from != ''
              AND strftime('%Y', date_from) = ?
        GROUP BY client_name ORDER BY ttc DESC LIMIT 12
    """, (sel_year,)).fetchall()
    client_names  = [r[0] or '—' for r in client_rows]
    client_totals = [round(r[1], 2) for r in client_rows]

    # Previous year comparison — work-period basis, same as the rest.
    prev_year = str(int(sel_year) - 1)
    prev_row = c.execute(
        "SELECT COALESCE(SUM(total),0) FROM invoice_records "
        "WHERE COALESCE(deleted,0)=0 AND date_from!='' "
        "AND strftime('%Y',date_from)=?",
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
    <div class="kpi-card" style="border-left:4px solid #06b6d4;"
         title="{{ tr.get('diagram_avg_tooltip','Calculated only over months with invoices.') }}">
      <div class="kpi-label">{{ tr.get("diagram_avg_active_month","Prosjek aktivnih mjeseci") }}</div>
      <div class="kpi-value" style="color:{{ '#67e8f9' if dark else '#0891b2' }};">
        {{ '%.2f'|format(avg_monthly) }} €
      </div>
      <div class="kpi-sub">
        {{ tr.get("diagram_avg_formula","TTC ukupno / aktivni mjeseci") }}: {{ active_months }}
      </div>
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
          <th></th>
        </tr>
      </thead>
      <tbody>
      {% for i in range(12) %}
      {% set pct = (month_ttc[i]/total_ttc*100)|round(0)|int if total_ttc > 0 else 0 %}
      {% set m_invs = month_invoices.get(i + 1, []) %}
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
        <td style="text-align:right;">
          {% if m_invs %}
          <button type="button" class="dgmd-toggle" data-target="dgmd-{{ i + 1 }}"
                  style="background:transparent; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }}; color:inherit; border-radius:6px; padding:4px 10px; cursor:pointer; font-size:11px; font-family:inherit;">
            ▸ {{ tr.get("diagram_view_details","Vidi detalje") }}
          </button>
          {% else %}—{% endif %}
        </td>
      </tr>
      {% if m_invs %}
      <tr class="dgmd-row" id="dgmd-{{ i + 1 }}" hidden>
        <td colspan="9" style="padding:0; background:{{ '#0f0f10' if dark else '#f8fafc' }};">
          <div style="padding:12px 16px; border-top:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }}; border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};">
            <div style="font-weight:700; font-size:13px; margin-bottom:8px;">
              📋 {{ tr.get("diagram_month_detail","Detalj po mjesecu") }} — {{ month_names[i] }} {{ sel_year }}
              <span style="color:{{ '#94a3b8' if dark else '#64748b' }}; font-weight:400; font-size:12px; margin-left:8px;">
                {{ tr.get("diagram_invoice_count","Br. faktura") }}: {{ m_invs|length }}
              </span>
            </div>
            <table style="width:100%; border-collapse:collapse; font-size:12px;">
              <thead>
                <tr style="text-align:left; border-bottom:1px solid {{ '#2c2c30' if dark else '#e2e8f0' }};">
                  <th style="padding:6px 8px;">{{ tr.get("invoice_number","N°") }}</th>
                  <th style="padding:6px 8px;">{{ tr.get("client","Klijent") }}</th>
                  <th style="padding:6px 8px;">{{ tr.get("diagram_work_period","Period rada") }}</th>
                  <th style="padding:6px 8px;">{{ tr.get("diagram_issue_date","Datum izdavanja") }}</th>
                  <th style="padding:6px 8px; text-align:right;">HT (€)</th>
                  <th style="padding:6px 8px; text-align:right;">TVA (€)</th>
                  <th style="padding:6px 8px; text-align:right;">TTC (€)</th>
                  <th style="padding:6px 8px;">{{ tr.get("paid","Placeno") }}</th>
                </tr>
              </thead>
              <tbody>
                {% for inv in m_invs %}
                <tr style="border-bottom:1px solid {{ '#1d1d1f' if dark else '#f1f5f9' }};">
                  <td style="padding:6px 8px;"><a href="/invoices/view?invoice_number={{ inv.invoice_number|urlencode }}" style="color:{{ '#93c5fd' if dark else '#1f4f82' }}; text-decoration:underline; font-weight:600;">{{ inv.invoice_number }}</a>{% if inv.source == 'manual' %} <a href="/invoices/manual?invoice_number={{ inv.invoice_number|urlencode }}" style="color:{{ '#ffd429' if dark else '#b45309' }}; text-decoration:none; margin-left:4px;" title="{{ tr.get('diagram_fix_period','Ispravi period rada') }}">✏️</a>{% endif %}</td>
                  <td style="padding:6px 8px;">{{ inv.client_name }}</td>
                  <td style="padding:6px 8px; font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }};">{{ format_date(inv.date_from) }} → {{ format_date(inv.date_to) }}</td>
                  <td style="padding:6px 8px; font-size:11px; color:{{ '#94a3b8' if dark else '#64748b' }};">{{ format_date(inv.invoice_date) }}</td>
                  <td style="padding:6px 8px; text-align:right;">{{ '%.2f'|format(inv.amount) }}</td>
                  <td style="padding:6px 8px; text-align:right; color:{{ '#94a3b8' if dark else '#64748b' }};">{{ '%.2f'|format(inv.vat_amount) }}</td>
                  <td style="padding:6px 8px; text-align:right; font-weight:700; color:{{ '#c4b5fd' if dark else '#7c3aed' }};">{{ '%.2f'|format(inv.total) }}</td>
                  <td style="padding:6px 8px;">
                    {% if inv.paid %}<span style="color:#16a34a; font-weight:700;">✓</span>{% else %}<span style="color:#dc2626; font-weight:700;">✗</span>{% endif %}
                  </td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
        </td>
      </tr>
      {% endif %}
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
          <td></td>
        </tr>
      </tfoot>
    </table>
    <script>
      // Inline expand/collapse for the month-detail rows. <details>
      // doesn't compose cleanly inside <tbody>, so we toggle a hidden
      // attribute on the sibling <tr.dgmd-row> instead.
      document.querySelectorAll('.dgmd-toggle').forEach(function(btn){
        btn.addEventListener('click', function(){
          var row = document.getElementById(btn.dataset.target);
          if (!row) return;
          var open = row.hasAttribute('hidden') ? false : true;
          if (open) { row.setAttribute('hidden',''); btn.textContent = btn.textContent.replace('▾','▸'); }
          else      { row.removeAttribute('hidden'); btn.textContent = btn.textContent.replace('▸','▾'); }
        });
      });
    </script>
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
     active_months=active_months, avg_monthly=avg_monthly,
     month_invoices=month_invoices, format_date=format_date)


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
          <label for="payrollFromDate" style="font-size:12px; color:{{ '#94a3b8' if dark else '#64748b' }}; display:block; margin-bottom:4px;">{{ tr["date_from"] }}</label>
          <input id="payrollFromDate" type="date" name="date_from" value="{{ date_from }}" required style="padding:9px 12px; border-radius:8px; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }}; background:{{ '#111113' if dark else '#f8fafc' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }};">
        </div>
        <div>
          <label for="payrollToDate" style="font-size:12px; color:{{ '#94a3b8' if dark else '#64748b' }}; display:block; margin-bottom:4px;">{{ tr["date_to"] }}</label>
          <input id="payrollToDate" type="date" name="date_to" value="{{ date_to }}" required style="padding:9px 12px; border-radius:8px; border:1px solid {{ '#2c2c30' if dark else '#cbd5e1' }}; background:{{ '#111113' if dark else '#f8fafc' }}; color:{{ '#e2e8f0' if dark else '#1e293b' }};">
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


@app.route("/delete_user/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    if session.get("role") != "admin": return redirect("/")
    conn = get_conn(); c = conn.cursor(); user = c.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if user and user[0] != "admin": c.execute("DELETE FROM users WHERE id = ?", (user_id,)); c.execute("DELETE FROM workers WHERE name = ?", (user[0],)); c.execute("DELETE FROM worker_colors WHERE worker_name = ?", (user[0],))
    conn.commit(); conn.close(); return redirect("/admin")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
