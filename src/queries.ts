import { pool } from './db';
import { RowDataPacket, ResultSetHeader } from 'mysql2';

export interface User {
  id: number;
  email: string;
  password_hash: string;
  company_id: number;
}

export interface Company {
  id: number;
  name: string;
  address?: string;
}

export interface License {
  id: number;
  company_id: number;
  name: string;
  platform: string;
  count: number;
  expiry_date: string;
  contract_term: string;
  allocated?: number;
}

export interface UserCompany {
  user_id: number;
  company_id: number;
  can_manage_licenses: number;
  can_manage_staff: number;
  company_name?: string;
  email?: string;
}

export interface Staff {
  id: number;
  company_id: number;
  first_name: string;
  last_name: string;
  email: string;
  date_onboarded: string;
  enabled: number;
}

export interface ApiKey {
  id: number;
  api_key: string;
  description: string;
  expiry_date: string | null;
}

export async function getUserByEmail(email: string): Promise<User | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM users WHERE email = ?', [email]);
  return (rows as User[])[0] || null;
}

export async function getCompanyById(id: number): Promise<Company | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM companies WHERE id = ?', [id]);
  return (rows as Company[])[0] || null;
}

export async function getLicensesByCompany(companyId: number): Promise<License[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT l.*, COUNT(sl.staff_id) AS allocated
     FROM licenses l
     LEFT JOIN staff_licenses sl ON l.id = sl.license_id
     WHERE l.company_id = ?
     GROUP BY l.id`,
    [companyId]
  );
  return rows as License[];
}

export async function getAllLicenses(): Promise<License[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT l.*, COUNT(sl.staff_id) AS allocated
     FROM licenses l
     LEFT JOIN staff_licenses sl ON l.id = sl.license_id
     GROUP BY l.id`
  );
  return rows as License[];
}

export async function getLicenseById(id: number): Promise<License | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT l.*, COUNT(sl.staff_id) AS allocated
     FROM licenses l
     LEFT JOIN staff_licenses sl ON l.id = sl.license_id
     WHERE l.id = ?
     GROUP BY l.id`,
    [id]
  );
  return (rows as License[])[0] || null;
}

export async function getUserCount(): Promise<number> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT COUNT(*) as count FROM users');
  return (rows[0] as { count: number }).count;
}

export async function createCompany(name: string, address?: string): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO companies (name, address) VALUES (?, ?)',
    [name, address || null]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function createUser(
  email: string,
  passwordHash: string,
  companyId: number
): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO users (email, password_hash, company_id) VALUES (?, ?, ?)',
    [email, passwordHash, companyId]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function createLicense(
  companyId: number,
  name: string,
  platform: string,
  count: number,
  expiryDate: string,
  contractTerm: string
): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO licenses (company_id, name, platform, count, expiry_date, contract_term) VALUES (?, ?, ?, ?, ?, ?)',
    [companyId, name, platform, count, expiryDate, contractTerm]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function getAllCompanies(): Promise<Company[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM companies');
  return rows as Company[];
}

export async function getAllUsers(): Promise<User[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM users');
  return rows as User[];
}

export async function getUserById(id: number): Promise<User | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM users WHERE id = ?', [id]);
  return (rows as User[])[0] || null;
}

export async function getCompaniesForUser(userId: number): Promise<UserCompany[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT uc.user_id, uc.company_id, uc.can_manage_licenses, uc.can_manage_staff, c.name AS company_name
     FROM user_companies uc JOIN companies c ON uc.company_id = c.id
     WHERE uc.user_id = ?`,
    [userId]
  );
  return rows as UserCompany[];
}

export async function getUserCompanyAssignments(): Promise<UserCompany[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT uc.user_id, uc.company_id, uc.can_manage_licenses, uc.can_manage_staff, c.name AS company_name, u.email
     FROM user_companies uc
     JOIN users u ON uc.user_id = u.id
     JOIN companies c ON uc.company_id = c.id
     ORDER BY u.email, c.name`
  );
  return rows as UserCompany[];
}

export async function assignUserToCompany(
  userId: number,
  companyId: number,
  canManageLicenses: boolean,
  canManageStaff: boolean
): Promise<void> {
  await pool.execute(
    `INSERT INTO user_companies (user_id, company_id, can_manage_licenses, can_manage_staff)
     VALUES (?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE can_manage_licenses = VALUES(can_manage_licenses), can_manage_staff = VALUES(can_manage_staff)`,
    [userId, companyId, canManageLicenses ? 1 : 0, canManageStaff ? 1 : 0]
  );
}

export async function updateUserCompanyPermission(
  userId: number,
  companyId: number,
  field: 'can_manage_licenses' | 'can_manage_staff',
  value: boolean
): Promise<void> {
  await pool.execute(
    `UPDATE user_companies SET ${field} = ? WHERE user_id = ? AND company_id = ?`,
    [value ? 1 : 0, userId, companyId]
  );
}

export async function getStaffByCompany(companyId: number): Promise<Staff[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM staff WHERE company_id = ?',
    [companyId]
  );
  return rows as Staff[];
}

export async function getAllStaff(): Promise<Staff[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM staff');
  return rows as Staff[];
}

export async function getStaffById(id: number): Promise<Staff | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM staff WHERE id = ?', [id]);
  return (rows as Staff[])[0] || null;
}

export async function addStaff(
  companyId: number,
  firstName: string,
  lastName: string,
  email: string,
  dateOnboarded: string,
  enabled: boolean
): Promise<void> {
  await pool.execute(
    'INSERT INTO staff (company_id, first_name, last_name, email, date_onboarded, enabled) VALUES (?, ?, ?, ?, ?, ?)',
    [companyId, firstName, lastName, email, dateOnboarded, enabled ? 1 : 0]
  );
}

export async function updateStaffEnabled(
  staffId: number,
  enabled: boolean
): Promise<void> {
  await pool.execute('UPDATE staff SET enabled = ? WHERE id = ?', [
    enabled ? 1 : 0,
    staffId,
  ]);
}

export async function updateStaff(
  id: number,
  companyId: number,
  firstName: string,
  lastName: string,
  email: string,
  dateOnboarded: string,
  enabled: boolean
): Promise<void> {
  await pool.execute(
    'UPDATE staff SET company_id = ?, first_name = ?, last_name = ?, email = ?, date_onboarded = ?, enabled = ? WHERE id = ?',
    [companyId, firstName, lastName, email, dateOnboarded, enabled ? 1 : 0, id]
  );
}

export async function deleteStaff(id: number): Promise<void> {
  await pool.execute('DELETE FROM staff WHERE id = ?', [id]);
}

export async function updateCompany(
  id: number,
  name: string,
  address: string | null
): Promise<void> {
  await pool.execute('UPDATE companies SET name = ?, address = ? WHERE id = ?', [
    name,
    address,
    id,
  ]);
}

export async function deleteCompany(id: number): Promise<void> {
  await pool.execute('DELETE FROM companies WHERE id = ?', [id]);
}

export async function updateUser(
  id: number,
  email: string,
  passwordHash: string,
  companyId: number
): Promise<void> {
  await pool.execute(
    'UPDATE users SET email = ?, password_hash = ?, company_id = ? WHERE id = ?',
    [email, passwordHash, companyId, id]
  );
}

export async function deleteUser(id: number): Promise<void> {
  await pool.execute('DELETE FROM users WHERE id = ?', [id]);
}

export async function updateLicense(
  id: number,
  companyId: number,
  name: string,
  platform: string,
  count: number,
  expiryDate: string,
  contractTerm: string
): Promise<void> {
  await pool.execute(
    'UPDATE licenses SET company_id = ?, name = ?, platform = ?, count = ?, expiry_date = ?, contract_term = ? WHERE id = ?',
    [companyId, name, platform, count, expiryDate, contractTerm, id]
  );
}

export async function deleteLicense(id: number): Promise<void> {
  await pool.execute('DELETE FROM licenses WHERE id = ?', [id]);
}

export async function unassignUserFromCompany(
  userId: number,
  companyId: number
): Promise<void> {
  await pool.execute('DELETE FROM user_companies WHERE user_id = ? AND company_id = ?', [
    userId,
    companyId,
  ]);
}

export async function linkStaffToLicense(
  staffId: number,
  licenseId: number
): Promise<void> {
  await pool.execute(
    'INSERT INTO staff_licenses (staff_id, license_id) VALUES (?, ?) ON DUPLICATE KEY UPDATE staff_id = staff_id',
    [staffId, licenseId]
  );
}

export async function unlinkStaffFromLicense(
  staffId: number,
  licenseId: number
): Promise<void> {
  await pool.execute('DELETE FROM staff_licenses WHERE staff_id = ? AND license_id = ?', [
    staffId,
    licenseId,
  ]);
}

export async function createApiKey(
  apiKey: string,
  description: string,
  expiryDate?: string
): Promise<void> {
  await pool.execute(
    'INSERT INTO api_keys (api_key, description, expiry_date) VALUES (?, ?, ?)',
    [apiKey, description, expiryDate || null]
  );
}

export async function getApiKeys(): Promise<ApiKey[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM api_keys');
  return rows as ApiKey[];
}

export async function deleteApiKey(id: number): Promise<void> {
  await pool.execute('DELETE FROM api_keys WHERE id = ?', [id]);
}

export async function getApiKeyRecord(apiKey: string): Promise<ApiKey | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM api_keys WHERE api_key = ? AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE())',
    [apiKey]
  );
  return (rows as ApiKey[])[0] || null;
}
