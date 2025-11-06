from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, conint, constr

PermissionScope = Literal["anonymous", "user", "company", "company_admin", "super_admin"]


class KnowledgeBaseArticleSection(BaseModel):
    heading: constr(strip_whitespace=True, max_length=255) | None = None
    content: constr(min_length=1)


class KnowledgeBaseArticleSectionResponse(KnowledgeBaseArticleSection):
    position: conint(ge=1) = 1


class KnowledgeBaseArticleBase(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=2000)
    permission_scope: PermissionScope = Field(default="anonymous")
    is_published: bool = False
    allowed_user_ids: list[int] = Field(default_factory=list)
    allowed_company_ids: list[int] = Field(default_factory=list)
    sections: list[KnowledgeBaseArticleSection] = Field(default_factory=list)
    content: str | None = Field(default=None)


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
    sections: list[KnowledgeBaseArticleSection] | None = None


class KnowledgeBaseArticleResponse(BaseModel):
    id: int
    slug: str
    title: str
    summary: str | None
    content: str
    permission_scope: PermissionScope
    is_published: bool
    ai_tags: list[str]
    excluded_ai_tags: list[str] = Field(default_factory=list)
    allowed_user_ids: list[int]
    allowed_company_ids: list[int]
    company_admin_ids: list[int]
    sections: list[KnowledgeBaseArticleSectionResponse]
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
    ai_tags: list[str]
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

