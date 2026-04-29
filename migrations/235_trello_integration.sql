-- Add Trello board ID to companies table so each company can be linked to one Trello board
ALTER TABLE companies ADD COLUMN IF NOT EXISTS trello_board_id VARCHAR(255) NULL DEFAULT NULL;
