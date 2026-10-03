"""Legal policy definitions shared across the application.

Moved out of ``app.main`` so that the API layer (and other modules) can
import the policy metadata without pulling in the full application factory.
"""

from __future__ import annotations

#: Human-readable policy catalogue keyed by URL slug.  The keys match the
#: routes served under ``/legal/<slug>``.
LEGAL_POLICIES: dict[str, dict[str, str]] = {
    "privacy": {
        "title": "Privacy Policy",
        "summary": "What information the portal collects, why, who it is shared with and your rights.",
        "template": "legal/_privacy.html",
    },
    "acceptable-use": {
        "title": "Acceptable Use Policy",
        "summary": "What you may and may not do when using the portal and its connected services.",
        "template": "legal/_acceptable_use.html",
    },
    "terms": {
        "title": "Terms and Conditions",
        "summary": "The agreement that applies when you create an account and use the portal.",
        "template": "legal/_terms.html",
    },
}

#: ISO date string that changes whenever any legal policy text is updated.
#: Stored on each user's ``policies_accepted_version`` at registration so
#: the system can detect users who need to re-accept revised policies.
LEGAL_POLICIES_UPDATED = "2026-10-03"

#: Short human-readable summary of the most recent policy changes, shown in
#: the re-acceptance banner and on the ``/legal`` overview page.  Leave empty
#: when there is no recent update to highlight.
LEGAL_POLICIES_CHANGE_SUMMARY: str = (
    "You can now ask us to delete or anonymise your account from your profile. The Privacy "
    "Policy and Terms and Conditions explain the request form and which records we keep, with "
    "your personal details removed, where the law requires us to."
)
