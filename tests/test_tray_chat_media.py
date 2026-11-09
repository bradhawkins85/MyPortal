"""Photos, videos and file attachments in the tray chat window."""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from app.api.routes import tray as tray_routes
from app.services import chat_media, matrix_sync

MIGRATION = Path(__file__).parent.parent / "migrations" / "543_chat_message_media.sql"


def test_migration_adds_media_json_column():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "-- phase: expand" in sql
    assert "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS media_json TEXT NULL" in sql


def test_media_from_content_reads_image_details():
    media = chat_media.media_from_content(
        "m.image",
        {
            "body": "IMG_3250.jpeg",
            "url": "mxc://matrix.example.test/AbC123",
            "info": {"mimetype": "image/jpeg", "size": 81234, "w": 1200, "h": 900},
        },
    )
    assert media == {
        "url": "mxc://matrix.example.test/AbC123",
        "filename": "IMG_3250.jpeg",
        "mimetype": "image/jpeg",
        "size": 81234,
        "width": 1200,
        "height": 900,
    }


def test_media_from_content_ignores_encrypted_and_invalid_media():
    assert chat_media.media_from_content("m.file", {"body": "a.pdf", "file": {"url": "mxc://x/y"}}) is None
    assert chat_media.media_from_content("m.image", {"url": "https://evil.test/a.png"}) is None
    assert chat_media.media_from_content("m.text", {"body": "hi"}) is None


def test_media_from_content_reads_location():
    media = chat_media.media_from_content(
        "m.location", {"geo_uri": "geo:-27.531671209864,152.743158427644;u=35"}
    )
    assert media == {"geo_uri": "geo:-27.531671209864,152.743158427644;u=35"}
    assert chat_media.media_from_content("m.location", {"geo_uri": "geo:999,1"}) is None


def test_clean_filename_strips_paths():
    assert chat_media.clean_filename("C:\\Users\\me\\report.pdf") == "report.pdf"
    assert chat_media.clean_filename("../../etc/passwd") == "passwd"
    assert chat_media.clean_filename("") == "attachment"


def _message(msgtype: str, media: dict, **extra) -> dict:
    return {
        "id": 9,
        "room_id": 42,
        "msgtype": msgtype,
        "body": media.get("filename", ""),
        "media_json": json.dumps(media),
        **extra,
    }


def test_popup_message_view_hides_mxc_and_links_to_proxy():
    view = chat_media.popup_message_view(
        _message("m.image", {"url": "mxc://hs/abc", "filename": "a.png", "mimetype": "image/png", "size": 10})
    )
    assert "media_json" not in view
    assert "mxc://" not in json.dumps(view)
    assert view["media"]["kind"] == "image"
    assert view["media"]["url"] == "/api/tray/popup-chat/42/messages/9/media"
    assert view["media"]["download_url"].endswith("/media?download=1")


def test_unsafe_image_types_are_shown_as_files():
    view = chat_media.popup_message_view(
        _message("m.image", {"url": "mxc://hs/abc", "filename": "x.svg", "mimetype": "image/svg+xml"})
    )
    assert view["media"]["kind"] == "file"


def test_location_view_has_coordinates():
    view = chat_media.popup_message_view(_message("m.location", {"geo_uri": "geo:-27.5,152.7;u=35"}))
    assert view["media"] == {"kind": "location", "latitude": -27.5, "longitude": 152.7}


def test_plain_messages_have_no_media():
    assert chat_media.popup_message_view({"id": 1, "room_id": 2, "body": "hi"})["media"] is None


@pytest.mark.asyncio
async def test_matrix_sync_stores_attachment_details(monkeypatch):
    added: dict = {}

    async def add_message(**kwargs):
        added.update(kwargs)
        return {"id": 5, **kwargs}

    repo = SimpleNamespace(
        get_room_by_matrix_id=AsyncMock(return_value={"id": 42, "tray_device_id": 7, "status": "open"}),
        get_message_by_event_id=AsyncMock(return_value=None),
        get_chat_user_link=AsyncMock(return_value=None),
        add_message=add_message,
        update_room=AsyncMock(),
        get_participants=AsyncMock(return_value=[{"matrix_user_id": "@tech:hs", "role": "technician"}]),
        list_invites=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(matrix_sync, "chat_repo", repo)
    monkeypatch.setattr(matrix_sync.matrix_service, "get_display_name", AsyncMock(return_value="Tech"))
    monkeypatch.setattr(matrix_sync.tray_chat_notifications, "notify_tray_device_of_chat_message", AsyncMock())
    monkeypatch.setattr(matrix_sync.chat_ticket_sync, "sync_chat_message_to_ticket", AsyncMock())

    await matrix_sync.process_sync_response(
        {
            "rooms": {
                "join": {
                    "!room:hs": {
                        "timeline": {
                            "events": [
                                {
                                    "type": "m.room.message",
                                    "event_id": "$evt",
                                    "sender": "@tech:hs",
                                    "origin_server_ts": 1_700_000_000_000,
                                    "content": {
                                        "msgtype": "m.file",
                                        "body": "RT355.pdf",
                                        "url": "mxc://hs/pdf1",
                                        "info": {"mimetype": "application/pdf", "size": 79000},
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }
    )

    assert added["msgtype"] == "m.file"
    assert json.loads(added["media_json"]) == {
        "url": "mxc://hs/pdf1",
        "filename": "RT355.pdf",
        "mimetype": "application/pdf",
        "size": 79000,
    }


def _session(monkeypatch, *, room_id: int = 42):
    monkeypatch.setattr(
        tray_routes,
        "_parse_popup_session",
        lambda request: {"device_id": 7, "room_id": room_id, "company_id": 3, "csrf": "csrf-token"},
    )
    monkeypatch.setattr(
        tray_routes.tray_repo,
        "get_device_by_id",
        AsyncMock(return_value={"id": 7, "hostname": "PC-1", "status": "active"}),
    )


class _Request:
    def __init__(self, csrf: str = "csrf-token") -> None:
        self.headers = {"X-Tray-CSRF": csrf}


def _upload(data: bytes, filename: str, content_type: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename, headers=Headers({"content-type": content_type}))


@pytest.mark.asyncio
async def test_send_attachment_uploads_to_matrix_and_stores_media(monkeypatch):
    _session(monkeypatch)
    monkeypatch.setattr(tray_routes._settings, "matrix_enabled", True, raising=False)
    room = {"id": 42, "status": "open", "matrix_room_id": "!room:hs"}
    monkeypatch.setattr(tray_routes.chat_repo, "get_room", AsyncMock(return_value=room))
    monkeypatch.setattr(tray_routes.tray_repo, "get_device_by_id", AsyncMock(return_value={"hostname": "PC-1", "status": "active"}))
    upload_media = AsyncMock(return_value="mxc://hs/up1")
    send_media = AsyncMock(return_value={"event_id": "$sent"})
    monkeypatch.setattr(tray_routes.matrix_service, "upload_media", upload_media)
    monkeypatch.setattr(tray_routes.matrix_service, "send_media_message", send_media)
    added: dict = {}

    async def add_message(**kwargs):
        added.update(kwargs)
        return {"id": 77, **kwargs}

    monkeypatch.setattr(tray_routes.chat_repo, "add_message", add_message)
    after = AsyncMock(
        side_effect=lambda **kw: tray_routes._serialise_popup_chat_value(chat_media.popup_message_view(kw["msg"]))
    )
    monkeypatch.setattr(tray_routes, "_after_popup_chat_message", after)

    response = await tray_routes.popup_chat_send_attachment(
        _Request(), 42, _upload(b"\x89PNG data", "C:\\fakepath\\photo.png", "image/png")
    )

    assert response.status_code == 201
    upload_media.assert_awaited_once_with(b"\x89PNG data", content_type="image/png", filename="photo.png")
    send_media.assert_awaited_once()
    assert send_media.await_args.kwargs["msgtype"] == "m.image"
    assert added["msgtype"] == "m.image"
    assert added["matrix_event_id"] == "$sent"
    assert added["sender_matrix_id"] == "@tray-device-7:tray"
    assert json.loads(added["media_json"])["url"] == "mxc://hs/up1"
    body = json.loads(response.body)
    assert body["media"]["kind"] == "image"
    assert body["media"]["url"] == "/api/tray/popup-chat/42/messages/77/media"


@pytest.mark.asyncio
async def test_send_attachment_requires_csrf(monkeypatch):
    _session(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await tray_routes.popup_chat_send_attachment(_Request("wrong"), 42, _upload(b"x", "a.txt", "text/plain"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_send_attachment_rejects_oversized_files(monkeypatch):
    _session(monkeypatch)
    monkeypatch.setattr(tray_routes._settings, "matrix_enabled", True, raising=False)
    monkeypatch.setattr(
        tray_routes.chat_repo,
        "get_room",
        AsyncMock(return_value={"id": 42, "status": "open", "matrix_room_id": "!room:hs"}),
    )
    monkeypatch.setattr(tray_routes.chat_media, "MAX_UPLOAD_BYTES", 4)
    with pytest.raises(HTTPException) as exc:
        await tray_routes.popup_chat_send_attachment(_Request(), 42, _upload(b"12345", "a.bin", "application/zip"))
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_send_attachment_rejects_closed_rooms(monkeypatch):
    _session(monkeypatch)
    monkeypatch.setattr(
        tray_routes.chat_repo,
        "get_room",
        AsyncMock(return_value={"id": 42, "status": "closed", "matrix_room_id": "!room:hs"}),
    )
    with pytest.raises(HTTPException) as exc:
        await tray_routes.popup_chat_send_attachment(_Request(), 42, _upload(b"x", "a.txt", "text/plain"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_get_media_serves_images_inline(monkeypatch):
    _session(monkeypatch)
    message = _message("m.image", {"url": "mxc://hs/abc", "filename": "IMG 1.jpeg", "mimetype": "image/jpeg"})
    get_message = AsyncMock(return_value=message)
    monkeypatch.setattr(tray_routes.chat_repo, "get_room_message", get_message)
    download = AsyncMock(return_value=(b"jpeg-bytes", "image/jpeg"))
    monkeypatch.setattr(tray_routes.matrix_service, "download_media", download)

    response = await tray_routes.popup_chat_get_media(_Request(), 42, 9, download=False)

    get_message.assert_awaited_once_with(42, 9)
    assert download.await_args.args[0] == "mxc://hs/abc"
    assert response.body == b"jpeg-bytes"
    assert response.media_type == "image/jpeg"
    assert response.headers["content-disposition"].startswith('inline; filename="IMG 1.jpeg"')
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_get_media_forces_download_for_unsafe_types(monkeypatch):
    _session(monkeypatch)
    message = _message("m.file", {"url": "mxc://hs/abc", "filename": "page.html", "mimetype": "text/html"})
    monkeypatch.setattr(tray_routes.chat_repo, "get_room_message", AsyncMock(return_value=message))
    monkeypatch.setattr(tray_routes.matrix_service, "download_media", AsyncMock(return_value=(b"<script>", "text/html")))

    response = await tray_routes.popup_chat_get_media(_Request(), 42, 9, download=False)

    assert response.media_type == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_get_media_is_scoped_to_the_session_room(monkeypatch):
    _session(monkeypatch, room_id=99)
    with pytest.raises(HTTPException) as exc:
        await tray_routes.popup_chat_get_media(_Request(), 42, 9, download=False)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_media_404_for_messages_without_attachments(monkeypatch):
    _session(monkeypatch)
    monkeypatch.setattr(tray_routes.chat_repo, "get_room_message", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await tray_routes.popup_chat_get_media(_Request(), 42, 9, download=False)
    assert exc.value.status_code == 404


def test_popup_template_renders_attachments_and_attach_button():
    from app.features.chat import routes as chat_routes

    html = chat_routes._render_popup(
        room={"id": 42, "status": "open", "subject": "test"},
        messages=[
            _message(
                "m.image",
                {"url": "mxc://hs/abc", "filename": "IMG_3250.jpeg", "mimetype": "image/jpeg"},
                sender_matrix_id="@tech:hs",
                sent_at=datetime(2026, 11, 5, 7, 10, 18),
            )
        ],
        csrf_token="csrf-token",
        room_id=42,
        device_id=7,
        hostname="PC-1",
    )
    assert 'id="chat-attach"' in html
    assert 'id="chat-file-input"' in html
    assert "/api/tray/popup-chat/42/messages/9/media?download=1" in html
    assert "mxc://" not in html
    assert '"kind": "image"' in html
