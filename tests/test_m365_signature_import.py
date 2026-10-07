"""Tests for the Outlook signature ZIP import service."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.services.m365_signature_import import (
    ParsedSignature,
    _locate_entries,
    _read_entry,
    _rewrite_image_sources,
    _sanitize_signature_html,
    _validate_zip_contents,
    import_outlook_signature,
)


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()


def _make_outlook_zip(
    name: str = "My Signature",
    *,
    html: str = '<html><body><p>Hello</p><img src="files/logo.png" /></body></html>',
    txt: str = "Hello\nSignature",
    images: list[tuple[str, bytes]] | None = None,
) -> bytes:
    files: dict[str, bytes] = {}
    files[f"{name}.html"] = html.encode("utf-8")
    files[f"{name}.txt"] = txt.encode("utf-8")
    files[f"{name}.rtf"] = b"{\\rtf1\\ansi hello}"
    if images:
        for img_name, img_data in images:
            files[f"files/{img_name}"] = img_data
    return _make_zip(files)


# ---------------------------------------------------------------------------
# _validate_zip_contents
# ---------------------------------------------------------------------------


def test_validate_rejects_empty_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        pass
    zf_obj = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    with pytest.raises(ValueError, match="empty"):
        _validate_zip_contents(zf_obj)


def test_validate_allows_valid_files():
    zip_bytes = _make_outlook_zip(images=[("logo.png", b"\x89PNG fake")])
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    entries = _validate_zip_contents(zf)
    assert len(entries) >= 4  # html, txt, rtf, + image


# ---------------------------------------------------------------------------
# _locate_entries
# ---------------------------------------------------------------------------


def test_locate_entries_finds_html_txt_and_files():
    zip_bytes = _make_outlook_zip(images=[("logo.png", b"\x89PNG")])
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    entries = zf.infolist()
    html, txt, files_dir = _locate_entries(entries)
    assert html.endswith(".html")
    assert txt and txt.endswith(".txt")
    assert files_dir == "files"


def test_locate_entries_no_html_raises():
    zip_bytes = _make_zip({"note.txt": b"hello"})
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    entries = zf.infolist()
    with pytest.raises(ValueError, match="HTML file"):
        _locate_entries(entries)


def test_locate_entries_no_files_dir():
    zip_bytes = _make_zip({"sig.html": b"<p>hi</p>", "sig.txt": b"hi"})
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    entries = zf.infolist()
    html, txt, files_dir = _locate_entries(entries)
    assert html == "sig.html"
    assert txt == "sig.txt"


# ---------------------------------------------------------------------------
# _read_entry
# ---------------------------------------------------------------------------


def test_read_entry_utf8():
    zip_bytes = _make_zip({"test.html": "<p>hello</p>".encode("utf-8")})
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    result = _read_entry(zf, "test.html")
    assert result == "<p>hello</p>"


def test_read_entry_windows_1252():
    content = "Caf\u00e9 Signature".encode("windows-1252")
    zip_bytes = _make_zip({"test.html": content})
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    result = _read_entry(zf, "test.html")
    assert "Caf" in result


# ---------------------------------------------------------------------------
# _rewrite_image_sources
# ---------------------------------------------------------------------------


def test_rewrite_image_sources_forward_slash():
    html = '<img src="files/image001.png" alt="Logo" />'
    result = _rewrite_image_sources(html, "abc123")
    assert "/uploads/m365-signatures/abc123/files/image001.png" in result


def test_rewrite_image_sources_backslash():
    html = '<img src="files\\image001.png" alt="Logo" />'
    result = _rewrite_image_sources(html, "abc123")
    assert "/uploads/m365-signatures/abc123/files/image001.png" in result


def test_rewrite_image_sources_url_encoded():
    html = '<img src="files/%3Eimage001.png" alt="Logo" />'
    result = _rewrite_image_sources(html, "abc123")
    assert "/uploads/m365-signatures/abc123/files/image001.png" in result


def test_rewrite_preserves_unrelated_src():
    html = '<img src="/external/photo.jpg" /><img src="files/logo.png" />'
    result = _rewrite_image_sources(html, "abc123")
    assert "/external/photo.jpg" in result
    assert "/uploads/m365-signatures/abc123/files/logo.png" in result


# ---------------------------------------------------------------------------
# _sanitize_signature_html
# ---------------------------------------------------------------------------


def test_sanitize_preserves_inline_styles():
    html = '<table style="width:100%; border:none;"><tr><td style="padding:8px;">Hi</td></tr></table>'
    result = _sanitize_signature_html(html)
    assert "style=" in result
    assert "padding:8px" in result


def test_sanitize_strips_scripts():
    html = '<p onclick="alert(1)">Hi</p><script>evil()</script>'
    result = _sanitize_signature_html(html)
    assert "alert" not in result
    assert "evil" not in result
    assert "<script" not in result


def test_sanitize_strips_dangerous_protocols():
    html = '<a href="javascript:alert(1)">Link</a>'
    result = _sanitize_signature_html(html)
    assert "javascript:" not in result


def test_sanitize_preserves_img_tags():
    html = '<img src="/uploads/m365-signatures/abc/files/logo.png" alt="Logo" width="100" />'
    result = _sanitize_signature_html(html)
    assert "<img" in result
    assert "src=" in result


# ---------------------------------------------------------------------------
# import_outlook_signature (integration)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_import_simple_signature(tmp_path):
    zip_bytes = _make_outlook_zip(
        "Test Sig",
        html='<html><body><p>Name</p><img src="files/avatar.png" /></body></html>',
        txt="Name\nSignature",
        images=[("avatar.png", b"\x89PNG\r\n\x1a\n fake data")],
    )
    result = await import_outlook_signature(
        zip_bytes, filename="Test Sig.zip", uploads_root=tmp_path
    )
    assert result.source_name == "Test Sig"
    assert "Name" in result.html_content
    assert "Name" in result.text_content
    assert len(result.files) == 1
    assert result.files[0]["name"] == "avatar.png"

    # Verify the image was written to disk
    expected_dir = tmp_path / "m365-signatures"
    subdirs = list(expected_dir.iterdir())
    assert len(subdirs) == 1
    img_file = subdirs[0] / "files" / "avatar.png"
    assert img_file.exists()

    # Verify the HTML src was rewritten
    assert "/uploads/m365-signatures/" in result.html_content
    assert "files/avatar.png" in result.html_content


@pytest.mark.anyio
async def test_import_no_images(tmp_path):
    zip_bytes = _make_outlook_zip(
        "Plain",
        html="<html><body><p>Just text</p></body></html>",
        txt="Just text",
        images=None,
    )
    result = await import_outlook_signature(
        zip_bytes, filename="Plain.zip", uploads_root=tmp_path
    )
    assert result.has_images is False
    assert "Just text" in result.html_content


@pytest.mark.anyio
async def test_import_rejects_non_zip(tmp_path):
    with pytest.raises(ValueError, match="not a valid ZIP"):
        await import_outlook_signature(
            b"this is not a zip file",
            filename="bad.zip",
            uploads_root=tmp_path,
        )


@pytest.mark.anyio
async def test_import_rejects_oversized(tmp_path):
    big_bytes = b"\x00" * (11 * 1024 * 1024)
    with pytest.raises(ValueError, match="exceeds the 10 MB"):
        await import_outlook_signature(
            big_bytes, filename="big.zip", uploads_root=tmp_path
        )


@pytest.mark.anyio
async def test_import_zip_without_html(tmp_path):
    zip_bytes = _make_zip({"only.txt": b"hello", "only.rtf": b"{\\rtf1}"})
    with pytest.raises(ValueError, match="HTML file"):
        await import_outlook_signature(
            zip_bytes, filename="nograph.zip", uploads_root=tmp_path
        )


@pytest.mark.anyio
async def test_import_preserves_outlook_table_structure(tmp_path):
    """Outlook signatures use complex table layouts with inline styles."""
    html = (
        '<table role="presentation" style="width:100%;max-width:500px;" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:12px;background:#f5f5f5;">'
        '<table cellpadding="0" cellspacing="0"><tr>'
        '<td style="width:40px;"><img src="files/photo.jpg" width="40" height="40" /></td>'
        '<td style="padding-left:12px;vertical-align:top;">'
        '<p style="margin:0;font-family:Arial;font-size:14px;"><strong>Jane Smith</strong></p>'
        '<p style="margin:2px 0;font-family:Arial;font-size:12px;color:#666;">Senior Engineer</p>'
        '<p style="margin:2px 0;font-family:Arial;font-size:12px;">T: 1300 000 000</p>'
        "</td></tr></table></td></tr></table>"
    )
    zip_bytes = _make_outlook_zip(
        "Jane", html=html, txt="Jane Smith\nSenior Engineer",
        images=[("photo.jpg", b"\xff\xd8JFIF")],
    )
    result = await import_outlook_signature(
        zip_bytes, filename="Jane.zip", uploads_root=tmp_path
    )
    assert 'role="presentation"' in result.html_content
    assert "style=" in result.html_content
    assert "<strong>Jane Smith</strong>" in result.html_content
    assert "/uploads/m365-signatures/" in result.html_content


@pytest.mark.anyio
async def test_import_classic_outlook_export_with_named_files_folder(tmp_path):
    """Classic Outlook exports use ``<name>_files`` and URL-encoded paths."""
    html = (
        "<html><head><style>p.MsoNormal{margin:0}</style></head><body>"
        "<!--[if gte vml 1]><v:imagedata src=\"Jane%20Smith_files/image001.png\"/><![endif]-->"
        '<p class="MsoNormal"><img width="120" src="Jane%20Smith_files/image002.png" '
        'alt="Logo"></p>'
        '<p><img src="cid:image003.jpg@01DA1234.ABCD5678"></p>'
        "</body></html>"
    )
    zip_bytes = _make_zip(
        {
            "Jane Smith/Jane Smith.htm": html.encode("windows-1252"),
            "Jane Smith/Jane Smith.txt": "Jane Smith".encode("utf-16"),
            "Jane Smith/Jane Smith.rtf": b"{\\rtf1}",
            "Jane Smith/Jane Smith_files/image002.png": b"\x89PNG",
            "Jane Smith/Jane Smith_files/image003.jpg": b"\xff\xd8",
            "Jane Smith/Jane Smith_files/filelist.xml": b"<xml/>",
            "__MACOSX/Jane Smith/._Jane Smith.htm": b"junk",
        }
    )
    result = await import_outlook_signature(
        zip_bytes, filename="export.zip", uploads_root=tmp_path
    )
    assert result.source_name == "Jane Smith"
    assert result.text_content == "Jane Smith"
    assert sorted(f["name"] for f in result.files) == ["image002.png", "image003.jpg"]
    base = f"/uploads/m365-signatures/{result.files[0]['stored_path'].split('/')[1]}/files/"
    assert f'src="{base}image002.png"' in result.html_content
    assert f'src="{base}image003.jpg"' in result.html_content
    assert "_files/" not in result.html_content
    assert "MsoNormal{" not in result.html_content


def test_locate_entries_prefers_named_files_folder():
    zip_bytes = _make_zip(
        {
            "Sig.htm": b"<p>hi</p>",
            "Sig_files/image001.png": b"\x89PNG",
            "Sig_files/filelist.xml": b"<xml/>",
        }
    )
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    html, txt, files_dir = _locate_entries(zf.infolist())
    assert html == "Sig.htm"
    assert txt is None
    assert files_dir == "Sig_files"


def test_import_route_accepts_multipart_upload_without_form_field(monkeypatch):
    """The import endpoint must not require a JSON/form field named ``form``."""
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    from fastapi.testclient import TestClient

    from app.features.m365_admin import routes

    async def fake_context(request, *, write=False):
        form = await request.form()
        return None, None, PlainTextResponse(f"reached:{form.get('file').filename}")

    monkeypatch.setattr(routes, "_signature_context", fake_context)
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post(
        "/m365/signatures/import",
        files={"file": ("sig.zip", b"PK", "application/zip")},
        data={"name": "", "slug": "", "description": ""},
    )
    assert response.status_code == 200
    assert response.text == "reached:sig.zip"
