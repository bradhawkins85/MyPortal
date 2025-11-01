from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, constr


class AgentSourceArticle(BaseModel):
    slug: str
    title: str
    summary: Optional[str] = None
    excerpt: Optional[str] = None
    updated_at: Optional[str] = None
    url: Optional[str] = None


class AgentSourceTicket(BaseModel):
    id: int
    subject: str
    status: str
    priority: str
    updated_at: Optional[str] = None
    summary: Optional[str] = None
    company_id: Optional[int] = None


class AgentSourceProduct(BaseModel):
    id: Optional[int] = None
    name: str
    sku: Optional[str] = None
    vendor_sku: Optional[str] = None
    price: Optional[str] = None
    description: Optional[str] = None
    recommendations: list[str] = Field(default_factory=list)


class AgentSources(BaseModel):
    knowledge_base: list[AgentSourceArticle] = Field(default_factory=list)
    tickets: list[AgentSourceTicket] = Field(default_factory=list)
    products: list[AgentSourceProduct] = Field(default_factory=list)


class AgentContextCompany(BaseModel):
    company_id: int
    company_name: str


class AgentContext(BaseModel):
    companies: list[AgentContextCompany] = Field(default_factory=list)


class AgentQueryRequest(BaseModel):
    query: constr(strip_whitespace=True, min_length=1, max_length=2000)


class AgentQueryResponse(BaseModel):
    query: str
    status: str
    answer: Optional[str] = None
    model: Optional[str] = None
    event_id: Optional[int] = None
    message: Optional[str] = None
    generated_at: datetime
    sources: AgentSources
    context: AgentContext
