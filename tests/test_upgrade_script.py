import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/upgrade.sh").read_text()


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
    assert 'python3 -m venv "${release}/.venv"' in SCRIPT
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


def test_release_upload_paths_point_to_persistent_shared_storage():
    function = SCRIPT[
        SCRIPT.index("link_shared_uploads() {") : SCRIPT.index("\nrelease_runtime_ready()")
    ]

    assert '"${release}/private_uploads"' in function
    assert '"${release}/app/static/uploads"' in function
    assert function.count("ln -s") == 2


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


def test_virtualenv_is_created_only_after_release_reaches_final_path():
    prepare = SCRIPT[SCRIPT.index("prepare_release() {") : SCRIPT.index("\nrun_release_manage()")]
    publish = prepare.index('mv "$staging" "$release"')
    install = prepare.index('install_dependencies "$release"', publish)

    assert publish < install
    assert 'install_dependencies "$staging"' not in prepare
    assert 'rm -rf "$release"' in prepare[install:]


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

    assert '"${release}/.venv/bin/python" -c \'import uvicorn\'' in function
    assert '.venv/bin/uvicorn' not in function


def test_systemd_launches_uvicorn_as_module_without_console_script_shebang():
    unit = (ROOT / "deploy/systemd/myportal@.service").read_text()

    assert '"$$release/.venv/bin/python" -m uvicorn' in unit
    assert '"$$release/.venv/bin/uvicorn"' not in unit


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


def test_upgrade_installs_instance_unit_before_restarting_a_slot():
    install_unit = SCRIPT.index('install_blue_green_service_unit "$RELEASE_DIR"')
    rolling_restart = SCRIPT.index('run_rolling_restart "$TARGET_REVISION"')

    assert install_unit < rolling_restart
    assert 'install -m 0644 "$source_unit" "$installed_unit"' in SCRIPT
    assert "systemctl daemon-reload" in SCRIPT
    assert "systemctl enable myportal@blue.service myportal@green.service" in SCRIPT


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


def test_drain_stops_new_work_before_waiting_for_inflight_requests():
    cutover = SCRIPT.index('write_upstream "$inactive" "$active"')
    drain = SCRIPT.index('sleep "$DRAIN_SECONDS"', cutover)
    current = SCRIPT.index('atomic_link "$release" "$CURRENT_LINK"', drain)
    assert cutover < drain < current


def test_remote_validation_rejects_untrusted_and_embedded_credentials():
    assert "validate_origin_remote()" in SCRIPT
    assert "https://github.com/*|git@github.com:*|ssh://git@github.com/*" in SCRIPT
    assert "credential-bearing HTTPS remotes" in SCRIPT
