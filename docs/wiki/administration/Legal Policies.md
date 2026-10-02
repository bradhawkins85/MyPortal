# Legal Policies

MyPortal publishes three policies at `/legal`:

| Page | Path |
| --- | --- |
| Privacy Policy | `/legal/privacy` |
| Acceptable Use Policy | `/legal/acceptable-use` |
| Terms and Conditions | `/legal/terms` |

They are public (no sign-in needed), linked under the sign-in, registration and
password reset cards, listed in the signed-out sidebar, and reachable by
signed-in users from the document icon in the sidebar footer.

Every policy page ends with a **No warranty** notice: the portal is provided
"as is", without warranty of any kind, and no liability lies with the
developers or contributors of the MyPortal software. Section 10 of the Terms
and Conditions sets this out in full.

## Operator details

| Variable | Purpose | Fallback |
| --- | --- | --- |
| `LEGAL_ENTITY_NAME` | Organisation operating the portal, named in each policy. | `APP_NAME` |
| `LEGAL_CONTACT_EMAIL` | Address for privacy and policy enquiries. | `SMTP_FROM`, otherwise users are asked to raise a ticket |

## Customising the wording

The text lives in `app/templates/legal/_privacy.html`,
`_acceptable_use.html` and `_terms.html`, and describes the information
MyPortal collects and the features it offers. It is written with the
Australian Privacy Principles and the GDPR in mind but is a starting point,
not legal advice: review it with your own adviser and adjust it for the
features you enable. Update `LEGAL_POLICIES_UPDATED` in `app/main.py` when the
wording changes.
