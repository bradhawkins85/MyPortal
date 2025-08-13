import { pool } from './db';
import { RowDataPacket } from 'mysql2';

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
