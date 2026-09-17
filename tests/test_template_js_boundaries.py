from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"
STATIC_JS_DIR = REPO_ROOT / "app" / "static" / "js"

TARGET_BCP_TEMPLATES = [
    "bcp/backups.html",
    "bcp/contacts.html",
    "bcp/emergency_kit.html",
    "bcp/incident.html",
    "bcp/insurance.html",
    "bcp/insurance_claims.html",
    "bcp/market_changes.html",
    "bcp/recovery.html",
    "bcp/recovery_contacts.html",
    "bcp/risks.html",
    "bcp/roles.html",
    "bcp/schedules.html",
]
MAILBOX_TEMPLATES = [
    "m365/shared_mailboxes.html",
    "m365/spam_purge.html",
    "m365/user_mailboxes.html",
]


def _template_text(relative_path: str) -> str:
    return (TEMPLATES_DIR / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", TARGET_BCP_TEMPLATES)
def test_bcp_templates_no_jinja_interpolation_inside_onclick(relative_path: str) -> None:
    text = _template_text(relative_path)
    handlers = re.findall(r'onclick\s*=\s*"([^"]*)"', text)
    handlers += re.findall(r"onclick\s*=\s*'([^']*)'", text)
    assert all('{{' not in handler and '{%' not in handler for handler in handlers), relative_path


@pytest.mark.parametrize("relative_path", MAILBOX_TEMPLATES)
def test_mailbox_templates_keep_jinja_out_of_inline_scripts(relative_path: str) -> None:
    text = _template_text(relative_path)
    inline_scripts = re.findall(
        r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.S | re.I
    )
    assert all("{{" not in script and "{%" not in script for script in inline_scripts), relative_path


@pytest.mark.parametrize(
    "relative_path",
    [
        "bcp_template_actions.js",
        "m365_mailboxes.js",
        "m365_spam_purge.js",
    ],
)
def test_static_javascript_files_parse(relative_path: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--check", str(STATIC_JS_DIR / relative_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("snippet", "context", "expected"),
    [
        (
            """<button data-bcp-payload='{{ {"id": contact.id, "kind": contact.kind, "person_or_org": contact.person_or_org, "phones": contact.phones or "", "email": contact.email or "", "responsibility_or_agency": contact.responsibility_or_agency or ""} | tojson | e }}'></button>""",
            {
                "contact": {
                    "id": 7,
                    "kind": 'Ext"ernal',
                    "person_or_org": "O'Brien\\n<ops>",
                    "phones": r"+61\\help",
                    "email": "",
                    "responsibility_or_agency": 'Lead "Owner" & Co',
                }
            },
            {
                "id": 7,
                "kind": 'Ext"ernal',
                "person_or_org": "O'Brien\\n<ops>",
                "phones": r"+61\\help",
                "email": "",
                "responsibility_or_agency": 'Lead "Owner" & Co',
            },
        ),
        (
            """<button data-bcp-payload='{{ {"id": role.id, "title": role.title, "responsibilities": role.responsibilities} | tojson | e }}'></button>""",
            {
                "role": {
                    "id": 3,
                    "title": 'BCP "Lead" O\'Brien',
                    "responsibilities": "Line 1\\nLine 2 with \\\\ slash",
                }
            },
            {
                "id": 3,
                "title": 'BCP "Lead" O\'Brien',
                "responsibilities": "Line 1\\nLine 2 with \\\\ slash",
            },
        ),
        (
            """<button data-bcp-payload='{{ {"id": policy.id, "type": policy.type, "coverage": policy.coverage or "", "exclusions": policy.exclusions or "", "insurer": policy.insurer or "", "contact": policy.contact or "", "last_review_date": policy.last_review_date, "payment_terms": policy.payment_terms or ""} | tojson | e }}'></button>""",
            {
                "policy": {
                    "id": 11,
                    "type": "Liability",
                    "coverage": "`quoted` coverage\\nline",
                    "exclusions": "<none> & more",
                    "insurer": "ACME",
                    "contact": "ops@example.com",
                    "last_review_date": "2026-09-16",
                    "payment_terms": "Monthly",
                }
            },
            {
                "id": 11,
                "type": "Liability",
                "coverage": "`quoted` coverage\\nline",
                "exclusions": "<none> & more",
                "insurer": "ACME",
                "contact": "ops@example.com",
                "last_review_date": "2026-09-16",
                "payment_terms": "Monthly",
            },
        ),
        (
            """<div data-mailbox-page-config='{{ {"csrfToken": csrf_token or "", "activeStaff": active_staff | default([])} | tojson | e }}'></div>""",
            {
                "csrf_token": 'tok"en\\n',
                "active_staff": [{"email": "alex@example.com", "first_name": "Alex", "last_name": "O'Brien"}],
            },
            {
                "csrfToken": 'tok"en\\n',
                "activeStaff": [{"email": "alex@example.com", "first_name": "Alex", "last_name": "O'Brien"}],
            },
        ),
        (
            """<div data-spam-purge-config='{{ {"refresh": (active_jobs | default([]) | length) > 0} | tojson | e }}'></div>""",
            {"active_jobs": [1]},
            {"refresh": True},
        ),
    ],
)
def test_json_data_attributes_round_trip_safely(snippet: str, context: dict, expected: dict) -> None:
    env = Environment(autoescape=True)
    rendered = env.from_string(snippet).render(**context)
    match = re.search(r"data-[^=]+='([^']+)'", rendered)
    assert match, rendered
    payload = json.loads(html.unescape(match.group(1)))
    assert payload == expected
