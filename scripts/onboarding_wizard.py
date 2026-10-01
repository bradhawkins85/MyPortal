#!/usr/bin/env python3
"""MyPortal onboarding wizard.

An interactive console wizard that walks an administrator through the initial
setup of a MyPortal deployment and writes the answers to its environment file
(``.env``, ``/etc/myportal.env`` or the Docker ``myportal.env``).

The wizard is safe to re-run at any time:

* every prompt shows the current value, and pressing Enter keeps it, so a
  re-run is how incorrect values are amended or new settings are added;
* ``--check`` reports missing, placeholder and weak values without prompting
  (exit status 1 when something needs attention), so it can be used to verify
  an existing file after an upgrade;
* disabling a feature only adds its slug(s) to ``DISABLED_FEATURE_PACKS`` or
  ``DISABLED_MODULES``; its settings stay in the file so re-enabling it later
  restores the previous configuration.

Existing lines, comments and ordering are preserved; changed keys are
rewritten in place and new keys are appended at the end of the file. The
previous file is copied to ``<file>.bak-<timestamp>`` before it is replaced.

The script only uses the Python standard library so it runs on a bare host as
well as inside the application container.

Usage::

    python3 scripts/onboarding_wizard.py                 # full wizard
    python3 scripts/onboarding_wizard.py --check         # verify only
    python3 scripts/onboarding_wizard.py --list          # feature status
    python3 scripts/onboarding_wizard.py --feature xero  # one feature
    myportal-docker setup [--check | --list | --feature SLUG]  # Docker hosts
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import re
import secrets
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_TEMPLATE = PROJECT_ROOT / ".env.example"
FEATURES_DIR = PROJECT_ROOT / "app" / "features"
PRODUCTION_ENV_FILE = Path("/etc/myportal.env")

DISABLED_PACKS_KEY = "DISABLED_FEATURE_PACKS"
DISABLED_MODULES_KEY = "DISABLED_MODULES"

# Values distributed in templates that must be replaced. Mirrors
# ``app.core.config._PLACEHOLDER_SECRETS`` plus the template database password.
PLACEHOLDER_VALUES = frozenset(
    {
        "change-me",
        "changeme",
        "change_me",
        "please-change",
        "replace-me",
        "secret",
        "password",
        "strong-password",
    }
)
MIN_SECRET_LENGTH = 32  # app.core.config._MIN_PRODUCTION_SECRET_LENGTH


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
def _token(length: int = 48) -> Callable[[], str]:
    return lambda: secrets.token_urlsafe(length)


def _fernet_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _vault_key() -> str:
    return "k1:" + base64.b64encode(os.urandom(32)).decode("ascii")


@dataclass(frozen=True)
class Setting:
    """One environment variable the wizard prompts for."""

    key: str
    label: str
    kind: str = "text"  # text, secret, bool, int, url, email, choice
    default: str = ""
    required: bool = False
    help: str = ""
    choices: tuple[str, ...] = ()
    # Offers "g" at the prompt to generate a value (and generates one
    # automatically when a required secret is missing).
    generate: Callable[[], str] | None = None
    # Required only while this boolean setting is true.
    required_when: str = ""
    # Warn loudly before changing a value that is already set.
    change_warning: str = ""
    # Managed by docker-compose on Docker installs; not prompted there.
    docker_managed: bool = False
    # Minimum length / strength checks apply (session and encryption keys).
    strong: bool = False
    # True when ``default`` is only a suggestion for the prompt; otherwise it
    # is the application's own default and satisfies ``required`` when the key
    # is absent from the file.
    suggested_default: bool = False


@dataclass(frozen=True)
class Section:
    """Always-on core settings (cannot be disabled)."""

    key: str
    name: str
    description: str
    settings: tuple[Setting, ...]


@dataclass(frozen=True)
class Feature:
    """A feature pack and/or module that can be enabled or disabled."""

    key: str
    name: str
    description: str
    packs: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    settings: tuple[Setting, ...] = ()
    group: str = "Portal features"
    # Feature whose disabling also makes this one unavailable.
    parent: str = ""

    @property
    def slugs(self) -> tuple[str, ...]:
        return self.packs + self.modules


S = Setting

CORE_SECTIONS: tuple[Section, ...] = (
    Section(
        "application",
        "Application",
        "Name, public address and environment of this MyPortal instance.",
        (
            S("APP_NAME", "Application name", default="MyPortal"),
            S(
                "ENVIRONMENT",
                "Environment",
                "choice",
                default="development",
                choices=("development", "production"),
                help="production enforces strong secrets and Secure-only cookies (HTTPS required).",
            ),
            S(
                "PORTAL_URL",
                "Portal URL",
                "url",
                required=True,
                help="Public address users open, e.g. https://portal.example.com. Used for links and OAuth callbacks.",
            ),
            S(
                "PUBLIC_BASE_URL",
                "Public base URL for callbacks",
                "url",
                help="Only needed when the reverse proxy does not forward X-Forwarded-Proto/Host.",
            ),
            S("CRON_TIMEZONE", "Scheduler time zone", default="UTC", help="IANA name, e.g. Australia/Brisbane."),
            S(
                "TRUSTED_PROXIES",
                "Trusted reverse proxies",
                help="Comma-separated IPs/CIDRs allowed to set X-Forwarded-For. Leave blank without a proxy.",
            ),
            S(
                "ALLOWED_ORIGINS",
                "Allowed CORS origins",
                help="Comma-separated origins. Leave blank for same-origin only (recommended).",
            ),
        ),
    ),
    Section(
        "security",
        "Security",
        "Session and encryption keys, CSRF protection and IP allow-listing.",
        (
            S(
                "SESSION_SECRET",
                "Session signing secret",
                "secret",
                required=True,
                generate=_token(),
                strong=True,
                help="Changing it signs every user out.",
            ),
            S(
                "TOTP_ENCRYPTION_KEY",
                "Two-factor / credential encryption key",
                "secret",
                required=True,
                generate=_token(),
                strong=True,
                change_warning=(
                    "Changing TOTP_ENCRYPTION_KEY makes existing two-factor secrets and "
                    "encrypted integration credentials unreadable."
                ),
            ),
            S("ENABLE_CSRF", "Enable CSRF protection", "bool", default="true"),
            S("IP_WHITELIST_ENABLED", "Restrict admin access by IP address", "bool", default="false"),
            S(
                "IP_WHITELIST",
                "Allowed IP addresses / CIDR ranges",
                required_when="IP_WHITELIST_ENABLED",
                help="Comma-separated, e.g. 192.168.1.0/24,10.0.0.5",
            ),
        ),
    ),
    Section(
        "database",
        "Database",
        "MySQL/MariaDB connection and optional Redis.",
        (
            S("DB_HOST", "Database host", default="localhost", required=True, docker_managed=True, suggested_default=True),
            S("DB_PORT", "Database port", "int", default="3306", docker_managed=True),
            S("DB_USER", "Database user", default="myportal", required=True, docker_managed=True, suggested_default=True),
            S("DB_PASSWORD", "Database password", "secret", required=True, docker_managed=True),
            S("DB_NAME", "Database name", default="myportal", required=True, docker_managed=True, suggested_default=True),
            S(
                "REDIS_URL",
                "Redis URL",
                help="Recommended for production, e.g. redis://127.0.0.1:6379/0. Leave blank to disable.",
            ),
        ),
    ),
    Section(
        "email",
        "Outgoing email (SMTP server)",
        "Server used for password resets, notifications and other system email.",
        (
            S("SMTP_HOST", "SMTP host", help="Leave blank to disable outgoing email."),
            S("SMTP_PORT", "SMTP port", "int", default="587"),
            S("SMTP_USER", "SMTP username"),
            S("SMTP_PASS", "SMTP password", "secret"),
            S("SMTP_SECURE", "Use STARTTLS/TLS", "bool", default="true"),
            S("SMTP_FROM", "From address", "email", help="Defaults to the SMTP username when blank."),
            S(
                "OUTBOUND_AUDIT_BCC",
                "Audit BCC mailbox",
                "email",
                help="Optional mailbox that receives a blind copy of every outbound email.",
            ),
        ),
    ),
)

_INTEGRATIONS = "Integrations"
_AI = "AI"
_TELEPHONY = "Telephony and messaging"
_PROFILE = "My Profile components"

FEATURES: tuple[Feature, ...] = (
    # -- Portal features ----------------------------------------------------
    Feature("tickets", "Tickets", "Service desk tickets, replies and time tracking.", packs=("tickets",)),
    Feature("companies", "Companies", "Customer companies and memberships.", packs=("companies",)),
    Feature("staff", "Staff", "Company staff directory, onboarding and offboarding.", packs=("staff",)),
    Feature("assets", "Assets", "Asset inventory and custom fields.", packs=("assets",)),
    Feature(
        "network_devices",
        "Network Devices",
        "Discovered devices, network scanners and device types.",
        packs=("network_devices",),
        parent="assets",
    ),
    Feature("ipam", "IPAM", "IP address management networks and addresses.", packs=("ipam",), parent="assets"),
    Feature("racks", "Racks", "Rack layouts, equipment and reservations.", packs=("racks",), parent="assets"),
    Feature("network_map", "Network Map", "Visual network topology maps.", packs=("network_map",)),
    Feature("automations", "Automations", "Scheduled and event-driven automations.", packs=("automations",)),
    Feature("applications", "Applications", "Register of company applications, types, champions and product keys.", packs=("applications",)),
    Feature("processes", "Processes", "Reusable, versioned process templates and execution runs.", packs=("processes",)),
    Feature("knowledge_base", "Knowledge Base", "Knowledge base articles.", packs=("knowledge_base",)),
    Feature("notifications", "Notifications", "In-portal notifications and preferences.", packs=("notifications",)),
    Feature("message_templates", "Message Templates", "Reusable email and message templates.", packs=("message_templates",)),
    Feature("webhooks", "Webhooks", "Outbound webhook delivery and monitoring.", packs=("webhooks",)),
    Feature("api_keys", "API Keys", "API key management for the REST API.", packs=("api_keys",)),
    Feature("help", "Help", "In-portal help pages.", packs=("help",)),
    Feature("reporting", "Reporting", "Report builder and reporting dashboards.", packs=("reporting",)),
    Feature("reports", "Company Reports", "Company overview reports.", packs=("reports",)),
    Feature("issue_tracker", "Issue Tracker", "Track known issues across companies.", packs=("issue_tracker",)),
    Feature("service_status", "Service Status", "Public service status page.", packs=("service_status",)),
    Feature("subscriptions", "Subscriptions", "Customer subscriptions and renewals.", packs=("subscriptions",)),
    Feature("invoices", "Invoices", "Customer invoices.", packs=("invoices",)),
    Feature(
        "quotes",
        "Quotes",
        "Customer quotes with magic-link acceptance.",
        packs=("quotes",),
        settings=(S("QUOTE_EXPIRY_DAYS", "Days before a quote expires", "int", default="7"),),
    ),
    Feature("orders", "Orders", "Customer orders.", packs=("orders",)),
    Feature(
        "shop",
        "Shop",
        "Product catalogue and ordering.",
        packs=("shop",),
        settings=(
            S("STOCK_FEED_URL", "Stock feed URL", "url", help="Optional supplier stock feed."),
            S("SHOP_WEBHOOK_URL", "Order webhook URL", "url"),
            S("SHOP_WEBHOOK_API_KEY", "Order webhook API key", "secret"),
        ),
    ),
    Feature("cart", "Cart", "Shopping cart and checkout.", packs=("cart",), parent="shop"),
    Feature("marketing", "Marketing", "Marketing pages and campaigns.", packs=("marketing",)),
    Feature("compliance", "Compliance", "Compliance checks and assignments.", packs=("compliance",)),
    Feature(
        "essential8",
        "Essential 8 Compliance",
        "Essential 8 dashboard, controls and report sections.",
        packs=("essential8",),
        parent="compliance",
    ),
    Feature(
        "gmp_glp",
        "GMP/GLP Compliance Checks",
        "GMP and GLP categories and predefined checks.",
        packs=("gmp_glp",),
        parent="compliance",
    ),
    Feature(
        "continuity",
        "Business Continuity",
        "Business continuity plans and PDF export.",
        packs=("continuity",),
        settings=(S("EXPORT_MAX_PER_MINUTE", "Plan exports allowed per minute", "int", default="10"),),
    ),
    Feature(
        "dmarc",
        "DMARC Reporting",
        "DMARC aggregate report ingestion and dashboards.",
        packs=("dmarc",),
        settings=(S("DMARC_RETENTION_DAYS", "Days to keep DMARC reports", "int", default="365"),),
    ),
    Feature("backups", "Backups", "Backup job monitoring.", packs=("backups",)),
    Feature("backup_history", "Backup History", "Backup job history administration.", packs=("backup_history",), parent="backups"),
    Feature("backup_summary", "Backup Summary", "Backup summary dashboard.", packs=("backup_summary",), parent="backups"),
    Feature(
        "websites",
        "Websites",
        "Company website documentation and monitoring.",
        packs=("websites",),
        settings=(
            S("WEBSITE_CHECK_INTERVAL_SECONDS", "Seconds between website checks", "int", default="86400"),
        ),
    ),
    Feature(
        "shared_credentials",
        "Shared Credentials",
        "Credential vault, shared credentials page and credential share links.",
        packs=("shared_credentials",),
        settings=(
            S(
                "VAULT_KEYS",
                "Vault encryption keys",
                "secret",
                generate=_vault_key,
                help="Comma-separated key-id:base64 AES-256 keys. Keep a secure copy; losing it loses the vault.",
                change_warning="Removing a vault key makes credentials encrypted with it unreadable.",
            ),
            S("VAULT_ACTIVE_KEY_ID", "Active vault key id", help="Id of the key used for new secrets, e.g. k1."),
        ),
    ),
    Feature(
        "office365",
        "Office 365",
        "Office 365 configuration, best practices, mailboxes, signatures and licenses.",
        packs=("office365",),
        settings=(
            S("M365_IT_EXTERNAL_EMAIL_ADDRESS", "Best-practice IT external contact", "email"),
            S("M365_IT_SUPPORT_EXTERNAL_EMAIL_ADDRESS", "Best-practice IT support external contact", "email"),
            S("M365_IT_RECIPIENT_ADDRESS_CONTAINS_WORDS", "Recipient match (usually your mail domain)"),
        ),
    ),
    Feature("defender", "Windows Defender", "Defender detections, exclusions and tray commands.", packs=("defender",)),
    Feature(
        "tray",
        "Tray Agent",
        "Windows tray agent API, installers, devices and settings.",
        packs=("tray",),
        settings=(
            S("GITHUB_TOKEN", "GitHub token for tray MSI downloads", "secret", help="Optional; avoids API rate limits."),
            S("RECAPTCHA_SITE_KEY", "reCAPTCHA site key (fallback ticket form)"),
            S("RECAPTCHA_SECRET_KEY", "reCAPTCHA secret key", "secret"),
        ),
    ),
    Feature(
        "forms",
        "Forms",
        "OpnForm-backed forms pages and administration.",
        packs=("forms",),
        settings=(S("OPNFORM_BASE_URL", "OpnForm base URL", "url"),),
    ),
    Feature(
        "chat",
        "Matrix Chat",
        "Built-in chat backed by a Matrix homeserver.",
        packs=("chat",),
        settings=(
            S("MATRIX_ENABLED", "Enable Matrix chat", "bool", default="false"),
            S("MATRIX_HOMESERVER_URL", "Homeserver URL", "url", required_when="MATRIX_ENABLED"),
            S("MATRIX_SERVER_NAME", "Server name (domain of MXIDs)", required_when="MATRIX_ENABLED"),
            S("MATRIX_BOT_USER_ID", "Bot user id, e.g. @myportal-bot:example.com", required_when="MATRIX_ENABLED"),
            S("MATRIX_BOT_ACCESS_TOKEN", "Bot access token", "secret", required_when="MATRIX_ENABLED"),
            S("MATRIX_IS_SELF_HOSTED", "Self-hosted homeserver", "bool", default="false"),
            S("MATRIX_ADMIN_ACCESS_TOKEN", "Homeserver admin access token", "secret"),
        ),
    ),
    # -- My Profile components ----------------------------------------------
    Feature(
        "outlook_contacts",
        "Outlook Contacts",
        "My Profile Outlook contacts and the ticket contact lookup.",
        packs=("outlook_contacts",),
        group=_PROFILE,
    ),
    Feature(
        "notification_contact",
        "Notification Contact",
        "My Profile notification contact (SMS number) card.",
        packs=("notification_contact",),
        group=_PROFILE,
    ),
    Feature("email_signature", "Email Signature", "My Profile email signature card.", packs=("email_signature",), group=_PROFILE),
    Feature(
        "click_to_call",
        "Click to Call",
        "Click-to-call settings and phone links.",
        packs=("click_to_call",),
        group=_PROFILE,
        settings=(
            S(
                "CLICK_TO_CALL_PHONE_PREFIXES",
                "Phone prefixes to link",
                default="+61,617,614,04",
                help="Comma-separated prefixes eligible for click-to-call detection.",
            ),
        ),
    ),
    # -- AI -------------------------------------------------------------------
    Feature(
        "ollama",
        "AI (Ollama / OpenAI / llama.cpp)",
        "AI ticket summaries, tags and the reporting query builder.",
        packs=("ollama",),
        modules=("ollama",),
        group=_AI,
        settings=(
            S("OLLAMA_PROVIDER", "Provider", "choice", default="ollama", choices=("ollama", "openai", "llamacpp")),
            S("OLLAMA_BASE_URL", "Provider base URL", "url", default="http://127.0.0.1:11434", required=True),
            S("OLLAMA_MODEL", "Model", default="llama3", required=True),
            S("OPENAI_API_KEY", "OpenAI API key", "secret", help="Required for the openai provider."),
        ),
    ),
    Feature("reprocess_ai", "Reprocess AI", "Regenerate AI ticket summaries, tags and resolutions.", packs=("reprocess_ai",), modules=("reprocess-ai",), group=_AI, parent="ollama"),
    Feature(
        "rag_index",
        "RAG Index",
        "Retrieval index used for AI answers and related-item matching.",
        packs=("rag_index",),
        group=_AI,
        settings=(
            S(
                "RAG_EMBEDDING_PROVIDER",
                "Embedding provider",
                "choice",
                default="lexical",
                choices=("lexical", "ollama", "openai_compatible"),
                help="lexical works offline without a model server.",
            ),
            S("RAG_EMBEDDING_MODEL", "Embedding model", default="myportal-lexical-v3"),
            S("RAG_EMBEDDING_BASE_URL", "Embedding server URL", "url", default="http://127.0.0.1:11434"),
            S("RAG_EMBEDDING_API_KEY", "Embedding API key", "secret"),
        ),
    ),
    Feature("ai_quality", "AI Quality", "AI quality review dashboard.", packs=("ai_quality",), group=_AI),
    Feature("ai_tag_synonyms", "AI Tag Synonyms", "AI tag synonym groups used by chat assignment.", packs=("ai_tag_synonyms",), group=_AI),
    Feature(
        "chatgpt_mcp",
        "ChatGPT MCP",
        "Expose ticketing tools to ChatGPT via the Model Context Protocol.",
        packs=("chatgpt_mcp",),
        modules=("chatgpt-mcp",),
        group=_AI,
        settings=(
            S("CHATGPT_MCP_SHARED_SECRET", "Shared secret", "secret", required=True, generate=_token(32)),
            S("CHATGPT_MCP_ALLOW_UPDATES", "Allow ticket updates", "bool", default="false"),
            S("CHATGPT_MCP_SYSTEM_USER_ID", "System user id for replies", "int"),
        ),
    ),
    Feature(
        "ollama_mcp",
        "Ollama MCP",
        "Expose ticket search and lookup tools to Ollama via MCP.",
        modules=("ollama-mcp",),
        group=_AI,
        settings=(
            S("OLLAMA_MCP_SHARED_SECRET", "Shared secret", "secret", required=True, generate=_token(32)),
            S("OLLAMA_MCP_ALLOW_REPLIES", "Allow ticket replies", "bool", default="false"),
            S("OLLAMA_MCP_ALLOW_UPDATES", "Allow ticket updates", "bool", default="false"),
        ),
    ),
    Feature(
        "whisperx",
        "WhisperX",
        "Transcribe ticket audio and call recordings.",
        modules=("whisperx",),
        group=_AI,
        settings=(
            S("WHISPERX_BASE_URL", "WhisperX base URL", "url", required=True),
            S("WHISPERX_API_KEY", "WhisperX API key", "secret"),
            S("WHISPERX_LANGUAGE", "Language", default="en"),
        ),
    ),
    # -- Telephony and messaging --------------------------------------------
    Feature("calls", "Calls", "Receive and review phone call webhook events.", packs=("calls",), modules=("calls",), group=_TELEPHONY),
    Feature(
        "call_recordings",
        "Call Recordings",
        "Store and process call recording files.",
        packs=("call_recordings",),
        modules=("call-recordings",),
        group=_TELEPHONY,
        settings=(
            S("CALL_RECORDINGS_PATH", "Recordings directory", default="/var/lib/myportal/call_recordings"),
            S(
                "CALL_RECORDINGS_PHONE_SYSTEM",
                "Phone system",
                "choice",
                default="generic",
                choices=("generic", "grandstream-ucm", "3cx"),
            ),
        ),
    ),
    Feature(
        "unifi_talk",
        "Unifi Talk",
        "Import call recordings from a Unifi Talk server via SFTP.",
        modules=("unifi-talk",),
        group=_TELEPHONY,
        settings=(
            S("UNIFI_TALK_REMOTE_HOST", "SFTP host", required=True),
            S("UNIFI_TALK_PORT", "SFTP port", "int", default="22"),
            S("UNIFI_TALK_USERNAME", "SFTP username", required=True),
            S("UNIFI_TALK_PASSWORD", "SFTP password", "secret", required=True),
            S("UNIFI_TALK_REMOTE_PATH", "Remote recordings path", default="/volume1/.srv/unifi-talk/recordings"),
            S("UNIFI_TALK_LOCAL_PATH", "Local recordings path", default="/var/lib/myportal/call_recordings"),
        ),
    ),
    Feature(
        "voice_monitor",
        "Voice Monitor",
        "Bounded health-check calls to subscribed telephone numbers over SIP.",
        packs=("voice_monitor",),
        modules=("voice-monitor",),
        group=_TELEPHONY,
        settings=(
            S("SIP_SERVER", "SIP server", required=True),
            S("SIP_PORT", "SIP port", "int", default="5060"),
            S("SIP_USERNAME", "SIP username", required=True),
            S("SIP_PASSWORD", "SIP password", "secret", required=True),
            S("SIP_CALLER_ID", "Caller id"),
            S("SIP_TRANSPORT", "SIP transport", "choice", default="udp", choices=("udp", "tcp", "tls")),
            S(
                "VOICE_MONITOR_MEDIA_ENCRYPTION_KEY",
                "Media encryption key (Fernet)",
                "secret",
                generate=_fernet_key,
                help="Optional; encrypts retained call media at rest.",
            ),
        ),
    ),
    Feature("receive_sms", "Receive SMS", "Create and update tickets from inbound SMS webhooks.", packs=("receive_sms",), modules=("receive-sms",), group=_TELEPHONY),
    Feature(
        "sms_gateway",
        "Send SMS",
        "Send SMS messages via an HTTP gateway (also used for staff verification).",
        packs=("sms_gateway",),
        modules=("sms-gateway",),
        group=_TELEPHONY,
        settings=(
            S("SMS_GATEWAY_URL", "Gateway URL", "url", required=True),
            S("SMS_GATEWAY_AUTH", "Gateway Authorization header value", "secret"),
        ),
    ),
    Feature(
        "ntfy",
        "ntfy",
        "Broadcast automation alerts to ntfy topics.",
        packs=("ntfy",),
        modules=("ntfy",),
        group=_TELEPHONY,
        settings=(
            S("NTFY_BASE_URL", "ntfy server", "url", default="https://ntfy.sh", required=True),
            S("NTFY_TOPIC", "Topic", required=True),
            S("NTFY_AUTH_TOKEN", "Access token", "secret"),
        ),
    ),
    Feature(
        "apprise",
        "Apprise",
        "Send notifications to 80+ services via Apprise URLs.",
        modules=("apprise",),
        group=_TELEPHONY,
        settings=(
            S("APPRISE_URLS", "Apprise URLs", "secret", required=True, help="Comma-separated notification URLs."),
            S("APPRISE_TITLE", "Default title"),
        ),
    ),
    Feature(
        "matrix_chat_assign",
        "Matrix Chat Auto-Assign",
        "Assign new Matrix chat rooms to technicians by rule.",
        packs=("matrix_chat_assign",),
        modules=("matrix-chat-assign",),
        group=_TELEPHONY,
        parent="chat",
    ),
    # -- Email integrations ---------------------------------------------------
    Feature(
        "smtp",
        "Send Email (automations)",
        "Outbound automation email through the platform SMTP server.",
        packs=("smtp",),
        modules=("smtp",),
        group=_INTEGRATIONS,
        settings=(
            S("SMTP_FROM_ADDRESS", "Automation From address", "email"),
            S("SMTP_DEFAULT_RECIPIENTS", "Default recipients", help="Comma-separated addresses."),
            S("SMTP_SUBJECT_PREFIX", "Subject prefix"),
        ),
    ),
    Feature(
        "smtp2go",
        "SMTP2Go",
        "Send email through the SMTP2Go API with delivery and open tracking.",
        modules=("smtp2go",),
        group=_INTEGRATIONS,
        settings=(
            S("SMTP2GO_API_KEY", "SMTP2Go API key", "secret", required=True),
            S("SMTP2GO_WEBHOOK_SECRET", "Webhook secret", "secret", generate=_token(32)),
            S("SMTP2GO_ENABLE_TRACKING", "Enable tracking", "bool", default="true"),
            S("SMTP2GO_TRACK_OPENS", "Track opens", "bool", default="true"),
            S("SMTP2GO_TRACK_CLICKS", "Track clicks", "bool", default="true"),
        ),
    ),
    Feature("m365_direct_delivery", "M365 Direct Delivery", "Deliver email directly into Microsoft 365 inboxes.", modules=("m365-direct-delivery",), group=_INTEGRATIONS),
    Feature("imap", "IMAP Mailboxes", "Import support email from IMAP mailboxes into tickets.", packs=("imap",), modules=("imap",), group=_INTEGRATIONS),
    Feature(
        "m365_admin",
        "Microsoft 365 Admin",
        "Microsoft 365 admin app used for tenant sync, licenses and mail import.",
        packs=("m365_admin",),
        modules=("m365-admin",),
        group=_INTEGRATIONS,
        settings=(
            S("M365_ADMIN_CLIENT_ID", "Admin app client id", help="Leave blank to provision from the portal."),
            S("M365_ADMIN_CLIENT_SECRET", "Admin app client secret", "secret"),
            S("M365_BOOTSTRAP_CLIENT_ID", "Bootstrap app client id (first-time provisioning)"),
            S("M365_BOOTSTRAP_CLIENT_SECRET", "Bootstrap app client secret", "secret"),
            S("M365_PKCE_CLIENT_ID", "Public client (PKCE) app id"),
        ),
    ),
    Feature("m365_mail", "Office 365 Mailbox Import", "Import support email from Microsoft 365 mailboxes.", packs=("m365_mail",), modules=("m365-mail",), group=_INTEGRATIONS),
    # -- Business integrations ----------------------------------------------
    Feature(
        "syncro",
        "Syncro",
        "Import tickets and contacts from SyncroMSP.",
        packs=("syncro",),
        modules=("syncro",),
        group=_INTEGRATIONS,
        settings=(
            S("SYNCRO_BASE_URL", "Syncro API base URL", "url", required=True),
            S("SYNCRO_API_KEY", "Syncro API key", "secret", required=True),
            S("SYNCRO_RATE_LIMIT_PER_MINUTE", "API calls per minute", "int", default="180"),
        ),
    ),
    Feature(
        "tacticalrmm",
        "Tactical RMM",
        "Asset sync and automation actions via Tactical RMM.",
        packs=("tacticalrmm",),
        modules=("tacticalrmm",),
        group=_INTEGRATIONS,
        settings=(
            S("TACTICALRMM_BASE_URL", "API base URL", "url", required=True),
            S("TACTICALRMM_BASE_RMM_URL", "Web UI URL", "url"),
            S("TACTICALRMM_API_KEY", "API key", "secret", required=True),
            S("TACTICALRMM_VERIFY_SSL", "Verify TLS certificates", "bool", default="true"),
        ),
    ),
    Feature(
        "uptimekuma",
        "Uptime Kuma",
        "Ingest uptime alerts from Uptime Kuma webhooks.",
        packs=("uptimekuma",),
        modules=("uptimekuma",),
        group=_INTEGRATIONS,
        settings=(
            S("UPTIMEKUMA_SHARED_SECRET", "Webhook shared secret", "secret", required=True, generate=_token(32)),
            S("UPTIMEKUMA_SYNC_SERVICE_STATUS", "Update Service Status from alerts", "bool", default="true"),
        ),
    ),
    Feature(
        "xero",
        "Xero",
        "Synchronise invoices with Xero.",
        packs=("xero",),
        modules=("xero",),
        group=_INTEGRATIONS,
        settings=(
            S("XERO_CLIENT_ID", "Xero app client id", required=True),
            S("XERO_CLIENT_SECRET", "Xero app client secret", "secret", required=True),
            S("XERO_WEBHOOK_KEY", "Webhook signing key", "secret"),
            S("XERO_TENANT_ID", "Tenant id", help="Can be selected from the portal after connecting."),
            S("XERO_COMPANY_NAME", "Company name in MyPortal that represents your business"),
            S("XERO_DEFAULT_HOURLY_RATE", "Default hourly rate"),
            S("XERO_ACCOUNT_CODE", "Sales account code", default="400"),
            S("XERO_TAX_TYPE", "Tax type"),
            S("XERO_INVOICE_DUE_DAYS", "Invoice due days", "int", default="14"),
        ),
    ),
    Feature(
        "password_pusher",
        "Password Pusher",
        "Share secrets through time- and view-limited pwpush links.",
        packs=("password_pusher",),
        modules=("password-pusher",),
        group=_INTEGRATIONS,
        settings=(
            S("PASSWORD_PUSHER_BASE_URL", "Password Pusher URL", "url", default="https://pwpush.com", required=True),
            S("PASSWORD_PUSHER_API_KEY", "API key", "secret"),
            S("PASSWORD_PUSHER_USER_EMAIL", "Account email", "email"),
            S("PASSWORD_PUSHER_EXPIRE_AFTER_DAYS", "Expire after days", "int", default="7"),
            S("PASSWORD_PUSHER_EXPIRE_AFTER_VIEWS", "Expire after views", "int", default="5"),
        ),
    ),
    Feature(
        "hudu",
        "Hudu",
        "Hudu documentation and password management integration.",
        packs=("hudu",),
        modules=("hudu",),
        group=_INTEGRATIONS,
        settings=(
            S("HUDU_BASE_URL", "Hudu URL", "url", required=True),
            S("HUDU_API_KEY", "Hudu API key", "secret", required=True),
        ),
    ),
    Feature(
        "huntress",
        "Huntress",
        "Huntress EDR/ITDR/SAT statistics for company reports.",
        packs=("huntress",),
        modules=("huntress",),
        group=_INTEGRATIONS,
        settings=(
            S("HUNTRESS_API_KEY", "Huntress API key", required=True),
            S("HUNTRESS_API_SECRET", "Huntress API secret", "secret", required=True),
            S("CURRICULA_API_KEY", "Managed SAT (Curricula) API key"),
            S("CURRICULA_API_SECRET", "Managed SAT (Curricula) API secret", "secret"),
        ),
    ),
    Feature("trello", "Trello", "Link companies to Trello boards; cards become tickets.", packs=("trello",), modules=("trello",), group=_INTEGRATIONS),
    Feature(
        "solidtime",
        "Solidtime",
        "Sync tickets and time entries with Solidtime.",
        packs=("solidtime",),
        modules=("solidtime",),
        group=_INTEGRATIONS,
        settings=(
            S("SOLIDTIME_BASE_URL", "Solidtime URL", "url", required=True),
            S("SOLIDTIME_API_TOKEN", "API token", "secret", required=True),
            S("SOLIDTIME_ORGANIZATION_ID", "Organisation id", required=True),
            S("SOLIDTIME_DEFAULT_CLIENT_ID", "Default client id"),
            S("SOLIDTIME_WEBHOOK_SECRET", "Webhook secret", "secret", generate=_token(32)),
        ),
    ),
)

FEATURES_BY_KEY: dict[str, Feature] = {feature.key: feature for feature in FEATURES}


def discovered_pack_slugs() -> list[str]:
    """Built-in pack directories present in this checkout (same rule as the app)."""

    if not FEATURES_DIR.is_dir():
        return []
    return sorted(
        entry.name
        for entry in FEATURES_DIR.iterdir()
        if entry.is_dir() and not entry.name.startswith("_") and (entry / "__init__.py").exists()
    )


def all_features() -> list[Feature]:
    """Catalogue features plus any pack added by a release but not catalogued yet."""

    known = {slug for feature in FEATURES for slug in feature.packs}
    extra = [
        Feature(slug, slug.replace("_", " ").title(), "Feature pack.", packs=(slug,))
        for slug in discovered_pack_slugs()
        if slug not in known
    ]
    return list(FEATURES) + extra


def known_slugs(features: Iterable[Feature]) -> tuple[set[str], set[str]]:
    packs: set[str] = set()
    modules: set[str] = set()
    for feature in features:
        packs.update(feature.packs)
        modules.update(feature.modules)
    return packs, modules


# ---------------------------------------------------------------------------
# Environment file handling
# ---------------------------------------------------------------------------
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_PLAIN_VALUE = re.compile(r"^[A-Za-z0-9_./:@,+=%*~^!?\-]*$")


def parse_value(raw: str) -> str:
    """Decode a dotenv value (quotes, escapes and trailing comments)."""

    raw = raw.strip()
    if raw[:1] == '"':
        out: list[str] = []
        i = 1
        while i < len(raw):
            char = raw[i]
            if char == "\\" and i + 1 < len(raw):
                nxt = raw[i + 1]
                out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
                i += 2
                continue
            if char == '"':
                return "".join(out)
            out.append(char)
            i += 1
        return raw[1:]
    if raw[:1] == "'":
        end = raw.find("'", 1)
        return raw[1:end] if end != -1 else raw[1:]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def format_value(value: str) -> str:
    """Encode *value* so python-dotenv, systemd and Compose read it back verbatim."""

    if _PLAIN_VALUE.match(value):
        return value
    if "'" not in value and "\n" not in value:
        # Single quotes are literal everywhere (no ${VAR} interpolation).
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


@dataclass
class EnvFile:
    path: Path
    lines: list[str] = field(default_factory=list)
    exists: bool = False
    # Keys not yet in ``lines``: key -> (heading, value). Written as one block
    # at the end of the file so existing content is never reordered.
    _new: dict[str, tuple[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "EnvFile":
        if path.exists():
            return cls(path, path.read_text(encoding="utf-8").splitlines(), True)
        return cls(path)

    def _index(self, key: str) -> int | None:
        found = None
        for i, line in enumerate(self.lines):
            match = _ASSIGNMENT.match(line)
            if match and match.group(1) == key:
                found = i  # the last assignment wins, as in python-dotenv
        return found

    def values(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in self.lines:
            match = _ASSIGNMENT.match(line)
            if match:
                result[match.group(1)] = parse_value(match.group(2))
        result.update({key: value for key, (_, value) in self._new.items()})
        return result

    def duplicates(self) -> list[str]:
        counts: dict[str, int] = {}
        for line in self.lines:
            match = _ASSIGNMENT.match(line)
            if match:
                counts[match.group(1)] = counts.get(match.group(1), 0) + 1
        return sorted(key for key, count in counts.items() if count > 1)

    def set(self, key: str, value: str, heading: str = "") -> None:
        index = self._index(key)
        if index is not None:
            self.lines[index] = f"{key}={format_value(value)}"
        else:
            self._new[key] = (self._new.get(key, (heading, ""))[0], value)

    def render(self) -> str:
        body = list(self.lines)
        if self._new:
            while body and not body[-1].strip():
                body.pop()
            if body:
                body.append("")
            body.append(f"# Added by the MyPortal onboarding wizard on {datetime.now():%Y-%m-%d}")
            groups: dict[str, list[str]] = {}
            for key, (heading, value) in self._new.items():
                groups.setdefault(heading, []).append(f"{key}={format_value(value)}")
            for heading, lines in groups.items():
                if heading:
                    body.append(f"# {heading}")
                body.extend(lines)
        return "\n".join(body) + "\n"

    def save(self) -> Path | None:
        """Write the file atomically, keeping a timestamped backup of the old one."""

        backup = None
        mode, owner = 0o600, None
        if self.path.exists():
            stat = self.path.stat()
            mode, owner = stat.st_mode & 0o777, (stat.st_uid, stat.st_gid)
            backup = self.path.with_name(f"{self.path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(self.path, backup)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = self.render()
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.chmod(tmp, mode)
            if owner is not None and hasattr(os, "chown"):
                try:
                    os.chown(tmp, *owner)
                except PermissionError:
                    pass
            try:
                os.replace(tmp, self.path)
            except OSError:
                # A file bind-mounted on its own cannot be replaced; rewrite it.
                with open(self.path, "w", encoding="utf-8") as handle:
                    handle.write(content)
                os.unlink(tmp)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self.lines = content.splitlines()
        self._new.clear()
        self.exists = True
        return backup


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------
def slug_list(value: str | None) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in (value or "").split(",") if part.strip()))


def is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def is_placeholder(value: str | None) -> bool:
    return (value or "").strip().lower() in PLACEHOLDER_VALUES


def weak_secret_reason(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "is empty"
    if stripped.lower() in PLACEHOLDER_VALUES:
        return "uses a placeholder value"
    if len(stripped) < MIN_SECRET_LENGTH:
        return f"is shorter than {MIN_SECRET_LENGTH} characters"
    if len(set(stripped)) < 8:
        return "has too few unique characters"
    return ""


def feature_disabled(feature: Feature, values: dict[str, str]) -> bool:
    disabled = set(slug_list(values.get(DISABLED_PACKS_KEY))) | set(slug_list(values.get(DISABLED_MODULES_KEY)))
    return any(slug in disabled for slug in feature.slugs)


def feature_unavailable_parent(feature: Feature, values: dict[str, str]) -> Feature | None:
    parent = FEATURES_BY_KEY.get(feature.parent)
    while parent is not None:
        if feature_disabled(parent, values):
            return parent
        parent = FEATURES_BY_KEY.get(parent.parent)
    return None


def set_feature_enabled(env: EnvFile, feature: Feature, enabled: bool) -> None:
    values = env.values()
    for key, slugs in ((DISABLED_PACKS_KEY, feature.packs), (DISABLED_MODULES_KEY, feature.modules)):
        if not slugs:
            continue
        current = slug_list(values.get(key))
        if enabled:
            updated = [slug for slug in current if slug not in slugs]
        else:
            updated = current + [slug for slug in slugs if slug not in current]
        if updated != current or key not in values:
            env.set(key, ",".join(updated), "Deployment feature switches")


def setting_is_required(setting: Setting, values: dict[str, str]) -> bool:
    if setting.required_when:
        return is_true(values.get(setting.required_when))
    return setting.required


def setting_problem(setting: Setting, values: dict[str, str], *, production: bool) -> str:
    """Return why a setting needs attention, or an empty string."""

    value = values.get(setting.key)
    if value is None and setting.default and not setting.suggested_default:
        return ""  # the application default applies
    if value is None or not value.strip() or is_placeholder(value):
        if setting_is_required(setting, values):
            if value is not None and is_placeholder(value):
                return "uses a placeholder value"
            return "is missing" if value is None else "is empty"
        return ""
    if setting.strong and production:
        return weak_secret_reason(value)
    error = validate(setting, value)
    return error or ""


def validate(setting: Setting, value: str) -> str | None:
    if not value:
        return None
    if setting.kind == "int" and not re.fullmatch(r"-?\d+", value):
        return "must be a whole number"
    if setting.kind == "bool" and value.lower() not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        return "must be true or false"
    if setting.kind == "url" and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://\S+$", value):
        return "must be a URL such as https://example.com"
    if setting.kind == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        return "must be an email address"
    if setting.kind == "choice" and setting.choices and value not in setting.choices:
        return "must be one of: " + ", ".join(setting.choices)
    return None


def mask(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{'*' * 8}…{value[-4:]}"


# ---------------------------------------------------------------------------
# Check / list reports
# ---------------------------------------------------------------------------
def visible_settings(settings: Iterable[Setting], docker: bool) -> list[Setting]:
    return [setting for setting in settings if not (docker and setting.docker_managed)]


def run_check(env: EnvFile, *, docker: bool, verbose: bool = False, out=sys.stdout) -> int:
    values = env.values()
    production = values.get("ENVIRONMENT", "development").strip().lower() == "production"
    features = all_features()
    errors = 0
    warnings = 0

    def emit(level: str, text: str) -> None:
        nonlocal errors, warnings
        if level == "error":
            errors += 1
        elif level == "warn":
            warnings += 1
        prefix = {"error": "  ✗", "warn": "  !", "ok": "  ✓", "info": "  ·"}[level]
        print(f"{prefix} {text}", file=out)

    print(f"Checking {env.path}", file=out)
    if not env.exists:
        print("  ✗ The file does not exist. Run the wizard without --check to create it.", file=out)
        return 1

    for key in env.duplicates():
        emit("warn", f"{key} is defined more than once; the last value is used.")

    print("\nCore settings", file=out)
    for section in CORE_SECTIONS:
        for setting in visible_settings(section.settings, docker):
            problem = setting_problem(setting, values, production=production)
            if problem:
                emit("error", f"{setting.key} ({section.name}: {setting.label}) {problem}.")
            elif verbose and setting.key not in values:
                emit("info", f"{setting.key} is not set; the default ({setting.default or 'blank'}) applies.")
    if not production:
        emit("info", "ENVIRONMENT is not production; secret strength is only enforced in production.")

    packs, modules = known_slugs(features)
    for key, known in ((DISABLED_PACKS_KEY, packs), (DISABLED_MODULES_KEY, modules)):
        unknown = [slug for slug in slug_list(values.get(key)) if slug not in known]
        if unknown:
            emit("warn", f"{key} contains unknown slug(s): {', '.join(unknown)} (they will be ignored at startup).")

    print("\nFeatures", file=out)
    disabled_names = []
    for feature in features:
        if feature_disabled(feature, values):
            disabled_names.append(feature.name)
            continue
        parent = feature_unavailable_parent(feature, values)
        if parent is not None:
            disabled_names.append(f"{feature.name} (via {parent.name})")
            continue
        problems = []
        unset = []
        for setting in feature.settings:
            problem = setting_problem(setting, values, production=production)
            if problem:
                problems.append(f"{setting.key} {problem}")
            elif setting.key not in values:
                unset.append(setting.key)
        if problems:
            emit(
                "error",
                f"{feature.name}: " + "; ".join(problems)
                + ". Configure it or disable the feature.",
            )
        elif any((values.get(s.key) or "").strip() for s in feature.settings):
            emit("ok", f"{feature.name}: configured")
        if verbose and unset:
            emit("info", f"{feature.name}: not in file, defaults apply: {', '.join(unset)}")
    if disabled_names:
        print("\nDisabled (settings are kept in the file)", file=out)
        print("  " + ", ".join(disabled_names), file=out)

    print(f"\n{errors} problem(s), {warnings} warning(s).", file=out)
    return 1 if errors else 0


def run_list(env: EnvFile, out=sys.stdout) -> int:
    values = env.values()
    production = values.get("ENVIRONMENT", "").strip().lower() == "production"
    group = None
    for feature in all_features():
        if feature.group != group:
            group = feature.group
            print(f"\n{group}", file=out)
        if feature_disabled(feature, values):
            status = "disabled"
        elif (parent := feature_unavailable_parent(feature, values)) is not None:
            status = f"unavailable ({parent.name} disabled)"
        elif any(setting_problem(s, values, production=production) for s in feature.settings):
            status = "enabled, needs configuration"
        else:
            status = "enabled"
        print(f"  {feature.key:<22} {feature.name:<34} {status}", file=out)
    return 0


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------
class Aborted(Exception):
    pass


class Prompter:
    """Console I/O, separated so tests can script the answers."""

    def __init__(self, input_fn=input, secret_fn=getpass.getpass, out=sys.stdout):
        self._input = input_fn
        self._secret = secret_fn
        self.out = out

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def ask(self, prompt: str, *, secret: bool = False) -> str:
        try:
            return (self._secret if secret else self._input)(prompt).strip()
        except EOFError as exc:
            raise Aborted from exc

    def confirm(self, prompt: str, default: bool) -> bool:
        suffix = " [Y/n] " if default else " [y/N] "
        while True:
            answer = self.ask(prompt + suffix).lower()
            if not answer:
                return default
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self.say("  Please answer y or n.")


class Wizard:
    def __init__(self, env: EnvFile, prompter: Prompter, *, docker: bool = False):
        self.env = env
        self.io = prompter
        self.docker = docker
        self.changes: list[str] = []

    # -- values -------------------------------------------------------------
    @property
    def values(self) -> dict[str, str]:
        return self.env.values()

    def _record(self, setting: Setting, heading: str, value: str) -> None:
        current = self.values.get(setting.key)
        if current == value:
            return
        self.env.set(setting.key, value, heading)
        shown = mask(value) if setting.kind == "secret" else (value or "(empty)")
        self.changes.append(f"{setting.key} = {shown}")

    # -- prompts ------------------------------------------------------------
    def prompt_setting(self, setting: Setting, heading: str) -> None:
        values = self.values
        current = values.get(setting.key)
        has_value = current is not None and current.strip() != "" and not is_placeholder(current)
        required = setting_is_required(setting, values)
        production = values.get("ENVIRONMENT", "").strip().lower() == "production"

        if setting.help:
            self.io.say(f"    {setting.help}")

        # Missing required secrets that can be generated are generated
        # without asking; the admin can still replace them on a re-run.
        if (
            setting.kind == "secret"
            and setting.generate
            and not has_value
            and required
        ):
            self._record(setting, heading, setting.generate())
            self.io.say(f"  {setting.label}: generated a new random value.")
            return

        if setting.kind == "bool":
            default = is_true(current) if current not in (None, "") else is_true(setting.default)
            answer = self.io.confirm(f"  {setting.label}?", default)
            self._record(setting, heading, "true" if answer else "false")
            return

        if has_value:
            shown = mask(current) if setting.kind == "secret" else current
        elif current is None and setting.default:
            shown = setting.default
        else:
            shown = ""
        extras = []
        if setting.choices:
            extras.append("/".join(setting.choices))
        if setting.generate:
            extras.append("g = generate")
        if has_value and not required:
            extras.append("- = clear")
        hint = f" ({'; '.join(extras)})" if extras else ""
        marker = " *" if required else ""
        prompt = f"  {setting.label}{marker}{hint} [{shown}]: "

        while True:
            answer = self.io.ask(prompt, secret=setting.kind == "secret")
            if not answer:
                if has_value:
                    return  # keep the existing value untouched
                value = setting.default if current is None else (current or "")
                if is_placeholder(value):
                    value = ""
                if required and not value:
                    self.io.say("    Required while this feature is enabled; left blank for now (reported by --check).")
                if current is None or value != current:
                    self._record(setting, heading, value)
                return
            if answer == "-" and not required:
                self._record(setting, heading, "")
                return
            if answer.lower() == "g" and setting.generate:
                self._record(setting, heading, setting.generate())
                self.io.say("    Generated a new random value.")
                return
            if setting.kind == "choice" and answer.isdigit() and 1 <= int(answer) <= len(setting.choices):
                answer = setting.choices[int(answer) - 1]
            error = validate(setting, answer)
            if setting.strong and production and not error:
                reason = weak_secret_reason(answer)
                error = f"{reason} (production requires a strong value)" if reason else None
            if error:
                self.io.say(f"    Invalid value: {error}.")
                continue
            if has_value and setting.change_warning and answer != current:
                self.io.say(f"    WARNING: {setting.change_warning}")
                if not self.io.confirm("    Change it anyway?", False):
                    return
            self._record(setting, heading, answer)
            return

    def configure(self, name: str, settings: list[Setting], heading: str, *, force: bool) -> None:
        if not settings:
            return
        values = self.values
        production = values.get("ENVIRONMENT", "").strip().lower() == "production"
        problems = [s for s in settings if setting_problem(s, values, production=production)]
        missing = [s for s in settings if s.key not in values]
        if not force and not problems:
            if all(not (values.get(s.key) or "").strip() for s in settings):
                question, default = f"  {name} has only optional settings, none set. Configure them now?", False
            else:
                note = f" ({len(missing)} setting(s) not yet in the file)" if missing else ""
                question, default = f"  {name} is configured{note}. Review its settings?", False
            if not self.io.confirm(question, default):
                if missing:
                    # Record defaults so the file documents every setting.
                    for setting in missing:
                        self._record(setting, heading, setting.default)
                return
        for setting in settings:
            self.prompt_setting(setting, heading)

    # -- flow ---------------------------------------------------------------
    def run_core(self) -> None:
        for section in CORE_SECTIONS:
            settings = visible_settings(section.settings, self.docker)
            self.io.say(f"\n== {section.name} ==")
            self.io.say(f"  {section.description}")
            if self.docker and len(settings) < len(section.settings):
                self.io.say("  Database connection settings are managed by myportal-docker and are not shown.")
            self.configure(section.name, settings, section.name, force=not self.env.exists)

    def run_feature(self, feature: Feature) -> None:
        values = self.values
        self.io.say(f"\n-- {feature.name} --")
        self.io.say(f"  {feature.description}")
        parent = feature_unavailable_parent(feature, values)
        if parent is not None:
            self.io.say(f"  Unavailable while {parent.name} is disabled; skipped.")
            return
        was_enabled = not feature_disabled(feature, values)
        enable = self.io.confirm(f"  Enable {feature.name}?", was_enabled)
        if enable != was_enabled:
            set_feature_enabled(self.env, feature, enable)
            self.changes.append(f"{feature.name}: {'enabled' if enable else 'disabled'}")
        if not enable:
            if feature.settings and any(s.key in values for s in feature.settings):
                self.io.say("  Disabled. Its settings are kept so re-enabling restores them.")
            return
        self.configure(feature.name, list(feature.settings), feature.name, force=False)

    def run(self, feature_keys: list[str] | None = None, include_core: bool = True) -> bool:
        self.io.say("MyPortal onboarding wizard")
        self.io.say(f"Environment file: {self.env.path}{'' if self.env.exists else ' (new)'}")
        self.io.say("Press Enter to keep the value shown in [brackets]; * marks a required value.")
        self.io.say("Press Ctrl+C at any time to quit without saving.")

        if include_core:
            self.run_core()
        features = all_features()
        if feature_keys:
            features = [f for f in features if f.key in feature_keys]
        group = None
        for feature in features:
            if feature.group != group:
                group = feature.group
                self.io.say(f"\n==== {group} ====")
            self.run_feature(feature)

        if not self.changes and self.env.exists:
            self.io.say("\nNo changes; the environment file is unchanged.")
            return False
        self.io.say("\nSummary of changes:")
        for change in self.changes or ["(new file)"]:
            self.io.say(f"  {change}")
        if not self.io.confirm(f"\nWrite these changes to {self.env.path}?", True):
            self.io.say("Nothing was written.")
            return False
        backup = self.env.save()
        self.io.say(f"Saved {self.env.path}.")
        if backup:
            self.io.say(f"Previous version kept at {backup}.")
        restart = "myportal-docker restart" if self.docker else "sudo systemctl restart myportal (or restart your service)"
        self.io.say(f"Restart MyPortal to apply the changes: {restart}")
        return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def default_env_path() -> Path:
    configured = os.environ.get("MYPORTAL_ENV_FILE")
    if configured:
        return Path(configured)
    if PRODUCTION_ENV_FILE.exists():
        return PRODUCTION_ENV_FILE
    return PROJECT_ROOT / ".env"


def resolve_feature_keys(requested: list[str]) -> tuple[list[str], bool, list[str]]:
    """Map --feature arguments (keys or slugs) to catalogue keys."""

    keys: list[str] = []
    include_core = False
    unknown: list[str] = []
    for item in requested:
        for name in slug_list(item):
            if name in {"core", *(section.key for section in CORE_SECTIONS)}:
                include_core = True
                continue
            match = next((f for f in all_features() if name == f.key or name in f.slugs), None)
            if match is None:
                unknown.append(name)
            elif match.key not in keys:
                keys.append(match.key)
    return keys, include_core, unknown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onboarding_wizard.py",
        description="Configure MyPortal's environment file interactively.",
    )
    parser.add_argument("--env-file", type=Path, help="environment file to edit (default: $MYPORTAL_ENV_FILE, /etc/myportal.env, or .env)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report missing or invalid settings without prompting")
    mode.add_argument("--list", action="store_true", help="list features and their status")
    parser.add_argument(
        "--feature",
        action="append",
        default=[],
        metavar="NAME",
        help="only walk through these features (key or slug, repeatable; 'core' for core settings)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="with --check, also list settings left at their defaults")
    parser.add_argument("--docker", action="store_true", help="the file is a myportal-docker myportal.env")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.env_file or default_env_path()
    env = EnvFile.load(path)

    if args.check:
        return run_check(env, docker=args.docker, verbose=args.verbose)
    if args.list:
        return run_list(env)

    if not sys.stdin.isatty():
        print("The wizard is interactive; run it from a terminal, or use --check.", file=sys.stderr)
        return 2

    prompter = Prompter()
    keys, include_core, unknown = resolve_feature_keys(args.feature)
    if unknown:
        print(f"Unknown feature(s): {', '.join(unknown)}. Use --list to see them.", file=sys.stderr)
        return 2
    if args.feature and not env.exists:
        include_core = True

    try:
        if not env.exists and ENV_TEMPLATE.exists():
            prompter.say(f"{path} does not exist.")
            if prompter.confirm(f"Start from the documented template {ENV_TEMPLATE}?", True):
                env.lines = ENV_TEMPLATE.read_text(encoding="utf-8").splitlines()
        wizard = Wizard(env, prompter, docker=args.docker)
        wizard.run(keys or None, include_core=include_core or not args.feature)
    except (KeyboardInterrupt, Aborted):
        print("\nAborted; nothing was written.", file=sys.stderr)
        return 130
    except PermissionError as exc:
        print(f"Permission denied: {exc}. Re-run with sudo.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
