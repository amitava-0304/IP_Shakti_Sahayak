import gc
import os
import re
import uuid
from pathlib import Path

import chromadb
import fitz
import pytesseract
from PIL import Image
from docx import Document
from dotenv import load_dotenv
from pypdf import PdfReader

try:
    from .job_store import get_job, update_job
except ImportError:
    from job_store import get_job, update_job


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

STORAGE_ROOT = Path(
    os.getenv(
        "STORAGE_ROOT",
        str(PROJECT_ROOT)
    )
)

VECTOR_FOLDER = STORAGE_ROOT / "chroma_db"
UPLOADED_TEXT_FOLDER = STORAGE_ROOT / "uploaded_text"

VECTOR_FOLDER.mkdir(parents=True, exist_ok=True)
UPLOADED_TEXT_FOLDER.mkdir(parents=True, exist_ok=True)

UPLOAD_COLLECTION = "ip_sakti_uploads"

client = chromadb.PersistentClient(
    path=str(VECTOR_FOLDER)
)


def get_upload_collection():
    return client.get_or_create_collection(
        name=UPLOAD_COLLECTION
    )


OCR_LANGUAGES = os.getenv(
    "OCR_LANGUAGES",
    "eng"
)

OCR_DPI = int(
    os.getenv(
        "OCR_DPI",
        "90"
    )
)

OCR_MIN_PAGE_TEXT = int(
    os.getenv(
        "OCR_MIN_PAGE_TEXT",
        "20"
    )
)

UPLOAD_CHUNK_SIZE = int(
    os.getenv(
        "UPLOAD_CHUNK_SIZE",
        "700"
    )
)

UPLOAD_CHUNK_OVERLAP = int(
    os.getenv(
        "UPLOAD_CHUNK_OVERLAP",
        "80"
    )
)

CHROMA_BATCH_SIZE = int(
    os.getenv(
        "CHROMA_BATCH_SIZE",
        "10"
    )
)


def clean_text(text):
    text = text or ""
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(
    text,
    chunk_size=UPLOAD_CHUNK_SIZE,
    overlap=UPLOAD_CHUNK_OVERLAP
):
    text = clean_text(text)

    if not text:
        return []

    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(
            start + chunk_size,
            text_length
        )

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= text_length:
            break

        start = max(
            end - overlap,
            start + 1
        )

    return chunks


def get_output_text_path(filename):
    return (
        UPLOADED_TEXT_FOLDER
        / (
            Path(filename).stem
            + ".txt"
        )
    )


def append_page_text(
    output_path,
    page_number,
    text
):
    with open(
        output_path,
        "a",
        encoding="utf-8"
    ) as file:
        file.write(
            f"===== PAGE {page_number} =====\n"
        )
        file.write(
            clean_text(text)
        )
        file.write(
            "\n\n"
        )


def write_single_text(
    output_path,
    text
):
    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as file:
        file.write(
            clean_text(text)
        )


def delete_previous_file_chunks(
    collection,
    filename
):
    try:
        collection.delete(
            where={
                "source": filename
            }
        )
    except Exception as error:
        print(
            f"Previous upload cleanup warning: {error}",
            flush=True
        )


def build_chunk_id(
    job_id,
    page_number,
    chunk_number
):
    return (
        f"{job_id}:"
        f"{page_number}:"
        f"{chunk_number}"
    )


def index_page_chunks(
    collection,
    job_id,
    filename,
    page_number,
    text,
    ocr_used
):
    chunks = chunk_text(text)

    total_added = 0

    for start in range(
        0,
        len(chunks),
        CHROMA_BATCH_SIZE
    ):
        batch = chunks[
            start:
            start + CHROMA_BATCH_SIZE
        ]

        documents = []
        metadatas = []
        ids = []

        for offset, chunk in enumerate(
            batch,
            start=start + 1
        ):
            documents.append(chunk)

            metadatas.append(
                {
                    "source": filename,
                    "category": "uploaded",
                    "page": str(page_number),
                    "uploaded": True,
                    "ocr": bool(ocr_used),
                    "chunk": offset,
                    "job_id": job_id
                }
            )

            ids.append(
                build_chunk_id(
                    job_id,
                    page_number,
                    offset
                )
            )

        # upsert makes restart/resume safe.
        collection.upsert(
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )

        total_added += len(batch)

        del documents
        del metadatas
        del ids
        gc.collect()

    del chunks
    gc.collect()

    return total_added


def ocr_pdf_page(
    pdf_document,
    page_index
):
    page = None
    pix = None
    image = None

    try:
        page = pdf_document.load_page(
            page_index
        )

        pix = page.get_pixmap(
            dpi=OCR_DPI,
            alpha=False
        )

        image = Image.frombytes(
            "RGB",
            [
                pix.width,
                pix.height
            ],
            pix.samples
        )

        text = pytesseract.image_to_string(
            image,
            lang=OCR_LANGUAGES,
            config="--psm 6"
        )

        return clean_text(text)

    finally:
        if image is not None:
            image.close()

        del image
        del pix
        del page
        gc.collect()


def extract_docx_text(file_path):
    document = Document(
        str(file_path)
    )

    parts = []

    for paragraph in document.paragraphs:
        text = clean_text(
            paragraph.text
        )
        if text:
            parts.append(text)

    for table in document.tables:
        for row in table.rows:
            cells = [
                clean_text(cell.text)
                for cell in row.cells
            ]

            line = " | ".join(
                cell
                for cell in cells
                if cell
            )

            if line:
                parts.append(line)

    return clean_text(
        "\n".join(parts)
    )


def ingest_text_or_docx(
    file_path,
    job_id
):
    filename = file_path.name
    extension = file_path.suffix.lower()

    update_job(
        job_id,
        stage="extracting",
        current_page=0,
        total_pages=1,
        message="Extracting document text."
    )

    if extension == ".txt":
        with open(
            file_path,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as file:
            text = clean_text(
                file.read()
            )
    else:
        text = extract_docx_text(
            file_path
        )

    if not text:
        return {
            "success": False,
            "message":
                "No readable text was found."
        }

    output_path = get_output_text_path(
        filename
    )

    write_single_text(
        output_path,
        text
    )

    collection = get_upload_collection()

    delete_previous_file_chunks(
        collection,
        filename
    )

    chunks = index_page_chunks(
        collection,
        job_id,
        filename,
        1,
        text,
        False
    )

    update_job(
        job_id,
        stage="indexing",
        current_page=1,
        total_pages=1,
        chunks=chunks,
        message="Document text indexed."
    )

    return {
        "success": True,
        "chunks": chunks,
        "ocr_pages": 0,
        "total_pages": 1,
        "searchable": True
    }


def ingest_pdf_streaming(
    file_path,
    job_id
):
    """
    True page-streaming PDF pipeline.

    It does NOT store the text of the entire PDF in RAM.
    Each page is:
      extract -> optional OCR -> append to TXT ->
      chunk -> Chroma upsert -> release memory.

    current_page is saved after every page, so after a service
    restart the worker resumes from the next page.
    """
    filename = file_path.name

    reader = PdfReader(
        str(file_path)
    )

    total_pages = len(
        reader.pages
    )

    if total_pages <= 0:
        return {
            "success": False,
            "message": "PDF contains no pages."
        }

    job = get_job(job_id) or {}

    last_completed_page = int(
        job.get(
            "current_page",
            0
        )
        or 0
    )

    total_chunks = int(
        job.get(
            "chunks",
            0
        )
        or 0
    )

    output_path = get_output_text_path(
        filename
    )

    collection = get_upload_collection()

    # New job: clear previous same-filename chunks and extracted text.
    if last_completed_page <= 0:
        delete_previous_file_chunks(
            collection,
            filename
        )

        with open(
            output_path,
            "w",
            encoding="utf-8"
        ) as file:
            file.write("")

    update_job(
        job_id,
        status="processing",
        stage="extracting",
        total_pages=total_pages,
        message=(
            f"Processing page "
            f"{last_completed_page + 1} "
            f"of {total_pages}."
        )
    )

    pdf_document = fitz.open(
        str(file_path)
    )

    ocr_pages = 0

    try:
        for page_index in range(
            last_completed_page,
            total_pages
        ):
            page_number = page_index + 1

            text = ""
            used_ocr = False

            try:
                text = (
                    reader.pages[
                        page_index
                    ].extract_text()
                    or ""
                )

                text = clean_text(
                    text
                )

            except Exception as error:
                print(
                    f"pypdf failed on page "
                    f"{page_number}: {error}",
                    flush=True
                )
                text = ""

            if (
                len(text)
                < OCR_MIN_PAGE_TEXT
            ):
                update_job(
                    job_id,
                    status="ocr",
                    stage="ocr",
                    current_page=page_number - 1,
                    total_pages=total_pages,
                    chunks=total_chunks,
                    message=(
                        f"OCR processing page "
                        f"{page_number} of "
                        f"{total_pages}."
                    )
                )

                try:
                    text = ocr_pdf_page(
                        pdf_document,
                        page_index
                    )

                    if text:
                        used_ocr = True
                        ocr_pages += 1

                except Exception as error:
                    print(
                        f"OCR failed on page "
                        f"{page_number}: {error}",
                        flush=True
                    )
                    text = ""

            if text:
                append_page_text(
                    output_path,
                    page_number,
                    text
                )

                update_job(
                    job_id,
                    status="processing",
                    stage="indexing",
                    current_page=page_number - 1,
                    total_pages=total_pages,
                    chunks=total_chunks,
                    message=(
                        f"Indexing page "
                        f"{page_number} of "
                        f"{total_pages}."
                    )
                )

                added = index_page_chunks(
                    collection,
                    job_id,
                    filename,
                    page_number,
                    text,
                    used_ocr
                )

                total_chunks += added

            # Commit page progress only after its extraction/indexing is done.
            update_job(
                job_id,
                status="processing",
                stage="processing",
                current_page=page_number,
                total_pages=total_pages,
                chunks=total_chunks,
                message=(
                    f"Completed page "
                    f"{page_number} of "
                    f"{total_pages}."
                )
            )

            del text
            gc.collect()

    finally:
        pdf_document.close()

        del pdf_document
        del reader
        gc.collect()

    if total_chunks <= 0:
        return {
            "success": False,
            "message":
                "No readable text was found. "
                "OCR was attempted but no searchable "
                "content could be created."
        }

    return {
        "success": True,
        "chunks": total_chunks,
        "ocr_pages": ocr_pages,
        "total_pages": total_pages,
        "searchable": True
    }


def ingest_uploaded_file(
    file_path,
    job_id
):
    file_path = Path(
        file_path
    )

    if not file_path.exists():
        return {
            "success": False,
            "message":
                "Uploaded file was not found."
        }

    extension = (
        file_path.suffix.lower()
    )

    try:
        if extension == ".pdf":
            return ingest_pdf_streaming(
                file_path,
                job_id
            )

        if extension in (
            ".txt",
            ".docx"
        ):
            return ingest_text_or_docx(
                file_path,
                job_id
            )

        return {
            "success": False,
            "message":
                "Unsupported file type. "
                "Please upload PDF, TXT or DOCX."
        }

    except Exception as error:
        gc.collect()

        return {
            "success": False,
            "message":
                "Document indexing failed: "
                + str(error)
        }
