import os
from pathlib import Path

import chromadb
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", str(PROJECT_ROOT)))
VECTOR_FOLDER = STORAGE_ROOT / "chroma_db"

MAIN_COLLECTION = "ip_sakti_main"
UPLOAD_COLLECTION = "ip_sakti_uploads"

VECTOR_FOLDER.mkdir(parents=True, exist_ok=True)
client = chromadb.PersistentClient(path=str(VECTOR_FOLDER))


def get_main_collection():
    return client.get_or_create_collection(name=MAIN_COLLECTION)


def get_upload_collection():
    return client.get_or_create_collection(name=UPLOAD_COLLECTION)


def query_collection(collection_name, query, top_k):
    collection = (
        get_main_collection()
        if collection_name == MAIN_COLLECTION
        else get_upload_collection()
    )

    count = collection.count()
    if count <= 0:
        return []

    result = collection.query(
        query_texts=[query],
        n_results=min(top_k, count),
        include=["documents", "metadatas", "distances"],
    )

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    rows = []
    for index, document in enumerate(documents):
        metadata = metadatas[index] if index < len(metadatas) else {}
        distance = distances[index] if index < len(distances) else None
        rows.append(
            {
                "text": document,
                "metadata": metadata or {},
                "distance": distance,
            }
        )
    return rows


def search_documents(query, top_k=8, final_results=5, distance_margin=0.45):
    query = (query or "").strip()
    if not query:
        return []

    combined = []
    combined.extend(query_collection(MAIN_COLLECTION, query, top_k))
    combined.extend(query_collection(UPLOAD_COLLECTION, query, top_k))

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
        max_distance = min(valid_distances) + distance_margin
        combined = [
            item
            for item in combined
            if item.get("distance") is None
            or item.get("distance") <= max_distance
        ]

    deduplicated = []
    seen = set()

    for item in combined:
        metadata = item.get("metadata") or {}
        text = item.get("text") or ""
        key = (metadata.get("source"), metadata.get("page"), text[:250])

        if key in seen:
            continue
        seen.add(key)

        distance = item.get("distance")
        item["relevance_score"] = (
            1 / (1 + distance) if distance is not None else None
        )
        deduplicated.append(item)

        if len(deduplicated) >= final_results:
            break

    return deduplicated


def get_collection_counts():
    # Fresh handles avoid stale collection UUID errors after rebuilds.
    return {
        "main": get_main_collection().count(),
        "uploads": get_upload_collection().count(),
    }
