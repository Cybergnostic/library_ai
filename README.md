# Local Library AI

A private, local-first system for cataloguing, searching, and asking questions
across a personal collection of books and documents.

The project combines deterministic file-processing tools with local language
models:

- Python scans files, extracts text, manages the catalogue, and controls the
  pipeline.
- Calibre extracts and converts ebook formats.
- PyMuPDF extracts text from PDFs.
- OCRmyPDF and Tesseract handle scanned PDFs.
- SQLite provides the catalogue and exact full-text search.
- Chroma stores semantic-search vectors.
- Qwen3 Embedding creates document embeddings.
- Qwen3 8B answers questions using retrieved passages.

Original books remain in their existing locations. Generated OCR copies,
extracted text, database records, chunks, and embeddings are stored inside this
project.

## Current status

The local RAG pipeline is working end to end:

```text
Book
→ text extraction
→ document-quality checks
→ duplicate detection
→ text chunks
→ embeddings
→ semantic retrieval
→ Qwen answer
→ validated source references
```

Current catalogue state after the latest 200-file batch:

| Processing status | Files |
| --- | ---: |
| `embedded` | 1,039 |
| `discovered` | 4,838 |
| `excluded_duplicate` | 16 |
| `drm_protected` | 2 |
| `quality_review` | 2 |
| `excluded_non_library` | 1 |
| **Total** | **5,898** |

Approximately 17.6% of the catalogue is embedded. The main task now is to
continue resumable batch processing until the `discovered` count reaches zero.
The exceptional queues can then be reviewed separately.

The latest 200-file run completed successfully in:

```text
real    107m28.424s
user      5m53.319s
sys       0m44.333s
```

At that observed rate, a 1,000-file batch may take roughly nine hours, although
runtime varies greatly with book size, format, conversion difficulty, and
system load.

The system now supports:

- Inventorying files from several configured locations.
- Recording paths, formats, sizes, hashes, and processing states in SQLite.
- Identifying PDFs that probably require OCR.
- Producing searchable OCR derivatives without overwriting originals.
- Extracting text from PDF, EPUB, MOBI, AZW, and AZW3 files.
- Running document-quality checks before embedding.
- Detecting exact extracted-text duplicates with SHA-256.
- Marking duplicate copies as `excluded_duplicate` without deleting originals.
- Removing chunks and vectors belonging to excluded duplicate copies.
- Dividing extracted text into overlapping passages.
- Performing exact SQLite FTS5 search.
- Performing semantic search with local embeddings.
- Asking Qwen3 8B questions grounded in retrieved book passages.
- Returning validated file and chunk references with generated answers.
- Resuming batch processing without repeating completed files.
- Recording DRM, quality-review, duplicate, and non-library outcomes separately.
- Continuing after individual failures instead of stopping the whole batch.
- Checking SQLite, FTS5, and Chroma consistency.

The last full integrity check reported equal SQLite chunk, FTS row, and Chroma
vector counts, with no orphan chunks and no duplicate chunk keys. This should be
rerun periodically as the collection grows.

## Latest handoff checkpoint

The current `library.py` is a working local pipeline with these main commands:

```text
extract
status
chunk
search
embed
semantic
ask
process
batch
```

The current operational focus is large, resumable ebook indexing. A typical run
that displays output live and saves the complete output and timing information is:

```bash
mkdir -p logs

{ time python -u library.py batch --limit 200; } \
  2>&1 | tee logs/batch_$(date +%Y%m%d_%H%M%S).log
```

`tee` sends the same output to both the terminal and the log file. Python's `-u`
flag prevents delayed buffered output during long unattended runs.

For a larger unattended run:

```bash
{ time python -u library.py batch --limit 1000; } \
  2>&1 | tee logs/batch_1000_$(date +%Y%m%d_%H%M%S).log
```

The batch is resumable. Files already marked `embedded` or otherwise resolved
are skipped. If the process is interrupted, a later batch continues with the
remaining eligible `discovered` files.

Current state:

```text
Catalogue files:        5,898
Embedded:               1,039
Discovered:             4,838
Excluded duplicates:      16
DRM protected:              2
Quality review:             2
Excluded non-library:       1
```

The duplicate-detection and cleanup workflow has been tested successfully. An
exact duplicate was identified by extracted-text SHA-256, one copy was retained,
the other was marked `excluded_duplicate`, and its SQLite chunks and Chroma
vectors were removed without touching either original book file.

The immediate practical milestone is to finish the ordinary `discovered` ebook
queue, then review the small exceptional queues. After that, development should
shift from raw indexing toward retrieval quality, source planning, summaries,
OCR integration, status reporting, and maintainability.

## Environment

Development machine:

- LMDE / Debian-based Linux
- NVIDIA GeForce RTX 3060 with 8 GB VRAM
- 32 GB RAM
- Python 3.13.5

Installed local models:

```text
qwen3:8b
gemma3:4b
qwen3-embedding:0.6b
```

Observed software versions:

```text
Ollama:       0.32.1
Chroma:       1.5.9
Calibre:      8.5
OCRmyPDF:     16.7.0
Tesseract:    5.5.0
```

Installed Tesseract languages:

```text
eng
srp
srp_latn
osd
```

Additional language packages can be installed later when a book requires
them. Multiple OCR languages may be supplied with syntax such as
`srp+eng+deu+hun`, but adding unnecessary languages can reduce accuracy and
increase processing time.

## Project structure

```text
library_ai/
├── README.md
├── library.py
├── config/
│   └── roots.yaml
├── data/
│   ├── catalog/
│   │   └── library.sqlite3
│   ├── extracted_text/
│   ├── ocr_copies/
│   │   └── rejected/
│   ├── summaries/
│   ├── vector_db/
│   └── cache/
├── logs/
├── scripts/
├── tests/
└── venv/
```

The `scripts/` directory currently contains development and diagnostic tools.
The main user-facing application is `library.py`.

## Long-run logging and hardware monitoring

Long batch output should be shown in the terminal and saved at the same time:

```bash
{ time python -u library.py batch --limit 200; } \
  2>&1 | tee logs/batch_$(date +%Y%m%d_%H%M%S).log
```

A separate shell monitor records CPU temperature, CPU usage, RAM usage, NVMe
temperatures, GPU temperature, and GPU utilization to timestamped CSV files.
The resulting CSV can be graphed with Python and Matplotlib to inspect the whole
run rather than only the final temperature.

This monitoring already demonstrated a clear thermal improvement after airflow
around the open side of the case was restored and unnecessary applications were
closed. For long unattended runs, leave clear space around the case, make sure
the monitor is logging, and verify that temperatures remain stable before
starting a very large batch.

Useful generated records are:

```text
logs/batch_YYYYMMDD_HHMMSS.log
monitor_logs/monitor_YYYYMMDD_HHMMSS.csv
```

A future helper should combine the batch log and monitor CSV into one compact
run report containing duration, throughput, maximum and average temperatures,
CPU/GPU utilization, RAM use, and failures.

## Source locations

The configured library roots are:

```text
/home/cyber/Downloads/books
/home/cyber
/mnt/storage/SAVED/Astrology ebooks
/mnt/storage/SAVED/Knjige
```

The first, third, and fourth roots are scanned recursively without a depth
limit. `/home/cyber` is scanned only at its top level so that the application
does not crawl every home directory, project, cache, and download twice.

Configuration is stored in:

```text
config/roots.yaml
```

Hidden paths and symbolic links are excluded by default.

## Adding new books and rescanning roots

The current system does not continuously watch the configured folders. A newly
downloaded book is invisible to `library.py batch` until the inventory and
catalogue are scanned again.

Current workflow:

```text
new book added to a configured root
→ rerun inventory/catalogue update
→ new database row marked `discovered`
→ run `library.py batch`
→ extract, validate, deduplicate, chunk, and embed
```

Current development scripts are:

```bash
python scripts/inventory.py
python scripts/build_catalog.py
python library.py status
```

Existing embedded files should remain unchanged. A genuinely new file should be
added as `discovered`; an exact duplicate should later be marked
`excluded_duplicate`.

A planned production command is:

```bash
python library.py sync
```

It should rescan all roots incrementally and report:

```text
New files
Changed files
Missing or unavailable files
Already known files
```

Disconnected external drives must be treated as temporarily unavailable rather
than as proof that files were deleted.

## Library inventory

The initial scan found 5,898 supported files:

| Format | Count |
| --- | ---: |
| MOBI | 3,573 |
| PDF | 1,049 |
| AZW3 | 641 |
| EPUB | 595 |
| AZW | 16 |
| RTF | 14 |
| TXT | 7 |
| DOCX | 3 |

The catalogue is stored at:

```text
data/catalog/library.sqlite3
```

The current `files` table records:

- Stable database ID
- Root ID
- Full path
- Filename and extension
- File size
- Modification time
- Optional hash
- Processing status
- OCR inspection results
- Error information

## PDF and OCR audit

All 1,049 PDFs were inspected:

| OCR status | Count |
| --- | ---: |
| Usable text available | 921 |
| OCR clearly needed | 102 |
| Mixed text and image pages | 5 |
| Manual review | 11 |
| Empty or zero-page PDF | 5 |
| Inspection failed | 5 |

OCR status is determined from page-level text and image evidence. A document
marked `ocr_needed` has little or no readable text and is predominantly
image-based.

The audit is deliberately conservative:

- Mixed documents are not automatically OCRed.
- Existing searchable PDFs are left untouched.
- Empty, damaged, encrypted, and unusual PDFs are recorded for review.
- OCR output is written to `data/ocr_copies/`.
- Original PDFs are never overwritten.

### Important OCR lesson

Some PDFs contain a broken hidden OCR layer. In that situation,
`--skip-text` may incorrectly skip pages even when extracted text is unusable.
Those files require:

```text
--redo-ocr
```

OCRmyPDF 16.7 does not permit `--redo-ocr` together with `--deskew`.

Some malformed PDFs also fail during PDF/A conversion because of damaged
fonts, bookmarks, or annotations. For this library, a normal searchable PDF is
enough, so these files can use:

```text
--output-type pdf
```

The Hamvas OCR test demonstrated all three cases:

- A pre-existing broken text layer
- The need to choose Serbian Cyrillic rather than Serbian Latin
- A Ghostscript failure during unnecessary PDF/A conversion

Raw OCR text must be preserved even if a later model creates a cleaned version.
LLM-cleaned text can improve reading and summaries, but it must never replace
the auditable raw extraction.

## Metadata

The metadata layer currently collects candidates from:

- Embedded ebook or PDF metadata
- Filenames
- Extracted document openings
- Manual rules
- Constrained Qwen resolution when sources conflict

The `file_metadata` table stores raw candidates, resolved values, method,
confidence, and status.

Metadata cannot be trusted blindly:

- A PDF may have title and author fields reversed.
- Language metadata may describe the wrong edition.
- Filenames may contain incorrect book information.
- A local 8B model may confidently invent an author when asked an
  unconstrained question.

The intended evidence order is:

```text
Embedded metadata
+ filename candidates
+ title-page or document-opening evidence
→ deterministic agreement or correction rules
→ constrained LLM choice only when necessary
→ manual review if still uncertain
```

The model should choose between evidence-backed candidates or return
`uncertain`. It should not freely propose a new title or author.

### Astrology lecture authorship rule

Astrology lecture notes and lesson transcripts in this collection are assigned
to:

```text
Bojan Šojić
```

The rule is stored in `config/roots.yaml` and applies to documents identified
as astrology lecture notes or lesson transcripts. It does not apply to
published astrology books.

## Main commands

Activate the project environment first:

```bash
cd /home/cyber/projects/library_ai
source venv/bin/activate
```

Show available commands:

```bash
python library.py --help
```

### Status

```bash
python library.py status
```

Shows basic catalogue and processing information.

### Extract text

```bash
python library.py extract FILE_ID
```

Examples:

```bash
python library.py extract 19
python library.py extract 36
```

For PDFs, the command uses an available OCR derivative. It refuses to extract a
PDF marked `ocr_needed` when no OCR copy exists.

For EPUB, MOBI, AZW, and AZW3, Calibre converts the document to plain text.

Generated files are stored as:

```text
data/extracted_text/FILE_ID.txt
```

PDF extraction preserves page markers:

```text
[[PAGE 1]]
```

Ebook extraction currently does not preserve reliable printed page numbers.

### Create chunks

```bash
python library.py chunk FILE_ID
```

Current chunking settings:

```text
Target size: 4,000 characters
Overlap:       400 characters
```

Chunks prefer paragraph boundaries where possible. They are stored in SQLite
and indexed in an FTS5 table for exact search.

### Create embeddings

```bash
python library.py embed FILE_ID
```

Optional batch size:

```bash
python library.py embed FILE_ID --batch-size 16
```

Embeddings are generated with:

```text
qwen3-embedding:0.6b
```

Each vector has 1,024 dimensions and is stored in the persistent Chroma
collection `library_chunks`.

### Process one document completely

```bash
python library.py process FILE_ID
```

This command performs:

```text
extract → chunk → embed
```

It skips stages whose output already exists.

Optional minimum extracted-text check:

```bash
python library.py process FILE_ID --minimum-characters 1000
```

### Process a resumable ebook batch

```bash
python library.py batch --limit 200
```

The batch command:

- Processes eligible EPUB, MOBI, AZW, and AZW3 files.
- Selects files currently marked `discovered`.
- Skips files already marked `embedded`.
- Skips files already classified as duplicates, DRM-protected, non-library, or
  quality-review unless an explicit maintenance or retry command is used.
- Runs extraction, quality checks, duplicate detection, chunking, and embedding.
- Records a terminal status and continues when one file cannot be processed.
- Can be interrupted and resumed without restarting completed files.

Useful commands:

```bash
python library.py batch --limit 10
python library.py batch --limit 200
python library.py batch --limit 10 --minimum-characters 2000
python library.py batch --limit 10 --retry-failed
```

For live terminal output plus a saved timestamped log:

```bash
{ time python -u library.py batch --limit 200; } \
  2>&1 | tee logs/batch_$(date +%Y%m%d_%H%M%S).log
```

A large batch such as `--limit 1000` is reasonable now that multiple batches and
integrity checks have completed successfully, but it should still be monitored
for temperature, disk space, and unexpected repeated failures.
### Exact full-text search

```bash
python library.py search "tradition apocalypse"
```

Optional result limit:

```bash
python library.py search tradition --limit 10
```

Exact search uses SQLite FTS5 and BM25 ranking. It is useful for:

- Names
- Exact phrases
- Rare terminology
- Technical vocabulary
- Direct word matches

### Semantic search

```bash
python library.py semantic \
  "What happens to Christian tradition in the modern world?"
```

Semantic search embeds the question and retrieves conceptually related chunks
from Chroma, even when the question does not use the book's exact wording.

### Ask the library

```bash
python library.py ask \
  "What happens to Christian tradition in the modern world?"
```

The `ask` command:

1. Embeds the question.
2. Retrieves the most relevant passages.
3. Detects explicit comparison wording and widens retrieval when needed.
4. Limits domination by one file during those comparisons.
5. Trims the evidence to fit the local model's context.
6. Gives the passages to Qwen3 8B.
7. Requires the answer to use only supplied material.
8. Validates simple internal source labels.
9. Converts valid labels into citations such as `[File 19, chunk 93]`.
10. Prints the retrieved source list.

For precise comparisons, restrict retrieval explicitly:

```bash
python library.py ask \
  "Compare these accounts of consciousness." \
  --file 27 \
  --file 38 \
  --sources 3
```

When `--file` is repeated, `--sources` means passages per selected file.
Without `--file`, explicit comparison language triggers automatic diversified
retrieval. The current automatic detection is heuristic, not a complete
book-selection system.

The generated answer is grounded, but it is not guaranteed to be a perfect
interpretation. Important claims should be checked against the cited chunks or
the original document.

## Development and diagnostic scripts

The following scripts were created while testing individual parts:

```text
scripts/inventory.py
scripts/build_catalog.py
scripts/test_pdf_audit.py
scripts/audit_pdfs.py
scripts/ocr_one.py
scripts/test_llm_metadata.py
scripts/inspect_metadata.py
scripts/resolve_metadata_test.py
scripts/classify_document_test.py
scripts/extract_ebook_one.py
scripts/init_metadata_schema.py
scripts/collect_metadata.py
```

These scripts document useful experiments, but they are not all production
commands. Their stable functionality should gradually move into `library.py`.

Notable lessons from the experiments:

- Filename-only LLM metadata extraction is unreliable.
- Free metadata generation allowed Qwen to invent an author.
- Constrained candidate selection worked substantially better.
- Document classification is not yet reliable enough to run blindly.
- Bibliographic perfection should not block extraction and search.

## Database and indexes

SQLite currently contains:

- `files` — inventory and processing state
- `file_metadata` — metadata evidence and resolutions
- `chunks` — extracted text passages
- `chunks_fts` — exact full-text index

Chroma contains:

- Collection: `library_chunks`
- Chunk text
- File ID
- SQLite chunk ID
- Chunk index
- Filename
- Embedding-model identifier

Stable Chroma IDs use:

```text
FILE_ID:CHUNK_INDEX
```

The vector database is regenerable. Manual metadata corrections, annotations,
and database records should be backed up.

## Safety and data-preservation rules

- Never overwrite original books.
- Never automatically delete suspected duplicates.
- Treat disconnected external drives as unavailable, not deleted.
- Preserve raw OCR and raw extracted text.
- Write OCR derivatives into the project.
- Record errors instead of stopping the whole library scan.
- Do not trust embedded metadata, filenames, OCR, or LLM output as individually
  authoritative.
- Keep model names, prompts, and processing versions so generated data can be
  reproduced.

## Known limitations

The current system is a functioning large-scale prototype, not yet a complete
library manager.

- 1,039 files are embedded, while 4,838 remain `discovered`.
- Batch processing currently concentrates on ebooks; PDF/OCR automation is not
  yet integrated into the same unattended pipeline.
- DRM-protected books require a legitimate non-DRM source copy before they can
  be extracted.
- Quality heuristics can still send unusual but valid documents to manual
  review or miss subtle low-quality conversion output.
- Automatic OCR-language selection still requires human judgment.
- TXT, DOCX, and RTF extraction are not fully integrated into the main batch.
- Metadata is incomplete and sometimes contradictory.
- Exact duplicate detection exists, but edition, translation, and near-duplicate
  handling remain distinct unsolved problems.
- Ebook page-number citations are generally unavailable.
- Chunking may include tables of contents, indexes, covers, and back matter.
- The `ask` command is not yet a full hybrid FTS5-plus-vector retrieval system.
- Retrieval may return overlapping or semantically repetitive chunks.
- General questions do not yet use a robust document-level source planner.
- Qwen3 8B may oversimplify difficult arguments or sound more certain than the
  evidence allows.
- Stronger remote or hosted models cannot directly use Chroma by themselves;
  the Python application must retrieve passages and send those passages to the
  chosen model.
- New books are not detected automatically until the configured roots are
  rescanned.
- `library.py` is still a large script and should gradually be split into
  focused modules.
- There is no graphical interface.

## Recommended next steps

### 1. Finish ordinary ebook indexing

Continue resumable batches until `discovered` reaches zero. Then handle the
small exceptional queues separately:

- `excluded_duplicate`: normally no action is required after integrity checks.
- `drm_protected`: obtain a legitimate extractable copy if available.
- `quality_review`: inspect manually and either approve, exclude, or reprocess.
- `excluded_non_library`: leave excluded unless classification was incorrect.

### 2. Expand `status` into a real dashboard

`python library.py status` should show:

```text
Total catalogue files
Discovered
Extracted
Chunked
Embedded
Duplicates
DRM protected
Quality review
Failed
SQLite chunks
FTS rows
Chroma vectors
Orphan chunks
Duplicate chunk keys
Completion percentage
```

It should also calculate remaining work using `processing_status='discovered'`,
not only `NULL` values.

### 3. Add an incremental `sync` command

Implement:

```bash
python library.py sync
```

It should rescan configured roots, add new files, detect changed paths or file
contents, retain stable IDs where possible, and distinguish unavailable drives
from deleted files.

### 4. Refactor `library.py` into modules

Keep one CLI entry point while moving implementation into focused modules, for
example:

```text
library_ai/
├── cli.py
├── catalog.py
├── extraction.py
├── quality.py
├── duplicates.py
├── chunking.py
├── embeddings.py
├── retrieval.py
├── answering.py
├── maintenance.py
└── monitoring.py
```

Refactor gradually, preserving behavior and tests after each move. Do not rewrite
the whole system at once.

### 5. Integrate OCR into the main pipeline

Add:

```bash
python library.py ocr FILE_ID --language srp
```

The command should select `--skip-text` or `--redo-ocr` from inspection evidence,
fall back to ordinary searchable PDF output when PDF/A is unnecessary, validate
page count and extracted text, preserve failed output, and record tool versions,
language, mode, and status.

### 6. Add hybrid retrieval and reranking

Merge SQLite FTS5 keyword retrieval with Chroma semantic retrieval, normalize
scores, remove duplicate or highly overlapping passages, and rerank the final
evidence before sending it to the answer model.

### 7. Add document-level source planning

Maintain a separate book-level index with title, author, subjects, summaries,
and representative embeddings. A complex question should first select likely
books and then retrieve chunks only inside those books.

This is important for essay writing, multi-author comparisons, and questions
that require broad coverage rather than a few locally similar passages.

### 8. Add hierarchical summaries and structured subjects

Generate and store:

```text
chunk summaries
→ section or chapter summaries
→ book summary
```

Also store one-sentence descriptions, catalogue summaries, detailed summaries,
subjects, tags, model name, prompt version, and review state. Raw extracted text
must remain authoritative.

### 9. Support stronger answer models cleanly

The local RAG should remain model-independent. Python retrieves evidence from
SQLite and Chroma, builds a bounded context package, and sends it to whichever
model is configured:

```text
local Qwen or Gemma
hosted ChatGPT/Codex workflow
Gemini or another API
future larger local model
```

The stronger model does not read Chroma directly. The application is responsible
for retrieval, citation labels, context construction, and validation.

### 10. Improve duplicate, edition, and work identity handling

Keep exact file duplicates separate from bibliographic relationships:

```text
work
edition or translation
digital manifestation
physical file location
```

Do not merge different translations or editions automatically. Add near-duplicate
analysis only after exact duplicate behavior remains stable.

### 11. Add automated tests and integrity commands

Create tests for extraction, quality classification, duplicate cleanup, chunk
boundaries, FTS search, vector retrieval, source citations, batch resumability,
and incremental syncing.

Add a maintenance command that verifies:

```text
SQLite chunks == FTS rows == Chroma vectors
no orphan chunks
no duplicate stable vector IDs
no vectors for excluded duplicates
all embedded files have extracted text
```

### 12. Add run reports and performance comparison

Combine batch logs and monitoring CSV files into a report with:

```text
start and end time
run duration
files attempted and embedded
files per hour
characters and chunks processed
failure categories
maximum and average CPU/GPU/NVMe temperatures
CPU, GPU, and RAM utilization
```

This will make model, batch-size, and performance changes objectively comparable.

### 13. Add project maintenance

Recommended additions:

```text
pyproject.toml or requirements.txt
.gitignore
Git repository
database backup command
configuration backup
schema migrations
prompt-version tracking
structured application logs
```

Do not commit:

```text
venv/
data/vector_db/
data/cache/
OCR derivatives
copyrighted extracted book text
```

## Project direction

This project is not intended to make a small local model memorize an entire
library. It gives the model tools to retrieve relevant evidence when needed.

The valuable part is the complete local system:

```text
Private files
+ reliable extraction
+ OCR
+ exact search
+ semantic search
+ grounded generation
+ traceable sources
```

The next practical milestone is completing the ordinary ebook queue, followed
by a proper status dashboard, incremental syncing, modularization, hybrid
retrieval, and document-level source planning.
