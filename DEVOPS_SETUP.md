# IP-SHAKTI DevOps Setup

This adds a basic DevOps pipeline to the existing IP-SHAKTI Sahayak project.

## Added files

```text
Dockerfile
.dockerignore
docker-compose.yml
.github/workflows/ci.yml
health_check.py
```

## Pipeline

```text
Local Development
      ↓
Git
      ↓
GitHub
      ↓
GitHub Actions CI
      ↓
Docker Build
      ↓
Railway
      ↓
/health + /api/status
      ↓
Monitoring
```

## Local Docker test

```powershell
docker build -t ip-shakti-sahayak .
docker run --env-file .env -e STORAGE_ROOT=/app/storage -p 8000:8000 ip-shakti-sahayak
```

Open:

```text
http://127.0.0.1:8000
http://127.0.0.1:8000/health
http://127.0.0.1:8000/api/status
```

## Docker Compose

```powershell
docker compose up --build
```

Stop:

```powershell
docker compose down
```

## GitHub Actions

On every push or pull request to `main`, CI will:

```text
Checkout repository
→ Set up Python 3.12
→ Install requirements
→ Syntax-check Python files
→ Import FastAPI app
→ Build Docker image
```

## Railway variables

```text
GEMINI_API_KEY=...
GROQ_API_KEY=...
SERPAPI_API_KEY=...
STORAGE_ROOT=/app/storage
AUTO_INGEST_ON_START=false
REBUILD_MAIN_ON_START=false
CHROMA_BATCH_SIZE=75
```

Railway volume:

```text
/app/storage
```

## Monitoring

Existing endpoints:

```text
GET /health
GET /api/status
```

Run locally:

```powershell
python health_check.py
```

## Recommended next steps

- pytest API tests
- dependency/security scanning
- Sentry
- uptime monitoring
- Azure App Service or AWS ECS later
- Kubernetes only when multiple services/scaling justify it
