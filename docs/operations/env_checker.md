# Server .env checker and formatter

Super admins can open **Administration → .env Checker / Formatter** at
`/admin/env-checker` and paste the entire server configuration. Other users cannot
open the page or see its menu entry.

The checker runs entirely in browser memory. Pasted contents are never submitted
to the server, written to browser storage, or applied to the running server.
**Clear contents** empties both editors; navigating away also clears them.
Copying to the clipboard or downloading `formatted.env` only happens when an
admin explicitly selects that action.

The server supplies a value-free catalog of variable names from its Settings
schema, literal environment reads in shipped application code, and `.env.example`.
Settings aliases are recognized. Missing variables are reported without adding
values: many are optional, have defaults, or can be configured through module
administration. Unrecognized variables are reported as extra and retained.

Output is grouped using the running server's module state, loaded feature packs,
and deployment availability switches (not switches in the pasted file):

1. Active configuration, grouped by core purpose, module, or feature pack.
2. Disabled modules and feature packs, retaining their assignments unchanged.
3. Stale content, retaining unknown variables, malformed lines, and unattached
   trailing comments for manual review.

Values, comments, multiline quoted values, duplicates, and their relative order
within each group are preserved. Duplicate, malformed, and interpolation-sensitive
assignments generate warnings. `${VARIABLE}` references are not expanded;
reordering may change dotenv expansion, so review those references before using
the formatted file. Custom variables read dynamically or by external tools may
be classified as stale; this tool never deletes them.

Review the output, then copy it or download `formatted.env`. Rename the reviewed
file to `.env` when installing it on the server through your usual deployment
process. The checker does not change module state, save settings, or restart the
server.

Verification:

```sh
node --test tests/test_env_formatter.js
.venv/bin/pytest -q tests/test_admin_env_checker.py
```

The browser check uses system Chromium with intercepted requests and synthetic
configuration. It skips when Chromium is unavailable.
