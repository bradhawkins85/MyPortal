from __future__ import annotations

from collections.abc import Sequence
from ipaddress import ip_address, ip_network

from fastapi import Depends, HTTPException, Request, status

from app.api.dependencies.database import require_database
from app.repositories import api_keys as api_key_repo


async def require_api_key(
    request: Request,
    _: None = Depends(require_database),
) -> dict:
    api_key_value = request.headers.get("x-api-key")
    if not api_key_value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key required")
    record = await api_key_repo.get_api_key_record(api_key_value)
    if not record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        source_ip = forwarded.split(",")[0].strip()
    elif request.client:
        source_ip = request.client.host
    else:
        source_ip = ""
    permissions: Sequence[dict[str, Sequence[str]]] = record.get("permissions") or []
    if permissions:
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        method = request.method.upper()
        allowed = False
        for entry in permissions:
            path = entry.get("path")
            methods = {str(value).upper() for value in entry.get("methods", [])}
            if not path or not methods:
                continue
            if route_path == path and (method in methods or (method == "HEAD" and "GET" in methods)):
                allowed = True
                break
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key not permitted for this endpoint",
            )
    restrictions: Sequence[dict[str, str]] = record.get("ip_restrictions") or []
    if restrictions:
        try:
            client_ip = ip_address(source_ip)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key not permitted from this IP address",
            )
        allowed_ip = False
        for entry in restrictions:
            cidr = str(entry.get("cidr") or "").strip()
            if not cidr:
                continue
            try:
                network = ip_network(cidr, strict=False)
            except ValueError:
                continue
            if client_ip in network:
                allowed_ip = True
                break
        if not allowed_ip:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key not permitted from this IP address",
            )
    await api_key_repo.record_api_key_usage(record["id"], source_ip or "unknown")
    request.state.api_key_id = record["id"]
    return record
