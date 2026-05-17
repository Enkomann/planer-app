# Luxmann Planner

Flask application for worker schedules, clients, absences, monthly/weekly calendars, PDF exports, and route planning.

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Default local accounts are created only when the database is empty:

- `admin` / `admin123`
- `worker1` / `1234`

Change these passwords immediately after first login.

## Render environment variables

Set these in Render before deploying:

- `SECRET_KEY`: long random secret used for Flask sessions
- `DATABASE_URL`: Render PostgreSQL internal database URL
- `ORS_API_KEY`: OpenRouteService key for route optimization

Render start command:

```bash
gunicorn app:app
```

Recommended Python version on Render:

```text
3.12.7
```
