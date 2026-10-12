"""Telegram client wrapper using Telethon."""

import asyncio
from typing import Any, AsyncGenerator, List, Optional, Union
import re

from telethon import TelegramClient, errors
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel, Chat

from config import Config
from logger import logger
from models import ChannelInfo, Message


def clean_channel_identifier(identifier: str) -> str:
    """Normalize a channel identifier from URLs, @ mentions, or plain strings.

    Examples:
        'https://t.me/anarconomy' -> 'anarconomy'
        't.me/anarconomy' -> 'anarconomy'
        '@anarconomy' -> 'anarconomy'
        'anarconomy' -> 'anarconomy'
    """
    cleaned = identifier.strip()
    # Match https://t.me/channel or t.me/channel
    url_match = re.search(r"(?:https?://)?(?:www\.)?t\.me/([^/?#]+)", cleaned)
    if url_match:
        return url_match.group(1)
    return cleaned.lstrip("@")


class TelegramCrawlerClient:
    """Telethon client wrapper providing authentication and message extraction."""

    def __init__(self, config: Config) -> None:
        """Initialize the crawler client.

        Args:
            config: Configuration object containing credentials.
        """
        self.config = config
        self._client: Optional[TelegramClient] = None

    @property
    def client(self) -> TelegramClient:
        """Get or initialize the underlying Telethon client."""
        if self._client is None:
            self.config.validate()
            self._client = TelegramClient(
                session=self.config.telegram_session_name,
                api_id=self.config.telegram_api_id,  # type: ignore[arg-type]
                api_hash=self.config.telegram_api_hash,  # type: ignore[arg-type]
            )
        return self._client

    async def connect_and_login(self) -> None:
        """Connect to Telegram and handle authentication if needed.

        If interactive authentication is required, Telethon will prompt
        the user in the terminal for their phone number, confirmation code,
        and 2FA password (if enabled).
        """
        logger.info("Initializing Telegram client session: '%s'", self.config.telegram_session_name)
        client = self.client

        # Connect to Telegram servers
        await client.connect()

        # Check authorization or start interactive auth
        if not await client.is_user_authorized():
            logger.info("Session is not authorized. Starting authentication...")
            phone = self.config.telegram_phone
            if phone:
                await client.start(phone=lambda: phone)
            else:
                await client.start()

        me = await client.get_me()
        user_display = getattr(me, "username", None) or getattr(me, "first_name", "User")
        logger.info("Successfully connected as %s (ID: %s)", user_display, getattr(me, "id", "Unknown"))

    async def get_channel_entity(self, channel_identifier: str) -> Union[Channel, Chat]:
        """Resolve a channel identifier to a Telethon entity.

        Args:
            channel_identifier: Channel username, link, or numeric ID.

        Returns:
            Telethon Channel or Chat entity.
        """
        target = clean_channel_identifier(channel_identifier)
        logger.info("Resolving channel entity: '%s' (normalized: '%s')", channel_identifier, target)
        try:
            # If numeric ID
            if target.lstrip("-").isdigit():
                entity = await self.client.get_entity(int(target))
            else:
                entity = await self.client.get_entity(target)
            return entity
        except errors.RPCError as e:
            logger.error("Failed to resolve channel '%s': %s", channel_identifier, e)
            raise

    async def get_channel_info(self, entity: Union[Channel, Chat]) -> ChannelInfo:
        """Retrieve full metadata for a channel entity.

        Args:
            entity: Channel entity obtained from get_channel_entity.

        Returns:
            Populated ChannelInfo instance.
        """
        full_chat = None
        try:
            full_response = await self.client(GetFullChannelRequest(channel=entity))
            full_chat = getattr(full_response, "full_chat", None)
        except Exception as e:
            logger.warning("Could not fetch full channel info (%s). Using entity metadata.", e)

        return ChannelInfo.from_telethon(entity, full_chat=full_chat)

    async def get_messages_by_ids(
        self, entity: Union[Channel, Chat], ids: List[int]
    ) -> List[Any]:
        """Retrieve specific messages by their IDs from a channel.

        Args:
            entity: Target channel entity.
            ids: List of message IDs to retrieve.

        Returns:
            List of raw Telethon message objects.
        """
        if not ids:
            return []
        try:
            messages = await self.client.get_messages(entity, ids=ids)
            return messages if isinstance(messages, list) else [messages]
        except Exception as e:
            logger.error("Error retrieving messages by IDs: %s", e)
            raise

    async def download_media(self, message: Any, file_path: str) -> Optional[str]:
        """Stream and download media associated with a message to a file path.

        Args:
            message: Raw Telethon message or media object.
            file_path: Destination path on local disk.

        Returns:
            Downloaded file path as string, or None if no media.
        """
        return await self.client.download_media(message, file=file_path)

    async def iter_messages(
        self,
        entity: Union[Channel, Chat],
        limit: Optional[int] = None,
        offset_id: int = 0,
        min_id: int = 0,
        max_id: int = 0,
        batch_size: int = 100,
        rate_limit_delay: float = 1.0,
    ) -> AsyncGenerator[Message, None]:
        """Stream messages from the channel, converting to Message models.

        Args:
            entity: Target channel entity.
            limit: Total maximum messages to retrieve (None for all).
            offset_id: Offset message ID to start reading from.
            min_id: Retrieve only messages with ID > min_id (incremental crawl).
            max_id: Retrieve only messages with ID < max_id.
            batch_size: Messages fetched per batch request.
            rate_limit_delay: Delay in seconds between batches.

        Yields:
            Message instances with raw_message populated.
        """
        channel_id = getattr(entity, "id", 0)
        yielded_count = 0
        current_offset = offset_id
        # Fold max_id into the starting offset (both are exclusive upper bounds) and
        # never pass it to Telethon: Telethon uses max(offset_id, max_id) as the offset,
        # so re-sending max_id resets pagination and returns the first batch forever.
        if max_id and (not current_offset or max_id < current_offset):
            current_offset = max_id

        while True:
            # Calculate remaining limit
            chunk_limit = batch_size
            if limit is not None:
                remaining = limit - yielded_count
                if remaining <= 0:
                    break
                chunk_limit = min(chunk_limit, remaining)

            try:
                messages_batch = await self.client.get_messages(
                    entity,
                    limit=chunk_limit,
                    offset_id=current_offset,
                    min_id=min_id,
                )
            except errors.FloodWaitError as err:
                wait_sec = err.seconds + 1
                logger.warning("Telegram FloodWait encountered. Waiting %d seconds...", wait_sec)
                await asyncio.sleep(wait_sec)
                continue
            except Exception as e:
                logger.error("Error fetching message chunk at offset %d: %s", current_offset, e)
                raise

            if not messages_batch:
                logger.info("No more messages available to fetch.")
                break

            for raw_msg in messages_batch:
                # Telethon can return ServiceMessage or Message
                msg_model = Message.from_telethon(raw_msg, channel_id=channel_id)
                yield msg_model
                yielded_count += 1
                current_offset = raw_msg.id

                if limit is not None and yielded_count >= limit:
                    break

            if limit is not None and yielded_count >= limit:
                break

            # If fewer messages returned than requested, we reached the end
            if len(messages_batch) < chunk_limit:
                break

            if rate_limit_delay > 0:
                await asyncio.sleep(rate_limit_delay)

    async def disconnect(self) -> None:
        """Disconnect the client session."""
        if self._client is not None and self._client.is_connected():
            logger.info("Disconnecting Telegram client session.")
            await self._client.disconnect()
            self._client = None

    async def __aenter__(self) -> "TelegramCrawlerClient":
        await self.connect_and_login()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
