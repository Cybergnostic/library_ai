from pathlib import Path
import sqlite3

import pymupdf


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data" / "catalog" / "library.sqlite3"
TEST_LIMIT = 10


def inspect_pdf(path: Path) -> dict:
    document = pymupdf.open(path)

    if document.needs_pass:
        document.close()
        return {
            "status": "encrypted",
            "pages": 0,
            "text_pages": 0,
            "image_only_pages": 0,
        }

    page_count = len(document)
    text_pages = 0
    image_only_pages = 0
    low_text_pages = 0

    for page in document:
        text = page.get_text("text")
        useful_characters = sum(
            character.isalnum()
            for character in text
        )

        has_images = bool(page.get_images(full=True))

        if useful_characters >= 50:
            text_pages += 1
        elif useful_characters < 20 and has_images:
            image_only_pages += 1
        else:
            low_text_pages += 1

    document.close()

    if page_count == 0:
        status = "empty"
    else:
        text_ratio = text_pages / page_count
        image_ratio = image_only_pages / page_count

        if text_ratio >= 0.80:
            status = "text_available"
        elif text_ratio <= 0.10 and image_ratio >= 0.50:
            status = "ocr_needed"
        elif text_ratio > 0.10 and image_ratio > 0.10:
            status = "mixed_document"
        else:
            status = "manual_review"

    return {
        "status": status,
        "pages": page_count,
        "text_pages": text_pages,
        "image_only_pages": image_only_pages,
        "low_text_pages": low_text_pages,
    }


def main():
    connection = sqlite3.connect(DATABASE_PATH)

    rows = connection.execute(
        """
        SELECT id, path
        FROM files
        WHERE extension = '.pdf'
        ORDER BY id
        LIMIT ?
        """,
        (TEST_LIMIT,),
    ).fetchall()

    connection.close()

    for file_id, path_string in rows:
        path = Path(path_string)

        print()
        print(f"File ID: {file_id}")
        print(f"File: {path.name}")

        try:
            result = inspect_pdf(path)

            print(f"Status: {result['status']}")
            print(f"Pages: {result['pages']}")
            print(f"Text pages: {result['text_pages']}")
            print(
                f"Image-only pages: "
                f"{result['image_only_pages']}"
            )
            print(
                f"Low-text pages: "
                f"{result.get('low_text_pages', 0)}"
            )

        except Exception as error:
            print(f"Status: inspection_failed")
            print(f"Error: {error}")


if __name__ == "__main__":
    main()
