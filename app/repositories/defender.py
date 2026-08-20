"""Persistence operations for Defender management."""
import json
from typing import Any
from app.core.database import db

async def company_enabled(company_id: int) -> bool:
    row = await db.fetch_one("SELECT defender_enabled FROM companies WHERE id=%s", (company_id,))
    return bool(row and row.get("defender_enabled"))

async def set_company_enabled(company_id: int, enabled: bool) -> None:
    await db.execute("UPDATE companies SET defender_enabled=%s WHERE id=%s", (enabled, company_id))

async def device_belongs_to_company(device_id: int, company_id: int) -> bool:
    row = await db.fetch_one("SELECT id FROM tray_devices WHERE id=%s AND company_id=%s", (device_id, company_id))
    return bool(row)

async def dashboard(company_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    devices = await db.fetch_all("""SELECT td.id, td.asset_id, td.hostname, td.last_seen_utc, ds.health_status, ds.antivirus_enabled,
        ds.realtime_protection_enabled, ds.tamper_protection_enabled, ds.signatures_updated_at, ds.last_scan_at, ds.threat_count, ds.updated_at,
        CASE WHEN td.last_seen_utc IS NULL OR td.last_seen_utc < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 15 MINUTE) THEN 1 ELSE 0 END AS is_stale
        FROM tray_devices td LEFT JOIN defender_device_status ds ON ds.tray_device_id=td.id
        WHERE td.company_id=%s AND td.status='active' ORDER BY td.hostname""", (company_id,))
    exclusions = await db.fetch_all("""SELECT de.*, td.hostname FROM defender_exclusions de
        LEFT JOIN tray_devices td ON td.id=de.tray_device_id
        WHERE de.scope='global' OR de.company_id=%s ORDER BY de.created_at DESC""", (company_id,))
    detections = await db.fetch_all("""SELECT dd.*, td.hostname FROM defender_detections dd
        JOIN tray_devices td ON td.id=dd.tray_device_id WHERE dd.company_id=%s
        ORDER BY dd.detected_at DESC LIMIT 100""", (company_id,))
    return list(devices or []), list(exclusions or []), list(detections or [])

async def settings(company_id: int) -> dict[str, Any]:
    return await db.fetch_one("""SELECT defender_scheduled_scan_type, defender_scheduled_scan_day,
      LEFT(CAST(defender_scheduled_scan_time AS CHAR), 5) AS defender_scheduled_scan_time,
      defender_auto_ticket_min_severity FROM companies WHERE id=%s""", (company_id,)) or {}

async def update_settings(company_id: int, payload: Any) -> None:
    await db.execute("""UPDATE companies SET defender_scheduled_scan_type=%s, defender_scheduled_scan_day=%s,
      defender_scheduled_scan_time=%s, defender_auto_ticket_min_severity=%s WHERE id=%s""",
      (payload.scheduled_scan_type, payload.scheduled_scan_day, payload.scheduled_scan_time, payload.auto_ticket_min_severity, company_id))

async def add_exclusion(scope: str, company_id: int, device_id: int | None, kind: str, value: str, user_id: int) -> None:
    await db.execute("INSERT INTO defender_exclusions (scope,company_id,tray_device_id,exclusion_type,value,created_by_user_id) VALUES (%s,%s,%s,%s,%s,%s)", (scope, None if scope == 'global' else company_id, device_id if scope == 'device' else None, kind, value, user_id))

async def delete_exclusion(exclusion_id: int, company_id: int, super_admin: bool) -> None:
    sql = ("DELETE FROM defender_exclusions WHERE id=%s AND (scope='global' OR company_id=%s)"
           if super_admin else "DELETE FROM defender_exclusions WHERE id=%s AND company_id=%s")
    await db.execute(sql, (exclusion_id, company_id))

async def policy(device_id: int, company_id: int) -> dict[str, Any]:
    rows = await db.fetch_all("""SELECT exclusion_type, value FROM defender_exclusions
      WHERE scope='global' OR (company_id=%s AND (scope='company' OR tray_device_id=%s)) ORDER BY id""", (company_id, device_id))
    return {"enabled": await company_enabled(company_id), "exclusions": list(rows or [])}

async def report_status(device_id: int, company_id: int, payload: Any) -> None:
    await db.execute("""INSERT INTO defender_device_status (tray_device_id,company_id,enabled,antivirus_enabled,realtime_protection_enabled,tamper_protection_enabled,signatures_updated_at,last_scan_at,health_status,details_json)
      VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE enabled=1,antivirus_enabled=VALUES(antivirus_enabled),realtime_protection_enabled=VALUES(realtime_protection_enabled),tamper_protection_enabled=VALUES(tamper_protection_enabled),signatures_updated_at=VALUES(signatures_updated_at),last_scan_at=VALUES(last_scan_at),health_status=VALUES(health_status),details_json=VALUES(details_json)""",
      (device_id,company_id,payload.antivirus_enabled,payload.realtime_protection_enabled,payload.tamper_protection_enabled,payload.signatures_updated_at,payload.last_scan_at,payload.health_status,json.dumps(payload.details)))

async def report_detection(device_id: int, company_id: int, payload: Any) -> dict[str, Any] | None:
    await db.execute("""INSERT INTO defender_detections (company_id,tray_device_id,detection_uid,threat_name,severity,status,detected_at,details_json)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE threat_name=VALUES(threat_name),severity=VALUES(severity),status=VALUES(status),details_json=VALUES(details_json)""",
      (company_id,device_id,payload.detection_uid,payload.threat_name,payload.severity,payload.status,payload.detected_at,json.dumps(payload.details)))
    await db.execute("UPDATE defender_device_status SET threat_count=(SELECT COUNT(*) FROM defender_detections WHERE tray_device_id=%s AND status='active') WHERE tray_device_id=%s", (device_id,device_id))
    return await db.fetch_one("SELECT * FROM defender_detections WHERE tray_device_id=%s AND detection_uid=%s", (device_id, payload.detection_uid))

async def detection(detection_id: int, company_id: int) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT dd.*, td.hostname, td.asset_id FROM defender_detections dd JOIN tray_devices td ON td.id=dd.tray_device_id WHERE dd.id=%s AND dd.company_id=%s", (detection_id,company_id))

async def device(device_id: int, company_id: int) -> dict[str, Any] | None:
    return await db.fetch_one("""SELECT td.id, td.asset_id, td.hostname, ds.health_status,
      ds.antivirus_enabled, ds.realtime_protection_enabled, ds.signatures_updated_at,
      ds.last_scan_at, ds.threat_count FROM tray_devices td
      LEFT JOIN defender_device_status ds ON ds.tray_device_id=td.id
      WHERE td.id=%s AND td.company_id=%s AND td.status='active'""", (device_id, company_id))

async def link_ticket(detection_id: int, ticket_id: int) -> None:
    await db.execute("UPDATE defender_detections SET ticket_id=%s WHERE id=%s", (ticket_id,detection_id))

async def queue_command(company_id: int, device_id: int, command_type: str, user_id: int, detection_id: int | None = None) -> int:
    return await db.execute_returning_lastrowid("""INSERT INTO defender_commands
      (company_id,tray_device_id,detection_id,command_type,requested_by_user_id) VALUES (%s,%s,%s,%s,%s)""",
      (company_id, device_id, detection_id, command_type, user_id))

async def poll_commands(device_id: int, company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all("""SELECT dc.id, dc.command_type, dc.detection_id, dc.requested_at,
      dd.detection_uid, dd.threat_name FROM defender_commands dc
      LEFT JOIN defender_detections dd ON dd.id=dc.detection_id
      WHERE dc.tray_device_id=%s AND dc.company_id=%s AND dc.status='pending'
      ORDER BY dc.requested_at LIMIT 10""", (device_id, company_id))
    for row in rows or []:
        await db.execute("UPDATE defender_commands SET status='claimed', claimed_at=UTC_TIMESTAMP() WHERE id=%s AND status='pending'", (row["id"],))
    return list(rows or [])

async def complete_command(command_id: int, device_id: int, status: str, result: dict[str, Any]) -> None:
    await db.execute("""UPDATE defender_commands SET status=%s, result_json=%s, completed_at=UTC_TIMESTAMP()
      WHERE id=%s AND tray_device_id=%s AND status IN ('pending','claimed')""", (status, json.dumps(result), command_id, device_id))
    if status == "completed":
        command = await db.fetch_one("SELECT detection_id, command_type FROM defender_commands WHERE id=%s AND tray_device_id=%s", (command_id, device_id))
        if command and command.get("detection_id") and command.get("command_type") in {"quarantine", "remediate"}:
            await db.execute("""UPDATE defender_detections SET status=%s, resolved_at=UTC_TIMESTAMP()
              WHERE id=%s AND tray_device_id=%s""", (command["command_type"] + "d", command["detection_id"], device_id))

async def update_detection_workflow(detection_id: int, company_id: int, user_id: int, action: str) -> dict[str, Any] | None:
    if action == "acknowledge":
        await db.execute("UPDATE defender_detections SET acknowledged_at=UTC_TIMESTAMP(), acknowledged_by_user_id=%s WHERE id=%s AND company_id=%s", (user_id,detection_id,company_id))
    elif action == "resolve":
        await db.execute("UPDATE defender_detections SET status='resolved', resolved_at=UTC_TIMESTAMP(), resolved_by_user_id=%s WHERE id=%s AND company_id=%s", (user_id,detection_id,company_id))
    elif action == "reopen":
        await db.execute("UPDATE defender_detections SET status='active', resolved_at=NULL, resolved_by_user_id=NULL WHERE id=%s AND company_id=%s", (detection_id,company_id))
    return await detection(detection_id, company_id)
