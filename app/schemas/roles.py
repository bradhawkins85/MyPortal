from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from app.security.menu_permissions import MENU_PERMISSION_MAP, compact_menu_permissions


def _validate_permission_keys(value: Any) -> None:
    if isinstance(value, dict):
        source = value.get("menu") if isinstance(value.get("menu"), dict) else value
        ignored = {"legacy", "permissions"}
        unknown = [key for key in source if key not in MENU_PERMISSION_MAP and key not in ignored]
        if unknown:
            raise ValueError(f"Unknown menu permission key(s): {', '.join(sorted(map(str, unknown)))}")


class RoleBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    permissions: dict[str, str] | list[str] = Field(
        default_factory=dict,
        description=(
            "Tri-state menu permissions keyed by menu permission id. "
            "Allowed levels are none, read, and write. Legacy string lists are accepted for compatibility."
        ),
        examples=[
            {
                "menu.m365.configuration": "none",
                "menu.m365.user_mailboxes": "read",
                "menu.subscriptions": "write",
            }
        ],
    )

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: Any) -> dict[str, str]:
        _validate_permission_keys(value)
        return compact_menu_permissions(value)


class RoleCreate(RoleBase):
    is_system: bool = False


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    description: Optional[str] = None
    permissions: Optional[dict[str, str] | list[str]] = Field(
        default=None,
        description="Tri-state menu permission updates. Use none, read, or write for each supplied menu key.",
    )

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: Any) -> dict[str, str] | None:
        if value is None:
            return None
        _validate_permission_keys(value)
        return compact_menu_permissions(value)


class MenuPermissionResponse(BaseModel):
    key: str
    label: str
    group: str
    description: str
    legacy_permissions: list[str] = Field(default_factory=list)
    legacy_boolean: Optional[str] = None
    admin_only: bool = False
    levels: list[str] = Field(default_factory=lambda: ["none", "read", "write"])


class RoleResponse(RoleBase):
    id: int
    is_system: bool = False
    menu_permissions: dict[str, str] = Field(default_factory=dict)
    legacy_permissions: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True
