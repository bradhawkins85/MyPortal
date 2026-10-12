from __future__ import annotations

import base64
import hashlib

import httpx
import pytest

from app.services import gitea, rmm_script_files


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("destination", ["C:\\Tools\\setup.exe", "/opt/tools/setup.sh"])
def test_absolute_file_destinations(destination):
    assert rmm_script_files.validate_associations([{"source_path": "Files/Common/setup.exe", "destination": destination}])[0]["destination"] == destination


@pytest.mark.parametrize("destination", ["setup.exe", "C:setup.exe", "/opt/../setup.exe", "C:\\Tools\\..\\setup.exe", "/", "/opt/tools/", "C:\\", "C:\\Tools\\setup.exe:stream", "C:\\Tools\\CON", "\\\\host\\share\\setup.exe", "/opt/\nsetup"])
def test_invalid_destinations_are_rejected(destination):
    with pytest.raises(rmm_script_files.ScriptFileError):
        rmm_script_files.validate_associations([{"source_path": "Files/Common/setup.exe", "destination": destination}])


@pytest.mark.parametrize("source", ["/Files/Common/x.exe", "Files/../x.exe", "Files//x.exe", "Files\\Common\\x.exe", "Files/Common/\nx.exe"])
def test_invalid_repository_paths_are_rejected(source):
    with pytest.raises(rmm_script_files.ScriptFileError):
        rmm_script_files.validate_associations([{"source_path": source, "destination": "/tmp/x.exe"}])


def test_duplicate_windows_destinations_are_case_insensitive():
    with pytest.raises(rmm_script_files.ScriptFileError, match="different destination"):
        rmm_script_files.validate_associations([
            {"source_path": "Files/Common/a", "destination": "C:\\Tools\\setup.exe"},
            {"source_path": "Files/Common/b", "destination": "c:/tools/SETUP.exe"},
        ])


@pytest.mark.anyio
async def test_file_catalog_respects_common_and_company_script_scope(monkeypatch):
    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "rmm", True)
    paths = ["rmm/Files/Common/tool.exe", "rmm/Files/Companies/Renamed/.myportal-company",
             "rmm/Files/Companies/Renamed/setup.vbs", "rmm/Files/Companies/Other/setup.exe",
             "rmm/Common/script.ps1", "other/Files/Common/tool.exe"]

    async def list_files(_settings):
        return [gitea.GiteaFile(path, "a" * 40, 1) for path in paths]

    async def companies(**kwargs):
        return [{"id": 1, "name": "Contoso"}, {"id": 2, "name": "Other"}]

    async def fetch_file(_settings, _path):
        return b'{"company_id": 1}'

    monkeypatch.setattr(gitea, "list_files", list_files)
    monkeypatch.setattr(gitea, "fetch_file", fetch_file)
    monkeypatch.setattr(rmm_script_files.companies_repo, "list_companies", companies)
    common = await rmm_script_files.available_files(settings, {"company_id": None})
    assert set(common) == {"rmm/Files/Common/tool.exe"}
    company = await rmm_script_files.available_files(settings, {"company_id": 1})
    assert set(company) == {"rmm/Files/Common/tool.exe", "rmm/Files/Companies/Renamed/setup.vbs"}
    other = await rmm_script_files.available_files(settings, {"company_id": 2})
    assert set(other) == {"rmm/Files/Common/tool.exe", "rmm/Files/Companies/Other/setup.exe"}


@pytest.mark.anyio
async def test_file_choices_create_missing_files_folders_first(monkeypatch):
    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "", True)
    created = []

    async def load_settings():
        return settings

    async def list_files(_settings):
        return [gitea.GiteaFile("Common/README.md", "a" * 40, 1), gitea.GiteaFile("Companies/Contoso/.myportal-company", "b" * 40, 1)]

    async def companies(**kwargs):
        return [{"id": 1, "name": "Contoso"}]

    async def create_files(_settings, files, _message):
        created.append(sorted(files))

    monkeypatch.setattr(gitea, "load_settings", load_settings)
    monkeypatch.setattr(gitea, "list_files", list_files)
    monkeypatch.setattr(gitea, "create_files", create_files)
    monkeypatch.setattr(rmm_script_files.companies_repo, "list_companies", companies)
    choices, warning = await rmm_script_files.file_choices({"company_id": 1})
    assert choices == [] and warning is None
    assert created == [["Files/Common/README.md", "Files/Companies/Contoso/.myportal-company", "Files/Companies/README.md"]]

    async def refuse(_settings, _files, _message):
        raise gitea.GiteaError("Gitea refused to create folders. GITEA_API_TOKEN needs write access to the repository.")

    monkeypatch.setattr(gitea, "create_files", refuse)
    choices, warning = await rmm_script_files.file_choices({"company_id": 1})
    assert choices == [] and "GITEA_API_TOKEN needs write access" in warning


@pytest.mark.anyio
async def test_existing_files_are_never_recreated(monkeypatch):
    from app.services import rmm_scripts

    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "", True)
    # A marker naming a deleted company, in the folder a current company would use.
    files = [gitea.GiteaFile("Files/Companies/Contoso/.myportal-company", "a" * 40, 1)]

    async def fetch_file(_settings, _path):
        return b'{"company_id": 99}'

    monkeypatch.setattr(gitea, "fetch_file", fetch_file)
    layout = await rmm_scripts._read_layout(settings, files, [{"id": 1, "name": "Contoso"}], files_folder=True)
    assert layout.company_folders == {"Contoso": 1}
    assert "Companies/Contoso/.myportal-company" not in layout.missing
    assert set(layout.missing) == {"Common/README.md"}


@pytest.mark.anyio
async def test_queue_rejects_missing_company_files_and_wrong_endpoint_paths(monkeypatch):
    settings = gitea.GiteaSettings("https://git", "https://git", "token", "msp", "scripts", "main", "", True)

    async def load_settings():
        return settings

    async def catalog(_settings, _script):
        return {}

    monkeypatch.setattr(gitea, "load_settings", load_settings)
    monkeypatch.setattr(rmm_script_files, "available_files", catalog)
    script = {"company_id": 1, "files": [{"source_path": "Files/Companies/Contoso/setup.exe", "destination": "C:\\Tools\\setup.exe"}]}
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
