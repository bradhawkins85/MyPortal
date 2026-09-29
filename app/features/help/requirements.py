"""Which help articles document which optional features.

Help articles that describe an optional feature pack or module are hidden when
that feature is not active on this deployment, so users only see guidance for
features they can actually use.  Articles and sections that are not listed
here are always shown.

Requirements are written as ``"pack:<slug>"`` (a feature pack or core
component slug, see ``DISABLED_FEATURE_PACKS``) or ``"module:<slug>"`` (an
integration module slug, see ``DISABLED_MODULES``).  A pack is active while the
deployment has not disabled it; a module is active while it is available and
switched on under Modules.

When several requirements are listed, the article is shown if **any** of them
is active.  An article inside a restricted section must satisfy both the
section's and its own requirements.
"""

from __future__ import annotations

# Keyed by the ``docs/wiki`` subfolder name.
SECTION_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "business-continuity": ("pack:continuity",),
    "m365": ("pack:office365",),
    "tickets": ("pack:tickets",),
}

# Keyed by ``"<section folder>/<file name without extension>"``.
ARTICLE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    # Administration
    "administration/API Keys": ("pack:api_keys",),
    "administration/Agent Setup": ("module:ollama",),
    "administration/Asset Custom Fields": ("pack:assets",),
    "administration/Automation Variables": ("pack:automations",),
    "administration/Cart CSP Fix": ("pack:cart",),
    "administration/Email Template Variables": ("pack:automations",),
    "administration/Knowledge Base Conditional Logic": ("pack:knowledge_base",),
    "administration/Message Templates": ("pack:message_templates",),
    "administration/Network Map": ("pack:network_map",),
    "administration/RAG Relationship Candidate Selection": ("pack:rag_index",),
    "administration/Service Status Dashboard": ("pack:service_status",),
    "administration/Shop Category Nesting": ("pack:shop",),
    "administration/Shop Packages": ("pack:shop",),
    "administration/Subscription Co-terming": ("pack:subscriptions",),
    "administration/Webhook Monitor": ("pack:webhooks",),
    # API reference
    "api-reference/Companies API": ("pack:companies",),
    "api-reference/Issues API": ("pack:issue_tracker",),
    "api-reference/Orders API": ("pack:orders",),
    "api-reference/Staff Onboarding Requests API": ("pack:staff",),
    # Compliance
    "compliance/Essential 8 Requirements": ("pack:essential8",),
    "compliance/SMB1001": ("pack:smb1001",),
    # Getting started
    "getting-started/Tray App": ("pack:tray",),
    # Integrations
    "integrations/ChatGPT MCP": ("module:chatgpt-mcp",),
    "integrations/Matrix Chat": ("pack:chat",),
    "integrations/OpnForm": ("pack:forms",),
    "integrations/SMTP Relay": ("module:smtp",),
    "integrations/SMTP2Go Integration": ("module:smtp2go",),
    "integrations/Solidtime Integration": ("module:solidtime",),
    "integrations/Tactical RMM Ticket Webhook": ("module:tacticalrmm",),
    "integrations/Tactical RMM Tray Agent Sync": ("module:tacticalrmm",),
    "integrations/Third-Party Staff Polling": ("pack:staff",),
    "integrations/Uptime Kuma": ("module:uptimekuma",),
    "integrations/Xero Auto Send": ("module:xero",),
    "integrations/Xero Billable Tickets": ("module:xero",),
    "integrations/Xero Integration": ("module:xero",),
    "integrations/Xero Labour Rates Resolution": ("module:xero",),
    "integrations/Xero Labour Type Rates": ("module:xero",),
    "integrations/Xero OAuth Setup": ("module:xero",),
    "integrations/Xero OAuth": ("module:xero",),
    "integrations/Xero Tenant Selection": ("module:xero",),
    # Tickets
    "tickets/IMAP Filters": ("module:imap",),
    "tickets/IMAP Setup": ("module:imap",),
    "tickets/Transcription Setup": ("module:call-recordings",),
}
