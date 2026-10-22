from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest

from app.services import gitea, rmm_script_files


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("destination", ["C:\\Tools\\setup.exe", "/opt/tools/setup.sh"])
def test_absolute_file_destinations(destination):
    assert rmm_script_files.validate_associations([{"source_path": "Common/setup.exe", "destination": destination}])[0]["destination"] == destination


@pytest.mark.parametrize("destination", ["setup.exe", "C:setup.exe", "/opt/../setup.exe", "C:\\Tools\\..\\setup.exe", "/", "/opt/tools/", "C:\\", "C:\\Tools\\setup.exe:stream", "C:\\Tools\\CON", "\\\\host\\share\\setup.exe", "/opt/\nsetup"])
def test_invalid_destinations_are_rejected(destination):
    with pytest.raises(rmm_script_files.ScriptFileError):
        rmm_script_files.validate_associations([{"source_path": "Common/setup.exe", "destination": destination}])


@pytest.mark.parametrize("source", ["/Common/x.exe", "Common/../x.exe", "Common//x.exe", "Common\\x.exe", "Common/\nx.exe"])
def test_invalid_repository_paths_are_rejected(source):
    with pytest.raises(rmm_script_files.ScriptFileError):
        rmm_script_files.validate_associations([{"source_path": source, "destination": "/tmp/x.exe"}])


def test_duplicate_windows_destinations_are_case_insensitive():
    with pytest.raises(rmm_script_files.ScriptFileError, match="different destination"):
        rmm_script_files.validate_associations([
            {"source_path": "Common/a", "destination": "C:\\Tools\\setup.exe"},
            {"source_path": "Common/b", "destination": "c:/tools/SETUP.exe"},
        ])


@pytest.mark.anyio
async def test_file_catalog_respects_common_and_company_script_scope(monkeypatch):
    settings = gitea.private_files_settings(
        gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "rmm", True)
    )
    paths = ["Common/tool.exe", "Companies/Renamed/.myportal-company",
             "Companies/Renamed/setup.vbs", "Companies/Other/setup.exe",
             "Files/Common/tool.exe", "README.md"]

    async def list_files(_settings):
        return [gitea.GiteaFile(path, "a" * 40, 1) for path in paths]

    async def companies(**kwargs):
        return [{"id": 1, "name": "Contoso"}, {"id": 2, "name": "Other"}]

    async def fetch_file(_settings, _path, **kwargs):
        return b'{"company_id": 1}'

    monkeypatch.setattr(gitea, "list_files", list_files)
    monkeypatch.setattr(gitea, "fetch_file", fetch_file)
    monkeypatch.setattr(rmm_script_files.companies_repo, "list_companies", companies)
    common = await rmm_script_files.available_files(settings, {"company_id": None}, set())
    assert set(common) == {"Common/tool.exe"}
    # A Common script reaches the run's company files through the {company} folder.
    for company_id, sha in ((1, "Companies/Renamed/setup.vbs"), (2, "Companies/Other/setup.exe")):
        for_company = await rmm_script_files.available_files(settings, {"company_id": None}, {company_id})
        assert {path: item.path for path, item in for_company.items()} == {
            "Common/tool.exe": "Common/tool.exe",
            "Companies/{company}/" + sha.rsplit("/", 1)[-1]: sha,
        }
    any_company = await rmm_script_files.available_files(settings, {"company_id": None})
    assert set(any_company) == {"Common/tool.exe", "Companies/{company}/setup.vbs",
                                "Companies/{company}/setup.exe"}
    company = await rmm_script_files.available_files(settings, {"company_id": 1})
    assert set(company) == {"Common/tool.exe", "Companies/Renamed/setup.vbs"}
    other = await rmm_script_files.available_files(settings, {"company_id": 2})
    assert set(other) == {"Common/tool.exe", "Companies/Other/setup.exe"}


@pytest.mark.anyio
async def test_file_choices_create_missing_files_folders_first(monkeypatch):
    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "", True)
    created = []

    async def load_settings():
        return settings

    async def list_files(_settings):
        # Script folders in the scripts repository do not stand in for Private-Files folders.
        if _settings.repo == "scripts":
            return [gitea.GiteaFile("Common/README.md", "a" * 40, 1), gitea.GiteaFile("Companies/Contoso/.myportal-company", "b" * 40, 1)]
        return []

    async def companies(**kwargs):
        return [{"id": 1, "name": "Contoso"}]

    async def create_files(_settings, files, _message):
        assert _settings.repo == "Private-Files"
        created.append(sorted(files))

    monkeypatch.setattr(gitea, "load_settings", load_settings)
    monkeypatch.setattr(gitea, "list_files", list_files)
    monkeypatch.setattr(gitea, "create_files", create_files)
    monkeypatch.setattr(rmm_script_files.companies_repo, "list_companies", companies)
    choices, warning = await rmm_script_files.file_choices({"company_id": 1}, 1)
    assert choices == [] and warning is None
    assert created == [["Common/README.md", "Companies/Contoso/.myportal-company", "Companies/README.md"]]

    async def refuse(_settings, _files, _message):
        raise gitea.GiteaError("Gitea refused to create folders. GITEA_API_TOKEN needs write access to the repository.")

    monkeypatch.setattr(gitea, "create_files", refuse)
    choices, warning = await rmm_script_files.file_choices({"company_id": 1}, 1)
    assert choices == [] and "GITEA_API_TOKEN needs write access" in warning


@pytest.mark.anyio
async def test_existing_files_are_never_recreated(monkeypatch):
    from app.services import rmm_script_layout

    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "", True)
    # A marker naming a deleted company, in the folder a current company would use.
    files = [gitea.GiteaFile("Companies/Contoso/.myportal-company", "a" * 40, 1)]

    async def fetch_file(_settings, _path, **kwargs):
        return b'{"company_id": 99}'

    monkeypatch.setattr(gitea, "fetch_file", fetch_file)
    layout = await rmm_script_layout._read_layout(settings, files, [{"id": 1, "name": "Contoso"}], files_folder=True)
    assert layout.company_folders == {"Contoso": 1}
    assert "Companies/Contoso/.myportal-company" not in layout.missing
    assert set(layout.missing) == {"Common/README.md"}


@pytest.mark.anyio
async def test_queue_rejects_missing_company_files_and_wrong_endpoint_paths(monkeypatch):
    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "", True)

    async def load_settings():
        return settings

    async def catalog(_settings, _script, _company_ids=None):
        return {}

    monkeypatch.setattr(gitea, "load_settings", load_settings)
    monkeypatch.setattr(rmm_script_files, "available_files", catalog)
    script = {"company_id": 1, "files": [{"source_path": "Companies/Contoso/setup.exe", "destination": "C:\\Tools\\setup.exe"}]}
    agent = {"company_id": 1, "os": "windows", "script_files_supported": True}
    with pytest.raises(rmm_script_files.ScriptFileError, match="missing or unavailable"):
        await rmm_script_files.prepare_files(script, agent)
    with pytest.raises(rmm_script_files.ScriptFileError, match="operating system"):
        await rmm_script_files.prepare_files(script, {**agent, "os": "linux"})
    with pytest.raises(rmm_script_files.ScriptFileError, match="unavailable to this company"):
        await rmm_script_files.prepare_files(script, {**agent, "company_id": 2})


@pytest.mark.anyio
async def test_gitea_blob_preserves_binary_content_and_checks_git_hash(monkeypatch):
    raw = b"MZ\x00\xff\x80binary\r\n"
    sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False).hexdigest()
    body = {"encoding": "base64", "content": base64.b64encode(raw).decode(), "size": len(raw)}
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=body)

    monkeypatch.setattr(gitea, "_client", lambda settings: httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=gitea._headers(settings)))
    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "", True)
    assert await gitea.fetch_blob(settings, sha, max_bytes=100) == raw
    assert seen[0].url.path.endswith("/git/blobs/" + sha)
    assert seen[0].headers["Authorization"] == "token token"
    body["content"] = base64.b64encode(b"wrong").decode()
    with pytest.raises(gitea.GiteaError, match="version"):
        await gitea.fetch_blob(settings, sha, max_bytes=100)
    body["content"] = base64.b64encode(raw).decode()
    with pytest.raises(gitea.GiteaError, match="size limit"):
        await gitea.fetch_blob(settings, sha, max_bytes=1)


@pytest.mark.anyio
async def test_common_script_uses_the_run_company_file(monkeypatch):
    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "", True)
    raw = b"installer"
    blob = gitea.GiteaFile("Companies/Contoso/setup.exe", "c" * 40, len(raw))

    async def load_settings():
        return settings

    async def catalog(_settings, _script, company_ids=None):
        return {"Companies/{company}/setup.exe": blob} if company_ids == {1} else {}

    async def fetch_blob(_settings, _sha, **kwargs):
        return raw

    async def resolve_commit(_settings):
        return "d" * 40

    monkeypatch.setattr(gitea, "resolve_commit", resolve_commit)
    monkeypatch.setattr(gitea, "load_settings", load_settings)
    monkeypatch.setattr(gitea, "fetch_blob", fetch_blob)
    monkeypatch.setattr(rmm_script_files, "available_files", catalog)
    script = {"company_id": None, "files": [{"source_path": "Companies/{company}/setup.exe", "destination": "C:\\Tools\\setup.exe"}]}
    agent = {"company_id": 1, "os": "windows", "script_files_supported": True}
    prepared = await rmm_script_files.prepare_files(script, agent)
    assert prepared[0]["source_path"] == "Companies/Contoso/setup.exe"
    assert prepared[0]["repository"] == "msp/Private-Files"
    assert prepared[0]["commit_sha"] == "d" * 40
    assert prepared[0]["sha256"] == hashlib.sha256(raw).hexdigest()
    pending = {"id": None, "company_id": 1, "pending_install": True}
    assert await rmm_script_files.prepare_files(script, pending) == prepared
    with pytest.raises(rmm_script_files.ScriptFileError, match="missing for this company"):
        await rmm_script_files.prepare_files(script, {**agent, "company_id": 2})


@pytest.mark.anyio
@pytest.mark.parametrize("raw", [b"MZ\x00\xff\x80installer\r\n", b""])
async def test_gitea_omitted_blob_content_downloads_raw_at_pinned_commit(monkeypatch, raw):
    sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False).hexdigest()
    commit = "d" * 40
    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "", True)
    seen = []
    response_raw = raw

    def handler(request):
        seen.append(request)
        if "/branches/" in request.url.path:
            return httpx.Response(200, json={"commit": {"id": commit}})
        if "/git/blobs/" in request.url.path:
            return httpx.Response(200, json={"sha": sha, "size": len(raw), "encoding": None, "content": None})
        assert request.url.path == "/api/v1/repos/msp/scripts/raw/Files/Common/setup file.exe"
        assert request.url.params["ref"] == commit
        assert request.headers["Authorization"] == "token token"
        return httpx.Response(200, content=response_raw)

    monkeypatch.setattr(gitea, "_client", lambda settings: httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=gitea._headers(settings)))
    assert await gitea.resolve_commit(settings) == commit
    kwargs = {"max_bytes": 100, "path": "Files/Common/setup file.exe", "ref": commit}
    assert await gitea.fetch_blob(settings, sha, **kwargs) == raw
    response_raw = b"changed on branch"
    with pytest.raises(gitea.GiteaError, match="version"):
        await gitea.fetch_blob(settings, sha, **kwargs)
    response_raw = b"x" * 101
    with pytest.raises(gitea.GiteaError, match="size limit"):
        await gitea.fetch_blob(settings, sha, **kwargs)
    count = len(seen)
    with pytest.raises(gitea.GiteaError, match="Queue a new run"):
        await gitea.fetch_blob(settings, sha, max_bytes=100)
    assert len(seen) == count + 1


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [[], None, {"encoding": "base64", "content": None}, {"encoding": "base64", "content": "!!!"}])
async def test_gitea_malformed_blob_is_rejected_without_raw_fallback(monkeypatch, payload):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=json.dumps(payload))

    monkeypatch.setattr(gitea, "_client", lambda settings: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    settings = gitea.GiteaSettings("https://git", "https://git", "", "msp", "scripts", "main", "", True)
    with pytest.raises(gitea.GiteaError, match="unreadable"):
        await gitea.fetch_blob(settings, "a" * 40, max_bytes=100, path="Files/Common/a", ref="d" * 40)
    assert len(seen) == 1


def test_file_repositories_sit_beside_the_scripts_repository():
    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "dev", "rmm", True)
    private = gitea.private_files_settings(settings)
    public = gitea.public_settings(settings)
    assert (private.owner, private.repo, private.branch, private.path) == ("msp", "Private-Files", "main", "")
    assert (public.owner, public.repo, public.branch, public.path) == ("msp", "Public-Files", "main", "")
    for name in ("private-files", "Public-Files"):
        with pytest.raises(gitea.GiteaError, match="separate"):
            gitea.private_files_settings(gitea.GiteaSettings("https://git", "https://git", "", "msp", name, "main", "", True))


@pytest.mark.anyio
@pytest.mark.parametrize("prefix", ["", "rmm/"])
async def test_legacy_files_folder_moves_to_private_files(monkeypatch, prefix):
    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", prefix.rstrip("/"), True)
    legacy = {
        prefix + "Common/run.ps1": b"script",
        prefix + "Files/Common/tool.exe": b"MZ\x00tool",
        prefix + "Files/Companies/Renamed/.myportal-company": b'{"company_id": 1}',
        prefix + "Files/Companies/Renamed/setup.exe": b"company copy",
        prefix + "Files/Common/huge.iso": b"",
    }
    private = {"Companies/Renamed/setup.exe": b"already moved"}
    calls = []
    saved = {}

    async def list_files(target):
        source = private if target.repo == "Private-Files" else legacy
        sizes = {prefix + "Files/Common/huge.iso": rmm_script_files.MAX_FILE_BYTES + 1}
        return [gitea.GiteaFile(path, hashlib.sha1(path.encode()).hexdigest(), sizes.get(path, len(raw)))
                for path, raw in source.items()]

    async def resolve_commit(_settings):
        return "d" * 40

    async def fetch_blob(_settings, _sha, *, path, ref, **kwargs):
        assert ref == "d" * 40
        return legacy[path]

    async def create_files(target, files, _message):
        assert target.repo == "Private-Files"
        calls.append(("create", sorted(files)))
        private.update(files)

    async def delete_files(target, files, _message):
        assert target.repo == "scripts"
        calls.append(("delete", sorted(files)))
        for path in files:
            legacy.pop(path)

    async def list_like(pattern):
        assert pattern == "%" + prefix + "Files/%"
        return [{"id": 7, "files": [
            {"source_path": prefix + "Files/Common/tool.exe", "destination": "C:\\Tools\\tool.exe"},
            {"source_path": "Common/other.exe", "destination": "C:\\Tools\\other.exe"},
        ]}, {"id": 8, "files": [{"source_path": "Common/Files/x.exe", "destination": "/opt/x"}]}]

    async def set_files(script_id, files):
        saved[script_id] = files

    monkeypatch.setattr(gitea, "list_files", list_files)
    monkeypatch.setattr(gitea, "resolve_commit", resolve_commit)
    monkeypatch.setattr(gitea, "fetch_blob", fetch_blob)
    monkeypatch.setattr(gitea, "create_files", create_files)
    monkeypatch.setattr(gitea, "delete_files", delete_files)
    monkeypatch.setattr(rmm_script_files.rmm_repo, "list_script_files_like", list_like)
    monkeypatch.setattr(rmm_script_files.rmm_repo, "set_script_files", set_files)

    assert await rmm_script_files.move_legacy_files(settings) == 3
    assert private["Common/tool.exe"] == b"MZ\x00tool"
    assert private["Companies/Renamed/.myportal-company"] == b'{"company_id": 1}'
    # A file already in Private-Files is kept, and the old copy is still removed.
    assert private["Companies/Renamed/setup.exe"] == b"already moved"
    # Files too large to deliver stay behind for an administrator to move.
    assert set(legacy) == {prefix + "Common/run.ps1", prefix + "Files/Common/huge.iso"}
    assert saved == {7: [
        {"source_path": "Common/tool.exe", "destination": "C:\\Tools\\tool.exe"},
        {"source_path": "Common/other.exe", "destination": "C:\\Tools\\other.exe"},
    ]}
    calls.clear()
    assert await rmm_script_files.move_legacy_files(settings) == 0
    assert calls == []
