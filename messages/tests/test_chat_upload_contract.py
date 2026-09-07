"""Regression contract for Studio/chat attachments."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import re
import sys

from fastapi.responses import FileResponse


MESSAGES_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MESSAGES_DIR))

import chat_server  # noqa: E402
from chat_db import ChatDB  # noqa: E402


def test_modern_media_extensions_are_preserved() -> None:
    assert chat_server._classify_upload("photo.heic", "image/heic") == ("image", ".heic")
    assert chat_server._classify_upload("photo.avif", "image/avif") == ("image", ".avif")
    assert chat_server._classify_upload("voice.m4a", "audio/mp4") == ("audio", ".m4a")
    assert chat_server._classify_upload("voice.flac", "audio/flac") == ("audio", ".flac")
    assert chat_server._classify_upload("clip.mp4", "video/mp4") == ("video", ".mp4")


def test_active_and_unknown_formats_are_download_only() -> None:
    assert chat_server._classify_upload("vector.svg", "image/svg+xml") == ("file", ".svg")
    assert chat_server._classify_upload("page.html", "image/png") == ("image", ".png")
    assert chat_server._classify_upload("tool.exe", "application/octet-stream") == ("file", ".exe")
    assert chat_server._classify_upload("notes.json", "application/json") == ("file", ".json")


def test_inline_signatures_reject_spoofed_content() -> None:
    assert chat_server._upload_signature_matches("image", "image/png", b"\x89PNG\r\n\x1a\nrest")
    assert not chat_server._upload_signature_matches("image", "image/png", b"<html>not a png")
    assert chat_server._upload_signature_matches("pdf", "application/octet-stream", b"%PDF-1.7")
    assert not chat_server._upload_signature_matches("pdf", "application/pdf", b"not a pdf")


def test_generic_downloads_do_not_require_content_sniffing() -> None:
    assert chat_server._upload_signature_matches("file", "application/octet-stream", b"anything")


def test_download_media_types_do_not_label_archives_as_plain_text() -> None:
    assert chat_server._download_media_type("archive_bundle.gz") == "application/gzip"
    assert chat_server._download_media_type("archive_bundle.zip") == "application/zip"
    assert chat_server._download_media_type("archive_bundle.7z") == "application/x-7z-compressed"
    assert chat_server._download_media_type("document_report.md") == "text/markdown"
    assert chat_server._download_media_type("file_tool.exe") == "application/octet-stream"


def test_upload_path_streams_and_fails_on_db_persistence() -> None:
    source = Path(chat_server.__file__).read_text(encoding="utf-8")
    block = source.split('@app.post("/api/upload")', 1)[1].split('@app.get("/uploads/{filename}")', 1)[0]
    assert "await file.read(UPLOAD_CHUNK_SIZE)" in block
    assert "await file.read()" not in block
    assert "upload persistence failed" in block
    assert "fpath.unlink(missing_ok=True)" in block
    assert "archivo muy grande (max 300MB)" in block


def test_private_upload_download_resolves_ownership_under_user_rls(tmp_path, monkeypatch) -> None:
    upload = tmp_path / "image_test.png"
    upload.write_bytes(b"\x89PNG\r\n\x1a\ncontent")
    observed = {}

    class FakeConn:
        async def fetchrow(self, query, file_url):
            observed["query"] = query
            observed["file_url"] = file_url
            return {"channel": "user:3:gtl-sistemas"}

    @asynccontextmanager
    async def fake_rls(uid, identity):
        observed["rls"] = (uid, identity)
        yield FakeConn()

    class FakeDB:
        pool = object()

        async def user_can_access_channel(self, uid, channel):
            observed["acl"] = (uid, channel)
            return True

    monkeypatch.setattr(chat_server, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(chat_server, "_rls_conn", fake_rls)
    monkeypatch.setattr(chat_server, "chat_db", FakeDB())
    response = asyncio.run(chat_server.serve_upload(
        upload.name,
        user={"sub": "3", "username": "henry", "role": "admin"},
    ))
    assert isinstance(response, FileResponse)
    assert observed["rls"] == (3, "henry")
    assert observed["acl"] == (3, "user:3:gtl-sistemas")
    assert observed["file_url"] == "/uploads/image_test.png"


def test_general_upload_alias_is_public_even_if_registry_only_has_legacy_name() -> None:
    """Studio renders General as web_chat; the authenticated upload must match it."""

    class FailIfQueriedPool:
        async def fetchrow(self, *_args, **_kwargs):
            raise AssertionError("canonical public aliases must not depend on registry rows")

    db = ChatDB()
    db.pool = FailIfQueriedPool()
    assert asyncio.run(db.user_can_access_channel(3, "web_chat")) is True
    assert asyncio.run(db.user_can_access_channel(3, "general")) is True


def test_upload_channel_normalizes_only_public_general_aliases() -> None:
    assert chat_server._canonical_upload_channel("web_chat") == "web_chat"
    assert chat_server._canonical_upload_channel(" General ") == "web_chat"
    assert chat_server._canonical_upload_channel("#General") == "web_chat"
    assert chat_server._canonical_upload_channel("#GENERAL") == "web_chat"
    assert chat_server._canonical_upload_channel("user:3:General") == "user:3:General"
    assert chat_server._canonical_upload_channel("dm:henry:ADA") == "dm:henry:ADA"


def test_upload_cors_covers_known_studio_clients_without_open_wildcard() -> None:
    allowed = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://192.168.68.200:5173",
        "http://100.75.201.110:5173",
        "http://192.168.68.200:3001",
    )
    for origin in allowed:
        assert re.fullmatch(chat_server._CORS_ORIGIN_REGEX, origin), origin
    assert not re.fullmatch(chat_server._CORS_ORIGIN_REGEX, "https://evil.example")
    assert not re.fullmatch(chat_server._CORS_ORIGIN_REGEX, "http://192.168.69.5:5173")
