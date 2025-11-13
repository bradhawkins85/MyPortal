-- Migrate billing_contacts table to use staff_id instead of user_id
-- This allows billing contacts to be any staff member, not just users with accounts

-- Step 1: Add new staff_id column
ALTER TABLE billing_contacts
ADD COLUMN staff_id INT DEFAULT NULL AFTER user_id;

-- Step 2: Migrate existing data - match users to staff by email and company
UPDATE billing_contacts bc
INNER JOIN users u ON u.id = bc.user_id
INNER JOIN staff s ON s.company_id = bc.company_id AND LOWER(s.email) = LOWER(u.email)
SET bc.staff_id = s.id;

-- Step 3: Drop the old foreign key constraint on user_id
ALTER TABLE billing_contacts
DROP FOREIGN KEY billing_contacts_ibfk_2;

-- Step 4: Drop the old user_id column
ALTER TABLE billing_contacts
DROP COLUMN user_id;

-- Step 5: Make staff_id NOT NULL
ALTER TABLE billing_contacts
MODIFY COLUMN staff_id INT NOT NULL;

-- Step 6: Add foreign key constraint for staff_id
ALTER TABLE billing_contacts
ADD CONSTRAINT billing_contacts_staff_fk 
FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE;

-- Step 7: Update the unique key to use staff_id
ALTER TABLE billing_contacts
DROP KEY unique_company_user;

ALTER TABLE billing_contacts
ADD UNIQUE KEY unique_company_staff (company_id, staff_id);

-- Step 8: Update the index
DROP INDEX IF EXISTS idx_billing_contacts_user ON billing_contacts;
CREATE INDEX idx_billing_contacts_staff ON billing_contacts(staff_id);
