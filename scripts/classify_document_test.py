from pathlib import Path
from typing import Literal
import json
import sqlite3
import sys

import pymupdf
from ollama import chat
from pydantic import BaseModel

from inspect_metadata import read_embedded_metadata


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data/catalog/library.sqlite3"


class Classification(BaseModel):
    document_type: Literal[
        "book",
        "article",
        "lecture_notes",
        "report",
        "form",
        "contract",
        "magazine",
        "personal_document",
        "unknown",
    ]
    confidence: Literal["high", "medium", "low"]
    reason: str


def pdf_sample(path: Path, character_limit: int = 6000) -> str:
    document = pymupdf.open(path)
    parts = []

    for page in document:
        text = page.get_text("text").strip()

        if text:
            parts.append(text)

        if sum(len(part) for part in parts) >= character_limit:
            break

    document.close()

    return "\n\n".join(parts)[:character_limit]


def existing_text_sample(
    file_id: int,
    character_limit: int = 6000,
) -> str:
    possible_paths = [
        PROJECT_DIR / f"data/extracted_text/{file_id}.txt",
        PROJECT_DIR / f"data/ocr_copies/{file_id}.txt",
    ]

    for path in possible_paths:
        if path.exists():
            return path.read_text(
                encoding="utf-8",
                errors="replace",
            )[:character_limit]

    return ""


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python scripts/classify_document_test.py FILE_ID"
        )

    file_id = int(sys.argv[1])

    database = sqlite3.connect(DATABASE_PATH)

    row = database.execute(
        """
        SELECT path, extension
        FROM files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()

    database.close()

    if row is None:
        raise SystemExit(f"Unknown file ID: {file_id}")

    path = Path(row[0])
    extension = row[1]
    embedded = read_embedded_metadata(path)

    if extension == ".pdf":
        sample = pdf_sample(path)
    else:
        sample = existing_text_sample(file_id)

    evidence = {
        "filename": path.name,
        "parent_directory": path.parent.name,
        "format": extension,
        "embedded_metadata": embedded,
        "text_available": bool(sample),
    }

    response = chat(
        model="qwen3:8b",
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify this digital document. "
                    "A filename containing a dash does not necessarily "
                    "mean Author - Title. Lecture notes, forms and reports "
                    "must not be classified as books merely because they "
                    "are PDFs. Use the filename, embedded metadata and "
                    "document opening. Return unknown when evidence is "
                    "insufficient."
                ),
            },
            {
                "role": "user",
                "content": (
                    "File evidence:\n"
                    + json.dumps(
                        evidence,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n\nDocument opening:\n"
                    + sample
                ),
            },
        ],
        format=Classification.model_json_schema(),
        options={"temperature": 0},
        think=False,
    )

    classification = Classification.model_validate_json(
        response.message.content
    )

    print(classification.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
