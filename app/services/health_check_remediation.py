"""Scripts and tickets for what the health check's automatic checks find.

On the Detections page (``/health-checks/detections``) an admin links a Gitea
script to a detection (:data:`app.services.health_check_detections.DETECTIONS`).
When a health check completes and its automatic checks found that detection,
the script is queued on the computer's RMM agent as an ordinary RMM run
(``run_source = 'health_check'``): for example ``DISM /RestoreHealth`` when the
Windows component store is damaged but repairable.

A link can also raise a ticket, linked to the asset, for a technician to
investigate, alongside or instead of the script. While that ticket is open,
later health checks that find the detection again add an update to it rather
than raising another.

A link applies to one company, or to every company (super admins, scripts in
``Common/`` only). A company's own link replaces the every-company one for its
devices, so a company can use another script or none at all. A script runs at
most once per health check, however many of its detections were found. Script
values may use the ``{{health.*}}`` variables (:data:`HEALTH_VARIABLES`).
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.logging import log_info, log_warning
from app.repositories import ai_health_check_remediations as repo
from app.repositories import rmm as rmm_repo
from app.security.encryption import encrypt_secret
from app.services import health_check_detections as detections
from app.services import rmm_actions, rmm_automation, rmm_scripts
from app.services.rmm_scripts import RunRequestError

RUN_SOURCE = "health_check"
SCOPES = ("company", "global")
HEALTH_VARIABLES = (
    ("Detection", "{{health.detection}}"),
    ("Detection name", "{{health.label}}"),
    ("What was found", "{{health.title}}"),
    ("Details", "{{health.detail}}"),
    ("Severity", "{{health.severity}}"),
    ("Health check ID", "{{health.check_id}}"),
)
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


class RemediationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def resolve(links: Sequence[Mapping[str, Any]], detection_key: str, company_id: int) -> Mapping[str, Any] | None:
    """The link that applies to a company's devices: its own, or else the one for every company."""

    matching = [link for link in links if link.get("detection_key") == detection_key]
    own = next((link for link in matching if link.get("company_id") is not None
                and int(link["company_id"]) == int(company_id)), None)
    return own or next((link for link in matching if link.get("company_id") is None), None)


def script_values(link: Mapping[str, Any] | None, script: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The script's fields with the link's saved values; secrets are never sent back."""

    if not link or not script:
        return []
    entries = rmm_automation.decrypt_entries(link.get("entries_encrypted"))
    fields = []
    for field_def in rmm_scripts.script_fields(script):
        value = entries.get(field_def["key"], "")
        secret = bool(field_def["sensitive"]) and not rmm_scripts._TOKEN.search(value)
        fields.append({**field_def, "value": "" if secret else value, "value_set": bool(value)})
    return fields


async def save_link(
    *,
    detection_key: str,
    scope: str,
    company_id: int,
    form: Mapping[str, Any],
    user_id: int | None,
    is_super_admin: bool,
) -> int:
    """Create or update a detection's link from the Detections page.

    ``company_id`` is the company the admin is working in. A company link
    without a script or ticket turns the every-company link off for that company.
    """

    errors: dict[str, str] = {}
    if detection_key not in detections.DETECTIONS:
        errors["detection"] = "Choose a detection."
    link_company: int | None = int(company_id)
    if scope == "global":
        if not is_super_admin:
            errors["scope"] = "Only super admins can link scripts for every company."
        link_company = None
    elif scope not in SCOPES:
        errors["scope"] = "Choose who the script runs for."
    existing = await repo.get_for_scope(detection_key, link_company, with_secrets=True) if not errors else None
    create_ticket = _checked(form.get("create_ticket"))
    script: dict[str, Any] | None = None
    entries: dict[str, str] = {}
    if str(form.get("script_id") or "").strip():
        try:
            script = await rmm_automation._scoped_script(form.get("script_id"), link_company)
        except RunRequestError as exc:
            errors.update(exc.errors)
    elif scope == "global" and not create_ticket:
        errors["script"] = "Choose a script, or create a ticket."
    if script is not None and existing and existing.get("script_id") == script["id"]:
        # A newly chosen script's fields appear after saving; only the current
        # script's values are read from the form.
        previous = rmm_automation.decrypt_entries(existing.get("entries_encrypted"))
        for field_def in rmm_scripts.script_fields(script):
            key = field_def["key"]
            text = str(form.get("entry:" + key) or "")[:65535]
            if not text.strip() and field_def["sensitive"] and previous.get(key):
                text = previous[key]  # a blank secret keeps the saved one
            if text.strip():
                entries[key] = text
    if errors:
        raise RemediationError(errors)
    return await repo.save(
        detection_key=detection_key,
        company_id=link_company,
        script_id=int(script["id"]) if script else None,
        entries_encrypted=encrypt_secret(json.dumps(entries)) if entries else None,
        inputs=rmm_scripts.stored_inputs(script, entries) if script else [],
        timeout_seconds=rmm_scripts.clamp_timeout(
            form.get("timeout_seconds"), int((script or {}).get("default_timeout_seconds") or 600)
        ),
        is_enabled=(bool(script) or create_ticket) and _checked(form.get("is_enabled")),
        user_id=user_id,
        create_ticket=create_ticket,
    )


def _checked(value: Any) -> bool:
    return str(value or "").lower() in {"1", "on", "true", "yes"}


def health_context(check: Mapping[str, Any], detection_key: str, signals: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    found = [signal for signal in signals if signal.get("key") == detection_key]
    worst = min(found, key=lambda s: _SEVERITY_RANK.get(str(s.get("severity")), 9)) if found else {}
    item = detections.DETECTIONS.get(detection_key, {})
    return {"health": {
        "detection": detection_key,
        "label": item.get("label") or detection_key,
        "title": "; ".join(str(s.get("title") or "") for s in found)[:2000],
        "detail": "; ".join(str(s.get("detail")) for s in found if s.get("detail"))[:2000],
        "severity": str(worst.get("severity") or item.get("severity") or ""),
        "check_id": check.get("id"),
    }}


async def run_for_check(check: Mapping[str, Any], signals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Queue the linked scripts and raise the tickets for what the check found.

    Returns what happened, for the report."""

    keys = list(dict.fromkeys(str(s.get("key")) for s in signals if s.get("key")))
    company_id = int(check.get("company_id") or 0)
    if not keys or not company_id:
        return []
    links = await repo.list_for_company(company_id, with_secrets=True)
    chosen = [(key, resolve(links, key, company_id)) for key in keys]
    chosen = [(key, link) for key, link in chosen if link and link.get("is_enabled")]
    scripted = [(key, link) for key, link in chosen if link.get("script_id")]
    ticketed = [(key, link) for key, link in chosen if link.get("create_ticket")]
    results = await _run_scripts(check, signals, scripted, company_id) if scripted else []
    for key, link in ticketed:
        results.append(await _ticket(check, signals, key, link))
    return results


async def _run_scripts(
    check: Mapping[str, Any], signals: Sequence[Mapping[str, Any]],
    chosen: Sequence[tuple[str, Mapping[str, Any]]], company_id: int,
) -> list[dict[str, Any]]:
    agent = await rmm_repo.get_asset_agent(int(check["asset_id"]))
    if not agent or int(agent.get("company_id") or 0) != company_id:
        return [{"detection": key, "label": detections.DETECTIONS[key]["label"], "script_name": link.get("script_name"),
                 "run_id": None, "summary": "Not run: the computer has no active RMM agent."} for key, link in chosen]
    results: list[dict[str, Any]] = []
    queued: set[int] = set()
    for key, link in chosen:
        item = {
            "detection": key,
            "label": detections.DETECTIONS[key]["label"],
            "script_name": link.get("script_name") or "The script",
            "scope": "company" if link.get("company_id") is not None else "global",
            "run_id": None,
        }
        script_id = int(link["script_id"])
        if script_id in queued:
            results.append({**item, "summary": "Already queued for another detection in this health check."})
            continue
        try:
            run_id, summary, _attempted = await rmm_actions.run_script(
                script_id=script_id,
                entries_encrypted=link.get("entries_encrypted"),
                inputs=link.get("inputs"),
                timeout_seconds=link.get("timeout_seconds"),
                agent=agent,
                run_source=RUN_SOURCE,
                extra_context=health_context(check, key, signals),
            )
        except Exception as exc:  # noqa: BLE001 - one script must not stop the others
            log_warning("Health check remediation failed", health_check_id=check.get("id"), detection=key,
                        error=str(exc)[:300])
            run_id, summary = None, f"Not run: {str(exc)[:300] or exc.__class__.__name__}"
        if run_id is not None:
            queued.add(script_id)
            summary = summary.replace("Ran ", "Queued ", 1)
        await repo.record_run(int(link["id"]), summary)
        results.append({**item, "run_id": run_id, "summary": summary})
        log_info("Health check remediation queued" if run_id else "Health check remediation not run",
                 health_check_id=check.get("id"), detection=key, script_id=script_id, run_id=run_id)
    return results


def _found_html(check: Mapping[str, Any], detection_key: str, signals: Sequence[Mapping[str, Any]]) -> str:
    found = [signal for signal in signals if signal.get("key") == detection_key]
    items = "".join(
        f"<li>{html.escape(str(s.get('title') or ''))}"
        + (f" — {html.escape(str(s.get('detail')))}" if s.get("detail") else "") + "</li>"
        for s in found
    )
    return (f"<ul>{items}</ul>" if items else "") + (
        f'<p><a href="/health-checks/{int(check["id"])}">Open the health check report</a></p>' if check.get("id") else ""
    )


async def _ticket(
    check: Mapping[str, Any], signals: Sequence[Mapping[str, Any]], detection_key: str, link: Mapping[str, Any],
) -> dict[str, Any]:
    """Raise a ticket for the asset, or update the one this detection raised while it is still open."""

    label = detections.DETECTIONS[detection_key]["label"]
    asset_id = int(check["asset_id"])
    device = str(check.get("asset_name") or f"asset {asset_id}")
    item = {
        "detection": detection_key,
        "label": label,
        "kind": "ticket",
        "scope": "company" if link.get("company_id") is not None else "global",
        "ticket_id": None,
    }
    reference = f"health-check-detection:{detection_key}:{asset_id}"
    found = _found_html(check, detection_key, signals)
    try:
        ticket = await rmm_actions.open_ticket(await repo.get_ticket(detection_key, asset_id))
        if ticket:
            await rmm_actions.add_ticket_update(
                ticket, reference=reference,
                body=f"<p>A health check found <strong>{html.escape(label)}</strong> on {html.escape(device)} again.</p>"
                     + found,
            )
            summary = f"Updated ticket {rmm_actions.ticket_label(ticket)} for {device}."
        else:
            ticket = await rmm_actions.raise_ticket(
                agent={"company_id": check.get("company_id"), "asset_id": asset_id},
                subject=f"{label}: {device}",
                description=(f"<p>A health check found <strong>{html.escape(label)}</strong> on {html.escape(device)}. "
                             "Please investigate.</p>" + found),
                reference=reference,
            )
            await repo.set_ticket(detection_key, asset_id, int(ticket["id"]))
            summary = f"Raised ticket {rmm_actions.ticket_label(ticket)} for {device}."
    except Exception as exc:  # noqa: BLE001 - one ticket must not stop the others
        log_warning("Health check detection ticket failed", health_check_id=check.get("id"), detection=detection_key,
                    error=str(exc)[:300])
        return {**item, "summary": f"No ticket raised: {str(exc)[:300] or exc.__class__.__name__}"}
    if not link.get("script_id"):  # a script run counts once for the link
        await repo.record_run(int(link["id"]), summary)
    log_info("Health check detection ticket", health_check_id=check.get("id"), detection=detection_key,
             ticket_id=ticket["id"])
    return {**item, "ticket_id": int(ticket["id"]), "ticket_label": rmm_actions.ticket_label(ticket), "summary": summary}


__all__ = [
    "HEALTH_VARIABLES",
    "RUN_SOURCE",
    "RemediationError",
    "health_context",
    "resolve",
    "run_for_check",
    "save_link",
    "script_values",
]
