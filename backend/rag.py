import os
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from dotenv import load_dotenv


# =========================================================
# PATHS / ENVIRONMENT
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

STORAGE_ROOT = Path(
    os.getenv(
        "STORAGE_ROOT",
        str(PROJECT_ROOT)
    )
)

VECTOR_FOLDER = STORAGE_ROOT / "chroma_db"

MAIN_COLLECTION = "ip_sakti_main"
UPLOAD_COLLECTION = "ip_sakti_uploads"

VECTOR_FOLDER.mkdir(
    parents=True,
    exist_ok=True
)

RAG_PARALLEL_SEARCH = (
    os.getenv(
        "RAG_PARALLEL_SEARCH",
        "true"
    )
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

RAG_WARMUP_EMBEDDING = (
    os.getenv(
        "RAG_WARMUP_EMBEDDING",
        "true"
    )
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

RAG_QUERY_CACHE_SIZE = int(
    os.getenv(
        "RAG_QUERY_CACHE_SIZE",
        "100"
    )
)


# =========================================================
# CHROMA CLIENT
# =========================================================

client = chromadb.PersistentClient(
    path=str(VECTOR_FOLDER)
)


# =========================================================
# SINGLE PERSISTENT EMBEDDING FUNCTION
#
# Important:
# Creating/fetching a collection repeatedly can expose a fresh
# DefaultEmbeddingFunction object. The actual query embedding was
# taking ~5 seconds in Railway. This singleton stays alive for the
# lifetime of the FastAPI process, so the ONNX model is loaded once.
# =========================================================

QUERY_EMBEDDING_FUNCTION = DefaultEmbeddingFunction()

_embedding_lock = Lock()
_embedding_cache_lock = Lock()

# Small LRU cache for repeated/similar exact questions.
_embedding_cache = OrderedDict()


def _normalise_embedding(embedding):
    if hasattr(embedding, "tolist"):
        return embedding.tolist()

    return list(embedding)


def _cache_get(query):
    with _embedding_cache_lock:
        if query not in _embedding_cache:
            return None

        embedding = _embedding_cache.pop(query)
        _embedding_cache[query] = embedding
        return embedding


def _cache_put(query, embedding):
    if RAG_QUERY_CACHE_SIZE <= 0:
        return

    with _embedding_cache_lock:
        if query in _embedding_cache:
            _embedding_cache.pop(query)

        _embedding_cache[query] = embedding

        while (
            len(_embedding_cache)
            > RAG_QUERY_CACHE_SIZE
        ):
            _embedding_cache.popitem(
                last=False
            )


def create_query_embedding(query):
    cached = _cache_get(query)

    if cached is not None:
        print(
            "RAG query embedding time: "
            "0.000 seconds (cache hit)",
            flush=True
        )
        return cached

    started = time.perf_counter()

    # Protect the singleton inference object from simultaneous calls.
    with _embedding_lock:
        embeddings = QUERY_EMBEDDING_FUNCTION(
            [query]
        )

    embedding = _normalise_embedding(
        embeddings[0]
    )

    elapsed = round(
        time.perf_counter() - started,
        3
    )

    _cache_put(
        query,
        embedding
    )

    print(
        f"RAG query embedding time: "
        f"{elapsed} seconds",
        flush=True
    )

    return embedding


def warm_up_embedding_model():
    if not RAG_WARMUP_EMBEDDING:
        return

    started = time.perf_counter()

    try:
        # Do not cache this artificial warm-up query.
        with _embedding_lock:
            QUERY_EMBEDDING_FUNCTION(
                ["IP SHAKTI embedding model warmup"]
            )

        elapsed = round(
            time.perf_counter() - started,
            3
        )

        print(
            f"RAG embedding model warmed up "
            f"in {elapsed} seconds.",
            flush=True
        )

    except Exception as error:
        print(
            "RAG embedding warmup failed: "
            f"{error}",
            flush=True
        )


# Warm up once when backend.rag is imported.
# This moves model initialization cost to application startup
# instead of making the user wait on the first question.
warm_up_embedding_model()


# =========================================================
# FRESH COLLECTION HANDLES
# =========================================================

def get_main_collection():
    return client.get_or_create_collection(
        name=MAIN_COLLECTION
    )


def get_upload_collection():
    return client.get_or_create_collection(
        name=UPLOAD_COLLECTION
    )


# =========================================================
# RESULT CONVERSION
# =========================================================

def _result_to_rows(result):
    documents = (
        result.get("documents")
        or [[]]
    )[0]

    metadatas = (
        result.get("metadatas")
        or [[]]
    )[0]

    distances = (
        result.get("distances")
        or [[]]
    )[0]

    rows = []

    for index, document in enumerate(
        documents
    ):
        metadata = (
            metadatas[index]
            if index < len(metadatas)
            else {}
        )

        distance = (
            distances[index]
            if index < len(distances)
            else None
        )

        rows.append(
            {
                "text": document,
                "metadata": metadata or {},
                "distance": distance
            }
        )

    return rows


# =========================================================
# QUERY COLLECTION USING PRECOMPUTED EMBEDDING
# =========================================================

def _query_collection_object(
    collection,
    query_embedding,
    top_k,
    count
):
    if count <= 0:
        return []

    started = time.perf_counter()

    result = collection.query(
        query_embeddings=[
            query_embedding
        ],
        n_results=min(
            top_k,
            count
        ),
        include=[
            "documents",
            "metadatas",
            "distances"
        ]
    )

    elapsed = round(
        time.perf_counter() - started,
        3
    )

    print(
        f"Chroma collection query time: "
        f"{elapsed} seconds",
        flush=True
    )

    return _result_to_rows(
        result
    )


def query_collection(
    collection_name,
    query,
    top_k
):
    if collection_name == MAIN_COLLECTION:
        collection = get_main_collection()
    else:
        collection = get_upload_collection()

    count = collection.count()

    if count <= 0:
        return []

    query_embedding = (
        create_query_embedding(
            query
        )
    )

    return _query_collection_object(
        collection,
        query_embedding,
        top_k,
        count
    )


# =========================================================
# SEARCH BOTH COLLECTIONS
# =========================================================

def search_documents(
    query,
    top_k=5,
    final_results=3,
    distance_margin=0.45
):
    query = (
        query
        or ""
    ).strip()

    if not query:
        return []

    total_started = time.perf_counter()

    main_collection = get_main_collection()
    upload_collection = get_upload_collection()

    count_started = time.perf_counter()

    main_count = main_collection.count()
    upload_count = upload_collection.count()

    count_elapsed = round(
        time.perf_counter() - count_started,
        3
    )

    print(
        f"Chroma count time: "
        f"{count_elapsed} seconds "
        f"(main={main_count}, "
        f"uploads={upload_count})",
        flush=True
    )

    if (
        main_count <= 0
        and upload_count <= 0
    ):
        return []

    # Query is embedded exactly once.
    query_embedding = (
        create_query_embedding(
            query
        )
    )

    def search_main():
        return _query_collection_object(
            main_collection,
            query_embedding,
            top_k,
            main_count
        )

    def search_uploads():
        return _query_collection_object(
            upload_collection,
            query_embedding,
            top_k,
            upload_count
        )

    combined = []

    if (
        RAG_PARALLEL_SEARCH
        and main_count > 0
        and upload_count > 0
    ):
        try:
            with ThreadPoolExecutor(
                max_workers=2
            ) as executor:
                main_future = executor.submit(
                    search_main
                )
                upload_future = executor.submit(
                    search_uploads
                )

                combined.extend(
                    main_future.result()
                )
                combined.extend(
                    upload_future.result()
                )

        except Exception as error:
            print(
                "Parallel Chroma search failed; "
                f"retrying sequentially: {error}",
                flush=True
            )

            combined = []
            combined.extend(
                search_main()
            )
            combined.extend(
                search_uploads()
            )

    else:
        combined.extend(
            search_main()
        )
        combined.extend(
            search_uploads()
        )

    if not combined:
        return []

    combined.sort(
        key=lambda item: (
            item.get("distance")
            if item.get("distance") is not None
            else float("inf")
        )
    )

    valid_distances = [
        item["distance"]
        for item in combined
        if item.get("distance") is not None
    ]

    if valid_distances:
        best_distance = min(
            valid_distances
        )

        max_distance = (
            best_distance
            + distance_margin
        )

        combined = [
            item
            for item in combined
            if (
                item.get("distance") is None
                or item.get("distance")
                <= max_distance
            )
        ]

    deduplicated = []
    seen = set()

    for item in combined:
        metadata = (
            item.get("metadata")
            or {}
        )

        text = (
            item.get("text")
            or ""
        )

        key = (
            metadata.get("source"),
            metadata.get("page"),
            text[:250]
        )

        if key in seen:
            continue

        seen.add(key)

        distance = item.get(
            "distance"
        )

        if distance is not None:
            item["relevance_score"] = (
                1 / (1 + distance)
            )
        else:
            item["relevance_score"] = None

        deduplicated.append(
            item
        )

        if (
            len(deduplicated)
            >= final_results
        ):
            break

    total_elapsed = round(
        time.perf_counter()
        - total_started,
        3
    )

    print(
        f"RAG total retrieval time: "
        f"{total_elapsed} seconds",
        flush=True
    )

    return deduplicated


# =========================================================
# STATUS HELPERS
# =========================================================

def get_collection_counts():
    main_collection = get_main_collection()
    upload_collection = get_upload_collection()

    return {
        "main": main_collection.count(),
        "uploads": upload_collection.count()
    }
