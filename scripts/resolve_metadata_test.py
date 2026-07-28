from pathlib import Path
from typing import Literal
import json
import sqlite3
import sys

from ollama import chat
from pydantic import BaseModel

from inspect_metadata import (
    filename_candidates,
    read_embedded_metadata,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data/catalog/library.sqlite3"


class Resolution(BaseModel):
    choice: Literal[
        "embedded_metadata",
        "filename_metadata",
        "uncertain",
    ]
    confidence: Literal["high", "medium", "low"]
    reason: str


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python scripts/resolve_metadata_test.py FILE_ID"
        )

    file_id = int(sys.argv[1])

    database = sqlite3.connect(DATABASE_PATH)

    row = database.execute(
        """
        SELECT path
        FROM files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()

    database.close()

    if row is None:
        raise SystemExit(f"Unknown file ID: {file_id}")

    source_path = Path(row[0])
    text_path = (
        PROJECT_DIR
        / "data"
        / "extracted_text"
        / f"{file_id}.txt"
    )

    if not text_path.exists():
        raise SystemExit(
            f"Extracted text not found: {text_path}"
        )

    embedded = read_embedded_metadata(source_path)
    filename = filename_candidates(source_path)

    text_sample = text_path.read_text(
        encoding="utf-8",
        errors="replace",
    )[:8000]

    candidates = {
        "embedded_metadata": {
            "title": embedded.get("title"),
            "authors": embedded.get("authors", []),
        },
        "filename_metadata": {
            "title": filename.get("title"),
            "authors": filename.get("authors", []),
        },
    }

    response = chat(
        model="qwen3:8b",
        messages=[
            {
                "role": "system",
                "content": (
                    "Resolve a bibliographic metadata conflict. "
                    "Use explicit evidence from the supplied document "
                    "text. You may only select one provided candidate "
                    "or return uncertain. Do not introduce another "
                    "title or author. General knowledge is not evidence. "
                    "If the text does not clearly identify the work, "
                    "return uncertain."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Candidates:\n"
                    + json.dumps(
                        candidates,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n\nDocument opening:\n"
                    + text_sample
                ),
            },
        ],
        format=Resolution.model_json_schema(),
        options={"temperature": 0},
        think=False,
    )

    resolution = Resolution.model_validate_json(
        response.message.content
    )

    print("Candidates:")
    print(
        json.dumps(
            candidates,
            ensure_ascii=False,
            indent=2,
        )
    )

    print()
    print("Resolution:")
    print(resolution.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
