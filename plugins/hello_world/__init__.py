"""Sample hello-world plugin."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.features import FeaturePack


_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
router = APIRouter(tags=["Plugin: Hello World"])


@router.get("/hello", response_class=HTMLResponse)
async def hello_plugin_page(request: Request):
    return _templates.TemplateResponse(
        request,
        "hello.html",
        {"request": request, "title": "Hello plugin"},
    )


PACK = FeaturePack(
    slug="plugin.hello_world",
    version="1.0.0",
    author="MyPortal Team",
    description="Minimal starter plugin with one route and a local template.",
    homepage="https://github.com/bradhawkins85/MyPortal",
    routers=(router,),
)
