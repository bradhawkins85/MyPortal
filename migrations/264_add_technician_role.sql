INSERT INTO roles (name, description, permissions, is_system)
SELECT
  'Technician',
  'Can switch between all companies and act using the permissions assigned to the Technician role.',
  '{"menu.admin.technician":"write","menu.tickets":"write","menu.reporting":"write"}',
  1
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'Technician');
