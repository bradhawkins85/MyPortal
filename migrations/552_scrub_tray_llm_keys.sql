-- phase: data
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 552: Before the step 2 security fixes, each AI troubleshoot command
-- stored the server's LLM API key in tray_command_log.payload_json. New commands
-- no longer carry it; remove it from the commands already logged. Rows that are
-- not valid JSON are left as they are.
UPDATE tray_command_log
SET payload_json = CASE
    WHEN JSON_VALID(payload_json) THEN JSON_REMOVE(payload_json, '$.llm_api_key')
    ELSE payload_json
END
WHERE payload_json LIKE '%llm_api_key%';
