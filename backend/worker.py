import os
import time
import traceback

try:
    from .job_store import (
        init_job_database,
        requeue_incomplete_jobs,
        claim_next_job,
        update_job
    )
    from .upload_ingest import ingest_uploaded_file
    from .semantic_cache import clear_semantic_cache
except ImportError:
    from job_store import (
        init_job_database,
        requeue_incomplete_jobs,
        claim_next_job,
        update_job
    )
    from upload_ingest import ingest_uploaded_file
    from semantic_cache import clear_semantic_cache


WORKER_POLL_SECONDS = float(
    os.getenv(
        "WORKER_POLL_SECONDS",
        "2"
    )
)


def process_job(job):
    job_id = job["job_id"]
    filename = job["filename"]
    file_path = job["file_path"]

    print(
        f"Worker starting job: "
        f"{job_id} / {filename}",
        flush=True
    )

    try:
        result = ingest_uploaded_file(
            file_path,
            job_id
        )

        if result.get("success"):
            # Uploaded knowledge changed, so old cached answers may be stale.
            clear_semantic_cache()

            update_job(
                job_id,
                status="completed",
                stage="completed",
                chunks=result.get(
                    "chunks",
                    0
                ),
                searchable=1,
                message=(
                    "Document indexing "
                    "completed successfully."
                ),
                error="",
                finished_at=time.time()
            )

        else:
            update_job(
                job_id,
                status="failed",
                stage="failed",
                searchable=0,
                message=result.get(
                    "message",
                    "Document indexing failed."
                ),
                error=result.get(
                    "message",
                    "Document indexing failed."
                ),
                finished_at=time.time()
            )

        print(
            f"Worker finished job: "
            f"{job_id} -> {result}",
            flush=True
        )

    except Exception as error:
        traceback.print_exc()

        update_job(
            job_id,
            status="failed",
            stage="failed",
            searchable=0,
            message=str(error),
            error=str(error),
            finished_at=time.time()
        )


def document_worker_loop():
    init_job_database()
    requeue_incomplete_jobs()

    print(
        "Document worker started.",
        flush=True
    )

    while True:
        job = claim_next_job()

        if not job:
            time.sleep(
                WORKER_POLL_SECONDS
            )
            continue

        process_job(job)


if __name__ == "__main__":
    document_worker_loop()
