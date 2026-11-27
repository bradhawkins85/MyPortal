# Service Agreements

**Agreements** (`/admin/agreements`) records what each client's contract covers and shows how much
of it their ticket time has used. Helpdesk technicians can view agreements; super admins add and
edit them. Agreements only track coverage: invoicing is unchanged.

## Kinds of agreement

- **Managed (unlimited)**: all support time is covered.
- **Hours each period**: a number of hours every month, quarter or year. Months and quarters
  follow the calendar; years run from the start date. Unused hours don't roll over.
- **Prepaid block**: a block of hours used up over the agreement until it runs out.

Each agreement has a start date and an optional end date. You can limit it to some **labour types**
(for example *Remote support* only); with none ticked it covers every labour type. A **fee** is
optional and is only used to work out the effective hourly rate.

## How time is counted

Every ticket time entry (a reply with time spent) on the company's tickets counts towards the
agreement that covers it, on the date it was logged (UTC):

1. Only agreements that are active on that date, not cancelled, and cover the entry's labour type
   count.
2. Unlimited agreements cover time first, then hours each period, then prepaid blocks, so block
   hours are only used when nothing else covers the work.
3. When an agreement's hours are used up, the time moves to the next agreement that covers it. If
   none is left, it counts as **overage** on the last one.

## Reading the page

Agreements are grouped by company. Each card shows its kind, period and labour types, and either
the hours covered this period (managed) or a meter of hours used, hours left and any overage.
Cards turn amber at the alert level and red when the hours are used up or exceeded.

Open an agreement to see the hours used, left and over, the value of the covered work (hours ×
labour type rates), the effective hourly rate (fee ÷ covered hours), its last 12 periods and the
time entries it counted.

## Alerts and automations

Once an hour MyPortal checks agreements and fires these automation triggers, each once per period:

| Trigger | When |
| --- | --- |
| **Service agreement usage reached its alert level** (`agreements.usage_threshold`) | The period's usage reaches the agreement's alert percentage (80% by default). |
| **Service agreement hours used up** (`agreements.exhausted`) | Usage reaches 100%. |
| **Service agreement ends within 30 days** (`agreements.expiring`) | The end date is 30 days away or less. |

The automation context has `agreement.name`, `company_name`, `type`, `used`, `allowance`,
`remaining`, `overage`, `percent`, `end_date` and `url`.

## API

| Method | Path | Who |
| --- | --- | --- |
| `GET` | `/api/agreements?company_id=` | Helpdesk technicians |
| `GET` | `/api/agreements/{id}` (with the last 12 periods) | Helpdesk technicians |
| `POST` | `/api/agreements` | Super admins |
| `PUT`/`DELETE` | `/api/agreements/{id}` | Super admins |

Disable the feature with `DISABLED_FEATURE_PACKS=agreements`; agreements stay in the database.
