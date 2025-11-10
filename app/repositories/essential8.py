from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.core.database import db
from app.schemas.essential8 import ComplianceStatus, MaturityLevel


def _format_datetime(value: Any) -> Optional[str]:
    """Convert datetime values to ISO formatted strings."""

    if value is None:
        return None

    if isinstance(value, datetime):
        # Ensure timezone aware values are normalised to UTC
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(timezone.utc).isoformat()

    return value  # type: ignore[return-value]


def _build_compliance_record(row: Any) -> dict[str, Any]:
    """Normalise a compliance record row returned from the database."""

    item = dict(row)

    item["control"] = {
        "id": row["control_id"],
        "name": row["control_name"],
        "description": row["control_description"] or "",
        "control_order": row["control_order"],
    }

    for key in ["control_name", "control_description"]:
        item.pop(key, None)

    for key in ("created_at", "updated_at"):
        item[key] = _format_datetime(item.get(key))

    return item


async def list_essential8_controls() -> list[dict[str, Any]]:
    """List all Essential 8 controls"""
    query = """
        SELECT id, name, description, control_order, created_at, updated_at
        FROM essential8_controls
        ORDER BY control_order
    """
    return await db.fetch_all(query)


async def get_essential8_control(control_id: int) -> Optional[dict[str, Any]]:
    """Get a specific Essential 8 control"""
    query = """
        SELECT id, name, description, control_order, created_at, updated_at
        FROM essential8_controls
        WHERE id = %(control_id)s
    """
    return await db.fetch_one(query, {"control_id": control_id})


async def list_company_compliance(
    company_id: int,
    status: Optional[ComplianceStatus] = None,
) -> list[dict[str, Any]]:
    """List compliance records for a company"""
    params: dict[str, Any] = {"company_id": company_id}

    where_clauses = ["cec.company_id = %(company_id)s"]
    if status:
        where_clauses.append("cec.status = %(status)s")
        params["status"] = status.value
    
    where_clause = " AND ".join(where_clauses)
    
    query = f"""
        SELECT 
            cec.id, cec.company_id, cec.control_id, cec.status, 
            cec.maturity_level, cec.evidence, cec.notes,
            cec.last_reviewed_date, cec.target_compliance_date,
            cec.created_at, cec.updated_at,
            ec.id as control_id, ec.name as control_name, 
            ec.description as control_description, ec.control_order
        FROM company_essential8_compliance cec
        INNER JOIN essential8_controls ec ON cec.control_id = ec.id
        WHERE {where_clause}
        ORDER BY ec.control_order
    """
    rows = await db.fetch_all(query, params)
    
    # Transform the flat rows into nested structure
    return [_build_compliance_record(row) for row in rows]


async def get_company_compliance(
    company_id: int,
    control_id: int,
) -> Optional[dict[str, Any]]:
    """Get a specific compliance record for a company and control"""
    query = """
        SELECT
            cec.id, cec.company_id, cec.control_id, cec.status,
            cec.maturity_level, cec.evidence, cec.notes,
            cec.last_reviewed_date, cec.target_compliance_date,
            cec.created_at, cec.updated_at,
            ec.id as control_id, ec.name as control_name,
            ec.description as control_description, ec.control_order
        FROM company_essential8_compliance cec
        INNER JOIN essential8_controls ec ON cec.control_id = ec.id
        WHERE cec.company_id = %(company_id)s AND cec.control_id = %(control_id)s
    """
    row = await db.fetch_one(query, {"company_id": company_id, "control_id": control_id})
    
    if not row:
        return None
    
    return _build_compliance_record(row)


async def create_company_compliance(
    company_id: int,
    control_id: int,
    status: ComplianceStatus = ComplianceStatus.NOT_STARTED,
    maturity_level: MaturityLevel = MaturityLevel.ML0,
    evidence: Optional[str] = None,
    notes: Optional[str] = None,
    last_reviewed_date: Optional[str] = None,
    target_compliance_date: Optional[str] = None,
) -> dict[str, Any]:
    """Create a compliance record for a company"""
    query = """
        INSERT INTO company_essential8_compliance
        (company_id, control_id, status, maturity_level, evidence, notes,
         last_reviewed_date, target_compliance_date)
        VALUES (%(company_id)s, %(control_id)s, %(status)s, %(maturity_level)s, %(evidence)s,
                %(notes)s, %(last_reviewed_date)s, %(target_compliance_date)s)
    """
    params = {
        "company_id": company_id,
        "control_id": control_id,
        "status": status.value if isinstance(status, ComplianceStatus) else status,
        "maturity_level": maturity_level.value if isinstance(maturity_level, MaturityLevel) else maturity_level,
        "evidence": evidence,
        "notes": notes,
        "last_reviewed_date": last_reviewed_date,
        "target_compliance_date": target_compliance_date,
    }
    
    record_id = await db.execute(query, params)
    
    # Return the created record
    result = await get_company_compliance(company_id, control_id)
    if result:
        return result
    
    return {"id": record_id, **params}


async def update_company_compliance(
    company_id: int,
    control_id: int,
    user_id: Optional[int] = None,
    **updates: Any,
) -> Optional[dict[str, Any]]:
    """Update a compliance record for a company"""
    # First get the current record for audit trail
    current = await get_company_compliance(company_id, control_id)
    if not current:
        return None
    
    # Build the update query
    set_clauses = []
    params: dict[str, Any] = {"company_id": company_id, "control_id": control_id}
    
    for key, value in updates.items():
        if value is not None:
            set_clauses.append(f"{key} = %({key})s")
            # Handle enum values
            if isinstance(value, (ComplianceStatus, MaturityLevel)):
                params[key] = value.value
            else:
                params[key] = value
    
    if not set_clauses:
        return current
    
    set_clause = ", ".join(set_clauses)
    query = f"""
        UPDATE company_essential8_compliance
        SET {set_clause}
        WHERE company_id = %(company_id)s AND control_id = %(control_id)s
    """
    
    await db.execute(query, params)
    
    # Create audit trail
    await create_compliance_audit(
        compliance_id=current["id"],
        company_id=company_id,
        control_id=control_id,
        user_id=user_id,
        action="update",
        old_status=current.get("status"),
        new_status=updates.get("status"),
        old_maturity_level=current.get("maturity_level"),
        new_maturity_level=updates.get("maturity_level"),
        notes=updates.get("notes"),
    )
    
    # Return the updated record
    return await get_company_compliance(company_id, control_id)


async def initialize_company_compliance(company_id: int) -> int:
    """Initialize compliance records for all Essential 8 controls for a company"""
    controls = await list_essential8_controls()
    created_count = 0
    
    for control in controls:
        # Check if record already exists
        existing = await get_company_compliance(company_id, control["id"])
        if not existing:
            await create_company_compliance(
                company_id=company_id,
                control_id=control["id"],
            )
            created_count += 1
    
    return created_count


async def get_company_compliance_summary(company_id: int) -> dict[str, Any]:
    """Get a summary of compliance status for a company"""
    query = """
        SELECT
            COUNT(*) as total_controls,
            SUM(CASE WHEN status = 'not_started' THEN 1 ELSE 0 END) as not_started,
            SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
            SUM(CASE WHEN status = 'compliant' THEN 1 ELSE 0 END) as compliant,
            SUM(CASE WHEN status = 'non_compliant' THEN 1 ELSE 0 END) as non_compliant,
            AVG(
                CASE maturity_level
                    WHEN 'ml0' THEN 0
                    WHEN 'ml1' THEN 1
                    WHEN 'ml2' THEN 2
                    WHEN 'ml3' THEN 3
                    ELSE 0
                END
            ) as avg_maturity
        FROM company_essential8_compliance
        WHERE company_id = %(company_id)s
    """
    row = await db.fetch_one(query, {"company_id": company_id})
    
    if not row or row["total_controls"] == 0:
        return {
            "company_id": company_id,
            "total_controls": 8,
            "not_started": 0,
            "in_progress": 0,
            "compliant": 0,
            "non_compliant": 0,
            "compliance_percentage": 0.0,
            "average_maturity_level": 0.0,
        }
    
    total = row["total_controls"] or 0
    compliant = row["compliant"] or 0
    compliance_percentage = (compliant / total * 100) if total > 0 else 0.0
    
    return {
        "company_id": company_id,
        "total_controls": total,
        "not_started": row["not_started"] or 0,
        "in_progress": row["in_progress"] or 0,
        "compliant": compliant,
        "non_compliant": row["non_compliant"] or 0,
        "compliance_percentage": round(compliance_percentage, 2),
        "average_maturity_level": round(row["avg_maturity"] or 0, 2),
    }


async def create_compliance_audit(
    compliance_id: int,
    company_id: int,
    control_id: int,
    user_id: Optional[int],
    action: str,
    old_status: Optional[str] = None,
    new_status: Optional[ComplianceStatus] = None,
    old_maturity_level: Optional[str] = None,
    new_maturity_level: Optional[MaturityLevel] = None,
    notes: Optional[str] = None,
) -> int:
    """Create an audit trail entry for compliance changes"""
    query = """
        INSERT INTO company_essential8_audit
        (compliance_id, company_id, control_id, user_id, action,
         old_status, new_status, old_maturity_level, new_maturity_level, notes)
        VALUES (%(compliance_id)s, %(company_id)s, %(control_id)s, %(user_id)s, %(action)s,
                %(old_status)s, %(new_status)s, %(old_maturity_level)s, %(new_maturity_level)s, %(notes)s)
    """
    params = {
        "compliance_id": compliance_id,
        "company_id": company_id,
        "control_id": control_id,
        "user_id": user_id,
        "action": action,
        "old_status": old_status,
        "new_status": new_status.value if isinstance(new_status, ComplianceStatus) else new_status,
        "old_maturity_level": old_maturity_level,
        "new_maturity_level": new_maturity_level.value if isinstance(new_maturity_level, MaturityLevel) else new_maturity_level,
        "notes": notes,
    }
    
    return await db.execute(query, params)


async def list_compliance_audit(
    company_id: int,
    control_id: Optional[int] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List audit trail for compliance changes"""
    params: dict[str, Any] = {"company_id": company_id, "limit": limit}

    where_clauses = ["company_id = %(company_id)s"]
    if control_id:
        where_clauses.append("control_id = %(control_id)s")
        params["control_id"] = control_id
    
    where_clause = " AND ".join(where_clauses)
    
    query = f"""
        SELECT 
            id, compliance_id, company_id, control_id, user_id, action,
            old_status, new_status, old_maturity_level, new_maturity_level,
            notes, created_at
        FROM company_essential8_audit
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT %(limit)s
    """
    
    rows = await db.fetch_all(query, params)

    result = []
    for row in rows:
        item = dict(row)
        item["created_at"] = _format_datetime(item.get("created_at"))
        result.append(item)

    return result


# =============================================================================
# Essential 8 Requirements Repository Functions
# =============================================================================


async def list_essential8_requirements(
    control_id: Optional[int] = None,
    maturity_level: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List Essential 8 requirements, optionally filtered by control and maturity level"""
    params: dict[str, Any] = {}
    where_clauses = []
    
    if control_id:
        where_clauses.append("control_id = %(control_id)s")
        params["control_id"] = control_id
    
    if maturity_level:
        where_clauses.append("maturity_level = %(maturity_level)s")
        params["maturity_level"] = maturity_level
    
    where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
    
    query = f"""
        SELECT id, control_id, maturity_level, requirement_order, description,
               created_at, updated_at
        FROM essential8_requirements
        WHERE {where_clause}
        ORDER BY control_id, maturity_level, requirement_order
    """
    
    rows = await db.fetch_all(query, params)
    
    result = []
    for row in rows:
        item = dict(row)
        item["created_at"] = _format_datetime(item.get("created_at"))
        item["updated_at"] = _format_datetime(item.get("updated_at"))
        result.append(item)
    
    return result


async def get_essential8_requirement(requirement_id: int) -> Optional[dict[str, Any]]:
    """Get a specific Essential 8 requirement"""
    query = """
        SELECT id, control_id, maturity_level, requirement_order, description,
               created_at, updated_at
        FROM essential8_requirements
        WHERE id = %(requirement_id)s
    """
    row = await db.fetch_one(query, {"requirement_id": requirement_id})
    
    if not row:
        return None
    
    item = dict(row)
    item["created_at"] = _format_datetime(item.get("created_at"))
    item["updated_at"] = _format_datetime(item.get("updated_at"))
    
    return item


async def get_control_with_requirements(
    control_id: int,
    company_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Get a control with all its requirements grouped by maturity level"""
    # Get the control
    control = await get_essential8_control(control_id)
    if not control:
        return None
    
    # Get all requirements for this control
    all_requirements = await list_essential8_requirements(control_id=control_id)
    
    # Group requirements by maturity level
    requirements_ml1 = [r for r in all_requirements if r["maturity_level"] == "ml1"]
    requirements_ml2 = [r for r in all_requirements if r["maturity_level"] == "ml2"]
    requirements_ml3 = [r for r in all_requirements if r["maturity_level"] == "ml3"]
    
    result = {
        "control": control,
        "requirements_ml1": requirements_ml1,
        "requirements_ml2": requirements_ml2,
        "requirements_ml3": requirements_ml3,
    }
    
    # If company_id is provided, get company compliance and requirement compliance
    if company_id:
        company_compliance = await get_company_compliance(company_id, control_id)
        result["company_compliance"] = company_compliance
        
        # Get requirement compliance
        requirement_compliance = await list_company_requirement_compliance(
            company_id=company_id,
            control_id=control_id,
        )
        result["requirement_compliance"] = requirement_compliance
    else:
        result["company_compliance"] = None
        result["requirement_compliance"] = []
    
    return result


async def initialize_company_requirement_compliance(
    company_id: int,
    control_id: Optional[int] = None,
) -> int:
    """Initialize requirement compliance records for a company
    
    If control_id is provided, only initialize for that control.
    Otherwise, initialize for all controls.
    """
    # Get all requirements, optionally filtered by control
    requirements = await list_essential8_requirements(control_id=control_id)
    created_count = 0
    
    for requirement in requirements:
        # Check if record already exists
        existing = await get_company_requirement_compliance(
            company_id=company_id,
            requirement_id=requirement["id"],
        )
        if not existing:
            await create_company_requirement_compliance(
                company_id=company_id,
                requirement_id=requirement["id"],
            )
            created_count += 1
    
    return created_count


async def create_company_requirement_compliance(
    company_id: int,
    requirement_id: int,
    status: ComplianceStatus = ComplianceStatus.NOT_STARTED,
    evidence: Optional[str] = None,
    notes: Optional[str] = None,
    last_reviewed_date: Optional[str] = None,
) -> dict[str, Any]:
    """Create a requirement compliance record for a company"""
    query = """
        INSERT INTO company_essential8_requirement_compliance
        (company_id, requirement_id, status, evidence, notes, last_reviewed_date)
        VALUES (%(company_id)s, %(requirement_id)s, %(status)s, %(evidence)s, %(notes)s,
                %(last_reviewed_date)s)
    """
    params = {
        "company_id": company_id,
        "requirement_id": requirement_id,
        "status": status.value if isinstance(status, ComplianceStatus) else status,
        "evidence": evidence,
        "notes": notes,
        "last_reviewed_date": last_reviewed_date,
    }
    
    record_id = await db.execute(query, params)
    
    # Return the created record
    result = await get_company_requirement_compliance(company_id, requirement_id)
    if result:
        return result
    
    return {"id": record_id, **params}


async def get_company_requirement_compliance(
    company_id: int,
    requirement_id: int,
) -> Optional[dict[str, Any]]:
    """Get a specific requirement compliance record"""
    query = """
        SELECT
            cerc.id, cerc.company_id, cerc.requirement_id, cerc.status,
            cerc.evidence, cerc.notes, cerc.last_reviewed_date,
            cerc.created_at, cerc.updated_at,
            er.id as req_id, er.control_id, er.maturity_level,
            er.requirement_order, er.description
        FROM company_essential8_requirement_compliance cerc
        INNER JOIN essential8_requirements er ON cerc.requirement_id = er.id
        WHERE cerc.company_id = %(company_id)s AND cerc.requirement_id = %(requirement_id)s
    """
    row = await db.fetch_one(query, {
        "company_id": company_id,
        "requirement_id": requirement_id,
    })
    
    if not row:
        return None
    
    item = dict(row)
    
    # Build nested requirement object
    item["requirement"] = {
        "id": row["requirement_id"],
        "control_id": row["control_id"],
        "maturity_level": row["maturity_level"],
        "requirement_order": row["requirement_order"],
        "description": row["description"],
    }
    
    # Clean up duplicate fields
    for key in ["req_id", "control_id", "maturity_level", "requirement_order", "description"]:
        item.pop(key, None)
    
    item["created_at"] = _format_datetime(item.get("created_at"))
    item["updated_at"] = _format_datetime(item.get("updated_at"))
    
    return item


async def list_company_requirement_compliance(
    company_id: int,
    control_id: Optional[int] = None,
    maturity_level: Optional[str] = None,
    status: Optional[ComplianceStatus] = None,
) -> list[dict[str, Any]]:
    """List requirement compliance records for a company"""
    params: dict[str, Any] = {"company_id": company_id}
    where_clauses = ["cerc.company_id = %(company_id)s"]
    
    if control_id:
        where_clauses.append("er.control_id = %(control_id)s")
        params["control_id"] = control_id
    
    if maturity_level:
        where_clauses.append("er.maturity_level = %(maturity_level)s")
        params["maturity_level"] = maturity_level
    
    if status:
        where_clauses.append("cerc.status = %(status)s")
        params["status"] = status.value
    
    where_clause = " AND ".join(where_clauses)
    
    query = f"""
        SELECT
            cerc.id, cerc.company_id, cerc.requirement_id, cerc.status,
            cerc.evidence, cerc.notes, cerc.last_reviewed_date,
            cerc.created_at, cerc.updated_at,
            er.id as req_id, er.control_id, er.maturity_level,
            er.requirement_order, er.description
        FROM company_essential8_requirement_compliance cerc
        INNER JOIN essential8_requirements er ON cerc.requirement_id = er.id
        WHERE {where_clause}
        ORDER BY er.control_id, er.maturity_level, er.requirement_order
    """
    
    rows = await db.fetch_all(query, params)
    
    result = []
    for row in rows:
        item = dict(row)
        
        # Build nested requirement object
        item["requirement"] = {
            "id": row["requirement_id"],
            "control_id": row["control_id"],
            "maturity_level": row["maturity_level"],
            "requirement_order": row["requirement_order"],
            "description": row["description"],
        }
        
        # Clean up duplicate fields
        for key in ["req_id", "control_id", "maturity_level", "requirement_order", "description"]:
            item.pop(key, None)
        
        item["created_at"] = _format_datetime(item.get("created_at"))
        item["updated_at"] = _format_datetime(item.get("updated_at"))
        
        result.append(item)
    
    return result


async def update_company_requirement_compliance(
    company_id: int,
    requirement_id: int,
    **updates: Any,
) -> Optional[dict[str, Any]]:
    """Update a requirement compliance record"""
    # Get current record
    current = await get_company_requirement_compliance(company_id, requirement_id)
    if not current:
        return None
    
    # Build update query
    set_clauses = []
    params: dict[str, Any] = {
        "company_id": company_id,
        "requirement_id": requirement_id,
    }
    
    for key, value in updates.items():
        if value is not None:
            set_clauses.append(f"{key} = %({key})s")
            # Handle enum values
            if isinstance(value, ComplianceStatus):
                params[key] = value.value
            else:
                params[key] = value
    
    if not set_clauses:
        return current
    
    set_clause = ", ".join(set_clauses)
    query = f"""
        UPDATE company_essential8_requirement_compliance
        SET {set_clause}
        WHERE company_id = %(company_id)s AND requirement_id = %(requirement_id)s
    """
    
    await db.execute(query, params)
    
    # Return updated record
    return await get_company_requirement_compliance(company_id, requirement_id)
