"""Configuration management for the Telegram crawler."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv


@dataclass
class Config:
    """Application configuration settings."""

    telegram_api_id: Optional[int]
    telegram_api_hash: Optional[str]
    telegram_phone: Optional[str]
    telegram_session_name: str
    default_channel: str
    database_path: str
    batch_size: int
    rate_limit_delay: float
    log_level: str
    # Media download settings
    download_media: bool = True
    media_directory: str = "media"
    media_max_retries: int = 3
    media_retry_delay: float = 5.0

    def validate(self) -> None:
        """Validate critical configuration settings required for Telegram connection.

        Raises:
            ValueError: If required API credentials are missing or invalid.
        """
        errors = []
        if not self.telegram_api_id:
            errors.append("TELEGRAM_API_ID is missing or not set to a valid integer.")
        if not self.telegram_api_hash or self.telegram_api_hash.strip() in ("", "your_api_hash_here"):
            errors.append("TELEGRAM_API_HASH is missing or not configured.")

        if errors:
            msg = (
                "Configuration validation failed:\n"
                + "\n".join(f"  - {err}" for err in errors)
                + "\n\nPlease obtain credentials from https://my.telegram.org and "
                "populate them in your .env file:\n"
                "  cp .env.example .env\n"
            )
            raise ValueError(msg)


def load_config(env_path: Optional[str] = None) -> Config:
    """Load configuration from environment variables and .env file.

    Args:
        env_path: Optional custom path to .env file.

    Returns:
        Config object populated with loaded settings.
    """
    if env_path:
        load_dotenv(dotenv_path=Path(env_path), override=True)
    else:
        load_dotenv(override=True)

    raw_api_id = os.getenv("TELEGRAM_API_ID")
    telegram_api_id: Optional[int] = None
    if raw_api_id and raw_api_id.strip():
        try:
            telegram_api_id = int(raw_api_id.strip())
        except ValueError:
            telegram_api_id = None

    telegram_api_hash = os.getenv("TELEGRAM_API_HASH")
    telegram_phone = os.getenv("TELEGRAM_PHONE")
    telegram_session_name = os.getenv("TELEGRAM_SESSION_NAME", "telegram_crawler")
    default_channel = os.getenv("DEFAULT_CHANNEL", "anarconomy")
    database_path = os.getenv("DATABASE_PATH", "crawler.db")

    try:
        batch_size = int(os.getenv("BATCH_SIZE", "100"))
    except ValueError:
        batch_size = 100

    try:
        rate_limit_delay = float(os.getenv("RATE_LIMIT_DELAY", "1.0"))
    except ValueError:
        rate_limit_delay = 1.0

    log_level = os.getenv("LOG_LEVEL", "INFO")

    # Media download configurations
    download_media_env = os.getenv("DOWNLOAD_MEDIA", "true").strip().lower()
    download_media = download_media_env in ("true", "1", "yes", "on")
    media_directory = os.getenv("MEDIA_DIRECTORY", "media").strip() or "media"

    try:
        media_max_retries = int(os.getenv("MEDIA_MAX_RETRIES", "3"))
    except ValueError:
        media_max_retries = 3

    try:
        media_retry_delay = float(os.getenv("MEDIA_RETRY_DELAY", "5.0"))
    except ValueError:
        media_retry_delay = 5.0

    return Config(
        telegram_api_id=telegram_api_id,
        telegram_api_hash=telegram_api_hash,
        telegram_phone=telegram_phone,
        telegram_session_name=telegram_session_name,
        default_channel=default_channel,
        database_path=database_path,
        batch_size=batch_size,
        rate_limit_delay=rate_limit_delay,
        log_level=log_level,
        download_media=download_media,
        media_directory=media_directory,
        media_max_retries=media_max_retries,
        media_retry_delay=media_retry_delay,
    )
