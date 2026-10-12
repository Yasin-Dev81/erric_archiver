"""Unit tests for crawler CLI logic and export functions."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from config import Config
from crawler import crawl_channel, export_messages, parse_args
from database import Database
from models import ChannelInfo, MediaItem, Message


def test_parse_args_defaults():
    """Verify CLI argument defaults."""
    args = parse_args([])
    assert args.channel is None
    assert args.limit is None
    assert args.min_id == 0
    assert args.max_id == 0
    assert args.incremental is False
    assert args.export is None
    assert args.download_media is None
    assert args.no_download_media is False
    assert args.media_dir is None
    assert args.retry_media is False


def test_parse_args_custom_values():
    """Verify custom CLI arguments."""
    args = parse_args(
        [
            "--channel",
            "anarconomy",
            "--limit",
            "50",
            "--min-id",
            "100",
            "--incremental",
            "--export",
            "out.json",
            "--download-media",
            "--media-dir",
            "custom_media",
            "--retry-media",
        ]
    )
    assert args.channel == "anarconomy"
    assert args.limit == 50
    assert args.min_id == 100
    assert args.incremental is True
    assert args.export == "out.json"
    assert args.download_media is True
    assert args.media_dir == "custom_media"
    assert args.retry_media is True


def test_export_messages_json(tmp_path):
    """Test exporting messages to a JSON file."""
    db_file = tmp_path / "test.db"
    db = Database(str(db_file))
    channel = ChannelInfo(id=3001, title="Export Test")
    db.save_channel(channel)
    db.save_messages(
        [
            Message(id=1, channel_id=3001, text="Hello 1", date="2025-01-01"),
            Message(id=2, channel_id=3001, text="Hello 2", date="2025-01-02"),
        ]
    )

    out_file = tmp_path / "messages.json"
    export_messages(db, 3001, str(out_file))

    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["id"] == 2
    assert data[1]["id"] == 1
    db.close()


def test_export_messages_csv(tmp_path):
    """Test exporting messages to a CSV file."""
    db_file = tmp_path / "test.db"
    db = Database(str(db_file))
    channel = ChannelInfo(id=3002, title="CSV Test")
    db.save_channel(channel)
    db.save_messages(
        [
            Message(id=10, channel_id=3002, text="CSV Row 1", date="2025-01-01"),
        ]
    )

    out_file = tmp_path / "messages.csv"
    export_messages(db, 3002, str(out_file))

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "CSV Row 1" in content
    assert "channel_id" in content
    db.close()


@pytest.mark.asyncio
async def test_crawl_channel_end_to_end_mocked(tmp_path):
    """Test crawl_channel flow with mocked Telegram client."""
    db_file = tmp_path / "crawl_test.db"
    media_dir = tmp_path / "media"
    config = Config(
        telegram_api_id=12345,
        telegram_api_hash="valid_hash",
        telegram_phone=None,
        telegram_session_name="test_session",
        default_channel="anarconomy",
        database_path=str(db_file),
        batch_size=10,
        rate_limit_delay=0.0,
        log_level="INFO",
        download_media=False,
        media_directory=str(media_dir),
    )

    args = parse_args(["--channel", "anarconomy", "--limit", "2", "--db", str(db_file), "--no-download-media"])

    fake_channel_info = ChannelInfo(id=5001, title="Anarconomy", username="anarconomy")
    fake_messages = [
        Message(id=101, channel_id=5001, date="2025-01-01", text="Msg 1"),
        Message(id=102, channel_id=5001, date="2025-01-02", text="Msg 2"),
    ]

    async def fake_iter(*args, **kwargs):
        for m in fake_messages:
            yield m

    mock_client_instance = MagicMock()
    mock_client_instance.get_channel_entity = AsyncMock(return_value=MagicMock())
    mock_client_instance.get_channel_info = AsyncMock(return_value=fake_channel_info)
    mock_client_instance.iter_messages = fake_iter
    mock_client_instance.disconnect = AsyncMock()
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)

    with patch("crawler.TelegramCrawlerClient", return_value=mock_client_instance):
        exit_code = await crawl_channel(args, config)

    assert exit_code == 0
    # Verify in DB
    db = Database(str(db_file))
    assert db.get_message_count(5001) == 2
    assert db.get_channel(5001).title == "Anarconomy"
    db.close()


@pytest.mark.asyncio
async def test_crawl_channel_retry_media_mocked(tmp_path):
    """Test --retry-media branch in crawl_channel."""
    db_file = tmp_path / "retry_test.db"
    media_dir = tmp_path / "media"
    config = Config(
        telegram_api_id=12345,
        telegram_api_hash="valid_hash",
        telegram_phone=None,
        telegram_session_name="test_session",
        default_channel="anarconomy",
        database_path=str(db_file),
        batch_size=10,
        rate_limit_delay=0.0,
        log_level="INFO",
        download_media=True,
        media_directory=str(media_dir),
    )

    db = Database(str(db_file))
    channel = ChannelInfo(id=6001, title="Retry Channel", username="retrychan")
    db.save_channel(channel)
    db.save_message(Message(id=50, channel_id=6001, text="Failed message"))
    db.save_media(
        MediaItem(
            message_id=50,
            channel_id=6001,
            telegram_message_id=50,
            media_type="photo",
            file_name="photo_50.jpg",
            file_path=str(media_dir / "retrychan" / "50" / "photo_50.jpg"),
            download_status="failed",
            download_error="Prior error",
        )
    )
    db.close()

    args = parse_args(["--channel", "retrychan", "--retry-media", "--db", str(db_file)])

    fake_raw = MagicMock(id=50, media=MagicMock(), photo=MagicMock(), grouped_id=None)
    mock_client_instance = MagicMock()
    mock_client_instance.get_channel_entity = AsyncMock(return_value=MagicMock())
    mock_client_instance.get_channel_info = AsyncMock(return_value=channel)
    mock_client_instance.get_messages_by_ids = AsyncMock(return_value=[fake_raw])
    async def fake_download(m, file):
        p = Path(file)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"downloaded_bytes")
        return str(p)
    mock_client_instance.download_media = AsyncMock(side_effect=fake_download)
    mock_client_instance.disconnect = AsyncMock()
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)

    with patch("crawler.TelegramCrawlerClient", return_value=mock_client_instance):
        exit_code = await crawl_channel(args, config)

    assert exit_code == 0
    # Verify media status updated to downloaded
    db2 = Database(str(db_file))
    media_records = db2.get_media_for_message(6001, 50)
    assert len(media_records) == 1
    assert media_records[0].download_status == "downloaded"
    db2.close()
