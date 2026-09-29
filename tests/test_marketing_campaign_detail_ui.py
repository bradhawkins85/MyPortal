"""UI regression tests for the marketing campaign detail page."""

from pathlib import Path


TEMPLATE = Path("app/templates/admin/marketing_campaign_detail.html")


def test_summary_and_results_use_stat_strips() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")

    assert template.count('class="stat-strip marketing-campaign-stat-strip"') == 2
    assert template.count("data-stat-strip") == 2
    assert '<dl class="detail-list">' not in template
    assert "campaign_stats.total" in template
    assert "campaign_stats.failed" in template
