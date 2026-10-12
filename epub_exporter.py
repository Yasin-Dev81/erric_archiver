"""Export crawled Telegram messages to an EPUB 3 e-book.

Only downloaded photos are embedded from the media archive; videos, audio,
voice notes, animations and documents are ignored (their captions are kept).
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import html
from pathlib import Path
import re
import sqlite3
import sys
from typing import Dict, Iterable, List, Optional, Tuple
import uuid
import zipfile

from config import load_config
from database import Database
from logger import setup_logger
from models import ChannelInfo

logger = setup_logger("epub_exporter")

RTL_LANGUAGES = {"fa", "ar", "he", "ur", "ps", "ku", "yi"}
IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
URL_RE = re.compile(r"(https?://[^\s<>\"']+)")
# Characters that are not allowed in XML 1.0 documents
INVALID_XML_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

STYLESHEET = """\
body { font-family: serif; line-height: 1.6; margin: 0 4%; }
h1 { text-align: center; margin: 1.5em 0 1em; }
.post { margin: 0 0 1.5em; padding-bottom: 1em; border-bottom: 1px solid #ccc; }
.meta { font-size: 0.8em; color: #777; margin-bottom: 0.4em; }
.meta a { color: inherit; }
.text { margin: 0.4em 0; white-space: normal; }
.photo { margin: 0.6em 0; text-align: center; page-break-inside: avoid; }
.photo img { max-width: 100%; max-height: 95vh; height: auto; }
.title-page { text-align: center; margin-top: 30%; }
.title-page .desc { margin-top: 2em; font-size: 0.95em; }
"""


@dataclass
class Photo:
    """A photo file embedded in the book."""

    source: Path
    href: str  # Path inside the EPUB, relative to OEBPS/
    media_type: str
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class Post:
    """A single Telegram post (an album is merged into one post)."""

    message_id: int
    date: datetime
    texts: List[str] = field(default_factory=list)
    photos: List[Photo] = field(default_factory=list)


@dataclass
class Chapter:
    """A month of posts, stored as one XHTML file."""

    key: str  # YYYY-MM
    file_name: str
    posts: List[Post] = field(default_factory=list)


def _clean(text: str) -> str:
    return INVALID_XML_RE.sub("", text)


def _parse_date(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _format_text(text: str) -> str:
    """Escape message text, linkify URLs and keep line breaks."""
    parts = []
    for i, chunk in enumerate(URL_RE.split(_clean(text))):
        if i % 2:
            url = html.escape(chunk, quote=True)
            parts.append(f'<a href="{url}">{html.escape(chunk)}</a>')
        else:
            parts.append(html.escape(chunk))
    return "<br/>".join("".join(parts).splitlines())


def resolve_channel(db: Database, channel: str) -> Optional[ChannelInfo]:
    """Find a crawled channel by username, t.me link, or numeric ID."""
    value = channel.strip()
    value = re.sub(r"^(https?://)?(www\.)?(t|telegram)\.me/", "", value).strip("/")
    if value.lstrip("-").isdigit():
        num = int(value)
        if str(num).startswith("-100"):
            num = int(str(num)[4:])
        return db.get_channel(abs(num))
    return db.get_channel_by_username(value)


def load_photos(
    conn: sqlite3.Connection, channel_id: int, base_dir: Path
) -> Dict[int, List[Photo]]:
    """Return downloaded photos that exist on disk, keyed by Telegram message ID."""
    cursor = conn.execute(
        """
        SELECT telegram_message_id, file_path, mime_type, width, height
        FROM media
        WHERE channel_id = ? AND media_type = 'photo' AND download_status = 'downloaded'
        ORDER BY telegram_message_id ASC, id ASC;
        """,
        (channel_id,),
    )
    photos: Dict[int, List[Photo]] = {}
    missing = 0
    for row in cursor:
        source = Path(row["file_path"])
        if not source.is_absolute():
            source = base_dir / source
        if not source.is_file():
            missing += 1
            continue
        suffix = source.suffix.lower()
        media_type = IMAGE_MEDIA_TYPES.get(suffix) or row["mime_type"] or "image/jpeg"
        if suffix not in IMAGE_MEDIA_TYPES:
            suffix = ".jpg"
        msg_id = row["telegram_message_id"]
        index = len(photos.get(msg_id, []))
        photos.setdefault(msg_id, []).append(
            Photo(
                source=source,
                href=f"images/{msg_id}_{index}{suffix}",
                media_type=media_type,
                width=row["width"],
                height=row["height"],
            )
        )
    if missing:
        logger.warning("Skipped %d photo(s) missing from disk.", missing)
    return photos


def load_posts(
    conn: sqlite3.Connection,
    channel_id: int,
    photos: Dict[int, List[Photo]],
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> List[Post]:
    """Load messages in chronological order, merging albums into single posts.

    Messages with neither text nor photos (e.g. a video without caption) are dropped.
    """
    cursor = conn.execute(
        """
        SELECT id, date, text, grouped_id FROM messages
        WHERE channel_id = ? AND crawl_status = 'success'
        ORDER BY id ASC;
        """,
        (channel_id,),
    )
    posts: List[Post] = []
    albums: Dict[int, Post] = {}
    for row in cursor:
        date = _parse_date(row["date"])
        if since and date < since:
            continue
        if until and date >= until:
            continue

        text = (row["text"] or "").strip()
        msg_photos = photos.get(row["id"], [])
        grouped_id = row["grouped_id"]

        post = albums.get(grouped_id) if grouped_id is not None else None
        if post is None:
            post = Post(message_id=row["id"], date=date)
            posts.append(post)
            if grouped_id is not None:
                albums[grouped_id] = post
        if text:
            post.texts.append(text)
        post.photos.extend(msg_photos)

    return [p for p in posts if p.texts or p.photos]


def group_into_chapters(posts: Iterable[Post]) -> List[Chapter]:
    """Split posts into one chapter per calendar month."""
    chapters: List[Chapter] = []
    for post in posts:
        key = post.date.strftime("%Y-%m")
        if not chapters or chapters[-1].key != key:
            chapters.append(Chapter(key=key, file_name=f"chapter_{key}.xhtml"))
        chapters[-1].posts.append(post)
    return chapters


def _xhtml(title: str, body: str, language: str, direction: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'xml:lang="{language}" lang="{language}" dir="{direction}">\n'
        "<head>\n"
        f"<title>{html.escape(_clean(title))}</title>\n"
        '<link rel="stylesheet" type="text/css" href="style.css"/>\n'
        "</head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def render_chapter(
    chapter: Chapter, channel: ChannelInfo, language: str, direction: str
) -> str:
    """Render a month of posts as an XHTML document."""
    blocks = [f"<h1>{chapter.key}</h1>"]
    for post in chapter.posts:
        meta = post.date.strftime("%Y-%m-%d %H:%M")
        if channel.username:
            link = f"https://t.me/{channel.username}/{post.message_id}"
            meta = f'<a href="{html.escape(link)}">{meta}</a>'
        parts = [f'<section class="post" id="post-{post.message_id}">',
                 f'<div class="meta" dir="ltr">{meta}</div>']
        for photo in post.photos:
            size = ""
            if photo.width and photo.height:
                size = f' width="{photo.width}" height="{photo.height}"'
            parts.append(
                f'<div class="photo"><img src="{photo.href}" alt=""{size}/></div>'
            )
        for text in post.texts:
            parts.append(f'<p class="text" dir="auto">{_format_text(text)}</p>')
        parts.append("</section>")
        blocks.append("\n".join(parts))
    return _xhtml(chapter.key, "\n".join(blocks), language, direction)


def render_title_page(channel: ChannelInfo, language: str, direction: str) -> str:
    body = ['<div class="title-page">', f"<h1>{html.escape(_clean(channel.title))}</h1>"]
    if channel.username:
        body.append(f'<p dir="ltr">@{html.escape(channel.username)}</p>')
    if channel.description:
        body.append(f'<p class="desc" dir="auto">{_format_text(channel.description)}</p>')
    body.append("</div>")
    return _xhtml(channel.title, "\n".join(body), language, direction)


def render_nav(
    title: str, chapters: List[Chapter], language: str, direction: str
) -> str:
    """Render the EPUB 3 navigation document, grouping months under years."""
    years: Dict[str, List[Chapter]] = {}
    for chapter in chapters:
        years.setdefault(chapter.key[:4], []).append(chapter)

    items = ['<li><a href="title.xhtml">' + html.escape(_clean(title)) + "</a></li>"]
    for year, year_chapters in years.items():
        months = "\n".join(
            f'<li><a href="{c.file_name}">{c.key}</a></li>' for c in year_chapters
        )
        items.append(
            f'<li><a href="{year_chapters[0].file_name}">{year}</a>\n<ol>\n{months}\n</ol></li>'
        )
    body = (
        '<nav epub:type="toc" id="toc">\n<h1>Contents</h1>\n<ol>\n'
        + "\n".join(items)
        + "\n</ol>\n</nav>"
    )
    return _xhtml("Contents", body, language, direction)


def render_ncx(book_id: str, title: str, chapters: List[Chapter]) -> str:
    """Render the EPUB 2 NCX table of contents for older readers."""
    points = [("title", title, "title.xhtml")] + [
        (c.key, c.key, c.file_name) for c in chapters
    ]
    nav_points = "\n".join(
        f'<navPoint id="np-{i}" playOrder="{i}"><navLabel><text>{html.escape(_clean(label))}'
        f'</text></navLabel><content src="{src}"/></navPoint>'
        for i, (_, label, src) in enumerate(points, start=1)
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'<head><meta name="dtb:uid" content="{book_id}"/></head>\n'
        f"<docTitle><text>{html.escape(_clean(title))}</text></docTitle>\n"
        f"<navMap>\n{nav_points}\n</navMap>\n</ncx>\n"
    )


def render_opf(
    book_id: str,
    title: str,
    channel: ChannelInfo,
    chapters: List[Chapter],
    photos: List[Photo],
    language: str,
    direction: str,
) -> str:
    """Render the package document (metadata, manifest and spine)."""
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
        '<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine = ['<itemref idref="title"/>']
    for i, chapter in enumerate(chapters):
        manifest.append(
            f'<item id="ch{i}" href="{chapter.file_name}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="ch{i}"/>')
    for i, photo in enumerate(photos):
        manifest.append(
            f'<item id="img{i}" href="{photo.href}" media-type="{photo.media_type}"/>'
        )

    creator = channel.username and f"@{channel.username}" or channel.title
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        f'unique-identifier="book-id" xml:lang="{language}">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'<dc:identifier id="book-id">urn:uuid:{book_id}</dc:identifier>\n'
        f"<dc:title>{html.escape(_clean(title))}</dc:title>\n"
        f"<dc:creator>{html.escape(_clean(creator))}</dc:creator>\n"
        f"<dc:language>{language}</dc:language>\n"
        f'<meta property="dcterms:modified">{modified}</meta>\n'
        "</metadata>\n"
        "<manifest>\n" + "\n".join(manifest) + "\n</manifest>\n"
        f'<spine toc="ncx" page-progression-direction="{direction}">\n'
        + "\n".join(spine)
        + "\n</spine>\n</package>\n"
    )


def write_epub(
    output_path: Path,
    channel: ChannelInfo,
    posts: List[Post],
    language: str = "fa",
    title: Optional[str] = None,
) -> Tuple[int, int]:
    """Write posts to an EPUB file.

    Returns:
        A tuple of (number of posts, number of embedded photos).
    """
    direction = "rtl" if language.split("-")[0].lower() in RTL_LANGUAGES else "ltr"
    title = title or channel.title
    book_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"telegram:{channel.id}:{title}"))
    chapters = group_into_chapters(posts)
    photos = [photo for post in posts for photo in post.photos]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".part")
    with zipfile.ZipFile(tmp_path, "w") as zf:
        # The mimetype entry must come first and be stored uncompressed.
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles>\n'
            "</container>\n",
            compress_type=zipfile.ZIP_DEFLATED,
        )

        def text_entry(name: str, content: str) -> None:
            zf.writestr(f"OEBPS/{name}", content, compress_type=zipfile.ZIP_DEFLATED)

        text_entry(
            "content.opf",
            render_opf(book_id, title, channel, chapters, photos, language, direction),
        )
        text_entry("toc.ncx", render_ncx(book_id, title, chapters))
        text_entry("nav.xhtml", render_nav(title, chapters, language, direction))
        text_entry("style.css", STYLESHEET)
        text_entry("title.xhtml", render_title_page(channel, language, direction))
        for chapter in chapters:
            text_entry(chapter.file_name, render_chapter(chapter, channel, language, direction))

        # Images are already compressed; store them as-is and stream from disk.
        for i, photo in enumerate(photos, start=1):
            zf.write(photo.source, f"OEBPS/{photo.href}", compress_type=zipfile.ZIP_STORED)
            if i % 500 == 0:
                logger.info("  Embedded %d/%d photos...", i, len(photos))

    tmp_path.replace(output_path)
    return len(posts), len(photos)


def _parse_cli_date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}', expected YYYY-MM-DD") from exc


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a crawled Telegram channel to EPUB (photos only from media)."
    )
    parser.add_argument("--channel", help="Channel username, t.me link, or numeric ID "
                        "(defaults to DEFAULT_CHANNEL from .env)")
    parser.add_argument("--db", help="SQLite database path (defaults to DATABASE_PATH)")
    parser.add_argument("--output", "-o", help="Output .epub path (default: <channel>.epub)")
    parser.add_argument("--title", help="Book title (default: channel title)")
    parser.add_argument("--language", default="fa", help="Book language code (default: fa)")
    parser.add_argument("--since", type=_parse_cli_date, help="Include posts on/after YYYY-MM-DD")
    parser.add_argument("--until", type=_parse_cli_date, help="Include posts before YYYY-MM-DD")
    parser.add_argument("--split-by-year", action="store_true",
                        help="Write one EPUB per year (<output>_<year>.epub)")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config()
    db_path = args.db or config.database_path
    if not Path(db_path).is_file():
        logger.error("Database not found: %s", db_path)
        return 1

    with Database(db_path) as db:
        channel_ref = args.channel or config.default_channel
        channel = resolve_channel(db, channel_ref)
        if channel is None:
            logger.error("Channel '%s' not found in %s. Crawl it first.", channel_ref, db_path)
            return 1

        conn = db.get_connection()
        base_dir = Path(db_path).resolve().parent
        photos = load_photos(conn, channel.id, base_dir)
        posts = load_posts(conn, channel.id, photos, args.since, args.until)

    if not posts:
        logger.error("No posts found for '%s' in the selected range.", channel.title)
        return 1

    output = Path(args.output or f"{channel.username or channel.id}.epub")
    if args.split_by_year:
        by_year: Dict[int, List[Post]] = {}
        for post in posts:
            by_year.setdefault(post.date.year, []).append(post)
        jobs = [
            (output.with_name(f"{output.stem}_{year}{output.suffix or '.epub'}"),
             year_posts, f"{args.title or channel.title} — {year}")
            for year, year_posts in sorted(by_year.items())
        ]
    else:
        jobs = [(output, posts, args.title)]

    for path, job_posts, title in jobs:
        logger.info("Writing %s (%d posts)...", path, len(job_posts))
        n_posts, n_photos = write_epub(path, channel, job_posts, args.language, title)
        size_mb = path.stat().st_size / (1024 * 1024)
        logger.info("Done: %s — %d posts, %d photos, %.1f MB", path, n_posts, n_photos, size_mb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
