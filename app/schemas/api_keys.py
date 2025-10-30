from __future__ import annotations

from datetime import date, datetime
from ipaddress import ip_network
from typing import Optional

from pydantic import BaseModel, Field, model_validator

ALLOWED_API_KEY_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class ApiKeyUsageEntry(BaseModel):
    ip_address: str = Field(..., max_length=45)
    usage_count: int = Field(..., ge=0)
    last_used_at: Optional[datetime]


class ApiKeyEndpointPermission(BaseModel):
    path: str = Field(..., min_length=1, max_length=255)
    methods: list[str] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def _normalise(self) -> "ApiKeyEndpointPermission":
        path_value = self.path.strip()
        if not path_value.startswith("/"):
            raise ValueError("Paths must start with a forward slash")
        normalised_methods: list[str] = []
        seen: set[str] = set()
        for raw in self.methods:
            method = str(raw).strip().upper()
            if not method:
                continue
            if method not in ALLOWED_API_KEY_HTTP_METHODS:
                allowed = ", ".join(sorted(ALLOWED_API_KEY_HTTP_METHODS))
                raise ValueError(f"Unsupported HTTP method '{method}'. Allowed methods: {allowed}.")
            if method not in seen:
                normalised_methods.append(method)
                seen.add(method)
        if not normalised_methods:
            raise ValueError("At least one HTTP method must be provided")
        self.path = path_value
        self.methods = sorted(normalised_methods)
        return self


class ApiKeyIpRestriction(BaseModel):
    cidr: str = Field(..., min_length=3, max_length=64)

    @model_validator(mode="after")
    def _normalise(self) -> "ApiKeyIpRestriction":
        value = self.cidr.strip()
        if not value:
            raise ValueError("IP restriction entries cannot be blank")
        try:
            network = ip_network(value, strict=False)
        except ValueError as exc:  # pragma: no cover - validation guard
            raise ValueError("Enter a valid IP address or CIDR range") from exc
        self.cidr = network.with_prefixlen
        return self


class ApiKeyCreateRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=255)
    expiry_date: Optional[date]
    permissions: list[ApiKeyEndpointPermission] = Field(default_factory=list)
    allowed_ips: list[ApiKeyIpRestriction] = Field(default_factory=list)
    is_enabled: bool = True


class ApiKeyRotateRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=255)
    expiry_date: Optional[date]
    retire_previous: bool = True
    permissions: Optional[list[ApiKeyEndpointPermission]] = None
    allowed_ips: Optional[list[ApiKeyIpRestriction]] = None


class ApiKeyUpdateRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=255)
    expiry_date: Optional[date] = None
    permissions: Optional[list[ApiKeyEndpointPermission]] = None
    is_enabled: Optional[bool] = None


class ApiKeyResponse(BaseModel):
    id: int
    description: Optional[str]
    expiry_date: Optional[date]
    created_at: datetime
    last_used_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    usage_count: int = 0
    key_preview: str = Field(..., max_length=64)
    usage: list[ApiKeyUsageEntry] = Field(default_factory=list)
    permissions: list[ApiKeyEndpointPermission] = Field(default_factory=list)
    allowed_ips: list[ApiKeyIpRestriction] = Field(default_factory=list)
    is_enabled: bool = True


class ApiKeyDetailResponse(ApiKeyResponse):
    pass


class ApiKeyCreateResponse(ApiKeyResponse):
    api_key: str = Field(..., min_length=32)
