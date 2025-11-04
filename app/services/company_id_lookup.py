"""Service to lookup missing company IDs from external API integrations."""

from __future__ import annotations

from typing import Any

from app.core.logging import log_error, log_info
from app.repositories import companies as company_repo
from app.services import syncro, tacticalrmm


async def lookup_missing_company_ids(company_id: int) -> dict[str, Any]:
    """
    Lookup missing external IDs for a company from Syncro, Tactical RMM, and Xero APIs.
    
    Args:
        company_id: The internal company ID to lookup missing IDs for
        
    Returns:
        Dictionary with lookup results including which IDs were found and updated
    """
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        return {
            "status": "skipped",
            "reason": "Company not found",
            "company_id": company_id,
        }
    
    company_name = company.get("name", "")
    updates: dict[str, str] = {}
    results = {
        "company_id": company_id,
        "company_name": company_name,
        "syncro_lookup": "skipped",
        "tactical_lookup": "skipped",
        "xero_lookup": "skipped",
        "updates": {},
    }
    
    # Lookup Syncro company ID if missing
    if not company.get("syncro_company_id"):
        try:
            syncro_id = await _lookup_syncro_company_id(company_name)
            if syncro_id:
                updates["syncro_company_id"] = syncro_id
                results["syncro_lookup"] = "found"
                results["updates"]["syncro_company_id"] = syncro_id
            else:
                results["syncro_lookup"] = "not_found"
        except Exception as exc:
            log_error(
                "Failed to lookup Syncro company ID",
                company_id=company_id,
                company_name=company_name,
                error=str(exc),
            )
            results["syncro_lookup"] = "error"
            results["syncro_error"] = str(exc)
    
    # Lookup Tactical RMM client ID if missing
    if not company.get("tacticalrmm_client_id"):
        try:
            tactical_id = await _lookup_tactical_client_id(company_name)
            if tactical_id:
                updates["tacticalrmm_client_id"] = tactical_id
                results["tactical_lookup"] = "found"
                results["updates"]["tacticalrmm_client_id"] = tactical_id
            else:
                results["tactical_lookup"] = "not_found"
        except Exception as exc:
            log_error(
                "Failed to lookup Tactical RMM client ID",
                company_id=company_id,
                company_name=company_name,
                error=str(exc),
            )
            results["tactical_lookup"] = "error"
            results["tactical_error"] = str(exc)
    
    # Lookup Xero contact ID if missing
    if not company.get("xero_id"):
        try:
            xero_id = await _lookup_xero_contact_id(company_name)
            if xero_id:
                updates["xero_id"] = xero_id
                results["xero_lookup"] = "found"
                results["updates"]["xero_id"] = xero_id
            else:
                results["xero_lookup"] = "not_found"
        except Exception as exc:
            log_error(
                "Failed to lookup Xero contact ID",
                company_id=company_id,
                company_name=company_name,
                error=str(exc),
            )
            results["xero_lookup"] = "error"
            results["xero_error"] = str(exc)
    
    # Apply updates if any IDs were found
    if updates:
        await company_repo.update_company(company_id, **updates)
        results["status"] = "updated"
        log_info(
            "Updated company with external IDs",
            company_id=company_id,
            company_name=company_name,
            updates=updates,
        )
    else:
        results["status"] = "no_updates"
    
    return results


async def _lookup_syncro_company_id(company_name: str) -> str | None:
    """
    Search for a Syncro customer by name and return their ID.
    
    Args:
        company_name: The company name to search for
        
    Returns:
        The Syncro customer ID if found, None otherwise
    """
    try:
        # Search through Syncro customers to find a match by name
        page = 1
        max_pages = 10  # Limit search to first 10 pages to avoid excessive API calls
        
        while page <= max_pages:
            customers, meta = await syncro.list_customers(page=page, per_page=100)
            
            for customer in customers:
                # Try different name fields
                customer_name = (
                    customer.get("business_name") or
                    customer.get("company_name") or
                    customer.get("name") or
                    ""
                )
                
                # Case-insensitive name comparison
                if customer_name.strip().lower() == company_name.strip().lower():
                    customer_id = customer.get("id")
                    if customer_id:
                        return str(customer_id)
            
            # Check if we've reached the last page
            total_pages = meta.get("total_pages")
            if total_pages and page >= total_pages:
                break
            
            # Stop if no more customers
            if not customers:
                break
                
            page += 1
    except syncro.SyncroConfigurationError:
        log_info("Syncro integration not configured, skipping lookup")
        return None
    except Exception as exc:
        log_error("Error searching Syncro customers", company_name=company_name, error=str(exc))
        raise
    
    return None


async def _lookup_tactical_client_id(company_name: str) -> str | None:
    """
    Search for a Tactical RMM client by name and return their ID.
    
    Args:
        company_name: The company name to search for
        
    Returns:
        The Tactical RMM client ID if found, None otherwise
    """
    try:
        # Fetch all agents and extract unique clients
        agents = await tacticalrmm.fetch_agents()
        
        # Build a set of clients with their IDs and names
        clients_seen = {}
        for agent in agents:
            if not isinstance(agent, dict):
                continue
                
            client_info = agent.get("client")
            if not isinstance(client_info, dict):
                continue
            
            client_id = client_info.get("id") or client_info.get("pk")
            client_name = client_info.get("name") or client_info.get("client")
            
            if client_id and client_name:
                # Store by lowercase name for case-insensitive matching
                key = str(client_name).strip().lower()
                if key not in clients_seen:
                    clients_seen[key] = str(client_id)
        
        # Look for a matching client name
        search_key = company_name.strip().lower()
        if search_key in clients_seen:
            return clients_seen[search_key]
    except tacticalrmm.TacticalRMMConfigurationError:
        log_info("Tactical RMM integration not configured, skipping lookup")
        return None
    except Exception as exc:
        log_error("Error searching Tactical RMM clients", company_name=company_name, error=str(exc))
        raise
    
    return None


async def _lookup_xero_contact_id(company_name: str) -> str | None:
    """
    Search for a Xero contact by name and return their ID.
    
    Note: Xero contact lookup is not currently implemented as the xero service
    focuses on invoice operations. This is a placeholder for future implementation.
    
    Args:
        company_name: The company name to search for
        
    Returns:
        The Xero contact ID if found, None otherwise
    """
    # Xero contact lookup not implemented yet
    # The xero service currently focuses on invoice operations
    # This would require calling the Xero contacts API
    log_info("Xero contact lookup not yet implemented, skipping")
    return None


async def refresh_all_missing_company_ids() -> dict[str, Any]:
    """
    Refresh missing external IDs for all companies in the system.
    
    Returns:
        Summary of the refresh operation including how many companies were processed
    """
    log_info("Starting refresh of all missing company IDs")
    
    companies = await company_repo.list_companies()
    summary = {
        "total_companies": len(companies),
        "processed": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "results": [],
    }
    
    for company in companies:
        company_id = company.get("id")
        if not company_id:
            continue
        
        # Skip companies that already have all IDs
        has_syncro = bool(company.get("syncro_company_id"))
        has_tactical = bool(company.get("tacticalrmm_client_id"))
        has_xero = bool(company.get("xero_id"))
        
        if has_syncro and has_tactical and has_xero:
            summary["skipped"] += 1
            continue
        
        try:
            result = await lookup_missing_company_ids(company_id)
            summary["processed"] += 1
            
            if result.get("status") == "updated":
                summary["updated"] += 1
            elif result.get("status") in ("error", "skipped"):
                summary["errors"] += 1
            
            summary["results"].append({
                "company_id": company_id,
                "company_name": company.get("name"),
                "status": result.get("status"),
                "updates": result.get("updates", {}),
            })
        except Exception as exc:
            log_error(
                "Failed to process company ID lookup",
                company_id=company_id,
                company_name=company.get("name"),
                error=str(exc),
            )
            summary["errors"] += 1
    
    log_info(
        "Completed refresh of all missing company IDs",
        total=summary["total_companies"],
        processed=summary["processed"],
        updated=summary["updated"],
        skipped=summary["skipped"],
        errors=summary["errors"],
    )
    
    return summary
