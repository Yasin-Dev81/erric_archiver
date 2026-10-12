"""Data models for Telegram channels, messages, and media."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ChannelInfo:
    """Represents a Telegram channel or chat."""

    id: int
    title: str
    username: Optional[str] = None
    description: Optional[str] = None
    member_count: Optional[int] = None
    is_broadcast: bool = True
    is_megagroup: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert ChannelInfo to a dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChannelInfo":
        """Instantiate ChannelInfo from a dictionary."""
        return cls(
            id=int(data["id"]),
            title=data.get("title", ""),
            username=data.get("username"),
            description=data.get("description"),
            member_count=data.get("member_count"),
            is_broadcast=bool(data.get("is_broadcast", True)),
            is_megagroup=bool(data.get("is_megagroup", False)),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    @classmethod
    def from_telethon(cls, entity: Any, full_chat: Any = None) -> "ChannelInfo":
        """Build ChannelInfo from Telethon entity and optional full_chat details."""
        now_iso = datetime.now(timezone.utc).isoformat()
        channel_id = getattr(entity, "id", 0)
        title = getattr(entity, "title", str(channel_id))
        username = getattr(entity, "username", None)
        is_broadcast = getattr(entity, "broadcast", True)
        is_megagroup = getattr(entity, "megagroup", False)

        description = None
        member_count = None
        if full_chat is not None:
            description = getattr(full_chat, "about", None)
            member_count = getattr(full_chat, "participants_count", None)

        if member_count is None:
            member_count = getattr(entity, "participants_count", None)

        return cls(
            id=channel_id,
            title=title,
            username=username,
            description=description,
            member_count=member_count,
            is_broadcast=is_broadcast,
            is_megagroup=is_megagroup,
            created_at=now_iso,
            updated_at=now_iso,
        )


@dataclass
class MediaItem:
    """Represents a media file associated with a Telegram message."""

    id: Optional[int] = None
    message_id: int = 0
    channel_id: int = 0
    telegram_message_id: int = 0
    media_type: str = "document"  # photo, video, voice, audio, video_note, animation, document
    file_name: str = ""
    file_path: str = ""
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None
    thumbnail_path: Optional[str] = None
    download_status: str = "pending"  # downloaded, failed, missing, skipped, pending
    download_error: Optional[str] = None
    grouped_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert MediaItem to a dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MediaItem":
        """Instantiate MediaItem from a dictionary."""
        return cls(
            id=data.get("id"),
            message_id=int(data.get("message_id", 0)),
            channel_id=int(data.get("channel_id", 0)),
            telegram_message_id=int(data.get("telegram_message_id", data.get("message_id", 0))),
            media_type=data.get("media_type", "document"),
            file_name=data.get("file_name", ""),
            file_path=data.get("file_path", ""),
            mime_type=data.get("mime_type"),
            file_size=data.get("file_size"),
            width=data.get("width"),
            height=data.get("height"),
            duration=data.get("duration"),
            thumbnail_path=data.get("thumbnail_path"),
            download_status=data.get("download_status", "pending"),
            download_error=data.get("download_error"),
            grouped_id=data.get("grouped_id"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


@dataclass
class Message:
    """Represents a Telegram message."""

    id: int
    channel_id: int
    sender_id: Optional[int] = None
    date: str = ""
    text: Optional[str] = None
    views: Optional[int] = None
    forwards: Optional[int] = None
    replies_count: Optional[int] = None
    reply_to_msg_id: Optional[int] = None
    media_type: Optional[str] = None
    edit_date: Optional[str] = None
    post_author: Optional[str] = None
    is_pinned: bool = False
    grouped_id: Optional[int] = None
    crawl_status: str = "success"  # success, deleted, failed
    media_items: List[MediaItem] = field(default_factory=list)
    raw_message: Any = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        """Convert Message to a dictionary (excluding raw_message object)."""
        d = {
            "id": self.id,
            "channel_id": self.channel_id,
            "sender_id": self.sender_id,
            "date": self.date,
            "text": self.text,
            "views": self.views,
            "forwards": self.forwards,
            "replies_count": self.replies_count,
            "reply_to_msg_id": self.reply_to_msg_id,
            "media_type": self.media_type,
            "edit_date": self.edit_date,
            "post_author": self.post_author,
            "is_pinned": self.is_pinned,
            "grouped_id": self.grouped_id,
            "crawl_status": self.crawl_status,
        }
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Instantiate Message from a dictionary."""
        return cls(
            id=int(data["id"]),
            channel_id=int(data["channel_id"]),
            sender_id=data.get("sender_id"),
            date=data.get("date", ""),
            text=data.get("text"),
            views=data.get("views"),
            forwards=data.get("forwards"),
            replies_count=data.get("replies_count"),
            reply_to_msg_id=data.get("reply_to_msg_id"),
            media_type=data.get("media_type"),
            edit_date=data.get("edit_date"),
            post_author=data.get("post_author"),
            is_pinned=bool(data.get("is_pinned", False)),
            grouped_id=data.get("grouped_id"),
            crawl_status=data.get("crawl_status", "success"),
        )

    @classmethod
    def from_telethon(cls, telethon_msg: Any, channel_id: int) -> "Message":
        """Extract and normalize data from a Telethon message object."""
        # Extract date
        msg_date = getattr(telethon_msg, "date", None)
        date_str = msg_date.isoformat() if isinstance(msg_date, datetime) else str(msg_date or "")

        # Extract edit date
        edit_date_val = getattr(telethon_msg, "edit_date", None)
        edit_date_str = edit_date_val.isoformat() if isinstance(edit_date_val, datetime) else None

        # Extract sender
        sender_id = getattr(telethon_msg, "sender_id", None)
        if sender_id is None and getattr(telethon_msg, "from_id", None) is not None:
            sender_id = getattr(telethon_msg.from_id, "user_id", None)

        # Extract text / caption
        text = getattr(telethon_msg, "message", None) or getattr(telethon_msg, "raw_text", None)

        # Extract replies count
        replies_count = None
        replies_obj = getattr(telethon_msg, "replies", None)
        if replies_obj is not None:
            replies_count = getattr(replies_obj, "replies", None)

        # Extract reply to
        reply_to_msg_id = None
        reply_to = getattr(telethon_msg, "reply_to", None)
        if reply_to is not None:
            reply_to_msg_id = getattr(reply_to, "reply_to_msg_id", None)
        if reply_to_msg_id is None:
            reply_to_msg_id = getattr(telethon_msg, "reply_to_msg_id", None)

        # Extract grouped_id
        grouped_id = getattr(telethon_msg, "grouped_id", None)

        # Extract media type
        media_type = None
        media = getattr(telethon_msg, "media", None)
        if media is not None:
            # Check photo
            if getattr(telethon_msg, "photo", None) or media.__class__.__name__ == "MessageMediaPhoto":
                media_type = "photo"
            else:
                # Check document attributes
                doc = getattr(telethon_msg, "document", None) or getattr(media, "document", None)
                if doc is not None:
                    attrs = getattr(doc, "attributes", [])
                    mime = (getattr(doc, "mime_type", "") or "").lower()

                    has_video = any(a.__class__.__name__ == "DocumentAttributeVideo" for a in attrs)
                    has_audio = any(a.__class__.__name__ == "DocumentAttributeAudio" for a in attrs)
                    has_anim = any(a.__class__.__name__ == "DocumentAttributeAnimated" for a in attrs)

                    if has_video:
                        v_attr = next(a for a in attrs if a.__class__.__name__ == "DocumentAttributeVideo")
                        if getattr(v_attr, "round_message", False):
                            media_type = "video_note"
                        elif has_anim:
                            media_type = "animation"
                        else:
                            media_type = "video"
                    elif has_audio:
                        a_attr = next(a for a in attrs if a.__class__.__name__ == "DocumentAttributeAudio")
                        if getattr(a_attr, "voice", False):
                            media_type = "voice"
                        else:
                            media_type = "audio"
                    elif has_anim or mime == "image/gif":
                        media_type = "animation"
                    elif mime.startswith("video/"):
                        media_type = "video"
                    elif mime.startswith("audio/"):
                        media_type = "audio"
                    else:
                        media_type = "document"
                elif getattr(telethon_msg, "video", None):
                    media_type = "video"
                elif getattr(telethon_msg, "audio", None):
                    media_type = "audio"
                elif getattr(telethon_msg, "voice", None):
                    media_type = "voice"
                elif getattr(telethon_msg, "web_page", None):
                    media_type = "web_page"
                elif getattr(telethon_msg, "poll", None):
                    media_type = "poll"
                else:
                    media_type = media.__class__.__name__.lower()

        return cls(
            id=getattr(telethon_msg, "id", 0),
            channel_id=channel_id,
            sender_id=sender_id,
            date=date_str,
            text=text,
            views=getattr(telethon_msg, "views", None),
            forwards=getattr(telethon_msg, "forwards", None),
            replies_count=replies_count,
            reply_to_msg_id=reply_to_msg_id,
            media_type=media_type,
            edit_date=edit_date_str,
            post_author=getattr(telethon_msg, "post_author", None),
            is_pinned=bool(getattr(telethon_msg, "pinned", False)),
            grouped_id=grouped_id,
            crawl_status="success",
            raw_message=telethon_msg,
        )
