"""Signed envelopes for Microsoft OAuth ``state`` and related browser tokens."""

from __future__ import annotations

from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer

from app.core.config import get_settings

oauth_state_serializer = URLSafeSerializer(get_settings().secret_key, salt="m365-oauth")

# Carries the tenant found by guided setup's Microsoft sign-in back to the
# configuration page.  Signed (and time-limited) so a crafted link cannot show
# an administrator a misleading tenant name next to a different tenant ID.
discovered_tenant_serializer = URLSafeTimedSerializer(
    get_settings().secret_key, salt="m365-discovered-tenant"
)
DISCOVERED_TENANT_MAX_AGE_SECONDS = 1800
