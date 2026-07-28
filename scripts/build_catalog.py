from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from inventory import load_config, scan_root


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data" / "catalog" / "library.sqlite3"


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    root_id TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL,
    sha256 TEXT,
    processing_status TEXT NOT NULL DEFAULT 'discovered',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_root_id
ON files(root_id);

CREATE INDEX IF NOT EXISTS idx_files_extension
ON files(extension);

CREATE INDEX IF NOT EXISTS idx_files_status
ON files(processing_status);

CREATE INDEX IF NOT EXISTS idx_files_sha256
ON files(sha256);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_database() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)

    return connection


def main():
    config = load_config()

    extensions = {
        extension.lower()
        for extension in config["extensions"]
    }

    scan_options = config.get("scan", {})
    include_hidden = scan_options.get("include_hidden", False)
    follow_symlinks = scan_options.get("follow_symlinks", False)

    connection = open_database()
    timestamp = utc_now()

    inserted = 0
    updated = 0
    failed = 0

    for root_config in config["roots"]:
        root_id = root_config["id"]
        root = Path(root_config["path"])
        recursive = root_config.get("recursive", True)

        if not root.exists() or not root.is_dir():
            print(f"[UNAVAILABLE] {root_id}: {root}")
            continue

        print(f"Scanning {root_id}: {root}")

        for path in scan_root(
            root=root,
            recursive=recursive,
            extensions=extensions,
            include_hidden=include_hidden,
            follow_symlinks=follow_symlinks,
        ):
            try:
                stat = path.stat()
                path_string = str(path.resolve())

                existing = connection.execute(
                    "SELECT id FROM files WHERE path = ?",
                    (path_string,),
                ).fetchone()

                connection.execute(
                    """
                    INSERT INTO files (
                        root_id,
                        path,
                        filename,
                        extension,
                        size_bytes,
                        modified_ns,
                        first_seen,
                        last_seen
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        root_id = excluded.root_id,
                        filename = excluded.filename,
                        extension = excluded.extension,
                        size_bytes = excluded.size_bytes,
                        modified_ns = excluded.modified_ns,
                        last_seen = excluded.last_seen,
                        error_message = NULL
                    """,
                    (
                        root_id,
                        path_string,
                        path.name,
                        path.suffix.lower(),
                        stat.st_size,
                        stat.st_mtime_ns,
                        timestamp,
                        timestamp,
                    ),
                )

                if existing:
                    updated += 1
                else:
                    inserted += 1

            except (OSError, PermissionError) as error:
                failed += 1
                print(f"  [FAILED] {path}: {error}")

        connection.commit()

    total = connection.execute(
        "SELECT COUNT(*) FROM files"
    ).fetchone()[0]

    print()
    print(f"Database: {DATABASE_PATH}")
    print(f"Inserted: {inserted}")
    print(f"Updated: {updated}")
    print(f"Failed: {failed}")
    print(f"Total catalogue files: {total}")

    print()
    print("Formats:")

    rows = connection.execute(
        """
        SELECT extension, COUNT(*)
        FROM files
        GROUP BY extension
        ORDER BY extension
        """
    ).fetchall()

    for extension, count in rows:
        print(f"  {extension}: {count}")

    connection.close()


if __name__ == "__main__":
    main()
