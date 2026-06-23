from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

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


class AgentSourcePackage(BaseModel):
    id: Optional[int] = None
    name: str
    sku: Optional[str] = None
    description: Optional[str] = None
    product_count: Optional[int] = None


class AgentSourceChat(BaseModel):
    id: int
    subject: str
    status: str
    company_id: Optional[int] = None
    updated_at: Optional[str] = None
    linked_ticket_id: Optional[int] = None
    summary: Optional[str] = None


class AgentSourceOrder(BaseModel):
    order_number: str
    company_id: Optional[int] = None
    status: str
    shipping_status: Optional[str] = None
    po_number: Optional[str] = None
    consignment_id: Optional[str] = None
    order_date: Optional[str] = None
    item_count: Optional[int] = None
    summary: Optional[str] = None


class AgentSourceAsset(BaseModel):
    id: int
    company_id: Optional[int] = None
    name: str
    type: Optional[str] = None
    serial_number: Optional[str] = None
    status: Optional[str] = None
    os_name: Optional[str] = None
    last_user: Optional[str] = None
    warranty_status: Optional[str] = None
    last_sync: Optional[str] = None


class AgentSourceFeaturePackItem(BaseModel):
    title: str
    summary: Optional[str] = None
    url: Optional[str] = None
    source_type: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentSources(BaseModel):
    knowledge_base: list[AgentSourceArticle] = Field(default_factory=list)
    tickets: list[AgentSourceTicket] = Field(default_factory=list)
    products: list[AgentSourceProduct] = Field(default_factory=list)
    packages: list[AgentSourcePackage] = Field(default_factory=list)
    chats: list[AgentSourceChat] = Field(default_factory=list)
    orders: list[AgentSourceOrder] = Field(default_factory=list)
    assets: list[AgentSourceAsset] = Field(default_factory=list)
    feature_packs: dict[str, list[AgentSourceFeaturePackItem]] = Field(
        default_factory=dict
    )


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
    has_relevant_sources: bool = True
    sources: AgentSources
    context: AgentContext
