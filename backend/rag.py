import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import chromadb
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

# Search both Chroma collections concurrently after creating
# the query embedding only once.
RAG_PARALLEL_SEARCH = (
    os.getenv(
        "RAG_PARALLEL_SEARCH",
        "true"
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on"
    }
)


# =========================================================
# CHROMA CLIENT
# =========================================================

client = chromadb.PersistentClient(
    path=str(VECTOR_FOLDER)
)


# =========================================================
# ALWAYS GET FRESH COLLECTION HANDLES
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
# CREATE QUERY EMBEDDING ONCE
# =========================================================

def _create_query_embedding(
    query,
    main_collection,
    upload_collection,
    main_count,
    upload_count
):
    """
    Chroma's query_texts path embeds the same question again for
    every collection. Because both project collections were created
    using the same default embedding configuration, we can create the
    question embedding once and reuse it for both searches.

    If Chroma's internal embedding-function API changes, this returns
    None and search_documents() automatically falls back to the old,
    compatible query_texts path.
    """

    collection = None

    if main_count > 0:
        collection = main_collection
    elif upload_count > 0:
        collection = upload_collection

    if collection is None:
        return None

    try:
        embedding_function = getattr(
            collection,
            "_embedding_function",
            None
        )

        if embedding_function is None:
            return None

        started = time.perf_counter()

        embeddings = embedding_function(
            [query]
        )

        elapsed = round(
            time.perf_counter() - started,
            3
        )

        print(
            f"RAG query embedding time: "
            f"{elapsed} seconds",
            flush=True
        )

        if embeddings is None:
            return None

        # Convert the first embedding to a normal Python list.
        embedding = embeddings[0]

        if hasattr(
            embedding,
            "tolist"
        ):
            embedding = embedding.tolist()
        else:
            embedding = list(embedding)

        return embedding

    except Exception as error:
        print(
            "Single query embedding optimization "
            f"not available: {error}",
            flush=True
        )
        return None


# =========================================================
# QUERY ONE COLLECTION
# =========================================================

def _query_collection_object(
    collection,
    query,
    query_embedding,
    top_k,
    count
):
    if count <= 0:
        return []

    started = time.perf_counter()

    query_args = {
        "n_results": min(
            top_k,
            count
        ),
        "include": [
            "documents",
            "metadatas",
            "distances"
        ]
    }

    if query_embedding is not None:
        query_args[
            "query_embeddings"
        ] = [query_embedding]
    else:
        query_args[
            "query_texts"
        ] = [query]

    result = collection.query(
        **query_args
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
    """
    Compatibility helper retained for any code that calls this
    function directly.
    """

    if collection_name == MAIN_COLLECTION:
        collection = get_main_collection()
    else:
        collection = get_upload_collection()

    count = collection.count()

    return _query_collection_object(
        collection,
        query,
        None,
        top_k,
        count
    )


# =========================================================
# SEARCH BOTH COLLECTIONS - OPTIMIZED
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

    # Fresh handles prevent stale collection UUID errors after
    # a knowledge-base rebuild.
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
        f"(main={main_count}, uploads={upload_count})",
        flush=True
    )

    if (
        main_count <= 0
        and upload_count <= 0
    ):
        return []

    # Biggest optimization: embed the user's question once instead
    # of once for the main collection and again for uploads.
    query_embedding = _create_query_embedding(
        query,
        main_collection,
        upload_collection,
        main_count,
        upload_count
    )

    combined = []

    def search_main():
        return _query_collection_object(
            main_collection,
            query,
            query_embedding,
            top_k,
            main_count
        )

    def search_uploads():
        return _query_collection_object(
            upload_collection,
            query,
            query_embedding,
            top_k,
            upload_count
        )

    # Once the embedding is ready, the two independent collection
    # searches can run at the same time.
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
            # Very conservative fallback for any local Chroma build
            # that does not like concurrent collection reads.
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

        deduplicated.append(item)

        if (
            len(deduplicated)
            >= final_results
        ):
            break

    total_elapsed = round(
        time.perf_counter() - total_started,
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
    # Always reacquire collection objects.
    # This avoids stale UUID errors after a rebuild.
    main_collection = get_main_collection()
    upload_collection = get_upload_collection()

    return {
        "main": main_collection.count(),
        "uploads": upload_collection.count()
    }
