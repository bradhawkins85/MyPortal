-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 550: Each tray device keeps its own Ed25519 key pair and registers the
-- public key here. Once set, the device must sign a server challenge when its
-- WebSocket connects, so a copied bearer token alone cannot impersonate it.
-- NULL means the device has not registered a key (older releases).
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS public_key VARCHAR(64) NULL;
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS public_key_registered_at DATETIME NULL;
