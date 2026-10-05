from contextlib import asynccontextmanager



import json

import os

import shutil

import threading

import time

import uuid



from fastapi import FastAPI, UploadFile, File

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import FileResponse, StreamingResponse

from pydantic import BaseModel

from dotenv import load_dotenv

from google import genai

from groq import Groq



try:
    from .agent_router import router as agent_router
except ImportError:
    from agent_router import router as agent_router


try:

    from .rag import (

        search_documents,

        get_collection_counts,

        create_query_embedding

    )

    from .map_service import search_places

    from .ingest import ensure_main_database

    from .job_store import (

        init_job_database,

        create_job,

        get_job

    )

    from .worker import document_worker_loop

    from .semantic_cache import (

        get_exact_cache,

        get_semantic_cache,

        save_cache_entry,

        clear_semantic_cache,

        get_cache_stats

    )

except ImportError:

    from rag import (

        search_documents,

        get_collection_counts,

        create_query_embedding

    )

    from map_service import search_places

    from ingest import ensure_main_database

    from job_store import (

        init_job_database,

        create_job,

        get_job

    )

    from worker import document_worker_loop

    from semantic_cache import (

        get_exact_cache,

        get_semantic_cache,

        save_cache_entry,

        clear_semantic_cache,

        get_cache_stats

    )





BASE_DIR = os.path.dirname(

    os.path.dirname(

        os.path.abspath(__file__)

    )

)



load_dotenv(

    os.path.join(

        BASE_DIR,

        ".env"

    )

)



STORAGE_ROOT = os.getenv(

    "STORAGE_ROOT",

    BASE_DIR

)



UPLOAD_FOLDER = os.path.join(

    STORAGE_ROOT,

    "uploaded_files"

)



FRONTEND_FOLDER = os.path.join(

    BASE_DIR,

    "frontend"

)



INDEX_FILE = os.path.join(

    FRONTEND_FOLDER,

    "index.html"

)



os.makedirs(

    UPLOAD_FOLDER,

    exist_ok=True

)



GEMINI_API_KEY = os.getenv(

    "GEMINI_API_KEY"

)



GROQ_API_KEY = os.getenv(

    "GROQ_API_KEY"

)



SERPAPI_API_KEY = os.getenv(

    "SERPAPI_API_KEY"

)



gemini_client = (

    genai.Client(

        api_key=GEMINI_API_KEY

    )

    if GEMINI_API_KEY

    else None

)



groq_client = (

    Groq(

        api_key=GROQ_API_KEY

    )

    if GROQ_API_KEY

    else None

)





def start_knowledge_base_indexing():

    try:

        result = ensure_main_database()



        # Permanent knowledge-base changes can make cached answers stale.

        clear_semantic_cache()



        print(

            "Knowledge base startup:",

            result,

            flush=True

        )



    except Exception as error:

        print(

            "Knowledge base startup failed:",

            repr(error),

            flush=True

        )





_worker_started = False





def start_document_worker_once():

    global _worker_started



    if _worker_started:

        return



    _worker_started = True



    thread = threading.Thread(

        target=document_worker_loop,

        daemon=True,

        name="document-worker"

    )



    thread.start()



    print(

        "Persistent document worker "

        "started in background.",

        flush=True

    )





@asynccontextmanager

async def lifespan(app: FastAPI):

    init_job_database()



    start_document_worker_once()



    auto_ingest = (

        os.getenv(

            "AUTO_INGEST_ON_START",

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



    if auto_ingest:

        thread = threading.Thread(

            target=start_knowledge_base_indexing,

            daemon=True

        )



        thread.start()



        print(

            "Knowledge base indexing "

            "started in background.",

            flush=True

        )



    yield





app = FastAPI(

    title="AyurSetu AI API",

    description=(

        "Multilingual RAG-based IP "

        "and Ayurveda assistant"

    ),

    version="3.0.0",

    lifespan=lifespan

)



app.add_middleware(

    CORSMiddleware,

    allow_origins=["*"],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"]

)


# Controlled Agentic AI endpoints:
# GET  /api/agent-guide
# POST /api/agent-search
app.include_router(
    agent_router
)





class ChatRequest(BaseModel):

    question: str

    language: str = "English"





class CentreSearchRequest(BaseModel):

    location: str

    latitude: float | None = None

    longitude: float | None = None


def normalize_language(language):
    value = (language or "English").strip().lower()

    language_map = {
        "english": "English",
        "en": "English",
        "bengali": "Bengali",
        "bangla": "Bengali",
        "বাংলা": "Bengali",
        "bn": "Bengali",
        "hindi": "Hindi",
        "हिन्दी": "Hindi",
        "हिंदी": "Hindi",
        "hi": "Hindi",
    }

    return language_map.get(value, "English")


def get_language_instruction(language):
    language = normalize_language(language)

    if language == "Bengali":
        return (
            "IMPORTANT: Write the complete final answer in Bengali using Bengali script. "
            "Translate the retrieved source information into natural Bengali. "
            "Do not write explanatory sentences in English. "
            "English is allowed only for unavoidable proper names, acronyms, "
            "official document titles, section numbers, or technical terms."
        )

    if language == "Hindi":
        return (
            "IMPORTANT: Write the complete final answer in Hindi using Devanagari script. "
            "Translate the retrieved source information into natural Hindi. "
            "Do not write explanatory sentences in English. "
            "English is allowed only for unavoidable proper names, acronyms, "
            "official document titles, section numbers, or technical terms."
        )

    return "Write the complete final answer in English."


def answer_matches_language(answer, language):
    if not answer:
        return False

    language = normalize_language(language)

    if language == "Bengali":
        bengali_chars = sum(
            1 for ch in answer
            if "\u0980" <= ch <= "\u09FF"
        )
        letters = sum(1 for ch in answer if ch.isalpha())
        return bengali_chars >= 20 and (
            letters == 0 or bengali_chars / letters >= 0.35
        )

    if language == "Hindi":
        devanagari_chars = sum(
            1 for ch in answer
            if "\u0900" <= ch <= "\u097F"
        )
        letters = sum(1 for ch in answer if ch.isalpha())
        return devanagari_chars >= 20 and (
            letters == 0 or devanagari_chars / letters >= 0.35
        )

    return True





@app.get("/")

def frontend_home():

    if os.path.exists(

        INDEX_FILE

    ):

        return FileResponse(

            INDEX_FILE

        )



    return {

        "message":

            "AyurSetu AI "

            "API is running."

    }





@app.get("/health")

def health():

    return {

        "status": "ok"

    }





@app.get("/api/status")

def api_status():

    try:

        counts = (

            get_collection_counts()

        )

    except Exception as error:

        print(

            "Status collection error:",

            repr(error),

            flush=True

        )



        counts = {

            "main": 0,

            "uploads": 0

        }



    cache_stats = get_cache_stats()



    return {

        "message":

            "AyurSetu AI API is running",

        "semantic_cache_entries":

            cache_stats.get("entries", 0),

        "semantic_cache_hits":

            cache_stats.get("hits", 0),

        "gemini_configured":

            bool(GEMINI_API_KEY),

        "groq_configured":

            bool(GROQ_API_KEY),

        "serpapi_configured":

            bool(SERPAPI_API_KEY),

        "main_chunks":

            counts.get(

                "main",

                0

            ),

        "uploaded_chunks":

            counts.get(

                "uploads",

                0

            )

    }





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



        safe_filename = (

            os.path.basename(

                file.filename

            )

        )



        extension = (

            os.path.splitext(

                safe_filename

            )[1]

            .lower()

        )



        if extension not in (

            ".pdf",

            ".txt",

            ".docx"

        ):

            return {

                "error":

                    "Unsupported file type. "

                    "Please upload PDF, "

                    "TXT or DOCX."

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



        job_id = str(

            uuid.uuid4()

        )



        create_job(

            job_id,

            safe_filename,

            file_path

        )



        return {

            "message":

                f"File '{safe_filename}' "

                "uploaded successfully. "

                "Document indexing has "

                "been queued.",

            "filename":

                safe_filename,

            "job_id":

                job_id,

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





@app.get(

    "/api/upload-status/{job_id}"

)

def upload_status(

    job_id: str

):

    job = get_job(

        job_id

    )



    if not job:

        return {

            "status":

                "unknown",

            "error":

                "Upload job was not found."

        }



    end_time = (

        job.get(

            "finished_at"

        )

        or time.time()

    )



    elapsed = int(

        end_time

        - job["started_at"]

    )



    total_pages = int(

        job.get(

            "total_pages",

            0

        )

        or 0

    )



    current_page = int(

        job.get(

            "current_page",

            0

        )

        or 0

    )



    progress_percent = (

        round(

            (

                current_page

                / total_pages

            )

            * 100,

            1

        )

        if total_pages > 0

        else 0.0

    )



    return {

        "job_id":

            job_id,

        "filename":

            job.get(

                "filename"

            ),

        "status":

            job.get(

                "status"

            ),

        "stage":

            job.get(

                "stage"

            ),

        "elapsed_seconds":

            elapsed,

        "current_page":

            current_page,

        "total_pages":

            total_pages,

        "progress_percent":

            progress_percent,

        "chunks":

            int(

                job.get(

                    "chunks",

                    0

                )

                or 0

            ),

        "searchable":

            bool(

                job.get(

                    "searchable",

                    0

                )

            ),

        "message":

            job.get(

                "message",

                ""

            ),

        "error":

            job.get(

                "error",

                ""

            )

    }





@app.post(

    "/api/ayurveda-centres"

)

def find_ayurveda_centres(

    request: CentreSearchRequest

):

    location = (

        request.location

        or ""

    ).strip()



    if not location:

        return {

            "status":

                "error",

            "message":

                "Please enter a city, "

                "state or location.",

            "results":

                []

        }



    query = (

        "Ayurveda centre"

        if (

            request.latitude

            is not None

            and request.longitude

            is not None

        )

        else (

            f"Ayurveda centres "

            f"in {location}"

        )

    )



    return search_places(

        query=query,

        latitude=request.latitude,

        longitude=request.longitude

    )





def generate_with_gemini(

    prompt

):

    if not gemini_client:

        return {

            "success":

                False,

            "provider":

                "Gemini",

            "error":

                "Gemini API key "

                "is not configured."

        }



    try:

        response = (

            gemini_client.models

            .generate_content(

                model=

                    "gemini-3.6-flash",

                contents=

                    prompt

            )

        )



        if (

            response

            and response.text

        ):

            return {

                "success":

                    True,

                "provider":

                    "Gemini",

                "answer":

                    response.text.strip()

            }



        return {

            "success":

                False,

            "provider":

                "Gemini",

            "error":

                "Gemini returned "

                "an empty response."

        }



    except Exception as error:

        return {

            "success":

                False,

            "provider":

                "Gemini",

            "error":

                str(error)

        }





def generate_with_groq(

    prompt

):

    if not groq_client:

        return {

            "success":

                False,

            "provider":

                "Groq",

            "error":

                "Groq API key "

                "is not configured."

        }



    try:

        response = (

            groq_client.chat

            .completions

            .create(

                model=

                    "openai/gpt-oss-120b",

                messages=[

                    {

                        "role":

                            "user",

                        "content":

                            prompt

                    }

                ],

                max_completion_tokens=

                    1400

            )

        )



        answer = (

            response

            .choices[0]

            .message

            .content

        )



        if answer:

            return {

                "success":

                    True,

                "provider":

                    "Groq",

                "answer":

                    answer.strip()

            }



        return {

            "success":

                False,

            "provider":

                "Groq",

            "error":

                "Groq returned "

                "an empty response."

        }



    except Exception as error:

        return {

            "success":

                False,

            "provider":

                "Groq",

            "error":

                str(error)

        }





def retrieve_search_results(

    question,

    query_embedding=None,

    top_k=6,

    final_results=4

):

    """Retrieve a small, high-quality result set."""



    started = time.perf_counter()



    results = search_documents(

        question,

        top_k=top_k,

        final_results=final_results,

        query_embedding=query_embedding

    )



    elapsed = round(

        time.perf_counter() - started,

        3

    )



    print(

        f"Document search time: "

        f"{elapsed} seconds",

        flush=True

    )



    return results, elapsed





def build_rag_prompt(

    question,

    language,

    results

):

    context_parts = []

    sources = []



    for result in results:

        metadata = (

            result.get("metadata")

            or {}

        )



        text = (

            result.get("text")

            or ""

        )



        # Your current upload chunks are about 700 chars.

        # This safety cap prevents unusually large permanent

        # knowledge-base chunks from making the AI prompt huge.

        prompt_text = text[:1200]



        source = metadata.get(

            "source",

            "Unknown"

        )



        category = metadata.get(

            "category",

            "general"

        )



        page = metadata.get(

            "page",

            "Unknown"

        )



        chunk = metadata.get(

            "chunk",

            "Unknown"

        )



        context_parts.append(

            f"Document: {source}\n"

            f"Category: {category}\n"

            f"Page: {page}\n"

            f"Chunk: {chunk}\n"

            f"Content:\n{prompt_text}"

        )



        # Keep the complete retrieved chunk for the UI source card.

        sources.append(

            {

                "source": source,

                "category": category,

                "page": page,

                "chunk": chunk,

                "text": text,

                "uploaded": bool(

                    metadata.get(

                        "uploaded",

                        False

                    )

                ),

                "distance": result.get(

                    "distance"

                ),

                "relevance_score": result.get(

                    "relevance_score"

                )

            }

        )



    context = (

        "\n\n---\n\n"

        .join(context_parts)

    )



    language = normalize_language(language)

    language_instruction = get_language_instruction(
        language
    )

    prompt = f"""

You are AyurSetu AI.



You are a multilingual educational assistant for

Intellectual Property, Ayurveda and Traditional Knowledge.



Answer the user's question using ONLY the retrieved

information supplied below.



OUTPUT LANGUAGE:

Selected language: {language}

{language_instruction}

The retrieved context may be in English or another language.
Translate ONLY the retrieved information into the selected output
language without changing its factual meaning.



STRICT RULES:

1\. Use only information from the retrieved documents.



2\. Do not add unsupported facts from your own knowledge.



3\. Give a detailed, comprehensive and educational answer.



4\. Prefer paragraph-based explanation over a list-only answer.



5\. When the retrieved information is sufficient, write approximately

   4 to 7 meaningful paragraphs. Each paragraph should explain a

   different aspect of the topic.



6\. Begin with a clear definition or introduction.



7\. Then explain the relevant aspects supported by the retrieved

   documents, such as:

   - purpose or objective

   - background

   - important features

   - requirements or eligibility

   - procedure or process

   - scope

   - rights or effects

   - advantages

   - limitations

   - important provisions

   Include only the aspects actually supported by the retrieved context.



8\. Use headings and subheadings to organize longer answers.



9\. Bullet points may be used for key points, features, requirements,

   advantages or steps, but do not present the entire answer only as

   bullet points when enough material exists for paragraph explanation.



10\. After an important bullet list, add a short explanatory paragraph

    when the retrieved context supports it.



11\. For short definition questions, still give a useful explanation

    rather than only one sentence.



12\. Do not artificially make an answer long by repeating the same

    information. Prefer useful detail over repetition.



13\. Use natural Unicode for English, Bengali and Hindi.



14\. Use Markdown formatting naturally.



15\. Do not invent laws, sections, treaty dates, legal requirements,

    medical claims, patent requirements or regulatory requirements.



16\. If the retrieved context is insufficient for a detailed answer,

    clearly state that limitation instead of adding outside knowledge.



17\. Add the sentence

    "*This explanation is for educational purposes and is not legal advice.*"

    only when the question asks about a specific legal decision,

    filing strategy, infringement, legal eligibility, legal risk,

    or what the user should legally do. Do not automatically add it

    to simple educational definition questions.



USER QUESTION:

{question}



RETRIEVED CONTEXT:

{context}

"""



    return prompt, sources





def generate_rag_answer(
    prompt,
    language="English"
):
    started = time.perf_counter()

    language = normalize_language(language)

    gemini_result = generate_with_gemini(prompt)

    if (
        gemini_result.get("success")
        and not answer_matches_language(
            gemini_result.get("answer", ""),
            language
        )
    ):
        retry_prompt = (
            prompt
            + "\n\nFINAL LANGUAGE CHECK:\n"
            + get_language_instruction(language)
            + "\nRewrite the complete answer now in the selected language. "
              "Do not add any new facts."
        )

        retry_result = generate_with_gemini(retry_prompt)

        if (
            retry_result.get("success")
            and answer_matches_language(
                retry_result.get("answer", ""),
                language
            )
        ):
            gemini_result = retry_result

    gemini_elapsed = round(
        time.perf_counter() - started,
        3
    )

    print(
        f"Gemini response time: {gemini_elapsed} seconds",
        flush=True
    )

    if (
        gemini_result.get("success")
        and answer_matches_language(
            gemini_result.get("answer", ""),
            language
        )
    ):
        return (
            gemini_result,
            gemini_elapsed,
            None
        )

    groq_started = time.perf_counter()

    groq_prompt = (
        prompt
        + "\n\nFINAL LANGUAGE CHECK:\n"
        + get_language_instruction(language)
    )

    groq_result = generate_with_groq(groq_prompt)

    groq_elapsed = round(
        time.perf_counter() - groq_started,
        3
    )

    print(
        f"Groq response time: {groq_elapsed} seconds",
        flush=True
    )

    if (
        groq_result.get("success")
        and answer_matches_language(
            groq_result.get("answer", ""),
            language
        )
    ):
        return (
            groq_result,
            groq_elapsed,
            gemini_result.get("error")
        )

    return (
        {
            "success": False,
            "provider": "None",
            "error": (
                "Both AI providers failed to generate "
                f"a valid {language} answer."
            ),
            "gemini_error": gemini_result.get("error"),
            "groq_error": groq_result.get("error")
        },
        groq_elapsed,
        gemini_result.get("error")
    )

def _cached_response(

    cache_result,

    question,

    language,

    started

):

    response = dict(

        cache_result["response"]

    )



    # Preserve the new question text for semantically similar queries.

    response["question"] = question

    response["language"] = language

    response["provider"] = "Cache"

    response["cache_hit"] = True

    response["cache_type"] = cache_result["type"]

    response["cache_similarity"] = cache_result["similarity"]



    total_seconds = round(

        time.perf_counter() - started,

        3

    )



    response["timing"] = {

        "search_seconds": total_seconds,

        "ai_seconds": 0,

        "total_seconds": total_seconds

    }



    print(

        f"RAG cache hit: "

        f"{cache_result['type']} "

        f"similarity={cache_result['similarity']} "

        f"time={total_seconds}s",

        flush=True

    )



    return response





def build_search_response(

    question,

    language

):

    request_started = time.perf_counter()



    # -----------------------------------------------------

    # 1. Persistent exact cache lookup

    # -----------------------------------------------------

    # This requires no embedding, no Chroma query and no AI request.

    exact_cache = get_exact_cache(

        question,

        language

    )



    if exact_cache:

        return _cached_response(

            exact_cache,

            question,

            language,

            request_started

        )



    # -----------------------------------------------------

    # 2. Compute the query embedding once

    # -----------------------------------------------------

    embedding_started = time.perf_counter()



    query_embedding = create_query_embedding(

        question

    )



    embedding_seconds = round(

        time.perf_counter()

        - embedding_started,

        3

    )



    # -----------------------------------------------------

    # 3. Persistent semantic cache lookup

    # -----------------------------------------------------

    semantic_cache = get_semantic_cache(

        question,

        language,

        query_embedding

    )



    if semantic_cache:

        return _cached_response(

            semantic_cache,

            question,

            language,

            request_started

        )



    # -----------------------------------------------------

    # 4. RAG search using the SAME query embedding

    # -----------------------------------------------------

    results, chroma_seconds = (

        retrieve_search_results(

            question,

            query_embedding=query_embedding,

            top_k=6,

            final_results=4

        )

    )



    search_seconds = round(

        embedding_seconds

        + chroma_seconds,

        3

    )



    if not results:

        return {

            "question": question,

            "language": language,

            "answer":

                "No relevant information "

                "was found in the "

                "knowledge base.",

            "provider": "None",

            "sources": [],

            "cache_hit": False,

            "timing": {

                "search_seconds":

                    search_seconds,

                "ai_seconds": 0,

                "total_seconds":

                    round(

                        time.perf_counter()

                        - request_started,

                        3

                    )

            }

        }



    prompt, sources = build_rag_prompt(

        question,

        language,

        results

    )



    final_result, ai_seconds, gemini_error = (

        generate_rag_answer(

            prompt

        )

    )



    if not final_result.get(

        "success"

    ):

        return {

            "question": question,

            "language": language,

            "error":

                "Both AI providers failed.",

            "gemini_error":

                final_result.get(

                    "gemini_error"

                )

                or gemini_error,

            "groq_error":

                final_result.get(

                    "groq_error"

                ),

            "sources": sources,

            "cache_hit": False,

            "timing": {

                "search_seconds":

                    search_seconds,

                "ai_seconds":

                    ai_seconds,

                "total_seconds":

                    round(

                        time.perf_counter()

                        - request_started,

                        3

                    )

            }

        }



    response = {

        "question": question,

        "language": language,

        "answer": final_result.get(

            "answer",

            ""

        ),

        "provider": final_result.get(

            "provider",

            "Unknown"

        ),

        "sources": sources,

        "cache_hit": False,

        "timing": {

            "search_seconds":

                search_seconds,

            "ai_seconds":

                ai_seconds,

            "total_seconds":

                round(

                    time.perf_counter()

                    - request_started,

                    3

                )

        }

    }



    # Persist successful result for exact and high-confidence

    # semantic reuse after Railway restarts.

    save_cache_entry(

        question,

        language,

        query_embedding,

        response

    )



    return response





@app.post("/api/search")

def search(

    request: ChatRequest

):

    """

    Existing non-streaming endpoint.

    Kept for compatibility with any older frontend/client.

    """

    question = (

        request.question

        or ""

    ).strip()



    if not question:

        return {

            "error":

                "Question cannot be empty."

        }



    language = normalize_language(
        request.language
    )



    print(
        f"Selected API language: {language}",
        flush=True
    )

    return build_search_response(

        question,

        language

    )





@app.post("/api/search-stream")

def search_stream(

    request: ChatRequest

):

    """

    Sends small JSON-line progress events so the frontend can

    show the actual stage:

      1. searching

      2. generating

      3. final result

    """



    question = (

        request.question

        or ""

    ).strip()



    language = normalize_language(
        request.language
    )



    def send_event(payload):

        return (

            json.dumps(

                payload,

                ensure_ascii=False

            )

            + "\n"

        )



    def event_generator():

        if not question:

            yield send_event(

                {

                    "type": "result",

                    "data": {

                        "error":

                            "Question cannot be empty."

                    }

                }

            )

            return



        try:

            yield send_event(

                {

                    "type": "stage",

                    "stage": "searching",

                    "message":

                        "Searching knowledge base..."

                }

            )



            results, search_seconds = (

                retrieve_search_results(

                    question,

                    top_k=5,

                    final_results=3

                )

            )



            if not results:

                yield send_event(

                    {

                        "type": "result",

                        "data": {

                            "question":

                                question,

                            "language":

                                language,

                            "answer":

                                "No relevant information "

                                "was found in the "

                                "knowledge base.",

                            "provider":

                                "None",

                            "sources":

                                [],

                            "timing": {

                                "search_seconds":

                                    search_seconds,

                                "ai_seconds":

                                    0,

                                "total_seconds":

                                    search_seconds

                            }

                        }

                    }

                )

                return



            prompt, sources = (

                build_rag_prompt(

                    question,

                    language,

                    results

                )

            )



            yield send_event(

                {

                    "type": "stage",

                    "stage": "generating",

                    "message":

                        "Relevant sources found. "

                        "Generating answer...",

                    "search_seconds":

                        search_seconds

                }

            )



            final_result, ai_seconds, gemini_error = (

                generate_rag_answer(

                    prompt

                )

            )



            if not final_result.get(

                "success"

            ):

                payload = {

                    "question":

                        question,

                    "language":

                        language,

                    "error":

                        "Both AI providers failed.",

                    "gemini_error":

                        final_result.get(

                            "gemini_error"

                        )

                        or gemini_error,

                    "groq_error":

                        final_result.get(

                            "groq_error"

                        ),

                    "sources":

                        sources,

                    "timing": {

                        "search_seconds":

                            search_seconds,

                        "ai_seconds":

                            ai_seconds,

                        "total_seconds":

                            round(

                                search_seconds

                                + ai_seconds,

                                3

                            )

                    }

                }



            else:

                payload = {

                    "question":

                        question,

                    "language":

                        language,

                    "answer":

                        final_result.get(

                            "answer",

                            ""

                        ),

                    "provider":

                        final_result.get(

                            "provider",

                            "Unknown"

                        ),

                    "sources":

                        sources,

                    "timing": {

                        "search_seconds":

                            search_seconds,

                        "ai_seconds":

                            ai_seconds,

                        "total_seconds":

                            round(

                                search_seconds

                                + ai_seconds,

                                3

                            )

                    }

                }



            yield send_event(

                {

                    "type": "result",

                    "data": payload

                }

            )



        except Exception as error:

            print(

                "Streaming search error:",

                repr(error),

                flush=True

            )



            yield send_event(

                {

                    "type": "result",

                    "data": {

                        "error":

                            f"Search failed: {error}"

                    }

                }

            )



    return StreamingResponse(

        event_generator(),

        media_type=

            "application/x-ndjson",

        headers={

            "Cache-Control":

                "no-cache",

            "X-Accel-Buffering":

                "no"

        }

    )


