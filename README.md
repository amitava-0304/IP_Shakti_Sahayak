# IP-SHAKTI Sahayak — Railway Ready

This package is configured to run the frontend and FastAPI backend from the
same Railway service.

## Project structure

```text
IP_Shakti_Sahayak/
├── backend/
│   ├── __init__.py
│   ├── app.py
│   ├── rag.py
│   ├── ingest.py
│   ├── upload_ingest.py
│   └── map_service.py
├── frontend/
│   └── index.html
├── data/
│   ├── ayurveda/
│   ├── copyright/
│   ├── international/
│   ├── patents/
│   ├── regulations/
│   ├── trademarks/
│   └── traditional_knowledge/
├── .env.example
├── .gitignore
├── .python-version
├── Procfile
├── railway.toml
└── requirements.txt
```

## Important before deploying

Copy your existing permanent PDFs into the matching folders under `data/`.
For example:

```text
data/international/WIPO_Knowledge_Guide_IP_SHAKTI.pdf
```

Do not commit `.env`.

## Local run

Create/activate a virtual environment, install requirements, then:

```powershell
pip install -r requirements.txt
uvicorn backend.app:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

The frontend now uses same-origin API calls, so a separate
`python -m http.server 5500` is not required.

## Railway variables

Add these in Railway > Service > Variables:

```text
GEMINI_API_KEY=...
GROQ_API_KEY=...
SERPAPI_API_KEY=...
STORAGE_ROOT=/app/storage
AUTO_INGEST_ON_START=true
REBUILD_MAIN_ON_START=false
```

## Railway persistent volume

Attach a Railway Volume to:

```text
/app/storage
```

It will contain:

```text
/app/storage/chroma_db
/app/storage/uploaded_files
```

This keeps ChromaDB and user uploads across redeployments.

## First deployment

On the first boot, if the main Chroma collection is empty and
`AUTO_INGEST_ON_START=true`, the backend indexes PDFs under `data/`.

Uploaded documents are stored in a separate collection and are never deleted
when the main collection is rebuilt.

## Rebuilding the main knowledge base

Normally keep:

```text
REBUILD_MAIN_ON_START=false
```

If you add/replace permanent PDFs and need to rebuild the main collection,
temporarily set:

```text
REBUILD_MAIN_ON_START=true
```

Redeploy/restart once, then set it back to `false`.

## Endpoints

```text
GET  /               Frontend
GET  /health         Railway health check
GET  /api/status     API/provider/database status
POST /api/search
POST /api/upload
POST /api/ayurveda-centres
```
