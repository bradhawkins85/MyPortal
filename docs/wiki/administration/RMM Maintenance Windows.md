# RMM Maintenance Windows

**RMM > Maintenance** (`/rmm/maintenance`) stops planned work from flooding the helpdesk. While a
maintenance window covers a device:

- its [checks](RMM%20Checks.md) keep running and recording results, so the asset and checks pages
  still show the real state;
- the checks' actions are held: no ticket, script, scheduled script or Chocolatey install;
- [event log rules](RMM%20Event%20Logs.md) raise no tickets for it.

If a check is still failing when the window ends, its actions run on its next result, so nothing
that stays broken is missed. Recoveries during a window are still noted on open tickets.

## Adding a window

Select **+ Add maintenance window** and fill in:

- **When**: *One-off* (a start and end) or *Every week* (days, a start time and how long it lasts;
  it may run past midnight, e.g. 22:00 for 240 minutes). Times are in the chosen **time zone**, and
  weekly windows follow daylight saving.
- **Covers**: all of the company's devices, one device, or (super admins) every company's devices.
- **Checks**: all checks, or one check, for example *Agent offline* during reboots.
- **Reason** (optional) and **Window is on**.

The summary on the right reads the window back in plain English as you type.

The page lists windows **In maintenance now**, **Upcoming**, **Recurring** and **Ended or off**.
**End now** ends a one-off window (**Cancel** before it starts); **Switch off** stops a weekly one.

## Snoozing from the asset page

The asset page's **Checks** section shows a banner while the device is in maintenance, with
**End snooze**/**End window** for one-off windows. **Snooze alerts from** [All checks or one check]
**for** [1 hour to 3 days] creates a one-off window for just that device. Snoozes that ended more
than 30 days ago are removed automatically.

## API

| Method | Path |
| --- | --- |
| `GET`/`POST` | `/api/rmm/maintenance-windows` |
| `GET`/`PUT`/`DELETE` | `/api/rmm/maintenance-windows/{id}` |
| `GET` | `/api/rmm/assets/{asset_id}/maintenance` (is the device in maintenance now?) |

Access follows the RMM scripts permission (`menu.rmm_scripts`); changing windows needs write
access, and windows for every company need a super admin. Every change is audited.
