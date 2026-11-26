# Projects

**Projects** (`/admin/projects`, helpdesk technicians) plans larger jobs, such as an office move,
server replacement or Microsoft 365 migration, as phases of task tickets with a budget.

## Creating a project

Select **+ New project** and choose the company, name, status, project manager, start and due
dates, and how it's billed:

- **Time and materials**: time logged on the project's tickets is invoiced as usual. The optional
  **Budget** amount is compared with the value of the time logged.
- **Fixed fee**: enter the fee. Time logged on the project's tickets is **kept off ticket
  invoices** (both the local invoice run and the Xero ticket sync skip it; ticket expenses are
  still invoiced). Bill the fee with milestones instead.

The optional **Hours budget** is what the time logged is compared with; leave it blank to compare
with the sum of the tasks' estimates.

## Phases and tasks

On the project's page, **+ Add phase** adds a phase (such as Planning, Rollout, Handover) with an
optional due date; move phases up and down, rename or delete them (their tasks are kept).

**+ Add task** either creates a **new ticket** for the project's company (with an optional
assignee) or adds an **existing ticket** by its ID. A ticket can only be in one project and must be
for the project's company. Give each task a phase and an optional estimate. Tasks are ordinary
tickets, so time entries, assignment, SLAs, appointments and automations work as usual.

The page shows each phase's tasks with their status, assignee and time logged against the estimate
(**Over** when past it). **Change** moves a task to another phase, changes its estimate or removes
it from the project (the ticket is kept).

## Progress and money

- **Progress** is the share of task tickets that are resolved or closed.
- **Hours used** is the time logged on the tasks against the hours budget (or estimates); projects
  turn amber at 85% and red when over budget, and are flagged when overdue.
- **Value of time logged** is the hours × each labour type's rate.
- **Margin on fee** (fixed fee) is the fee less the value of time logged; **Budget left** (time and
  materials) is the budget less the value of time logged.

## Milestones

Milestones split a fixed fee into payments, such as a 30% deposit and the rest at go-live, each
with an amount, optional due date and phase. The section shows how much of the fee is planned and
invoiced. A super admin selects **Create invoice** to create a **draft invoice** for the company
with one line for the milestone; review and send it from **Invoices** as usual. A milestone can
only be invoiced once, and invoiced milestones can't be changed or deleted.

## API

| Method | Path | Who |
| --- | --- | --- |
| `GET` | `/api/projects?company_id=` | Helpdesk technicians |
| `GET` | `/api/projects/{id}` (phases, tasks, milestones, financials) | Helpdesk technicians |
| `POST`/`PUT` | `/api/projects`, `/api/projects/{id}` | Helpdesk technicians |
| `DELETE` | `/api/projects/{id}` | Super admins |
| `POST` | `/api/projects/{id}/tasks` (new or existing ticket) | Helpdesk technicians |
| `POST` | `/api/projects/{id}/milestones/{milestone_id}/invoice` | Super admins |

Disable the feature with `DISABLED_FEATURE_PACKS=projects`; projects stay in the database, their
tickets are untouched and fixed-fee time is still kept off ticket invoices.
