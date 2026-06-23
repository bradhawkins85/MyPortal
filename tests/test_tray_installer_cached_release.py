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
