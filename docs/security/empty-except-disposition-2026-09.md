# Empty-except alert disposition (September 2026)

This change reviews the 11 `py/empty-except` alerts reported against commit
`3c0321a1f55e`. The repository CodeQL workflow remains the authoritative rerun
on the repaired default branch; the focused regression tests below cover the
same exception paths before merge.

| Alert | Disposition |
| --- | --- |
| 2399 (`app/api/routes/tickets.py`) | Fixed: malformed Trello company IDs explicitly resolve to no company context; valid IDs still use the repository lookup. |
| 2283 (`app/features/tickets/portal_routes.py`) | Fixed: invalid active-company state explicitly falls back to the user's accessible companies and does not itself grant access. |
| 2282 (`app/main.py`) | Fixed identically to 2283 through the shared company-ID parser. |
| 2665, 2680, 2681 (`app/services/m365.py`) | Fixed: optional Exchange, Purview, and Teams discovery failures continue best-effort repair but emit status/code-only warnings. |
| 2668, 2682 (`app/services/m365.py`) | Fixed: grant failures remain best-effort and observable, with provider message bodies omitted from diagnostics. |
| 1939, 1940, 1941 (`app/services/service_status.py`) | Dismissed by code fix: parse misses are expected while trying direct, fenced, then embedded JSON; explicit comments and state assignments document the quiet fallback. |

Post-merge verification: the `CodeQL` workflow must complete on `main` with no
new `py/empty-except` alerts before these alert records are closed. This file
does not claim a remote default-branch result before the repair is merged.
