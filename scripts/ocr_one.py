from pathlib import Path
import argparse
import sqlite3
import subprocess

from test_pdf_audit import inspect_pdf


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data" / "catalog" / "library.sqlite3"
OUTPUT_DIR = PROJECT_DIR / "data" / "ocr_copies"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file_id", type=int)
    parser.add_argument(
        "--language",
        default="eng",
        help="Tesseract language, for example eng, srp or srp_latn",
    )
    args = parser.parse_args()

    connection = sqlite3.connect(DATABASE_PATH)

    row = connection.execute(
        """
        SELECT path, filename, ocr_status
        FROM files
        WHERE id = ?
        """,
        (args.file_id,),
    ).fetchone()

    connection.close()

    if row is None:
        raise SystemExit(f"No file found with ID {args.file_id}")

    path_string, filename, ocr_status = row
    input_path = Path(path_string)

    if not input_path.exists():
        raise SystemExit(f"Original file is unavailable: {input_path}")

    if input_path.suffix.lower() != ".pdf":
        raise SystemExit("This OCR tool currently supports PDF files only.")

    if ocr_status != "ocr_needed":
        raise SystemExit(
            f"File status is {ocr_status!r}, not 'ocr_needed'. "
            "Refusing automatic OCR."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_pdf = OUTPUT_DIR / f"{args.file_id}.pdf"
    output_text = OUTPUT_DIR / f"{args.file_id}.txt"

    if output_pdf.exists() or output_text.exists():
        raise SystemExit(
            "Output already exists. Refusing to overwrite it:\n"
            f"{output_pdf}\n"
            f"{output_text}"
        )

    command = [
        "ocrmypdf",
        "--output-type",
        "pdf",
        "--redo-ocr",
        "--rotate-pages",
        "--optimize",
        "1",
        "--language",
        args.language,
        "--sidecar",
        str(output_text),
        str(input_path),
        str(output_pdf),
    ]

    print(f"File: {filename}")
    print(f"Language: {args.language}")
    print(f"Original: {input_path}")
    print(f"OCR copy: {output_pdf}")
    print()
    print("Starting OCR...")

    result = subprocess.run(command)

    if result.returncode != 0:
        print()
        print(f"OCR failed with exit code {result.returncode}.")
        raise SystemExit(result.returncode)

    print()
    print("OCR completed. Inspecting the result...")

    inspection = inspect_pdf(output_pdf)

    print(f"Status after OCR: {inspection['status']}")
    print(f"Pages: {inspection['pages']}")
    print(f"Text pages: {inspection['text_pages']}")
    print(f"Image-only pages: {inspection['image_only_pages']}")
    print(f"Low-text pages: {inspection['low_text_pages']}")
    print()
    print(f"Searchable PDF: {output_pdf}")
    print(f"Extracted OCR text: {output_text}")


if __name__ == "__main__":
    main()
