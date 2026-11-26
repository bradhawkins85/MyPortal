"""Handlers for the read-only Fail2ban status page."""

from __future__ import annotations

import asyncio

from fastapi import Request

from . import service


def _main():
    from app import main as main_module

    return main_module


def _filter_bans(bans: list[dict], *, jail: str, query: str) -> list[dict]:
    query = query.strip().lower()
    return [
        row
        for row in bans
        if (not jail or row["jail"] == jail)
        and (not query or query in row["ip"].lower() or query in (row["reason"] or "").lower())
    ]


async def admin_fail2ban_page(request: Request):
    from app.core.config import get_settings

    main = _main()
    user, redirect = await main._require_super_admin_page(request)
    if redirect:
        return redirect

    settings = get_settings()
    overview = await asyncio.to_thread(
        service.build_overview,
        settings.fail2ban_log_path,
        getattr(settings, "fail2ban_status_path", None),
    )
    jail_names = {jail["name"] for jail in overview["jails"]}
    jail_filter = (request.query_params.get("jail") or "").strip()
    if jail_filter not in jail_names:
        jail_filter = ""
    search = (request.query_params.get("q") or "").strip()[:100]

    response = await main._render_template(
        "admin/fail2ban.html",
        request,
        user,
        extra={
            "title": "Fail2ban",
            "f2b": overview,
            "f2b_bans": _filter_bans(overview["bans"], jail=jail_filter, query=search),
            "f2b_jail_filter": jail_filter,
            "f2b_search": search,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
