# Scheduling and Dispatch

**Schedule** (`/admin/schedule`, helpdesk technicians) books technicians for tickets: onsite visits,
remote sessions, phone calls, meetings or any other booking kind you add.

## The dispatch board

The board shows one row per technician across the working day (07:00–19:00 by default) for the
chosen date, with today's time marked. Each appointment is a block coloured by kind; overlapping
bookings stack so both stay visible, and a red outline means it couldn't be written to the
technician's calendar. Each row shows how much of the day is booked.

- Click an appointment to change it, mark it as done or cancel it.
- Click an empty spot on a technician's row to book them at that time (to the nearest 15 minutes).
- **Waiting to be scheduled** lists open tickets with no upcoming appointment; **Book** opens the
  editor for that ticket and its assigned technician.
- **My schedule** (`/admin/schedule/me`) lists a technician's week, day by day.

Every ticket page also has an **Appointments** card with the ticket's bookings and a **Book
appointment** button. It opens the booking editor on the ticket itself, with the ticket filled in
and its assigned technician chosen, and once the appointment is booked you stay on the ticket.

## Booking kinds

Super admins manage booking kinds from the schedule page's **Actions** menu:

- **Manage booking kinds** lists every kind with its colour, how many appointments use it and which
  one is the default. Edit a kind to rename it or change its colour on the board.
- **+ Add booking kind** adds one.
- **Default booking kind** chooses the kind new bookings start with (also *Make default* in the
  list, or *Start new bookings with this kind* in the editor).

Deleting a kind that appointments already use archives it instead: those appointments keep its
name, but it is no longer offered for new bookings. *Restore* brings it back. The default kind
can't be removed; make another kind the default first.

## Booking

Choose the ticket, technician, kind, date, start time and length, and optionally where and notes
for the technician. Times are in the scheduling time zone. If the technician is already booked
then, MyPortal says with what and asks you to tick **Book anyway** to double-book.

Booking, changing, completing or cancelling an appointment:

- adds an internal note to the ticket;
- fires the **Appointment booked** (`appointments.scheduled`), **Appointment changed**
  (`appointments.updated`) or **Appointment cancelled** (`appointments.cancelled`) automation
  trigger, so an automation can, for example, email the requester. The context has the ticket and
  `appointment.kind_label`, `technician`, `when`, `date`, `start_time`, `end_time`, `timezone`,
  `location` and `notes`.

## Microsoft 365 calendars

Super admins choose, under **Scheduling settings**, the company whose Microsoft 365 connection holds
your technicians' mailboxes (usually your own business). Its app registration needs the
**Calendars.ReadWrite** application permission (grant admin consent after adding it).

Each appointment is then written to the technician's own calendar (found by their MyPortal email
address), moved when it changes (including to another technician's calendar), and removed when
cancelled. Writes happen in the background; the appointment shows *In calendar* or *Calendar sync
failed* with the reason and a **Retry calendar** button, and failed writes are retried every ten
minutes, up to five times.

Settings also set the board's hours, the time zone and the default appointment length.

## API

| Method | Path |
| --- | --- |
| `GET` | `/api/appointments?start=&end=&technician_id=` (UTC, up to 93 days) |
| `GET` | `/api/appointments/{id}` |
| `GET` | `/api/tickets/{ticket_id}/appointments` |
| `POST` | `/api/appointments` (409 when the technician is already booked, unless `book_anyway`) |
| `PUT` | `/api/appointments/{id}` |
| `POST` | `/api/appointments/{id}/cancel` |
| `GET` | `/api/scheduling/settings` (super admins) |
| `GET` | `/api/scheduling/kinds` |
| `POST` | `/api/scheduling/kinds` (super admins) |
| `PUT` | `/api/scheduling/kinds/{id}` (super admins) |
| `DELETE` | `/api/scheduling/kinds/{id}` (super admins; archives a kind that is in use) |

Disable the feature with `DISABLED_FEATURE_PACKS=scheduling`.
