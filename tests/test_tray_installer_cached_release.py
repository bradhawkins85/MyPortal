import json

from app.services import tray_installer


def test_get_cached_latest_release_info_reads_loaded_github_release(monkeypatch, tmp_path):
    monkeypatch.setattr(tray_installer, "_TRAY_STATIC_DIR", tmp_path)

    (tmp_path / "myportal-tray.msi").write_bytes(b"msi")
    (tmp_path / "myportal-tray.msi.json").write_text(
        json.dumps(
            {
                "release_id": 42,
                "release_tag": "v1.2.3",
                "asset_id": 101,
                "asset_name": "myportal-tray.msi",
                "asset_updated_at": "2026-06-23T01:02:03Z",
                "asset_size": 3,
                "download_url": "https://github.example/myportal-tray.msi",
            }
        ),
        encoding="utf-8",
    )

    info = tray_installer.get_cached_latest_release_info()

    assert info["version"] == "1.2.3"
    assert info["release_tag"] == "v1.2.3"
    assert info["release_id"] == 42
    assert info["loaded_assets"] == [
        {
            "name": "myportal-tray.msi",
            "size": 3,
            "updated_at": "2026-06-23T01:02:03Z",
            "download_url": "https://github.example/myportal-tray.msi",
        }
    ]


def test_get_cached_latest_release_info_handles_no_loaded_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(tray_installer, "_TRAY_STATIC_DIR", tmp_path)

    info = tray_installer.get_cached_latest_release_info()

    assert info == {
        "version": None,
        "release_tag": None,
        "loaded_assets": [],
    }


def _cache_installer(directory, name, tag="v0.8.5", sha256="ab" * 32):
    (directory / name).write_bytes(b"installer")
    (directory / f"{name}.json").write_text(
        json.dumps(
            {
                "release_tag": tag,
                "asset_name": name,
                "asset_updated_at": "2026-10-01T00:00:00Z",
                "asset_size": 9,
                "download_url": f"https://github.example/{name}",
                "sha256": sha256,
            }
        ),
        encoding="utf-8",
    )


def test_cached_server_installers_lists_self_update_installers(monkeypatch, tmp_path):
    monkeypatch.setattr(tray_installer, "_TRAY_STATIC_DIR", tmp_path)
    _cache_installer(tmp_path, "myportal-tray.msi")
    _cache_installer(tmp_path, "myportal-tray.pkg", sha256="cd" * 32)
    _cache_installer(tmp_path, "myportal-tray.dmg")

    installers = tray_installer.cached_server_installers("https://portal.example.com/")

    assert installers == [
        {
            "name": "myportal-tray.msi",
            "platform": "windows",
            "version": "0.8.5",
            "download_url": "https://portal.example.com/static/tray/myportal-tray.msi",
            "sha256": "ab" * 32,
        },
        {
            "name": "myportal-tray.pkg",
            "platform": "darwin",
            "version": "0.8.5",
            "download_url": "https://portal.example.com/static/tray/myportal-tray.pkg",
            "sha256": "cd" * 32,
        },
    ]


def test_cached_installer_sha256_matches_github_and_server_urls(monkeypatch, tmp_path):
    monkeypatch.setattr(tray_installer, "_TRAY_STATIC_DIR", tmp_path)
    _cache_installer(tmp_path, "myportal-tray.msi")

    assert tray_installer.cached_installer_sha256("https://github.example/myportal-tray.msi") == "ab" * 32
    assert (
        tray_installer.cached_installer_sha256("https://portal.example.com/static/tray/myportal-tray.msi")
        == "ab" * 32
    )
    assert tray_installer.cached_installer_sha256("https://other.example/myportal-tray.msi") is None
    assert tray_installer.cached_installer_sha256("") is None
