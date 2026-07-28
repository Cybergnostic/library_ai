from pathlib import Path
import json
import sqlite3
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data/catalog/library.sqlite3"

DC = "http://purl.org/dc/elements/1.1/"


def element_text(element):
    if element is None or element.text is None:
        return None

    value = element.text.strip()
    return value or None


def read_embedded_metadata(path: Path) -> dict:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opf_path = Path(temporary_directory) / "metadata.opf"

        result = subprocess.run(
            [
                "ebook-meta",
                str(path),
                f"--to-opf={opf_path}",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0 or not opf_path.exists():
            return {
                "available": False,
                "error": result.stderr.strip(),
            }

        root = ET.parse(opf_path).getroot()

        return {
            "available": True,
            "title": element_text(
                root.find(f".//{{{DC}}}title")
            ),
            "authors": [
                element.text.strip()
                for element in root.findall(f".//{{{DC}}}creator")
                if element.text and element.text.strip()
            ],
            "language": element_text(
                root.find(f".//{{{DC}}}language")
            ),
            "publisher": element_text(
                root.find(f".//{{{DC}}}publisher")
            ),
            "date": element_text(
                root.find(f".//{{{DC}}}date")
            ),
        }


def filename_candidates(path: Path) -> dict:
    stem = path.stem.strip()

    if " - " not in stem:
        return {
            "pattern": "unrecognized",
            "title": None,
            "authors": [],
        }

    left, right = stem.split(" - ", 1)

    return {
        "pattern": "author_dash_title",
        "title": right.strip(),
        "authors": [left.strip()],
    }


def normalized(value):
    if value is None:
        return None

    return " ".join(value.casefold().split())


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python scripts/inspect_metadata.py FILE_ID"
        )

    file_id = int(sys.argv[1])

    database = sqlite3.connect(DATABASE_PATH)

    row = database.execute(
        """
        SELECT path, filename
        FROM files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()

    database.close()

    if row is None:
        raise SystemExit(f"Unknown file ID: {file_id}")

    path = Path(row[0])
    embedded = read_embedded_metadata(path)
    filename = filename_candidates(path)

    diagnosis = "no_clear_resolution"
    recommendation = None

    if (
        embedded.get("available")
        and filename["pattern"] == "author_dash_title"
        and normalized(embedded.get("title"))
        == normalized(filename["authors"][0])
        and embedded.get("authors")
        and normalized(embedded["authors"][0])
        == normalized(filename["title"])
    ):
        diagnosis = "embedded_title_and_author_probably_swapped"
        recommendation = {
            "title": filename["title"],
            "authors": filename["authors"],
            "confidence": "high",
        }

    output = {
        "file_id": file_id,
        "filename": path.name,
        "embedded_metadata": embedded,
        "filename_candidates": filename,
        "diagnosis": diagnosis,
        "recommendation": recommendation,
    }

    print(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
