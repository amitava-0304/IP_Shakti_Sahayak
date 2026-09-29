# IP-SHAKTI RAG Fast Search Update

This update targets the slow ChromaDB retrieval stage.

Your last measured result was approximately:

```text
Search: 5.73s
AI: 1.23s
Total: 6.96s
```

The new `backend/rag.py` keeps your two existing collections but improves retrieval by:

- creating the query embedding only once
- reusing that embedding for both `ip_sakti_main` and `ip_sakti_uploads`
- searching the two collections concurrently when possible
- keeping fresh collection handles to avoid stale UUID errors
- retaining distance filtering, deduplication, source metadata, and relevance scores
- changing defaults to `top_k=5`, `final_results=3`
- printing detailed timing in Railway logs
- automatically falling back if the single-embedding optimization or parallel search is unavailable

## Replace

Replace:

```text
D:\IP_Shakti_Sahayak\backend\rag.py
```

with the provided `backend/rag.py`.

No changes are required to `app.py` or `frontend/index.html` for this update.

## Railway variable

Optional; default is already true:

```text
RAG_PARALLEL_SEARCH=true
```

If you ever see Chroma concurrency errors, set:

```text
RAG_PARALLEL_SEARCH=false
```

## Test locally

```powershell
cd D:\IP_Shakti_Sahayak
python -m py_compile backend\rag.py
```

## Push

```powershell
git add backend\rag.py
git commit -m "Optimize Chroma RAG retrieval speed"
git push origin main
```

## Railway logs

After deployment, ask a question and look for:

```text
Chroma count time: ...
RAG query embedding time: ...
Chroma collection query time: ...
RAG total retrieval time: ...
Document search time: ...
Gemini response time: ...
```

The first question after a restart can still be slower because Chroma/the embedding model may need to warm up. Compare the second and third questions as well.
