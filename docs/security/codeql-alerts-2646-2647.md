# CodeQL alerts 2646 and 2647 disposition

## Disposition

Alerts [2646](https://github.com/bradhawkins85/MyPortal/security/code-scanning/2646)
and [2647](https://github.com/bradhawkins85/MyPortal/security/code-scanning/2647), both
`py/path-injection`, are **fixed**. The exclusive-creation and failure-cleanup
sinks now receive a path whose basename is generated entirely by the server.
No part of the multipart filename, its suffix, the company ID, or the
requirement ID is used in the storage path.

## Source-to-sink review

1. The multipart filename is normalized across POSIX and Windows separators,
   reduced to its basename, rejected when empty or a dot segment, sanitized,
   and capped at 255 characters. That value is retained only as display
   metadata.
2. `_allocate_requirement_evidence_storage_path` resolves the configured
   evidence directory and creates a storage basename from a random UUID plus a
   constant `.evidence` suffix. Consequently, request data has no flow to
   either filesystem sink.
3. `_open_requirement_evidence_storage_file` uses `xb`; a collision, including
   a symlink at the candidate name, fails rather than following or replacing
   it, and allocation retries with a new UUID.
4. Cleanup runs only after this request successfully created its candidate and
   only while the candidate remains a direct child of the resolved evidence
   root. It cannot select a supplied filename or another upload's name.
5. The evidence directory and its ancestors are deployment-owned and are not
   writable by portal users. The final directory is explicitly rejected when
   it is a symlink. This is the deployment threat boundary; an operating-system
   actor able to rename deployment-owned directories while the service is
   writing already has equivalent access to all private uploads.

Regression coverage includes absolute paths, both separator styles, dot
segments, unusual and multi-part suffixes, empty names, excessive lengths,
ordinary collisions, symlink collisions, a symlinked evidence directory,
interrupted reads, and the 15 MB limit. It also verifies that collision and
failure handling preserve neighbouring files, metadata, authorization, and
successful upload behavior.

## Analysis evidence

CodeQL CLI 2.27.0 was run on the repaired tree with the `PathInjection.ql`
query from the matching `python-security-extended` query suite. Database
creation processed 1,529 modules and analysis completed without an error. The
SARIF contains **zero** `py/path-injection` results in
`app/api/routes/essential8.py`, so both reported sinks are resolved and no new
finding for this upload flow was introduced. Four pre-existing results remain
elsewhere in the repository (`app/services/call_recordings.py`, `app/main.py`,
`app/core/plugin_loader.py`, and `app/api/routes/tray.py`); they are unrelated
to these alerts or this change. After merge, the repository CodeQL workflow on
`main` remains the authoritative check used to close alerts 2646 and 2647 as
fixed.
