"""Legacy ``static/uploads/tickets`` attachments move to private storage and are never served."""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from app import main as app_main
from app.services import ticket_attachments


def _stores(tmp_path: Path) -> tuple[Path, Path]:
    legacy = tmp_path / "static" / "uploads" / "tickets"
    private = tmp_path / "private_uploads" / "tickets"
    legacy.mkdir(parents=True)
    return legacy, private


def test_migration_moves_files_and_is_idempotent(tmp_path):
    legacy, private = _stores(tmp_path)
    (legacy / "aaa.pdf").write_bytes(b"one")
    (legacy / "bbb.png").write_bytes(b"two")

    first = ticket_attachments.migrate_legacy_attachment_files(legacy_dir=legacy, target_dir=private)
    assert first["moved"] == 2
    assert not any(legacy.iterdir())
    assert (private / "aaa.pdf").read_bytes() == b"one"
    assert (private / "bbb.png").stat().st_mode & 0o077 == 0

    second = ticket_attachments.migrate_legacy_attachment_files(legacy_dir=legacy, target_dir=private)
    assert second == {"moved": 0, "duplicates_removed": 0, "conflicts": 0, "skipped": 0}


def test_migration_handles_duplicates_conflicts_and_symlinks(tmp_path):
    legacy, private = _stores(tmp_path)
    private.mkdir(parents=True)
    (legacy / "same.pdf").write_bytes(b"same")
    (private / "same.pdf").write_bytes(b"same")
    (legacy / "clash.pdf").write_bytes(b"legacy")
    (private / "clash.pdf").write_bytes(b"private")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    (legacy / "link.txt").symlink_to(outside)

    counts = ticket_attachments.migrate_legacy_attachment_files(legacy_dir=legacy, target_dir=private)

    assert counts == {"moved": 0, "duplicates_removed": 1, "conflicts": 1, "skipped": 1}
    assert not (legacy / "same.pdf").exists()
    assert (legacy / "clash.pdf").read_bytes() == b"legacy"
    assert (private / "clash.pdf").read_bytes() == b"private"
    assert not (private / "link.txt").exists()


def test_dry_run_changes_nothing(tmp_path):
    legacy, private = _stores(tmp_path)
    (legacy / "aaa.pdf").write_bytes(b"one")
    counts = ticket_attachments.migrate_legacy_attachment_files(
        legacy_dir=legacy, target_dir=private, dry_run=True
    )
    assert counts["moved"] == 1
    assert (legacy / "aaa.pdf").exists()
    assert not private.exists()


def test_missing_legacy_folder_is_a_noop(tmp_path):
    counts = ticket_attachments.migrate_legacy_attachment_files(
        legacy_dir=tmp_path / "missing", target_dir=tmp_path / "private"
    )
    assert counts["moved"] == 0


def test_static_uploads_never_serves_legacy_ticket_attachments(tmp_path):
    (tmp_path / "tickets").mkdir()
    (tmp_path / "tickets" / "secret.pdf").write_bytes(b"%PDF")
    (tmp_path / "ports").mkdir()
    (tmp_path / "ports" / "doc.pdf").write_bytes(b"%PDF")
    static_app = Starlette(
        routes=[Mount("/static/uploads", app_main._DownloadOnlyStaticFiles(directory=str(tmp_path)))]
    )
    client = TestClient(static_app)
    for path in (
        "/static/uploads/tickets/secret.pdf",
        "/static/uploads/Tickets/secret.pdf",
        "/static/uploads/ports/../tickets/secret.pdf",
        "/static/uploads/./tickets/secret.pdf",
    ):
        assert client.get(path).status_code == 404, path
    assert client.get("/static/uploads/ports/doc.pdf").status_code == 200
