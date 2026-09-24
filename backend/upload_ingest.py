import os
import re
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from pypdf import PdfReader
from docx import Document

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", str(PROJECT_ROOT)))
VECTOR_FOLDER = STORAGE_ROOT / "chroma_db"
UPLOAD_TEXT_FOLDER = STORAGE_ROOT / "uploaded_text"
UPLOAD_COLLECTION = "ip_sakti_uploads"

VECTOR_FOLDER.mkdir(parents=True, exist_ok=True)
UPLOAD_TEXT_FOLDER.mkdir(parents=True, exist_ok=True)

client = chromadb.PersistentClient(path=str(VECTOR_FOLDER))


def get_upload_collection():
    return client.get_or_create_collection(name=UPLOAD_COLLECTION)


def split_into_sentences(text):
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return re.split(r"(?<=[.!?])\s+", text)


def chunk_text(text, chunk_size=1200, overlap=200):
    sentences = split_into_sentences(text)
    if not sentences:
        return []

    chunks = []
    current = ""

    for sentence in sentences:
        candidate = sentence if not current else current + " " + sentence

        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current.strip())

        if overlap > 0 and current:
            tail = current[max(0, len(current) - overlap):]
            current = (tail + " " + sentence).strip()
        else:
            current = sentence

    if current.strip():
        chunks.append(current.strip())

    return chunks


def extract_pdf_pages(file_path):
    reader = PdfReader(str(file_path))
    pages = []

    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((page_number, text))

    return pages


def extract_txt_pages(file_path):
    text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    return [(1, text)]


def extract_docx_pages(file_path):
    document = Document(str(file_path))
    text = "\n".join(
        paragraph.text
        for paragraph in document.paragraphs
        if paragraph.text.strip()
    )
    return [(1, text)]


def save_extracted_text(original_filename, pages):
    output_file = UPLOAD_TEXT_FOLDER / f"{Path(original_filename).stem}.txt"
    sections = [
        f"===== PAGE {page_number} =====\n{text.strip()}\n"
        for page_number, text in pages
    ]
    output_file.write_text("\n".join(sections), encoding="utf-8")
    return output_file


def ingest_uploaded_file(file_path):
    try:
        file_path = Path(file_path)
        extension = file_path.suffix.lower()

        if extension == ".pdf":
            pages = extract_pdf_pages(file_path)
        elif extension == ".txt":
            pages = extract_txt_pages(file_path)
        elif extension == ".docx":
            pages = extract_docx_pages(file_path)
        else:
            return {
                "success": False,
                "message": "Unsupported file type. Use PDF, TXT or DOCX.",
                "chunks": 0,
            }

        pages = [
            (page_number, text.strip())
            for page_number, text in pages
            if text and text.strip()
        ]

        if not pages:
            return {
                "success": False,
                "message": (
                    "No extractable text was found. "
                    "A scanned/image-only PDF requires OCR."
                ),
                "chunks": 0,
            }

        text_file = save_extracted_text(file_path.name, pages)
        collection = get_upload_collection()

        # If the same filename is uploaded again, replace its old chunks.
        try:
            collection.delete(where={"source": file_path.name})
        except Exception:
            pass

        total_chunks = 0

        for page_number, page_text in pages:
            chunks = chunk_text(page_text)

            for chunk_number, chunk in enumerate(chunks, start=1):
                document_id = (
                    f"upload::{file_path.name}"
                    f"::p{page_number}::c{chunk_number}"
                )

                collection.upsert(
                    ids=[document_id],
                    documents=[chunk],
                    metadatas=[
                        {
                            "source": file_path.name,
                            "source_path": str(text_file),
                            "category": "user_upload",
                            "page": page_number,
                            "chunk": chunk_number,
                            "uploaded": True,
                        }
                    ],
                )
                total_chunks += 1

        if total_chunks == 0:
            return {
                "success": False,
                "message": "No indexable text was generated.",
                "chunks": 0,
            }

        return {
            "success": True,
            "message": "Document converted to text and indexed successfully.",
            "chunks": total_chunks,
            "text_file": str(text_file),
        }

    except Exception as error:
        return {
            "success": False,
            "message": f"Document conversion/indexing failed: {error}",
            "chunks": 0,
        }
