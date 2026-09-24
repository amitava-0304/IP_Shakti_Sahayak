import os
import re
from pathlib import Path

import chromadb
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = PROJECT_ROOT / "data"

load_dotenv(PROJECT_ROOT / ".env")

STORAGE_ROOT = Path(
    os.getenv(
        "STORAGE_ROOT",
        str(PROJECT_ROOT)
    )
)

VECTOR_FOLDER = STORAGE_ROOT / "chroma_db"
MAIN_COLLECTION = "ip_sakti_main"

VECTOR_FOLDER.mkdir(
    parents=True,
    exist_ok=True
)

BATCH_SIZE = int(
    os.getenv(
        "CHROMA_BATCH_SIZE",
        "75"
    )
)

PAGE_MARKER_PATTERN = re.compile(
    r"^===== PAGE (\d+) =====\s*$",
    re.MULTILINE
)


def split_into_sentences(text):
    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    if not text:
        return []

    return re.split(
        r"(?<=[.!?])\s+",
        text
    )


def chunk_text(
    text,
    chunk_size=1200,
    overlap=200
):
    sentences = split_into_sentences(text)

    if not sentences:
        return []

    chunks = []
    current = ""

    for sentence in sentences:
        candidate = (
            sentence
            if not current
            else current + " " + sentence
        )

        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current.strip())

        if overlap > 0 and current:
            tail = current[
                max(
                    0,
                    len(current) - overlap
                ):
            ]

            current = (
                tail + " " + sentence
            ).strip()
        else:
            current = sentence

        while len(current) > chunk_size:
            chunks.append(
                current[:chunk_size]
            )

            current = current[
                max(
                    1,
                    chunk_size - overlap
                ):
            ]

    if current.strip():
        chunks.append(current.strip())

    return chunks


def parse_text_pages(text):
    matches = list(
        PAGE_MARKER_PATTERN.finditer(
            text
        )
    )

    if not matches:
        return [
            (
                1,
                text.strip()
            )
        ]

    pages = []

    for index, match in enumerate(matches):
        page_number = int(match.group(1))
        start = match.end()

        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(text)
        )

        page_text = text[start:end].strip()

        if page_text:
            pages.append(
                (
                    page_number,
                    page_text
                )
            )

    return pages


def find_text_files():
    if not DATASET_ROOT.exists():
        return []

    return sorted(
        DATASET_ROOT.rglob("*.txt")
    )


def category_from_path(text_file):
    try:
        relative = text_file.relative_to(
            DATASET_ROOT
        )

        if len(relative.parts) > 1:
            return relative.parts[0]
    except ValueError:
        pass

    return "general"


def upsert_batch(
    collection,
    ids,
    documents,
    metadatas
):
    if not ids:
        return

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas
    )


def index_text_file(
    text_file,
    collection
):
    category = category_from_path(
        text_file
    )

    raw_text = text_file.read_text(
        encoding="utf-8",
        errors="ignore"
    )

    pages = parse_text_pages(
        raw_text
    )

    if not pages:
        return 0

    relative_source_path = str(
        text_file.relative_to(
            PROJECT_ROOT
        )
    )

    original_source = (
        text_file.stem + ".pdf"
    )

    ids = []
    documents = []
    metadatas = []

    total_chunks = 0

    for page_number, page_text in pages:
        chunks = chunk_text(page_text)

        for chunk_number, chunk in enumerate(
            chunks,
            start=1
        ):
            document_id = (
                f"main::{relative_source_path}"
                f"::p{page_number}"
                f"::c{chunk_number}"
            )

            ids.append(document_id)
            documents.append(chunk)

            metadatas.append({
                "source": original_source,
                "source_path": relative_source_path,
                "category": category,
                "page": page_number,
                "chunk": chunk_number,
                "uploaded": False
            })

            total_chunks += 1

            if len(ids) >= BATCH_SIZE:
                upsert_batch(
                    collection,
                    ids,
                    documents,
                    metadatas
                )

                print(
                    f"  Added {total_chunks} chunks "
                    f"from {text_file.name}",
                    flush=True
                )

                ids = []
                documents = []
                metadatas = []

    upsert_batch(
        collection,
        ids,
        documents,
        metadatas
    )

    return total_chunks


def ingest_main_database(
    rebuild=True
):
    client = chromadb.PersistentClient(
        path=str(VECTOR_FOLDER)
    )

    if rebuild:
        try:
            client.delete_collection(
                name=MAIN_COLLECTION
            )

            print(
                "Deleted old main collection.",
                flush=True
            )
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=MAIN_COLLECTION
    )

    text_files = find_text_files()

    if not text_files:
        return {
            "success": False,
            "message":
                "No TXT knowledge files found under data/.",
            "files": 0,
            "chunks": 0
        }

    total_chunks = 0
    indexed_files = 0
    failed_files = 0

    print(
        f"Found {len(text_files)} TXT files.",
        flush=True
    )

    for number, text_file in enumerate(
        text_files,
        start=1
    ):
        try:
            print(
                f"[{number}/{len(text_files)}] "
                f"Indexing: {text_file}",
                flush=True
            )

            chunks = index_text_file(
                text_file,
                collection
            )

            if chunks > 0:
                indexed_files += 1
                total_chunks += chunks

                print(
                    f"Indexed: {text_file.name} "
                    f"({chunks} chunks)",
                    flush=True
                )
            else:
                print(
                    f"Skipped empty file: {text_file.name}",
                    flush=True
                )

        except Exception as error:
            failed_files += 1

            print(
                f"Could not index {text_file}: "
                f"{repr(error)}",
                flush=True
            )

    return {
        "success": True,
        "message":
            "Main text knowledge base indexing finished.",
        "files":
            indexed_files,
        "failed_files":
            failed_files,
        "chunks":
            total_chunks,
        "collection_count":
            collection.count()
    }


def ensure_main_database():
    client = chromadb.PersistentClient(
        path=str(VECTOR_FOLDER)
    )

    collection = client.get_or_create_collection(
        name=MAIN_COLLECTION
    )

    rebuild_on_start = (
        os.getenv(
            "REBUILD_MAIN_ON_START",
            "false"
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

    current_count = collection.count()

    print(
        f"Current main collection chunks: {current_count}",
        flush=True
    )

    if rebuild_on_start:
        print(
            "REBUILD_MAIN_ON_START=true. "
            "Rebuilding main knowledge base...",
            flush=True
        )

        return ingest_main_database(
            rebuild=True
        )

    if current_count > 0:
        return {
            "success": True,
            "message":
                "Main knowledge base already exists.",
            "files": None,
            "chunks":
                current_count
        }

    print(
        "Main knowledge base is empty. "
        "Indexing TXT files from data/...",
        flush=True
    )

    return ingest_main_database(
        rebuild=False
    )


if __name__ == "__main__":
    print(
        ingest_main_database(
            rebuild=True
        )
    )
