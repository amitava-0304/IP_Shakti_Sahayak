# IP-SHAKTI Persistent Semantic Query Cache

This update adds a persistent answer cache on the existing Railway volume.

## Behavior

### Exact repeated question

```text
Question
   ↓
SQLite exact cache hit
   ↓
Return answer
```

No embedding, Chroma search or Gemini/Groq request is needed.

### Similar question

```text
Question
   ↓
Create query embedding
   ↓
Compare with cached query embeddings
   ↓
Similarity >= 0.97
   ↓
Return cached answer
```

### Cache miss

```text
Question
   ↓
Create embedding once
   ↓
Semantic-cache lookup
   ↓
Chroma search with SAME embedding
   ↓
Gemini/Groq
   ↓
Store successful result in persistent cache
```

## Persistence

Cache database:

```text
/app/storage/semantic_query_cache.sqlite3
```

It survives Railway restarts because `STORAGE_ROOT=/app/storage` is already on your volume.

## Safety against stale answers

The cache is automatically cleared when:

- a successfully uploaded document finishes indexing
- the permanent knowledge base is ingested/rebuilt through startup ingestion

Entries also expire automatically by TTL.

## Replace/add files

Replace:

```text
backend/app.py
backend/rag.py
backend/worker.py
```

Add:

```text
backend/semantic_cache.py
```

No new Python package and no Redis are required.

## Recommended Railway variables

```text
RAG_CACHE_TTL_SECONDS=21600
RAG_CACHE_MAX_ENTRIES=500
RAG_SEMANTIC_CACHE_THRESHOLD=0.97
RAG_SEMANTIC_CACHE_SCAN_LIMIT=250
RAG_QUERY_CACHE_SIZE=100
RAG_WARMUP_EMBEDDING=true
RAG_PARALLEL_SEARCH=true
```

`21600` seconds = 6 hours.

The semantic threshold is intentionally conservative because your assistant answers IP/Ayurveda knowledge questions. Lowering it too much can reuse an answer for a question that only looks similar.

## Test

```powershell
cd D:\IP_Shakti_Sahayak
python -m py_compile backend\app.py
python -m py_compile backend\rag.py
python -m py_compile backend\worker.py
python -m py_compile backend\semantic_cache.py
```

## Push

```powershell
git add backend\app.py backend\rag.py backend\worker.py backend\semantic_cache.py

git commit -m "Add persistent semantic RAG cache"

git push origin main
```

## Expected test

Ask a new question once:

```text
Provider: Gemini
Search: ~1-2s
AI: ~1s
```

Ask the exact same question again:

```text
Provider: Cache
Search: ~0.00-0.05s
AI: 0.00s
Total: ~0.00-0.05s
```

A high-confidence paraphrase can show:

```text
Provider: Cache
cache_type: semantic
cache_similarity: 0.98
```

## Status endpoint

`/api/status` now also includes:

```json
{
  "semantic_cache_entries": 12,
  "semantic_cache_hits": 8
}
```
