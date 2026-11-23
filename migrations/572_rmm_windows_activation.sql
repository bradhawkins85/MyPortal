-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 572: Windows activation state from the RMM agent's inventory.
--
-- windows_activation is licensed, grace, notification, non_genuine or
-- unlicensed (NULL when the device is not Windows or has not reported it), so
-- the assets list can show which machines still need activating. The licence
-- channel and remaining grace period are kept in details_json.

ALTER TABLE rmm_device_inventory ADD COLUMN IF NOT EXISTS windows_activation VARCHAR(32) NULL;
