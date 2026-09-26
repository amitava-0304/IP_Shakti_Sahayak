import os
import re
import uuid
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from pypdf import PdfReader
from docx import Document
import fitz
import pytesseract
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / '.env')
STORAGE_ROOT = Path(os.getenv('STORAGE_ROOT', str(PROJECT_ROOT)))
VECTOR_FOLDER = STORAGE_ROOT / 'chroma_db'
UPLOADED_TEXT_FOLDER = STORAGE_ROOT / 'uploaded_text'
VECTOR_FOLDER.mkdir(parents=True, exist_ok=True)
UPLOADED_TEXT_FOLDER.mkdir(parents=True, exist_ok=True)
UPLOAD_COLLECTION = 'ip_sakti_uploads'
client = chromadb.PersistentClient(path=str(VECTOR_FOLDER))

def get_upload_collection():
    return client.get_or_create_collection(name=UPLOAD_COLLECTION)

OCR_LANGUAGES = os.getenv('OCR_LANGUAGES', 'eng+ben+hin')
OCR_DPI = int(os.getenv('OCR_DPI', '180'))
MIN_PAGE_TEXT = int(os.getenv('OCR_MIN_PAGE_TEXT', '30'))
CHUNK_SIZE = int(os.getenv('UPLOAD_CHUNK_SIZE', '1200'))
CHUNK_OVERLAP = int(os.getenv('UPLOAD_CHUNK_OVERLAP', '180'))

def clean_text(text):
    text = (text or '').replace('\x00', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    text = clean_text(text)
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks

def extract_pdf_pages_with_pypdf(file_path):
    reader = PdfReader(str(file_path))
    pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ''
        except Exception:
            text = ''
        pages.append({'page': page_number, 'text': clean_text(text), 'ocr': False})
    return pages

def ocr_pdf_page(pdf_document, page_index):
    page = pdf_document.load_page(page_index)
    pix = page.get_pixmap(dpi=OCR_DPI, alpha=False)
    mode = 'RGB' if pix.n >= 3 else 'L'
    image = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
    text = pytesseract.image_to_string(image, lang=OCR_LANGUAGES)
    return clean_text(text)

def extract_pdf_pages(file_path):
    pages = extract_pdf_pages_with_pypdf(file_path)
    if all(len(item['text']) >= MIN_PAGE_TEXT for item in pages):
        return pages, 0
    pdf_document = fitz.open(str(file_path))
    ocr_count = 0
    try:
        for item in pages:
            if len(item['text']) >= MIN_PAGE_TEXT:
                continue
            try:
                ocr_text = ocr_pdf_page(pdf_document, item['page'] - 1)
            except Exception as error:
                print(f"OCR failed on page {item['page']}: {error}", flush=True)
                ocr_text = ''
            if ocr_text:
                item['text'] = ocr_text
                item['ocr'] = True
                ocr_count += 1
    finally:
        pdf_document.close()
    return pages, ocr_count

def extract_txt(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
        return clean_text(file.read())

def extract_docx(file_path):
    document = Document(str(file_path))
    parts = []
    for paragraph in document.paragraphs:
        text = clean_text(paragraph.text)
        if text:
            parts.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [clean_text(cell.text) for cell in row.cells]
            line = ' | '.join(cell for cell in cells if cell)
            if line:
                parts.append(line)
    return clean_text('\n'.join(parts))

def save_extracted_text(source_filename, sections):
    output_path = UPLOADED_TEXT_FOLDER / (Path(source_filename).stem + '.txt')
    with open(output_path, 'w', encoding='utf-8') as file:
        for section in sections:
            page = section.get('page', 'Unknown')
            if page != 'Unknown':
                file.write(f'===== PAGE {page} =====\n')
            file.write(section.get('text', '').strip())
            file.write('\n\n')
    return output_path

def delete_previous_file_chunks(collection, filename):
    try:
        collection.delete(where={'source': filename})
    except Exception as error:
        print(f'Previous upload cleanup warning: {error}', flush=True)

def build_records(filename, sections):
    documents, metadatas, ids = [], [], []
    for section in sections:
        page = section.get('page', 'Unknown')
        ocr_used = bool(section.get('ocr', False))
        for chunk_number, chunk in enumerate(chunk_text(section.get('text', '')), start=1):
            documents.append(chunk)
            metadatas.append({
                'source': filename,
                'category': 'uploaded',
                'page': str(page),
                'uploaded': True,
                'ocr': ocr_used,
                'chunk': chunk_number
            })
            ids.append(str(uuid.uuid4()))
    return documents, metadatas, ids

def ingest_uploaded_file(file_path):
    file_path = Path(file_path)
    if not file_path.exists():
        return {'success': False, 'message': 'Uploaded file was not found.'}
    extension = file_path.suffix.lower()
    filename = file_path.name
    try:
        ocr_pages = 0
        if extension == '.pdf':
            sections, ocr_pages = extract_pdf_pages(file_path)
        elif extension == '.txt':
            sections = [{'page': 'Unknown', 'text': extract_txt(file_path), 'ocr': False}]
        elif extension == '.docx':
            sections = [{'page': 'Unknown', 'text': extract_docx(file_path), 'ocr': False}]
        else:
            return {'success': False, 'message': 'Unsupported file type. Please upload PDF, TXT or DOCX.'}
        sections = [item for item in sections if clean_text(item.get('text', ''))]
        if not sections:
            return {
                'success': False,
                'message': 'No extractable text was found. OCR was attempted but no readable text could be detected.'
            }
        save_extracted_text(filename, sections)
        documents, metadatas, ids = build_records(filename, sections)
        if not documents:
            return {'success': False, 'message': 'Text was extracted, but no searchable chunks could be created.'}
        collection = get_upload_collection()
        delete_previous_file_chunks(collection, filename)
        batch_size = int(os.getenv('CHROMA_BATCH_SIZE', '75'))
        for start in range(0, len(documents), batch_size):
            end = start + batch_size
            collection.add(
                documents=documents[start:end],
                metadatas=metadatas[start:end],
                ids=ids[start:end]
            )
        return {
            'success': True,
            'message': 'Document indexed successfully.',
            'chunks': len(documents),
            'ocr_pages': ocr_pages,
            'searchable': True
        }
    except Exception as error:
        return {'success': False, 'message': f'Document indexing failed: {error}'}
