from pathlib import Path
import sqlite3
import subprocess
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data/catalog/library.sqlite3"
OUTPUT_DIR = PROJECT_DIR / "data/extracted_text"

SUPPORTED_FORMATS = {
    ".epub",
    ".mobi",
    ".azw",
    ".azw3",
}


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python scripts/extract_ebook_one.py FILE_ID"
        )

    file_id = int(sys.argv[1])

    database = sqlite3.connect(DATABASE_PATH)

    row = database.execute(
        """
        SELECT path, filename, extension
        FROM files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()

    database.close()

    if row is None:
        raise SystemExit(f"Unknown file ID: {file_id}")

    path = Path(row[0])
    filename = row[1]
    extension = row[2]

    if extension not in SUPPORTED_FORMATS:
        raise SystemExit(
            f"Unsupported ebook format: {extension}"
        )

    if not path.exists():
        raise SystemExit(f"File is unavailable: {path}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{file_id}.txt"

    if output_path.exists():
        raise SystemExit(
            f"Output already exists: {output_path}"
        )

    print(f"Extracting: {filename}")

    result = subprocess.run(
        [
            "ebook-convert",
            str(path),
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(result.returncode)

    character_count = len(
        output_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    )

    print(f"Output: {output_path}")
    print(f"Characters extracted: {character_count}")


if __name__ == "__main__":
    main()
