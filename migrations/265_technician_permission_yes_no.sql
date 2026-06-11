-- Normalize existing Technician menu permission records to the new Yes/No model.
-- No Access remains unset/none, while prior Read Only values become Yes (write).
UPDATE roles
SET permissions = REPLACE(
  REPLACE(
    REPLACE(
      REPLACE(permissions, '"menu.admin.technician":"read"', '"menu.admin.technician":"write"'),
      '"menu.admin.technician": "read"', '"menu.admin.technician": "write"'
    ),
    '"menu.admin.technician":"Read Only"', '"menu.admin.technician":"write"'
  ),
  '"menu.admin.technician": "Read Only"', '"menu.admin.technician": "write"'
)
WHERE permissions LIKE '%"menu.admin.technician"%';
