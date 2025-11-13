-- Migrate billing_contacts table to use staff_id instead of user_id
-- This allows billing contacts to be any staff member, not just users with accounts

-- Step 1: Add new staff_id column (only if it doesn't exist)
SET @column_exists = (
  SELECT COUNT(*) 
  FROM information_schema.COLUMNS 
  WHERE TABLE_SCHEMA = DATABASE() 
    AND TABLE_NAME = 'billing_contacts' 
    AND COLUMN_NAME = 'staff_id'
);

SET @user_column_exists = (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'billing_contacts'
    AND COLUMN_NAME = 'user_id'
);

SET @sql = IF(@column_exists = 0,
  IF(@user_column_exists > 0,
    'ALTER TABLE billing_contacts ADD COLUMN staff_id INT DEFAULT NULL AFTER user_id',
    'ALTER TABLE billing_contacts ADD COLUMN staff_id INT DEFAULT NULL AFTER company_id'
  ),
  'SELECT "Column staff_id already exists, skipping" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 2: Migrate existing data - match users to staff by email and company (only if user_id exists)
SET @column_exists = (
  SELECT COUNT(*) 
  FROM information_schema.COLUMNS 
  WHERE TABLE_SCHEMA = DATABASE() 
    AND TABLE_NAME = 'billing_contacts' 
    AND COLUMN_NAME = 'user_id'
);

SET @sql = IF(@column_exists > 0,
  'UPDATE billing_contacts bc INNER JOIN users u ON u.id = bc.user_id INNER JOIN staff s ON s.company_id = bc.company_id AND LOWER(s.email) = LOWER(u.email) SET bc.staff_id = s.id WHERE bc.staff_id IS NULL',
  'SELECT "Column user_id does not exist, skipping data migration" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 3: Remove any billing contacts that couldn't be migrated (no matching staff) (only if needed)
SET @sql = IF(@column_exists > 0,
  'DELETE FROM billing_contacts WHERE staff_id IS NULL',
  'SELECT "Column user_id does not exist, skipping deletion" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @fk_exists = (
  SELECT COUNT(*)
  FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'billing_contacts'
    AND CONSTRAINT_NAME = 'billing_contacts_ibfk_2'
    AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);

SET @sql = IF(@fk_exists > 0,
  'ALTER TABLE billing_contacts DROP FOREIGN KEY billing_contacts_ibfk_2',
  'SELECT "Foreign key billing_contacts_ibfk_2 does not exist, skipping" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 5: Drop the old unique key referencing user_id (only if it exists)
SET @key_exists = (
  SELECT COUNT(*)
  FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'billing_contacts'
    AND CONSTRAINT_NAME = 'unique_company_user'
);

SET @sql = IF(@key_exists > 0,
  'ALTER TABLE billing_contacts DROP KEY unique_company_user',
  'SELECT "Key unique_company_user does not exist, skipping drop" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 6: Drop the old user_id index (only if it exists)
SET @index_exists = (
  SELECT COUNT(*)
  FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'billing_contacts'
    AND INDEX_NAME = 'idx_billing_contacts_user'
);

SET @sql = IF(@index_exists > 0,
  'ALTER TABLE billing_contacts DROP INDEX idx_billing_contacts_user',
  'SELECT "Index idx_billing_contacts_user does not exist, skipping drop" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 7: Drop the old user_id column (only if it exists)
SET @column_exists = (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'billing_contacts'
    AND COLUMN_NAME = 'user_id'
);

SET @sql = IF(@column_exists > 0,
  'ALTER TABLE billing_contacts DROP COLUMN user_id',
  'SELECT "Column user_id does not exist, skipping" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 8: Make staff_id NOT NULL (only if the column is nullable)
SET @column_nullable = (
  SELECT IS_NULLABLE
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'billing_contacts'
    AND COLUMN_NAME = 'staff_id'
);

SET @sql = IF(@column_nullable = 'YES',
  'ALTER TABLE billing_contacts MODIFY COLUMN staff_id INT NOT NULL',
  'SELECT "Column staff_id is already NOT NULL, skipping" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 9: Add foreign key constraint for staff_id (only if it doesn't exist)
SET @fk_exists = (
  SELECT COUNT(*)
  FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'billing_contacts'
    AND CONSTRAINT_NAME = 'billing_contacts_staff_fk'
    AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);

SET @sql = IF(@fk_exists = 0,
  'ALTER TABLE billing_contacts ADD CONSTRAINT billing_contacts_staff_fk FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE',
  'SELECT "Foreign key billing_contacts_staff_fk already exists, skipping" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 10: Ensure the unique key uses staff_id
SET @key_exists = (
  SELECT COUNT(*)
  FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'billing_contacts'
    AND CONSTRAINT_NAME = 'unique_company_staff'
);

SET @sql = IF(@key_exists = 0,
  'ALTER TABLE billing_contacts ADD UNIQUE KEY unique_company_staff (company_id, staff_id)',
  'SELECT "Key unique_company_staff already exists, skipping" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 11: Ensure an index exists for staff_id
SET @index_exists = (
  SELECT COUNT(*)
  FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'billing_contacts'
    AND INDEX_NAME = 'idx_billing_contacts_staff'
);

SET @sql = IF(@index_exists = 0,
  'CREATE INDEX idx_billing_contacts_staff ON billing_contacts(staff_id)',
  'SELECT "Index idx_billing_contacts_staff already exists, skipping" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
