"""RMM feature pack: scripts loaded from Gitea and run on devices by the RMM agent.

* ``GET /rmm/scripts`` script library, devices with an RMM agent and run history
* ``/api/rmm/...`` technician APIs for script fields, pushing runs and results
* ``/api/rmm/agent/...`` the API the separate RMM agent uses to enrol, collect
  runs and report exit codes, output and custom values
* ``GET /rmm/automation`` scheduled scripts and the onboarding scripts run
  when a device's RMM agent first enrols (:mod:`app.services.rmm_automation`)
* ``GET /rmm/remote-control`` RustDesk and MeshCentral, switched on per session
  by an activation script (:mod:`app.services.rmm_remote_control`)
* ``GET /rmm/event-logs`` Windows event logs agents send to the separate event
  log database, and the extra logs collected per company and device
  (:mod:`app.services.rmm_event_logs`)
* ``GET /rmm/event-logs/rules`` rules that drop incoming event log entries or
  run an RMM script on the device that sent them
  (:mod:`app.services.rmm_event_log_rules`)
* ``GET /rmm/scripts/result-rules`` rules that read a script's output, exit
  code and returned values when it finishes, then raise a ticket or run
  another script on the device (:mod:`app.services.rmm_script_result_rules`)
* ``GET /rmm/checks`` checks for disk space, ping, CPU, memory, services,
  network throughput, script results and agents that stop checking in, with
  the same actions as event log rules; the agent reports what they need and
  device stats go to the separate stats database (:mod:`app.services.rmm_checks`)
* each asset page's Hardware & software section: the hardware and installed
  software the RMM agent reports daily, on start and when asked to refresh,
  ready for filtering which devices scripts run on
  (:mod:`app.services.rmm_inventory`)
* each asset page's Windows updates section: the updates a Windows device
  still needs and its installation history, from a built-in scan run daily
  and on request, with a button to install the chosen updates
  (:mod:`app.services.rmm_windows_updates`)
* each asset page's Printers section: the printers installed on a Windows
  device (Get-Printer plus signed-in users' connections), from a built-in scan
  run daily and on request, kept with first and last seen dates
  (:mod:`app.services.rmm_printers`)
* ``POST /api/rmm/assets/{id}/agent-install`` push the RMM agent to a device
  through its tray app, and the tray app's download and result APIs
  (:mod:`app.services.rmm_agent_deploy`)
* ``GET /rmm/maintenance`` maintenance windows and snoozes: while one covers a
  device its checks hold their actions and event log rules raise no tickets
  (:mod:`app.services.rmm_maintenance`)
* ``GET /rmm/vulnerabilities`` known CVEs (NVD, with CISA's actively exploited
  list) in the software the company's devices report, worst first
  (:mod:`app.services.vulnerabilities`); super admins sync and map software
  at ``/rmm/vulnerabilities/settings``
* ``GET /rmm/findings`` (super admins) a platform-wide, best-guess look at
  every company's RMM data for abnormalities: event spikes, lockouts, new
  accounts and services, software changes and slow devices, from fixed rules
  and the configured language model (:mod:`app.services.rmm_findings`)

A background job keeps the Gitea repository's ``Common/`` and
``Companies/<company>/`` folders in place, adding a folder for each new
company, and another runs due schedules and moves onboarding sequences on
and runs server-side checks (each on one instance at a time; see
:mod:`app.services.singleton_jobs`).

Disable it with ``DISABLED_FEATURE_PACKS=rmm``: the menu entry, the asset
page's script card and every route above go away. Scripts, agents, schedules,
onboarding steps, remote control settings and run history stay in the database.
"""

from __future__ import annotations

from app.core.features import FeaturePack
from app.repositories import rmm as rmm_repo

from app.services import (rmm_automation, rmm_checks, rmm_event_log_rules, rmm_findings, rmm_live, rmm_maintenance,
                          rmm_scripts,
                          rmm_patching, rmm_printers, rmm_software_updates, rmm_ticket_scripts,
                          rmm_windows_updates, vulnerabilities)

from .agent_deploy_routes import router as agent_deploy_router
from .automation_routes import router as automation_router
from .check_routes import router as check_router
from .event_log_routes import router as event_log_router
from .event_log_rule_routes import router as event_log_rule_router
from .finding_routes import router as finding_router
from .inventory_routes import router as inventory_router
from .maintenance_routes import router as maintenance_router
from .vulnerability_routes import router as vulnerability_router
from .remote_control_routes import router as remote_control_router
from .result_rule_routes import router as result_rule_router
from .patch_routes import router as patch_router
from .printer_routes import router as printer_router
from .windows_update_routes import router as windows_update_router
from .routes import router


async def _follow_up_server_finished_run(run_id: int) -> None:
    run = await rmm_repo.get_run(run_id)
    if run and run.get("run_source") == "ticket":
        await rmm_ticket_scripts.record_result(
            run_id, status=str(run["status"]), exit_code=None, stdout=str(run.get("stdout") or ""),
            stderr=str(run.get("stderr") or ""), error=run.get("error_message"),
        )
    await rmm_automation.run_finished(run_id)
    await rmm_checks.run_finished(run_id)


rmm_scripts.register_server_finish_hook(_follow_up_server_finished_run)

# Event log rules that schedule a script are queued on the automation tick,
# as are checks judged on the server (agent offline, script results) and
# check actions scheduled for later.
rmm_automation.register_tick_job(rmm_event_log_rules.run_due_scheduled)
rmm_automation.register_tick_job(rmm_checks.tick)
# Snoozes that ended a while ago are tidied away.
rmm_automation.register_tick_job(rmm_maintenance.tick)
rmm_automation.register_tick_job(rmm_patching.tick)
# Connected Windows devices scan for Windows updates once a day.
rmm_automation.register_tick_job(rmm_windows_updates.tick)
# Connected Windows devices report their installed printers once a day.
rmm_automation.register_tick_job(rmm_printers.tick)
# CVE downloads and device matching start on the tick once a day (or when asked)
# and run in the background.
rmm_automation.register_tick_job(vulnerabilities.tick)
# Findings analyses start on the tick when one is due and run in the background.
rmm_automation.register_tick_job(rmm_findings.tick)
# Ticket automation runs that expire or are cancelled are posted to their ticket.
rmm_automation.register_tick_job(rmm_ticket_scripts.tick)
# Live tools sessions are offered to agents through the job poll, and expired
# sessions and stale relay frames are swept on the automation tick.
rmm_scripts.register_poll_provider(
    "live", rmm_live.sessions_for_agent,
    watch=rmm_live.watch_agent_for_offers, unwatch=rmm_live.unwatch_agent_for_offers,
)
rmm_automation.register_tick_job(rmm_live.sweep)
# Onboarding steps can install Chocolatey packages through the patch job.
rmm_automation.register_package_steps(validate=rmm_software_updates.package_ids, queue=rmm_software_updates.queue_apps)

PACK = FeaturePack(
    slug="rmm",
    version="1.35.0",
    routers=(router, automation_router, remote_control_router, agent_deploy_router, event_log_router,
             event_log_rule_router, inventory_router, check_router, finding_router, patch_router,
             windows_update_router, result_rule_router, printer_router, maintenance_router,
             vulnerability_router),
    background_jobs=(rmm_scripts.folder_maintenance_loop, rmm_automation.automation_loop),
)

__all__ = ["PACK"]
