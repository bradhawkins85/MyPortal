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
