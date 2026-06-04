"""Sample plugin exposing dashboard-widget JSON."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.features import FeaturePack
from app.repositories import companies as companies_repo


router = APIRouter(tags=["Plugin: Dashboard Widget"])


@router.get("/api/plugins/dashboard-widget")
async def dashboard_widget_data() -> dict[str, object]:
    # Example of reusing existing repository APIs from plugin code.
    company_count = await companies_repo.count_companies()
    return {
        "title": "Custom dashboard widget",
        "message": "Plugin-provided dashboard data.",
        "stats": {"companies": company_count},
    }


PACK = FeaturePack(
    slug="plugin.custom_dashboard_widget",
    version="1.0.0",
    author="MyPortal Team",
    description="Returns JSON payloads for dashboard/widget integrations.",
    homepage="https://github.com/bradhawkins85/MyPortal",
    routers=(router,),
)
