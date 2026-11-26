# Customer Satisfaction Surveys

**Satisfaction** (`/admin/csat`, helpdesk technicians and super admins) shows how happy
requesters are with the support they received. Once a ticket is resolved or closed, MyPortal
emails its requester a one-question survey: *How satisfied were you with our support?* on a
scale from 1 (very dissatisfied) to 5 (very satisfied), with an optional comment.

## Turning surveys on

Surveys are off until a super admin turns them on under **Survey settings** at the bottom of the
Satisfaction page:

- **Wait before sending** (minutes, default 60) gives the requester time to reopen the ticket
  before being asked to rate it.
- **Link works for** (days, default 14). After this the survey closes.
- **Follow up ratings of** (default *2 or lower*) puts low ratings on the *Low scores to follow
  up* list until someone selects **Mark done**. Choose *Never* to turn follow-ups off.
- **Don't survey these companies** stops surveys for the companies you tick.

Only tickets resolved after surveys were turned on are surveyed, and never tickets resolved more
than seven days ago, so turning surveys on does not email a backlog. Each ticket gets at most one
survey, even if it is reopened and resolved again. Tickets with no requester email are skipped.

## What the requester sees

The email has a link for each rating. A link opens a page with that rating already picked, where
the requester can change it, add a comment and select **Send feedback**. Opening a link does not
record a rating on its own, so email security scanners that follow links can't answer the survey.
Only a hash of each link's token is stored.

The survey email comes from two message templates, *Ticket satisfaction survey* and *Ticket
satisfaction survey subject*, under **Message Templates** (**Edit survey email** on the
Satisfaction page). Available variables: `{{ recipient.first_name }}`, `{{ ticket.number }}`,
`{{ ticket.subject }}`, `{{ technician.name }}`, `{{ survey.link }}`, `{{ survey.link_1 }}` to
`{{ survey.link_5 }}`, `{{ survey.expires }}` and `{{ app.name }}`.

## Reading the results

- **CSAT score** is the share of responses rated 4 or 5.
- **Response rate** is responses as a share of surveys sent.
- **By technician** scores each response against the technician assigned when the ticket was
  resolved; **By company** links to that company's own view.
- Pick a period (30 days, 90 days or 12 months) and a company in the page header.

Every response is also posted to its ticket as an internal note.

## Automations

A response fires the **Ticket satisfaction survey answered** (`tickets.csat_received`) automation
trigger. Alongside the usual `ticket` context it carries `csat.rating`, `csat.label`,
`csat.comment`, `csat.is_low` and `csat.is_satisfied`, so you can, for example, notify a team lead
or reopen the ticket when `csat.is_low` is true.

## API

| Method | Path | Who |
| --- | --- | --- |
| `GET` | `/api/csat/summary?period=90&company_id=` | Helpdesk technicians |
| `GET` | `/api/csat/responses?period=90&company_id=&follow_up_only=false` | Helpdesk technicians |
| `GET` | `/api/csat/tickets/{ticket_id}` | Helpdesk technicians |
| `POST` | `/api/csat/responses/{survey_id}/follow-up` | Helpdesk technicians |
| `GET`/`PUT` | `/api/csat/settings` | Super admins |

## Turning the feature off

Set `DISABLED_FEATURE_PACKS=csat`. No surveys are sent and the page and APIs go away; settings,
surveys and responses stay in the database.
