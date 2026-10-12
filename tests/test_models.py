"""Unit tests for ChannelInfo, Message, and MediaItem models."""

from datetime import datetime, timezone
from models import ChannelInfo, MediaItem, Message


class DummyMedia:
    pass


class DummyTelethonMsg:
    def __init__(
        self,
        id=101,
        date=datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        message="Hello world test message",
        views=45,
        forwards=3,
        sender_id=777,
        media=None,
        photo=None,
        video=None,
        reply_to=None,
        pinned=True,
    ):
        self.id = id
        self.date = date
        self.message = message
        self.views = views
        self.forwards = forwards
        self.sender_id = sender_id
        self.media = media
        self.photo = photo
        self.video = video
        self.reply_to = reply_to
        self.pinned = pinned
        self.edit_date = None
        self.post_author = "Admin"
        self.replies = None


def test_channel_info_dict_roundtrip():
    """Verify ChannelInfo serialization to and from dict."""
    channel = ChannelInfo(
        id=1234567,
        title="Anarconomy News",
        username="anarconomy",
        description="Economic and tech news",
        member_count=5000,
        is_broadcast=True,
        is_megagroup=False,
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
    )
    d = channel.to_dict()
    restored = ChannelInfo.from_dict(d)
    assert restored.id == channel.id
    assert restored.title == channel.title
    assert restored.username == channel.username
    assert restored.member_count == 5000
    assert restored.is_broadcast is True


def test_message_dict_roundtrip():
    """Verify Message serialization to and from dict."""
    msg = Message(
        id=42,
        channel_id=1234567,
        sender_id=888,
        date="2025-01-15T12:00:00+00:00",
        text="Test payload",
        views=150,
        forwards=5,
        replies_count=2,
        reply_to_msg_id=10,
        media_type="photo",
        is_pinned=True,
        grouped_id=555666,
        crawl_status="success",
    )
    d = msg.to_dict()
    restored = Message.from_dict(d)
    assert restored.id == msg.id
    assert restored.channel_id == msg.channel_id
    assert restored.text == "Test payload"
    assert restored.views == 150
    assert restored.media_type == "photo"
    assert restored.is_pinned is True
    assert restored.grouped_id == 555666
    assert restored.crawl_status == "success"


def test_media_item_dict_roundtrip():
    """Verify MediaItem serialization to and from dict."""
    item = MediaItem(
        id=1,
        message_id=42,
        channel_id=1234567,
        telegram_message_id=42,
        media_type="video",
        file_name="video_42.mp4",
        file_path="media/anarconomy/42/video_42.mp4",
        mime_type="video/mp4",
        file_size=2048000,
        width=1920,
        height=1080,
        duration=30,
        download_status="downloaded",
        download_error=None,
        grouped_id=555666,
    )
    d = item.to_dict()
    restored = MediaItem.from_dict(d)
    assert restored.id == item.id
    assert restored.telegram_message_id == 42
    assert restored.media_type == "video"
    assert restored.file_name == "video_42.mp4"
    assert restored.file_size == 2048000
    assert restored.width == 1920
    assert restored.height == 1080
    assert restored.duration == 30
    assert restored.download_status == "downloaded"
    assert restored.grouped_id == 555666


def test_message_from_telethon():
    """Verify conversion from Telethon message object to Message model."""
    raw = DummyTelethonMsg(photo="photo_object", media=DummyMedia())
    model = Message.from_telethon(raw, channel_id=999)

    assert model.id == 101
    assert model.channel_id == 999
    assert model.sender_id == 777
    assert model.text == "Hello world test message"
    assert model.views == 45
    assert model.forwards == 3
    assert model.media_type == "photo"
    assert model.is_pinned is True
    assert model.post_author == "Admin"
    assert model.raw_message is raw
