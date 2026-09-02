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
