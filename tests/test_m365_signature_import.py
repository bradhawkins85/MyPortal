"""Tests for the Outlook signature ZIP import service."""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import pytest

from app.services.m365_signature_import import (
    ParsedSignature,
    _locate_entries,
    _read_entry,
    _convert_outlook_html,
    _resolve_image_reference,
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
# _resolve_image_reference
# ---------------------------------------------------------------------------

_IMAGES = {"image001.png": {"name": "image001.png", "data_uri": "data:image/png;base64,AAA="}}


@pytest.mark.parametrize(
    "value",
    [
        "files/image001.png",
        "files\\image001.png",
        "files/%3Eimage001.png",
        "My%20Sig_files/image001.png",
        "cid:image001.png@01DA1234.ABCD5678",
    ],
)
def test_resolve_image_reference_matches_outlook_paths(value):
    assert _resolve_image_reference(value, _IMAGES) == "data:image/png;base64,AAA="


@pytest.mark.parametrize(
    "value", ["/external/image001.png", "https://example.com/image001.png", "files/other.png"]
)
def test_resolve_image_reference_ignores_unrelated(value):
    assert _resolve_image_reference(value, _IMAGES) is None


# ---------------------------------------------------------------------------
# _convert_outlook_html
# ---------------------------------------------------------------------------


def test_convert_inlines_outlook_stylesheet_and_drops_office_markup():
    html = (
        "<html><head><title>Sig</title><style><!--\n"
        "@font-face {font-family:Calibri;}\n"
        "p.MsoNormal, li.MsoNormal\n\t{margin:0cm;\n\tfont-size:11.0pt;\n\tmso-fareast-language:EN-US;}\n"
        "a:link {color:blue;}\n"
        "--></style></head>\n<body><div class=WordSection1>\n"
        "<p class=MsoNormal><a name=\"_MailAutoSig\"><span\nstyle='color:#333'>Regards,<o:p></o:p></span></a></p>\n"
        "</div></body></html>"
    )
    result = _convert_outlook_html(html, {})
    assert "Sig" not in result.replace("Regards", "")
    assert '<p style="margin:0cm;font-size:11.0pt">' in result
    assert '<span style="color:#333">Regards,</span>' in result
    assert "<a" not in result
    assert "o:p" not in result
    assert "mso-" not in result


def test_convert_handles_implicitly_closed_head():
    html = "<html><head><title>Sig</title><meta charset=utf-8><body><p>Regards,</p></body></html>"
    result = _sanitize_signature_html(_convert_outlook_html(html, {}))
    assert result == "<p>Regards,</p>"


def test_convert_inline_style_overrides_stylesheet():
    html = "<style>p {margin:0; color:red}</style><p style='color:blue'>x</p>"
    assert '<p style="margin:0;color:blue">x</p>' in _convert_outlook_html(html, {})


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


def test_sanitize_preserves_data_uri_images():
    html = '<img src="data:image/png;base64,iVBORw0KGgo=" alt="Logo" width="100" />'
    result = _sanitize_signature_html(html)
    assert 'src="data:image/png;base64,iVBORw0KGgo="' in result


def test_sanitize_drops_svg_data_uri():
    html = '<img src="data:image/svg+xml;base64,PHN2Zz4=" />'
    assert "data:" not in _sanitize_signature_html(html)


def test_sanitize_collapses_whitespace_between_blocks():
    html = "<p>\n  Regards,\n</p>\n\n<p>Jane\n   Smith</p>"
    assert _sanitize_signature_html(html) == "<p>Regards,</p><p>Jane Smith</p>"


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
        zip_bytes, filename="Test Sig.zip")
    assert result.source_name == "Test Sig"
    assert "Name" in result.html_content
    assert "Name" in result.text_content
    assert len(result.files) == 1
    assert result.files[0]["name"] == "avatar.png"

    # The image is embedded, nothing is written to disk
    expected = base64.b64encode(b"\x89PNG\r\n\x1a\n fake data").decode()
    assert f'src="data:image/png;base64,{expected}"' in result.html_content
    assert "files/avatar.png" not in result.html_content
    assert not list(tmp_path.iterdir())


@pytest.mark.anyio
async def test_import_no_images(tmp_path):
    zip_bytes = _make_outlook_zip(
        "Plain",
        html="<html><body><p>Just text</p></body></html>",
        txt="Just text",
        images=None,
    )
    result = await import_outlook_signature(
        zip_bytes, filename="Plain.zip")
    assert result.has_images is False
    assert "Just text" in result.html_content


@pytest.mark.anyio
async def test_import_rejects_non_zip(tmp_path):
    with pytest.raises(ValueError, match="not a valid ZIP"):
        await import_outlook_signature(
            b"this is not a zip file",
            filename="bad.zip",
        )


@pytest.mark.anyio
async def test_import_rejects_oversized(tmp_path):
    big_bytes = b"\x00" * (11 * 1024 * 1024)
    with pytest.raises(ValueError, match="exceeds the 10 MB"):
        await import_outlook_signature(
            big_bytes, filename="big.zip")


@pytest.mark.anyio
async def test_import_zip_without_html(tmp_path):
    zip_bytes = _make_zip({"only.txt": b"hello", "only.rtf": b"{\\rtf1}"})
    with pytest.raises(ValueError, match="HTML file"):
        await import_outlook_signature(
            zip_bytes, filename="nograph.zip")


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
        zip_bytes, filename="Jane.zip")
    assert 'role="presentation"' in result.html_content
    assert "style=" in result.html_content
    assert "<strong>Jane Smith</strong>" in result.html_content
    assert 'src="data:image/jpeg;base64,' in result.html_content


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
    result = await import_outlook_signature(zip_bytes, filename="export.zip")
    assert result.source_name == "Jane Smith"
    assert result.text_content == "Jane Smith"
    assert sorted(f["name"] for f in result.files) == ["image002.png", "image003.jpg"]
    png = base64.b64encode(b"\x89PNG").decode()
    jpg = base64.b64encode(b"\xff\xd8").decode()
    assert f'src="data:image/png;base64,{png}"' in result.html_content
    assert f'src="data:image/jpeg;base64,{jpg}"' in result.html_content
    assert '<p style="margin:0">' in result.html_content
    assert "_files/" not in result.html_content
    assert "MsoNormal" not in result.html_content


@pytest.mark.anyio
async def test_import_converts_bmp_to_png():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="BMP")
    zip_bytes = _make_zip(
        {
            "Sig.htm": b'<p><img src="Sig_files/logo.bmp"></p>',
            "Sig_files/logo.bmp": buffer.getvalue(),
        }
    )
    result = await import_outlook_signature(zip_bytes, filename="Sig.zip")
    assert 'src="data:image/png;base64,' in result.html_content
    assert result.files[0]["content_type"] == "image/png"


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
