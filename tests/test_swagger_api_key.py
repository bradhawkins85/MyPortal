"""Test Swagger UI API Key authentication configuration."""
import pytest
from app.main import app


@pytest.mark.anyio("asyncio")
async def test_openapi_schema_includes_api_key_security():
    """Verify that the OpenAPI schema includes API Key security scheme."""
    # Get the OpenAPI schema
    schema = app.openapi()
    
    # Verify security schemes are defined
    assert "components" in schema
    assert "securitySchemes" in schema["components"]
    
    # Verify API Key security scheme is configured
    security_schemes = schema["components"]["securitySchemes"]
    assert "ApiKeyAuth" in security_schemes
    
    # Verify the API Key configuration
    api_key_auth = security_schemes["ApiKeyAuth"]
    assert api_key_auth["type"] == "apiKey"
    assert api_key_auth["in"] == "header"
    assert api_key_auth["name"] == "x-api-key"
    assert "description" in api_key_auth


@pytest.mark.anyio("asyncio")
async def test_openapi_schema_has_proper_structure():
    """Verify that the OpenAPI schema has all required components."""
    schema = app.openapi()
    
    # Basic OpenAPI structure validation
    assert "openapi" in schema
    assert "info" in schema
    assert "paths" in schema
    assert "components" in schema
