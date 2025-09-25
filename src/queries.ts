import { pool } from './db';
import { RowDataPacket, ResultSetHeader } from 'mysql2';
import crypto from 'crypto';
import { encryptSecret } from './crypto';
import { logInfo } from './logger';

export interface User {
  id: number;
  email: string;
  password_hash: string;
  company_id: number;
  first_name?: string | null;
  last_name?: string | null;
  force_password_change?: number;
  mobile_phone?: string | null;
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
  display_name?: string;
}

export interface App {
  id: number;
  sku: string;
  vendor_sku: string | null;
  name: string;
  license_sku_id: string | null;
}

export interface CompanyAppPrice {
  company_id: number;
  app_id: number;
  price: number;
  payment_term: string;
  contract_term: string;
}

export interface AppPriceOption {
  id: number;
  app_id: number;
  payment_term: string;
  contract_term: string;
  price: number;
}

export interface UserCompany {
  user_id: number;
  company_id: number;
  can_manage_licenses: number;
  staff_permission: number;
  can_manage_office_groups: number;
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
  buy_price?: number | null;
  weight?: number | null;
  length?: number | null;
  width?: number | null;
  height?: number | null;
  stock_at?: string | null;
  warranty_length?: string | null;
  manufacturer?: string | null;
  stock_nsw?: number;
  stock_qld?: number;
  stock_vic?: number;
  stock_sa?: number;
  category_name?: string;
}

export interface ProductPriceAlert {
  id: number;
  product_id: number;
  price: number;
  vip_price: number | null;
  buy_price: number;
  threshold_price: number;
  triggered_at: string;
  emailed_at: string | null;
  resolved_at: string | null;
}

export interface ProductPriceAlertWithProduct extends ProductPriceAlert {
  product_name: string;
  product_sku: string;
}

export interface StockFeedItem {
  sku: string;
  product_name: string;
  product_name2?: string | null;
  rrp?: number | null;
  category_name?: string | null;
  on_hand_nsw: number;
  on_hand_qld: number;
  on_hand_vic: number;
  on_hand_sa: number;
  dbp?: number | null;
  weight?: number | null;
  length?: number | null;
  width?: number | null;
  height?: number | null;
  pub_date?: string | null;
  warranty_length?: string | null;
  manufacturer?: string | null;
  image_url?: string | null;
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
  shipping_status: string;
  notes: string | null;
  po_number: string | null;
  consignment_id: string | null;
  product_name: string;
  sku: string;
  description: string;
  image_url: string | null;
  price: number;
  eta: Date | null;
}

export interface OrderSummary {
  order_number: string;
  order_date: Date;
  status: string;
  shipping_status: string;
  notes: string | null;
  po_number: string | null;
  consignment_id: string | null;
  eta: Date | null;
}

export interface Asset {
  id: number;
  company_id: number;
  name: string;
  type: string | null;
  serial_number: string | null;
  status: string | null;
  os_name?: string | null;
  cpu_name?: string | null;
  ram_gb?: number | null;
  hdd_size?: string | null;
  last_sync?: string | null;
  motherboard_manufacturer?: string | null;
  form_factor?: string | null;
  last_user?: string | null;
  approx_age?: number | null;
  performance_score?: number | null;
  warranty_status?: string | null;
  warranty_end_date?: string | null;
  syncro_asset_id?: string | null;
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
  syncro_contact_id?: string | null;
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

function hashApiKey(key: string): string {
  const secret = process.env.API_KEY_SECRET;
  if (!secret) {
    throw new Error('API_KEY_SECRET not set');
  }
  return crypto.createHmac('sha256', secret).update(key).digest('hex');
}

export async function hashExistingApiKeys(): Promise<void> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT id, api_key FROM api_keys'
  );
  for (const row of rows as { id: number; api_key: string }[]) {
    if (!/^[0-9a-f]{64}$/i.test(row.api_key)) {
      const hashedKey = hashApiKey(row.api_key);
      await pool.execute('UPDATE api_keys SET api_key = ? WHERE id = ?', [hashedKey, row.id]);
    }
  }
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
  favicon: string | null;
}

export interface ShopSettings {
  discord_webhook_url: string | null;
}

export interface Form {
  id: number;
  name: string;
  url: string;
  description: string;
  embed_code: string | null;
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

export async function getHiddenSyncroCustomerIds(): Promise<string[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT syncro_customer_id FROM hidden_syncro_customers'
  );
  return (rows as RowDataPacket[]).map((r) => String(r.syncro_customer_id));
}

export async function hideSyncroCustomer(id: string): Promise<void> {
  await pool.query(
    'INSERT IGNORE INTO hidden_syncro_customers (syncro_customer_id) VALUES (?)',
    [id]
  );
}

export async function unhideSyncroCustomer(id: string): Promise<void> {
  await pool.query(
    'DELETE FROM hidden_syncro_customers WHERE syncro_customer_id = ?',
    [id]
  );
}

export async function getLicensesByCompany(companyId: number): Promise<License[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT l.id, l.company_id, l.name, l.platform, l.count, l.expiry_date, l.contract_term,
            COALESCE(a.name, l.platform) AS display_name,
            COUNT(DISTINCT sl.staff_id) AS allocated
     FROM licenses l
     LEFT JOIN apps a ON l.platform = a.vendor_sku
     LEFT JOIN staff_licenses sl ON l.id = sl.license_id
     WHERE l.company_id = ?
     GROUP BY l.id`,
    [companyId]
  );
  return rows as License[];
}

export async function getAllLicenses(): Promise<License[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT l.id, l.company_id, l.name, l.platform, l.count, l.expiry_date, l.contract_term,
            COALESCE(a.name, l.platform) AS display_name,
            COUNT(DISTINCT sl.staff_id) AS allocated
     FROM licenses l
     LEFT JOIN apps a ON l.platform = a.vendor_sku
     LEFT JOIN staff_licenses sl ON l.id = sl.license_id
     GROUP BY l.id`
  );
  return rows as License[];
}

export async function getLicenseById(id: number): Promise<License | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT l.id, l.company_id, l.name, l.platform, l.count, l.expiry_date, l.contract_term,
            COALESCE(a.name, l.platform) AS display_name,
            COUNT(DISTINCT sl.staff_id) AS allocated
     FROM licenses l
     LEFT JOIN apps a ON l.platform = a.vendor_sku
     LEFT JOIN staff_licenses sl ON l.id = sl.license_id
     WHERE l.id = ?
     GROUP BY l.id`,
    [id]
  );
  return (rows as License[])[0] || null;
}

export async function getLicenseByCompanyAndSku(
  companyId: number,
  sku: string
): Promise<License | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT l.id, l.company_id, l.name, l.platform, l.count, l.expiry_date, l.contract_term,
            COALESCE(a.name, l.platform) AS display_name,
            COUNT(DISTINCT sl.staff_id) AS allocated
     FROM licenses l
     LEFT JOIN apps a ON l.platform = a.vendor_sku
     LEFT JOIN staff_licenses sl ON l.id = sl.license_id
     WHERE l.company_id = ? AND l.platform = ?
     GROUP BY l.id`,
    [companyId, sku]
  );
  return (rows as License[])[0] || null;
}

export async function getAllApps(): Promise<App[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM apps');
  return rows as App[];
}

export async function createApp(
  sku: string,
  vendorSku: string | null,
  name: string,
  licenseSkuId: string | null = null
): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO apps (sku, vendor_sku, name, license_sku_id) VALUES (?, ?, ?, ?)',
    [sku, vendorSku, name, licenseSkuId]
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
  vendorSku: string | null,
  name: string,
  licenseSkuId: string | null = null
): Promise<void> {
  await pool.execute(
    'UPDATE apps SET sku = ?, vendor_sku = ?, name = ?, license_sku_id = ? WHERE id = ?',
    [sku, vendorSku, name, licenseSkuId, id]
  );
}

export async function getAppByVendorSku(
  vendorSku: string
): Promise<App | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM apps WHERE vendor_sku = ? OR sku = ? LIMIT 1',
    [vendorSku, vendorSku]
  );
  return (rows as App[])[0] || null;
}

export async function deleteApp(id: number): Promise<void> {
  await pool.execute('DELETE FROM apps WHERE id = ?', [id]);
}

export async function upsertCompanyAppPrice(
  companyId: number,
  appId: number,
  paymentTerm: string,
  contractTerm: string,
  price: number
): Promise<void> {
  await pool.execute(
    `INSERT INTO company_app_prices (company_id, app_id, payment_term, contract_term, price)
     VALUES (?, ?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE price = VALUES(price)` ,
    [companyId, appId, paymentTerm, contractTerm, price]
  );
}

export async function getAppPrice(
  companyId: number,
  appId: number,
  paymentTerm: string,
  contractTerm: string
): Promise<number | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT price FROM company_app_prices WHERE company_id = ? AND app_id = ? AND payment_term = ? AND contract_term = ?',
    [companyId, appId, paymentTerm, contractTerm]
  );
  return rows[0] ? (rows[0] as any).price : null;
}

export async function getCompanyAppPrices(): Promise<
  (CompanyAppPrice & {
    company_name: string;
    app_name: string;
    sku: string;
    vendor_sku: string | null;
  })[]
> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT cap.company_id, cap.app_id, cap.price, cap.payment_term, cap.contract_term,
            c.name AS company_name, a.name AS app_name, a.sku AS sku, a.vendor_sku AS vendor_sku
     FROM company_app_prices cap
     JOIN companies c ON cap.company_id = c.id
     JOIN apps a ON cap.app_id = a.id`
  );
  return rows as any;
}

export async function deleteCompanyAppPrice(
  companyId: number,
  appId: number,
  paymentTerm: string,
  contractTerm: string
): Promise<void> {
  await pool.execute(
    'DELETE FROM company_app_prices WHERE company_id = ? AND app_id = ? AND payment_term = ? AND contract_term = ?',
    [companyId, appId, paymentTerm, contractTerm]
  );
}

export async function addAppPriceOption(
  appId: number,
  paymentTerm: string,
  contractTerm: string,
  price: number
): Promise<void> {
  await pool.execute(
    `INSERT INTO app_price_options (app_id, payment_term, contract_term, price)
     VALUES (?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE price = VALUES(price)`,
    [appId, paymentTerm, contractTerm, price]
  );
}

export async function getAppPriceOptions(
  appId?: number
): Promise<AppPriceOption[]> {
  let sql = 'SELECT * FROM app_price_options';
  const params: any[] = [];
  if (appId !== undefined) {
    sql += ' WHERE app_id = ?';
    params.push(appId);
  }
  const [rows] = await pool.query<RowDataPacket[]>(sql, params);
  return rows as any;
}

export async function getAppPriceOption(
  appId: number,
  paymentTerm: string,
  contractTerm: string
): Promise<AppPriceOption | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM app_price_options WHERE app_id = ? AND payment_term = ? AND contract_term = ?',
    [appId, paymentTerm, contractTerm]
  );
  return (rows as AppPriceOption[])[0] || null;
}

export async function deleteAppPriceOption(id: number): Promise<void> {
  await pool.execute('DELETE FROM app_price_options WHERE id = ?', [id]);
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
  if (userId === 1) {
    const [rows] = await pool.query<RowDataPacket[]>(
      'SELECT id AS company_id, name AS company_name, is_vip FROM companies'
    );
    return (rows as RowDataPacket[]).map((row) => ({
      user_id: userId,
      company_id: row.company_id as number,
      company_name: row.company_name as string,
      is_vip: Number(row.is_vip),
      can_manage_licenses: 1,
      staff_permission: 3,
      can_manage_office_groups: 1,
      can_manage_assets: 1,
      can_manage_invoices: 1,
      can_order_licenses: 1,
      can_access_shop: 1,
      is_admin: 1,
    })) as UserCompany[];
  }
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT uc.user_id, uc.company_id, uc.can_manage_licenses, uc.staff_permission, uc.can_manage_office_groups, uc.can_manage_assets, uc.can_manage_invoices, uc.can_order_licenses, uc.can_access_shop, uc.is_admin, c.name AS company_name, c.is_vip AS is_vip
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
  let sql = `SELECT uc.user_id, uc.company_id, uc.can_manage_licenses, uc.staff_permission, uc.can_manage_office_groups, uc.can_manage_assets, uc.can_manage_invoices, uc.can_order_licenses, uc.can_access_shop, uc.is_admin, c.name AS company_name, c.is_vip AS is_vip, u.email
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
    can_manage_licenses: Number(row.can_manage_licenses),
    staff_permission: Number(row.staff_permission),
    can_manage_office_groups: Number(row.can_manage_office_groups),
    can_manage_assets: Number(row.can_manage_assets),
    can_manage_invoices: Number(row.can_manage_invoices),
    can_order_licenses: Number(row.can_order_licenses),
    can_access_shop: Number(row.can_access_shop),
    is_admin: Number(row.is_admin),
    is_vip: Number(row.is_vip),
  })) as UserCompany[];
}

export async function assignUserToCompany(
  userId: number,
  companyId: number,
  canManageLicenses: boolean,
  staffPermission: number,
  canManageOfficeGroups: boolean,
  canManageAssets: boolean,
  canManageInvoices: boolean,
  isAdmin: boolean,
  canOrderLicenses: boolean,
  canAccessShop: boolean
): Promise<void> {
  await pool.execute(
    `INSERT INTO user_companies (user_id, company_id, can_manage_licenses, staff_permission, can_manage_office_groups, can_manage_assets, can_manage_invoices, is_admin, can_order_licenses, can_access_shop)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE can_manage_licenses = VALUES(can_manage_licenses), staff_permission = VALUES(staff_permission), can_manage_office_groups = VALUES(can_manage_office_groups), can_manage_assets = VALUES(can_manage_assets), can_manage_invoices = VALUES(can_manage_invoices), is_admin = VALUES(is_admin), can_order_licenses = VALUES(can_order_licenses), can_access_shop = VALUES(can_access_shop)`,
    [
      userId,
      companyId,
      canManageLicenses ? 1 : 0,
      staffPermission,
      canManageOfficeGroups ? 1 : 0,
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
    | 'can_manage_office_groups'
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

export async function updateUserCompanyStaffPermission(
  userId: number,
  companyId: number,
  permission: number
): Promise<void> {
  await pool.execute(
    `UPDATE user_companies SET staff_permission = ? WHERE user_id = ? AND company_id = ?`,
    [permission, userId, companyId]
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

export async function getStaffByCompanyAndEmail(
  companyId: number,
  email: string
): Promise<Staff | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name FROM staff s LEFT JOIN staff_verification_codes svc ON s.id = svc.staff_id WHERE s.company_id = ? AND LOWER(s.email) = LOWER(?)',
    [companyId, email]
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
  accountAction?: string | null,
  syncroContactId?: string | null
): Promise<void> {
  await pool.execute(
    'INSERT INTO staff (company_id, first_name, last_name, email, mobile_phone, date_onboarded, date_offboarded, enabled, street, city, state, postcode, country, department, job_title, org_company, manager_name, account_action, syncro_contact_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
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
      syncroContactId || null,
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
  accountAction?: string | null,
  syncroContactId?: string | null
): Promise<void> {
  await pool.execute(
    'UPDATE staff SET company_id = ?, first_name = ?, last_name = ?, email = ?, mobile_phone = ?, date_onboarded = ?, date_offboarded = ?, enabled = ?, street = ?, city = ?, state = ?, postcode = ?, country = ?, department = ?, job_title = ?, org_company = ?, manager_name = ?, account_action = ?, syncro_contact_id = ? WHERE id = ?',
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
      syncroContactId || null,
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

export async function updateUserMobile(
  id: number,
  mobilePhone: string | null
): Promise<void> {
  await pool.execute('UPDATE users SET mobile_phone = ? WHERE id = ?', [
    mobilePhone,
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

export async function createPasswordToken(
  userId: number,
  token: string,
  expiresAt: Date
): Promise<void> {
  await pool.execute(
    'INSERT INTO password_tokens (token, user_id, expires_at) VALUES (?, ?, ?)',
    [token, userId, expiresAt]
  );
}

export async function getUserIdByPasswordToken(
  token: string
): Promise<number | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT user_id FROM password_tokens WHERE token = ? AND used = 0 AND expires_at > NOW()',
    [token]
  );
  return rows.length ? (rows[0] as any).user_id : null;
}

export async function markPasswordTokenUsed(token: string): Promise<void> {
  await pool.execute('UPDATE password_tokens SET used = 1 WHERE token = ?', [token]);
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
    [userId, name, encryptSecret(secret)]
  );
}

export async function deleteUserTotpAuthenticator(id: number): Promise<void> {
  await pool.execute('DELETE FROM user_totp_authenticators WHERE id = ?', [id]);
}

export async function encryptExistingTotpSecrets(): Promise<void> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT id, secret FROM user_totp_authenticators'
  );
  const updates = (rows as { id: number; secret: string }[])
    .filter((r) => !r.secret.includes(':'))
    .map((r) =>
      pool.execute('UPDATE user_totp_authenticators SET secret = ? WHERE id = ?', [
        encryptSecret(r.secret),
        r.id,
      ])
    );
  await Promise.all(updates);
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
  description: string,
  expiryDate?: string
): Promise<string> {
  const rawKey = crypto.randomBytes(32).toString('hex');
  const hashedKey = hashApiKey(rawKey);
  await pool.execute(
    'INSERT INTO api_keys (api_key, description, expiry_date) VALUES (?, ?, ?)',
    [hashedKey, description, expiryDate || null]
  );
  return rawKey;
}

export async function getApiKeys(): Promise<ApiKey[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM api_keys');
  return rows as ApiKey[];
}

export async function deleteApiKey(id: number): Promise<void> {
  await pool.execute('DELETE FROM api_keys WHERE id = ?', [id]);
}

export async function getApiKeyRecord(apiKey: string): Promise<ApiKey | null> {
  const hashedKey = hashApiKey(apiKey);
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM api_keys WHERE api_key = ? AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE())',
    [hashedKey]
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
  logInfo('Audit log entry', {
    action: options.action,
    userId: options.userId || null,
    companyId: options.companyId || null,
    apiKey: snippet,
    ipAddress: options.ipAddress || null,
    value: valueToLog,
  });
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

function toMysqlDatetime(date?: string | null): string | null {
  if (!date) return null;
  const d = new Date(date);
  if (isNaN(d.getTime())) return null;
  return d.toISOString().slice(0, 19).replace('T', ' ');
}

function toMysqlDate(date?: string | null): string | null {
  if (!date) return null;
  const d = new Date(date);
  if (isNaN(d.getTime())) return null;
  return d.toISOString().slice(0, 10);
}

export async function upsertAsset(
  companyId: number,
  name: string,
  type: string | null,
  serialNumber: string | null,
  status: string | null,
  osName?: string | null,
  cpuName?: string | null,
  ramGb?: number | null,
  hddSize?: string | null,
  lastSync?: string | null,
  motherboardManufacturer?: string | null,
  formFactor?: string | null,
  lastUser?: string | null,
  approxAge?: number | null,
  performanceScore?: number | null,
  warrantyStatus?: string | null,
  warrantyEndDate?: string | null,
  syncroAssetId?: string | null
): Promise<void> {
  const syncId = syncroAssetId ?? null;
  const lastSyncDb = toMysqlDatetime(lastSync);
  const warrantyEndDb = toMysqlDate(warrantyEndDate);
  let rows: RowDataPacket[] = [];
  if (syncId) {
    [rows] = await pool.query<RowDataPacket[]>(
      'SELECT id FROM assets WHERE company_id = ? AND syncro_asset_id = ?',
      [companyId, syncId]
    );
  }
  if (!rows.length && serialNumber) {
    [rows] = await pool.query<RowDataPacket[]>(
      'SELECT id FROM assets WHERE company_id = ? AND serial_number = ?',
      [companyId, serialNumber]
    );
  }
  if (rows.length) {
    await pool.execute(
      'UPDATE assets SET name = ?, type = ?, status = ?, os_name = ?, cpu_name = ?, ram_gb = ?, hdd_size = ?, last_sync = ?, motherboard_manufacturer = ?, form_factor = ?, last_user = ?, approx_age = ?, performance_score = ?, warranty_status = ?, warranty_end_date = ?, syncro_asset_id = ?, serial_number = ? WHERE id = ?',
      [
        name,
        type,
        status,
        osName,
        cpuName,
        ramGb,
        hddSize,
        lastSyncDb,
        motherboardManufacturer,
        formFactor,
        lastUser,
        approxAge,
        performanceScore,
        warrantyStatus,
        warrantyEndDb,
        syncId,
        serialNumber,
        rows[0].id,
      ]
    );
  } else {
    await pool.execute(
      'INSERT INTO assets (company_id, name, type, serial_number, status, os_name, cpu_name, ram_gb, hdd_size, last_sync, motherboard_manufacturer, form_factor, last_user, approx_age, performance_score, warranty_status, warranty_end_date, syncro_asset_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [
        companyId,
        name,
        type,
        serialNumber,
        status,
        osName,
        cpuName,
        ramGb,
        hddSize,
        lastSyncDb,
        motherboardManufacturer,
        formFactor,
        lastUser,
        approxAge,
        performanceScore,
        warrantyStatus,
        warrantyEndDb,
        syncId,
      ]
    );
  }
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
  status: string,
  osName?: string | null,
  cpuName?: string | null,
  ramGb?: number | null,
  hddSize?: string | null,
  lastSync?: string | null,
  motherboardManufacturer?: string | null,
  formFactor?: string | null,
  lastUser?: string | null,
  approxAge?: number | null,
  performanceScore?: number | null,
  warrantyStatus?: string | null,
  warrantyEndDate?: string | null
): Promise<void> {
  const lastSyncDb = toMysqlDatetime(lastSync);
  const warrantyEndDb = toMysqlDate(warrantyEndDate);
  await pool.execute(
    'UPDATE assets SET company_id = ?, name = ?, type = ?, serial_number = ?, status = ?, os_name = ?, cpu_name = ?, ram_gb = ?, hdd_size = ?, last_sync = ?, motherboard_manufacturer = ?, form_factor = ?, last_user = ?, approx_age = ?, performance_score = ?, warranty_status = ?, warranty_end_date = ? WHERE id = ?',
    [
      companyId,
      name,
      type,
      serialNumber,
      status,
      osName,
      cpuName,
      ramGb,
      hddSize,
      lastSyncDb,
      motherboardManufacturer,
      formFactor,
      lastUser,
      approxAge,
      performanceScore,
      warrantyStatus,
      warrantyEndDb,
      id,
    ]
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

export async function getCategoryByName(name: string): Promise<Category | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM shop_categories WHERE name = ?',
    [name]
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
    category_id: row.category_id !== null ? Number(row.category_id) : null,
    buy_price: row.buy_price !== null ? Number(row.buy_price) : null,
    weight: row.weight !== null ? Number(row.weight) : null,
    length: row.length !== null ? Number(row.length) : null,
    width: row.width !== null ? Number(row.width) : null,
    height: row.height !== null ? Number(row.height) : null,
    stock_at: row.stock_at ? String(row.stock_at) : null,
    warranty_length: row.warranty_length || null,
    manufacturer: row.manufacturer || null,
    stock_nsw: row.stock_nsw !== null ? Number(row.stock_nsw) : 0,
    stock_qld: row.stock_qld !== null ? Number(row.stock_qld) : 0,
    stock_vic: row.stock_vic !== null ? Number(row.stock_vic) : 0,
    stock_sa: row.stock_sa !== null ? Number(row.stock_sa) : 0,
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
        category_id: row.category_id !== null ? Number(row.category_id) : null,
        buy_price: row.buy_price !== null ? Number(row.buy_price) : null,
        weight: row.weight !== null ? Number(row.weight) : null,
        length: row.length !== null ? Number(row.length) : null,
        width: row.width !== null ? Number(row.width) : null,
        height: row.height !== null ? Number(row.height) : null,
        stock_at: row.stock_at ? String(row.stock_at) : null,
        warranty_length: row.warranty_length || null,
        manufacturer: row.manufacturer || null,
        stock_nsw: row.stock_nsw !== null ? Number(row.stock_nsw) : 0,
        stock_qld: row.stock_qld !== null ? Number(row.stock_qld) : 0,
        stock_vic: row.stock_vic !== null ? Number(row.stock_vic) : 0,
        stock_sa: row.stock_sa !== null ? Number(row.stock_sa) : 0,
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
        category_id: row.category_id !== null ? Number(row.category_id) : null,
        buy_price: row.buy_price !== null ? Number(row.buy_price) : null,
        weight: row.weight !== null ? Number(row.weight) : null,
        length: row.length !== null ? Number(row.length) : null,
        width: row.width !== null ? Number(row.width) : null,
        height: row.height !== null ? Number(row.height) : null,
        stock_at: row.stock_at ? String(row.stock_at) : null,
        warranty_length: row.warranty_length || null,
        manufacturer: row.manufacturer || null,
        stock_nsw: row.stock_nsw !== null ? Number(row.stock_nsw) : 0,
        stock_qld: row.stock_qld !== null ? Number(row.stock_qld) : 0,
        stock_vic: row.stock_vic !== null ? Number(row.stock_vic) : 0,
        stock_sa: row.stock_sa !== null ? Number(row.stock_sa) : 0,
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

export interface UpsertProductInput {
  name: string;
  sku: string;
  vendorSku: string;
  description: string;
  imageUrl: string | null;
  price: number;
  vipPrice: number | null;
  stock: number;
  categoryId: number | null;
  stockNsw: number;
  stockQld: number;
  stockVic: number;
  stockSa: number;
  buyPrice: number | null;
  weight: number | null;
  length: number | null;
  width: number | null;
  height: number | null;
  stockAt: string | null;
  warrantyLength: string | null;
  manufacturer: string | null;
}

export async function upsertProductFromFeed(data: UpsertProductInput): Promise<void> {
  await pool.execute(
    `INSERT INTO shop_products (name, sku, vendor_sku, description, image_url, price, vip_price, stock, category_id, stock_nsw, stock_qld, stock_vic, stock_sa, buy_price, weight, length, width, height, stock_at, warranty_length, manufacturer)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE name=VALUES(name), sku=VALUES(sku), description=VALUES(description), image_url=IFNULL(VALUES(image_url), image_url), price=VALUES(price), vip_price=VALUES(vip_price), stock=VALUES(stock), category_id=VALUES(category_id), stock_nsw=VALUES(stock_nsw), stock_qld=VALUES(stock_qld), stock_vic=VALUES(stock_vic), stock_sa=VALUES(stock_sa), buy_price=VALUES(buy_price), weight=VALUES(weight), length=VALUES(length), width=VALUES(width), height=VALUES(height), stock_at=VALUES(stock_at), warranty_length=VALUES(warranty_length), manufacturer=VALUES(manufacturer)`,
    [
      data.name,
      data.sku,
      data.vendorSku,
      data.description,
      data.imageUrl,
      data.price,
      data.vipPrice,
      data.stock,
      data.categoryId,
      data.stockNsw,
      data.stockQld,
      data.stockVic,
      data.stockSa,
      data.buyPrice,
      data.weight,
      data.length,
      data.width,
      data.height,
      data.stockAt,
      data.warrantyLength,
      data.manufacturer,
    ]
  );
}

function mapProductPriceAlert(row: RowDataPacket): ProductPriceAlert {
  return {
    id: Number(row.id),
    product_id: Number(row.product_id),
    price: Number(row.price),
    vip_price: row.vip_price !== null ? Number(row.vip_price) : null,
    buy_price: Number(row.buy_price),
    threshold_price: Number(row.threshold_price),
    triggered_at: String(row.triggered_at),
    emailed_at: row.emailed_at ? String(row.emailed_at) : null,
    resolved_at: row.resolved_at ? String(row.resolved_at) : null,
  };
}

export async function getActiveProductPriceAlertByProductId(
  productId: number
): Promise<ProductPriceAlert | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM product_price_alerts WHERE product_id = ? AND resolved_at IS NULL LIMIT 1',
    [productId]
  );
  const row = rows[0];
  return row ? mapProductPriceAlert(row) : null;
}

export async function createProductPriceAlert(
  productId: number,
  price: number,
  vipPrice: number | null,
  buyPrice: number,
  thresholdPrice: number,
  triggeredAt: string
): Promise<number> {
  const [result] = await pool.execute<ResultSetHeader>(
    'INSERT INTO product_price_alerts (product_id, price, vip_price, buy_price, threshold_price, triggered_at) VALUES (?, ?, ?, ?, ?, ?)',
    [productId, price, vipPrice, buyPrice, thresholdPrice, triggeredAt]
  );
  return (result as ResultSetHeader).insertId;
}

export async function markProductPriceAlertEmailed(
  id: number,
  emailedAt: string
): Promise<void> {
  await pool.execute(
    'UPDATE product_price_alerts SET emailed_at = ? WHERE id = ?',
    [emailedAt, id]
  );
}

export async function resolveProductPriceAlerts(
  productId: number,
  resolvedAt: string
): Promise<void> {
  await pool.execute(
    'UPDATE product_price_alerts SET resolved_at = ? WHERE product_id = ? AND resolved_at IS NULL',
    [resolvedAt, productId]
  );
}

export async function getActiveProductPriceAlerts(): Promise<
  ProductPriceAlertWithProduct[]
> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT a.*, p.name AS product_name, p.sku AS product_sku
     FROM product_price_alerts a
     JOIN shop_products p ON p.id = a.product_id
     WHERE a.resolved_at IS NULL
     ORDER BY a.triggered_at DESC`
  );
  return rows.map((row) => ({
    ...mapProductPriceAlert(row),
    product_name: String(row.product_name),
    product_sku: String(row.product_sku),
  }));
}

export async function clearStockFeed(): Promise<void> {
  await pool.execute('TRUNCATE TABLE stock_feed');
}

export async function insertStockFeedItem(item: StockFeedItem): Promise<void> {
  await pool.execute(
    `INSERT INTO stock_feed (sku, product_name, product_name2, rrp, category_name, on_hand_nsw, on_hand_qld, on_hand_vic, on_hand_sa, dbp, weight, length, width, height, pub_date, warranty_length, manufacturer, image_url)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      item.sku,
      item.product_name,
      item.product_name2 || null,
      item.rrp ?? null,
      item.category_name || null,
      item.on_hand_nsw,
      item.on_hand_qld,
      item.on_hand_vic,
      item.on_hand_sa,
      item.dbp ?? null,
      item.weight ?? null,
      item.length ?? null,
      item.width ?? null,
      item.height ?? null,
      item.pub_date || null,
      item.warranty_length || null,
      item.manufacturer || null,
      item.image_url || null,
    ]
  );
}

export async function getStockFeedItems(): Promise<StockFeedItem[]> {
  const [rows] = await pool.query('SELECT * FROM stock_feed');
  return (rows as any[]).map((row) => ({
    sku: row.sku,
    product_name: row.product_name,
    product_name2: row.product_name2,
    rrp: row.rrp !== null ? Number(row.rrp) : null,
    category_name: row.category_name,
    on_hand_nsw: row.on_hand_nsw !== null ? Number(row.on_hand_nsw) : 0,
    on_hand_qld: row.on_hand_qld !== null ? Number(row.on_hand_qld) : 0,
    on_hand_vic: row.on_hand_vic !== null ? Number(row.on_hand_vic) : 0,
    on_hand_sa: row.on_hand_sa !== null ? Number(row.on_hand_sa) : 0,
    dbp: row.dbp !== null ? Number(row.dbp) : null,
    weight: row.weight !== null ? Number(row.weight) : null,
    length: row.length !== null ? Number(row.length) : null,
    width: row.width !== null ? Number(row.width) : null,
    height: row.height !== null ? Number(row.height) : null,
    pub_date: row.pub_date ? String(row.pub_date) : null,
    warranty_length: row.warranty_length,
    manufacturer: row.manufacturer,
    image_url: row.image_url,
  }));
}

export async function getStockFeedItemBySku(
  sku: string
): Promise<StockFeedItem | null> {
  const [rows] = await pool.query('SELECT * FROM stock_feed WHERE sku = ? LIMIT 1', [
    sku,
  ]);
  const row = (rows as any[])[0];
  return row
    ? {
        sku: row.sku,
        product_name: row.product_name,
        product_name2: row.product_name2,
        rrp: row.rrp !== null ? Number(row.rrp) : null,
        category_name: row.category_name,
        on_hand_nsw: row.on_hand_nsw !== null ? Number(row.on_hand_nsw) : 0,
        on_hand_qld: row.on_hand_qld !== null ? Number(row.on_hand_qld) : 0,
        on_hand_vic: row.on_hand_vic !== null ? Number(row.on_hand_vic) : 0,
        on_hand_sa: row.on_hand_sa !== null ? Number(row.on_hand_sa) : 0,
        dbp: row.dbp !== null ? Number(row.dbp) : null,
        weight: row.weight !== null ? Number(row.weight) : null,
        length: row.length !== null ? Number(row.length) : null,
        width: row.width !== null ? Number(row.width) : null,
        height: row.height !== null ? Number(row.height) : null,
        pub_date: row.pub_date ? String(row.pub_date) : null,
        warranty_length: row.warranty_length,
        manufacturer: row.manufacturer,
        image_url: row.image_url,
      }
    : null;
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

export interface StockChange {
  productId: number;
  previousStock: number | null;
  newStock: number | null;
}

export async function createOrder(
  userId: number,
  companyId: number,
  productId: number,
  quantity: number,
  orderNumber: string,
  status: string,
  poNumber: string | null
): Promise<{ previousStock: number | null; newStock: number | null }> {
  const conn = await pool.getConnection();
  try {
    await conn.beginTransaction();
    let previousStock: number | null = null;
    let newStock: number | null = null;
    const [productRows] = await conn.query<RowDataPacket[]>(
      'SELECT stock FROM shop_products WHERE id = ? FOR UPDATE',
      [productId]
    );
    if ((productRows as RowDataPacket[]).length > 0) {
      previousStock = Number((productRows as RowDataPacket[])[0].stock);
      newStock = previousStock - quantity;
    }
    await conn.execute(
      'INSERT INTO shop_orders (user_id, company_id, product_id, quantity, order_number, status, notes, po_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, companyId, productId, quantity, orderNumber, status, null, poNumber]
    );
    await conn.execute(
      'UPDATE shop_products SET stock = stock - ? WHERE id = ?',
      [quantity, productId]
    );
    await conn.commit();
    return { previousStock, newStock };
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
    `SELECT order_number, MAX(order_date) as order_date, MAX(status) as status, MAX(shipping_status) as shipping_status, MAX(notes) as notes, MAX(po_number) as po_number, MAX(consignment_id) as consignment_id, MAX(eta) as eta
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

export async function getOrdersByConsignmentId(
  consignmentId: string
): Promise<OrderItem[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT o.*, p.name as product_name, IF(c.is_vip = 1 AND p.vip_price IS NOT NULL, p.vip_price, p.price) AS price, p.sku, p.description, p.image_url
     FROM shop_orders o
     JOIN shop_products p ON o.product_id = p.id
     JOIN companies c ON o.company_id = c.id
     WHERE o.consignment_id = ?`,
    [consignmentId]
  );
  return (rows as RowDataPacket[]).map((row) => ({
    ...(row as any),
    price: Number(row.price),
  })) as OrderItem[];
}

export async function deleteOrder(
  orderNumber: string,
  companyId: number
): Promise<StockChange[]> {
  const conn = await pool.getConnection();
  const stockChanges: StockChange[] = [];
  try {
    await conn.beginTransaction();
    const [rows] = await conn.query<RowDataPacket[]>(
      'SELECT product_id, quantity FROM shop_orders WHERE order_number = ? AND company_id = ?',
      [orderNumber, companyId]
    );
    for (const row of rows as any[]) {
      const quantity = Number(row.quantity);
      const productId = Number(row.product_id);
      const [productRows] = await conn.query<RowDataPacket[]>(
        'SELECT stock FROM shop_products WHERE id = ? FOR UPDATE',
        [productId]
      );
      let previousStock: number | null = null;
      let newStock: number | null = null;
      if ((productRows as RowDataPacket[]).length > 0) {
        previousStock = Number((productRows as RowDataPacket[])[0].stock);
        newStock = previousStock + quantity;
      }
      await conn.execute('UPDATE shop_products SET stock = stock + ? WHERE id = ?', [
        quantity,
        productId,
      ]);
      stockChanges.push({ productId, previousStock, newStock });
    }
    await conn.execute('DELETE FROM shop_orders WHERE order_number = ? AND company_id = ?', [orderNumber, companyId]);
    await conn.commit();
    return stockChanges;
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

export async function updateOrderShipping(
  orderNumber: string,
  companyId: number,
  shippingStatus: string,
  consignmentId: string | null,
  eta: string | null
): Promise<void> {
  await pool.execute(
    'UPDATE shop_orders SET shipping_status = ?, consignment_id = ?, eta = ? WHERE order_number = ? AND company_id = ?',
    [shippingStatus, consignmentId, eta, orderNumber, companyId]
  );
}

export async function updateShippingStatusByConsignmentId(
  consignmentId: string,
  shippingStatus: string,
  eta: string | null
): Promise<void> {
  await pool.execute(
    'UPDATE shop_orders SET shipping_status = ?, eta = ? WHERE consignment_id = ?',
    [shippingStatus, eta, consignmentId]
  );
}

export async function getOrderPoNumber(
  orderNumber: string
): Promise<string | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT po_number FROM shop_orders WHERE order_number = ? LIMIT 1',
    [orderNumber]
  );
  return (rows as RowDataPacket[])[0]?.po_number || null;
}

export async function getUsersMobilePhones(
  userIds: number[]
): Promise<string[]> {
  if (userIds.length === 0) {
    return [];
  }
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT mobile_phone FROM users WHERE id IN (?)`,
    [userIds]
  );
  return (rows as RowDataPacket[])
    .map((r) => r.mobile_phone as string | null)
    .filter((p): p is string => !!p);
}

export async function getSmsSubscriptionsForUser(
  userId: number
): Promise<string[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT order_number FROM order_sms_subscriptions WHERE user_id = ?',
    [userId]
  );
  return rows.map((r) => String(r.order_number));
}

export async function setSmsSubscription(
  orderNumber: string,
  userId: number,
  subscribe: boolean
): Promise<void> {
  if (subscribe) {
    await pool.execute(
      'REPLACE INTO order_sms_subscriptions (order_number, user_id) VALUES (?, ?)',
      [orderNumber, userId]
    );
  } else {
    await pool.execute(
      'DELETE FROM order_sms_subscriptions WHERE order_number = ? AND user_id = ?',
      [orderNumber, userId]
    );
  }
}

export async function getSmsSubscribersByOrder(
  orderNumber: string
): Promise<number[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT user_id FROM order_sms_subscriptions WHERE order_number = ?',
    [orderNumber]
  );
  return rows.map((r) => r.user_id as number);
}

export async function isUserSubscribedToOrder(
  orderNumber: string,
  userId: number
): Promise<boolean> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT 1 FROM order_sms_subscriptions WHERE order_number = ? AND user_id = ?',
    [orderNumber, userId]
  );
  return rows.length > 0;
}

export async function createForm(
  name: string,
  url: string,
  embedCode: string | null,
  description: string
): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO forms (name, url, embed_code, description) VALUES (?, ?, ?, ?)',
    [name, url, embedCode, description]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function updateForm(
  id: number,
  name: string,
  url: string,
  embedCode: string | null,
  description: string
): Promise<void> {
  await pool.execute(
    'UPDATE forms SET name = ?, url = ?, embed_code = ?, description = ? WHERE id = ?',
    [name, url, embedCode, description, id]
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
    'SELECT company_name, login_logo, sidebar_logo, favicon FROM site_settings WHERE id = 1'
  );
  const row = (rows as SiteSettings[])[0];
  return {
    company_name: row?.company_name || null,
    login_logo: row?.login_logo || null,
    sidebar_logo: row?.sidebar_logo || null,
    favicon: row?.favicon || null,
  };
}

export async function updateSiteSettings(
  companyName: string,
  loginLogo?: string | null,
  sidebarLogo?: string | null,
  favicon?: string | null
): Promise<void> {
  await pool.query(
    'UPDATE site_settings SET company_name = ?, login_logo = COALESCE(?, login_logo), sidebar_logo = COALESCE(?, sidebar_logo), favicon = COALESCE(?, favicon) WHERE id = 1',
    [companyName, loginLogo ?? null, sidebarLogo ?? null, favicon ?? null]
  );
}

export async function getShopSettings(): Promise<ShopSettings> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT discord_webhook_url FROM shop_settings WHERE id = 1'
  );
  const row = (rows as RowDataPacket[])[0];
  return {
    discord_webhook_url: row?.discord_webhook_url || null,
  };
}

export async function updateShopDiscordWebhook(
  url: string | null
): Promise<void> {
  await pool.execute('UPDATE shop_settings SET discord_webhook_url = ? WHERE id = 1', [
    url,
  ]);
}

export interface ScheduledTask {
  id: number;
  company_id: number | null;
  name: string;
  command: string;
  cron: string;
  last_run_at: Date | null;
  active: number;
}

export async function getScheduledTasks(): Promise<ScheduledTask[]> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM scheduled_tasks');
  return rows as ScheduledTask[];
}

export async function getScheduledTask(id: number): Promise<ScheduledTask | null> {
  const [rows] = await pool.query<RowDataPacket[]>('SELECT * FROM scheduled_tasks WHERE id = ?', [id]);
  if ((rows as ScheduledTask[]).length === 0) return null;
  return (rows as ScheduledTask[])[0];
}

export async function createScheduledTask(
  companyId: number | null,
  name: string,
  command: string,
  cron: string
): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO scheduled_tasks (company_id, name, command, cron) VALUES (?, ?, ?, ?)',
    [companyId, name, command, cron]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function updateScheduledTask(
  id: number,
  companyId: number | null,
  name: string,
  command: string,
  cron: string
): Promise<void> {
  await pool.execute(
    'UPDATE scheduled_tasks SET company_id = ?, name = ?, command = ?, cron = ? WHERE id = ?',
    [companyId, name, command, cron, id]
  );
}

export async function deleteScheduledTask(id: number): Promise<void> {
  await pool.execute('DELETE FROM scheduled_tasks WHERE id = ?', [id]);
}

export async function markScheduledTaskRun(id: number): Promise<void> {
  await pool.execute('UPDATE scheduled_tasks SET last_run_at = NOW() WHERE id = ?', [id]);
}

export interface CompanyM365Credential {
  id: number;
  company_id: number;
  tenant_id: string;
  client_id: string;
  client_secret: string;
  refresh_token: string | null;
  access_token: string | null;
  token_expires_at: Date | null;
}

export async function getM365Credentials(companyId: number): Promise<CompanyM365Credential | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM company_m365_credentials WHERE company_id = ?',
    [companyId]
  );
  return (rows as CompanyM365Credential[])[0] || null;
}

export async function upsertM365Credentials(
  companyId: number,
  tenantId: string,
  clientId: string,
  clientSecret: string,
  refreshToken?: string | null,
  accessToken?: string | null,
  expiresAt?: string | null
): Promise<void> {
  await pool.execute(
    `INSERT INTO company_m365_credentials (company_id, tenant_id, client_id, client_secret, refresh_token, access_token, token_expires_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE tenant_id = VALUES(tenant_id), client_id = VALUES(client_id), client_secret = VALUES(client_secret), refresh_token = VALUES(refresh_token), access_token = VALUES(access_token), token_expires_at = VALUES(token_expires_at)`,
    [companyId, tenantId, clientId, clientSecret, refreshToken ?? null, accessToken ?? null, expiresAt ?? null]
  );
}

export async function deleteM365Credentials(companyId: number): Promise<void> {
  await pool.execute('DELETE FROM company_m365_credentials WHERE company_id = ?', [companyId]);
}

