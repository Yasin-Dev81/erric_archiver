"""Unit tests for SQLite database operations."""

import sqlite3
import pytest
from database import Database
from models import ChannelInfo, MediaItem, Message


@pytest.fixture
def db(tmp_path):
    """Provide a fresh SQLite database in a temporary directory."""
    db_file = tmp_path / "test_crawler.db"
    database = Database(db_path=str(db_file))
    yield database
    database.close()


def test_channel_crud(db):
    """Test inserting, retrieving, and updating channel data."""
    channel = ChannelInfo(
        id=1001,
        title="Test Channel",
        username="testchannel",
        description="A channel for testing",
        member_count=123,
    )
    db.save_channel(channel)

    fetched = db.get_channel(1001)
    assert fetched is not None
    assert fetched.title == "Test Channel"
    assert fetched.username == "testchannel"
    assert fetched.member_count == 123

    # By username lookup (with and without @)
    by_user = db.get_channel_by_username("@testchannel")
    assert by_user is not None
    assert by_user.id == 1001

    # Update channel
    channel.title = "Updated Channel Title"
    channel.member_count = 150
    db.save_channel(channel)

    updated = db.get_channel(1001)
    assert updated is not None
    assert updated.title == "Updated Channel Title"
    assert updated.member_count == 150


def test_messages_crud_and_upsert(db):
    """Test saving single and batch messages, upserting duplicates, and pagination."""
    channel = ChannelInfo(id=2001, title="Msg Channel", username="msgchannel")
    db.save_channel(channel)

    messages = [
        Message(
            id=1,
            channel_id=2001,
            date="2025-01-01T10:00:00Z",
            text="First message",
            views=10,
        ),
        Message(
            id=2,
            channel_id=2001,
            date="2025-01-01T11:00:00Z",
            text="Second message",
            views=25,
        ),
        Message(
            id=3,
            channel_id=2001,
            date="2025-01-01T12:00:00Z",
            text="Third message",
            views=50,
        ),
    ]

    count = db.save_messages(messages)
    assert count == 3
    assert db.get_message_count(2001) == 3

    # Check order (descending ID)
    stored = db.get_messages(2001, limit=2)
    assert len(stored) == 2
    assert stored[0].id == 3
    assert stored[1].id == 2

    # Check bounds
    assert db.get_latest_message_id(2001) == 3
    assert db.get_oldest_message_id(2001) == 1

    # Test Upsert: update text and views of message 2
    updated_msg = Message(
        id=2,
        channel_id=2001,
        date="2025-01-01T11:00:00Z",
        text="Edited second message",
        views=99,
    )
    db.save_message(updated_msg)

    # Total count shouldn't change
    assert db.get_message_count(2001) == 3

    # Fetch updated message
    res = [m for m in db.get_messages(2001, limit=10) if m.id == 2][0]
    assert res.text == "Edited second message"
    assert res.views == 99


def test_media_crud_and_crawl_stats(db):
    """Test media records persistence, status updates, and stats aggregation."""
    channel = ChannelInfo(id=3001, title="Media Stats Channel")
    db.save_channel(channel)

    # Message 10 with media
    db.save_message(Message(id=10, channel_id=3001, crawl_status="success"))
    item1 = MediaItem(
        message_id=10,
        channel_id=3001,
        telegram_message_id=10,
        media_type="photo",
        file_name="photo_10.jpg",
        file_path="media/photo_10.jpg",
        download_status="downloaded",
        file_size=5000,
    )
    db.save_media(item1)

    # Message 20 with failed media
    db.save_message(Message(id=20, channel_id=3001, crawl_status="success"))
    item2 = MediaItem(
        message_id=20,
        channel_id=3001,
        telegram_message_id=20,
        media_type="video",
        file_name="video_20.mp4",
        file_path="media/video_20.mp4",
        download_status="failed",
        download_error="Timeout",
    )
    db.save_media(item2)

    # Message 30 text only
    db.save_message(Message(id=30, channel_id=3001, crawl_status="success"))

    # Message 40 deleted
    db.save_message(Message(id=40, channel_id=3001, crawl_status="deleted"))

    failed = db.get_failed_media(3001)
    assert len(failed) == 1
    assert failed[0].telegram_message_id == 20

    stats = db.get_crawl_stats(3001)
    assert stats["messages"]["total"] == 4
    assert stats["messages"]["success"] == 3
    assert stats["messages"]["deleted"] == 1
    assert stats["messages"]["failed"] == 0

    assert stats["media"]["total"] == 2
    assert stats["media"]["downloaded"] == 1
    assert stats["media"]["failed"] == 1
    assert stats["media"]["missing"] == 0


def test_database_migration(tmp_path):
    """Test schema migration when older schema without grouped_id is loaded."""
    db_file = tmp_path / "legacy.db"
    raw_conn = sqlite3.connect(str(db_file))
    # Create older schema without grouped_id or crawl_status
    raw_conn.executescript(
        """
        CREATE TABLE channels (
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

        CREATE TABLE messages (
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
            PRIMARY KEY (channel_id, id)
        );
        """
    )
    raw_conn.close()

    # Opening with Database should run migration
    db = Database(str(db_file))
    conn = db.get_connection()
    cursor = conn.execute("PRAGMA table_info(messages);")
    columns = [row["name"] for row in cursor.fetchall()]
    assert "grouped_id" in columns
    assert "crawl_status" in columns
    db.close()
