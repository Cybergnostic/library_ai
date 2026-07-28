from pathlib import Path
from typing import Literal
import sqlite3
import sys

from ollama import chat
from pydantic import BaseModel


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data/catalog/library.sqlite3"


class BookMetadata(BaseModel):
    title: str | None
    authors: list[str]
    languages: list[str]
    document_type: Literal[
        "book",
        "article",
        "report",
        "other",
        "unknown",
    ]
    subjects: list[str]
    confidence: Literal["high", "medium", "low"]


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python scripts/test_llm_metadata.py FILE_ID"
        )

    file_id = int(sys.argv[1])

    database = sqlite3.connect(DATABASE_PATH)

    row = database.execute(
        """
        SELECT filename
        FROM files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()

    database.close()

    if row is None:
        raise SystemExit(f"Unknown file ID: {file_id}")

    filename = row[0]
    text_path = PROJECT_DIR / f"data/ocr_copies/{file_id}.txt"

    if not text_path.exists():
        raise SystemExit(f"OCR text not found: {text_path}")

    text_sample = text_path.read_text(
        encoding="utf-8",
        errors="replace",
    )[:8000]

    response = chat(
        model="qwen3:8b",
        messages=[
            {
                "role": "system",
                "content": (
                    "Catalogue this specific digital document. "
                    "Identify its actual text language, not the author's "
                    "nationality or the work's original language. "
                    "Use only the filename and supplied text. "
                    "Do not invent publication details. "
                    "Subjects must be supported by the text."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Filename: {filename}\n\n"
                    f"Text sample:\n{text_sample}"
                ),
            },
        ],
        format=BookMetadata.model_json_schema(),
        options={"temperature": 0},
        think=False,
    )

    metadata = BookMetadata.model_validate_json(
        response.message.content
    )

    print(metadata.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
