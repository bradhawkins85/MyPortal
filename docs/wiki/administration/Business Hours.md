# Business Hours

Business hours describe when your organisation and each customer are open.
They drive automation timing, SLA timers, and the open/closed indicator on
tickets.

## Schedules

- **Global schedule** – *Tickets → Actions → Business hours*
  (`/admin/business-hours`). Your organisation's hours, time zone, and
  closures such as public holidays.
- **Company schedule** – the *Business hours* card on a company's edit page.
  Choose *Use the global business hours* (default) or *Use custom hours* with
  the company's own time zone, hours, and closures. Custom schedules include
  global closures unless you clear *Also close on global closures and public
  holidays*.

Each weekday can have up to two open periods (for example a lunch closure).
A closing time of `00:00` means midnight at the end of the day.

If no global schedule has been saved, every moment counts as open, so
existing automations and SLA timers keep working exactly as before.

## Automations

Event and scheduled automations have a **Business hours** setting:

| Option | Event automations | Scheduled automations |
| --- | --- | --- |
| Run at any time | Runs immediately (default). | Runs on schedule (default). |
| Pause until business hours | Stores the event and runs it when the schedule next opens. | Not available. |
| Skip | Does not run and records a *skipped* history entry. | Skips the run (or, for ticket scans, each matched ticket whose company is closed). |

*Use the business hours of* selects either **the ticket's company** (falling
back to the global schedule) or **our business** (the global schedule). Use
company hours to avoid texting customers when they are closed, and global
hours to hold actions while your team is away.

Paused runs are held in `automation_deferred_runs` and processed by the
automation scheduler. When a paused run becomes due MyPortal re-checks the
automation: inactive or deleted automations are cancelled, and if the hours
have changed the run waits for the new opening time. Pending runs are listed
on the ticket under *Ticket details*.

Filters and templates can also read `business_hours.company_open`,
`business_hours.global_open`, and the fuller `business_hours.company` /
`business_hours.global` objects. The context is added only to automations
whose filters or actions reference `business_hours`.

The REST API accepts `business_hours_mode` (`pause` or `skip`) and
`business_hours_source` (`company` or `global`) on automation create and
update requests.

## SLA timers

Enable *Only count time during business hours* on an SLA template to pause
response and resolution timers while the ticket's company is closed. Due
times, at-risk warnings, and breach events then use business time. Tickets
show *Timer paused (outside business hours)* while the customer is closed.

## Ticket open/closed indicator

The *Ticket details* card shows **Customer open** or **Customer closed** for
the linked company. Hover to see the customer's local time and when they
next open or close.
