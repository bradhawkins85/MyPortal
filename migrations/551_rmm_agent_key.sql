-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 551: Each RMM agent keeps its own Ed25519 key pair and registers
-- the public key here. Once set, re-enrolling with the agent's token also needs
-- a signature from that key, and live tools sessions open only after the agent
-- signs a challenge. NULL means the agent has not registered a key yet.
ALTER TABLE rmm_agents ADD COLUMN IF NOT EXISTS public_key VARCHAR(64) NULL;
ALTER TABLE rmm_agents ADD COLUMN IF NOT EXISTS public_key_registered_at DATETIME NULL;
