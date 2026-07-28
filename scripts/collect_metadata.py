from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import sqlite3

from inspect_metadata import (
    filename_candidates,
    normalized,
    read_embedded_metadata,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data/catalog/library.sqlite3"

EBOOK_FORMATS = {
    ".epub",
    ".mobi",
    ".azw",
    ".azw3",
}


def json_text(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
    )


def first_author(metadata: dict):
    authors = metadata.get("authors", [])
    return authors[0] if authors else None


def resolve_without_llm(
    embedded: dict,
    filename: dict,
    extension: str,
) -> dict:
    embedded_title = embedded.get("title")
    embedded_author = first_author(embedded)

    filename_title = filename.get("title")
    filename_author = first_author(filename)

    filename_recognized = (
        filename.get("pattern") != "unrecognized"
        and filename_title
        and filename_author
    )

    embedded_complete = (
        embedded.get("available")
        and embedded_title
        and embedded_author
    )

    # Rule 1: Embedded title and author are clearly swapped.
    if (
        embedded_complete
        and filename_recognized
        and normalized(embedded_title)
        == normalized(filename_author)
        and normalized(embedded_author)
        == normalized(filename_title)
    ):
        return {
            "status": "resolved",
            "method": "swapped_fields_rule",
            "confidence": "high",
            "title": filename_title,
            "authors": filename["authors"],
            "language": None,
            "publisher": embedded.get("publisher"),
            "date": embedded.get("date"),
        }

    # Rule 2: Embedded and filename identity agree.
    if (
        embedded_complete
        and filename_recognized
        and normalized(embedded_title)
        == normalized(filename_title)
        and normalized(embedded_author)
        == normalized(filename_author)
    ):
        return {
            "status": "resolved",
            "method": "source_agreement",
            "confidence": "high",
            "title": embedded_title,
            "authors": embedded["authors"],
            "language": embedded.get("language"),
            "publisher": embedded.get("publisher"),
            "date": embedded.get("date"),
        }

    # Rule 3: Complete ebook metadata with no competing
    # filename interpretation.
    if (
        extension in EBOOK_FORMATS
        and embedded_complete
        and not filename_recognized
    ):
        return {
            "status": "provisional",
            "method": "embedded_ebook_metadata",
            "confidence": "medium",
            "title": embedded_title,
            "authors": embedded["authors"],
            "language": embedded.get("language"),
            "publisher": embedded.get("publisher"),
            "date": embedded.get("date"),
        }

    # Rule 4: Filename identity exists but embedded data
    # is missing or unusable.
    if filename_recognized and not embedded_complete:
        return {
            "status": "provisional",
            "method": "filename_only",
            "confidence": "medium",
            "title": filename_title,
            "authors": filename["authors"],
            "language": None,
            "publisher": None,
            "date": None,
        }

    return {
        "status": "needs_resolution",
        "method": None,
        "confidence": None,
        "title": None,
        "authors": [],
        "language": None,
        "publisher": None,
        "date": None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
    )
    args = parser.parse_args()

    database = sqlite3.connect(DATABASE_PATH)
    database.execute("PRAGMA foreign_keys = ON")

    rows = database.execute(
        """
        SELECT
            f.id,
            f.path,
            f.extension
        FROM files AS f
        LEFT JOIN file_metadata AS m
            ON m.file_id = f.id
        WHERE m.file_id IS NULL
        ORDER BY f.id
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()

    results = Counter()
    timestamp = datetime.now(timezone.utc).isoformat()

    for file_id, path_string, extension in rows:
        path = Path(path_string)

        embedded = read_embedded_metadata(path)
        filename = filename_candidates(path)

        resolution = resolve_without_llm(
            embedded=embedded,
            filename=filename,
            extension=extension,
        )

        database.execute(
            """
            INSERT INTO file_metadata (
                file_id,
                embedded_metadata_json,
                filename_metadata_json,
                resolved_title,
                resolved_authors_json,
                resolved_language,
                resolved_publisher,
                resolved_date,
                resolution_method,
                confidence,
                status,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                json_text(embedded),
                json_text(filename),
                resolution["title"],
                json_text(resolution["authors"]),
                resolution["language"],
                resolution["publisher"],
                resolution["date"],
                resolution["method"],
                resolution["confidence"],
                resolution["status"],
                timestamp,
            ),
        )

        results[resolution["status"]] += 1

        title = resolution["title"] or "[unresolved]"

        print(
            f"ID {file_id}: "
            f"{resolution['status']} | "
            f"{resolution['method']} | "
            f"{title}"
        )

    database.commit()
    database.close()

    print()
    print(f"Processed: {len(rows)}")

    for status, count in sorted(results.items()):
        print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
