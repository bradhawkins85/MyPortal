"""Signed envelopes for Microsoft OAuth ``state`` and related browser tokens."""

from __future__ import annotations

from itsdangerous import URLSafeSerializer

from app.core.config import get_settings

oauth_state_serializer = URLSafeSerializer(get_settings().secret_key, salt="m365-oauth")
