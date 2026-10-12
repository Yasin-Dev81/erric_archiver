"""Unit tests for Telegram client logic and helper methods."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from config import Config
from telegram_client import TelegramCrawlerClient, clean_channel_identifier


def test_clean_channel_identifier():
    """Verify various channel URL/handle formats are cleanly parsed."""
    assert clean_channel_identifier("anarconomy") == "anarconomy"
    assert clean_channel_identifier("@anarconomy") == "anarconomy"
    assert clean_channel_identifier("  @anarconomy  ") == "anarconomy"
    assert clean_channel_identifier("https://t.me/anarconomy") == "anarconomy"
    assert clean_channel_identifier("http://t.me/anarconomy") == "anarconomy"
    assert clean_channel_identifier("t.me/anarconomy") == "anarconomy"
    assert clean_channel_identifier("https://t.me/anarconomy/") == "anarconomy"
    assert clean_channel_identifier("-100123456789") == "-100123456789"


@pytest.mark.asyncio
async def test_iter_messages_batching():
    """Verify iter_messages streams messages properly with mock client."""
    config = Config(
        telegram_api_id=12345,
        telegram_api_hash="valid_hash",
        telegram_phone=None,
        telegram_session_name="test_session",
        default_channel="test",
        database_path="test.db",
        batch_size=2,
        rate_limit_delay=0.0,
        log_level="INFO",
    )

    client_wrapper = TelegramCrawlerClient(config)

    # Mock telethon client
    mock_telethon = MagicMock()
    mock_entity = MagicMock(id=555)

    msg1 = MagicMock(id=10, date=None, message="Msg 1", media=None, reply_to=None)
    msg2 = MagicMock(id=9, date=None, message="Msg 2", media=None, reply_to=None)
    msg3 = MagicMock(id=8, date=None, message="Msg 3", media=None, reply_to=None)

    # First batch returns 2 messages, second batch returns 1 message
    mock_telethon.get_messages = AsyncMock(
        side_effect=[
            [msg1, msg2],
            [msg3],
        ]
    )

    client_wrapper._client = mock_telethon

    collected = []
    async for msg in client_wrapper.iter_messages(
        entity=mock_entity,
        limit=3,
        batch_size=2,
        rate_limit_delay=0.0,
    ):
        collected.append(msg)

    assert len(collected) == 3
    assert collected[0].id == 10
    assert collected[1].id == 9
    assert collected[2].id == 8
    assert mock_telethon.get_messages.call_count == 2


@pytest.mark.asyncio
async def test_iter_messages_max_id_paginates():
    """max_id must not reset pagination: emulate Telethon's offset = max(offset_id, max_id)."""
    config = Config(
        telegram_api_id=12345,
        telegram_api_hash="valid_hash",
        telegram_phone=None,
        telegram_session_name="test_session",
        default_channel="test",
        database_path="test.db",
        batch_size=3,
        rate_limit_delay=0.0,
        log_level="INFO",
    )
    client_wrapper = TelegramCrawlerClient(config)

    all_ids = list(range(1, 21))  # channel has messages 1..20

    async def fake_get_messages(entity, limit, offset_id=0, min_id=0, max_id=0):
        offset = max(offset_id, max_id)  # Telethon 1.x behaviour
        ids = [i for i in reversed(all_ids) if (not offset or i < offset) and i > min_id]
        return [MagicMock(id=i, date=None, message=f"Msg {i}", media=None, reply_to=None) for i in ids[:limit]]

    mock_telethon = MagicMock()
    mock_telethon.get_messages = AsyncMock(side_effect=fake_get_messages)
    client_wrapper._client = mock_telethon

    collected = [
        m.id
        async for m in client_wrapper.iter_messages(
            entity=MagicMock(id=555), limit=50, max_id=11, batch_size=3, rate_limit_delay=0.0
        )
    ]

    assert collected == list(range(10, 0, -1))
