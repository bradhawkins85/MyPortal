"""TacticalRMM handlers for the ``tacticalrmm`` feature pack."""

from __future__ import annotations

from fastapi import Request


def _main():
    from app import main as main_module

    return main_module


async def admin_push_companies_to_tactical_rmm(request: Request):
    return await _main().admin_push_companies_to_tactical_rmm(request=request)


async def admin_pull_companies_from_tactical_rmm(request: Request):
    return await _main().admin_pull_companies_from_tactical_rmm(request=request)

