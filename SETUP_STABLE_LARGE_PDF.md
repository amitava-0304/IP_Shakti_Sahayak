# IP-SHAKTI Sahayak — Stable Large-PDF OCR Update

This package replaces the "one thread per upload + in-memory job status"
approach with:

- one persistent background worker
- one OCR/indexing job at a time
- SQLite job queue stored on the Railway volume
- page-by-page OCR
- page-by-page Chroma indexing
- restart/resume from the last completed page
- persistent upload status
- live page/progress/chunk counts in the existing frontend

## Why this is more stable

The previous low-memory version still built an in-memory `sections` list
for the complete PDF. This version never keeps the whole document text
in memory.

For a PDF:

1. Read one page.
2. Try pypdf extraction.
3. OCR only if required.
4. Append that page to `uploaded_text/<file>.txt`.
5. Chunk that page.
6. Upsert its chunks into ChromaDB.
7. Save current_page/chunks in SQLite.
8. Release memory.
9. Continue to the next page.

The peak memory therefore stays much closer to a single-page workload
instead of growing with the number of pages.

## Replace these files

- `backend/app.py`
- `backend/upload_ingest.py`
- `frontend/index.html`
- `requirements.txt`
- `Dockerfile`
- `railway.toml`

Add these new files:

- `backend/job_store.py`
- `backend/worker.py`

## Railway volume

Keep the persistent volume mounted at:

`/app/storage`

and set:

`STORAGE_ROOT=/app/storage`

The SQLite job DB will be:

`/app/storage/upload_jobs.sqlite3`

## Recommended Railway variables

```text
STORAGE_ROOT=/app/storage

AUTO_INGEST_ON_START=false
REBUILD_MAIN_ON_START=false

OCR_DPI=90
OCR_MIN_PAGE_TEXT=20
OCR_LANGUAGES=eng

CHROMA_BATCH_SIZE=10
UPLOAD_CHUNK_SIZE=700
UPLOAD_CHUNK_OVERLAP=80

WORKER_POLL_SECONDS=2
```

For Bengali/Hindi OCR later:

```text
OCR_LANGUAGES=eng+ben+hin
```

Test memory before leaving all language models enabled.

## Important production behavior

Only one large document is processed at a time.

If 3 users upload large PDFs:

- job 1 = processing
- job 2 = queued
- job 3 = queued

This prevents multiple OCR jobs from exhausting Railway RAM.

## Resume behavior

After every completed page, `current_page` is stored in SQLite.

If Railway restarts at page 643 of a 1000-page PDF:

- the incomplete job is automatically requeued
- processing resumes at page 644
- deterministic Chroma IDs + `upsert()` avoid duplicate chunks

## Syntax checks

```powershell
python -m py_compile backend\app.py
python -m py_compile backend\job_store.py
python -m py_compile backend\worker.py
python -m py_compile backend\upload_ingest.py
```

Then:

```powershell
git diff --check
git status
```

## Push

```powershell
git add backend\app.py backend\upload_ingest.py backend\job_store.py backend\worker.py frontend\index.html requirements.txt Dockerfile railway.toml

git commit -m "Add resumable streaming OCR worker for large PDFs"

git push origin main
```

## After Railway deploys

Check:

- `/health`
- `/api/status`
- `/docs`

Then upload a scanned PDF.

The frontend should show:

```text
OCR processing page 138 of 1000.
Page 137 / 1000
Progress: 13.7%
Chunks indexed: ...
Elapsed time: ...
```

## Practical meaning of "any number of pages"

No finite cloud instance can guarantee literally unlimited input.

This architecture makes memory use approximately page-bounded, so the
main limits become:

- total processing time
- persistent storage
- Railway CPU allowance
- document corruption
- OCR quality

instead of RAM increasing with total page count.

For very large production workloads or many simultaneous users, the next
step is a dedicated worker service plus object storage and a durable queue.
