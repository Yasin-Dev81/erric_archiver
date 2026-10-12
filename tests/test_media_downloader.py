"""Unit tests for media detection, downloading, retries, and database consistency."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from config import Config
from database import Database
from media_downloader import (
    MediaDownloader,
    detect_media_info,
    generate_safe_filename,
    retry_failed_media,
    sanitize_filename,
)
from models import ChannelInfo, MediaItem, Message


class DummyAttrVideo:
    def __init__(self, round_message=False, w=1920, h=1080, duration=60):
        self.round_message = round_message
        self.w = w
        self.h = h
        self.duration = duration


class DummyAttrAudio:
    def __init__(self, voice=False, duration=120):
        self.voice = voice
        self.duration = duration


class DummyAttrAnimated:
    pass


class DummyAttrFilename:
    def __init__(self, file_name="document.pdf"):
        self.file_name = file_name


class DummyDocument:
    def __init__(self, attributes=None, mime_type="application/octet-stream", size=1024):
        self.attributes = attributes or []
        self.mime_type = mime_type
        self.size = size


class DummyMediaDocument:
    def __init__(self, document):
        self.document = document


class DummyMediaPhoto:
    def __init__(self):
        self.sizes = [MagicMock(w=800, h=600, size=2048)]


class DummyRawMessage:
    def __init__(self, msg_id=100, media=None, photo=None, document=None, grouped_id=None):
        self.id = msg_id
        self.media = media
        self.photo = photo
        self.document = document
        self.grouped_id = grouped_id


@pytest.fixture
def tmp_config_and_db(tmp_path):
    """Provide a Config and fresh Database instance for testing."""
    db_file = tmp_path / "test.db"
    media_dir = tmp_path / "media"
    config = Config(
        telegram_api_id=12345,
        telegram_api_hash="valid_hash",
        telegram_phone=None,
        telegram_session_name="test_session",
        default_channel="testchan",
        database_path=str(db_file),
        batch_size=10,
        rate_limit_delay=0.0,
        log_level="INFO",
        download_media=True,
        media_directory=str(media_dir),
        media_max_retries=2,
        media_retry_delay=0.01,
    )
    db = Database(str(db_file))
    channel = ChannelInfo(id=999, title="Test Chan", username="testchan")
    db.save_channel(channel)
    yield config, db, media_dir
    db.close()


# 1. Photo detection
def test_photo_detection():
    msg = DummyRawMessage(msg_id=101, media=DummyMediaPhoto(), photo=DummyMediaPhoto())
    info = detect_media_info(msg)
    assert info is not None
    assert info["media_type"] == "photo"
    assert info["extension"] == ".jpg"
    assert info["mime_type"] == "image/jpeg"
    assert info["width"] == 800
    assert info["height"] == 600


# 2. Video detection
def test_video_detection():
    doc = DummyDocument(
        attributes=[DummyAttrVideo(round_message=False, duration=60)],
        mime_type="video/mp4",
        size=5000,
    )
    msg = DummyRawMessage(msg_id=102, media=DummyMediaDocument(doc), document=doc)
    info = detect_media_info(msg)
    assert info is not None
    assert info["media_type"] == "video"
    assert info["extension"] == ".mp4"
    assert info["duration"] == 60


# 3. Voice detection
def test_voice_detection():
    doc = DummyDocument(
        attributes=[DummyAttrAudio(voice=True, duration=120)],
        mime_type="audio/ogg",
        size=3000,
    )
    msg = DummyRawMessage(msg_id=103, media=DummyMediaDocument(doc), document=doc)
    info = detect_media_info(msg)
    assert info is not None
    assert info["media_type"] == "voice"
    assert info["extension"] == ".ogg"
    assert info["duration"] == 120


# 4. Audio detection
def test_audio_detection():
    doc = DummyDocument(
        attributes=[DummyAttrAudio(voice=False, duration=180)],
        mime_type="audio/mpeg",
        size=4000,
    )
    msg = DummyRawMessage(msg_id=104, media=DummyMediaDocument(doc), document=doc)
    info = detect_media_info(msg)
    assert info is not None
    assert info["media_type"] == "audio"
    assert info["extension"] == ".mp3"
    assert info["duration"] == 180


# 5. Video-note detection
def test_video_note_detection():
    doc = DummyDocument(
        attributes=[DummyAttrVideo(round_message=True, w=360, h=360, duration=15)],
        mime_type="video/mp4",
        size=6000,
    )
    msg = DummyRawMessage(msg_id=105, media=DummyMediaDocument(doc), document=doc)
    info = detect_media_info(msg)
    assert info is not None
    assert info["media_type"] == "video_note"
    assert info["extension"] == ".mp4"
    assert info["width"] == 360
    assert info["duration"] == 15


# 6. Document and animation detection
def test_document_and_animation_detection():
    # PDF document
    doc = DummyDocument(
        attributes=[DummyAttrFilename("whitepaper.pdf")],
        mime_type="application/pdf",
        size=8000,
    )
    msg = DummyRawMessage(msg_id=106, media=DummyMediaDocument(doc), document=doc)
    info = detect_media_info(msg)
    assert info is not None
    assert info["media_type"] == "document"
    assert info["extension"] == ".pdf"

    # Animation
    doc_anim = DummyDocument(
        attributes=[DummyAttrAnimated()],
        mime_type="image/gif",
        size=1500,
    )
    msg_anim = DummyRawMessage(msg_id=107, media=DummyMediaDocument(doc_anim), document=doc_anim)
    info_anim = detect_media_info(msg_anim)
    assert info_anim is not None
    assert info_anim["media_type"] == "animation"


# 7. Multiple media in an album
def test_multiple_media_in_album():
    fn1 = generate_safe_filename("photo", 200, ".jpg", index=1)
    fn2 = generate_safe_filename("photo", 200, ".jpg", index=2)
    assert fn1 == "photo_200.jpg"
    assert fn2 == "photo_200_2.jpg"
    assert fn1 != fn2


# 8. Duplicate download prevention
@pytest.mark.asyncio
async def test_duplicate_download_prevention(tmp_config_and_db):
    config, db, media_dir = tmp_config_and_db
    downloader = MediaDownloader(config, db)

    # Save parent message
    msg_obj = Message(id=301, channel_id=999, text="Photo post")
    db.save_message(msg_obj)

    # Create dummy raw message
    raw_msg = DummyRawMessage(msg_id=301, media=DummyMediaPhoto(), photo=DummyMediaPhoto())

    # Mock client that writes bytes to file
    mock_client = MagicMock()
    async def fake_download(m, file):
        p = Path(file)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"image_data_bytes")
        return str(p)
    mock_client.download_media = AsyncMock(side_effect=fake_download)

    # First download
    res1 = await downloader.download_media(mock_client, raw_msg, "testchan", 999)
    assert res1 is not None
    assert res1.download_status == "downloaded"
    assert Path(res1.file_path).exists()
    assert mock_client.download_media.call_count == 1

    # Second download (should skip and NOT call client.download_media again)
    res2 = await downloader.download_media(mock_client, raw_msg, "testchan", 999)
    assert res2 is not None
    assert res2.download_status == "downloaded"
    assert mock_client.download_media.call_count == 1  # Still 1!


# 9. Failed download handling
@pytest.mark.asyncio
async def test_failed_download_handling(tmp_config_and_db):
    config, db, media_dir = tmp_config_and_db
    downloader = MediaDownloader(config, db)

    msg_obj = Message(id=401, channel_id=999, text="Failing download post")
    db.save_message(msg_obj)

    raw_msg = DummyRawMessage(msg_id=401, media=DummyMediaPhoto(), photo=DummyMediaPhoto())

    mock_client = MagicMock()
    mock_client.download_media = AsyncMock(side_effect=RuntimeError("Connection dropped"))

    # Calling download_media should not raise
    res = await downloader.download_media(mock_client, raw_msg, "testchan", 999)
    assert res is not None
    assert res.download_status == "failed"
    assert "Connection dropped" in str(res.download_error)

    # Verify status in database
    records = db.get_media_for_message(999, 401)
    assert len(records) == 1
    assert records[0].download_status == "failed"


# 10. Retry of failed downloads
@pytest.mark.asyncio
async def test_retry_of_failed_downloads(tmp_config_and_db):
    config, db, media_dir = tmp_config_and_db
    downloader = MediaDownloader(config, db)

    # Setup parent message and failed media record
    msg_obj = Message(id=501, channel_id=999, text="Retry candidate")
    db.save_message(msg_obj)

    media_rec = MediaItem(
        message_id=501,
        channel_id=999,
        telegram_message_id=501,
        media_type="photo",
        file_name="photo_501.jpg",
        file_path=str(media_dir / "testchan" / "501" / "photo_501.jpg"),
        download_status="failed",
        download_error="Temporary network issue",
    )
    db.save_media(media_rec)

    # Verify failed list contains it
    failed_before = db.get_failed_media(999)
    assert len(failed_before) == 1

    # Mock client for retry
    raw_msg = DummyRawMessage(msg_id=501, media=DummyMediaPhoto(), photo=DummyMediaPhoto())
    mock_client = MagicMock()
    mock_client.get_messages_by_ids = AsyncMock(return_value=[raw_msg])
    async def fake_download(m, file):
        p = Path(file)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"recovered_bytes")
        return str(p)
    mock_client.download_media = AsyncMock(side_effect=fake_download)

    channel_info = ChannelInfo(id=999, title="Test Chan", username="testchan")
    summary = await retry_failed_media(mock_client, MagicMock(), db, channel_info, downloader)

    assert summary["total_failed"] == 1
    assert summary["downloaded"] == 1
    assert summary["still_failed"] == 0

    # Verify DB status is now downloaded
    records = db.get_media_for_message(999, 501)
    assert records[0].download_status == "downloaded"


# 11. Missing local file detection
def test_missing_local_file_detection(tmp_config_and_db):
    config, db, media_dir = tmp_config_and_db
    downloader = MediaDownloader(config, db)

    msg_obj = Message(id=601, channel_id=999, text="Missing test")
    db.save_message(msg_obj)

    non_existent_path = str(media_dir / "testchan" / "601" / "photo_601.jpg")
    media_rec = MediaItem(
        message_id=601,
        channel_id=999,
        telegram_message_id=601,
        media_type="photo",
        file_name="photo_601.jpg",
        file_path=non_existent_path,
        download_status="downloaded",
    )
    db.save_media(media_rec)

    # Run audit and recovery
    downloader.audit_and_recover_media(channel_id=999)

    records = db.get_media_for_message(999, 601)
    assert records[0].download_status == "missing"


# 12. Partial .part file handling
@pytest.mark.asyncio
async def test_partial_part_file_handling(tmp_config_and_db):
    config, db, media_dir = tmp_config_and_db
    downloader = MediaDownloader(config, db)

    msg_obj = Message(id=701, channel_id=999, text="Part test")
    db.save_message(msg_obj)

    msg_folder = media_dir / "testchan" / "701"
    msg_folder.mkdir(parents=True, exist_ok=True)
    stale_part = msg_folder / "photo_701.jpg.part"
    stale_part.write_bytes(b"corrupted_partial_data")

    raw_msg = DummyRawMessage(msg_id=701, media=DummyMediaPhoto(), photo=DummyMediaPhoto())

    mock_client = MagicMock()
    async def fake_download(m, file):
        # Verify stale part file was cleaned up before writing new data
        p = Path(file)
        p.write_bytes(b"fresh_complete_data")
        return str(p)
    mock_client.download_media = AsyncMock(side_effect=fake_download)

    res = await downloader.download_media(mock_client, raw_msg, "testchan", 999)
    assert res is not None
    assert res.download_status == "downloaded"
    assert not stale_part.exists()  # .part should no longer exist
    final_file = msg_folder / "photo_701.jpg"
    assert final_file.exists()
    assert final_file.read_bytes() == b"fresh_complete_data"


# 13. Database/media consistency
def test_database_media_consistency(tmp_config_and_db):
    config, db, media_dir = tmp_config_and_db

    msg = Message(id=801, channel_id=999, text="Consistent post")
    db.save_message(msg)

    item = MediaItem(
        message_id=801,
        channel_id=999,
        telegram_message_id=801,
        grouped_id=123456,
        media_type="video",
        file_name="video_801.mp4",
        file_path="media/testchan/801/video_801.mp4",
        mime_type="video/mp4",
        file_size=1048576,
        width=1920,
        height=1080,
        duration=45,
        download_status="downloaded",
    )
    db.save_media(item)

    fetched = db.get_media_for_message(999, 801)
    assert len(fetched) == 1
    m = fetched[0]
    assert m.telegram_message_id == 801
    assert m.grouped_id == 123456
    assert m.media_type == "video"
    assert m.file_size == 1048576
    assert m.width == 1920
    assert m.height == 1080
    assert m.duration == 45
    assert m.download_status == "downloaded"


# 14. Message success + media failure separation
@pytest.mark.asyncio
async def test_message_success_media_failure_separation(tmp_config_and_db):
    config, db, media_dir = tmp_config_and_db
    downloader = MediaDownloader(config, db)

    # 1. Message crawled successfully
    msg = Message(id=901, channel_id=999, text="Message text", crawl_status="success")
    db.save_message(msg)

    # 2. Media download fails
    raw_msg = DummyRawMessage(msg_id=901, media=DummyMediaPhoto(), photo=DummyMediaPhoto())
    mock_client = MagicMock()
    mock_client.download_media = AsyncMock(side_effect=Exception("Timeout downloading media"))

    res = await downloader.download_media(mock_client, raw_msg, "testchan", 999)

    # Verify message in DB is still success
    db_msg = db.get_message(999, 901)
    assert db_msg is not None
    assert db_msg.crawl_status == "success"

    # Verify media in DB is failed
    db_media = db.get_media_for_message(999, 901)
    assert len(db_media) == 1
    assert db_media[0].download_status == "failed"
    assert "Timeout downloading media" in str(db_media[0].download_error)


# Regression: downloader must work through the real TelegramCrawlerClient wrapper,
# whose download_media() signature is (message, file_path), not Telethon's (message, file=...).
@pytest.mark.asyncio
async def test_download_through_crawler_client_wrapper(tmp_config_and_db):
    from telegram_client import TelegramCrawlerClient

    config, db, media_dir = tmp_config_and_db
    downloader = MediaDownloader(config, db)

    raw_msg = DummyRawMessage(msg_id=777, media=DummyMediaPhoto(), photo=DummyMediaPhoto())

    async def fake_telethon_download(m, file):
        Path(file).write_bytes(b"image_data_bytes")
        return file

    wrapper = TelegramCrawlerClient(config)
    wrapper._client = MagicMock()
    wrapper._client.download_media = AsyncMock(side_effect=fake_telethon_download)

    res = await downloader.download_media(wrapper, raw_msg, "testchan", 999)
    assert res is not None
    assert res.download_status == "downloaded", res.download_error
    assert Path(res.file_path).read_bytes() == b"image_data_bytes"
