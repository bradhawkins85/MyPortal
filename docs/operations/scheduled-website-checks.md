# Scheduled website and DNS checks

MyPortal exposes two Scheduled Tasks commands:

* **Refresh website checks** queues the enabled availability, TLS, and domain
  registration observations for each eligible website.
* **Refresh DNS records** queues DNS collection and change detection only. It
  does not perform an HTTP request, TLS handshake, or registration lookup.

A task with no company is the default for all companies. Creating a task with
a company overrides the default for that company and command. The override is
intentional even when the company task is disabled, so pausing an override does
not unexpectedly fall back to the global cadence.

## Transition from the legacy interval

The lease worker continues to enqueue website checks at
`WEBSITE_CHECK_INTERVAL_SECONDS` for companies without a website Scheduled
Task. As soon as an all-company or company-specific **Refresh website checks**
task exists, interval enqueueing is suppressed for its scope. Do not create a
second external schedule during migration. Create and verify the Scheduled
Task first, inspect its run history, and then tune or disable it as required.

Scheduled, automatic, and manual requests share the typed leased queue. A
pending or running job of the same type for the same website is reused, and a
per-run idempotency key prevents two application instances from inserting the
same due-window job. Batch size and per-company concurrency remain controlled
by `WEBSITE_CHECK_BATCH_SIZE` and `WEBSITE_CHECK_COMPANY_CONCURRENCY`.

Task run details report scope plus queued, checked, changed, skipped, and failed
counts. `checked` and `changed` are zero at enqueue time; the website job list
and DNS history contain the independently retried observation results.
