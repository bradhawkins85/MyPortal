"""Tests for the cache control middleware."""
from __future__ import annotations

import os
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.security.cache_control import CacheControlMiddleware


def test_cache_control_disabled_by_default():
    """Test that cache control headers are not added when DISABLE_CACHING is false."""
    app = FastAPI()
    app.add_middleware(CacheControlMiddleware)

    @app.get("/test")
    async def test_endpoint():
        return JSONResponse({"message": "test"})

    client = TestClient(app)
    
    with patch.dict(os.environ, {"DISABLE_CACHING": "false"}):
        # Force settings reload
        from app.core.config import get_settings
        get_settings.cache_clear()
        
        response = client.get("/test")
        
        assert response.status_code == 200
        assert "Cache-Control" not in response.headers or "no-store" not in response.headers.get("Cache-Control", "")
        assert "Pragma" not in response.headers


def test_cache_control_enabled():
    """Test that cache control headers are added when DISABLE_CACHING is true."""
    app = FastAPI()
    app.add_middleware(CacheControlMiddleware)

    @app.get("/test")
    async def test_endpoint():
        return JSONResponse({"message": "test"})

    client = TestClient(app)
    
    with patch.dict(os.environ, {"DISABLE_CACHING": "true"}):
        # Force settings reload
        from app.core.config import get_settings
        get_settings.cache_clear()
        
        response = client.get("/test")
        
        assert response.status_code == 200
        assert "Cache-Control" in response.headers
        assert "no-store" in response.headers["Cache-Control"]
        assert "no-cache" in response.headers["Cache-Control"]
        assert "must-revalidate" in response.headers["Cache-Control"]
        assert "private" in response.headers["Cache-Control"]
        assert "max-age=0" in response.headers["Cache-Control"]
        assert response.headers.get("Pragma") == "no-cache"
        assert response.headers.get("Expires") == "0"


def test_cache_control_exempt_paths():
    """Test that exempt paths do not receive cache control headers."""
    app = FastAPI()
    app.add_middleware(CacheControlMiddleware, exempt_paths=("/static",))

    @app.get("/static/test.js")
    async def static_endpoint():
        return JSONResponse({"message": "static"})

    @app.get("/dynamic")
    async def dynamic_endpoint():
        return JSONResponse({"message": "dynamic"})

    client = TestClient(app)
    
    with patch.dict(os.environ, {"DISABLE_CACHING": "true"}):
        # Force settings reload
        from app.core.config import get_settings
        get_settings.cache_clear()
        
        # Static path should not have cache control headers
        static_response = client.get("/static/test.js")
        assert static_response.status_code == 200
        assert "no-store" not in static_response.headers.get("Cache-Control", "")
        
        # Dynamic path should have cache control headers
        dynamic_response = client.get("/dynamic")
        assert dynamic_response.status_code == 200
        assert "Cache-Control" in dynamic_response.headers
        assert "no-store" in dynamic_response.headers["Cache-Control"]


def test_cache_control_with_multiple_paths():
    """Test that cache control works correctly with multiple endpoints."""
    app = FastAPI()
    app.add_middleware(CacheControlMiddleware, exempt_paths=("/static", "/public"))

    @app.get("/api/data")
    async def api_endpoint():
        return JSONResponse({"data": "api"})

    @app.get("/public/info")
    async def public_endpoint():
        return JSONResponse({"data": "public"})

    @app.get("/static/file.css")
    async def static_endpoint():
        return JSONResponse({"data": "static"})

    client = TestClient(app)
    
    with patch.dict(os.environ, {"DISABLE_CACHING": "true"}):
        # Force settings reload
        from app.core.config import get_settings
        get_settings.cache_clear()
        
        # API endpoint should have cache control
        api_response = client.get("/api/data")
        assert api_response.status_code == 200
        assert "no-store" in api_response.headers.get("Cache-Control", "")
        
        # Public endpoint should not have cache control
        public_response = client.get("/public/info")
        assert public_response.status_code == 200
        assert "no-store" not in public_response.headers.get("Cache-Control", "")
        
        # Static endpoint should not have cache control
        static_response = client.get("/static/file.css")
        assert static_response.status_code == 200
        assert "no-store" not in static_response.headers.get("Cache-Control", "")
