from pathlib import Path
from pypdf import PdfReader

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "local_pdfs"
OUTPUT_ROOT = PROJECT_ROOT / "data"


def extract_pdf_to_text(pdf_file: Path, output_file: Path):
    reader = PdfReader(str(pdf_file))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    sections = []

    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            sections.append(
                f"===== PAGE {page_number} =====\n{text}\n"
            )

    if not sections:
        raise ValueError(
            f"No extractable text found in {pdf_file.name}. "
            "If it is a scanned/image-only PDF, OCR is required first."
        )

    output_file.write_text("\n".join(sections), encoding="utf-8")
    return len(sections)


def main():
    pdf_files = sorted(SOURCE_ROOT.rglob("*.pdf"))

    if not pdf_files:
        print("No PDFs found under local_pdfs/.")
        return

    converted = 0
    failed = 0

    for pdf_file in pdf_files:
        relative = pdf_file.relative_to(SOURCE_ROOT)
        output_file = (OUTPUT_ROOT / relative).with_suffix(".txt")

        try:
            pages = extract_pdf_to_text(pdf_file, output_file)
            converted += 1
            print(
                f"Converted: {pdf_file.name} -> {output_file} "
                f"({pages} text pages)"
            )
        except Exception as error:
            failed += 1
            print(f"FAILED: {pdf_file} -> {error}")

    print(f"Finished. Converted={converted}, Failed={failed}")


if __name__ == "__main__":
    main()
