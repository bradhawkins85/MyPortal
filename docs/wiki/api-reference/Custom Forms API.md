# Custom Forms API

Custom forms collect support requests. Administrators manage forms at
`/admin/custom-forms`; signed-in users see available forms at `/forms`.
Inactive forms cannot be submitted. An empty access list allows all signed-in
users; a nonempty list restricts access to those users and super admins.

## Administration

All endpoints below require a super admin. Authenticated writes require CSRF
protection under the portal's existing session policy.

| Method | Path | Purpose |
| --- | --- | --- |
| GET, POST | `/api/forms` | List or create forms |
| GET, PATCH, DELETE | `/api/forms/{form_id}` | Read, update or delete a form |
| POST | `/api/forms/{form_id}/fields` | Add a field |
| PATCH, DELETE | `/api/forms/{form_id}/fields/{field_id}` | Update or delete a field |
| PUT | `/api/forms/{form_id}/fields/reorder` | Set the complete field order |
| GET, PUT | `/api/forms/{form_id}/access` | Read or replace the access list |
| GET | `/api/forms/{form_id}/submissions` | List submissions |

Reordering accepts `{"field_ids": [11, 10, 12]}` and requires every existing
field exactly once. Access updates accept `{"user_ids": [4, 9]}`.

PATCH requests preserve omitted properties. Explicit `null` clears the form
`description` or a field's `placeholder`, `help_text`, or `default_value`.
Choice fields (`select`, `radio`, `multiselect`) require at least one option;
options have a maximum of 100 entries, each up to 255 characters.

The admin page uses searchable lists and focused modal editors with live previews.
Field choices can be entered individually, reordered, or pasted one per line.
Save failures keep the editor open and retain the draft. Browser navigation and
closing a changed editor warn about unsaved changes.

`POST /admin/custom-forms/save` is the regular HTML form fallback, excluded
from the JSON API schema. It accepts `action` and the relevant `form_id`,
`field_id`, or `user_id`, plus the form fields. Supported actions are
`create_form`, `update_form`, `delete_form`, `save_field`, `delete_field`,
`move_field`, `add_access`, and `remove_access`. It applies the same admin,
record ownership, and schema checks as the API and redirects with a flash message.

## Submission

`POST /forms/{slug}/submit` accepts multipart form data. The signed-in user
must have access, and the form must be active.

JavaScript clients send a `form_data` JSON object keyed by field ID and uploads
under `file_{field_id}`. Multiple choices are arrays; checkboxes are booleans.
Normal HTML forms send answers as `field_{field_id}`, repeating that name for
multiple choices, and use the same upload names. Hidden fields are filled from
the stored default on the server. Empty upload placeholders are ignored.

JSON clients receive HTTP 201 with `ticket_id`, `submission_id`, and
`attachments`. Validation failures return HTTP 400 with `detail.errors`, a list
of messages. HTML clients receive a redirect to the created ticket, or an HTTP
400 page with validation messages and their entered answers retained. File
inputs must be selected again after a failed HTML submission.

## Extended field types

The editor retains the field types available on the current main branch, including
time, URL, password, rating, matrix, signature, and multiple audio clips. Matrix
configuration uses `config.rows` and `config.columns` (each label unique) plus
`config.mode`: `"row"` (one answer per row, the default for older fields) or
`"table"` (one answer for the whole table); ratings use `config.max`
(1–10); audio uses `config.max_clips` (1–5). The editor previews these types and
retains the recent submissions view.

Matrix questions render as a table with the columns across the top and a radio
button in every cell. Answers are submitted and stored as `{row label: column
label}`. With one answer per row, a required matrix needs every row answered and
the ticket lists each row with its chosen column. With one answer per table, at
most one row is accepted and the ticket shows that row and its column.

HTML matrix answers use `matrix_{field_id}_{row_index}` (the column label) for one
answer per row, or a single `matrix_{field_id}` of `"{row_index}:{column_index}"`
for one answer per table; the server maps both to the configured labels and
rejects a cell outside the table with a 400. Signatures can be drawn with a pointer or uploaded as
a PNG/JPEG image (up to 1 MB) under `signature_{field_id}`; uploaded images are
converted to the existing data URL format before submission validation. Audio
uploads repeat `file_{field_id}` for each clip.

Audio fields also offer direct microphone recording in supported browsers over
HTTPS, including mobile browsers. Users grant microphone permission, record and
stop a clip, then listen back or remove it before submitting. Recordings and
uploaded files share the configured clip limit and the 50 MB per-file limit.
Recordings use a browser-supported audio format (for example, MP4/M4A or WebM)
and are submitted through the same `file_{field_id}` attachment flow. Submission
waits until recording has stopped; failed JavaScript submissions retain the clips.
Resetting the form or leaving the page releases the microphone and local previews.
Uploads remain available when recording is unsupported or permission is denied,
and without JavaScript. Microphone permission and blob media playback are enabled
only on `/forms/{slug}` and `/public/forms/{slug}` pages by the portal's security headers.

File fields offer **Take photo** and **Record video** controls using native mobile
camera capture (`capture="environment"` with image/video file inputs). Devices
that support capture open their camera; other browsers may show a file picker.
Captured media uses the same `file_{field_id}` attachment flow and 50 MB limit.
Users can review or remove the attachment, and choosing another file or capture
replaces it. Cancelling capture keeps the existing attachment. Normal file uploads
remain available, including without JavaScript. HEIC/HEIF photos and MP4/QuickTime
videos are accepted; HEIC/HEIF photos are downloadable even when a browser cannot
preview them. Native camera capture does not require enabling in-page camera
streaming in the portal's Permissions-Policy.


## Public magic links

Open a form in `/admin/custom-forms` and choose **Share via magic link**.
Select the company that should receive tickets, then choose a future expiry
(date and time in UTC) or **Never expires**. Copy the generated URL immediately:
raw links are shown once, and only a SHA-256 token digest is stored. The sharing
page lists links and lets super admins revoke them, including links that never
expire. Creating another link does not invalidate existing links.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/forms/{form_id}/share-links` | Create a link |
| GET | `/api/forms/{form_id}/share-links` | List link metadata (no tokens) |
| DELETE | `/api/forms/{form_id}/share-links/{link_id}` | Revoke a link belonging to the form |

Creation accepts `{"company_id": 7, "expires_at": "2027-01-01T00:00:00Z"}`.
`expires_at` must include a timezone and be in the future. Omit it or send `null`
for a link that never expires. HTTP 201 returns link metadata and `url`.
These management endpoints require a super admin and the normal CSRF policy.

`GET /public/forms/{slug}?token=...` displays a standalone form without login.
POST to that same URL accepts the same field and upload formats as authenticated
submissions. Link access is independent of the form's portal user access list.
Invalid, expired, revoked, mismatched, and inactive-form links return HTTP 404
on both GET and POST. Expiry is checked again when submitting.

Public submissions create tickets for the company stored on the link, with no
requester or authenticated submitter identity. Browser cookies and submitted
company IDs do not change this routing. Attachments remain private. JSON success
returns only `{"status": "ok", "message": "Your submission was received."}`;
HTML success displays a confirmation. Ticket IDs, attachment metadata, and portal
navigation are not exposed to public recipients. Validation errors retain HTML
answers as with signed-in forms. These capability endpoints do not use session
identity and are exempt from session CSRF checks; the bearer token is required
for every read and submission. Public pages disable caching, indexing, and
referrer sharing. The link can be reused until expiry or revocation.

Deployments must apply migration `492_custom_form_share_links.sql` before using
this feature.
