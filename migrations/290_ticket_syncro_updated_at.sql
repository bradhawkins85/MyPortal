-- Track the last Syncro updated_at value imported for each ticket.
ALTER TABLE tickets ADD COLUMN syncro_updated_at DATETIME(6) NULL;
CREATE INDEX idx_tickets_syncro_updated_at ON tickets(syncro_updated_at);
