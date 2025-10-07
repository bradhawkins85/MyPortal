from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.routes import companies, users
from app.core.config import get_settings, get_templates_config
from app.core.database import db
from app.core.logging import configure_logging, log_info

configure_logging()
settings = get_settings()
templates_config = get_templates_config()
app = FastAPI(title=settings.app_name, docs_url=settings.swagger_ui_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(origin) for origin in settings.allowed_origins] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(templates_config.template_path))
app.mount("/static", StaticFiles(directory=str(templates_config.static_path)), name="static")

app.include_router(users.router)
app.include_router(companies.router)


@app.on_event("startup")
async def on_startup() -> None:
    await db.connect()
    await db.run_migrations()
    log_info("Application started", environment=settings.environment)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await db.disconnect()
    log_info("Application shutdown")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    context = {
        "request": request,
        "app_name": settings.app_name,
        "current_year": datetime.utcnow().year,
    }
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
