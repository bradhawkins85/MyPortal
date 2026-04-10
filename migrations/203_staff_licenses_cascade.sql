-- Add ON DELETE CASCADE to staff_licenses.staff_id FK so that deleting a
-- staff record automatically removes associated staff_licenses rows.

-- Drop the existing constraint if it does not already have CASCADE behaviour.
SET @fk_exists = (
  SELECT COUNT(*)
  FROM information_schema.REFERENTIAL_CONSTRAINTS
  WHERE CONSTRAINT_SCHEMA = DATABASE()
    AND TABLE_NAME = 'staff_licenses'
    AND CONSTRAINT_NAME = 'staff_licenses_ibfk_1'
    AND DELETE_RULE != 'CASCADE'
);

SET @sql = IF(@fk_exists > 0,
  'ALTER TABLE staff_licenses DROP FOREIGN KEY staff_licenses_ibfk_1',
  'SELECT "staff_licenses_ibfk_1 already has correct DELETE rule, skipping drop" AS message'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Re-add the constraint with ON DELETE CASCADE (only when it was just dropped).
SET @fk_missing = (
  SELECT COUNT(*)
  FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'staff_licenses'
    AND CONSTRAINT_NAME = 'staff_licenses_ibfk_1'
    AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);

SET @sql2 = IF(@fk_missing = 0,
  'ALTER TABLE staff_licenses ADD CONSTRAINT staff_licenses_ibfk_1 FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE',
  'SELECT "staff_licenses_ibfk_1 already exists, skipping add" AS message'
);
PREPARE stmt2 FROM @sql2;
EXECUTE stmt2;
DEALLOCATE PREPARE stmt2;
