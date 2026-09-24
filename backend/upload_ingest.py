import os
import re
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from pypdf import PdfReader
from docx import Document


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

UPLOAD_COLLECTION = "ip_sakti_uploads"

VECTOR_FOLDER.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# CHROMA
# =========================================================

client = chromadb.PersistentClient(
    path=str(VECTOR_FOLDER)
)

collection = client.get_or_create_collection(
    name=UPLOAD_COLLECTION
)


# =========================================================
# CHUNKING
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

    if current.strip():

        chunks.append(
            current.strip()
        )

    return chunks


# =========================================================
# EXTRACTION
# =========================================================

def extract_pdf(
    file_path
):

    reader = PdfReader(
        str(file_path)
    )

    pages = []

    for page_number, page in enumerate(
        reader.pages,
        start=1
    ):

        text = (
            page.extract_text()
            or ""
        ).strip()

        if text:

            pages.append(
                (
                    page_number,
                    text
                )
            )

    return pages


def extract_txt(
    file_path
):

    text = Path(
        file_path
    ).read_text(
        encoding="utf-8",
        errors="ignore"
    )

    return [
        (
            1,
            text
        )
    ]


def extract_docx(
    file_path
):

    document = Document(
        str(file_path)
    )

    paragraphs = [
        paragraph.text
        for paragraph in document.paragraphs
        if paragraph.text.strip()
    ]

    return [
        (
            1,
            "\n".join(
                paragraphs
            )
        )
    ]


# =========================================================
# INGEST UPLOADED FILE
# =========================================================

def ingest_uploaded_file(
    file_path
):

    try:

        file_path = Path(
            file_path
        )

        extension = (
            file_path.suffix
            .lower()
        )

        if extension == ".pdf":

            pages = extract_pdf(
                file_path
            )

        elif extension == ".txt":

            pages = extract_txt(
                file_path
            )

        elif extension == ".docx":

            pages = extract_docx(
                file_path
            )

        else:

            return {
                "success": False,
                "message":
                    "Unsupported file type. "
                    "Use PDF, TXT or DOCX.",
                "chunks": 0
            }

        if not pages:

            return {
                "success": False,
                "message":
                    "No readable text was found in the document.",
                "chunks": 0
            }

        total_chunks = 0

        for page_number, text in pages:

            chunks = chunk_text(
                text
            )

            for chunk_number, chunk in enumerate(
                chunks,
                start=1
            ):

                document_id = (
                    f"upload::{file_path.name}"
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
                            "source": file_path.name,
                            "source_path": str(file_path),
                            "category": "user_upload",
                            "page": page_number,
                            "chunk": chunk_number,
                            "uploaded": True
                        }
                    ]
                )

                total_chunks += 1

        if total_chunks == 0:

            return {
                "success": False,
                "message":
                    "The document did not contain indexable text.",
                "chunks": 0
            }

        return {
            "success": True,
            "message":
                "Document indexed successfully.",
            "chunks": total_chunks
        }

    except Exception as error:

        return {
            "success": False,
            "message":
                f"Document indexing failed: {error}",
            "chunks": 0
        }
