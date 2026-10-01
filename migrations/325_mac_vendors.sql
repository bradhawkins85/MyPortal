-- IEEE OUI assignments used to identify discovered network devices.
CREATE TABLE IF NOT EXISTS mac_vendors (
  oui_prefix CHAR(6) PRIMARY KEY,
  vendor VARCHAR(255) NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
