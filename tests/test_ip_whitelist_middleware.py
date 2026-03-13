"""Tests for IP whitelisting middleware."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.ip_whitelist import IPWhitelistMiddleware


@pytest.fixture
def test_app():
    """Create a test FastAPI app with IP whitelist middleware."""
    app = FastAPI()
    
    @app.get("/public")
    def public_endpoint():
        return {"message": "public"}
    
    @app.get("/admin/users")
    def admin_endpoint():
        return {"message": "admin"}
    
    @app.get("/api/data")
    def api_endpoint():
        return {"message": "api"}
    
    return app


def test_ip_whitelist_disabled(test_app):
    """Test that middleware passes through when disabled."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=False,
        whitelist=["192.168.1.1"],
        protected_paths=["/admin"],
    )
    
    client = TestClient(test_app)
    
    # Should allow access even from non-whitelisted IP
    response = client.get("/admin/users")
    assert response.status_code == 200


def test_ip_whitelist_allows_whitelisted_ip(test_app):
    """Test that whitelisted IPs can access protected paths."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=True,
        whitelist=["192.168.1.100"],
        protected_paths=["/admin"],
    )

    client = TestClient(test_app, client=("192.168.1.100", 50000))
    response = client.get("/admin/users")
    assert response.status_code == 200


def test_ip_whitelist_blocks_non_whitelisted_ip(test_app):
    """Test that non-whitelisted IPs are blocked from protected paths."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=True,
        whitelist=["192.168.1.100"],
        protected_paths=["/admin"],
    )

    client = TestClient(test_app, client=("10.0.0.50", 50000))
    response = client.get("/admin/users")
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"].lower()


def test_ip_whitelist_allows_cidr_range(test_app):
    """Test that CIDR ranges work correctly."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=True,
        whitelist=["192.168.1.0/24"],  # Allows 192.168.1.0 - 192.168.1.255
        protected_paths=["/admin"],
    )
    
    client = TestClient(test_app, client=("192.168.1.150", 50000))

    # IP in range should be allowed
    response = client.get("/admin/users")
    assert response.status_code == 200
    
    # IP outside range should be blocked
    blocked_client = TestClient(test_app, client=("192.168.2.150", 50000))
    response = blocked_client.get("/admin/users")
    assert response.status_code == 403


def test_ip_whitelist_exempt_paths(test_app):
    """Test that exempt paths are not protected."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=True,
        whitelist=["192.168.1.100"],
        protected_paths=["/admin", "/api"],
        exempt_paths=["/public"],
    )
    
    client = TestClient(test_app, client=("10.0.0.50", 50000))

    # Public path should be accessible from any IP
    response = client.get("/public")
    assert response.status_code == 200
    
    # Protected path should be blocked
    response = client.get("/admin/users")
    assert response.status_code == 403


def test_ip_whitelist_empty_whitelist_allows_all(test_app):
    """Test that empty whitelist allows all IPs."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=True,
        whitelist=[],  # Empty whitelist
        protected_paths=["/admin"],
    )
    
    client = TestClient(test_app)
    
    # Should allow any IP when whitelist is empty
    response = client.get("/admin/users")
    assert response.status_code == 200


def test_ip_whitelist_ignores_spoofed_proxy_headers(test_app):
    """Test that proxy headers do not bypass whitelist checks."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=True,
        whitelist=["192.168.1.100"],
        protected_paths=["/admin"],
    )

    client = TestClient(test_app, client=("10.0.0.50", 50000))
    response = client.get(
        "/admin/users",
        headers={
            "CF-Connecting-IP": "192.168.1.100",
            "X-Forwarded-For": "192.168.1.100",
        },
    )
    assert response.status_code == 403


def test_ip_whitelist_ipv6_support(test_app):
    """Test that IPv6 addresses are supported."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=True,
        whitelist=["2001:db8::/32"],  # IPv6 CIDR range
        protected_paths=["/admin"],
    )
    
    client = TestClient(test_app, client=("2001:db8::1", 50000))

    # IPv6 address in range should be allowed
    response = client.get("/admin/users")
    assert response.status_code == 200
    
    # IPv6 address outside range should be blocked
    blocked_client = TestClient(test_app, client=("2001:db9::1", 50000))
    response = blocked_client.get("/admin/users")
    assert response.status_code == 403


def test_ip_whitelist_multiple_ips(test_app):
    """Test that multiple IPs/ranges in whitelist work correctly."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=True,
        whitelist=["192.168.1.100", "10.0.0.0/8", "172.16.0.1"],
        protected_paths=["/admin"],
    )
    
    # Each whitelisted IP/range should work
    for ip in ["192.168.1.100", "10.5.10.20", "172.16.0.1"]:
        client = TestClient(test_app, client=(ip, 50000))
        response = client.get("/admin/users")
        assert response.status_code == 200, f"IP {ip} should be allowed"

    # Non-whitelisted IP should be blocked
    blocked_client = TestClient(test_app, client=("192.168.2.100", 50000))
    response = blocked_client.get("/admin/users")
    assert response.status_code == 403


def test_ip_whitelist_invalid_ip_format(test_app):
    """Test that invalid IP addresses are handled gracefully."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=True,
        whitelist=["192.168.1.100"],
        protected_paths=["/admin"],
    )
    
    client = TestClient(test_app, client=("not-an-ip", 50000))

    # Invalid IP format should be rejected
    response = client.get("/admin/users")
    assert response.status_code == 403


def test_ip_whitelist_protected_paths_only(test_app):
    """Test that only specified paths are protected."""
    test_app.add_middleware(
        IPWhitelistMiddleware,
        enabled=True,
        whitelist=["192.168.1.100"],
        protected_paths=["/admin"],  # Only protect /admin
    )
    
    client = TestClient(test_app, client=("10.0.0.50", 50000))

    # Non-protected path should be accessible from any IP
    response = client.get("/api/data")
    assert response.status_code == 200
    
    # Protected path should be blocked
    response = client.get("/admin/users")
    assert response.status_code == 403
