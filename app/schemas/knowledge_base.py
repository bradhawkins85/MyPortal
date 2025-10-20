from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, constr

PermissionScope = Literal["anonymous", "user", "company", "company_admin", "super_admin"]


class KnowledgeBaseArticleBase(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=2000)
    content: str = Field(..., min_length=1)
    permission_scope: PermissionScope = Field(default="anonymous")
    is_published: bool = False
    allowed_user_ids: list[int] = Field(default_factory=list)
    allowed_company_ids: list[int] = Field(default_factory=list)


class KnowledgeBaseArticleCreate(KnowledgeBaseArticleBase):
    slug: constr(strip_whitespace=True, min_length=1, max_length=191)


class KnowledgeBaseArticleUpdate(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=255) | None = None
    slug: constr(strip_whitespace=True, min_length=1, max_length=191) | None = None
    summary: str | None = Field(default=None, max_length=2000)
    content: str | None = None
    permission_scope: PermissionScope | None = None
    is_published: bool | None = None
    allowed_user_ids: list[int] | None = None
    allowed_company_ids: list[int] | None = None


class KnowledgeBaseArticleResponse(BaseModel):
    id: int
    slug: str
    title: str
    summary: str | None
    content: str
    permission_scope: PermissionScope
    is_published: bool
    allowed_user_ids: list[int]
    allowed_company_ids: list[int]
    company_admin_ids: list[int]
    created_by: int | None
    created_at: datetime | None
    updated_at: datetime | None
    published_at: datetime | None


class KnowledgeBaseArticleListItem(BaseModel):
    id: int
    slug: str
    title: str
    summary: str | None
    permission_scope: PermissionScope
    is_published: bool
    updated_at: datetime | None
    updated_at_iso: str | None
    published_at_iso: str | None


class KnowledgeBaseSearchRequest(BaseModel):
    query: constr(strip_whitespace=True, min_length=1, max_length=500)


class KnowledgeBaseSearchResult(BaseModel):
    id: int
    slug: str
    title: str
    summary: str | None
    excerpt: str | None
    updated_at_iso: str | None


class KnowledgeBaseSearchResponse(BaseModel):
    results: list[KnowledgeBaseSearchResult]
    ollama_status: str
    ollama_model: str | None = None
    ollama_summary: str | None = None

