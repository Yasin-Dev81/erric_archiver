"""Unit tests for configuration loading and validation."""

import os
import pytest
from config import Config, load_config


def test_config_validation_success():
    """Verify validation passes when credentials are present."""
    config = Config(
        telegram_api_id=123456,
        telegram_api_hash="valid_hash_12345",
        telegram_phone="+1234567890",
        telegram_session_name="test_session",
        default_channel="testchannel",
        database_path="test.db",
        batch_size=50,
        rate_limit_delay=0.5,
        log_level="DEBUG",
    )
    # Should not raise
    config.validate()


def test_config_validation_failure():
    """Verify validation fails when credentials are missing or default placeholders."""
    # Missing api_id
    config = Config(
        telegram_api_id=None,
        telegram_api_hash="valid_hash",
        telegram_phone=None,
        telegram_session_name="test",
        default_channel="test",
        database_path="test.db",
        batch_size=100,
        rate_limit_delay=1.0,
        log_level="INFO",
    )
    with pytest.raises(ValueError, match="TELEGRAM_API_ID is missing"):
        config.validate()

    # Placeholder api_hash
    config2 = Config(
        telegram_api_id=12345,
        telegram_api_hash="your_api_hash_here",
        telegram_phone=None,
        telegram_session_name="test",
        default_channel="test",
        database_path="test.db",
        batch_size=100,
        rate_limit_delay=1.0,
        log_level="INFO",
    )
    with pytest.raises(ValueError, match="TELEGRAM_API_HASH is missing"):
        config2.validate()


def test_load_config_from_custom_env(tmp_path):
    """Verify loading settings from an environment file."""
    env_file = tmp_path / ".env.custom"
    env_file.write_text(
        "TELEGRAM_API_ID=987654\n"
        "TELEGRAM_API_HASH=my_super_secret_hash\n"
        "TELEGRAM_PHONE=+9988776655\n"
        "DEFAULT_CHANNEL=my_channel\n"
        "DATABASE_PATH=custom.db\n"
        "BATCH_SIZE=25\n"
        "RATE_LIMIT_DELAY=2.5\n"
        "LOG_LEVEL=WARNING\n"
        "DOWNLOAD_MEDIA=false\n"
        "MEDIA_DIRECTORY=my_media\n"
        "MEDIA_MAX_RETRIES=5\n"
        "MEDIA_RETRY_DELAY=10\n",
        encoding="utf-8",
    )

    cfg = load_config(str(env_file))
    assert cfg.telegram_api_id == 987654
    assert cfg.telegram_api_hash == "my_super_secret_hash"
    assert cfg.telegram_phone == "+9988776655"
    assert cfg.default_channel == "my_channel"
    assert cfg.database_path == "custom.db"
    assert cfg.batch_size == 25
    assert cfg.rate_limit_delay == 2.5
    assert cfg.log_level == "WARNING"
    assert cfg.download_media is False
    assert cfg.media_directory == "my_media"
    assert cfg.media_max_retries == 5
    assert cfg.media_retry_delay == 10.0
