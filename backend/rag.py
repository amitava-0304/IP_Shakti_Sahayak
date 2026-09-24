import os
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


# =========================================================
# CHROMA CLIENT
# =========================================================

client = chromadb.PersistentClient(
    path=str(VECTOR_FOLDER)
)


# =========================================================
# ALWAYS GET A FRESH COLLECTION HANDLE
#
# Important:
# ingest.py can delete + recreate the main collection.
# A collection object created before that rebuild becomes stale
# and raises chromadb.errors.NotFoundError.
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
# QUERY ONE COLLECTION
# =========================================================

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

    result = collection.query(
        query_texts=[query],
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
# SEARCH BOTH COLLECTIONS
# =========================================================

def search_documents(
    query,
    top_k=8,
    final_results=5,
    distance_margin=0.45
):

    query = (
        query
        or ""
    ).strip()

    if not query:
        return []

    combined = []

    combined.extend(
        query_collection(
            MAIN_COLLECTION,
            query,
            top_k
        )
    )

    combined.extend(
        query_collection(
            UPLOAD_COLLECTION,
            query,
            top_k
        )
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
            +
            distance_margin
        )

        combined = [
            item
            for item in combined
            if (
                item.get("distance") is None
                or
                item.get("distance") <= max_distance
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

        seen.add(
            key
        )

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
