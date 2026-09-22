import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/upgrade.sh").read_text()


def test_failed_upgrade_clears_pending_update_flag():
    cleanup = SCRIPT[
        SCRIPT.index("clear_update_flag_on_failure() {") : SCRIPT.index(
            "\nresolve_environment_file()"
        )
    ]

    assert 'SYSTEM_UPDATE_FLAG_FILE="${PROJECT_ROOT}/var/state/system_update.flag"' in SCRIPT
    assert "if ((status != 0))" in cleanup
    assert 'rm -f -- "$SYSTEM_UPDATE_FLAG_FILE"' in cleanup
    assert "trap clear_update_flag_on_failure EXIT" in cleanup


def _resolve_environment_file(
    tmp_path: Path, system_env: Path, **environment: str
) -> str:
    function = SCRIPT[
        SCRIPT.index("resolve_environment_file() {") : SCRIPT.index('\nVENV_DIR=')
    ]
    project_root = tmp_path / "checkout"
    project_root.mkdir()
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -Eeuo pipefail\n" + function + '\nresolve_environment_file "$SYSTEM_ENV"',
        ],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PROJECT_ROOT": str(project_root),
            "SYSTEM_ENV": str(system_env),
            **environment,
        },
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_upgrade_defaults_to_systemd_environment_file(tmp_path):
    system_env = tmp_path / "myportal.env"
    system_env.write_text("DB_NAME=production\n")

    selected = _resolve_environment_file(
        tmp_path,
        system_env,
        MYPORTAL_ENV_FILE="",
    )

    assert selected == str(system_env)


def test_explicit_environment_file_takes_precedence_and_resolves_symlinks(tmp_path):
    system_env = tmp_path / "myportal.env"
    system_env.write_text("DB_NAME=wrong\n")
    configured_env = tmp_path / "persistent.env"
    configured_env.write_text("DB_NAME=expected\n")
    env_link = tmp_path / "configured.env"
    env_link.symlink_to(configured_env)

    selected = _resolve_environment_file(
        tmp_path,
        system_env,
        MYPORTAL_ENV_FILE=str(env_link),
    )

    assert selected == str(configured_env)


def test_upgrade_prepares_revision_without_mutating_control_checkout():
    assert 'git fetch --quiet origin main' in SCRIPT
    assert 'git archive "$revision"' in SCRIPT
    assert 'RELEASE_DIR="${RELEASE_ROOT}/${TARGET_REVISION}"' in SCRIPT
    assert "git pull" not in SCRIPT
    assert "git restore" not in SCRIPT
    assert 'chown -R myportal:myportal "$PROJECT_ROOT"' not in SCRIPT


def test_release_has_private_dependencies_and_shared_mutable_state():
    assert 'python3 -m venv "$staging"' in SCRIPT
    assert '"$staging/bin/python" -m pip install --disable-pip-version-check --requirement "$lock"' in SCRIPT
    assert 'pip install --disable-pip-version-check --upgrade pip setuptools wheel' not in SCRIPT
    assert 'ln -s "$layer" "${release}/.venv"' in SCRIPT
    assert 'ln -s "$SHARED_ROOT" "$staging/var"' in SCRIPT
    assert 'find "$release" -type f -exec chmod a+rX,a-w' in SCRIPT
    assert 'ln -s "$ENV_FILE" "$staging/.env"' in SCRIPT
    assert 'ln -sfn "$ENV_FILE" "$release/.env"' in SCRIPT
    assert 'chmod a+rx "$RELEASE_ROOT" "$INSTANCE_ROOT" "$SHARED_ROOT"' in SCRIPT
    assert 'ln -s "${SHARED_ROOT}/private_uploads" "${release}/private_uploads"' in SCRIPT
    assert 'ln -s "${SHARED_ROOT}/uploads" "${release}/app/static/uploads"' in SCRIPT


def test_upload_storage_is_seeded_without_removing_legacy_data():
    function = SCRIPT[
        SCRIPT.index("prepare_shared_uploads() {") : SCRIPT.index("\nlink_shared_uploads()")
    ]

    assert 'cp -a "$legacy"/. "$shared"/' in function
    assert 'rm -rf "$legacy"' not in function
    assert 'mv "$legacy"' not in function
    assert 'install -d -m 0750 -o myportal -g myportal' in function
    assert 'chown -R myportal:myportal "$shared"' in function
    assert 'find "$shared" -type d -exec chmod u+rwx {} +' in function


def test_release_upload_paths_point_to_persistent_shared_storage():
    function = SCRIPT[
        SCRIPT.index("link_shared_uploads() {") : SCRIPT.index("\nvalidate_release_uploads()")
    ]

    assert '"${release}/private_uploads"' in function
    assert '"${release}/app/static/uploads"' in function
    assert function.count("ln -s") == 2
    assert 'chown -h myportal:myportal' in function


def test_release_uploads_are_validated_before_service_startup():
    validation = SCRIPT[
        SCRIPT.index("validate_release_uploads() {") : SCRIPT.index("\nrelease_runtime_ready()")
    ]
    prepare = SCRIPT[SCRIPT.index("prepare_release() {") : SCRIPT.index("\nrun_release_manage()")]

    assert '[[ ! -L "$path"' in validation
    assert 'readlink -f "$path"' in validation
    assert 'runuser --user myportal -- test -w "$path"' in validation
    assert prepare.count('validate_release_uploads "$release"') == 2


def test_assigned_legacy_releases_are_repaired_before_migration_and_service_start():
    repair = SCRIPT[
        SCRIPT.index("repair_assigned_release_uploads() {") : SCRIPT.index("\nrelease_runtime_ready()")
    ]

    assert 'for instance in blue green' in repair
    assert 'cp -a -n "$path"/. "$expected"/' in repair
    assert 'ln -s "$expected" "$path"' in repair
    assert 'chown -h myportal:myportal "$path"' in repair
    assert '-type d -exec chmod u+rwx {} +' in repair
    assert 'validate_release_uploads "$release"' in repair

    invoke = SCRIPT.index("\nrepair_assigned_release_uploads\n")
    migration = SCRIPT.index('run_migration_phase "$RELEASE_DIR"', invoke)
    install_unit = SCRIPT.index('install_blue_green_service_unit "$RELEASE_DIR"', invoke)
    assert invoke < migration < install_unit


def test_release_permissions_allow_unprivileged_service_to_read_and_traverse(tmp_path):
    release = tmp_path / "release"
    nested = release / "app" / "templates"
    nested.mkdir(parents=True)
    module = release / "app" / "main.py"
    module.write_text("app = object()\n")
    executable = release / "manage.py"
    executable.write_text("#!/usr/bin/env python3\n")
    secret = tmp_path / "myportal.env"
    secret.write_text("SESSION_SECRET=not-a-real-secret\n")
    env_link = release / ".env"
    env_link.symlink_to(secret)
    release.chmod(0o700)
    (release / "app").chmod(0o700)
    nested.chmod(0o700)
    module.chmod(0o600)
    executable.chmod(0o700)
    secret.chmod(0o600)
    function = SCRIPT[
        SCRIPT.index("make_release_service_readable() {") : SCRIPT.index("\nprepare_release()")
    ]

    result = subprocess.run(
        ["bash", "-c", "set -Eeuo pipefail\n" + function + '\nmake_release_service_readable "$RELEASE"'],
        text=True,
        capture_output=True,
        env={**os.environ, "RELEASE": str(release)},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert release.stat().st_mode & 0o005 == 0o005
    assert nested.stat().st_mode & 0o005 == 0o005
    assert module.stat().st_mode & 0o004 == 0o004
    assert executable.stat().st_mode & 0o005 == 0o005
    assert secret.stat().st_mode & 0o077 == 0


def test_retry_repairs_release_permissions_before_returning():
    retry_branch = SCRIPT[SCRIPT.index('if [[ -e "$release" ]]') : SCRIPT.index("return 0", SCRIPT.index('if [[ -e "$release" ]]'))]

    assert 'make_release_service_readable "$release"' in retry_branch


def test_retry_repairs_the_release_version_marker():
    prepare = SCRIPT[SCRIPT.index("prepare_release() {") : SCRIPT.index("\nrun_release_manage()")]
    existing = prepare[: prepare.index("return 0")]

    assert 'printf \'%s\\n\' "$revision" >"$release/version.txt"' in existing


def test_readiness_timeout_reports_the_last_response():
    wait = SCRIPT[SCRIPT.index("wait_for_version() {") : SCRIPT.index("\nsmoke_test()")]

    assert 'last_body="no response"' in wait
    assert 'last readiness response: ${last_body}' in wait


def test_virtualenv_is_created_only_after_release_reaches_final_path():
    prepare = SCRIPT[SCRIPT.index("prepare_release() {") : SCRIPT.index("\nrun_release_manage()")]
    publish = prepare.index('mv "$staging" "$release"')
    install = prepare.index('install_dependencies "$release"', publish)

    assert publish < install
    assert 'install_dependencies "$staging"' not in prepare
    assert 'rm -rf "$release"' in prepare[install:]


def test_preparation_uses_verified_dependency_cache_and_reports_decision():
    install = SCRIPT[SCRIPT.index("install_dependencies() {") : SCRIPT.index("\ninstall_blue_green_service_unit()")]

    assert 'requirements.lock' in install
    assert 'pyproject.toml' in install
    assert 'sys.implementation.name' in install
    assert 'pip check' in install
    assert 'record_step dependency_layer hit' in install
    assert 'record_step dependency_layer miss' in install


def test_dependency_layer_is_traversable_and_executable_by_service_user(tmp_path):
    function = SCRIPT[
        SCRIPT.index("make_dependency_layer_service_readable() {") : SCRIPT.index(
            "\ninstall_dependencies()"
        )
    ]
    shared = tmp_path / "shared"
    layer = shared / "dependency-layers" / "fixture"
    python = layer / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n")
    shared.chmod(0o755)
    (shared / "dependency-layers").chmod(0o700)
    layer.chmod(0o700)
    python.parent.chmod(0o700)
    python.chmod(0o700)

    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -Eeuo pipefail\n"
            + function
            + '\nmake_dependency_layer_service_readable "$LAYER"',
        ],
        text=True,
        capture_output=True,
        env={**os.environ, "SHARED_ROOT": str(shared), "LAYER": str(layer)},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (shared / "dependency-layers").stat().st_mode & 0o005 == 0o005
    assert layer.stat().st_mode & 0o005 == 0o005
    assert python.parent.stat().st_mode & 0o005 == 0o005
    assert python.stat().st_mode & 0o005 == 0o005


def test_dependency_cache_hit_and_invalid_layer_fallback(tmp_path):
    functions = SCRIPT[
        SCRIPT.index("record_step() {") : SCRIPT.index("\ninstall_blue_green_service_unit()")
    ]
    shared = tmp_path / "shared"
    release_one = tmp_path / "release-one"
    release_two = tmp_path / "release-two"
    release_three = tmp_path / "release-three"
    for release in (release_one, release_two, release_three):
        release.mkdir()
        (release / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
        (release / "requirements.lock").write_text("# exact fixture lock\n")
    command = f"""
set -Eeuo pipefail
SHARED_ROOT={shared!s}
STEP_REPORT=''
python3() {{
  if [[ "${{1:-}}" == -m && "${{2:-}}" == venv ]]; then
    mkdir -p "$3/bin"
    printf '#!/bin/sh\\nexit 0\\n' >"$3/bin/python"
    chmod +x "$3/bin/python"
  else
    command python3 "$@"
  fi
}}
{functions}
install_dependencies {release_one!s}
install_dependencies {release_two!s}
rm {release_two!s}/.venv/.verified
install_dependencies {release_three!s}
printf '%s' "$STEP_REPORT"
"""

    result = subprocess.run(["bash", "-c", command], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    outcomes = [entry.split(":")[1] for entry in result.stdout.split(";")]
    assert outcomes == ["miss", "hit", "miss"]


def test_tray_artifacts_are_verified_before_release_preparation():
    validation = SCRIPT.index('validate_tray_artifacts "$TARGET_REVISION"')
    preparation = SCRIPT.index('prepare_release "$TARGET_REVISION"')

    assert validation < preparation
    assert 'sha256sum --check --strict SHA256SUMS' in SCRIPT
    assert 'Tray artifacts are stale or do not identify revision' in SCRIPT
    assert 'Required tray artifact missing' in SCRIPT
    assert 'publish_tray_artifacts "$TARGET_REVISION"' in SCRIPT


def test_tray_artifact_functions_initialize_revision_before_derived_paths(tmp_path):
    functions = SCRIPT[
        SCRIPT.index("record_step() {") : SCRIPT.index("\ninstall_blue_green_service_unit()")
    ]
    artifact_root = tmp_path / "artifacts"
    shared_root = tmp_path / "shared"
    revision = "fixture-revision"
    source = artifact_root / revision
    source.mkdir(parents=True)
    (source / "REVISION").write_text(revision + "\n")
    (source / "myportal-tray.msi").write_text("msi fixture\n")
    (source / "myportal-tray.pkg").write_text("pkg fixture\n")
    subprocess.run(
        ["sha256sum", "REVISION", "myportal-tray.msi", "myportal-tray.pkg"],
        cwd=source,
        text=True,
        stdout=(source / "SHA256SUMS").open("w"),
        check=True,
    )
    command = f"""
set -Eeuo pipefail
TRAY_ARTIFACT_ROOT={artifact_root!s}
SHARED_ROOT={shared_root!s}
STEP_REPORT=''
{functions}
validate_tray_artifacts {revision}
publish_tray_artifacts {revision}
test "$(readlink "$SHARED_ROOT/published/tray/current")" = "$SHARED_ROOT/published/tray/{revision}"
"""

    result = subprocess.run(
        ["bash", "-c", command], text=True, capture_output=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert (shared_root / "published" / "tray" / revision / "SHA256SUMS").is_file()


def test_restart_does_not_install_or_mutate_dependencies():
    restart = (ROOT / "scripts/restart.sh").read_text()

    assert "pip install" not in restart
    assert "cleanup_invalid_distribution \"$PYTHON_BIN\"" not in restart


def test_server_installer_does_not_invoke_tray_toolchains():
    installer = (ROOT / "scripts/install_environment.sh").read_text()
    tail = installer[installer.index('if [[ "$ENVIRONMENT" == "production" ]]'):]

    assert "install_dotnet\n" not in tail
    assert "install_wix\n" not in tail
    assert "install_go\n" not in tail
    assert "build_tray_installers\n" not in tail


def test_retry_rebuilds_only_a_broken_release_runtime():
    prepare = SCRIPT[SCRIPT.index("prepare_release() {") : SCRIPT.index("\nrun_release_manage()")]
    existing = prepare[: prepare.index("return 0")]

    assert 'if ! release_runtime_ready "$release"' in existing
    assert 'rm -rf "${release}/.venv"' in existing
    assert 'install_dependencies "$release"' in existing


def test_runtime_validation_uses_release_python_module_import():
    function = SCRIPT[
        SCRIPT.index("release_runtime_ready() {") : SCRIPT.index("\nprepare_release()")
    ]

    assert 'runuser --user myportal -- "${release}/.venv/bin/python" -c \'import uvicorn\'' in function
    assert '.venv/bin/uvicorn' not in function


def test_preparation_rejects_runtime_the_service_user_cannot_execute():
    prepare = SCRIPT[SCRIPT.index("prepare_release() {") : SCRIPT.index("\nrun_release_manage()")]

    assert prepare.count('if ! release_runtime_ready "$release"; then') == 3
    assert prepare.count("cause=runtime_not_executable_by_service_user") == 2


def test_systemd_launches_uvicorn_as_module_without_console_script_shebang():
    unit = (ROOT / "deploy/systemd/myportal@.service").read_text()

    assert '"$$release/.venv/bin/python" -m uvicorn' in unit
    assert '"$$release/.venv/bin/uvicorn"' not in unit


def test_systemd_uses_the_ports_checked_by_blue_green_coordinator():
    unit = (ROOT / "deploy/systemd/myportal@.service").read_text()

    assert "blue) port=8001" in unit
    assert "green) port=8002" in unit
    assert "MYPORTAL_INSTANCE_PORT" not in unit
    assert '--port "$$port"' in unit


def test_systemd_does_not_wait_for_unsupported_uvicorn_notifications():
    unit = (ROOT / "deploy/systemd/myportal@.service").read_text()
    installer = (ROOT / "scripts/install_environment.sh").read_text()

    assert "Type=simple" in unit
    assert "\nType=notify\n" not in unit
    assert "Type=simple" in installer
    assert "\nType=notify\n" not in installer


def test_systemd_rejects_duplicate_service_suffix_with_actionable_error():
    unit = (ROOT / "deploy/systemd/myportal@.service").read_text()

    assert "Invalid MyPortal instance %i" in unit
    assert "one .service suffix" in unit
    assert "exit 64" in unit


def test_upgrade_removes_malformed_duplicate_suffix_instances():
    install = SCRIPT[
        SCRIPT.index("install_blue_green_service_unit() {") : SCRIPT.index(
            "\nmake_release_service_readable()"
        )
    ]

    assert "systemctl disable --now myportal@blue.service.service myportal@green.service.service" in install
    assert "systemctl reset-failed myportal@blue.service.service myportal@green.service.service" in install
    assert install.index("systemctl disable --now") < install.index(
        "systemctl enable myportal@blue.service myportal@green.service"
    )


def test_systemd_does_not_mask_shared_upload_symlinks_as_read_only():
    unit = (ROOT / "deploy/systemd/myportal@.service").read_text()

    read_only_line = next(line for line in unit.splitlines() if line.startswith("ReadOnlyPaths="))
    assert "/opt/myportal/releases" not in read_only_line
    assert read_only_line == "ReadOnlyPaths=/opt/myportal/instances"
    assert "ReadWritePaths=/opt/myportal/shared" in unit
    assert 'find "$release" -type d -exec chmod a+rx,a-w' in SCRIPT


def test_atomic_link_runs_with_nounset(tmp_path):
    function = SCRIPT[SCRIPT.index("atomic_link() {") : SCRIPT.index("\ninstance_port()")]
    target = tmp_path / "release"
    target.mkdir()
    link = tmp_path / "current"

    result = subprocess.run(
        ["bash", "-c", "set -Eeuo pipefail\n" + function + '\natomic_link "$TARGET" "$LINK"'],
        text=True,
        capture_output=True,
        env={**os.environ, "TARGET": str(target), "LINK": str(link)},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert link.resolve() == target


def test_deleted_migration_is_not_treated_as_additive_release(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Upgrade Test"], cwd=repo, check=True)
    migrations = repo / "migrations"
    migrations.mkdir()
    migration = migrations / "001_expand.sql"
    migration.write_text("-- phase: expand\n-- compatible-from: *\n-- compatible-to: *\nSELECT 1;\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "old"], cwd=repo, check=True)
    old_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    migration.unlink()
    subprocess.run(["git", "add", "-u"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "delete migration"], cwd=repo, check=True)
    target_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    function = SCRIPT[
        SCRIPT.index("is_additive_migration_only_release() {") : SCRIPT.index("\ncommand -v git")
    ]

    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -Eeuo pipefail\n" + function + '\nif is_additive_migration_only_release "$OLD" "$TARGET"; then exit 9; fi',
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        env={**os.environ, "OLD": old_revision, "TARGET": target_revision},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "fatal:" not in result.stderr


def test_migration_environment_preserves_special_characters(tmp_path):
    release = tmp_path / "release"
    (release / ".venv" / "bin").mkdir(parents=True)
    (release / ".venv" / "bin" / "python").symlink_to(Path(os.sys.executable))
    (release / "manage.py").write_text(
        "import json, os\n"
        "print(json.dumps({key: os.environ.get(key) for key in "
        "['DB_HOST', 'DB_PORT', 'DB_USER', 'DB_PASSWORD', 'DB_NAME']}))\n"
    )
    env_file = tmp_path / "application.env"
    password = "space $dollar #hash !bang 'single'"
    env_file.write_text(
        "DB_HOST=db.internal.example\n"
        "DB_PORT=3307\n"
        "DB_USER=myportal_app\n"
        f'DB_PASSWORD="{password}"\n'
        "DB_NAME=existing_portal\n"
    )
    function = SCRIPT[SCRIPT.index("run_release_manage() {") : SCRIPT.index("\nrun_migration_phase()")]
    result = subprocess.run(
        ["bash", "-c", "set -Eeuo pipefail\n" + function + '\nrun_release_manage "$RELEASE" check'],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "ENV_FILE": str(env_file),
            "RELEASE": str(release),
            "DB_PASSWORD": "stale-inherited-password",
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "DB_HOST": "db.internal.example",
        "DB_PORT": "3307",
        "DB_USER": "myportal_app",
        "DB_PASSWORD": password,
        "DB_NAME": "existing_portal",
    }


def _run_configuration_validation(tmp_path: Path, contents: str, **environment: str):
    env_file = tmp_path / ".env"
    env_file.write_text(contents)
    function = SCRIPT[SCRIPT.index("validate_required_configuration() {") : SCRIPT.index("\natomic_link()")]
    child_environment = dict(os.environ)
    child_environment.pop("SESSION_SECRET", None)
    child_environment.pop("TOTP_ENCRYPTION_KEY", None)
    return subprocess.run(
        [
            "bash",
            "-c",
            'set -Eeuo pipefail\nENV_FILE="$MYPORTAL_ENV_FILE"\n'
            + function
            + "\nvalidate_required_configuration",
        ],
        text=True,
        capture_output=True,
        env={**child_environment, "MYPORTAL_ENV_FILE": str(env_file), **environment},
        check=False,
    )


def test_upgrade_accepts_required_secrets_from_existing_env_file(tmp_path):
    result = _run_configuration_validation(
        tmp_path,
        'SESSION_SECRET="existing session secret"\nTOTP_ENCRYPTION_KEY=existing-totp-key\n',
    )

    assert result.returncode == 0
    assert result.stderr == ""


def test_upgrade_rejects_missing_secrets_before_installing_release(tmp_path):
    result = _run_configuration_validation(tmp_path, "SESSION_SECRET=configured\n")

    assert result.returncode != 0
    assert "Missing required application configuration: TOTP_ENCRYPTION_KEY." in result.stderr
    assert "before running the upgrade" in result.stderr
    validation = SCRIPT.index("validate_required_configuration\n", SCRIPT.index("command -v git"))
    preparation = SCRIPT.index('prepare_release "$TARGET_REVISION"')
    assert validation < preparation


def test_cutover_checks_expected_version_before_nginx_switch():
    version_check = SCRIPT.index('wait_for_version "$(instance_port "$inactive")" "$revision"')
    smoke = SCRIPT.index('smoke_test "$(instance_port "$inactive")" "$revision"')
    cutover = SCRIPT.index('write_upstream "$inactive" "$active"')
    assert version_check < smoke < cutover
    assert "nginx -t" in SCRIPT


def _run_version_check(tmp_path: Path, response: str, expected: str = "target-revision"):
    function = SCRIPT[SCRIPT.index("wait_for_version() {") : SCRIPT.index("\nsmoke_test()")]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text("#!/bin/sh\nprintf '%s' \"$READY_RESPONSE\"\n")
    curl.chmod(0o755)
    return subprocess.run(
        ["bash", "-c", "set -Eeuo pipefail\n" + function + '\nwait_for_version 8001 "$EXPECTED"'],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "READY_RESPONSE": response,
            "EXPECTED": expected,
            "READY_TIMEOUT": "1",
            "READY_REQUEST_TIMEOUT": "10",
        },
        check=False,
    )


def test_version_check_parses_readiness_json_instead_of_matching_format(tmp_path):
    result = _run_version_check(
        tmp_path,
        '{ "checks": {}, "version": "target-revision", "status": "ok" }',
    )

    assert result.returncode == 0, result.stderr


def test_version_check_failure_reports_endpoint_expected_and_reported(tmp_path):
    result = _run_version_check(
        tmp_path,
        '{"status":"ok","version":"repository-timestamp"}',
    )

    assert result.returncode != 0
    assert "cause=stale_version" in result.stderr
    assert "endpoint=http://127.0.0.1:8001/readyz" in result.stderr
    assert "expected=target-revision" in result.stderr
    assert "reported=repository-timestamp" in result.stderr
    assert "curl -fsS --max-time 10 http://127.0.0.1:8001/readyz" in result.stderr


def test_version_check_distinguishes_no_response_from_stale_version(tmp_path):
    result = _run_version_check(tmp_path, "")

    assert result.returncode != 0
    assert "cause=no_response" in result.stderr
    assert "reported=<invalid-or-empty-response>" in result.stderr


def test_inactive_slot_is_stopped_and_verified_against_assigned_release():
    restart = SCRIPT[
        SCRIPT.index("restart_instance_on_release() {") : SCRIPT.index("\nsmoke_test()")
    ]

    assert 'readlink -f "$INSTANCE_ROOT/$instance"' in restart
    assert 'cause=wrong_instance_target' in restart
    stop = restart.index('systemctl stop "myportal@${instance}.service"')
    stale_listener = restart.index("cause=stale_listener", stop)
    start = restart.index('systemctl start "myportal@${instance}.service"', stale_listener)
    process_release = restart.index('readlink -f "/proc/${pid}/cwd"', start)
    assert stop < stale_listener < start < process_release

    rolling = SCRIPT[SCRIPT.index("run_rolling_restart() {") : SCRIPT.index("\nrun_migration_phase()")]
    link = rolling.index('atomic_link "$release" "$INSTANCE_ROOT/$inactive"')
    restart_call = rolling.index('restart_instance_on_release "$inactive" "$release"')
    readiness = rolling.index('wait_for_version "$(instance_port "$inactive")" "$revision"')
    assert link < restart_call < readiness


def test_version_check_allows_slow_startup_readiness_responses():
    assert 'READY_REQUEST_TIMEOUT="${MYPORTAL_READY_REQUEST_TIMEOUT:-10}"' in SCRIPT
    assert 'curl -fsS --max-time "$READY_REQUEST_TIMEOUT" "$endpoint"' in SCRIPT


def test_existing_release_version_metadata_is_repaired_before_restart():
    prepare = SCRIPT[SCRIPT.index("prepare_release() {") : SCRIPT.index("\nrun_release_manage()")]
    existing = prepare[: prepare.index("return 0")]

    assert 'printf \'%s\\n\' "$revision" >"$release/version.txt"' in existing


def test_upgrade_installs_instance_unit_before_restarting_a_slot():
    install_unit = SCRIPT.index('install_blue_green_service_unit "$RELEASE_DIR"')
    rolling_restart = SCRIPT.index('run_rolling_restart "$TARGET_REVISION"')

    assert install_unit < rolling_restart
    assert 'install -m 0644 "$source_unit" "$installed_unit"' in SCRIPT
    assert "systemctl daemon-reload" in SCRIPT
    assert "systemctl enable myportal@blue.service myportal@green.service" in SCRIPT


def test_upgrade_installs_and_starts_blue_green_nginx_after_backend_validation():
    nginx_install = SCRIPT[
        SCRIPT.index("install_blue_green_nginx_config() {") : SCRIPT.index(
            "\nmake_release_service_readable()"
        )
    ]

    assert 'deploy/nginx/myportal-bluegreen.conf' in nginx_install
    assert 'available_dir="/etc/nginx/sites-available"' in nginx_install
    assert 'installed_config="${available_dir}/myportal.conf"' in nginx_install
    assert '/etc/nginx/conf.d/myportal.conf' in nginx_install
    assert 'ln -sfn "$installed_config" "${enabled_dir}/myportal.conf"' in nginx_install
    assert 'systemctl enable --now nginx' in nginx_install

    validation = SCRIPT.index('smoke_test "$(instance_port "$inactive")" "$revision"')
    install = SCRIPT.index(
        'install_blue_green_nginx_config "$release" "$inactive" "$active"',
        validation,
    )
    cutover = SCRIPT.index('write_upstream "$inactive" "$active"', install)
    assert validation < install < cutover


def test_first_nginx_start_uses_the_validated_candidate_upstream():
    install = SCRIPT[
        SCRIPT.index("install_blue_green_nginx_config() {") : SCRIPT.index(
            "\nmake_release_service_readable()"
        )
    ]

    assert 'write_upstream_file "$active" "$inactive"' in install
    write = install.index('write_upstream_file "$active" "$inactive"')
    validate = install.index("\n  nginx -t", write)
    start = install.index("systemctl enable --now nginx", validate)
    assert write < validate < start


def test_pre_cutover_failure_does_not_rewrite_working_upstream():
    rollback = SCRIPT[SCRIPT.index("rollback() {") : SCRIPT.index("\nrun_rolling_restart()")]

    assert '[[ "$upstream_switched" == true ]] && write_upstream' in rollback
    switch = SCRIPT.index('write_upstream "$inactive" "$active"')
    marked = SCRIPT.index("upstream_switched=true", switch)
    assert switch < marked


def test_failure_rolls_back_links_and_upstream():
    assert "rollback()" in SCRIPT
    assert 'trap \'rollback "$active" "$inactive" "$old_inactive" "$upstream_switched"\' ERR' in SCRIPT
    assert 'write_upstream "$old_active" "$new_instance"' in SCRIPT
    assert 'atomic_link "$PREVIOUS_RELEASE" "$CURRENT_LINK"' in SCRIPT


def test_failed_migration_is_reported_before_release_activation():
    migration = SCRIPT.index('run_migration_phase "$RELEASE_DIR"')
    cutover = SCRIPT.index('run_rolling_restart "$TARGET_REVISION"')
    assert migration < cutover
    assert "Database migration failed; release was not activated." in SCRIPT
    assert "Review the migration error above" in SCRIPT


def test_drain_stops_new_work_before_waiting_for_inflight_requests():
    cutover = SCRIPT.index('write_upstream "$inactive" "$active"')
    drain = SCRIPT.index('sleep "$DRAIN_SECONDS"', cutover)
    current = SCRIPT.index('atomic_link "$release" "$CURRENT_LINK"', drain)
    assert cutover < drain < current


def test_remote_validation_rejects_untrusted_and_embedded_credentials():
    assert "validate_origin_remote()" in SCRIPT
    assert "https://github.com/*|git@github.com:*|ssh://git@github.com/*" in SCRIPT
    assert "credential-bearing HTTPS remotes" in SCRIPT
