"""Reporting handlers for the ``reporting`` feature pack."""

from __future__ import annotations


def _main():
    from app import main as main_module

    return main_module


reporting_page = _main().reporting_page
reporting_export = _main().reporting_export
admin_reporting = _main().admin_reporting
admin_reporting_new = _main().admin_reporting_new
admin_reporting_edit = _main().admin_reporting_edit
admin_reporting_create = _main().admin_reporting_create
admin_reporting_update = _main().admin_reporting_update
admin_reporting_delete = _main().admin_reporting_delete


__all__ = [
    "reporting_page",
    "reporting_export",
    "admin_reporting",
    "admin_reporting_new",
    "admin_reporting_edit",
    "admin_reporting_create",
    "admin_reporting_update",
    "admin_reporting_delete",
]
