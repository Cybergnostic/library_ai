from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import argparse
import sqlite3

from test_pdf_audit import inspect_pdf


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data" / "catalog" / "library.sqlite3"


PDF_COLUMNS = {
    "pdf_page_count": "INTEGER",
    "pdf_text_pages": "INTEGER",
    "pdf_image_only_pages": "INTEGER",
    "pdf_low_text_pages": "INTEGER",
    "ocr_status": "TEXT",
    "ocr_checked_at": "TEXT",
}


def ensure_columns(connection: sqlite3.Connection):
    existing = {
        row[1]
        for row in connection.execute("PRAGMA table_info(files)")
    }

    for column, column_type in PDF_COLUMNS.items():
        if column not in existing:
            connection.execute(
                f"ALTER TABLE files ADD COLUMN {column} {column_type}"
            )

    connection.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Number of unaudited PDFs to inspect; use 0 for all",
    )
    args = parser.parse_args()

    connection = sqlite3.connect(DATABASE_PATH)
    ensure_columns(connection)

    query = """
        SELECT id, path
        FROM files
        WHERE extension = '.pdf'
          AND ocr_status IS NULL
        ORDER BY id
    """

    parameters = ()

    if args.limit > 0:
        query += " LIMIT ?"
        parameters = (args.limit,)

    rows = connection.execute(query, parameters).fetchall()

    if not rows:
        print("No unaudited PDFs found.")
        connection.close()
        return

    results = Counter()
    checked_at = datetime.now(timezone.utc).isoformat()

    for position, (file_id, path_string) in enumerate(rows, start=1):
        path = Path(path_string)

        try:
            result = inspect_pdf(path)
            status = result["status"]

            connection.execute(
                """
                UPDATE files
                SET pdf_page_count = ?,
                    pdf_text_pages = ?,
                    pdf_image_only_pages = ?,
                    pdf_low_text_pages = ?,
                    ocr_status = ?,
                    ocr_checked_at = ?,
                    error_message = NULL
                WHERE id = ?
                """,
                (
                    result.get("pages", 0),
                    result.get("text_pages", 0),
                    result.get("image_only_pages", 0),
                    result.get("low_text_pages", 0),
                    status,
                    checked_at,
                    file_id,
                ),
            )

        except Exception as error:
            status = "inspection_failed"

            connection.execute(
                """
                UPDATE files
                SET ocr_status = ?,
                    ocr_checked_at = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (
                    status,
                    checked_at,
                    str(error),
                    file_id,
                ),
            )

        results[status] += 1

        if position % 10 == 0:
            connection.commit()
            print(f"Inspected {position}/{len(rows)} PDFs...")

    connection.commit()
    connection.close()

    print()
    print(f"Inspected this run: {len(rows)}")

    for status, count in sorted(results.items()):
        print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
