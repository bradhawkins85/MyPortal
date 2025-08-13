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
}

export interface UserCompany {
  user_id: number;
  company_id: number;
  can_manage_licenses: number;
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

export async function getUserByEmail(email: string): Promise<User | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM users WHERE email = ?', [email]);
  return (rows as User[])[0] || null;
}

export async function getCompanyById(id: number): Promise<Company | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM companies WHERE id = ?', [id]);
  return (rows as Company[])[0] || null;
}

export async function getLicensesByCompany(companyId: number): Promise<License[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM licenses WHERE company_id = ?', [companyId]);
  return rows as License[];
}

export async function getUserCount(): Promise<number> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT COUNT(*) as count FROM users');
  return (rows[0] as { count: number }).count;
}

export async function createCompany(name: string): Promise<number> {
  const [result] = await pool.execute('INSERT INTO companies (name) VALUES (?)', [name]);
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

export async function getAllCompanies(): Promise<Company[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM companies');
  return rows as Company[];
}

export async function getAllUsers(): Promise<User[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM users');
  return rows as User[];
}

export async function getCompaniesForUser(userId: number): Promise<UserCompany[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT uc.user_id, uc.company_id, uc.can_manage_licenses, c.name AS company_name
     FROM user_companies uc JOIN companies c ON uc.company_id = c.id
     WHERE uc.user_id = ?`,
    [userId]
  );
  return rows as UserCompany[];
}

export async function getUserCompanyAssignments(): Promise<UserCompany[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT uc.user_id, uc.company_id, uc.can_manage_licenses, c.name AS company_name, u.email
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
  canManageLicenses: boolean
): Promise<void> {
  await pool.execute(
    `INSERT INTO user_companies (user_id, company_id, can_manage_licenses)
     VALUES (?, ?, ?)
     ON DUPLICATE KEY UPDATE can_manage_licenses = VALUES(can_manage_licenses)`,
    [userId, companyId, canManageLicenses ? 1 : 0]
  );
}

export async function updateUserCompanyPermission(
  userId: number,
  companyId: number,
  canManageLicenses: boolean
): Promise<void> {
  await pool.execute(
    'UPDATE user_companies SET can_manage_licenses = ? WHERE user_id = ? AND company_id = ?',
    [canManageLicenses ? 1 : 0, userId, companyId]
  );
}

export async function getStaffByCompany(companyId: number): Promise<Staff[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM staff WHERE company_id = ?',
    [companyId]
  );
  return rows as Staff[];
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
