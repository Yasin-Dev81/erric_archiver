"""Main CLI entrypoint for Telegram channel crawler."""

import argparse
import asyncio
import csv
import json
from pathlib import Path
import sys
from typing import Optional

from config import Config, load_config
from database import Database
from logger import logger, setup_logger
from media_downloader import MediaDownloader, retry_failed_media
from telegram_client import TelegramCrawlerClient, clean_channel_identifier


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="telegram-crawler",
        description="Crawl and archive Telegram channel messages and media into SQLite using Telethon and uv.",
    )
    parser.add_argument(
        "--channel",
        "-c",
        type=str,
        default=None,
        help="Target channel username, URL, or ID (e.g., 'anarconomy' or 'https://t.me/anarconomy').",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Maximum number of messages to fetch (default: all available).",
    )
    parser.add_argument(
        "--min-id",
        type=int,
        default=0,
        help="Only fetch messages newer than this message ID.",
    )
    parser.add_argument(
        "--max-id",
        type=int,
        default=0,
        help="Only fetch messages older than this message ID.",
    )
    parser.add_argument(
        "--batch-size",
        "-b",
        type=int,
        default=None,
        help="Number of messages fetched per Telegram batch request (default: from config).",
    )
    parser.add_argument(
        "--rate-limit-delay",
        "-d",
        type=float,
        default=None,
        help="Delay in seconds between batch requests to avoid Telegram flood limits (default: from config).",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to the SQLite database file (default: from config).",
    )
    parser.add_argument(
        "--info-only",
        action="store_true",
        help="Fetch and display channel information only, without fetching messages.",
    )
    parser.add_argument(
        "--incremental",
        "-i",
        action="store_true",
        help="Automatically resume crawl by fetching only messages newer than the latest message in the database.",
    )
    parser.add_argument(
        "--export",
        type=str,
        default=None,
        metavar="FILE",
        help="Export stored channel messages to a JSON or CSV file (e.g., 'messages.json' or 'messages.csv').",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Path to custom .env file (default: '.env').",
    )
    # Media download arguments
    parser.add_argument(
        "--download-media",
        action="store_true",
        default=None,
        help="Enable downloading and storing media files locally (default from .env).",
    )
    parser.add_argument(
        "--no-download-media",
        action="store_true",
        help="Disable downloading media files locally.",
    )
    parser.add_argument(
        "--media-dir",
        type=str,
        default=None,
        help="Directory to store downloaded media (default: from config).",
    )
    parser.add_argument(
        "--retry-media",
        action="store_true",
        help="Retry previously failed or missing media downloads without re-crawling the entire channel.",
    )
    return parser.parse_args(argv)


def export_messages(db: Database, channel_id: int, file_path: str) -> None:
    """Export messages from the database to JSON or CSV."""
    path = Path(file_path)
    messages = db.get_messages(channel_id=channel_id, limit=100000)
    logger.info("Exporting %d messages to %s...", len(messages), path)

    if path.suffix.lower() == ".json":
        data = [m.to_dict() for m in messages]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    elif path.suffix.lower() == ".csv":
        if not messages:
            logger.warning("No messages found to export.")
            return
        fieldnames = list(messages[0].to_dict().keys())
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for m in messages:
                writer.writerow(m.to_dict())
    else:
        # Default to JSON format
        data = [m.to_dict() for m in messages]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info("Successfully exported messages to %s", path)


async def crawl_channel(args: argparse.Namespace, config: Config) -> int:
    """Execute the channel crawling and media downloading pipeline."""
    channel_target = args.channel or config.default_channel
    if not channel_target:
        logger.error("No target channel specified. Use --channel or set DEFAULT_CHANNEL in .env.")
        return 1

    # Resolve settings and overrides
    batch_size = args.batch_size if args.batch_size is not None else config.batch_size
    rate_limit_delay = (
        args.rate_limit_delay if args.rate_limit_delay is not None else config.rate_limit_delay
    )
    db_path = args.db if args.db is not None else config.database_path

    if args.media_dir:
        config.media_directory = args.media_dir

    if args.no_download_media:
        download_media = False
    elif args.download_media:
        download_media = True
    else:
        download_media = config.download_media

    logger.info("Starting Telegram Crawler for target: '%s'", channel_target)
    logger.info(
        "Database: %s | Batch size: %d | Delay: %.2fs | Download Media: %s",
        db_path,
        batch_size,
        rate_limit_delay,
        download_media,
    )

    db = Database(db_path=db_path)
    downloader = MediaDownloader(config=config, db=db)

    async with TelegramCrawlerClient(config) as crawler_client:
        entity = await crawler_client.get_channel_entity(channel_target)
        channel_info = await crawler_client.get_channel_info(entity)

        # Save channel metadata
        db.save_channel(channel_info)

        print("\n" + "=" * 60)
        print(f" CHANNEL INFO: {channel_info.title}")
        print("=" * 60)
        print(f" ID:           {channel_info.id}")
        print(f" Username:     @{channel_info.username or 'N/A'}")
        print(f" Subscribers:  {channel_info.member_count or 'N/A'}")
        print(f" Broadcast:    {channel_info.is_broadcast}")
        if channel_info.description:
            print(f" Description:  {channel_info.description.strip()[:150]}...")
        print("=" * 60 + "\n")

        # Handle --retry-media standalone option
        if args.retry_media:
            await retry_failed_media(
                crawler_client=crawler_client,
                entity=entity,
                db=db,
                channel_info=channel_info,
                downloader=downloader,
            )
            db.close()
            return 0

        if args.info_only:
            logger.info("Channel info retrieved successfully (--info-only specified). Exiting.")
            db.close()
            return 0

        # Run media audit & recovery before crawling
        downloader.audit_and_recover_media(channel_id=channel_info.id)

        # Determine min_id for incremental crawl
        min_id = args.min_id
        if args.incremental and min_id == 0:
            latest_db_id = db.get_latest_message_id(channel_info.id)
            if latest_db_id:
                min_id = latest_db_id
                logger.info("Incremental crawl: Resuming from latest DB message ID: %d", min_id)

        # Crawl messages
        logger.info(
            "Fetching messages (limit=%s, min_id=%d, max_id=%d)...",
            args.limit if args.limit is not None else "all",
            min_id,
            args.max_id,
        )

        batch = []
        total_fetched = 0
        channel_name = channel_info.username or f"channel_{channel_info.id}"

        async for message in crawler_client.iter_messages(
            entity=entity,
            limit=args.limit,
            offset_id=0,
            min_id=min_id,
            max_id=args.max_id,
            batch_size=batch_size,
            rate_limit_delay=rate_limit_delay,
        ):
            # Check if message is deleted / service message
            raw_msg = message.raw_message
            if raw_msg is None:
                message.crawl_status = "deleted"
                logger.info("[%d] DELETED", message.id)
            else:
                logger.info("[%d] SUCCESS", message.id)

            # Download media if message has media and download is enabled
            if download_media and raw_msg is not None and getattr(raw_msg, "media", None) is not None:
                await downloader.download_media(
                    client=crawler_client,
                    raw_msg=raw_msg,
                    channel_identifier=channel_name,
                    channel_id=channel_info.id,
                )

            batch.append(message)
            total_fetched += 1

            if len(batch) >= batch_size:
                db.save_messages(batch)
                logger.info("Saved batch of %d messages (Total fetched: %d)", len(batch), total_fetched)
                batch.clear()

        # Save remaining messages
        if batch:
            db.save_messages(batch)
            logger.info("Saved final batch of %d messages (Total fetched: %d)", len(batch), total_fetched)
            batch.clear()

        stats = db.get_crawl_stats(channel_info.id)
        msg_stats = stats["messages"]
        med_stats = stats["media"]

        print("\n" + "-" * 60)
        print(" CRAWL SUMMARY")
        print("-" * 60)
        print("Messages:")
        print(f"  Success: {msg_stats['success']}")
        print(f"  Deleted: {msg_stats['deleted']}")
        print(f"  Failed:  {msg_stats['failed']}\n")
        print("Media:")
        print(f"  Total:      {med_stats['total']}")
        print(f"  Downloaded: {med_stats['downloaded']}")
        print(f"  Failed:     {med_stats['failed']}")
        print(f"  Missing:    {med_stats['missing']}")
        print("-" * 60 + "\n")

        if args.export:
            export_messages(db, channel_info.id, args.export)

    db.close()
    return 0


def main(argv=None) -> int:
    """Main application entrypoint."""
    args = parse_args(argv)
    config = load_config(args.env_file)
    setup_logger(level=config.log_level)

    try:
        return asyncio.run(crawl_channel(args, config))
    except KeyboardInterrupt:
        logger.info("\nCrawling interrupted by user. Exiting gracefully.")
        return 130
    except ValueError as val_err:
        logger.error(str(val_err))
        return 1
    except Exception as exc:
        logger.exception("An unexpected error occurred: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
