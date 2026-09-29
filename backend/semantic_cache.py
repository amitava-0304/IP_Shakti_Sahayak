import json
import math
import os
import sqlite3
import time
from pathlib import Path
from threading import Lock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORAGE_ROOT = Path(
    os.getenv(
        "STORAGE_ROOT",
        str(PROJECT_ROOT)
    )
)

CACHE_DB = STORAGE_ROOT / "semantic_query_cache.sqlite3"

CACHE_TTL_SECONDS = int(
    os.getenv(
        "RAG_CACHE_TTL_SECONDS",
        "21600"
    )
)

CACHE_MAX_ENTRIES = int(
    os.getenv(
        "RAG_CACHE_MAX_ENTRIES",
        "500"
    )
)

SEMANTIC_THRESHOLD = float(
    os.getenv(
        "RAG_SEMANTIC_CACHE_THRESHOLD",
        "0.97"
    )
)

SEMANTIC_SCAN_LIMIT = int(
    os.getenv(
        "RAG_SEMANTIC_CACHE_SCAN_LIMIT",
        "250"
    )
)

# Changing the prompt/output style should not reuse old cached answers.
# Increase this value whenever the answer-generation prompt changes
# substantially.
CACHE_VERSION = os.getenv(
    "RAG_CACHE_VERSION",
    "v2-detailed"
).strip()

STORAGE_ROOT.mkdir(
    parents=True,
    exist_ok=True
)

_db_lock = Lock()


def _connect():
    connection = sqlite3.connect(
        str(CACHE_DB),
        timeout=30,
        check_same_thread=False
    )

    connection.row_factory = sqlite3.Row
    connection.execute(
        "PRAGMA journal_mode=WAL;"
    )
    connection.execute(
        "PRAGMA synchronous=NORMAL;"
    )

    return connection


def init_semantic_cache():
    with _db_lock:
        with _connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_question TEXT NOT NULL,
                    original_question TEXT NOT NULL,
                    language TEXT NOT NULL,
                    embedding_json TEXT,
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_used_at REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_semantic_cache_exact
                ON semantic_cache(normalized_question, language)
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_semantic_cache_recent
                ON semantic_cache(last_used_at DESC)
                """
            )

            connection.commit()


def normalize_question(question):
    return " ".join(
        (question or "")
        .strip()
        .lower()
        .split()
    )


def _cache_language(language):
    language = (
        language
        or "English"
    ).strip()

    return (
        f"{language}::"
        f"{CACHE_VERSION}"
    )


def _is_fresh(created_at):
    if CACHE_TTL_SECONDS <= 0:
        return True

    return (
        time.time() - float(created_at)
        <= CACHE_TTL_SECONDS
    )


def _touch(cache_id):
    with _db_lock:
        with _connect() as connection:
            connection.execute(
                """
                UPDATE semantic_cache
                SET
                    last_used_at = ?,
                    hit_count = hit_count + 1
                WHERE id = ?
                """,
                (
                    time.time(),
                    cache_id
                )
            )
            connection.commit()


def get_exact_cache(question, language):
    normalized = normalize_question(
        question
    )

    language = _cache_language(
        language
    )

    with _db_lock:
        with _connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM semantic_cache
                WHERE
                    normalized_question = ?
                    AND language = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    normalized,
                    language
                )
            ).fetchone()

    if not row:
        return None

    if not _is_fresh(
        row["created_at"]
    ):
        return None

    try:
        response = json.loads(
            row["response_json"]
        )
    except Exception:
        return None

    _touch(
        row["id"]
    )

    return {
        "type": "exact",
        "similarity": 1.0,
        "response": response
    }


def _cosine_similarity(vector_a, vector_b):
    if (
        not vector_a
        or not vector_b
        or len(vector_a) != len(vector_b)
    ):
        return -1.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0

    for a, b in zip(
        vector_a,
        vector_b
    ):
        a = float(a)
        b = float(b)

        dot += a * b
        norm_a += a * a
        norm_b += b * b

    if norm_a <= 0 or norm_b <= 0:
        return -1.0

    return dot / math.sqrt(
        norm_a * norm_b
    )


def get_semantic_cache(
    question,
    language,
    query_embedding
):
    if not query_embedding:
        return None

    language = _cache_language(
        language
    )

    cutoff = (
        time.time()
        - CACHE_TTL_SECONDS
        if CACHE_TTL_SECONDS > 0
        else 0
    )

    with _db_lock:
        with _connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    embedding_json,
                    response_json,
                    created_at
                FROM semantic_cache
                WHERE
                    language = ?
                    AND created_at >= ?
                    AND embedding_json IS NOT NULL
                ORDER BY last_used_at DESC
                LIMIT ?
                """,
                (
                    language,
                    cutoff,
                    SEMANTIC_SCAN_LIMIT
                )
            ).fetchall()

    best_row = None
    best_similarity = -1.0

    for row in rows:
        try:
            cached_embedding = json.loads(
                row["embedding_json"]
            )
        except Exception:
            continue

        similarity = _cosine_similarity(
            query_embedding,
            cached_embedding
        )

        if similarity > best_similarity:
            best_similarity = similarity
            best_row = row

    if (
        best_row is None
        or best_similarity
        < SEMANTIC_THRESHOLD
    ):
        return None

    try:
        response = json.loads(
            best_row["response_json"]
        )
    except Exception:
        return None

    _touch(
        best_row["id"]
    )

    return {
        "type": "semantic",
        "similarity": round(
            best_similarity,
            4
        ),
        "response": response
    }


def save_cache_entry(
    question,
    language,
    query_embedding,
    response
):
    if (
        not response
        or response.get("error")
        or not response.get("answer")
    ):
        return

    normalized = normalize_question(
        question
    )

    language = (
        language
        or "English"
    ).strip()

    now = time.time()

    embedding_json = (
        json.dumps(
            query_embedding,
            separators=(",", ":")
        )
        if query_embedding
        else None
    )

    response_copy = dict(
        response
    )

    # Do not persist transient timing/cache fields.
    response_copy.pop(
        "timing",
        None
    )
    response_copy.pop(
        "cache_hit",
        None
    )
    response_copy.pop(
        "cache_type",
        None
    )
    response_copy.pop(
        "cache_similarity",
        None
    )

    response_json = json.dumps(
        response_copy,
        ensure_ascii=False,
        separators=(",", ":")
    )

    with _db_lock:
        with _connect() as connection:
            # Keep one newest exact entry for each question/language.
            connection.execute(
                """
                DELETE FROM semantic_cache
                WHERE
                    normalized_question = ?
                    AND language = ?
                """,
                (
                    normalized,
                    language
                )
            )

            connection.execute(
                """
                INSERT INTO semantic_cache (
                    normalized_question,
                    original_question,
                    language,
                    embedding_json,
                    response_json,
                    created_at,
                    last_used_at,
                    hit_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    normalized,
                    question,
                    language,
                    embedding_json,
                    response_json,
                    now,
                    now
                )
            )

            # Bound persistent cache size.
            if CACHE_MAX_ENTRIES > 0:
                connection.execute(
                    """
                    DELETE FROM semantic_cache
                    WHERE id IN (
                        SELECT id
                        FROM semantic_cache
                        ORDER BY last_used_at DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (
                        CACHE_MAX_ENTRIES,
                    )
                )

            connection.commit()


def clear_semantic_cache():
    init_semantic_cache()

    with _db_lock:
        with _connect() as connection:
            connection.execute(
                "DELETE FROM semantic_cache"
            )
            connection.commit()

    print(
        "Persistent semantic cache cleared.",
        flush=True
    )


def get_cache_stats():
    init_semantic_cache()

    with _db_lock:
        with _connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS entries,
                    COALESCE(SUM(hit_count), 0) AS hits
                FROM semantic_cache
                """
            ).fetchone()

    return {
        "entries": int(
            row["entries"]
            or 0
        ),
        "hits": int(
            row["hits"]
            or 0
        )
    }


init_semantic_cache()
