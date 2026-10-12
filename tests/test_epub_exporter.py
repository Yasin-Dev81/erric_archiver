"""Unit tests for the EPUB exporter."""

import xml.etree.ElementTree as ET
import zipfile

import pytest

from database import Database
from epub_exporter import load_photos, load_posts, main, resolve_channel, write_epub
from models import ChannelInfo, MediaItem, Message


@pytest.fixture
def archive(tmp_path):
    """Build a small database with photos, an album, a video and a text post."""
    db_path = tmp_path / "crawler.db"
    db = Database(db_path=str(db_path))
    channel = ChannelInfo(id=42, title="کانال تست", username="testchan", description="توضیح <b>")
    db.save_channel(channel)
    db.save_messages([
        Message(id=1, channel_id=42, date="2020-01-05T10:00:00+00:00", text="سلام https://example.com/a?b=1&c=2"),
        Message(id=2, channel_id=42, date="2020-01-06T10:00:00+00:00", text="Album caption",
                media_type="photo", grouped_id=777),
        Message(id=3, channel_id=42, date="2020-01-06T10:00:01+00:00", media_type="photo", grouped_id=777),
        Message(id=4, channel_id=42, date="2021-02-01T10:00:00+00:00", media_type="video"),
        Message(id=5, channel_id=42, date="2021-02-02T10:00:00+00:00", text="video caption", media_type="video"),
    ])

    def add_media(msg_id, media_type, name):
        path = tmp_path / "media" / "testchan" / str(msg_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8fakejpeg")
        db.save_media(MediaItem(
            message_id=msg_id, channel_id=42, telegram_message_id=msg_id, media_type=media_type,
            file_name=name, file_path=str(path.relative_to(tmp_path)), mime_type="image/jpeg",
            width=800, height=600, download_status="downloaded",
        ))

    add_media(2, "photo", "photo_2.jpg")
    add_media(3, "photo", "photo_3.jpg")
    add_media(4, "video", "video_4.mp4")
    add_media(5, "video", "video_5.mp4")
    yield db, channel, tmp_path
    db.close()


def test_resolve_channel(archive):
    db, _, _ = archive
    assert resolve_channel(db, "@testchan").id == 42
    assert resolve_channel(db, "https://t.me/testchan").id == 42
    assert resolve_channel(db, "-10042").id == 42
    assert resolve_channel(db, "unknown") is None


def test_load_posts_only_photos_and_albums_merged(archive):
    db, _, base = archive
    conn = db.get_connection()
    photos = load_photos(conn, 42, base)
    assert set(photos) == {2, 3}

    posts = load_posts(conn, 42, photos)
    # Message 4 (video, no caption) is dropped; album 2+3 is merged.
    assert [p.message_id for p in posts] == [1, 2, 5]
    album = posts[1]
    assert album.texts == ["Album caption"]
    assert [p.href for p in album.photos] == ["images/2_0.jpg", "images/3_0.jpg"]
    assert posts[2].photos == [] and posts[2].texts == ["video caption"]


def test_write_epub_structure(archive, tmp_path):
    db, channel, base = archive
    conn = db.get_connection()
    posts = load_posts(conn, 42, load_photos(conn, 42, base))
    out = tmp_path / "out" / "book.epub"

    assert write_epub(out, channel, posts) == (3, 2)

    with zipfile.ZipFile(out) as zf:
        infos = zf.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"

        names = set(zf.namelist())
        assert {"OEBPS/images/2_0.jpg", "OEBPS/images/3_0.jpg"} <= names
        assert not any(n.endswith(".mp4") for n in names)
        assert {"OEBPS/chapter_2020-01.xhtml", "OEBPS/chapter_2021-02.xhtml"} <= names

        # Every XML document must be well-formed.
        for name in names:
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                ET.fromstring(zf.read(name))

        opf = zf.read("OEBPS/content.opf").decode()
        assert 'page-progression-direction="rtl"' in opf
        chapter = zf.read("OEBPS/chapter_2020-01.xhtml").decode()
        assert 'href="https://example.com/a?b=1&amp;c=2"' in chapter
        assert "https://t.me/testchan/2" in chapter


def test_main_split_by_year(archive, tmp_path, monkeypatch):
    _, _, base = archive
    monkeypatch.chdir(base)
    out = tmp_path / "book.epub"
    rc = main(["--db", str(base / "crawler.db"), "--channel", "testchan",
               "--output", str(out), "--split-by-year"])
    assert rc == 0
    assert (tmp_path / "book_2020.epub").is_file()
    assert (tmp_path / "book_2021.epub").is_file()
