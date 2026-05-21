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
- `DOCUMENT_STORAGE_DIR` is optional. By default uploaded documents are stored in `storage/documents`.
- `MAX_UPLOAD_MB` is optional. Folder/document upload requests default to 600 MB; split very large folders into smaller batches when needed.

The `render.yaml` blueprint attaches a 10 GB persistent disk at:

```text
/opt/render/project/src/storage
```

Only upload business documents after the disk is attached on Render. Otherwise files written to the normal web-service filesystem are not persistent across deploys and restarts.

The Documents screen supports a single document, multiple selected documents, or a whole folder upload. Folder upload adds each allowed document from that folder as its own document record.

Render start command:

```bash
gunicorn app:app
```

Recommended Python version on Render:

```text
3.12.7
```
