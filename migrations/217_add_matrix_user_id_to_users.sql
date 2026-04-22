-- Add Matrix user ID to users table so technicians can set their Matrix username
ALTER TABLE users ADD COLUMN IF NOT EXISTS matrix_user_id VARCHAR(255) NULL;
