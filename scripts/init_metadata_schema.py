from pathlib import Path
import sqlite3


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data/catalog/library.sqlite3"


SCHEMA = """
CREATE TABLE IF NOT EXISTS file_metadata (
    file_id INTEGER PRIMARY KEY,

    embedded_metadata_json TEXT,
    filename_metadata_json TEXT,
    llm_resolution_json TEXT,

    resolved_title TEXT,
    resolved_authors_json TEXT,
    resolved_language TEXT,
    resolved_publisher TEXT,
    resolved_date TEXT,

    resolution_method TEXT,
    confidence TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT,

    FOREIGN KEY (file_id)
        REFERENCES files(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_file_metadata_status
ON file_metadata(status);

CREATE INDEX IF NOT EXISTS idx_file_metadata_title
ON file_metadata(resolved_title);
"""


def main():
    database = sqlite3.connect(DATABASE_PATH)
    database.execute("PRAGMA foreign_keys = ON")
    database.executescript(SCHEMA)
    database.commit()

    columns = database.execute(
        "PRAGMA table_info(file_metadata)"
    ).fetchall()

    database.close()

    print("Metadata table created.")
    print("Columns:")

    for column in columns:
        print(f"  {column[1]}")


if __name__ == "__main__":
    main()
