"""SQLite Database storage for Telegram channels, messages, and media."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from models import ChannelInfo, MediaItem, Message


def _to_int(val: Any) -> Optional[int]:
    """Safely convert value to integer or None."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


class Database:
    """Manages SQLite storage for crawled Telegram channels, messages, and media."""

    def __init__(self, db_path: str = "crawler.db") -> None:
        """Initialize database connection.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = str(db_path)
        # Ensure parent directory exists if nested
        db_parent = Path(self.db_path).parent
        if db_parent and not db_parent.exists():
            db_parent.mkdir(parents=True, exist_ok=True)

        self._conn: Optional[sqlite3.Connection] = None
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Get or create the SQLite database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency and integrity
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    def init_db(self) -> None:
        """Initialize database tables, migrations, and indexes."""
        conn = self.get_connection()
        with conn:
            # 1. Base table creation
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    username TEXT,
                    description TEXT,
                    member_count INTEGER,
                    is_broadcast INTEGER NOT NULL DEFAULT 1,
                    is_megagroup INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    sender_id INTEGER,
                    date TEXT NOT NULL,
                    text TEXT,
                    views INTEGER,
                    forwards INTEGER,
                    replies_count INTEGER,
                    reply_to_msg_id INTEGER,
                    media_type TEXT,
                    edit_date TEXT,
                    post_author TEXT,
                    is_pinned INTEGER NOT NULL DEFAULT 0,
                    grouped_id INTEGER,
                    crawl_status TEXT NOT NULL DEFAULT 'success',
                    PRIMARY KEY (channel_id, id),
                    FOREIGN KEY (channel_id) REFERENCES channels (id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    telegram_message_id INTEGER NOT NULL,
                    grouped_id INTEGER,
                    media_type TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    mime_type TEXT,
                    file_size INTEGER,
                    width INTEGER,
                    height INTEGER,
                    duration INTEGER,
                    thumbnail_path TEXT,
                    download_status TEXT NOT NULL DEFAULT 'pending',
                    download_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (channel_id, telegram_message_id) REFERENCES messages (channel_id, id) ON DELETE CASCADE,
                    UNIQUE (channel_id, telegram_message_id, file_name)
                );
                """
            )

            # 2. Migrations for existing tables before index creation
            cursor = conn.execute("PRAGMA table_info(messages);")
            columns = [row["name"] for row in cursor.fetchall()]
            if "grouped_id" not in columns:
                conn.execute("ALTER TABLE messages ADD COLUMN grouped_id INTEGER;")
            if "crawl_status" not in columns:
                conn.execute("ALTER TABLE messages ADD COLUMN crawl_status TEXT NOT NULL DEFAULT 'success';")

            # 3. Create indexes now that all columns are guaranteed to exist
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_channel_date
                    ON messages(channel_id, date DESC);

                CREATE INDEX IF NOT EXISTS idx_messages_channel_id_desc
                    ON messages(channel_id, id DESC);

                CREATE INDEX IF NOT EXISTS idx_messages_grouped
                    ON messages(channel_id, grouped_id);

                CREATE INDEX IF NOT EXISTS idx_media_channel_msg
                    ON media(channel_id, telegram_message_id);

                CREATE INDEX IF NOT EXISTS idx_media_status
                    ON media(download_status);

                CREATE INDEX IF NOT EXISTS idx_media_grouped
                    ON media(grouped_id);
                """
            )

    def save_channel(self, channel: ChannelInfo) -> None:
        """Insert or update channel details."""
        conn = self.get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO channels (
                    id, title, username, description, member_count,
                    is_broadcast, is_megagroup, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    username = excluded.username,
                    description = excluded.description,
                    member_count = excluded.member_count,
                    is_broadcast = excluded.is_broadcast,
                    is_megagroup = excluded.is_megagroup,
                    updated_at = excluded.updated_at;
                """,
                (
                    channel.id,
                    channel.title,
                    channel.username,
                    channel.description,
                    channel.member_count,
                    1 if channel.is_broadcast else 0,
                    1 if channel.is_megagroup else 0,
                    channel.created_at,
                    channel.updated_at,
                ),
            )

    def get_channel(self, channel_id: int) -> Optional[ChannelInfo]:
        """Retrieve channel by ID."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return ChannelInfo(
            id=row["id"],
            title=row["title"],
            username=row["username"],
            description=row["description"],
            member_count=row["member_count"],
            is_broadcast=bool(row["is_broadcast"]),
            is_megagroup=bool(row["is_megagroup"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_channel_by_username(self, username: str) -> Optional[ChannelInfo]:
        """Retrieve channel by username (case-insensitive, optional @)."""
        clean_user = username.lstrip("@").lower()
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM channels WHERE LOWER(username) = ?", (clean_user,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return ChannelInfo(
            id=row["id"],
            title=row["title"],
            username=row["username"],
            description=row["description"],
            member_count=row["member_count"],
            is_broadcast=bool(row["is_broadcast"]),
            is_megagroup=bool(row["is_megagroup"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save_message(self, message: Message) -> None:
        """Insert or update a single message."""
        self.save_messages([message])

    def save_messages(self, messages: List[Message]) -> int:
        """Insert or update a batch of messages."""
        if not messages:
            return 0

        conn = self.get_connection()
        data = [
            (
                m.id,
                m.channel_id,
                _to_int(m.sender_id),
                str(m.date or ""),
                m.text,
                _to_int(m.views),
                _to_int(m.forwards),
                _to_int(m.replies_count),
                _to_int(m.reply_to_msg_id),
                m.media_type,
                m.edit_date,
                m.post_author,
                1 if m.is_pinned else 0,
                _to_int(m.grouped_id),
                m.crawl_status or "success",
            )
            for m in messages
        ]

        with conn:
            conn.executemany(
                """
                INSERT INTO messages (
                    id, channel_id, sender_id, date, text, views,
                    forwards, replies_count, reply_to_msg_id,
                    media_type, edit_date, post_author, is_pinned,
                    grouped_id, crawl_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, id) DO UPDATE SET
                    sender_id = excluded.sender_id,
                    date = excluded.date,
                    text = excluded.text,
                    views = excluded.views,
                    forwards = excluded.forwards,
                    replies_count = excluded.replies_count,
                    reply_to_msg_id = excluded.reply_to_msg_id,
                    media_type = excluded.media_type,
                    edit_date = excluded.edit_date,
                    post_author = excluded.post_author,
                    is_pinned = excluded.is_pinned,
                    grouped_id = excluded.grouped_id,
                    crawl_status = excluded.crawl_status;
                """,
                data,
            )
        return len(messages)

    def get_messages(
        self, channel_id: int, limit: int = 100, offset: int = 0
    ) -> List[Message]:
        """Fetch messages for a channel ordered by date descending."""
        conn = self.get_connection()
        cursor = conn.execute(
            """
            SELECT * FROM messages
            WHERE channel_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?;
            """,
            (channel_id, limit, offset),
        )
        rows = cursor.fetchall()
        return [
            Message(
                id=r["id"],
                channel_id=r["channel_id"],
                sender_id=r["sender_id"],
                date=r["date"],
                text=r["text"],
                views=r["views"],
                forwards=r["forwards"],
                replies_count=r["replies_count"],
                reply_to_msg_id=r["reply_to_msg_id"],
                media_type=r["media_type"],
                edit_date=r["edit_date"],
                post_author=r["post_author"],
                is_pinned=bool(r["is_pinned"]),
                grouped_id=r["grouped_id"] if "grouped_id" in r.keys() else None,
                crawl_status=r["crawl_status"] if "crawl_status" in r.keys() else "success",
            )
            for r in rows
        ]

    def get_message(self, channel_id: int, message_id: int) -> Optional[Message]:
        """Fetch a single message by channel ID and message ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM messages WHERE channel_id = ? AND id = ?",
            (channel_id, message_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return Message(
            id=row["id"],
            channel_id=row["channel_id"],
            sender_id=row["sender_id"],
            date=row["date"],
            text=row["text"],
            views=row["views"],
            forwards=row["forwards"],
            replies_count=row["replies_count"],
            reply_to_msg_id=row["reply_to_msg_id"],
            media_type=row["media_type"],
            edit_date=row["edit_date"],
            post_author=row["post_author"],
            is_pinned=bool(row["is_pinned"]),
            grouped_id=row["grouped_id"] if "grouped_id" in row.keys() else None,
            crawl_status=row["crawl_status"] if "crawl_status" in row.keys() else "success",
        )

    def get_message_count(self, channel_id: int) -> int:
        """Count total messages stored for a channel."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE channel_id = ?", (channel_id,)
        )
        return cursor.fetchone()[0]

    def get_latest_message_id(self, channel_id: int) -> Optional[int]:
        """Get the highest message ID crawled for a channel."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT MAX(id) FROM messages WHERE channel_id = ?", (channel_id,)
        )
        val = cursor.fetchone()[0]
        return int(val) if val is not None else None

    def get_oldest_message_id(self, channel_id: int) -> Optional[int]:
        """Get the lowest message ID crawled for a channel."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT MIN(id) FROM messages WHERE channel_id = ?", (channel_id,)
        )
        val = cursor.fetchone()[0]
        return int(val) if val is not None else None

    # --- Media Operations ---

    def save_media(self, media_item: MediaItem) -> int:
        """Insert or update a media record."""
        now_iso = datetime.now(timezone.utc).isoformat()
        created_at = media_item.created_at or now_iso
        updated_at = now_iso

        conn = self.get_connection()
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO media (
                    message_id, channel_id, telegram_message_id, grouped_id,
                    media_type, file_name, file_path, mime_type, file_size,
                    width, height, duration, thumbnail_path, download_status,
                    download_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, telegram_message_id, file_name) DO UPDATE SET
                    file_path = excluded.file_path,
                    mime_type = excluded.mime_type,
                    file_size = excluded.file_size,
                    width = excluded.width,
                    height = excluded.height,
                    duration = excluded.duration,
                    thumbnail_path = excluded.thumbnail_path,
                    download_status = excluded.download_status,
                    download_error = excluded.download_error,
                    updated_at = excluded.updated_at;
                """,
                (
                    _to_int(media_item.message_id or media_item.telegram_message_id),
                    media_item.channel_id,
                    media_item.telegram_message_id,
                    _to_int(media_item.grouped_id),
                    media_item.media_type,
                    media_item.file_name,
                    media_item.file_path,
                    media_item.mime_type,
                    _to_int(media_item.file_size),
                    _to_int(media_item.width),
                    _to_int(media_item.height),
                    _to_int(media_item.duration),
                    media_item.thumbnail_path,
                    media_item.download_status,
                    media_item.download_error,
                    created_at,
                    updated_at,
                ),
            )
            return cursor.lastrowid or 0

    def get_media_for_message(
        self, channel_id: int, message_id: int
    ) -> List[MediaItem]:
        """Fetch all media records for a specific message."""
        conn = self.get_connection()
        cursor = conn.execute(
            """
            SELECT * FROM media
            WHERE channel_id = ? AND telegram_message_id = ?
            ORDER BY id ASC;
            """,
            (channel_id, message_id),
        )
        rows = cursor.fetchall()
        return [self._row_to_media(r) for r in rows]

    def get_media_by_id(self, media_id: int) -> Optional[MediaItem]:
        """Fetch media record by primary ID."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,))
        row = cursor.fetchone()
        return self._row_to_media(row) if row else None

    def get_media_by_file_path(self, file_path: str) -> Optional[MediaItem]:
        """Fetch media record by file path."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT * FROM media WHERE file_path = ?", (file_path,))
        row = cursor.fetchone()
        return self._row_to_media(row) if row else None

    def get_failed_media(
        self, channel_id: Optional[int] = None
    ) -> List[MediaItem]:
        """Fetch media items with status 'failed' or 'missing'."""
        conn = self.get_connection()
        if channel_id is not None:
            cursor = conn.execute(
                """
                SELECT * FROM media
                WHERE channel_id = ? AND download_status IN ('failed', 'missing')
                ORDER BY telegram_message_id ASC;
                """,
                (channel_id,),
            )
        else:
            cursor = conn.execute(
                """
                SELECT * FROM media
                WHERE download_status IN ('failed', 'missing')
                ORDER BY telegram_message_id ASC;
                """
            )
        return [self._row_to_media(r) for r in cursor.fetchall()]

    def get_all_media(
        self, channel_id: int, limit: int = 100, offset: int = 0
    ) -> List[MediaItem]:
        """Fetch all media records for a channel with pagination."""
        conn = self.get_connection()
        cursor = conn.execute(
            """
            SELECT * FROM media
            WHERE channel_id = ?
            ORDER BY telegram_message_id DESC, id ASC
            LIMIT ? OFFSET ?;
            """,
            (channel_id, limit, offset),
        )
        return [self._row_to_media(r) for r in cursor.fetchall()]

    def update_media_status(
        self,
        media_id: int,
        status: str,
        error: Optional[str] = None,
        file_size: Optional[int] = None,
        file_path: Optional[str] = None,
    ) -> None:
        """Update download status and details of a media item."""
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        with conn:
            conn.execute(
                """
                UPDATE media
                SET download_status = ?,
                    download_error = ?,
                    file_size = COALESCE(?, file_size),
                    file_path = COALESCE(?, file_path),
                    updated_at = ?
                WHERE id = ?;
                """,
                (status, error, file_size, file_path, now_iso, media_id),
            )

    def get_crawl_stats(self, channel_id: int) -> Dict[str, Any]:
        """Compute aggregated statistics for messages and media."""
        conn = self.get_connection()

        # Messages stats
        msg_cur = conn.execute(
            """
            SELECT
                SUM(CASE WHEN crawl_status = 'success' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN crawl_status = 'deleted' THEN 1 ELSE 0 END) AS deleted_count,
                SUM(CASE WHEN crawl_status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                COUNT(*) AS total_count
            FROM messages
            WHERE channel_id = ?;
            """,
            (channel_id,),
        )
        msg_row = msg_cur.fetchone()

        # Media stats
        med_cur = conn.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN download_status = 'downloaded' THEN 1 ELSE 0 END) AS downloaded_count,
                SUM(CASE WHEN download_status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                SUM(CASE WHEN download_status = 'missing' THEN 1 ELSE 0 END) AS missing_count,
                SUM(CASE WHEN download_status = 'pending' THEN 1 ELSE 0 END) AS pending_count
            FROM media
            WHERE channel_id = ?;
            """,
            (channel_id,),
        )
        med_row = med_cur.fetchone()

        return {
            "messages": {
                "total": msg_row["total_count"] or 0,
                "success": msg_row["success_count"] or 0,
                "deleted": msg_row["deleted_count"] or 0,
                "failed": msg_row["failed_count"] or 0,
            },
            "media": {
                "total": med_row["total_count"] or 0,
                "downloaded": med_row["downloaded_count"] or 0,
                "failed": med_row["failed_count"] or 0,
                "missing": med_row["missing_count"] or 0,
                "pending": med_row["pending_count"] or 0,
            },
        }

    def _row_to_media(self, r: sqlite3.Row) -> MediaItem:
        """Convert a database row to a MediaItem instance."""
        return MediaItem(
            id=r["id"],
            message_id=r["message_id"],
            channel_id=r["channel_id"],
            telegram_message_id=r["telegram_message_id"],
            grouped_id=r["grouped_id"],
            media_type=r["media_type"],
            file_name=r["file_name"],
            file_path=r["file_path"],
            mime_type=r["mime_type"],
            file_size=r["file_size"],
            width=r["width"],
            height=r["height"],
            duration=r["duration"],
            thumbnail_path=r["thumbnail_path"],
            download_status=r["download_status"],
            download_error=r["download_error"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    def close(self) -> None:
        """Close database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
