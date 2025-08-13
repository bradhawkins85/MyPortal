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
  is_vip?: number;
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
}

export interface OrderItem {
  id: number;
  order_number: string;
  user_id: number;
  company_id: number;
  product_id: number;
  quantity: number;
  order_date: Date;
  product_name: string;
  sku: string;
  description: string;
  image_url: string | null;
  price: number;
}

export interface OrderSummary {
  order_number: string;
  order_date: Date;
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

export interface ExternalApiSettings {
  company_id: number;
  xero_endpoint: string | null;
  xero_api_key: string | null;
  syncro_endpoint: string | null;
  syncro_api_key: string | null;
  webhook_url: string | null;
  webhook_api_key: string | null;
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

export async function createCompany(name: string, address?: string, isVip = false): Promise<number> {
  const [result] = await pool.execute(
    'INSERT INTO companies (name, address, is_vip) VALUES (?, ?, ?)',
    [name, address || null, isVip ? 1 : 0]
  );
  const insert = result as ResultSetHeader;
  return insert.insertId;
}

export async function updateCompanyVipStatus(id: number, isVip: boolean): Promise<void> {
  await pool.execute('UPDATE companies SET is_vip = ? WHERE id = ?', [isVip ? 1 : 0, id]);
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

export async function getStaffForLicense(
  licenseId: number
): Promise<Staff[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    `SELECT s.*
     FROM staff s
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

export async function getAllProducts(includeArchived = false): Promise<Product[]> {
  const [rows] = await pool.query<RowDataPacket[]>(
    includeArchived ? 'SELECT * FROM shop_products' : 'SELECT * FROM shop_products WHERE archived = 0'
  );
  return (rows as RowDataPacket[]).map((row) => ({
    ...(row as any),
    price: Number(row.price),
    vip_price: row.vip_price !== null ? Number(row.vip_price) : null,
  })) as Product[];
}

export async function getProductById(
  id: number,
  includeArchived = false
): Promise<Product | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM shop_products WHERE id = ?' + (includeArchived ? '' : ' AND archived = 0'),
    [id]
  );
  const row = (rows as RowDataPacket[])[0];
  return row
    ? ({
        ...(row as any),
        price: Number(row.price),
        vip_price: row.vip_price !== null ? Number(row.vip_price) : null,
      } as Product)
    : null;
}

export async function getProductBySku(
  sku: string,
  includeArchived = false
): Promise<Product | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM shop_products WHERE sku = ?' + (includeArchived ? '' : ' AND archived = 0'),
    [sku]
  );
  const row = (rows as RowDataPacket[])[0];
  return row
    ? ({
        ...(row as any),
        price: Number(row.price),
        vip_price: row.vip_price !== null ? Number(row.vip_price) : null,
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
  stock: number
): Promise<number> {
  const [result] = await pool.execute<ResultSetHeader>(
    'INSERT INTO shop_products (name, sku, vendor_sku, description, image_url, price, vip_price, stock) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [name, sku, vendorSku, description, imageUrl, price, vipPrice, stock]
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
  stock: number
): Promise<void> {
  await pool.execute(
    'UPDATE shop_products SET name = ?, sku = ?, vendor_sku = ?, description = ?, image_url = IFNULL(?, image_url), price = ?, vip_price = ?, stock = ? WHERE id = ?',
    [name, sku, vendorSku, description, imageUrl, price, vipPrice, stock, id]
  );
}

export async function archiveProduct(id: number): Promise<void> {
  await pool.execute('UPDATE shop_products SET archived = 1 WHERE id = ?', [id]);
}

export async function unarchiveProduct(id: number): Promise<void> {
  await pool.execute('UPDATE shop_products SET archived = 0 WHERE id = ?', [id]);
}

export async function createOrder(
  userId: number,
  companyId: number,
  productId: number,
  quantity: number,
  orderNumber: string
): Promise<void> {
  const conn = await pool.getConnection();
  try {
    await conn.beginTransaction();
    await conn.execute(
      'INSERT INTO shop_orders (user_id, company_id, product_id, quantity, order_number) VALUES (?, ?, ?, ?, ?)',
      [userId, companyId, productId, quantity, orderNumber]
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
    'SELECT order_number, MAX(order_date) as order_date FROM shop_orders WHERE company_id = ? GROUP BY order_number ORDER BY order_date DESC',
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

export async function getExternalApiSettings(
  companyId: number
): Promise<ExternalApiSettings | null> {
  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT * FROM external_api_settings WHERE company_id = ?',
    [companyId]
  );
  return (rows as ExternalApiSettings[])[0] || null;
}

export async function upsertExternalApiSettings(
  companyId: number,
  xeroEndpoint: string,
  xeroApiKey: string,
  syncroEndpoint: string,
  syncroApiKey: string,
  webhookUrl: string,
  webhookApiKey: string
): Promise<void> {
  await pool.execute(
    `INSERT INTO external_api_settings (company_id, xero_endpoint, xero_api_key, syncro_endpoint, syncro_api_key, webhook_url, webhook_api_key)
     VALUES (?, ?, ?, ?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE xero_endpoint = VALUES(xero_endpoint), xero_api_key = VALUES(xero_api_key), syncro_endpoint = VALUES(syncro_endpoint), syncro_api_key = VALUES(syncro_api_key), webhook_url = VALUES(webhook_url), webhook_api_key = VALUES(webhook_api_key)` ,
    [companyId, xeroEndpoint, xeroApiKey, syncroEndpoint, syncroApiKey, webhookUrl, webhookApiKey]
  );
}
