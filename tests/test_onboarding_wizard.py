"""Tests for scripts/onboarding_wizard.py."""

from __future__ import annotations

import importlib.util
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "onboarding_wizard.py"

_spec = importlib.util.spec_from_file_location("onboarding_wizard", SCRIPT)
wizard = importlib.util.module_from_spec(_spec)
sys.modules["onboarding_wizard"] = wizard
_spec.loader.exec_module(wizard)


def _prompter(answers: dict[str, str] | None = None, default: str = ""):
    """Answer each prompt from the first matching substring, else *default*."""

    asked: list[str] = []

    def answer(prompt: str) -> str:
        asked.append(prompt)
        for fragment, value in (answers or {}).items():
            if fragment in prompt:
                return value
        return default

    return wizard.Prompter(answer, answer, io.StringIO()), asked


def _complete_env(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "myportal.env"
    path.write_text(
        "# Operator comment\n"
        "ENVIRONMENT=production\n"
        "PORTAL_URL=https://portal.example.com\n"
        f"SESSION_SECRET={'a1B2c3D4e5F6g7H8' * 4}\n"
        f"TOTP_ENCRYPTION_KEY={'z9Y8x7W6v5U4t3S2' * 4}\n"
        "DB_HOST=localhost\nDB_USER=myportal\nDB_PASSWORD=pw\nDB_NAME=myportal\n"
        "TEMPLATE='Ticket {id} ok'\n" + extra,
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _disable_all_but(*keep: str) -> str:
    packs, modules = [], []
    for feature in wizard.FEATURES:
        if feature.key not in keep:
            packs.extend(feature.packs)
            modules.extend(feature.modules)
    return f"DISABLED_FEATURE_PACKS={','.join(packs)}\nDISABLED_MODULES={','.join(modules)}\n"


# -- Catalogue --------------------------------------------------------------
def test_catalogue_covers_every_pack_component_and_module():
    from app.core.core_components import CORE_COMPONENT_SLUGS
    from app.core.features import discover_builtin_feature_pack_slugs
    from app.services.modules import DEFAULT_MODULES

    packs = [slug for feature in wizard.FEATURES for slug in feature.packs]
    modules = [slug for feature in wizard.FEATURES for slug in feature.modules]
    assert len(packs) == len(set(packs)), "a pack slug is listed twice"
    assert len(modules) == len(set(modules)), "a module slug is listed twice"
    assert set(packs) == set(discover_builtin_feature_pack_slugs()) | set(CORE_COMPONENT_SLUGS)
    assert set(modules) == {module["slug"] for module in DEFAULT_MODULES}
    assert all(feature.parent in wizard.FEATURES_BY_KEY for feature in wizard.FEATURES if feature.parent)


def test_modules_are_grouped_with_their_feature_pack():
    from app.core.module_capabilities import feature_pack_for_module

    for feature in wizard.FEATURES:
        for module in feature.modules:
            pack = feature_pack_for_module(module)
            if pack:
                assert pack in feature.packs, f"{module} belongs with pack {pack}"


def test_every_setting_is_documented_in_env_example():
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(), re.M))
    keys = [s.key for section in wizard.CORE_SECTIONS for s in section.settings]
    keys += [s.key for feature in wizard.FEATURES for s in feature.settings]
    assert len(keys) == len(set(keys)), "a setting is prompted for twice"
    assert sorted(set(keys) - documented) == []


def test_catalogue_choices_and_conditions_are_consistent():
    keys = {s.key: s for section in wizard.CORE_SECTIONS for s in section.settings}
    keys.update({s.key: s for feature in wizard.FEATURES for s in feature.settings})
    for setting in keys.values():
        if setting.kind == "choice":
            assert setting.default in setting.choices
        if setting.required_when:
            assert keys[setting.required_when].kind == "bool"


# -- Env file handling ------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    ["", "plain", "https://x.example/a?b=c", "has space", "Ticket {id}", "it's", 'q"uote$HOME', "a\\b"],
)
def test_format_and_parse_round_trip(value):
    assert wizard.parse_value(wizard.format_value(value)) == value


def test_parse_value_handles_comments_and_quotes():
    assert wizard.parse_value("true  # comment") == "true"
    assert wizard.parse_value('"a # b"') == "a # b"
    assert wizard.parse_value("'x'") == "x"


def test_set_updates_in_place_and_appends_new_keys(tmp_path):
    path = _complete_env(tmp_path)
    env = wizard.EnvFile.load(path)
    env.set("DB_HOST", "db.internal", "Database")
    env.set("HUDU_BASE_URL", "https://hudu.example.com", "Hudu")
    backup = env.save()

    text = path.read_text()
    assert text.startswith("# Operator comment\nENVIRONMENT=production\n")
    assert "DB_HOST=db.internal\n" in text and "DB_HOST=localhost" not in text
    assert "TEMPLATE='Ticket {id} ok'" in text
    assert text.rstrip().endswith("# Hudu\nHUDU_BASE_URL=https://hudu.example.com")
    assert backup is not None and "DB_HOST=localhost" in backup.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


# -- Enable / disable -------------------------------------------------------
def test_disabling_keeps_settings_and_enabling_restores(tmp_path):
    path = _complete_env(tmp_path, "XERO_CLIENT_ID=abc\nXERO_CLIENT_SECRET=def\n")
    env = wizard.EnvFile.load(path)
    xero = wizard.FEATURES_BY_KEY["xero"]

    wizard.set_feature_enabled(env, xero, False)
    values = env.values()
    assert values["DISABLED_FEATURE_PACKS"] == "xero"
    assert values["DISABLED_MODULES"] == "xero"
    assert values["XERO_CLIENT_ID"] == "abc"

    wizard.set_feature_enabled(env, wizard.FEATURES_BY_KEY["hudu"], False)
    assert env.values()["DISABLED_FEATURE_PACKS"] == "xero,hudu"

    wizard.set_feature_enabled(env, xero, True)
    values = env.values()
    assert values["DISABLED_FEATURE_PACKS"] == "hudu"
    assert values["DISABLED_MODULES"] == "hudu"
    assert values["XERO_CLIENT_SECRET"] == "def"


def test_wizard_disable_answer_only_touches_disabled_lists(tmp_path):
    path = _complete_env(tmp_path, "XERO_CLIENT_ID=abc\nXERO_CLIENT_SECRET=def\n")
    prompter, _ = _prompter({"Enable Xero?": "n"})
    wiz = wizard.Wizard(wizard.EnvFile.load(path), prompter)
    wiz.run_feature(wizard.FEATURES_BY_KEY["xero"])
    wiz.env.save()

    values = wizard.EnvFile.load(path).values()
    assert values["XERO_CLIENT_ID"] == "abc" and values["XERO_CLIENT_SECRET"] == "def"
    assert "xero" in wizard.slug_list(values["DISABLED_FEATURE_PACKS"])
    assert wiz.changes == ["Xero: disabled"]


def test_children_of_a_disabled_feature_are_skipped(tmp_path):
    path = _complete_env(tmp_path, "DISABLED_FEATURE_PACKS=assets\n")
    prompter, asked = _prompter()
    wiz = wizard.Wizard(wizard.EnvFile.load(path), prompter)
    wiz.run_feature(wizard.FEATURES_BY_KEY["ipam"])
    assert asked == []


# -- Interactive prompts ----------------------------------------------------
def test_rerun_pressing_enter_changes_nothing(tmp_path):
    path = _complete_env(tmp_path, _disable_all_but())
    # The first pass writes the defaults of settings missing from the file.
    prompter, _ = _prompter()
    assert wizard.Wizard(wizard.EnvFile.load(path), prompter).run() is True
    assert wizard.EnvFile.load(path).values()["CRON_TIMEZONE"] == "UTC"

    before = path.read_text()
    prompter, asked = _prompter()
    changed = wizard.Wizard(wizard.EnvFile.load(path), prompter).run()
    assert asked and changed is False
    assert path.read_text() == before


def test_enabling_a_feature_prompts_for_its_settings(tmp_path):
    path = _complete_env(tmp_path, _disable_all_but())
    prompter, _ = _prompter(
        {"Enable Hudu?": "y", "Hudu URL": "https://hudu.example.com", "Hudu API key": "k" * 20},
    )
    wiz = wizard.Wizard(wizard.EnvFile.load(path), prompter)
    wiz.run_feature(wizard.FEATURES_BY_KEY["hudu"])
    values = wiz.env.values()
    assert "hudu" not in wizard.slug_list(values["DISABLED_FEATURE_PACKS"])
    assert values["HUDU_BASE_URL"] == "https://hudu.example.com"
    assert values["HUDU_API_KEY"] == "k" * 20


def test_invalid_value_is_reprompted(tmp_path):
    path = _complete_env(tmp_path)
    answers = iter(["not a url", "https://hudu.example.com"])
    asked: list[str] = []

    def answer(prompt):
        asked.append(prompt)
        return next(answers)

    wiz = wizard.Wizard(wizard.EnvFile.load(path), wizard.Prompter(answer, answer, io.StringIO()))
    wiz.prompt_setting(next(s for s in wizard.FEATURES_BY_KEY["hudu"].settings if s.key == "HUDU_BASE_URL"), "Hudu")
    assert len(asked) == 2
    assert wiz.env.values()["HUDU_BASE_URL"] == "https://hudu.example.com"


def test_missing_required_secret_is_generated(tmp_path):
    path = tmp_path / ".env"
    path.write_text("SESSION_SECRET=change-me\n")
    prompter, _ = _prompter()
    wiz = wizard.Wizard(wizard.EnvFile.load(path), prompter)
    setting = wizard.CORE_SECTIONS[1].settings[0]
    wiz.prompt_setting(setting, "Security")
    assert wizard.weak_secret_reason(wiz.env.values()["SESSION_SECRET"]) == ""


def test_changing_encryption_key_requires_confirmation(tmp_path):
    path = _complete_env(tmp_path)
    original = wizard.EnvFile.load(path).values()["TOTP_ENCRYPTION_KEY"]
    replacement = "n3wK3yN3wK3yN3wK3y-abcdefghijklmnopqrstuvwxyz"
    prompter, _ = _prompter({"encryption key": replacement, "Change it anyway?": "n"})
    wiz = wizard.Wizard(wizard.EnvFile.load(path), prompter)
    setting = next(s for s in wizard.CORE_SECTIONS[1].settings if s.key == "TOTP_ENCRYPTION_KEY")
    wiz.prompt_setting(setting, "Security")
    assert wiz.env.values()["TOTP_ENCRYPTION_KEY"] == original


def test_docker_mode_hides_database_settings(tmp_path):
    path = _complete_env(tmp_path)
    prompter, asked = _prompter({"Review its settings?": "y"})
    wiz = wizard.Wizard(wizard.EnvFile.load(path), prompter, docker=True)
    wiz.run_core()
    assert not any("Database host" in prompt or "Database password" in prompt for prompt in asked)


# -- Check mode ---------------------------------------------------------------
def test_check_passes_for_a_complete_file(tmp_path):
    path = _complete_env(tmp_path, _disable_all_but("tickets", "companies"))
    out = io.StringIO()
    assert wizard.run_check(wizard.EnvFile.load(path), docker=False, out=out) == 0, out.getvalue()


def test_check_reports_missing_and_invalid_values(tmp_path):
    path = _complete_env(
        tmp_path,
        _disable_all_but("hudu") + "DISABLED_MODULES=bogus\nSESSION_SECRET=change-me\nDB_HOST=a\nDB_HOST=b\n",
    )
    out = io.StringIO()
    assert wizard.run_check(wizard.EnvFile.load(path), docker=False, out=out) == 1
    report = out.getvalue()
    assert "SESSION_SECRET" in report and "placeholder" in report
    assert "HUDU_BASE_URL is missing" in report
    assert "unknown slug(s): bogus" in report
    assert "DB_HOST is defined more than once" in report


def test_check_cli_exit_codes(tmp_path):
    missing = tmp_path / "missing.env"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--env-file", str(missing)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    good = _complete_env(tmp_path, _disable_all_but("tickets"))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--env-file", str(good)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout


def test_interactive_mode_refuses_without_a_terminal(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(tmp_path / "x.env")],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
    )
    assert result.returncode == 2
    assert not (tmp_path / "x.env").exists()


def test_feature_argument_accepts_keys_and_slugs():
    keys, core, unknown = wizard.resolve_feature_keys(["xero", "m365-mail,core", "nope"])
    assert keys == ["xero", "m365_mail"] and core is True and unknown == ["nope"]
