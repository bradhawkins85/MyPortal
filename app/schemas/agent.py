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


class AgentSourceCompany(BaseModel):
    id: int
    name: str
    syncro_company_id: Optional[str] = None


class AgentSourceStaff(BaseModel):
    id: int
    company_id: Optional[int] = None
    name: str
    email: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    mobile_phone: Optional[str] = None
    org_company: Optional[str] = None
    manager_name: Optional[str] = None
    account_action: Optional[str] = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_ex_staff: bool = False
    onboarding_status: Optional[str] = None
    updated_at: Optional[str] = None


class AgentSourceIssueAssignment(BaseModel):
    company_id: Optional[int] = None
    company_name: Optional[str] = None
    status: Optional[str] = None
    status_label: Optional[str] = None


class AgentSourceIssue(BaseModel):
    id: int
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    updated_at: Optional[str] = None
    assignments: list[AgentSourceIssueAssignment] = Field(default_factory=list)


class AgentSourceServiceStatus(BaseModel):
    id: int
    name: str
    status: Optional[str] = None
    status_message: Optional[str] = None
    description: Optional[str] = None


class AgentSourceBackupJob(BaseModel):
    id: int
    company_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    latest_status: Optional[str] = None
    today_status: Optional[str] = None


class AgentSourceReport(BaseModel):
    key: str
    title: str
    description: Optional[str] = None
    source_type: Optional[str] = None


class AgentSourceMailbox(BaseModel):
    user_principal_name: str
    display_name: str
    mailbox_type: Optional[str] = None
    company_id: Optional[int] = None
    storage_used_bytes: Optional[int] = None


class AgentSourceBestPractice(BaseModel):
    check_id: str
    check_name: str
    status: Optional[str] = None
    details: Optional[str] = None
    company_id: Optional[int] = None


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
    companies: list[AgentSourceCompany] = Field(default_factory=list)
    staff: list[AgentSourceStaff] = Field(default_factory=list)
    issues: list[AgentSourceIssue] = Field(default_factory=list)
    service_status: list[AgentSourceServiceStatus] = Field(default_factory=list)
    backup_jobs: list[AgentSourceBackupJob] = Field(default_factory=list)
    reports: list[AgentSourceReport] = Field(default_factory=list)
    mailboxes: list[AgentSourceMailbox] = Field(default_factory=list)
    best_practices: list[AgentSourceBestPractice] = Field(default_factory=list)
    feature_packs: dict[str, list[AgentSourceFeaturePackItem]] = Field(
        default_factory=dict
    )


class AgentContextCompany(BaseModel):
    company_id: int
    company_name: str


class AgentContextRagCandidate(BaseModel):
    source_type: str | None = None
    source_id: str | None = None
    title: str | None = None
    url: str | None = None
    excerpt: str | None = None
    score: float | None = None


class AgentContext(BaseModel):
    companies: list[AgentContextCompany] = Field(default_factory=list)
    rag_candidates: list[AgentContextRagCandidate] = Field(default_factory=list)


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
