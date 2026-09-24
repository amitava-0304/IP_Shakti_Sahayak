import os
import re
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from pypdf import PdfReader


# =========================================================
# PATHS / ENVIRONMENT
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

DATASET_ROOT = PROJECT_ROOT / "data"

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


# =========================================================
# TEXT CHUNKING
# =========================================================

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

    sentences = split_into_sentences(
        text
    )

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

            chunks.append(
                current.strip()
            )

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

        chunks.append(
            current.strip()
        )

    return chunks


# =========================================================
# PDF INGESTION
# =========================================================

def find_pdf_files():

    if not DATASET_ROOT.exists():
        return []

    return sorted(
        DATASET_ROOT.rglob(
            "*.pdf"
        )
    )


def category_from_path(
    pdf_path
):

    try:

        relative = pdf_path.relative_to(
            DATASET_ROOT
        )

        if len(relative.parts) > 1:
            return relative.parts[0]

    except ValueError:
        pass

    return "general"


def index_pdf(
    pdf_path,
    collection
):

    reader = PdfReader(
        str(pdf_path)
    )

    category = category_from_path(
        pdf_path
    )

    total_chunks = 0

    relative_source_path = str(
        pdf_path.relative_to(
            PROJECT_ROOT
        )
    )

    for page_number, page in enumerate(
        reader.pages,
        start=1
    ):

        text = (
            page.extract_text()
            or ""
        ).strip()

        if not text:
            continue

        chunks = chunk_text(
            text
        )

        for chunk_number, chunk in enumerate(
            chunks,
            start=1
        ):

            document_id = (
                f"main::{relative_source_path}"
                f"::p{page_number}"
                f"::c{chunk_number}"
            )

            collection.upsert(
                ids=[
                    document_id
                ],
                documents=[
                    chunk
                ],
                metadatas=[
                    {
                        "source": pdf_path.name,
                        "source_path": relative_source_path,
                        "category": category,
                        "page": page_number,
                        "chunk": chunk_number,
                        "uploaded": False
                    }
                ]
            )

            total_chunks += 1

    return total_chunks


# =========================================================
# MAIN DATABASE BUILD
# =========================================================

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

        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=MAIN_COLLECTION
    )

    pdf_files = find_pdf_files()

    if not pdf_files:

        return {
            "success": False,
            "message":
                "No PDF files were found under the data folder.",
            "files": 0,
            "chunks": 0
        }

    total_chunks = 0

    indexed_files = 0

    for pdf_path in pdf_files:

        try:

            chunks = index_pdf(
                pdf_path,
                collection
            )

            total_chunks += chunks

            indexed_files += 1

            print(
                f"Indexed: {pdf_path.name} "
                f"({chunks} chunks)"
            )

        except Exception as error:

            print(
                f"Could not index {pdf_path}:",
                repr(error)
            )

    return {
        "success": True,
        "message":
            "Main knowledge base indexed successfully.",
        "files": indexed_files,
        "chunks": total_chunks
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

    if rebuild_on_start:

        print(
            "REBUILD_MAIN_ON_START is enabled. "
            "Rebuilding main knowledge base..."
        )

        return ingest_main_database(
            rebuild=True
        )

    if collection.count() > 0:

        return {
            "success": True,
            "message":
                "Main knowledge base already exists.",
            "files": None,
            "chunks": collection.count()
        }

    print(
        "Main knowledge base is empty. "
        "Building it from data/..."
    )

    return ingest_main_database(
        rebuild=False
    )


# =========================================================
# MANUAL EXECUTION
# =========================================================

if __name__ == "__main__":

    result = ingest_main_database(
        rebuild=True
    )

    print(
        result
    )
