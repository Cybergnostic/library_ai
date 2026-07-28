from pathlib import Path
import hashlib
import sqlite3
import subprocess
import re

import pymupdf
import typer

import chromadb
from ollama import (
    chat as ollama_chat,
    embed as ollama_embed,
)


PROJECT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = PROJECT_DIR / "data/catalog/library.sqlite3"
TEXT_DIR = PROJECT_DIR / "data/extracted_text"
OCR_DIR = PROJECT_DIR / "data/ocr_copies"

VECTOR_DIR = PROJECT_DIR / "data/vector_db"
EMBEDDING_MODEL = "qwen3-embedding:0.6b"

EBOOK_FORMATS = {
    ".epub",
    ".mobi",
    ".azw",
    ".azw3",
}

PERMANENT_BATCH_EXCLUSIONS = {
    "embedded",
    "quality_review",
    "excluded_non_library",
    "excluded_duplicate",
    "drm_protected",
}

app = typer.Typer(
    help="Local AI library manager."
)


class EbookConversionError(RuntimeError):
    def __init__(self, message: str, status: str):
        super().__init__(message)
        self.status = status


def classify_ebook_conversion_error(message: str) -> str:
    """Return a durable processing status for a Calibre failure."""

    normalized = message.casefold()

    drm_markers = (
        "calibre.ebooks.drmerror",
        "drmerror",
        "drm protected",
        "drm-protected",
        "digital rights management",
    )

    malformed_markers = (
        "badzipfile",
        "not a zip file",
        "central directory",
        "invalid epub",
        "malformed epub",
        "corrupt archive",
        "corrupted archive",
    )

    if any(marker in normalized for marker in drm_markers):
        return "drm_protected"

    if any(marker in normalized for marker in malformed_markers):
        return "malformed_ebook"

    return "ebook_conversion_failed"


def analyze_document_quality(text: str) -> dict:
    """Calculate conservative signals for human quality review."""

    stripped_text = text.strip()
    character_count = len(stripped_text)
    non_whitespace_count = sum(
        not character.isspace()
        for character in stripped_text
    )
    alphabetic_count = sum(
        character.isalpha()
        for character in stripped_text
    )

    words = re.findall(
        r"[^\W\d_]+(?:['’\-][^\W\d_]+)*",
        stripped_text,
        flags=re.UNICODE,
    )
    normalized_words = [
        word.casefold()
        for word in words
    ]

    nonempty_lines = [
        " ".join(line.split())
        for line in stripped_text.splitlines()
        if line.strip()
    ]
    unique_lines = {
        line.casefold()
        for line in nonempty_lines
    }

    urls = re.findall(
        r"https?://\S+",
        stripped_text,
        flags=re.IGNORECASE,
    )

    word_count = len(words)
    unique_word_count = len(set(normalized_words))
    line_count = len(nonempty_lines)
    url_count = len(urls)

    alphabetic_ratio = (
        alphabetic_count / non_whitespace_count
        if non_whitespace_count
        else 0.0
    )
    unique_line_ratio = (
        len(unique_lines) / line_count
        if line_count
        else 0.0
    )
    repeated_line_ratio = (
        1.0 - unique_line_ratio
        if line_count
        else 0.0
    )
    unique_word_ratio = (
        unique_word_count / word_count
        if word_count
        else 0.0
    )
    urls_per_1000_characters = (
        url_count * 1000 / character_count
        if character_count
        else 0.0
    )

    review_reasons = []

    if word_count < 300:
        review_reasons.append(
            f"very low word count ({word_count})"
        )

    if (
        url_count >= 10
        and urls_per_1000_characters >= 2.0
    ):
        review_reasons.append(
            "high URL density "
            f"({url_count} URLs; "
            f"{urls_per_1000_characters:.1f} per 1,000 characters)"
        )

    if (
        character_count >= 1000
        and alphabetic_ratio < 0.45
    ):
        review_reasons.append(
            "low alphabetic-character ratio "
            f"({alphabetic_ratio:.1%})"
        )

    if (
        line_count >= 10
        and repeated_line_ratio >= 0.35
    ):
        review_reasons.append(
            "many repeated lines "
            f"({repeated_line_ratio:.1%})"
        )

    if (
        200 <= word_count <= 5000
        and unique_word_ratio < 0.08
    ):
        review_reasons.append(
            "low unique-word ratio "
            f"({unique_word_ratio:.1%})"
        )

    return {
        "character_count": character_count,
        "word_count": word_count,
        "unique_word_count": unique_word_count,
        "line_count": line_count,
        "url_count": url_count,
        "alphabetic_ratio": alphabetic_ratio,
        "unique_line_ratio": unique_line_ratio,
        "repeated_line_ratio": repeated_line_ratio,
        "unique_word_ratio": unique_word_ratio,
        "urls_per_1000_characters": (
            urls_per_1000_characters
        ),
        "review_reasons": review_reasons,
    }


def quality_summary(analysis: dict) -> str:
    reasons = analysis["review_reasons"]

    if not reasons:
        return "No automatic quality-review signals were found."

    return "Quality review required: " + "; ".join(reasons) + "."


def ensure_file_processing_schema(
    database: sqlite3.Connection,
):
    """Add regenerable processing fields when upgrading older catalogues."""

    columns = {
        row[1]
        for row in database.execute(
            "PRAGMA table_info(files)"
        ).fetchall()
    }

    if "extracted_text_sha256" not in columns:
        database.execute(
            """
            ALTER TABLE files
            ADD COLUMN extracted_text_sha256 TEXT
            """
        )

    if "duplicate_of_file_id" not in columns:
        database.execute(
            """
            ALTER TABLE files
            ADD COLUMN duplicate_of_file_id INTEGER
            """
        )

    database.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_files_extracted_text_sha256
        ON files(extracted_text_sha256)
        """
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as source:
        for block in iter(
            lambda: source.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def backfill_extracted_text_hashes(
    database: sqlite3.Connection,
):
    """Hash extracted texts that do not yet have a stored digest."""

    ensure_file_processing_schema(database)

    rows = database.execute(
        """
        SELECT id
        FROM files
        WHERE extracted_text_sha256 IS NULL
        ORDER BY id
        """
    ).fetchall()

    for row in rows:
        file_id = row[0]
        text_path = TEXT_DIR / f"{file_id}.txt"

        if not text_path.exists():
            continue

        database.execute(
            """
            UPDATE files
            SET extracted_text_sha256 = ?
            WHERE id = ?
            """,
            (
                sha256_file(text_path),
                file_id,
            ),
        )

    database.commit()


def find_exact_text_duplicate(
    file_id: int,
) -> dict | None:
    """Find an already catalogued exact extracted-text duplicate."""

    database = sqlite3.connect(DATABASE_PATH)
    backfill_extracted_text_hashes(database)

    row = database.execute(
        """
        SELECT extracted_text_sha256
        FROM files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()

    if row is None or row[0] is None:
        database.close()
        return None

    digest = row[0]
    duplicate_row = database.execute(
        """
        SELECT
            id,
            filename
        FROM files
        WHERE extracted_text_sha256 = ?
          AND id != ?
          AND COALESCE(processing_status, '') NOT IN (
              'excluded_duplicate',
              'excluded_non_library'
          )
        ORDER BY
            CASE processing_status
                WHEN 'embedded' THEN 0
                WHEN 'text_chunked' THEN 1
                WHEN 'text_extracted' THEN 2
                ELSE 3
            END,
            id
        LIMIT 1
        """,
        (
            digest,
            file_id,
        ),
    ).fetchone()

    database.close()

    if duplicate_row is None:
        return None

    return {
        "file_id": duplicate_row[0],
        "filename": duplicate_row[1],
        "sha256": digest,
    }


def set_duplicate_status(
    file_id: int,
    duplicate_of_file_id: int,
    message: str,
):
    database = sqlite3.connect(DATABASE_PATH)
    ensure_file_processing_schema(database)
    database.execute(
        """
        UPDATE files
        SET processing_status = 'excluded_duplicate',
            error_message = ?,
            duplicate_of_file_id = ?
        WHERE id = ?
        """,
        (
            message,
            duplicate_of_file_id,
            file_id,
        ),
    )
    database.commit()
    database.close()


def get_file(file_id: int):
    database = sqlite3.connect(DATABASE_PATH)

    row = database.execute(
        """
        SELECT
            id,
            path,
            filename,
            extension,
            ocr_status
        FROM files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()

    database.close()

    if row is None:
        raise typer.BadParameter(
            f"Unknown file ID: {file_id}"
        )

    return {
        "id": row[0],
        "path": Path(row[1]),
        "filename": row[2],
        "extension": row[3],
        "ocr_status": row[4],
    }


def set_processing_status(
    file_id: int,
    status: str,
    error: str | None = None,
):
    database = sqlite3.connect(DATABASE_PATH)

    database.execute(
        """
        UPDATE files
        SET processing_status = ?,
            error_message = ?
        WHERE id = ?
        """,
        (status, error, file_id),
    )

    database.commit()
    database.close()


def get_processing_status(file_id: int) -> str | None:
    database = sqlite3.connect(DATABASE_PATH)

    row = database.execute(
        """
        SELECT processing_status
        FROM files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()

    database.close()

    if row is None:
        return None

    return row[0]


def extract_pdf(
    source_path: Path,
    output_path: Path,
) -> int:
    document = pymupdf.open(source_path)

    if document.needs_pass:
        document.close()
        raise RuntimeError("PDF is encrypted.")

    temporary_path = output_path.with_name(
        f".{output_path.stem}.working.txt"
    )

    character_count = 0

    with temporary_path.open(
        "w",
        encoding="utf-8",
    ) as output:
        for page_number, page in enumerate(
            document,
            start=1,
        ):
            text = page.get_text("text").strip()
            character_count += len(text)

            output.write(
                f"[[PAGE {page_number}]]\n"
            )
            output.write(text)
            output.write("\n\n")

    document.close()
    temporary_path.replace(output_path)

    return character_count


def extract_ebook(
    source_path: Path,
    output_path: Path,
) -> int:
    temporary_path = output_path.with_name(
        f".{output_path.stem}.working.txt"
    )

    result = subprocess.run(
        [
            "ebook-convert",
            str(source_path),
            str(temporary_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        temporary_path.unlink(missing_ok=True)

        output_parts = [
            output.strip()
            for output in (
                result.stderr,
                result.stdout,
            )
            if output.strip()
        ]
        error = (
            "\n".join(output_parts)
            or "Calibre conversion failed."
        )

        raise EbookConversionError(
            error,
            classify_ebook_conversion_error(error),
        )

    text = temporary_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    temporary_path.replace(output_path)

    return len(text)


@app.command()
def extract(
    file_id: int = typer.Argument(
        help="Catalogue file ID."
    ),
):
    """Extract searchable text from one document."""

    file_record = get_file(file_id)
    original_path = file_record["path"]
    extension = file_record["extension"]

    if not original_path.exists():
        raise typer.BadParameter(
            f"Source file is unavailable: {original_path}"
        )

    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = TEXT_DIR / f"{file_id}.txt"

    if output_path.exists():
        typer.echo(
            f"Text already exists: {output_path}"
        )
        raise typer.Exit()

    typer.echo(
        f"Extracting: {file_record['filename']}"
    )

    set_processing_status(
        file_id,
        "extracting_text",
    )

    try:
        if extension == ".pdf":
            ocr_copy = OCR_DIR / f"{file_id}.pdf"

            if ocr_copy.exists():
                source_path = ocr_copy
                typer.echo("Using OCR copy.")
            elif file_record["ocr_status"] == "ocr_needed":
                raise RuntimeError(
                    "This PDF requires OCR before extraction."
                )
            else:
                source_path = original_path

            character_count = extract_pdf(
                source_path,
                output_path,
            )

        elif extension in EBOOK_FORMATS:
            character_count = extract_ebook(
                original_path,
                output_path,
            )

        else:
            raise RuntimeError(
                f"Unsupported format: {extension}"
            )

    except EbookConversionError as error:
        set_processing_status(
            file_id,
            error.status,
            str(error),
        )
        raise typer.BadParameter(str(error))

    except Exception as error:
        set_processing_status(
            file_id,
            "text_extraction_failed",
            str(error),
        )
        raise typer.BadParameter(str(error))

    set_processing_status(
        file_id,
        "text_extracted",
    )

    typer.echo(f"Output: {output_path}")
    typer.echo(
        f"Characters extracted: {character_count}"
    )

@app.command()
def status():
    """Show basic catalogue status."""

    database = sqlite3.connect(DATABASE_PATH)

    total_files = database.execute(
        "SELECT COUNT(*) FROM files"
    ).fetchone()[0]

    extracted_files = database.execute(
        """
        SELECT COUNT(*)
        FROM files
        WHERE processing_status IN (
            'text_extracted',
            'text_chunked',
            'embedded'
        )
        """
    ).fetchone()[0]

    database.close()

    typer.echo(f"Catalogue files: {total_files}")
    typer.echo(f"Text extracted: {extracted_files}")


@app.command("quality")
def inspect_quality(
    file_id: int = typer.Argument(
        help="Catalogue file ID."
    ),
    preview_characters: int = typer.Option(
        800,
        "--preview-characters",
        min=0,
        help="Number of extracted characters to preview.",
    ),
):
    """Inspect extracted-text quality without changing anything."""

    get_file(file_id)
    text_path = TEXT_DIR / f"{file_id}.txt"

    if not text_path.exists():
        raise typer.BadParameter(
            "Text has not been extracted. "
            f"Run: python library.py extract {file_id}"
        )

    text = text_path.read_text(
        encoding="utf-8",
        errors="replace",
    )
    analysis = analyze_document_quality(text)

    typer.echo(f"File ID: {file_id}")
    typer.echo(
        f"Characters: {analysis['character_count']}"
    )
    typer.echo(f"Words: {analysis['word_count']}")
    typer.echo(
        f"Unique words: {analysis['unique_word_count']} "
        f"({analysis['unique_word_ratio']:.1%})"
    )
    typer.echo(f"Nonempty lines: {analysis['line_count']}")
    typer.echo(
        f"Unique lines: "
        f"{analysis['unique_line_ratio']:.1%}"
    )
    typer.echo(
        f"Alphabetic ratio: "
        f"{analysis['alphabetic_ratio']:.1%}"
    )
    typer.echo(
        f"URLs: {analysis['url_count']} "
        f"({analysis['urls_per_1000_characters']:.1f} "
        "per 1,000 characters)"
    )
    typer.echo()
    typer.echo(quality_summary(analysis))

    if preview_characters:
        preview = text.strip()[:preview_characters]
        typer.echo()
        typer.echo("Preview:")
        typer.echo()
        typer.echo(preview)

        if len(text.strip()) > preview_characters:
            typer.echo("…")


@app.command("reclassify-failure")
def reclassify_failure(
    file_id: int = typer.Argument(
        help="Catalogue file ID."
    ),
):
    """Reclassify a stored ebook conversion error."""

    file_record = get_file(file_id)

    if file_record["extension"] not in EBOOK_FORMATS:
        raise typer.BadParameter(
            "Failure reclassification currently supports "
            "ebook formats only."
        )

    database = sqlite3.connect(DATABASE_PATH)
    row = database.execute(
        """
        SELECT
            processing_status,
            error_message
        FROM files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()
    database.close()

    current_status, error_message = row

    if not error_message:
        raise typer.BadParameter(
            "This file has no stored error message."
        )

    new_status = classify_ebook_conversion_error(
        error_message
    )
    set_processing_status(
        file_id,
        new_status,
        error_message,
    )

    typer.echo(f"File ID: {file_id}")
    typer.echo(f"Previous status: {current_status}")
    typer.echo(f"New status: {new_status}")


def ensure_chunk_schema(
    database: sqlite3.Connection,
):
    database.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            character_count INTEGER NOT NULL,

            UNIQUE(file_id, chunk_index),

            FOREIGN KEY (file_id)
                REFERENCES files(id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_file_id
        ON chunks(file_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(
            file_id UNINDEXED,
            chunk_index UNINDEXED,
            text
        );
        """
    )


def split_text(
    text: str,
    target_size: int = 4000,
    overlap: int = 400,
) -> list[str]:
    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        maximum_end = min(
            start + target_size,
            text_length,
        )

        end = maximum_end

        if maximum_end < text_length:
            minimum_break = start + int(
                target_size * 0.6
            )

            paragraph_break = text.rfind(
                "\n\n",
                minimum_break,
                maximum_end,
            )

            if paragraph_break != -1:
                end = paragraph_break

        chunk_text = text[start:end].strip()

        if chunk_text:
            chunks.append(chunk_text)

        if end >= text_length:
            break

        next_start = max(end - overlap, start + 1)

        if next_start <= start:
            next_start = end

        start = next_start

    return chunks


@app.command()
def chunk(
    file_id: int = typer.Argument(
        help="Catalogue file ID."
    ),
):
    """Divide extracted text into searchable chunks."""

    get_file(file_id)

    text_path = TEXT_DIR / f"{file_id}.txt"

    if not text_path.exists():
        raise typer.BadParameter(
            "Text has not been extracted. "
            f"Run: python library.py extract {file_id}"
        )

    text = text_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    chunks = split_text(text)

    if not chunks:
        raise typer.BadParameter(
            "No usable text was found."
        )

    database = sqlite3.connect(DATABASE_PATH)
    database.execute("PRAGMA foreign_keys = ON")
    ensure_chunk_schema(database)

    old_rows = database.execute(
        """
        SELECT id
        FROM chunks
        WHERE file_id = ?
        """,
        (file_id,),
    ).fetchall()

    for old_row in old_rows:
        database.execute(
            """
            DELETE FROM chunks_fts
            WHERE rowid = ?
            """,
            (old_row[0],),
        )

    database.execute(
        "DELETE FROM chunks WHERE file_id = ?",
        (file_id,),
    )

    for chunk_index, chunk_text in enumerate(chunks):
        cursor = database.execute(
            """
            INSERT INTO chunks (
                file_id,
                chunk_index,
                text,
                character_count
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                file_id,
                chunk_index,
                chunk_text,
                len(chunk_text),
            ),
        )

        database.execute(
            """
            INSERT INTO chunks_fts (
                rowid,
                file_id,
                chunk_index,
                text
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                cursor.lastrowid,
                file_id,
                chunk_index,
                chunk_text,
            ),
        )

    database.commit()
    database.close()

    set_processing_status(
        file_id,
        "text_chunked",
    )

    typer.echo(f"File ID: {file_id}")
    typer.echo(f"Chunks created: {len(chunks)}")
    typer.echo(
        f"Average characters: "
        f"{sum(map(len, chunks)) // len(chunks)}"
    )

@app.command()
def search(
    query: str = typer.Argument(
        help="Words or phrase to search for."
    ),
    limit: int = typer.Option(
        5,
        "--limit",
        "-n",
        help="Maximum number of results.",
    ),
):
    """Search indexed book passages."""

    database = sqlite3.connect(DATABASE_PATH)
    ensure_chunk_schema(database)

    try:
        rows = database.execute(
            """
            SELECT
                c.file_id,
                f.filename,
                c.chunk_index,
                snippet(
                    chunks_fts,
                    2,
                    '[[',
                    ']]',
                    ' … ',
                    30
                ) AS passage,
                bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks AS c
                ON c.id = chunks_fts.rowid
            JOIN files AS f
                ON f.id = c.file_id
            WHERE chunks_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()

    except sqlite3.OperationalError as error:
        database.close()
        raise typer.BadParameter(
            f"Invalid search query: {error}"
        )

    database.close()

    if not rows:
        typer.echo("No matching passages found.")
        raise typer.Exit()

    for position, row in enumerate(rows, start=1):
        file_id, filename, chunk_index, passage, score = row

        typer.echo()
        typer.echo(
            f"{position}. File {file_id}, "
            f"chunk {chunk_index}"
        )
        typer.echo(f"   {filename}")
        typer.echo(f"   Score: {score:.4f}")
        typer.echo()
        typer.echo(passage.strip())

def get_vector_collection():
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(
        path=str(VECTOR_DIR)
    )

    collection = client.get_or_create_collection(
        name="library_chunks",
        metadata={"hnsw:space": "cosine"},
    )

    return collection


@app.command("exclude")
def exclude_document(
    file_id: int = typer.Argument(
        help="Catalogue file ID."
    ),
    reason: str = typer.Option(
        ...,
        "--reason",
        help="Reason the document is excluded from search.",
    ),
    duplicate_of: int | None = typer.Option(
        None,
        "--duplicate-of",
        help=(
            "Canonical file ID when excluding an exact "
            "extracted-text duplicate."
        ),
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm removal of generated chunks and vectors.",
    ),
):
    """
    Exclude a non-library document from search.

    The original file and extracted text are preserved.
    """

    get_file(file_id)
    cleaned_reason = reason.strip()
    canonical_file = None

    if not cleaned_reason:
        raise typer.BadParameter(
            "--reason cannot be empty."
        )

    if duplicate_of is not None:
        if duplicate_of == file_id:
            raise typer.BadParameter(
                "A file cannot be a duplicate of itself."
            )

        canonical_file = get_file(duplicate_of)

    database = sqlite3.connect(DATABASE_PATH)
    database.execute("PRAGMA foreign_keys = ON")
    ensure_file_processing_schema(database)
    ensure_chunk_schema(database)

    exclusion_status = "excluded_non_library"

    if duplicate_of is not None:
        backfill_extracted_text_hashes(database)

        hashes = database.execute(
            """
            SELECT
                id,
                extracted_text_sha256
            FROM files
            WHERE id IN (?, ?)
            """,
            (
                file_id,
                duplicate_of,
            ),
        ).fetchall()
        hashes_by_id = {
            row[0]: row[1]
            for row in hashes
        }

        current_hash = hashes_by_id.get(file_id)
        canonical_hash = hashes_by_id.get(duplicate_of)

        if current_hash is None or canonical_hash is None:
            database.close()
            raise typer.BadParameter(
                "Both files must have extracted text before "
                "duplicate exclusion."
            )

        if current_hash != canonical_hash:
            database.close()
            raise typer.BadParameter(
                "The extracted-text SHA-256 hashes do not match."
            )

        exclusion_status = "excluded_duplicate"
        typer.echo(
            f"Exact duplicate of File {duplicate_of}: "
            f"{canonical_file['filename']}"
        )

    old_rows = database.execute(
        """
        SELECT id
        FROM chunks
        WHERE file_id = ?
        """,
        (file_id,),
    ).fetchall()
    database.close()

    collection = get_vector_collection()
    vector_records = collection.get(
        where={"file_id": file_id},
        include=[],
    )
    vector_count = len(vector_records["ids"])

    typer.echo(f"File ID: {file_id}")
    typer.echo(
        f"Generated chunks to remove: {len(old_rows)}"
    )
    typer.echo(
        f"Generated vectors to remove: {vector_count}"
    )
    typer.echo(
        "Original document: preserved"
    )
    typer.echo(
        "Extracted text: preserved"
    )

    if not yes:
        typer.echo()
        typer.echo(
            "No search data or status changes made. "
            "Re-run with --yes to confirm."
        )
        raise typer.Exit()

    collection.delete(
        where={"file_id": file_id}
    )

    database = sqlite3.connect(DATABASE_PATH)
    database.execute("PRAGMA foreign_keys = ON")
    ensure_chunk_schema(database)

    try:
        for old_row in old_rows:
            database.execute(
                """
                DELETE FROM chunks_fts
                WHERE rowid = ?
                """,
                (old_row[0],),
            )

        database.execute(
            "DELETE FROM chunks WHERE file_id = ?",
            (file_id,),
        )
        database.execute(
            """
            UPDATE files
            SET processing_status = ?,
                error_message = ?,
                duplicate_of_file_id = ?
            WHERE id = ?
            """,
            (
                exclusion_status,
                cleaned_reason,
                duplicate_of,
                file_id,
            ),
        )
        database.commit()

    except Exception:
        database.rollback()
        raise

    finally:
        database.close()

    typer.echo()
    typer.echo(
        "Document excluded from search. "
        "Original and extracted text were preserved."
    )


@app.command()
def embed(
    file_id: int = typer.Argument(
        help="Catalogue file ID."
    ),
    batch_size: int = typer.Option(
        16,
        "--batch-size",
        help="Chunks processed per Ollama request.",
    ),
):
    """Create semantic-search embeddings for a document."""

    file_record = get_file(file_id)

    database = sqlite3.connect(DATABASE_PATH)
    ensure_chunk_schema(database)

    rows = database.execute(
        """
        SELECT
            id,
            chunk_index,
            text
        FROM chunks
        WHERE file_id = ?
        ORDER BY chunk_index
        """,
        (file_id,),
    ).fetchall()

    database.close()

    if not rows:
        raise typer.BadParameter(
            "No chunks found. "
            f"Run: python library.py chunk {file_id}"
        )

    collection = get_vector_collection()

    collection.delete(
        where={"file_id": file_id}
    )

    total = len(rows)

    for batch_start in range(
        0,
        total,
        batch_size,
    ):
        batch = rows[
            batch_start:batch_start + batch_size
        ]

        chunk_ids = [
            row[0]
            for row in batch
        ]

        chunk_indexes = [
            row[1]
            for row in batch
        ]

        texts = [
            row[2]
            for row in batch
        ]

        result = ollama_embed(
            model=EMBEDDING_MODEL,
            input=texts,
        )

        collection.upsert(
            ids=[
                f"{file_id}:{chunk_index}"
                for chunk_index in chunk_indexes
            ],
            embeddings=result.embeddings,
            documents=texts,
            metadatas=[
                {
                    "file_id": file_id,
                    "chunk_id": chunk_id,
                    "chunk_index": chunk_index,
                    "filename": file_record["filename"],
                    "embedding_model": EMBEDDING_MODEL,
                }
                for chunk_id, chunk_index in zip(
                    chunk_ids,
                    chunk_indexes,
                )
            ],
        )

        completed = min(
            batch_start + batch_size,
            total,
        )

        typer.echo(
            f"Embedded {completed}/{total} chunks..."
        )

    set_processing_status(
        file_id,
        "embedded",
    )

    typer.echo()
    typer.echo(f"File ID: {file_id}")
    typer.echo(f"Embedded chunks: {total}")
    typer.echo(f"Model: {EMBEDDING_MODEL}")

@app.command("semantic")
def semantic_search(
    query: str = typer.Argument(
        help="Natural-language search question."
    ),
    limit: int = typer.Option(
        5,
        "--limit",
        "-n",
        help="Maximum number of results.",
    ),
):
    """Search by meaning rather than exact wording."""

    collection = get_vector_collection()

    if collection.count() == 0:
        raise typer.BadParameter(
            "The vector database is empty."
        )

    query_result = ollama_embed(
        model=EMBEDDING_MODEL,
        input=[query],
    )

    results = collection.query(
        query_embeddings=[
            query_result.embeddings[0]
        ],
        n_results=limit,
        include=[
            "documents",
            "metadatas",
            "distances",
        ],
    )

    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    if not documents:
        typer.echo("No semantic results found.")
        raise typer.Exit()

    for position, (
        document,
        metadata,
        distance,
    ) in enumerate(
        zip(
            documents,
            metadatas,
            distances,
        ),
        start=1,
    ):
        preview = " ".join(
            document.split()
        )[:600]

        typer.echo()
        typer.echo(
            f"{position}. File "
            f"{metadata['file_id']}, "
            f"chunk {metadata['chunk_index']}"
        )
        typer.echo(
            f"   {metadata['filename']}"
        )
        typer.echo(
            f"   Distance: {distance:.4f}"
        )
        typer.echo()
        typer.echo(preview)

        if len(document) > 600:
            typer.echo("…")

@app.command()
def ask(
    question: str = typer.Argument(
        help="Question to ask the indexed library."
    ),
    sources: int = typer.Option(
        5,
        "--sources",
        "-n",
        help=(
            "Number of retrieved passages, or passages "
            "per file when --file is used."
        ),
    ),
    file_ids: list[int] = typer.Option(
        [],
        "--file",
        help=(
            "Restrict retrieval to a catalogue file ID. "
            "Repeat this option to compare books."
        ),
    ),
):
    """Answer a question using retrieved library passages."""

    collection = get_vector_collection()

    if collection.count() == 0:
        raise typer.BadParameter(
            "The vector database is empty."
        )

    question_embedding = ollama_embed(
        model=EMBEDDING_MODEL,
        input=[question],
    )

    documents = []
    metadatas = []

    if file_ids:
        selected_file_ids = list(dict.fromkeys(file_ids))

        for file_id in selected_file_ids:
            get_file(file_id)

            file_results = collection.query(
                query_embeddings=[
                    question_embedding.embeddings[0]
                ],
                n_results=sources,
                where={
                    "file_id": file_id,
                },
                include=[
                    "documents",
                    "metadatas",
                    "distances",
                ],
            )

            file_documents = (
                file_results["documents"][0]
            )
            file_metadatas = (
                file_results["metadatas"][0]
            )

            if not file_documents:
                raise typer.BadParameter(
                    "No embedded passages were found "
                    f"for file ID {file_id}."
                )

            documents.extend(file_documents)
            metadatas.extend(file_metadatas)

    else:
        normalized_question = (
            f" {question.casefold()} "
        )
        comparison_markers = (
            " compare ",
            " comparing ",
            " contrast ",
            " versus ",
            " vs. ",
            " vs ",
            " difference between ",
            " differences between ",
        )
        comparison_mode = any(
            marker in normalized_question
            for marker in comparison_markers
        )

        candidate_count = sources

        if comparison_mode:
            candidate_count = min(
                max(sources * 6, 30),
                collection.count(),
            )

        results = collection.query(
            query_embeddings=[
                question_embedding.embeddings[0]
            ],
            n_results=candidate_count,
            include=[
                "documents",
                "metadatas",
                "distances",
            ],
        )

        candidate_documents = (
            results["documents"][0]
        )
        candidate_metadatas = (
            results["metadatas"][0]
        )

        if comparison_mode:
            per_file_limit = max(
                1,
                (sources + 1) // 2,
            )
            file_counts = {}
            deferred_candidates = []

            for document, metadata in zip(
                candidate_documents,
                candidate_metadatas,
            ):
                file_id = metadata["file_id"]
                current_count = file_counts.get(
                    file_id,
                    0,
                )

                if current_count < per_file_limit:
                    documents.append(document)
                    metadatas.append(metadata)
                    file_counts[file_id] = (
                        current_count + 1
                    )
                else:
                    deferred_candidates.append(
                        (document, metadata)
                    )

                if len(documents) >= sources:
                    break

            if len(documents) < sources:
                for document, metadata in deferred_candidates:
                    documents.append(document)
                    metadatas.append(metadata)

                    if len(documents) >= sources:
                        break

        else:
            documents = candidate_documents
            metadatas = candidate_metadatas

    if not documents:
        raise typer.BadParameter(
            "No relevant passages were found."
        )

    context_parts = []
    source_map = {}

    for source_number, (document, metadata) in enumerate(
        zip(documents, metadatas),
        start=1,
    ):
        source_label = f"S{source_number}"
        shortened_document = document[:2000]

        source_map[source_label] = (
            metadata["file_id"],
            metadata["chunk_index"],
        )

        context_parts.append(
            f"[{source_label}]\n"
            f"File: {metadata['file_id']}\n"
            f"Chunk: {metadata['chunk_index']}\n"
            f"Book: {metadata['filename']}\n"
            f"{shortened_document}"
        )

    context = "\n\n---\n\n".join(context_parts)
    allowed_labels = ", ".join(
        f"[{label}]" for label in source_map
    )

    response = ollama_chat(
        model="qwen3:8b",
        messages=[
            {
                "role": "system",
                "content": (
                    "Answer using only the supplied library "
                    "passages. Synthesize the material without "
                    "copying long passages. Cite claims using only "
                    "the supplied source labels, such as [S1]. "
                    "Do not write file or chunk numbers yourself. "
                    "If the passages do not contain enough evidence, "
                    "say so clearly. Do not invent an author's "
                    "position."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question:\n{question}\n\n"
                    f"Allowed source labels: {allowed_labels}\n\n"
                    f"Library passages:\n{context}"
                ),
            },
        ],
        options={
            "temperature": 0.1,
        },
        think=False,
    )

    answer = response.message.content

    cited_numbers = re.findall(
        r"\[S(\d+)\]",
        answer,
    )

    invalid_labels = {
        f"S{number}"
        for number in cited_numbers
        if f"S{number}" not in source_map
    }

    if invalid_labels or "[File " in answer:
        invalid_text = ", ".join(
            sorted(invalid_labels)
        ) or "direct file/chunk citation"

        raise typer.BadParameter(
            "The model produced an invalid citation: "
            f"{invalid_text}"
        )

    for label, (file_id, chunk_index) in source_map.items():
        answer = answer.replace(
            f"[{label}]",
            f"[File {file_id}, chunk {chunk_index}]",
        )

    typer.echo()
    typer.echo("Answer:")
    typer.echo()
    typer.echo(answer)

    typer.echo()
    typer.echo("Sources retrieved:")

    for metadata in metadatas:
        typer.echo(
            f"  File {metadata['file_id']}, "
            f"chunk {metadata['chunk_index']}"
        )

@app.command()
def process(
    file_id: int = typer.Argument(
        help="Catalogue file ID."
    ),
    batch_size: int = typer.Option(
        16,
        "--batch-size",
    ),
    minimum_characters: int = typer.Option(
        0,
        "--minimum-characters",
        help=(
            "Reject extracted text shorter than this. "
            "Zero disables the check."
        ),
    ),
    allow_quality_review: bool = typer.Option(
        False,
        "--allow-quality-review",
        help=(
            "Continue despite automatic quality-review "
            "signals after manually inspecting the text."
        ),
    ),
):
    """Extract, chunk and embed one document."""

    get_file(file_id)

    text_path = TEXT_DIR / f"{file_id}.txt"

    if text_path.exists():
        typer.echo("Text already extracted.")
    else:
        extract(file_id)

    extracted_text = text_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).strip()

    if minimum_characters > 0:
        if len(extracted_text) < minimum_characters:
            error = (
                "Extracted text is too short: "
                f"{len(extracted_text)} characters; "
                f"minimum is {minimum_characters}."
            )

            set_processing_status(
                file_id,
                "text_too_short",
                error,
            )

            raise typer.BadParameter(error)

    quality_analysis = analyze_document_quality(
        extracted_text
    )
    quality_error = quality_summary(quality_analysis)

    if quality_analysis["review_reasons"]:
        if not allow_quality_review:
            set_processing_status(
                file_id,
                "quality_review",
                quality_error,
            )
            raise typer.BadParameter(
                quality_error
                + " Inspect it with: "
                f"python library.py quality {file_id}"
            )

        typer.echo(
            "Quality warning accepted: "
            + quality_error
        )

    duplicate = find_exact_text_duplicate(file_id)

    if duplicate is not None:
        duplicate_message = (
            "Exact extracted-text duplicate of "
            f"File {duplicate['file_id']} "
            f"({duplicate['filename']}); "
            f"SHA-256 {duplicate['sha256']}."
        )
        set_duplicate_status(
            file_id,
            duplicate["file_id"],
            duplicate_message,
        )
        raise typer.BadParameter(duplicate_message)

    database = sqlite3.connect(DATABASE_PATH)
    ensure_chunk_schema(database)

    chunk_count = database.execute(
        """
        SELECT COUNT(*)
        FROM chunks
        WHERE file_id = ?
        """,
        (file_id,),
    ).fetchone()[0]

    database.close()

    if chunk_count:
        typer.echo(
            f"Chunks already exist: {chunk_count}"
        )
    else:
        chunk(file_id)

    collection = get_vector_collection()

    existing_vectors = collection.get(
        where={"file_id": file_id},
        limit=1,
    )

    if existing_vectors["ids"]:
        typer.echo("Embeddings already exist.")
        set_processing_status(
            file_id,
            "embedded",
        )
    else:
        embed(
            file_id=file_id,
            batch_size=batch_size,
        )

    typer.echo()
    typer.echo(
        f"Processing complete for file {file_id}."
    )


@app.command()
def batch(
    limit: int = typer.Option(
        10,
        "--limit",
        "-n",
        help="Maximum number of ebooks to process.",
    ),
    batch_size: int = typer.Option(
        16,
        "--batch-size",
        help="Chunks processed per embedding request.",
    ),
    minimum_characters: int = typer.Option(
        1000,
        "--minimum-characters",
        help=(
            "Reject documents whose extracted text is "
            "shorter than this."
        ),
    ),
    retry_failed: bool = typer.Option(
        False,
        "--retry-failed",
        help="Retry documents that failed previously.",
    ),
):
    """Process a resumable batch of ebooks."""

    if limit < 1:
        raise typer.BadParameter(
            "--limit must be at least 1."
        )

    if minimum_characters < 0:
        raise typer.BadParameter(
            "--minimum-characters cannot be negative."
        )

    excluded_statuses = sorted(
        PERMANENT_BATCH_EXCLUSIONS
    )

    if not retry_failed:
        excluded_statuses.extend(
            [
                "text_too_short",
                "text_extraction_failed",
                "ebook_conversion_failed",
                "malformed_ebook",
                "processing_failed",
            ]
        )

    extensions = sorted(EBOOK_FORMATS)
    extension_placeholders = ", ".join(
        "?" for _ in extensions
    )
    status_placeholders = ", ".join(
        "?" for _ in excluded_statuses
    )

    database = sqlite3.connect(DATABASE_PATH)

    rows = database.execute(
        f"""
        SELECT
            id,
            filename
        FROM files
        WHERE extension IN ({extension_placeholders})
          AND COALESCE(processing_status, '')
              NOT IN ({status_placeholders})
        ORDER BY id
        LIMIT ?
        """,
        (
            *extensions,
            *excluded_statuses,
            limit,
        ),
    ).fetchall()

    database.close()

    if not rows:
        typer.echo(
            "No eligible ebooks remain in this batch."
        )
        raise typer.Exit()

    completed = 0
    failed = 0
    too_short = 0
    review_required = 0
    drm_protected = 0
    duplicates = 0

    for position, (file_id, filename) in enumerate(
        rows,
        start=1,
    ):
        typer.echo()
        typer.echo(
            f"[{position}/{len(rows)}] "
            f"File {file_id}: {filename}"
        )

        try:
            process(
                file_id=file_id,
                batch_size=batch_size,
                minimum_characters=minimum_characters,
                allow_quality_review=False,
            )
            completed += 1

        except Exception as error:
            current_status = get_processing_status(
                file_id
            )

            if current_status == "text_too_short":
                too_short += 1
                typer.echo(
                    f"Skipped: {error}"
                )

            elif current_status == "quality_review":
                review_required += 1
                typer.echo(
                    f"Review required: {error}"
                )

            elif current_status == "drm_protected":
                drm_protected += 1
                typer.echo(
                    "Skipped: DRM-protected ebook."
                )

            elif current_status == "excluded_duplicate":
                duplicates += 1
                typer.echo(
                    f"Skipped duplicate: {error}"
                )

            elif current_status in {
                "ebook_conversion_failed",
                "malformed_ebook",
                "text_extraction_failed",
            }:
                failed += 1
                typer.echo(
                    f"Failed: {error}"
                )

            else:
                failed += 1
                set_processing_status(
                    file_id,
                    "processing_failed",
                    str(error),
                )
                typer.echo(
                    f"Failed: {error}"
                )

    typer.echo()
    typer.echo("Batch complete.")
    typer.echo(f"  Embedded: {completed}")
    typer.echo(f"  Too short: {too_short}")
    typer.echo(
        f"  Quality review: {review_required}"
    )
    typer.echo(
        f"  DRM-protected: {drm_protected}"
    )
    typer.echo(f"  Duplicates: {duplicates}")
    typer.echo(f"  Failed: {failed}")


if __name__ == "__main__":
    app()

