from __future__ import annotations

from collections.abc import Iterable

DEFAULT_NOTIFICATION_EVENTS: dict[str, dict[str, object]] = {
    "general": {
        "display_name": "General updates",
        "description": "Announcements and broad system messages shared with all users.",
        "message_template": "{{ message }}",
        "allow_channel_in_app": True,
        "allow_channel_email": True,
        "allow_channel_sms": False,
        "default_channel_in_app": True,
        "default_channel_email": False,
        "default_channel_sms": False,
        "is_user_visible": True,
        "module_actions": [],
    },
    "billing.invoice_overdue": {
        "display_name": "Invoices overdue",
        "description": "Triggered when an invoice remains unpaid past the due date.",
        "message_template": "{{ message }}",
        "allow_channel_in_app": True,
        "allow_channel_email": True,
        "allow_channel_sms": False,
        "default_channel_in_app": True,
        "default_channel_email": True,
        "default_channel_sms": False,
        "is_user_visible": True,
        "module_actions": [],
    },
    "billing.payment_received": {
        "display_name": "Payments received",
        "description": "Notifies finance teams when a customer payment is captured.",
        "message_template": "{{ message }}",
        "allow_channel_in_app": True,
        "allow_channel_email": True,
        "allow_channel_sms": False,
        "default_channel_in_app": True,
        "default_channel_email": False,
        "default_channel_sms": False,
        "is_user_visible": True,
        "module_actions": [],
    },
    "shop.order_submitted": {
        "display_name": "Shop order submitted",
        "description": "Created whenever a customer successfully submits an order.",
        "message_template": "{{ message }}",
        "allow_channel_in_app": True,
        "allow_channel_email": True,
        "allow_channel_sms": False,
        "default_channel_in_app": True,
        "default_channel_email": True,
        "default_channel_sms": False,
        "is_user_visible": True,
        "module_actions": [],
    },
    "shop.order_cancelled": {
        "display_name": "Shop order cancelled",
        "description": "Issued when a submitted shop order is cancelled or voided.",
        "message_template": "{{ message }}",
        "allow_channel_in_app": True,
        "allow_channel_email": True,
        "allow_channel_sms": False,
        "default_channel_in_app": True,
        "default_channel_email": False,
        "default_channel_sms": False,
        "is_user_visible": True,
        "module_actions": [],
    },
    "shop.shipping_status_updated": {
        "display_name": "Shop shipping status updated",
        "description": "Alerts on changes to shipping status or tracking milestones.",
        "message_template": "{{ message }}",
        "allow_channel_in_app": True,
        "allow_channel_email": True,
        "allow_channel_sms": False,
        "default_channel_in_app": True,
        "default_channel_email": False,
        "default_channel_sms": False,
        "is_user_visible": True,
        "module_actions": [],
    },
    "shop.stock_notification": {
        "display_name": "Shop stock notifications",
        "description": "Signals when product stock moves between in-stock and out-of-stock states.",
        "message_template": "{{ message }}",
        "allow_channel_in_app": True,
        "allow_channel_email": True,
        "allow_channel_sms": False,
        "default_channel_in_app": True,
        "default_channel_email": False,
        "default_channel_sms": False,
        "is_user_visible": True,
        "module_actions": [],
    },
    "webhook.failed": {
        "display_name": "Webhook delivery failed",
        "description": "Raised when an outbound webhook request is not acknowledged successfully.",
        "message_template": "{{ message }}",
        "allow_channel_in_app": True,
        "allow_channel_email": True,
        "allow_channel_sms": False,
        "default_channel_in_app": True,
        "default_channel_email": True,
        "default_channel_sms": False,
        "is_user_visible": True,
        "module_actions": [],
    },
    "webhook.retried": {
        "display_name": "Webhook delivery retried",
        "description": "Broadcast when the webhook monitor retries delivery after a failure.",
        "message_template": "{{ message }}",
        "allow_channel_in_app": True,
        "allow_channel_email": False,
        "allow_channel_sms": False,
        "default_channel_in_app": True,
        "default_channel_email": False,
        "default_channel_sms": False,
        "is_user_visible": True,
        "module_actions": [],
    },
}

DEFAULT_NOTIFICATION_EVENT_TYPES: list[str] = list(DEFAULT_NOTIFICATION_EVENTS.keys())


def merge_event_types(*collections: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for collection in collections:
        if not collection:
            continue
        for raw in collection:
            if not raw:
                continue
            value = str(raw).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            ordered.append(value)
    return sorted(ordered)
