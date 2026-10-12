# Telegram Crawler

A high-performance, resilient Telegram channel message crawler and media archiver built with [Telethon](https://github.com/LonamiWebs/Telethon) and SQLite, managed strictly using [uv](https://docs.astral.sh/uv/).

---

## Prerequisites & Requirements

> [!IMPORTANT]
> **`uv` is strictly required.**
>
> This project uses **uv** as the single tool for Python version management, virtual environment creation, dependency resolution, and script execution.
>
> **Do NOT use `pip`, `pipenv`, `poetry`, or manually create/activate a virtual environment (`python -m venv` / `source venv/bin/activate`).** All operations are executed through `uv`.

### Installing `uv`

If you do not have `uv` installed, install it via the official installer:

- **macOS / Linux**:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Homebrew (macOS)**:
  ```bash
  brew install uv
  ```
- **Windows**:
  ```powershell
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

Verify installation:
```bash
uv --version
```

---

## Project Structure

```text
telegram-crawler/
├── pyproject.toml         # Project metadata, dependencies, uv & pytest settings
├── uv.lock                # Pinned, deterministic dependency lockfile
├── .python-version        # Pinned Python version (3.12)
├── .env                   # Local configuration & credentials (git-ignored)
├── .env.example           # Environment template with configuration keys
├── .gitignore             # Git ignore rules for environments, sessions, databases, and media
├── crawler.py             # CLI entrypoint and crawler orchestration
├── media_downloader.py    # Media detection, atomic streaming downloads, and retry logic
├── config.py              # Environment and credential management
├── database.py            # SQLite persistence layer, schema migrations, and indexing
├── telegram_client.py     # Telethon client wrapper, rate-limiting & pagination
├── models.py              # ChannelInfo, Message, and MediaItem dataclass models
├── logger.py              # Standardized logger setup
├── README.md              # Complete project documentation
├── media/                 # Local directory for downloaded media files (git-ignored)
└── tests/                 # Automated test suite executed via pytest
    ├── __init__.py
    ├── test_config.py
    ├── test_models.py
    ├── test_database.py
    ├── test_telegram_client.py
    ├── test_media_downloader.py
    └── test_crawler.py
```

---

## Getting Started: Clean Setup

Follow these exact steps from a clean clone of the repository:

### 1. Synchronize Dependencies

Run `uv sync` to install pinned dependencies and create the `.venv` automatically:

```bash
uv sync
```

`uv` downloads the pinned Python version (if not present) and installs exact versions from `uv.lock`.

### 2. Configure Credentials

Copy the `.env.example` file to `.env`:

```bash
cp .env.example .env
```

Open `.env` and configure your Telegram credentials and crawler settings:

```env
# Telegram API Credentials (obtain from https://my.telegram.org)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_PHONE=+1234567890
TELEGRAM_SESSION_NAME=telegram_crawler

# Channel and Crawler Configuration
DEFAULT_CHANNEL=anarconomy
DATABASE_PATH=crawler.db
BATCH_SIZE=100
RATE_LIMIT_DELAY=1.0
LOG_LEVEL=INFO

# Media Download Configuration
DOWNLOAD_MEDIA=true
MEDIA_DIRECTORY=media
MEDIA_MAX_RETRIES=3
MEDIA_RETRY_DELAY=5
```

#### Obtaining Telegram API Credentials:
1. Log in to [https://my.telegram.org](https://my.telegram.org) with your Telegram account phone number.
2. Navigate to **API development tools**.
3. Create a new application (if you haven't already).
4. Copy `api_id` into `TELEGRAM_API_ID` and `api_hash` into `TELEGRAM_API_HASH`.

---

## First-Time Interactive Authentication

When running the crawler for the first time on a new system or with a fresh session name:

1. Launch the crawler:
   ```bash
   uv run python crawler.py
   ```
2. Telethon connects to Telegram's MTProto servers. Because no active session exists yet, Telethon detects that authorization is required and begins an interactive terminal login prompt:
   - **Phone number**: Enter your Telegram phone number with international country code (e.g., `+1234567890`) if not pre-configured in `.env`.
   - **Confirmation code**: Telegram sends a login code (usually to your active Telegram app on phone/desktop or SMS). Enter this code in the terminal.
   - **Two-Step Verification (2FA)**: If you have a cloud password enabled on your Telegram account, you will be prompted to enter your 2FA password.
3. Upon successful login, Telethon saves an encrypted session file (`telegram_crawler.session`).
4. **Subsequent runs will connect automatically** without asking for codes or passwords as long as the `.session` file is present.

---

## Media Downloading Architecture

### Supported Media Types

The crawler inspects Telethon media and document attributes to accurately identify:
* **`photo`**: Telegram photos (`.jpg`)
* **`video`**: Standard videos (`.mp4`, `.mov`, `.mkv`)
* **`voice`**: Voice messages (`.ogg`)
* **`audio`**: Music and audio tracks (`.mp3`, `.m4a`, `.flac`)
* **`video_note`**: Round videos / video notes (`.mp4`)
* **`animation`**: GIFs and looped animations (`.mp4`, `.gif`)
* **`document`**: Generic files and documents (`.pdf`, `.zip`, etc.)
* **Albums / Grouped Media**: Automatically downloads every media item in grouped posts without duplicates.

Text-only messages are crawled without interruption.

### Storage Hierarchy

Downloaded media is organized predictably by channel username (or ID) and message ID:

```text
media/
└── <channel_username>/
    ├── 123/
    │   └── photo_123.jpg
    ├── 124/
    │   └── video_124.mp4
    ├── 125/
    │   └── voice_125.ogg
    ├── 126/
    │   └── video_note_126.mp4
    └── 127/
        ├── photo_127_1.jpg
        └── photo_127_2.jpg
```

### Reliability & Integrity Features

1. **Atomic Downloads**: Media is streamed directly to a `.part` temporary file (e.g. `video_124.mp4.part`) and renamed to `video_124.mp4` only after the file is completely written and verified on disk.
2. **Memory Efficiency**: Media files are streamed in chunks directly to disk rather than loaded into RAM, supporting large videos safely.
3. **Resumable Downloads**: If a file already exists on disk and is marked `downloaded` in SQLite, it is not downloaded again.
4. **Disk Space Protection**: Verifies available disk space before writing, failing gracefully with an error if disk space is below safety limits without corrupting the database.
5. **Separate Crawl & Media Status**: A media download failure never crashes the crawler. The message is marked `crawl_status = success` while the media record is marked `download_status = failed`.
6. **Automatic Recovery**: On startup, missing local files for previously downloaded records are marked `missing`, while valid files found on disk with failed statuses are recovered to `downloaded`.

---

## Running the Application

All commands must be executed using `uv run`.

### Default Crawl

Crawl the default channel configured in `.env` and download media:

```bash
uv run python crawler.py
```

### Crawl a Specific Channel

```bash
uv run python crawler.py --channel anarconomy
```

Pass channel usernames (`anarconomy`, `@anarconomy`), links (`https://t.me/anarconomy`), or numeric IDs (`-1001234567890`).

### Disable or Enable Media Downloads

```bash
# Disable media downloading (crawl text and metadata only)
uv run python crawler.py --channel anarconomy --no-download-media

# Explicitly enable media downloading
uv run python crawler.py --channel anarconomy --download-media
```

### Re-Download Failed Media (`--retry-media`)

Retry all previously failed or missing media downloads without re-crawling the entire channel:

```bash
uv run python crawler.py --channel anarconomy --retry-media
```

Output:
```text
============================================================
 RETRY MEDIA: Anarconomy
============================================================
 Found 35 media item(s) to retry.

============================================================
Media retry completed
============================================================
Total failed media: 35
Retried: 35
Downloaded: 29
Still failed: 6
============================================================
```

### View Channel Info Only

```bash
uv run python crawler.py --channel anarconomy --info-only
```

### Limit Number of Messages

```bash
uv run python crawler.py --channel anarconomy --limit 50
```

### Incremental Crawling

Resumes from where you left off by querying SQLite for the latest stored message ID and only fetching newer posts:

```bash
uv run python crawler.py --channel anarconomy --incremental
```

### Export to JSON or CSV

```bash
# Export to JSON
uv run python crawler.py --channel anarconomy --export messages.json

# Export to CSV
uv run python crawler.py --channel anarconomy --export messages.csv
```

### Export to EPUB

Builds an e-book from the crawled database. Only downloaded **photos** are embedded; other media (videos, audio, voice, documents) are skipped, but their captions are kept. Albums are merged into one post, and chapters are grouped by month.

```bash
# Whole channel in one book
uv run python epub_exporter.py --channel anarconomy -o anarconomy.epub

# One book per year (anarconomy_2018.epub, anarconomy_2019.epub, ...)
uv run python epub_exporter.py --channel anarconomy -o anarconomy.epub --split-by-year

# Date range
uv run python epub_exporter.py --channel anarconomy --since 2020-01-01 --until 2021-01-01
```

### Custom Media Directory and Rate Limiting

```bash
uv run python crawler.py --channel anarconomy --media-dir custom_media --batch-size 50 --rate-limit-delay 1.5
```

### CLI Help and All Options

```bash
uv run python crawler.py --help
```

---

## Running Tests

Execute the automated test suite using `uv run pytest`:

```bash
uv run pytest
```

Run with verbose test reporting:

```bash
uv run pytest -v
```

All 33 unit tests execute offline without requiring real Telegram credentials or actual network connections.

---

## Database Schema

SQLite uses Write-Ahead Logging (`WAL`) mode for high concurrency and performance.

### Tables

1. **`channels`**:
   - `id`: Telegram channel ID (Primary Key)
   - `title`: Channel display name
   - `username`: Channel @username
   - `description`: Channel bio / about text
   - `member_count`: Subscriber count
   - `is_broadcast`: Whether entity is a broadcast channel
   - `is_megagroup`: Whether entity is a supergroup
   - `created_at`, `updated_at`: ISO 8601 timestamps

2. **`messages`**:
   - `(channel_id, id)`: Composite Primary Key
   - `sender_id`: User or channel sender ID
   - `date`: Publication timestamp
   - `text`: Message body text or caption
   - `views`: View count
   - `forwards`: Forward count
   - `replies_count`: Comments/replies count
   - `reply_to_msg_id`: ID of parent message
   - `media_type`: Detected media type string
   - `edit_date`: Timestamp of last edit
   - `post_author`: Author sign if enabled
   - `is_pinned`: Pinned flag
   - `grouped_id`: Telegram album / media group identifier
   - `crawl_status`: `success` | `deleted` | `failed`

3. **`media`**:
   - `id`: Integer Primary Key (Autoincrement)
   - `message_id`: Message reference ID
   - `channel_id`: Target channel ID
   - `telegram_message_id`: Telegram message ID
   - `grouped_id`: Album group ID if part of an album
   - `media_type`: `photo` | `video` | `voice` | `audio` | `video_note` | `animation` | `document`
   - `file_name`: Safe, deterministic filename
   - `file_path`: Local filesystem path
   - `mime_type`: Original MIME type
   - `file_size`: Size in bytes
   - `width`, `height`: Media dimensions (for photos/videos)
   - `duration`: Length in seconds (for audio/video)
   - `thumbnail_path`: Thumbnail path if available
   - `download_status`: `downloaded` | `failed` | `missing` | `pending`
   - `download_error`: Last error message if failed
   - `created_at`, `updated_at`: ISO 8601 timestamps
   - `UNIQUE(channel_id, telegram_message_id, file_name)`

---

## Progress Reporting Example

```text
------------------------------------------------------------
 CRAWL SUMMARY
------------------------------------------------------------
Messages:
  Success: 12000
  Deleted: 850
  Failed: 10

Media:
  Total:      7300
  Downloaded: 7150
  Failed:     120
  Missing:    30
------------------------------------------------------------
```
