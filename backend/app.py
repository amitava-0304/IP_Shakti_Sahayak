from contextlib import asynccontextmanager
import os
import shutil
import threading

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from groq import Groq

try:
    from .rag import search_documents, get_collection_counts
    from .upload_ingest import ingest_uploaded_file
    from .map_service import search_places
    from .ingest import ensure_main_database
except ImportError:
    from rag import search_documents, get_collection_counts
    from upload_ingest import ingest_uploaded_file
    from map_service import search_places
    from ingest import ensure_main_database

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

STORAGE_ROOT = os.getenv("STORAGE_ROOT", BASE_DIR)
UPLOAD_FOLDER = os.path.join(STORAGE_ROOT, "uploaded_files")
FRONTEND_FOLDER = os.path.join(BASE_DIR, "frontend")
INDEX_FILE = os.path.join(FRONTEND_FOLDER, "index.html")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


def start_knowledge_base_indexing():
    try:
        result = ensure_main_database()
        print("Knowledge base startup:", result, flush=True)
    except Exception as error:
        print("Knowledge base startup failed:", repr(error), flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    auto_ingest = (
        os.getenv("AUTO_INGEST_ON_START", "true")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )

    if auto_ingest:
        thread = threading.Thread(
            target=start_knowledge_base_indexing,
            daemon=True
        )
        thread.start()
        print("Knowledge base indexing started in background.", flush=True)

    yield


app = FastAPI(
    title="IP-SHAKTI Sahayak API",
    description="Multilingual RAG-based IP and Ayurveda assistant",
    version="2.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


class ChatRequest(BaseModel):
    question: str
    language: str = "English"


class CentreSearchRequest(BaseModel):
    location: str
    latitude: float | None = None
    longitude: float | None = None


@app.get("/")
def frontend_home():
    if os.path.exists(INDEX_FILE):
        return FileResponse(INDEX_FILE)

    return {"message": "IP-SHAKTI Sahayak API is running."}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/status")
def api_status():
    try:
        counts = get_collection_counts()
    except Exception as error:
        print("Status collection error:", repr(error), flush=True)
        counts = {"main": 0, "uploads": 0}

    return {
        "message": "IP-SHAKTI Sahayak API is running",
        "gemini_configured": bool(GEMINI_API_KEY),
        "groq_configured": bool(GROQ_API_KEY),
        "serpapi_configured": bool(SERPAPI_API_KEY),
        "main_chunks": counts.get("main", 0),
        "uploaded_chunks": counts.get("uploads", 0)
    }


def process_uploaded_document(
    file_path,
    filename
):
    """
    Convert uploaded PDF/TXT/DOCX to text and index it in
    ChromaDB without blocking the upload HTTP request.
    """

    try:

        print(
            f"Starting upload indexing: {filename}",
            flush=True
        )

        result = ingest_uploaded_file(
            file_path
        )

        print(
            f"Upload indexing finished: "
            f"{filename} -> {result}",
            flush=True
        )

    except Exception as error:

        print(
            f"Upload indexing failed: "
            f"{filename} -> {repr(error)}",
            flush=True
        )


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...)
):

    try:

        if not file.filename:

            return {
                "error":
                    "No file selected."
            }

        safe_filename = os.path.basename(
            file.filename
        )

        extension = os.path.splitext(
            safe_filename
        )[1].lower()

        if extension not in (
            ".pdf",
            ".txt",
            ".docx"
        ):

            return {
                "error":
                    "Unsupported file type. "
                    "Please upload PDF, TXT or DOCX."
            }

        file_path = os.path.join(
            UPLOAD_FOLDER,
            safe_filename
        )

        with open(
            file_path,
            "wb"
        ) as buffer:

            shutil.copyfileobj(
                file.file,
                buffer
            )

        thread = threading.Thread(
            target=process_uploaded_document,
            args=(
                file_path,
                safe_filename
            ),
            daemon=True
        )

        thread.start()

        return {
            "message":
                f"File '{safe_filename}' uploaded successfully. "
                "Document indexing has started in background.",
            "filename":
                safe_filename,
            "indexing":
                True,
            "searchable":
                False
        }

    except Exception as error:

        return {
            "error":
                f"Upload failed: {error}"
        }


@app.post("/api/ayurveda-centres")
def find_ayurveda_centres(request: CentreSearchRequest):
    location = (request.location or "").strip()

    if not location:
        return {
            "status": "error",
            "message": "Please enter a city, state or location.",
            "results": []
        }

    query = (
        "Ayurveda centre"
        if request.latitude is not None and request.longitude is not None
        else f"Ayurveda centres in {location}"
    )

    return search_places(
        query=query,
        latitude=request.latitude,
        longitude=request.longitude
    )


def generate_with_gemini(prompt):
    if not gemini_client:
        return {
            "success": False,
            "provider": "Gemini",
            "error": "Gemini API key is not configured."
        }

    try:
        response = gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )

        if response and response.text:
            return {
                "success": True,
                "provider": "Gemini",
                "answer": response.text.strip()
            }

        return {
            "success": False,
            "provider": "Gemini",
            "error": "Gemini returned an empty response."
        }

    except Exception as error:
        return {
            "success": False,
            "provider": "Gemini",
            "error": str(error)
        }


def generate_with_groq(prompt):
    if not groq_client:
        return {
            "success": False,
            "provider": "Groq",
            "error": "Groq API key is not configured."
        }

    try:
        response = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=1800
        )

        answer = response.choices[0].message.content

        if answer:
            return {
                "success": True,
                "provider": "Groq",
                "answer": answer.strip()
            }

        return {
            "success": False,
            "provider": "Groq",
            "error": "Groq returned an empty response."
        }

    except Exception as error:
        return {
            "success": False,
            "provider": "Groq",
            "error": str(error)
        }


@app.post("/api/search")
def search(request: ChatRequest):
    question = (request.question or "").strip()

    if not question:
        return {"error": "Question cannot be empty."}

    try:
        results = search_documents(
            question,
            top_k=8,
            final_results=5
        )
    except Exception as error:
        return {
            "question": question,
            "language": request.language,
            "error": f"Document search failed: {error}",
            "sources": []
        }

    if not results:
        return {
            "question": question,
            "language": request.language,
            "answer": "No relevant information was found in the knowledge base.",
            "provider": "None",
            "sources": []
        }

    context_parts = []
    sources = []

    for result in results:
        metadata = result.get("metadata") or {}
        text = result.get("text") or ""

        source = metadata.get("source", "Unknown")
        category = metadata.get("category", "general")
        page = metadata.get("page", "Unknown")
        chunk = metadata.get("chunk", "Unknown")

        context_parts.append(
            f"Document: {source}\n"
            f"Category: {category}\n"
            f"Page: {page}\n"
            f"Chunk: {chunk}\n"
            f"Content:\n{text}"
        )

        sources.append({
            "source": source,
            "category": category,
            "page": page,
            "chunk": chunk,
            "text": text,
            "uploaded": bool(metadata.get("uploaded", False)),
            "distance": result.get("distance"),
            "relevance_score": result.get("relevance_score")
        })

    context = "\n\n---\n\n".join(context_parts)
    language = request.language or "English"

    prompt = f"""
You are IP-SHAKTI Sahayak.

You are a multilingual educational assistant for
Intellectual Property, Ayurveda and Traditional Knowledge.

Answer the user's question using ONLY the retrieved
information supplied below.

LANGUAGE:
Answer completely in {language}.

STRICT RULES:
1. Use only information from the retrieved documents.
2. Do not add unsupported facts from your own knowledge.
3. Give a complete educational answer.
4. Use headings and bullet points when useful.
5. Use natural Unicode for English, Bengali and Hindi.
6. Use Markdown formatting naturally.
7. Do not invent laws, sections, dates, medical claims,
   patent requirements or regulatory requirements.
8. If the retrieved context is insufficient, clearly say so.

USER QUESTION:
{question}

RETRIEVED CONTEXT:
{context}
"""

    gemini_result = generate_with_gemini(prompt)

    if gemini_result.get("success"):
        final_result = gemini_result
    else:
        groq_result = generate_with_groq(prompt)

        if groq_result.get("success"):
            final_result = groq_result
        else:
            return {
                "question": question,
                "language": language,
                "error": "Both AI providers failed.",
                "gemini_error": gemini_result.get("error"),
                "groq_error": groq_result.get("error"),
                "sources": sources
            }

    return {
        "question": question,
        "language": language,
        "answer": final_result.get("answer", ""),
        "provider": final_result.get("provider", "Unknown"),
        "sources": sources
    }
