from pathlib import Path

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader


def _render_best_practices(results, catalog=None):
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
    )


def test_wholly_not_applicable_section_and_stat_strip_are_hidden():
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
    assert "Not Applicable" not in html


def test_mixed_section_keeps_results_and_its_stat_strip():
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
    assert 'data-bp-table="bp-table-intune-windows"' in html
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


def test_stat_filter_script_persists_each_checks_table_separately():
    script = (
        Path(__file__).parents[1] / "app" / "static" / "js" / "m365_best_practices.js"
    ).read_text()

    assert "myportal.m365BestPractices.statusFilters." in script
    assert "window.localStorage.setItem(storageKey(tableId)" in script
    assert "loadStatuses(tableId, availableStatuses)" in script


def test_score_history_is_available_from_page_header():
    html = _render_best_practices([])

    assert 'href="/m365/best-practices/history"' in html
    assert "Score history" in html


def test_account_findings_have_per_check_exclude_and_restore_controls():
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

    # Non-admin users can see which findings were excluded, but cannot mutate them.
    assert "one@example.com" in html
    assert "two@example.com — excluded" in html
    assert "/m365/best-practices/account-exclusion/bp_test" not in html
