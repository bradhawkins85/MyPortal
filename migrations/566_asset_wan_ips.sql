-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Tray agents report the public IPv4 and IPv6 addresses their network uses,
-- discovered through the server owner's whoami endpoint, on each heartbeat,
-- plus the Cloudflare location (Cf-Ipcity, Cf-Ipcountry, Cf-Region-Code)
-- when whoami returns it.
ALTER TABLE assets ADD COLUMN IF NOT EXISTS wan_ipv4 VARCHAR(15) NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS wan_ipv6 VARCHAR(45) NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS wan_ip_updated_at DATETIME NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS wan_city VARCHAR(128) NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS wan_country VARCHAR(8) NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS wan_region_code VARCHAR(16) NULL;
