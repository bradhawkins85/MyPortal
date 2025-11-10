from __future__ import annotations

from typing import Any, Optional

from app.core.database import db
from app.schemas.essential8 import ComplianceStatus, MaturityLevel


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
        WHERE id = :control_id
    """
    return await db.fetch_one(query, {"control_id": control_id})


async def list_company_compliance(
    company_id: int,
    status: Optional[ComplianceStatus] = None,
) -> list[dict[str, Any]]:
    """List compliance records for a company"""
    params: dict[str, Any] = {"company_id": company_id}
    
    where_clauses = ["cec.company_id = :company_id"]
    if status:
        where_clauses.append("cec.status = :status")
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
    result = []
    for row in rows:
        item = dict(row)
        item["control"] = {
            "id": row["control_id"],
            "name": row["control_name"],
            "description": row["control_description"] or "",
            "control_order": row["control_order"],
        }
        # Remove the flattened control fields
        for key in ["control_name", "control_description"]:
            item.pop(key, None)
        result.append(item)
    
    return result


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
        WHERE cec.company_id = :company_id AND cec.control_id = :control_id
    """
    row = await db.fetch_one(query, {"company_id": company_id, "control_id": control_id})
    
    if not row:
        return None
    
    item = dict(row)
    item["control"] = {
        "id": row["control_id"],
        "name": row["control_name"],
        "description": row["control_description"] or "",
        "control_order": row["control_order"],
    }
    # Remove the flattened control fields
    for key in ["control_name", "control_description"]:
        item.pop(key, None)
    
    return item


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
        VALUES (:company_id, :control_id, :status, :maturity_level, :evidence, 
                :notes, :last_reviewed_date, :target_compliance_date)
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
            set_clauses.append(f"{key} = :{key}")
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
        WHERE company_id = :company_id AND control_id = :control_id
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
        WHERE company_id = :company_id
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
        VALUES (:compliance_id, :company_id, :control_id, :user_id, :action,
                :old_status, :new_status, :old_maturity_level, :new_maturity_level, :notes)
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
    
    where_clauses = ["company_id = :company_id"]
    if control_id:
        where_clauses.append("control_id = :control_id")
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
        LIMIT :limit
    """
    
    return await db.fetch_all(query, params)
