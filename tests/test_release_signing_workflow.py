"""The Sign release workflow signs what the agents verify (security review F3)."""

from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/sign-release.yml")
PUBLIC_KEY = Path("tray/internal/release/release-public-key.pem")


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def test_only_the_approved_job_can_use_the_signing_key():
    jobs = _workflow()["jobs"]
    users = [name for name, job in jobs.items() if "RELEASE_SIGNING_KEY" in str(job)]
    assert users == ["sign"]
    assert jobs["sign"]["environment"] == "release-signing"
    assert "environment" not in jobs["wait"]
    # The job holding the key runs no third-party actions.
    assert all("uses" not in step for step in jobs["sign"]["steps"])


def test_signs_every_asset_the_agents_install():
    assets = _workflow()["env"]["ASSETS"].split()
    assert {"myportal-tray.msi", "myportal-tray.pkg", "myportal-tray-linux.tar.gz"} <= set(assets)
    for os_name in ("windows", "darwin", "linux"):
        for arch in ("amd64", "arm64"):
            suffix = ".exe" if os_name == "windows" else ""
            assert f"myportal-rmm-{os_name}-{arch}{suffix}" in assets


def test_manifest_matches_the_go_verifier():
    text = WORKFLOW.read_text()
    assert "printf 'myportal-release-v1\\nasset=%s\\nversion=%s\\nsha256=%s\\n'" in text
    go = Path("tray/internal/release/release.go").read_text()
    assert '"myportal-release-v1\\n"' in go
    assert '"asset=" + assetName' in go


def test_refuses_a_key_that_does_not_match_the_built_in_one():
    text = WORKFLOW.read_text()
    assert "openssl pkey -in \"$key\" -pubout" in text
    assert str(PUBLIC_KEY) in text
    assert PUBLIC_KEY.read_text().startswith("-----BEGIN PUBLIC KEY-----")


def test_signs_the_release_source_for_the_build_server():
    text = WORKFLOW.read_text()
    assert "printf 'myportal-source-v1\\ntag=%s\\ncommit=%s\\n'" in text
    assert "myportal-source.sig" in text
    go = Path("tray/build-agent/verify-release/main.go").read_text()
    assert '"myportal-source-v1\\ntag=" + tag + "\\ncommit=" + commit + "\\n"' in go


def test_build_agent_checks_the_release_before_building():
    agent = Path("tray/build-agent/MyPortalBuildAgent.ps1").read_text()
    clone = agent.index('Invoke-Native "git clone of $Tag"')
    check = agent.index("Assert-SignedRelease $Tag $src $dir")
    build = agent.index("go build -ldflags $ldflags")
    assert clone < check < build
    installer = Path("tray/build-agent/Install-MyPortalBuildAgent.ps1").read_text()
    assert "go build -o $verifier ./build-agent/verify-release" in installer
    assert "release-public-key.pem" in installer
    assert "VerifierPath" in installer and "ReleaseKeyPath" in installer


def test_build_agent_installer_fetches_source_when_run_on_its_own():
    installer = Path("tray/build-agent/Install-MyPortalBuildAgent.ps1").read_text()
    fetch = installer.index("git clone --quiet --depth 1 --branch $SourceRef")
    build = installer.index("go build -o $verifier ./build-agent/verify-release")
    assert fetch < build
    assert "Push-Location $trayRoot" in installer
    assert "Join-Path $PSScriptRoot '..'" not in installer
    assert "Remove-Item -LiteralPath $sourceClone" in installer
