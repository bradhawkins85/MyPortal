import re
from pathlib import Path

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader


def _render_best_practices(
    results,
    catalog=None,
    secure_score=None,
    can_manage_account_exclusions=False,
    can_edit_notes=False,
):
    templates = Path(__file__).parents[1] / "app" / "templates"
    loader = ChoiceLoader(
        [
            DictLoader(
                {
                    "base.html": (
                        "{% block header_title %}{% endblock %}"
                        "{% block title %}{% endblock %}"
                        "{% block header_actions %}{% endblock %}"
                        "{% block styles %}{% endblock %}"
                        "{% block content %}{% endblock %}"
                        "{% block scripts %}{% endblock %}"
                    )
                }
            ),
            FileSystemLoader(templates),
        ]
    )
    template = Environment(loader=loader, autoescape=True).get_template(
        "m365/best_practices.html"
    )
    return template.render(
        results=results,
        catalog=catalog or [],
        has_credentials=True,
        is_super_admin=False,
        can_manage_account_exclusions=can_manage_account_exclusions,
        secure_score=secure_score,
        can_edit_notes=can_edit_notes,
    )


def test_wholly_not_applicable_section_is_hidden_but_global_stat_strip_remains():
    html = _render_best_practices(
        [
            {
                "cis_group": "intune_windows",
                "status": "not_applicable",
                "check_name": "Windows-only check",
            }
        ],
        catalog=[{"cis_group": "intune_windows"}],
    )

    assert "CIS Intune Benchmark – Windows" not in html
    assert "bp-table-intune-windows" not in html
    assert html.count('class="stat-strip bp-filter-strip"') == 1
    assert "Not Applicable" in html


def test_mixed_section_keeps_results_without_rendering_section_stat_strip():
    html = _render_best_practices(
        [
            {
                "cis_group": "intune_windows",
                "status": "pass",
                "check_name": "Applicable check",
            },
            {
                "cis_group": "intune_windows",
                "status": "not_applicable",
                "check_name": "Unsupported check",
            },
        ]
    )

    assert "CIS Intune Benchmark – Windows" in html
    assert html.count('class="stat-strip bp-filter-strip"') == 1
    assert "Not Applicable" in html


def test_results_table_supports_persisted_filtering_and_sorting():
    html = _render_best_practices(
        [
            {
                "cis_group": "",
                "status": "pass",
                "check_name": "Secure defaults",
                "details": "Enabled",
            }
        ]
    )

    assert 'data-table-id="m365-best-practices-bp-table-m365"' in html
    assert 'data-table-filter="bp-table-m365"' in html
    assert 'data-column-key="check" data-sort="string"' in html
    assert 'data-column-key="evaluated" data-sort="date"' in html
    assert '/static/js/tables.js' in html
    assert '/static/js/m365_best_practices.js' in html


def test_stat_filter_script_persists_one_global_status_filter():
    script = (
        Path(__file__).parents[1] / "app" / "static" / "js" / "m365_best_practices.js"
    ).read_text()

    assert "myportal.m365BestPractices.statusFilters.global" in script
    assert "window.localStorage.setItem(STORAGE_KEY" in script
    assert "document.querySelectorAll('.bp-results-table')" in script


def test_score_history_is_available_from_page_header():
    html = _render_best_practices([])

    assert 'href="/m365/best-practices/history"' in html
    assert "Score history" in html


def test_secure_score_is_shown_in_main_stat_strip():
    html = _render_best_practices(
        [{"cis_group": "", "status": "pass", "check_name": "Secure Score"}],
        secure_score={"current": 42.5, "maximum": 80.0, "percentage": 53.1},
    )

    assert "Secure Score" in html
    assert "53.1%" in html
    assert "Microsoft Secure Score: 42.5/80.0" in html


def test_global_stat_strip_counts_all_benchmarks():
    html = _render_best_practices(
        [
            {"cis_group": "", "status": "pass", "check_name": "Main check"},
            {"cis_group": "intune_windows", "status": "fail", "check_name": "Windows check"},
            {"cis_group": "intune_ios", "status": "unknown", "check_name": "iOS check"},
            {"cis_group": "intune_macos", "status": "not_applicable", "check_name": "macOS check"},
        ]
    )

    assert html.count('class="stat-strip bp-filter-strip"') == 1
    assert '<span class="stat-strip__stat-label">Passed</span>' in html
    assert '<span class="stat-strip__stat-label">Failed</span>' in html
    assert '<span class="stat-strip__stat-label">Unknown</span>' in html
    assert '<span class="stat-strip__stat-label">Not Applicable</span>' in html
    assert re.search(r'Passed</span>\s*<span class="stat-strip__stat-value">1</span>', html)
    assert re.search(r'Failed</span>\s*<span class="stat-strip__stat-value">1</span>', html)
    assert re.search(r'Unknown</span>\s*<span class="stat-strip__stat-value">1</span>', html)
    assert re.search(r'Not Applicable</span>\s*<span class="stat-strip__stat-value">1</span>', html)
    assert "CIS Intune Benchmark – Windows" in html
    assert "CIS Intune Benchmark – iOS / iPadOS" in html
    assert "CIS Intune Benchmark – macOS" not in html


def test_account_findings_show_exclusion_state_without_mutation_controls():
    html = _render_best_practices([
        {
            "cis_group": "", "status": "fail", "check_id": "bp_test",
            "check_name": "Account check", "details": "Review accounts",
            "affected_accounts": [
                {"id": "one", "name": "one@example.com", "excluded": False},
                {"id": "two", "name": "two@example.com", "excluded": True},
            ],
        }
    ])

    # Users without account-exclusion permission can still see excluded status.
    assert "one@example.com" in html
    assert "two@example.com — excluded" in html
    assert "/m365/best-practices/account-exclusion/bp_test" not in html


def test_account_findings_show_per_account_exclude_and_restore_controls_when_permitted():
    html = _render_best_practices(
        [
            {
                "cis_group": "",
                "status": "fail",
                "check_id": "bp_test",
                "check_name": "Account check",
                "details": "Review accounts",
                "affected_accounts": [
                    {"id": "one", "name": "one@example.com", "excluded": False},
                    {"id": "two", "name": "two@example.com", "excluded": True},
                ],
            }
        ],
        can_manage_account_exclusions=True,
    )

    assert "/m365/best-practices/account-exclusion/bp_test" in html
    assert "Exclude" in html
    assert "Restore" in html


def test_notes_are_shown_and_editable_for_techs():
    html = _render_best_practices(
        [
            {
                "cis_group": "",
                "status": "fail",
                "check_id": "bp_test",
                "check_name": "Account check",
                "details": "Review accounts",
                "notes": "Customer approved temporary exception.",
            }
        ],
        can_edit_notes=True,
    )

    assert "Customer approved temporary exception." in html
    assert 'action="/m365/best-practices/note/bp_test"' in html
    assert "Save note" in html
