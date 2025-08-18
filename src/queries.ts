import { pool } from './db';
import { RowDataPacket, ResultSetHeader } from 'mysql2';

export interface User {
  id: number;
  email: string;
  password_hash: string;
  company_id: number;
  first_name?: string | null;
  last_name?: string | null;
  force_password_change?: number;
}

export interface UserTotpAuthenticator {
  id: number;
  user_id: number;
  name: string;
  secret: string;
}

export interface Company {
  id: number;
  name: string;
  address?: string;
  is_vip?: number;
  syncro_company_id?: string | null;
  xero_id?: string | null;
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

export interface App {
  id: number;
  sku: string;
  name: string;
  default_price: number;
  contract_term: string;
}

export interface CompanyAppPrice {
  company_id: number;
  app_id: number;
  price: number;
}

export interface UserCompany {
  user_id: number;
  company_id: number;
  can_manage_licenses: number;
  can_manage_staff: number;
  can_manage_assets: number;
  can_manage_invoices: number;
  can_order_licenses: number;
  can_access_shop: number;
  is_admin: number;
  company_name?: string;
  email?: string;
  is_vip?: number;
}

export interface EmailTemplate {
  id: number;
  name: string;
  subject: string;
  body: string;
}

export interface Product {
  id: number;
  name: string;
  sku: string;
  vendor_sku: string;
  description: string;
  image_url: string | null;
  price: number;
  vip_price: number | null;
  stock: number;
  archived: number;
  category_id: number | null;
  category_name?: string;
}

export interface Category {
  id: number;
  name: string;
}

export interface ProductCompanyRestriction {
  product_id: number;
  company_id: number;
  company_name: string;
}

export interface OrderItem {
  id: number;
  order_number: string;
  user_id: number;
  company_id: number;
  product_id: number;
  quantity: number;
  order_date: Date;
  status: string;
  notes: string | null;
  po_number: string | null;
  product_name: string;
  sku: string;
  description: string;
  image_url: string | null;
  price: number;
}

export interface OrderSummary {
  order_number: string;
  order_date: Date;
  status: string;
  notes: string | null;
  po_number: string | null;
}

export interface Asset {
  id: number;
  company_id: number;
  name: string;
  type: string;
  serial_number: string;
  status: string;
}

export interface Invoice {
  id: number;
  company_id: number;
  invoice_number: string;
  amount: number;
  due_date: string;
  status: string;
}


export interface Staff {
  id: number;
  company_id: number;
  first_name: string;
  last_name: string;
  email: string;
  mobile_phone?: string | null;
  date_onboarded: string | null;
  date_offboarded?: string | null;
  enabled: number;
  street?: string | null;
  city?: string | null;
  state?: string | null;
  postcode?: string | null;
  country?: string | null;
  department?: string | null;
  job_title?: string | null;
  org_company?: string | null;
  manager_name?: string | null;
  account_action?: string | null;
  verification_code?: string | null;
  verification_admin_name?: string | null;
}

export interface StaffVerificationCode {
  staff_id: number;
  admin_name: string | null;
}

export interface ApiKey {
  id: number;
  api_key: string;
  description: string;
  expiry_date: string | null;
}

export interface ApiKeyUsage {
  ip_address: string;
  usage_count: number;
  last_used_at: string;
}

export interface ApiKeyWithUsage extends ApiKey {
  usage_count: number;
  last_used_at: string | null;
  ips: ApiKeyUsage[];
}

export interface AuditLog {
  id: number;
  user_id: number | null;
  action: string;
  value: string | null;
  api_key: string | null;
  ip_address: string | null;
  created_at: string;
  email: string | null;
  company_id: number | null;
  company_name: string | null;
}

export interface SiteSettings {
  company_name: string | null;
  login_logo: string | null;
  sidebar_logo: string | null;
}

export interface Form {
  id: number;
  name: string;
  url: string;
  description: string;
}

export async function getUserByEmail(email: string): Promise<User | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM users WHERE email = ?', [email]);
  return (rows as User[])[0] || null;
}

export async function getCompanyById(id: number): Promise<Company | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM companies WHERE id = ?', [id]);
  const row = (rows as RowDataPacket[])[0];
  return row ? ({ ...(row as any), is_vip: Number(row.is_vip) } as Company) : null;
}

export async function getCompanyBySyncroId(
  syncroId: string
): Promise<Company | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM companies WHERE syncro_company_id = ?',
    [syncroId]
  );
  const row = (rows as RowDataPacket[])[0];
  return row ? ({ ...(row as any), is_vip: Number(row.is_vip) } as Company) : null;
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

export async function getAllApps(): Promise<App[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM apps');
  return rows as App[];
}

export async function createApp(
  sku: string,
  name: string,
  defaultPrice: number,
  contractTerm: string
): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO apps (sku, name, default_price, contract_term) VALUES (?, ?, ?, ?)',
    [sku, name, defaultPrice, contractTerm]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function getAppById(id: number): Promise<App | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM apps WHERE id = ?',
    [id]
  );
  return (rows as App[])[0] || null;
}

export async function updateApp(
  id: number,
  sku: string,
  name: string,
  defaultPrice: number,
  contractTerm: string
): Promise<void> {
  await pool.execute(
    'UPDATE apps SET sku = ?, name = ?, default_price = ?, contract_term = ? WHERE id = ?',
    [sku, name, defaultPrice, contractTerm, id]
  );
}

export async function deleteApp(id: number): Promise<void> {
  await pool.execute('DELETE FROM apps WHERE id = ?', [id]);
}

export async function upsertCompanyAppPrice(
  companyId: number,
  appId: number,
  price: number
): Promise<void> {
  await pool.execute(
    `INSERT INTO company_app_prices (company_id, app_id, price)
     VALUES (?, ?, ?)
     ON DUPLICATE KEY UPDATE price = VALUES(price)` ,
    [companyId, appId, price]
  );
}

export async function getAppPrice(
  companyId: number,
  appId: number
): Promise<number | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT price FROM company_app_prices WHERE company_id = ? AND app_id = ?',
    [companyId, appId]
  );
  return rows[0] ? (rows[0] as any).price : null;
}

export async function getCompanyAppPrices(): Promise<
  (CompanyAppPrice & { company_name: string; app_name: string; sku: string })[]
> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT cap.company_id, cap.app_id, cap.price, c.name AS company_name, a.name AS app_name, a.sku AS sku
     FROM company_app_prices cap
     JOIN companies c ON cap.company_id = c.id
     JOIN apps a ON cap.app_id = a.id`
  );
  return rows as any;
}

export async function deleteCompanyAppPrice(
  companyId: number,
  appId: number
): Promise<void> {
  await pool.execute(
    'DELETE FROM company_app_prices WHERE company_id = ? AND app_id = ?',
    [companyId, appId]
  );
}

export async function getUserCount(): Promise<number> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT COUNT(*) as count FROM users');
  return (rows[0] as { count: number }).count;
}

export async function createCompany(
  name: string,
  address?: string,
  isVip = false,
  syncroCompanyId?: string,
  xeroId?: string
): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO companies (name, address, is_vip, syncro_company_id, xero_id) VALUES (?, ?, ?, ?, ?)',
    [
      name,
      address || null,
      isVip ? 1 : 0,
      syncroCompanyId || null,
      xeroId || null,
    ]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function updateCompanyVipStatus(id: number, isVip: boolean): Promise<void> {
  await pool.execute('UPDATE companies SET is_vip = ? WHERE id = ?', [isVip ? 1 : 0, id]);
}

export async function updateCompanyIds(
  id: number,
  syncroCompanyId: string | null,
  xeroId: string | null,
  isVip: boolean
): Promise<void> {
  await pool.execute(
    'UPDATE companies SET syncro_company_id = ?, xero_id = ?, is_vip = ? WHERE id = ?',
    [syncroCompanyId, xeroId, isVip ? 1 : 0, id]
  );
}

export async function createUser(
  email: string,
  passwordHash: string,
  companyId: number,
  forcePasswordChange = false
): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO users (email, password_hash, company_id, force_password_change) VALUES (?, ?, ?, ?)',
    [email, passwordHash, companyId, forcePasswordChange ? 1 : 0]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function createLicense(
  companyId: number,
  name: string,
  platform: string,
  count: number,
  expiryDate: string | null,
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
  return (rows as RowDataPacket[]).map((row) => ({
    ...(row as any),
    is_vip: Number(row.is_vip),
  })) as Company[];
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
    `SELECT uc.user_id, uc.company_id, uc.can_manage_licenses, uc.can_manage_staff, uc.can_manage_assets, uc.can_manage_invoices, uc.can_order_licenses, uc.can_access_shop, uc.is_admin, c.name AS company_name, c.is_vip AS is_vip
     FROM user_companies uc JOIN companies c ON uc.company_id = c.id
     WHERE uc.user_id = ?`,
    [userId]
  );
  return (rows as RowDataPacket[]).map((row) => ({
    ...(row as any),
    is_vip: Number(row.is_vip),
  })) as UserCompany[];
}

export async function getUserCompanyAssignments(companyId?: number): Promise<UserCompany[]> {
  let sql = `SELECT uc.user_id, uc.company_id, uc.can_manage_licenses, uc.can_manage_staff, uc.can_manage_assets, uc.can_manage_invoices, uc.can_order_licenses, uc.can_access_shop, uc.is_admin, c.name AS company_name, c.is_vip AS is_vip, u.email
     FROM user_companies uc
     JOIN users u ON uc.user_id = u.id
     JOIN companies c ON uc.company_id = c.id`;
  const params: any[] = [];
  if (companyId) {
    sql += ' WHERE uc.company_id = ?';
    params.push(companyId);
  }
  sql += ' ORDER BY u.email, c.name';
  const [rows] = await pool.query<RowDataPacket[]>(sql, params);
  return (rows as RowDataPacket[]).map((row) => ({
    ...(row as any),
    is_vip: Number(row.is_vip),
  })) as UserCompany[];
}

export async function assignUserToCompany(
  userId: number,
  companyId: number,
  canManageLicenses: boolean,
  canManageStaff: boolean,
  canManageAssets: boolean,
  canManageInvoices: boolean,
  isAdmin: boolean,
  canOrderLicenses: boolean,
  canAccessShop: boolean
): Promise<void> {
  await pool.execute(
    `INSERT INTO user_companies (user_id, company_id, can_manage_licenses, can_manage_staff, can_manage_assets, can_manage_invoices, is_admin, can_order_licenses, can_access_shop)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE can_manage_licenses = VALUES(can_manage_licenses), can_manage_staff = VALUES(can_manage_staff), can_manage_assets = VALUES(can_manage_assets), can_manage_invoices = VALUES(can_manage_invoices), is_admin = VALUES(is_admin), can_order_licenses = VALUES(can_order_licenses), can_access_shop = VALUES(can_access_shop)`,
    [
      userId,
      companyId,
      canManageLicenses ? 1 : 0,
      canManageStaff ? 1 : 0,
      canManageAssets ? 1 : 0,
      canManageInvoices ? 1 : 0,
      isAdmin ? 1 : 0,
      canOrderLicenses ? 1 : 0,
      canAccessShop ? 1 : 0,
    ]
  );
}

export async function updateUserCompanyPermission(
  userId: number,
  companyId: number,
  field:
    | 'can_manage_licenses'
    | 'can_manage_staff'
    | 'can_manage_assets'
    | 'can_manage_invoices'
    | 'can_order_licenses'
    | 'can_access_shop'
    | 'is_admin',
  value: boolean
): Promise<void> {
  await pool.execute(
    `UPDATE user_companies SET ${field} = ? WHERE user_id = ? AND company_id = ?`,
    [value ? 1 : 0, userId, companyId]
  );
}

export async function getStaffByCompany(
  companyId: number,
  enabled?: boolean
): Promise<Staff[]> {
  let sql =
    'SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name FROM staff s LEFT JOIN staff_verification_codes svc ON s.id = svc.staff_id WHERE s.company_id = ?';
  const params: any[] = [companyId];
  if (enabled !== undefined) {
    sql += ' AND s.enabled = ?';
    params.push(enabled ? 1 : 0);
  }
  const [rows] = await pool.query<RowDataPacket[]>(sql, params);
  return rows as Staff[];
}

export async function getAllStaff(
  accountAction?: string,
  email?: string
): Promise<Staff[]> {
  let sql =
    'SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name FROM staff s LEFT JOIN staff_verification_codes svc ON s.id = svc.staff_id';
  const params: any[] = [];
  const conditions: string[] = [];
  if (accountAction) {
    conditions.push('s.account_action = ?');
    params.push(accountAction);
  }
  if (email) {
    conditions.push('s.email LIKE ?');
    params.push(`%${email}%`);
  }
  if (conditions.length) {
    sql += ' WHERE ' + conditions.join(' AND ');
  }
  const [rows] = await pool.query<RowDataPacket[]>(sql, params);
  return rows as Staff[];
}

export async function getStaffById(id: number): Promise<Staff | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name FROM staff s LEFT JOIN staff_verification_codes svc ON s.id = svc.staff_id WHERE s.id = ?',
    [id]
  );
  return (rows as Staff[])[0] || null;
}

export async function addStaff(
  companyId: number,
  firstName: string,
  lastName: string,
  email: string,
  mobilePhone: string | null,
  dateOnboarded: string | null,
  dateOffboarded: string | null,
  enabled: boolean,
  street?: string | null,
  city?: string | null,
  state?: string | null,
  postcode?: string | null,
  country?: string | null,
  department?: string | null,
  jobTitle?: string | null,
  orgCompany?: string | null,
  managerName?: string | null,
  accountAction?: string | null
): Promise<void> {
  await pool.execute(
    'INSERT INTO staff (company_id, first_name, last_name, email, mobile_phone, date_onboarded, date_offboarded, enabled, street, city, state, postcode, country, department, job_title, org_company, manager_name, account_action) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
    [
      companyId,
      firstName,
      lastName,
      email,
      mobilePhone,
      dateOnboarded,
      dateOffboarded,
      enabled ? 1 : 0,
      street || null,
      city || null,
      state || null,
      postcode || null,
      country || null,
      department || null,
      jobTitle || null,
      orgCompany || null,
      managerName || null,
      accountAction || null,
    ]
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
  mobilePhone: string | null,
  dateOnboarded: string | null,
  dateOffboarded: string | null,
  enabled: boolean,
  street?: string | null,
  city?: string | null,
  state?: string | null,
  postcode?: string | null,
  country?: string | null,
  department?: string | null,
  jobTitle?: string | null,
  orgCompany?: string | null,
  managerName?: string | null,
  accountAction?: string | null
): Promise<void> {
  await pool.execute(
    'UPDATE staff SET company_id = ?, first_name = ?, last_name = ?, email = ?, mobile_phone = ?, date_onboarded = ?, date_offboarded = ?, enabled = ?, street = ?, city = ?, state = ?, postcode = ?, country = ?, department = ?, job_title = ?, org_company = ?, manager_name = ?, account_action = ? WHERE id = ?',
    [
      companyId,
      firstName,
      lastName,
      email,
      mobilePhone,
      dateOnboarded,
      dateOffboarded,
      enabled ? 1 : 0,
      street || null,
      city || null,
      state || null,
      postcode || null,
      country || null,
      department || null,
      jobTitle || null,
      orgCompany || null,
      managerName || null,
      accountAction || null,
      id,
    ]
  );
}

export async function deleteStaff(id: number): Promise<void> {
  await pool.execute('DELETE FROM staff WHERE id = ?', [id]);
}

export async function setStaffVerificationCode(
  staffId: number,
  code: string,
  adminName: string
): Promise<void> {
  await pool.execute(
    `INSERT INTO staff_verification_codes (staff_id, code, admin_name, created_at)
     VALUES (?, ?, ?, NOW())
     ON DUPLICATE KEY UPDATE code = VALUES(code), admin_name = VALUES(admin_name), created_at = VALUES(created_at)` ,
    [staffId, code, adminName]
  );
}

export async function updateUserName(
  id: number,
  firstName: string,
  lastName: string
): Promise<void> {
  await pool.execute('UPDATE users SET first_name = ?, last_name = ? WHERE id = ?', [
    firstName,
    lastName,
    id,
  ]);
}

export async function purgeExpiredVerificationCodes(): Promise<void> {
  await pool.execute(
    'DELETE FROM staff_verification_codes WHERE created_at < (NOW() - INTERVAL 5 MINUTE)'
  );
}

export async function getVerificationByCode(
  code: string
): Promise<StaffVerificationCode | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT staff_id, admin_name FROM staff_verification_codes WHERE code = ?',
    [code]
  );
  return rows.length ? (rows[0] as StaffVerificationCode) : null;
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

export async function setUserForcePasswordChange(
  id: number,
  force: boolean
): Promise<void> {
  await pool.execute('UPDATE users SET force_password_change = ? WHERE id = ?', [
    force ? 1 : 0,
    id,
  ]);
}

export async function updateUserPassword(
  id: number,
  passwordHash: string
): Promise<void> {
  await pool.execute('UPDATE users SET password_hash = ? WHERE id = ?', [passwordHash, id]);
}

export async function getEmailTemplate(
  name: string
): Promise<EmailTemplate | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM email_templates WHERE name = ?',
    [name]
  );
  return (rows as EmailTemplate[])[0] || null;
}

export async function upsertEmailTemplate(
  name: string,
  subject: string,
  body: string
): Promise<void> {
  await pool.execute(
    `INSERT INTO email_templates (name, subject, body) VALUES (?, ?, ?)
     ON DUPLICATE KEY UPDATE subject = VALUES(subject), body = VALUES(body)`,
    [name, subject, body]
  );
}

export async function getUserTotpAuthenticators(
  userId: number
): Promise<UserTotpAuthenticator[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT id, user_id, name, secret FROM user_totp_authenticators WHERE user_id = ?',
    [userId]
  );
  return rows as UserTotpAuthenticator[];
}

export async function addUserTotpAuthenticator(
  userId: number,
  name: string,
  secret: string
): Promise<void> {
  await pool.execute(
    'INSERT INTO user_totp_authenticators (user_id, name, secret) VALUES (?, ?, ?)',
    [userId, name, secret]
  );
}

export async function deleteUserTotpAuthenticator(id: number): Promise<void> {
  await pool.execute('DELETE FROM user_totp_authenticators WHERE id = ?', [id]);
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
  expiryDate: string | null,
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

export async function getStaffForLicense(
  licenseId: number
): Promise<Staff[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT s.*, svc.code AS verification_code
     FROM staff s
     LEFT JOIN staff_verification_codes svc ON s.id = svc.staff_id
     JOIN staff_licenses sl ON s.id = sl.staff_id
     WHERE sl.license_id = ?`,
    [licenseId]
  );
  return rows as Staff[];
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

export async function getApiKeysWithUsage(): Promise<ApiKeyWithUsage[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT ak.*, COALESCE(SUM(aku.usage_count),0) AS usage_count,
            MAX(aku.last_used_at) AS last_used_at
     FROM api_keys ak
     LEFT JOIN api_key_usage aku ON ak.id = aku.api_key_id
     GROUP BY ak.id`
  );
  const keys = rows as any[];
  for (const key of keys) {
    const [ips] = await pool.query<RowDataPacket[]>(
      'SELECT ip_address, usage_count, last_used_at FROM api_key_usage WHERE api_key_id = ?',
      [key.id]
    );
    key.ips = ips;
  }
  return keys as ApiKeyWithUsage[];
}

export async function recordApiKeyUsage(
  apiKeyId: number,
  ipAddress: string
): Promise<void> {
  await pool.execute(
    `INSERT INTO api_key_usage (api_key_id, ip_address, usage_count, last_used_at)
     VALUES (?, ?, 1, NOW())
     ON DUPLICATE KEY UPDATE usage_count = usage_count + 1, last_used_at = NOW()`,
    [apiKeyId, ipAddress]
  );
}

export async function logAudit(options: {
  userId?: number | null;
  companyId?: number | null;
  action: string;
  previousValue?: string | null;
  newValue?: string | null;
  apiKey?: string | null;
  ipAddress?: string | null;
}): Promise<void> {
  const snippet = options.apiKey
    ? `${options.apiKey.slice(0, 3)}.........${options.apiKey.slice(-3)}`
    : null;
  let valueToLog = options.newValue || null;
  if (options.previousValue && options.newValue) {
    try {
      const prev = JSON.parse(options.previousValue);
      const next = JSON.parse(options.newValue);
      const diff: any = {};
      for (const key of Object.keys(next)) {
        if (JSON.stringify(next[key]) !== JSON.stringify(prev[key])) {
          diff[key] = next[key];
        }
      }
      valueToLog = JSON.stringify(diff);
    } catch {
      valueToLog = options.newValue;
    }
  }
  await pool.execute(
    'INSERT INTO audit_logs (user_id, company_id, action, previous_value, new_value, api_key, ip_address) VALUES (?, ?, ?, ?, ?, ?, ?)',
    [
      options.userId || null,
      options.companyId || null,
      options.action,
      options.previousValue || null,
      valueToLog,
      snippet,
      options.ipAddress || null,
    ]
  );
}

export async function getAuditLogs(companyId?: number): Promise<AuditLog[]> {
  let sql = `SELECT al.id, al.user_id, al.company_id, al.action, al.new_value AS value, al.api_key, al.ip_address, al.created_at,
             u.email, c.name AS company_name
             FROM audit_logs al
             LEFT JOIN users u ON al.user_id = u.id
             LEFT JOIN companies c ON al.company_id = c.id`;
  const params: any[] = [];
  if (companyId) {
    sql += ' WHERE al.company_id = ?';
    params.push(companyId);
  }
  sql += ' ORDER BY al.created_at DESC';
  const [rows] = await pool.query<RowDataPacket[]>(sql, params);
  return (rows as any[]).map((r) => ({
    ...r,
    api_key: r.api_key ? `${r.api_key.slice(0, 3)}.........${r.api_key.slice(-3)}` : null,
  })) as AuditLog[];
}

export async function getAssetsByCompany(companyId: number): Promise<Asset[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM assets WHERE company_id = ?',
    [companyId]
  );
  return rows as Asset[];
}

export async function upsertAsset(
  companyId: number,
  name: string,
  type: string,
  serialNumber: string,
  status: string
): Promise<void> {
  await pool.execute(
    'INSERT INTO assets (company_id, name, type, serial_number, status) VALUES (?, ?, ?, ?, ?)',
    [companyId, name, type, serialNumber, status]
  );
}

export async function getAssetById(id: number): Promise<Asset | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM assets WHERE id = ?', [id]);
  return (rows as Asset[])[0] || null;
}

export async function updateAsset(
  id: number,
  companyId: number,
  name: string,
  type: string,
  serialNumber: string,
  status: string
): Promise<void> {
  await pool.execute(
    'UPDATE assets SET company_id = ?, name = ?, type = ?, serial_number = ?, status = ? WHERE id = ?',
    [companyId, name, type, serialNumber, status, id]
  );
}

export async function deleteAsset(id: number): Promise<void> {
  await pool.execute('DELETE FROM assets WHERE id = ?', [id]);
}

export async function getInvoicesByCompany(companyId: number): Promise<Invoice[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM invoices WHERE company_id = ?',
    [companyId]
  );
  return rows as Invoice[];
}

export async function upsertInvoice(
  companyId: number,
  invoiceNumber: string,
  amount: number,
  dueDate: string,
  status: string
): Promise<void> {
  await pool.execute(
    'INSERT INTO invoices (company_id, invoice_number, amount, due_date, status) VALUES (?, ?, ?, ?, ?)',
    [companyId, invoiceNumber, amount, dueDate, status]
  );
}

export async function getInvoiceById(id: number): Promise<Invoice | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM invoices WHERE id = ?', [id]);
  return (rows as Invoice[])[0] || null;
}

export async function updateInvoice(
  id: number,
  companyId: number,
  invoiceNumber: string,
  amount: number,
  dueDate: string,
  status: string
): Promise<void> {
  await pool.execute(
    'UPDATE invoices SET company_id = ?, invoice_number = ?, amount = ?, due_date = ?, status = ? WHERE id = ?',
    [companyId, invoiceNumber, amount, dueDate, status, id]
  );
}

export async function deleteInvoice(id: number): Promise<void> {
  await pool.execute('DELETE FROM invoices WHERE id = ?', [id]);
}

export async function getAllCategories(): Promise<Category[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM shop_categories ORDER BY name'
  );
  return rows as Category[];
}

export async function getCategoryById(id: number): Promise<Category | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM shop_categories WHERE id = ?',
    [id]
  );
  return (rows as Category[])[0] || null;
}

export async function createCategory(name: string): Promise<number> {
  const [result] = await pool.execute<ResultSetHeader>(
    'INSERT INTO shop_categories (name) VALUES (?)',
    [name]
  );
  return (result as ResultSetHeader).insertId;
}

export async function updateCategory(
  id: number,
  name: string
): Promise<void> {
  await pool.execute('UPDATE shop_categories SET name = ? WHERE id = ?', [
    name,
    id,
  ]);
}

export async function deleteCategory(id: number): Promise<void> {
  await pool.execute('DELETE FROM shop_categories WHERE id = ?', [id]);
}

export async function getAllProducts(
  includeArchived = false,
  companyId?: number,
  categoryId?: number
): Promise<Product[]> {
  let sql =
    'SELECT p.*, c.name AS category_name FROM shop_products p LEFT JOIN shop_categories c ON p.category_id = c.id';
  const params: any[] = [];
  if (companyId !== undefined) {
    sql +=
      ' LEFT JOIN shop_product_exclusions e ON p.id = e.product_id AND e.company_id = ?';
    params.push(companyId);
  }
  const conditions: string[] = [];
  if (!includeArchived) {
    conditions.push('p.archived = 0');
  }
  if (companyId !== undefined) {
    conditions.push('e.product_id IS NULL');
  }
  if (categoryId !== undefined) {
    conditions.push('p.category_id = ?');
    params.push(categoryId);
  }
  if (conditions.length > 0) {
    sql += ' WHERE ' + conditions.join(' AND ');
  }
  const [rows] = await pool.query<RowDataPacket[]>(sql, params);
  return (rows as RowDataPacket[]).map((row) => ({
    ...(row as any),
    price: Number(row.price),
    vip_price: row.vip_price !== null ? Number(row.vip_price) : null,
    category_id:
      row.category_id !== null ? Number(row.category_id) : null,
  })) as Product[];
}

export async function getProductById(
  id: number,
  includeArchived = false,
  companyId?: number
): Promise<Product | null> {
  let sql =
    'SELECT p.*, c.name AS category_name FROM shop_products p LEFT JOIN shop_categories c ON p.category_id = c.id';
  const params: any[] = [];
  if (companyId !== undefined) {
    sql +=
      ' LEFT JOIN shop_product_exclusions e ON p.id = e.product_id AND e.company_id = ?';
    params.push(companyId);
  }
  sql += ' WHERE p.id = ?';
  params.push(id);
  if (!includeArchived) {
    sql += ' AND p.archived = 0';
  }
  if (companyId !== undefined) {
    sql += ' AND e.product_id IS NULL';
  }
  const [rows] = await pool.query<RowDataPacket[]>(sql, params);
  const row = (rows as RowDataPacket[])[0];
  return row
    ? ({
        ...(row as any),
        price: Number(row.price),
        vip_price: row.vip_price !== null ? Number(row.vip_price) : null,
        category_id:
          row.category_id !== null ? Number(row.category_id) : null,
      } as Product)
    : null;
}

export async function getProductBySku(
  sku: string,
  includeArchived = false,
  companyId?: number
): Promise<Product | null> {
  let sql =
    'SELECT p.*, c.name AS category_name FROM shop_products p LEFT JOIN shop_categories c ON p.category_id = c.id';
  const params: any[] = [];
  if (companyId !== undefined) {
    sql +=
      ' LEFT JOIN shop_product_exclusions e ON p.id = e.product_id AND e.company_id = ?';
    params.push(companyId);
  }
  sql += ' WHERE p.sku = ?';
  params.push(sku);
  if (!includeArchived) {
    sql += ' AND p.archived = 0';
  }
  if (companyId !== undefined) {
    sql += ' AND e.product_id IS NULL';
  }
  const [rows] = await pool.query<RowDataPacket[]>(sql, params);
  const row = (rows as RowDataPacket[])[0];
  return row
    ? ({
        ...(row as any),
        price: Number(row.price),
        vip_price: row.vip_price !== null ? Number(row.vip_price) : null,
        category_id:
          row.category_id !== null ? Number(row.category_id) : null,
      } as Product)
    : null;
}

export async function createProduct(
  name: string,
  sku: string,
  vendorSku: string,
  description: string,
  imageUrl: string | null,
  price: number,
  vipPrice: number | null,
  stock: number,
  categoryId: number | null
): Promise<number> {
  const [result] = await pool.execute<ResultSetHeader>(
    'INSERT INTO shop_products (name, sku, vendor_sku, description, image_url, price, vip_price, stock, category_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
    [
      name,
      sku,
      vendorSku,
      description,
      imageUrl,
      price,
      vipPrice,
      stock,
      categoryId,
    ]
  );
  return (result as ResultSetHeader).insertId;
}

export async function updateProduct(
  id: number,
  name: string,
  sku: string,
  vendorSku: string,
  description: string,
  imageUrl: string | null,
  price: number,
  vipPrice: number | null,
  stock: number,
  categoryId: number | null
): Promise<void> {
  await pool.execute(
    'UPDATE shop_products SET name = ?, sku = ?, vendor_sku = ?, description = ?, image_url = IFNULL(?, image_url), price = ?, vip_price = ?, stock = ?, category_id = ? WHERE id = ?',
    [
      name,
      sku,
      vendorSku,
      description,
      imageUrl,
      price,
      vipPrice,
      stock,
      categoryId,
      id,
    ]
  );
}

export async function archiveProduct(id: number): Promise<void> {
  await pool.execute('UPDATE shop_products SET archived = 1 WHERE id = ?', [id]);
}

export async function unarchiveProduct(id: number): Promise<void> {
  await pool.execute('UPDATE shop_products SET archived = 0 WHERE id = ?', [id]);
}

export async function deleteProduct(id: number): Promise<void> {
  await pool.execute('DELETE FROM shop_products WHERE id = ?', [id]);
}

export async function removeProductForCompany(
  productId: number,
  companyId: number
): Promise<void> {
  await pool.execute(
    'INSERT IGNORE INTO shop_product_exclusions (product_id, company_id) VALUES (?, ?)',
    [productId, companyId]
  );
}

export async function addProductForCompany(
  productId: number,
  companyId: number
): Promise<void> {
  await pool.execute(
    'DELETE FROM shop_product_exclusions WHERE product_id = ? AND company_id = ?',
    [productId, companyId]
  );
}

export async function getProductCompanyRestrictions(): Promise<
  ProductCompanyRestriction[]
> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT spe.product_id, spe.company_id, c.name AS company_name
     FROM shop_product_exclusions spe
     JOIN companies c ON spe.company_id = c.id`
  );
  return rows as ProductCompanyRestriction[];
}

export async function setProductCompanyExclusions(
  productId: number,
  companyIds: number[]
): Promise<void> {
  await pool.execute('DELETE FROM shop_product_exclusions WHERE product_id = ?', [
    productId,
  ]);
  if (companyIds.length === 0) {
    return;
  }
  const values = companyIds.map(() => '(?, ?)').join(', ');
  const params: (number | string)[] = [];
  companyIds.forEach((id) => {
    params.push(productId, id);
  });
  await pool.execute(
    `INSERT INTO shop_product_exclusions (product_id, company_id) VALUES ${values}`,
    params
  );
}

export async function createOrder(
  userId: number,
  companyId: number,
  productId: number,
  quantity: number,
  orderNumber: string,
  status: string,
  poNumber: string | null
): Promise<void> {
  const conn = await pool.getConnection();
  try {
    await conn.beginTransaction();
    await conn.execute(
      'INSERT INTO shop_orders (user_id, company_id, product_id, quantity, order_number, status, notes, po_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, companyId, productId, quantity, orderNumber, status, null, poNumber]
    );
    await conn.execute(
      'UPDATE shop_products SET stock = stock - ? WHERE id = ?',
      [quantity, productId]
    );
    await conn.commit();
  } catch (err) {
    await conn.rollback();
    throw err;
  } finally {
    conn.release();
  }
}

export async function getOrdersByCompany(companyId: number): Promise<OrderItem[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT o.*, p.name as product_name, IF(c.is_vip = 1 AND p.vip_price IS NOT NULL, p.vip_price, p.price) AS price, p.sku, p.description, p.image_url
     FROM shop_orders o
     JOIN shop_products p ON o.product_id = p.id
     JOIN companies c ON o.company_id = c.id
     WHERE o.company_id = ?`,
    [companyId]
  );
  return (rows as RowDataPacket[]).map((row) => ({
    ...(row as any),
    price: Number(row.price),
  })) as OrderItem[];
}

export async function getOrderSummariesByCompany(
  companyId: number
): Promise<OrderSummary[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT order_number, MAX(order_date) as order_date, MAX(status) as status, MAX(notes) as notes, MAX(po_number) as po_number
     FROM shop_orders WHERE company_id = ? GROUP BY order_number ORDER BY order_date DESC`,
    [companyId]
  );
  return rows as OrderSummary[];
}

export async function getOrderItems(
  orderNumber: string,
  companyId: number
): Promise<OrderItem[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT o.*, p.name as product_name, IF(c.is_vip = 1 AND p.vip_price IS NOT NULL, p.vip_price, p.price) AS price, p.sku, p.description, p.image_url
     FROM shop_orders o
     JOIN shop_products p ON o.product_id = p.id
     JOIN companies c ON o.company_id = c.id
     WHERE o.order_number = ? AND o.company_id = ?`,
    [orderNumber, companyId]
  );
  return (rows as RowDataPacket[]).map((row) => ({
    ...(row as any),
    price: Number(row.price),
  })) as OrderItem[];
}

export async function deleteOrder(
  orderNumber: string,
  companyId: number
): Promise<void> {
  const conn = await pool.getConnection();
  try {
    await conn.beginTransaction();
    const [rows] = await conn.query<RowDataPacket[]>(
      'SELECT product_id, quantity FROM shop_orders WHERE order_number = ? AND company_id = ?',
      [orderNumber, companyId]
    );
    for (const row of rows as any[]) {
      await conn.execute('UPDATE shop_products SET stock = stock + ? WHERE id = ?', [row.quantity, row.product_id]);
    }
    await conn.execute('DELETE FROM shop_orders WHERE order_number = ? AND company_id = ?', [orderNumber, companyId]);
    await conn.commit();
  } catch (err) {
    await conn.rollback();
    throw err;
  } finally {
    conn.release();
  }
}

export async function updateOrder(
  orderNumber: string,
  companyId: number,
  status: string,
  notes: string | null
): Promise<void> {
  await pool.execute(
    'UPDATE shop_orders SET status = ?, notes = ? WHERE order_number = ? AND company_id = ?',
    [status, notes, orderNumber, companyId]
  );
}

export async function createForm(
  name: string,
  url: string,
  description: string
): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO forms (name, url, description) VALUES (?, ?, ?)',
    [name, url, description]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function updateForm(
  id: number,
  name: string,
  url: string,
  description: string
): Promise<void> {
  await pool.execute(
    'UPDATE forms SET name = ?, url = ?, description = ? WHERE id = ?',
    [name, url, description, id]
  );
}

export async function deleteForm(id: number): Promise<void> {
  await pool.execute('DELETE FROM forms WHERE id = ?', [id]);
}

export async function getAllForms(): Promise<Form[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM forms ORDER BY name'
  );
  return rows as Form[];
}

export async function getFormsByCompany(companyId: number): Promise<Form[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT DISTINCT f.* FROM forms f
     JOIN form_permissions fp ON f.id = fp.form_id
     WHERE fp.company_id = ?
     ORDER BY f.name`,
    [companyId]
  );
  return rows as Form[];
}

export async function getFormsForUser(userId: number): Promise<Form[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT DISTINCT f.* FROM forms f JOIN form_permissions fp ON f.id = fp.form_id WHERE fp.user_id = ? ORDER BY f.name',
    [userId]
  );
  return rows as Form[];
}

export async function getFormPermissions(
  formId: number,
  companyId: number
): Promise<number[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT user_id FROM form_permissions WHERE form_id = ? AND company_id = ?',
    [formId, companyId]
  );
  return (rows as RowDataPacket[]).map((r) => r.user_id as number);
}

export async function updateFormPermissions(
  formId: number,
  companyId: number,
  userIds: number[]
): Promise<void> {
  await pool.execute(
    'DELETE FROM form_permissions WHERE form_id = ? AND company_id = ?',
    [formId, companyId]
  );
  if (userIds.length === 0) {
    return;
  }
  const values = userIds.map(() => '(?, ?, ?)').join(', ');
  const params: (number | string)[] = [];
  userIds.forEach((id) => {
    params.push(formId, id, companyId);
  });
  await pool.execute(
    `INSERT INTO form_permissions (form_id, user_id, company_id) VALUES ${values}`,
    params
  );
}

export interface FormPermissionEntry {
  form_id: number;
  form_name: string;
  user_id: number;
  email: string;
  company_id: number;
  company_name: string;
}

export async function getAllFormPermissionEntries(): Promise<FormPermissionEntry[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT fp.form_id, f.name AS form_name, fp.user_id, u.email,
            fp.company_id, c.name AS company_name
     FROM form_permissions fp
     JOIN forms f ON fp.form_id = f.id
     JOIN users u ON fp.user_id = u.id
     JOIN companies c ON fp.company_id = c.id
     ORDER BY u.email, c.name, f.name`
  );
  return rows as FormPermissionEntry[];
}

export async function deleteFormPermission(
  formId: number,
  userId: number,
  companyId: number
): Promise<void> {
  await pool.execute(
    'DELETE FROM form_permissions WHERE form_id = ? AND user_id = ? AND company_id = ?',
    [formId, userId, companyId]
  );
}
export interface OfficeGroup {
  id: number;
  company_id: number;
  name: string;
}

export interface OfficeGroupWithMembers extends OfficeGroup {
  members: Staff[];
}

export async function getOfficeGroupsByCompany(companyId: number): Promise<OfficeGroupWithMembers[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT og.id, og.company_id, og.name,
            s.id as staff_id, s.first_name, s.last_name, s.email, s.date_onboarded, s.enabled
     FROM office_groups og
     LEFT JOIN office_group_members ogm ON og.id = ogm.group_id
     LEFT JOIN staff s ON ogm.staff_id = s.id
     WHERE og.company_id = ?
     ORDER BY og.name, s.last_name, s.first_name`,
    [companyId]
  );
  const groupMap: Record<number, OfficeGroupWithMembers> = {};
  (rows as any[]).forEach(r => {
    if (!groupMap[r.id]) {
      groupMap[r.id] = { id: r.id, company_id: r.company_id, name: r.name, members: [] };
    }
    if (r.staff_id) {
      groupMap[r.id].members.push({
        id: r.staff_id,
        company_id: r.company_id,
        first_name: r.first_name,
        last_name: r.last_name,
        email: r.email,
        date_onboarded: r.date_onboarded,
        enabled: r.enabled,
      });
    }
  });
  return Object.values(groupMap);
}

export async function createOfficeGroup(companyId: number, name: string): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO office_groups (company_id, name) VALUES (?, ?)',
    [companyId, name]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function deleteOfficeGroup(id: number): Promise<void> {
  await pool.execute('DELETE FROM office_groups WHERE id = ?', [id]);
}

export async function updateOfficeGroupMembers(groupId: number, staffIds: number[]): Promise<void> {
  await pool.execute('DELETE FROM office_group_members WHERE group_id = ?', [groupId]);
  if (staffIds.length === 0) {
    return;
  }
  const values = staffIds.map(() => '(?, ?)').join(', ');
  const params: number[] = [];
  staffIds.forEach(id => { params.push(groupId, id); });
  await pool.execute(
    `INSERT INTO office_group_members (group_id, staff_id) VALUES ${values}`,
    params
  );
}

export async function getSiteSettings(): Promise<SiteSettings> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT company_name, login_logo, sidebar_logo FROM site_settings WHERE id = 1'
  );
  const row = (rows as SiteSettings[])[0];
  return {
    company_name: row?.company_name || null,
    login_logo: row?.login_logo || null,
    sidebar_logo: row?.sidebar_logo || null,
  };
}

export async function updateSiteSettings(
  companyName: string,
  loginLogo?: string | null,
  sidebarLogo?: string | null
): Promise<void> {
  await pool.query(
    'UPDATE site_settings SET company_name = ?, login_logo = COALESCE(?, login_logo), sidebar_logo = COALESCE(?, sidebar_logo) WHERE id = 1',
    [companyName, loginLogo ?? null, sidebarLogo ?? null]
  );
}
