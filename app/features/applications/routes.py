from __future__ import annotations

from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.api.dependencies.auth import get_current_session, get_current_user
from app.repositories import applications as repo
from app.repositories import user_companies as user_company_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.security.session import SessionData
from app.services import audit as audit_service
from app.services import knowledge_base as knowledge_base_service

MENU_KEY = "menu.applications"
MAX_EXTERNAL_LINKS = 50

router = APIRouter(prefix="/api/applications", tags=["Applications"])
web_router = APIRouter(tags=["Applications"])


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class ExternalLink(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("url")
    @classmethod
    def http_url(cls, value: str) -> str:
        value = value.strip()
        parts = urlsplit(value)
        # Only web links are stored so a link can never run script when opened.
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            raise ValueError("KB links must be http:// or https:// addresses")
        return value


class ApplicationInput(BaseModel):
    name: str = Field(min_length=1, max_length=191)
    type_id: int | None = None
    type_name: str | None = Field(default=None, max_length=191)
    version: str | None = Field(default=None, max_length=100)
    business_impact: str | None = Field(default=None, max_length=20000)
    importance: str = "medium"
    champion_staff_id: int | None = None
    champion_name: str | None = Field(default=None, max_length=255)
    product_key: str | None = Field(default=None, max_length=1000)
    clear_product_key: bool = False
    notes: str | None = Field(default=None, max_length=20000)
    knowledge_base_article_ids: list[int] = Field(default_factory=list, max_length=100)
    external_links: list[ExternalLink] = Field(default_factory=list, max_length=MAX_EXTERNAL_LINKS)

    @field_validator("name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name is required")
        return value

    @field_validator("type_name", "version", "business_impact", "champion_name", "product_key", "notes")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("importance")
    @classmethod
    def known_importance(cls, value: str) -> str:
        value = (value or "").strip().lower()
        if value not in repo.IMPORTANCE_KEYS:
            raise ValueError("Unknown importance level")
        return value


class TypeInput(BaseModel):
    name: str = Field(min_length=1, max_length=191)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Type name is required")
        return value


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------


async def access_context(user: dict = Depends(get_current_user),
                         session: SessionData = Depends(get_current_session)):
    company_id = session.active_company_id or user.get("company_id")
    if company_id is None:
        raise HTTPException(status_code=400, detail="An active company is required")
    company_id = int(company_id)
    membership = await user_company_repo.get_user_company(int(user["id"]), company_id)
    from app import main as main_module
    if not main_module._membership_menu_can(user, membership, MENU_KEY):
        raise HTTPException(status_code=403, detail="Application access required")
    return user, company_id, membership


def require_write(context):
    user, company_id, membership = context
    from app import main as main_module
    if not main_module._membership_menu_can(user, membership, MENU_KEY, write=True):
        raise HTTPException(status_code=403, detail="Application write access required")
    return user, company_id


async def _web_context(request: Request, *, write: bool = False):
    from app import main as main_module
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return None, None, None, redirect
    company_id = user.get("company_id")
    if company_id is None:
        raise HTTPException(status_code=400, detail="An active company is required")
    company_id = int(company_id)
    membership = await main_module._get_effective_company_membership(request, user["id"], company_id)
    if not main_module._membership_menu_can(user, membership, MENU_KEY, write=write):
        raise HTTPException(status_code=403, detail="Application access required")
    return user, company_id, membership, None


async def _accessible_articles(user: dict) -> list[dict]:
    access = await knowledge_base_service.build_access_context(user)
    return await knowledge_base_service.list_articles_for_context(
        access, include_unpublished=bool(user.get("is_super_admin"))
    )


async def _visible_links(user: dict, company_id: int, application_id: int) -> dict:
    links = await repo.get_links(company_id, application_id)
    visible_ids = {int(article["id"]) for article in await _accessible_articles(user)}
    links["articles"] = [article for article in links["articles"] if int(article["id"]) in visible_ids]
    return links


# ---------------------------------------------------------------------------
# Shared save logic
# ---------------------------------------------------------------------------


def parse_external_links(text: str | None) -> list[dict[str, str | None]]:
    """Parse one KB link per line, optionally written as ``Title | URL``."""
    links: list[dict[str, str | None]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        title, separator, url = line.rpartition("|")
        if not separator:
            title, url = "", line
        links.append({"title": title.strip() or None, "url": url.strip()})
    return links


def _form_payload(form) -> ApplicationInput:
    def optional_id(name: str) -> int | None:
        value = (form.get(name) or "").strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid linked record") from exc

    try:
        article_ids = [int(value) for value in form.getlist("knowledge_base_article_ids") if value]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid linked record") from exc
    try:
        return ApplicationInput(
            name=form.get("name", ""),
            type_id=optional_id("type_id"),
            type_name=form.get("type_name") or None,
            version=form.get("version") or None,
            business_impact=form.get("business_impact") or None,
            importance=form.get("importance") or "medium",
            champion_staff_id=optional_id("champion_staff_id"),
            champion_name=form.get("champion_name") or None,
            product_key=form.get("product_key") or None,
            clear_product_key="clear_product_key" in form,
            notes=form.get("notes") or None,
            knowledge_base_article_ids=article_ids,
            external_links=parse_external_links(form.get("external_links")),
        )
    except ValidationError as exc:
        messages = "; ".join(str(error.get("msg", "")).removeprefix("Value error, ") for error in exc.errors())
        raise HTTPException(status_code=422, detail=f"Application fields are invalid: {messages}") from exc


async def _resolve_values(company_id: int, payload: ApplicationInput) -> dict:
    values = payload.model_dump(exclude={"product_key", "clear_product_key", "type_name",
                                         "knowledge_base_article_ids", "external_links"})
    if payload.type_name:
        values["type_id"] = await repo.get_or_create_type(company_id, payload.type_name)
    elif payload.type_id is not None and not await repo.get_type(company_id, payload.type_id):
        raise HTTPException(status_code=422, detail="Application type does not belong to the active company")
    if payload.champion_staff_id is not None:
        if not await repo.staff_belongs_to_company(company_id, payload.champion_staff_id):
            raise HTTPException(status_code=422, detail="Champion does not belong to the active company")
        # A linked staff member is the champion; a free-text name would only conflict.
        values["champion_name"] = None
    return values


async def _article_ids_to_store(user: dict, company_id: int, application_id: int | None,
                                requested: list[int]) -> list[int]:
    """Validate requested articles and keep links the editor cannot see.

    An editor can only add articles they may open, and saving must not drop
    links another user made to articles hidden from this editor.
    """
    accessible = {int(article["id"]) for article in await _accessible_articles(user)}
    if any(article_id not in accessible for article_id in requested):
        raise HTTPException(status_code=422, detail="Linked article is not available to you")
    hidden: list[int] = []
    if application_id:
        existing = await repo.get_links(company_id, application_id)
        hidden = [int(a["id"]) for a in existing["articles"] if int(a["id"]) not in accessible]
    return list(dict.fromkeys([*requested, *hidden]))


async def _save(request: Request, user: dict, company_id: int, payload: ApplicationInput,
                application_id: int | None) -> int:
    values = await _resolve_values(company_id, payload)
    article_ids = await _article_ids_to_store(user, company_id, application_id, payload.knowledge_base_article_ids)
    external = [link.model_dump() for link in payload.external_links]
    encrypted_key = encrypt_secret(payload.product_key) if payload.product_key else None
    if application_id:
        await repo.update_application(
            company_id, application_id, values, product_key=encrypted_key,
            clear_product_key=payload.clear_product_key,
        )
        action = "application.update"
    else:
        values["product_key_encrypted"] = encrypted_key
        application_id = await repo.create_application(company_id, values, int(user["id"]))
        action = "application.create"
    await repo.replace_links(company_id, application_id, article_ids, external)
    # The product key is never written to the audit trail, only whether it changed.
    await audit_service.record(
        action=action, request=request, user_id=int(user["id"]),
        entity_type="application", entity_id=application_id,
        after={
            "company_id": company_id, "name": payload.name, "importance": payload.importance,
            "product_key_changed": bool(encrypted_key) or payload.clear_product_key,
        },
    )
    return application_id


# ---------------------------------------------------------------------------
# Web pages
# ---------------------------------------------------------------------------


@web_router.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request):
    from app import main as main_module
    user, company_id, membership, redirect = await _web_context(request)
    if redirect:
        return redirect
    rows = await repo.list_applications(company_id)
    types = await repo.list_types(company_id)
    total_count = len(rows)
    importance_counts = {key: 0 for key in repo.IMPORTANCE_KEYS}
    for row in rows:
        if row["importance"] in importance_counts:
            importance_counts[row["importance"]] += 1
    importance_filter = request.query_params.get("importance", "")
    if importance_filter in repo.IMPORTANCE_KEYS:
        rows = [row for row in rows if row["importance"] == importance_filter]
    type_filter = request.query_params.get("type", "")
    if type_filter:
        rows = [row for row in rows if str(row.get("type_id") or "") == type_filter]
    can_write = main_module._membership_menu_can(user, membership, MENU_KEY, write=True)
    return await main_module._render_template("applications/index.html", request, user, extra={
        "title": "Applications", "applications": rows, "can_write": can_write,
        "type_options": [(str(item["id"]), item["name"]) for item in types],
        "importance_levels": list(repo.IMPORTANCE_LEVELS),
        "total_count": total_count, "importance_counts": importance_counts,
    })


@web_router.get("/applications/types", response_class=HTMLResponse)
async def application_types_page(request: Request):
    from app import main as main_module
    user, company_id, membership, redirect = await _web_context(request)
    if redirect:
        return redirect
    can_write = main_module._membership_menu_can(user, membership, MENU_KEY, write=True)
    return await main_module._render_template("applications/types.html", request, user, extra={
        "title": "Application types", "types": await repo.list_types(company_id), "can_write": can_write,
    })


@web_router.post("/applications/types")
async def application_type_create(request: Request):
    from app import main as main_module
    user, company_id, _membership, redirect = await _web_context(request, write=True)
    if redirect:
        return redirect
    form = await request.form()
    try:
        payload = TypeInput(name=form.get("name", ""))
    except ValidationError:
        return main_module.flash_redirect("/applications/types", "Enter a type name.", "error")
    type_id = await repo.get_or_create_type(company_id, payload.name)
    await audit_service.record(action="application_type.create", request=request, user_id=int(user["id"]),
                               entity_type="application_type", entity_id=type_id, after={"name": payload.name})
    return main_module.flash_redirect("/applications/types", "Application type saved.", "success")


@web_router.post("/applications/types/{type_id}/delete")
async def application_type_delete(request: Request, type_id: int):
    from app import main as main_module
    user, company_id, _membership, redirect = await _web_context(request, write=True)
    if redirect:
        return redirect
    existing = await repo.get_type(company_id, type_id)
    if not existing or not await repo.delete_type(company_id, type_id):
        raise HTTPException(status_code=404, detail="Application type not found")
    await audit_service.record(action="application_type.delete", request=request, user_id=int(user["id"]),
                               entity_type="application_type", entity_id=type_id, before={"name": existing["name"]})
    return main_module.flash_redirect("/applications/types", "Application type deleted.", "success")


@web_router.get("/applications/new", response_class=HTMLResponse)
@web_router.get("/applications/{application_id}/edit", response_class=HTMLResponse)
async def application_form_page(request: Request, application_id: int | None = None):
    from app import main as main_module
    user, company_id, _membership, redirect = await _web_context(request, write=True)
    if redirect:
        return redirect
    application = await repo.get_application(company_id, application_id) if application_id else None
    if application_id and not application:
        raise HTTPException(status_code=404, detail="Application not found")
    links = await repo.get_links(company_id, application_id) if application_id else {"articles": [], "external": []}
    return await main_module._render_template("applications/form.html", request, user, extra={
        "title": "Edit application" if application else "Create application",
        "application": application, "links": links,
        "types": await repo.list_types(company_id),
        "staff": await repo.list_champion_choices(company_id),
        "articles": await _accessible_articles(user),
        "importance_levels": repo.IMPORTANCE_LEVELS,
    })


@web_router.post("/applications/save")
async def application_save(request: Request):
    user, company_id, _membership, redirect = await _web_context(request, write=True)
    if redirect:
        return redirect
    form = await request.form()
    payload = _form_payload(form)
    try:
        application_id = int(form["application_id"]) if form.get("application_id") else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid application identifier") from exc
    if application_id and not await repo.get_application(company_id, application_id):
        raise HTTPException(status_code=404, detail="Application not found")
    application_id = await _save(request, user, company_id, payload, application_id)
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@web_router.get("/applications/{application_id}", response_class=HTMLResponse)
async def application_detail_page(request: Request, application_id: int):
    from app import main as main_module
    user, company_id, membership, redirect = await _web_context(request)
    if redirect:
        return redirect
    application = await repo.get_application(company_id, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    can_write = main_module._membership_menu_can(user, membership, MENU_KEY, write=True)
    return await main_module._render_template("applications/detail.html", request, user, extra={
        "title": application["name"], "application": application,
        "links": await _visible_links(user, company_id, application_id),
        "importance_labels": dict(repo.IMPORTANCE_LEVELS), "can_write": can_write,
    })


@web_router.post("/applications/{application_id}/delete")
async def application_delete(request: Request, application_id: int):
    user, company_id, _membership, redirect = await _web_context(request, write=True)
    if redirect:
        return redirect
    application = await repo.get_application(company_id, application_id)
    if not application or not await repo.delete_application(company_id, application_id):
        raise HTTPException(status_code=404, detail="Application not found")
    await audit_service.record(
        action="application.delete", request=request, user_id=int(user["id"]),
        entity_type="application", entity_id=application_id,
        before={"company_id": company_id, "name": application["name"]},
    )
    return RedirectResponse("/applications?deleted=1", status_code=303)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@router.get("", summary="List applications for the active company")
async def list_applications(context=Depends(access_context)):
    return await repo.list_applications(context[1])


@router.get("/types", summary="List application types for the active company")
async def list_application_types(context=Depends(access_context)):
    return await repo.list_types(context[1])


@router.post("/types", status_code=status.HTTP_201_CREATED, summary="Create an application type")
async def create_application_type(payload: TypeInput, request: Request, context=Depends(access_context)):
    user, company_id = require_write(context)
    type_id = await repo.get_or_create_type(company_id, payload.name)
    await audit_service.record(action="application_type.create", request=request, user_id=int(user["id"]),
                               entity_type="application_type", entity_id=type_id, after={"name": payload.name})
    return {"id": type_id, "name": payload.name}


@router.get("/{application_id}", summary="Get an application")
async def get_application(application_id: int, context=Depends(access_context)):
    user, company_id, _membership = context
    application = await repo.get_application(company_id, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return {**application, **await _visible_links(user, company_id, application_id)}


@router.get("/{application_id}/product-key", summary="Reveal an application's product key")
async def reveal_product_key(application_id: int, request: Request, context=Depends(access_context)):
    user, company_id, _membership = context
    if not await repo.get_application(company_id, application_id):
        raise HTTPException(status_code=404, detail="Application not found")
    ciphertext = await repo.get_product_key_ciphertext(company_id, application_id)
    if not ciphertext:
        raise HTTPException(status_code=404, detail="No product key is stored")
    try:
        product_key = decrypt_secret(ciphertext, field="applications.product_key")
    except (ValueError, InvalidTag) as exc:
        raise HTTPException(status_code=409, detail="The stored product key could not be decrypted") from exc
    await audit_service.record(action="application.product_key.view", request=request, user_id=int(user["id"]),
                               entity_type="application", entity_id=application_id)
    return {"product_key": product_key}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create an application")
async def create_application(payload: ApplicationInput, request: Request, context=Depends(access_context)):
    user, company_id = require_write(context)
    application_id = await _save(request, user, company_id, payload, None)
    return {"id": application_id}


@router.put("/{application_id}", summary="Update an application")
async def update_application(application_id: int, payload: ApplicationInput, request: Request,
                             context=Depends(access_context)):
    user, company_id = require_write(context)
    if not await repo.get_application(company_id, application_id):
        raise HTTPException(status_code=404, detail="Application not found")
    await _save(request, user, company_id, payload, application_id)
    return await repo.get_application(company_id, application_id)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an application")
async def delete_application(application_id: int, request: Request, context=Depends(access_context)):
    user, company_id = require_write(context)
    if not await repo.delete_application(company_id, application_id):
        raise HTTPException(status_code=404, detail="Application not found")
    await audit_service.record(action="application.delete", request=request, user_id=int(user["id"]),
                               entity_type="application", entity_id=application_id)
