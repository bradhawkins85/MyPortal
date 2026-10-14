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
configuration uses `config.rows` and `config.columns`; ratings use `config.max`
(1–10); audio uses `config.max_clips` (1–5). The editor previews these types and
retains the recent submissions view.

HTML matrix answers use `matrix_{field_id}_{row_index}`, which the server maps to
the configured row labels. Signatures can be drawn with a pointer or uploaded as
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
only on `/forms/{slug}` pages by the portal's security headers.
