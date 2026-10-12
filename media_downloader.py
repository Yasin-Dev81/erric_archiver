"""Media detection, file naming, disk management, and download orchestration."""

import asyncio
from datetime import datetime, timezone
import mimetypes
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

from telethon import errors
from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from config import Config
from database import Database
from logger import logger
from models import ChannelInfo, MediaItem, Message


def sanitize_filename(name: str) -> str:
    """Sanitize a string to be a safe filename across Linux, macOS, and Windows."""
    # Remove characters illegal in Windows/POSIX filenames
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Strip leading/trailing spaces and dots
    cleaned = cleaned.strip(". ")
    # Avoid reserved Windows device names
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    stem = Path(cleaned).stem.upper()
    if stem in reserved:
        cleaned = f"_{cleaned}"
    # Truncate length
    if len(cleaned) > 200:
        cleaned = cleaned[:200]
    return cleaned or "unnamed_file"


def detect_media_info(raw_msg: Any) -> Optional[Dict[str, Any]]:
    """Inspect Telethon message media and extract detailed type and metadata.

    Returns:
        Dictionary containing media_type, mime_type, file_size, width, height,
        duration, original_filename, or None if no downloadable media.
    """
    media = getattr(raw_msg, "media", None)
    if not media:
        return None

    # Handle Photo
    is_photo = (
        getattr(raw_msg, "photo", None) is not None
        or isinstance(media, MessageMediaPhoto)
        or media.__class__.__name__ in ("MessageMediaPhoto", "DummyMediaPhoto")
    )
    if is_photo:
        photo_obj = getattr(raw_msg, "photo", None) or getattr(media, "photo", None)
        width, height = None, None
        file_size = None
        if photo_obj:
            sizes = getattr(photo_obj, "sizes", [])
            if sizes:
                # Get the largest photo size
                largest = sizes[-1]
                width = getattr(largest, "w", None)
                height = getattr(largest, "h", None)
                file_size = getattr(largest, "size", None)

        return {
            "media_type": "photo",
            "mime_type": "image/jpeg",
            "extension": ".jpg",
            "file_size": file_size,
            "width": width,
            "height": height,
            "duration": None,
            "original_filename": None,
        }

    # Handle Document
    doc = getattr(raw_msg, "document", None) or getattr(media, "document", None)
    if doc is not None:
        attrs = getattr(doc, "attributes", [])
        mime = (getattr(doc, "mime_type", "") or "").lower()
        file_size = getattr(doc, "size", None)

        # Inspect attributes
        v_attr = next(
            (a for a in attrs if isinstance(a, DocumentAttributeVideo) or getattr(a, "round_message", None) is not None or "Video" in a.__class__.__name__),
            None,
        )
        a_attr = next(
            (a for a in attrs if isinstance(a, DocumentAttributeAudio) or getattr(a, "voice", None) is not None or "Audio" in a.__class__.__name__),
            None,
        )
        anim_attr = next(
            (a for a in attrs if isinstance(a, DocumentAttributeAnimated) or "Animated" in a.__class__.__name__),
            None,
        )
        fn_attr = next(
            (a for a in attrs if isinstance(a, DocumentAttributeFilename) or hasattr(a, "file_name")),
            None,
        )

        original_filename = getattr(fn_attr, "file_name", None) if fn_attr else None

        # Determine media type
        if v_attr:
            round_msg = bool(getattr(v_attr, "round_message", False))
            w = getattr(v_attr, "w", None)
            h = getattr(v_attr, "h", None)
            duration = int(getattr(v_attr, "duration", 0) or 0)
            if round_msg:
                return {
                    "media_type": "video_note",
                    "mime_type": mime or "video/mp4",
                    "extension": ".mp4",
                    "file_size": file_size,
                    "width": w,
                    "height": h,
                    "duration": duration,
                    "original_filename": original_filename,
                }
            if anim_attr:
                return {
                    "media_type": "animation",
                    "mime_type": mime or "video/mp4",
                    "extension": ".mp4",
                    "file_size": file_size,
                    "width": w,
                    "height": h,
                    "duration": duration,
                    "original_filename": original_filename,
                }
            return {
                "media_type": "video",
                "mime_type": mime or "video/mp4",
                "extension": _guess_video_ext(mime, original_filename),
                "file_size": file_size,
                "width": w,
                "height": h,
                "duration": duration,
                "original_filename": original_filename,
            }

        if a_attr:
            is_voice = bool(getattr(a_attr, "voice", False))
            duration = int(getattr(a_attr, "duration", 0) or 0)
            if is_voice:
                return {
                    "media_type": "voice",
                    "mime_type": mime or "audio/ogg",
                    "extension": ".ogg",
                    "file_size": file_size,
                    "width": None,
                    "height": None,
                    "duration": duration,
                    "original_filename": original_filename,
                }
            return {
                "media_type": "audio",
                "mime_type": mime or "audio/mpeg",
                "extension": _guess_audio_ext(mime, original_filename),
                "file_size": file_size,
                "width": None,
                "height": None,
                "duration": duration,
                "original_filename": original_filename,
            }

        if anim_attr or mime == "image/gif":
            ext = ".gif" if mime == "image/gif" else ".mp4"
            return {
                "media_type": "animation",
                "mime_type": mime or "image/gif",
                "extension": ext,
                "file_size": file_size,
                "width": None,
                "height": None,
                "duration": None,
                "original_filename": original_filename,
            }

        # Check by MIME prefix fallback
        if mime.startswith("video/"):
            return {
                "media_type": "video",
                "mime_type": mime,
                "extension": _guess_video_ext(mime, original_filename),
                "file_size": file_size,
                "width": None,
                "height": None,
                "duration": None,
                "original_filename": original_filename,
            }
        if mime.startswith("audio/"):
            return {
                "media_type": "audio",
                "mime_type": mime,
                "extension": _guess_audio_ext(mime, original_filename),
                "file_size": file_size,
                "width": None,
                "height": None,
                "duration": None,
                "original_filename": original_filename,
            }

        # Generic document
        doc_ext = _guess_doc_ext(mime, original_filename)
        return {
            "media_type": "document",
            "mime_type": mime or "application/octet-stream",
            "extension": doc_ext,
            "file_size": file_size,
            "width": None,
            "height": None,
            "duration": None,
            "original_filename": original_filename,
        }

    return None


def _guess_video_ext(mime: str, filename: Optional[str]) -> str:
    if filename and Path(filename).suffix:
        return Path(filename).suffix.lower()
    if mime == "video/quicktime":
        return ".mov"
    if mime == "video/x-matroska":
        return ".mkv"
    return ".mp4"


def _guess_audio_ext(mime: str, filename: Optional[str]) -> str:
    if filename and Path(filename).suffix:
        return Path(filename).suffix.lower()
    if mime in ("audio/ogg", "application/ogg"):
        return ".ogg"
    if mime == "audio/x-m4a":
        return ".m4a"
    if mime == "audio/flac":
        return ".flac"
    return ".mp3"


def _guess_doc_ext(mime: str, filename: Optional[str]) -> str:
    if filename and Path(filename).suffix:
        return Path(filename).suffix.lower()
    if mime:
        ext = mimetypes.guess_extension(mime)
        if ext:
            return ext.lower()
    return ".bin"


def generate_safe_filename(
    media_type: str, message_id: int, extension: str, index: Optional[int] = None
) -> str:
    """Generate a deterministic, safe filename for a media item.

    Examples:
        generate_safe_filename("photo", 1234, ".jpg") -> "photo_1234.jpg"
        generate_safe_filename("photo", 1234, ".jpg", index=2) -> "photo_1234_2.jpg"
    """
    ext = extension if extension.startswith(".") else f".{extension}"
    if index is not None and index > 1:
        base = f"{media_type}_{message_id}_{index}{ext}"
    else:
        base = f"{media_type}_{message_id}{ext}"
    return sanitize_filename(base)


def check_disk_space(target_dir: Path, required_bytes: int = 0) -> bool:
    """Verify available disk space before writing a media file.

    Ensures at least 50MB or required_bytes + 10MB safety margin.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        usage = shutil.disk_usage(target_dir)
        min_required = max(50 * 1024 * 1024, required_bytes + 10 * 1024 * 1024)
        if usage.free < min_required:
            logger.error(
                "Insufficient disk space in %s: %d bytes free, %d bytes required.",
                target_dir,
                usage.free,
                min_required,
            )
            return False
        return True
    except Exception as e:
        logger.warning("Could not determine disk usage (%s). Proceeding.", e)
        return True


class MediaDownloader:
    """Orchestrates resilient media downloads, retries, and disk storage."""

    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.media_root = Path(config.media_directory)

    def get_channel_folder(self, channel_identifier: str) -> Path:
        """Resolve safe channel storage directory."""
        safe_channel = sanitize_filename(channel_identifier.lstrip("@").lower())
        return self.media_root / safe_channel

    def _ensure_message_exists(self, channel_id: int, message_id: int, raw_msg: Any = None) -> None:
        """Ensure parent message record exists in SQLite to satisfy foreign key constraints."""
        existing = self.db.get_message(channel_id, message_id)
        if existing is None:
            # Auto-create placeholder message if not yet inserted
            now_iso = datetime.now(timezone.utc).isoformat()
            date_str = now_iso
            text = None
            if raw_msg is not None:
                d = getattr(raw_msg, "date", None)
                if d is not None:
                    date_str = d.isoformat() if hasattr(d, "isoformat") else str(d)
                text = getattr(raw_msg, "message", None) or getattr(raw_msg, "raw_text", None)

            placeholder = Message(
                id=message_id,
                channel_id=channel_id,
                date=date_str,
                text=text,
                crawl_status="success",
            )
            self.db.save_message(placeholder)

    async def download_media(
        self,
        client: Any,
        raw_msg: Any,
        channel_identifier: str,
        channel_id: int,
        index: Optional[int] = None,
    ) -> Optional[MediaItem]:
        """Download media for a Telegram message, tracking status and retrying transient errors.

        Args:
            client: Telethon client or wrapper.
            raw_msg: Raw Telethon message object.
            channel_identifier: Channel username or name.
            channel_id: Channel database ID.
            index: Optional media item index for multiple items in album/message.

        Returns:
            Saved MediaItem, or None if message has no media.
        """
        msg_id = getattr(raw_msg, "id", 0)
        info = detect_media_info(raw_msg)
        if not info:
            return None

        # Ensure parent message exists in database for foreign key constraint
        self._ensure_message_exists(channel_id, msg_id, raw_msg=raw_msg)

        media_type = info["media_type"]
        file_name = generate_safe_filename(media_type, msg_id, info["extension"], index=index)
        channel_folder = self.get_channel_folder(channel_identifier)
        msg_folder = channel_folder / str(msg_id)
        msg_folder.mkdir(parents=True, exist_ok=True)

        dest_file = msg_folder / file_name
        part_file = msg_folder / f"{file_name}.part"
        rel_path = str(dest_file)

        logger.info("[%d] Media detected: %s", msg_id, media_type)

        # Check existing database record
        existing_records = self.db.get_media_for_message(channel_id, msg_id)
        existing = next((r for r in existing_records if r.file_name == file_name), None)

        # Resume check: if already marked downloaded and file exists with size > 0, skip
        if existing and existing.download_status == "downloaded":
            if dest_file.is_file() and dest_file.stat().st_size > 0:
                logger.info("[%d] Media already downloaded: %s", msg_id, rel_path)
                return existing
            else:
                logger.warning(
                    "[%d] Media record is 'downloaded' but file is missing on disk. Re-downloading...",
                    msg_id,
                )
                self.db.update_media_status(
                    existing.id, "missing", error="File missing from disk"
                )

        # Check if completed file already exists on disk
        if dest_file.is_file() and dest_file.stat().st_size > 0:
            actual_size = dest_file.stat().st_size
            media_item = MediaItem(
                id=existing.id if existing else None,
                message_id=msg_id,
                channel_id=channel_id,
                telegram_message_id=msg_id,
                grouped_id=getattr(raw_msg, "grouped_id", None),
                media_type=media_type,
                file_name=file_name,
                file_path=rel_path,
                mime_type=info["mime_type"],
                file_size=actual_size,
                width=info["width"],
                height=info["height"],
                duration=info["duration"],
                download_status="downloaded",
                download_error=None,
            )
            media_item.id = self.db.save_media(media_item)
            logger.info("[%d] Media verified on disk: %s", msg_id, rel_path)
            return media_item

        # Check disk space
        expected_size = info["file_size"] or 0
        if not check_disk_space(msg_folder, expected_size):
            err_msg = "Insufficient disk space"
            logger.error("[%d] Media download failed: %s", msg_id, err_msg)
            media_item = MediaItem(
                id=existing.id if existing else None,
                message_id=msg_id,
                channel_id=channel_id,
                telegram_message_id=msg_id,
                grouped_id=getattr(raw_msg, "grouped_id", None),
                media_type=media_type,
                file_name=file_name,
                file_path=rel_path,
                mime_type=info["mime_type"],
                file_size=0,
                width=info["width"],
                height=info["height"],
                duration=info["duration"],
                download_status="failed",
                download_error=err_msg,
            )
            media_item.id = self.db.save_media(media_item)
            return media_item

        # Download with retry mechanism
        logger.info("[%d] Downloading %s...", msg_id, media_type)
        success = False
        last_error = None
        max_retries = max(1, self.config.media_max_retries)

        for attempt in range(1, max_retries + 1):
            try:
                # Remove stale partial file
                if part_file.exists():
                    part_file.unlink()

                # Execute Telethon download. Pass the path positionally: the
                # TelegramCrawlerClient wrapper names it `file_path`, Telethon names it `file`.
                if hasattr(client, "download_media"):
                    await client.download_media(raw_msg, str(part_file))
                else:
                    await client.client.download_media(raw_msg, str(part_file))

                if not part_file.exists():
                    raise FileNotFoundError(f"Download completed but partial file {part_file} was not created.")

                actual_size = part_file.stat().st_size
                if actual_size == 0:
                    raise ValueError(f"Downloaded file {part_file} is 0 bytes.")

                # Atomic rename from .part to final destination
                part_file.replace(dest_file)
                success = True
                break

            except errors.FloodWaitError as fwe:
                wait_time = fwe.seconds + 1
                last_error = fwe
                logger.warning(
                    "[%d] FloodWait encountered (%ds) during attempt %d/%d.",
                    msg_id,
                    wait_time,
                    attempt,
                    max_retries,
                )
                await asyncio.sleep(wait_time)

            except Exception as e:
                last_error = e
                # Check for fatal / non-retryable errors
                is_fatal = isinstance(e, (FileNotFoundError, PermissionError))
                logger.warning(
                    "[%d] Media download attempt %d/%d failed: %s",
                    msg_id,
                    attempt,
                    max_retries,
                    e,
                )
                if is_fatal or attempt >= max_retries:
                    break

                # Exponential backoff delay
                delay = self.config.media_retry_delay * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

        # Clean up any leftover partial file
        if part_file.exists():
            try:
                part_file.unlink()
            except Exception:
                pass

        if success and dest_file.exists():
            final_size = dest_file.stat().st_size
            media_item = MediaItem(
                id=existing.id if existing else None,
                message_id=msg_id,
                channel_id=channel_id,
                telegram_message_id=msg_id,
                grouped_id=getattr(raw_msg, "grouped_id", None),
                media_type=media_type,
                file_name=file_name,
                file_path=rel_path,
                mime_type=info["mime_type"],
                file_size=final_size,
                width=info["width"],
                height=info["height"],
                duration=info["duration"],
                download_status="downloaded",
                download_error=None,
            )
            media_item.id = self.db.save_media(media_item)
            logger.info("[%d] Media downloaded: %s", msg_id, rel_path)
            return media_item
        else:
            err_str = str(last_error or "Download failed")
            logger.warning("[%d] Media download failed: %s", msg_id, err_str)
            media_item = MediaItem(
                id=existing.id if existing else None,
                message_id=msg_id,
                channel_id=channel_id,
                telegram_message_id=msg_id,
                grouped_id=getattr(raw_msg, "grouped_id", None),
                media_type=media_type,
                file_name=file_name,
                file_path=rel_path,
                mime_type=info["mime_type"],
                file_size=0,
                width=info["width"],
                height=info["height"],
                duration=info["duration"],
                download_status="failed",
                download_error=err_str,
            )
            media_item.id = self.db.save_media(media_item)
            return media_item

    def audit_and_recover_media(self, channel_id: Optional[int] = None) -> None:
        """Scan SQLite media records against disk to repair missing and recovered files."""
        # 1. Check all 'downloaded' items: if missing from disk, set to 'missing'
        all_media = self.db.get_all_media(channel_id=channel_id or 0, limit=100000) if channel_id else []
        for item in all_media:
            if item.download_status == "downloaded":
                p = Path(item.file_path)
                if not p.is_file() or p.stat().st_size == 0:
                    logger.warning(
                        "[%d] Media file missing on disk: %s (marked as missing)",
                        item.telegram_message_id,
                        item.file_path,
                    )
                    self.db.update_media_status(
                        item.id, "missing", error="File missing from disk"
                    )

        # 2. Check failed or pending items: if completed file exists, recover to 'downloaded'
        failed_items = self.db.get_failed_media(channel_id=channel_id)
        for item in failed_items:
            p = Path(item.file_path)
            if p.is_file() and p.stat().st_size > 0:
                actual_size = p.stat().st_size
                logger.info(
                    "[%d] Found existing valid file: %s (recovered to downloaded)",
                    item.telegram_message_id,
                    item.file_path,
                )
                self.db.update_media_status(
                    item.id, "downloaded", error=None, file_size=actual_size
                )


async def retry_failed_media(
    crawler_client: Any,
    entity: Any,
    db: Database,
    channel_info: ChannelInfo,
    downloader: MediaDownloader,
) -> Dict[str, int]:
    """Retry all failed or missing media downloads for a channel.

    Returns:
        Summary dict containing total_failed, retried, downloaded, still_failed.
    """
    failed_items = db.get_failed_media(channel_id=channel_info.id)
    total_failed = len(failed_items)

    print("\n" + "=" * 60)
    print(f" RETRY MEDIA: {channel_info.title}")
    print("=" * 60)
    print(f" Found {total_failed} media item(s) to retry.\n")

    if total_failed == 0:
        print("Media retry completed\n")
        print("Total failed media: 0")
        print("Retried: 0")
        print("Downloaded: 0")
        print("Still failed: 0\n")
        return {
            "total_failed": 0,
            "retried": 0,
            "downloaded": 0,
            "still_failed": 0,
        }

    # Fetch corresponding messages by ID from Telegram
    msg_ids = sorted(list({item.telegram_message_id for item in failed_items}))
    logger.info("Fetching %d message(s) from Telegram for media retry...", len(msg_ids))

    try:
        raw_messages = await crawler_client.get_messages_by_ids(entity, msg_ids)
    except Exception as e:
        logger.error("Failed to retrieve messages from Telegram: %s", e)
        return {
            "total_failed": total_failed,
            "retried": 0,
            "downloaded": 0,
            "still_failed": total_failed,
        }

    # Map message ID -> raw message object
    msg_map = {
        getattr(m, "id"): m
        for m in raw_messages
        if m is not None and getattr(m, "id", None) is not None
    }

    retried_count = 0
    downloaded_count = 0

    channel_name = channel_info.username or f"channel_{channel_info.id}"

    for item in failed_items:
        retried_count += 1
        raw_msg = msg_map.get(item.telegram_message_id)

        if not raw_msg or getattr(raw_msg, "media", None) is None:
            logger.warning(
                "[%d] Message or media no longer available on Telegram.",
                item.telegram_message_id,
            )
            db.update_media_status(
                item.id, "failed", error="Message deleted or media removed on Telegram"
            )
            continue

        result = await downloader.download_media(
            client=crawler_client,
            raw_msg=raw_msg,
            channel_identifier=channel_name,
            channel_id=channel_info.id,
        )

        if result and result.download_status == "downloaded":
            downloaded_count += 1

    still_failed = total_failed - downloaded_count

    print("\n" + "=" * 60)
    print("Media retry completed")
    print("=" * 60)
    print(f"Total failed media: {total_failed}")
    print(f"Retried: {retried_count}")
    print(f"Downloaded: {downloaded_count}")
    print(f"Still failed: {still_failed}")
    print("=" * 60 + "\n")

    return {
        "total_failed": total_failed,
        "retried": retried_count,
        "downloaded": downloaded_count,
        "still_failed": still_failed,
    }
