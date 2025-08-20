import express from 'express';
import session from 'express-session';
import path from 'path';
import bcrypt from 'bcrypt';
import dotenv from 'dotenv';
import crypto from 'crypto';
import cookieParser from 'cookie-parser';
import { authenticator } from 'otplib';
import QRCode from 'qrcode';
import swaggerUi from 'swagger-ui-express';
import swaggerJSDoc from 'swagger-jsdoc';
import multer from 'multer';
import nodemailer from 'nodemailer';
import {
  getSyncroCustomers,
  getSyncroCustomer,
  getSyncroContacts,
} from './syncro';
import { findExistingStaff } from './staff-import';
import {
  getUserByEmail,
  getCompanyById,
  getCompanyBySyncroId,
  getLicensesByCompany,
  getAllLicenses,
  getLicenseById,
  getUserCount,
  createCompany,
  createUser,
  getCompaniesForUser,
  getAllCompanies,
  getAllUsers,
  getUserById,
  assignUserToCompany,
  getUserCompanyAssignments,
  updateUserCompanyPermission,
  getStaffByCompany,
  getAllStaff,
  getStaffById,
  addStaff,
  updateStaffEnabled,
  updateStaff,
  deleteStaff,
  setStaffVerificationCode,
  purgeExpiredVerificationCodes,
  getVerificationByCode,
  createLicense,
  updateCompany,
  deleteCompany,
  updateUser,
  deleteUser,
  updateUserPassword,
  updateUserName,
  updateUserMobile,
  setUserForcePasswordChange,
  getUserTotpAuthenticators,
  addUserTotpAuthenticator,
  deleteUserTotpAuthenticator,
  updateLicense,
  deleteLicense,
  unassignUserFromCompany,
  linkStaffToLicense,
  unlinkStaffFromLicense,
  getStaffForLicense,
  getApiKeysWithUsage,
  createApiKey,
  deleteApiKey,
  getApiKeyRecord,
  recordApiKeyUsage,
  logAudit,
  getAuditLogs,
  getAssetsByCompany,
  getAssetById,
  updateAsset,
  deleteAsset,
  getInvoicesByCompany,
  getInvoiceById,
  updateInvoice,
  deleteInvoice,
    getAllForms,
    getFormsByCompany,
    createForm,
    updateForm,
    deleteForm,
    getFormsForUser,
    getFormPermissions,
    updateFormPermissions,
    getAllFormPermissionEntries,
    deleteFormPermission,
  getAllCategories,
  getCategoryById,
  createCategory,
  updateCategory,
  deleteCategory,
  getAllProducts,
  createProduct,
  getProductById,
  getProductBySku,
  updateProduct,
  archiveProduct,
  unarchiveProduct,
  deleteProduct,
  getProductCompanyRestrictions,
  setProductCompanyExclusions,
  getOfficeGroupsByCompany,
  createOfficeGroup,
  updateOfficeGroupMembers,
  deleteOfficeGroup,
  createOrder,
  getOrdersByCompany,
  getOrderSummariesByCompany,
  getOrderItems,
  deleteOrder,
  updateOrder,
  updateOrderShipping,
  getOrdersByConsignmentId,
  updateShippingStatusByConsignmentId,
  getSmsSubscriptionsForUser,
  setSmsSubscription,
  getSmsSubscribersByOrder,
  getOrderPoNumber,
  getUsersMobilePhones,
  isUserSubscribedToOrder,
  upsertAsset,
  upsertInvoice,
  getAllApps,
  createApp,
  getAppById,
  updateApp,
  deleteApp,
  getAppPrice,
  getCompanyAppPrices,
  deleteCompanyAppPrice,
  upsertCompanyAppPrice,
  updateCompanyIds,
  getHiddenSyncroCustomerIds,
  hideSyncroCustomer,
  unhideSyncroCustomer,
  getSiteSettings,
  updateSiteSettings,
  getEmailTemplate,
  upsertEmailTemplate,
  Company,
  User,
  UserCompany,
  ApiKey,
  ApiKeyWithUsage,
  AuditLog,
  App,
  ProductCompanyRestriction,
  Category,
  Asset,
  Invoice,
  Staff,
  OfficeGroupWithMembers,
} from './queries';
import { runMigrations } from './db';

dotenv.config();

const smtpUser = process.env.SMTP_USER || process.env.SMTP_USERNAME;
const smtpPass = process.env.SMTP_PASS || process.env.SMTP_PASSWORD;

const transporter = nodemailer.createTransport({
  host: process.env.SMTP_HOST,
  port: parseInt(process.env.SMTP_PORT || '587', 10),
  secure:
    process.env.SMTP_SECURE === 'true' ||
    parseInt(process.env.SMTP_PORT || '587', 10) === 465,
  auth:
    smtpUser && smtpPass
      ? {
          user: smtpUser,
          pass: smtpPass,
        }
      : undefined,
});

async function sendEmail(to: string, subject: string, html: string) {
  await transporter.sendMail({
    from: process.env.SMTP_FROM || smtpUser,
    to,
    subject,
    html,
  });
}

async function sendSmsUpdate(
  orderNumber: string,
  shippingStatus: string,
  eta: string | null
) {
  const { SMS_WEBHOOK_URL, SMS_WEBHOOK_API_KEY } = process.env;
  if (SMS_WEBHOOK_URL && SMS_WEBHOOK_API_KEY) {
    try {
      const [poNumber, subscriberIds] = await Promise.all([
        getOrderPoNumber(orderNumber),
        getSmsSubscribersByOrder(orderNumber),
      ]);
      if (!subscriberIds.length) {
        return;
      }
      const recipients = await getUsersMobilePhones(subscriberIds);
      if (!recipients.length) {
        return;
      }
      await fetch(SMS_WEBHOOK_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': SMS_WEBHOOK_API_KEY,
        },
        body: JSON.stringify({
          type: 'Shipping Update',
          orderNumber,
          poNumber,
          shippingStatus,
          eta,
          recipients,
        }),
      });
    } catch (err) {
      console.error('Failed to call SMS webhook', err);
    }
  }
}

setInterval(() => {
  purgeExpiredVerificationCodes().catch((err) =>
    console.error('Failed to purge verification codes', err)
  );
}, 60 * 1000).unref();

function toDateTime(value?: string): string | null {
  return value ? value.replace('T', ' ') : null;
}

function toDate(value?: string): string | null {
  return value ? value.split('T')[0] : null;
}

function mapStaff(s: Staff) {
  return {
    id: s.id,
    companyId: s.company_id,
    firstName: s.first_name,
    lastName: s.last_name,
    email: s.email,
    mobilePhone: s.mobile_phone ?? null,
    dateOnboarded: s.date_onboarded,
    dateOffboarded: s.date_offboarded ?? null,
    enabled: !!s.enabled,
    street: s.street ?? null,
    city: s.city ?? null,
    state: s.state ?? null,
    postcode: s.postcode ?? null,
    country: s.country ?? null,
    department: s.department ?? null,
    jobTitle: s.job_title ?? null,
    company: s.org_company ?? null,
    managerName: s.manager_name ?? null,
    accountAction: s.account_action ?? null,
    verificationCode: s.verification_code ?? null,
  };
}

const app = express();
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

// Register core middleware needed for all requests first
app.use(cookieParser());
app.use(
  session({
    secret: process.env.SESSION_SECRET || 'secret',
    resave: false,
    saveUninitialized: false,
  })
);

// Attach the audit logger before body parsing so that even requests that
// fail in the parsers (e.g. malformed JSON) are still recorded
app.use(auditLogger);

// Body parsing and static serving come after the audit logger
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

const upload = multer({ dest: path.join(__dirname, 'public', 'uploads') });
const memoryUpload = multer();

const verifyAttempts: Record<string, { count: number; reset: number }> = {};

// Populate common template variables
app.use(async (req, res, next) => {
  res.locals.isSuperAdmin = req.session.userId === 1;
  res.locals.cart = req.session.cart || [];
  res.locals.hasForms = req.session.hasForms ?? false;
  try {
    res.locals.siteSettings = await getSiteSettings();
  } catch (err) {
    console.error('Failed to load site settings', err);
    res.locals.siteSettings = { company_name: null, login_logo: null, sidebar_logo: null };
  }
  next();
});

function generateTrustedDeviceToken(userId: number): string {
  const expires = Date.now() + 24 * 60 * 60 * 1000;
  const data = `${userId}.${expires}`;
  const hmac = crypto
    .createHmac('sha256', process.env.SESSION_SECRET || 'secret')
    .update(data)
    .digest('hex');
  return `${data}.${hmac}`;
}

function verifyTrustedDeviceToken(token: string, userId: number): boolean {
  const parts = token.split('.');
  if (parts.length !== 3) return false;
  const [id, expires, signature] = parts;
  if (Number(id) !== userId) return false;
  if (Number(expires) < Date.now()) return false;
  const data = `${id}.${expires}`;
  const hmac = crypto
    .createHmac('sha256', process.env.SESSION_SECRET || 'secret')
    .update(data)
    .digest('hex');
  return hmac === signature;
}

function sanitizeSensitiveData(obj: any): any {
  if (!obj || typeof obj !== 'object') return obj;
  const result: any = Array.isArray(obj) ? [] : {};
  for (const [key, value] of Object.entries(obj)) {
    if (value && typeof value === 'object') {
      result[key] = sanitizeSensitiveData(value);
    } else {
      const lower = key.toLowerCase();
      if (lower.includes('password') || (lower.includes('api') && lower.includes('key'))) {
        result[key] = '***';
      } else {
        result[key] = value;
      }
    }
  }
  return result;
}

function auditLogger(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  res.on('finish', async () => {
    const sanitizedBody = sanitizeSensitiveData(req.body || {});
    let companyId: number | null = null;

    const sources: any[] = [
      req.body?.companyId,
      req.body?.organisationId,
      req.query.companyId,
      req.query.organisationId,
      (req.params as any).companyId,
      (req.params as any).organisationId,
    ];
    for (const s of sources) {
      const n = parseInt(s as string, 10);
      if (!isNaN(n)) {
        companyId = n;
        break;
      }
    }

    if (!companyId) {
      const lookupMappings: Array<[
        string,
        (id: number) => Promise<{ company_id: number } | null>
      ]> = [
        ['userId', getUserById],
        ['staffId', getStaffById],
        ['assetId', getAssetById],
        ['invoiceId', getInvoiceById],
      ];
      for (const [param, getter] of lookupMappings) {
        const raw = (req.params as any)[param] || (req.body as any)?.[param];
        if (raw) {
          const idNum = parseInt(raw as string, 10);
          if (!isNaN(idNum)) {
            try {
              const record = await getter(idNum);
              if (record && 'company_id' in record && record.company_id) {
                companyId = record.company_id;
                break;
              }
            } catch {}
          }
        }
      }
      if (!companyId && req.params.id) {
        const idNum = parseInt(req.params.id, 10);
        if (!isNaN(idNum)) {
          try {
            if (req.originalUrl.includes('/users/')) {
              const r = await getUserById(idNum);
              companyId = r?.company_id || null;
            } else if (req.originalUrl.includes('/staff/')) {
              const r = await getStaffById(idNum);
              companyId = r?.company_id || null;
            } else if (req.originalUrl.includes('/assets/')) {
              const r = await getAssetById(idNum);
              companyId = r?.company_id || null;
            } else if (req.originalUrl.includes('/invoices/')) {
              const r = await getInvoiceById(idNum);
              companyId = r?.company_id || null;
            }
          } catch {}
        }
      }
    }

    const ip = (req.headers['cf-connecting-ip'] as string) || req.ip;
    logAudit({
      userId: req.session.userId || null,
      companyId,
      action: `${req.method} ${req.originalUrl}`,
      previousValue: null,
      newValue: JSON.stringify(sanitizedBody),
      apiKey: req.apiKey,
      ipAddress: ip,
    }).catch(() => {});
  });
  next();
}

app.use(auditLogger);

const swaggerSpec = swaggerJSDoc({
  definition: {
    openapi: '3.0.0',
    info: {
      title: 'MyPortal API',
      version: '1.0.0',
    },
    tags: [
      { name: 'Apps' },
      { name: 'Companies' },
      { name: 'Users' },
      { name: 'Licenses' },
      { name: 'Staff' },
      { name: 'Assets' },
      { name: 'Invoices' },
      { name: 'Shop' },
    ],
    components: {
      securitySchemes: {
        ApiKeyAuth: {
          type: 'apiKey',
          in: 'header',
          name: 'x-api-key',
        },
      },
    },
    security: [{ ApiKeyAuth: [] }],
  },
  apis: [path.join(__dirname, '../src/**/*.ts')],
});

app.use(
  '/swagger',
  ensureAuth,
  ensureSuperAdmin,
  swaggerUi.serve,
  swaggerUi.setup(swaggerSpec)
);

function ensureAuth(req: express.Request, res: express.Response, next: express.NextFunction) {
  if (!req.session.userId) {
    return res.redirect('/login');
  }
  if (
    req.session.mustChangePassword &&
    req.path !== '/force-password-change' &&
    req.path !== '/logout'
  ) {
    return res.redirect('/force-password-change');
  }
  next();
}

async function ensureAdmin(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  if (req.session.userId === 1) {
    return next();
  }
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.is_admin) {
    return next();
  }
  return res.redirect('/');
}

function ensureSuperAdmin(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  if (req.session.userId === 1) {
    return next();
  }
  return res.redirect('/');
}

async function completeLogin(req: express.Request, userId: number) {
  const [companies, forms] = await Promise.all([
    getCompaniesForUser(userId),
    getFormsForUser(userId),
  ]);
  req.session.userId = userId;
  req.session.companyId = companies[0]?.company_id;
  req.session.hasForms = forms.length > 0;
  req.session.cookie.expires = undefined;
  req.session.cookie.maxAge = undefined;
}

app.get('/api-docs', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('api-docs', {
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: true,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get('/login', async (req, res) => {
  const count = await getUserCount();
  if (count === 0) {
    return res.redirect('/register');
  }
  res.render('login', { error: '' });
});

app.post('/login', async (req, res) => {
  const { email, password } = req.body;
  const user = await getUserByEmail(email);
  if (user && (await bcrypt.compare(password, user.password_hash))) {
    const trusted = req.cookies[`trusted_${user.id}`];
    if (trusted && verifyTrustedDeviceToken(trusted, user.id)) {
      await completeLogin(req, user.id);
      if (user.force_password_change) {
        req.session.mustChangePassword = true;
        return res.redirect('/force-password-change');
      }
      return res.redirect('/');
    }
    req.session.tempUserId = user.id;
    req.session.pendingForcePassword = !!user.force_password_change;
    const totpAuths = await getUserTotpAuthenticators(user.id);
    if (totpAuths.length === 0) {
      req.session.pendingTotpSecret = authenticator.generateSecret();
      req.session.requireTotpSetup = true;
    } else {
      req.session.requireTotpSetup = false;
    }
    return res.redirect('/totp');
  }
  res.render('login', { error: 'Invalid credentials' });
});

app.get('/totp', async (req, res) => {
  if (!req.session.tempUserId) {
    return res.redirect('/login');
  }
  let qrCode: string | null = null;
  let secret: string | null = null;
  if (req.session.requireTotpSetup && req.session.pendingTotpSecret) {
    const user = await getUserById(req.session.tempUserId);
    secret = req.session.pendingTotpSecret;
    const otpauth = authenticator.keyuri(
      user!.email,
      'MyPortal',
      secret
    );
    qrCode = await QRCode.toDataURL(otpauth);
  }
  res.render('totp', { qrCode, secret, requireSetup: req.session.requireTotpSetup, error: '' });
});

app.post('/totp', async (req, res) => {
  if (!req.session.tempUserId) {
    return res.redirect('/login');
  }
  const userId = req.session.tempUserId;
  let valid = false;
  if (req.session.requireTotpSetup && req.session.pendingTotpSecret) {
    const secret = req.session.pendingTotpSecret;
    valid = authenticator.verify({ token: req.body.token, secret });
    if (valid) {
      const name = req.body.deviceName || 'Authenticator';
      await addUserTotpAuthenticator(userId, name, secret);
    }
  } else {
    const auths = await getUserTotpAuthenticators(userId);
    valid = auths.some((a) =>
      authenticator.verify({ token: req.body.token, secret: a.secret })
    );
  }
  if (valid) {
    if (req.body.trust) {
      const token = generateTrustedDeviceToken(userId);
      res.cookie(`trusted_${userId}`, token, {
        maxAge: 24 * 60 * 60 * 1000,
        httpOnly: true,
      });
    }
    await completeLogin(req, userId);
    req.session.tempUserId = undefined;
    req.session.pendingTotpSecret = undefined;
    req.session.requireTotpSetup = undefined;
    if (req.session.pendingForcePassword) {
      req.session.mustChangePassword = true;
      req.session.pendingForcePassword = undefined;
      return res.redirect('/force-password-change');
    }
    return res.redirect('/');
  }
  let qrCode: string | null = null;
  let secret: string | null = null;
  if (req.session.requireTotpSetup && req.session.pendingTotpSecret) {
    const user = await getUserById(userId);
    secret = req.session.pendingTotpSecret;
    const otpauth = authenticator.keyuri(
      user!.email,
      'MyPortal',
      secret
    );
    qrCode = await QRCode.toDataURL(otpauth);
  }
  res.render('totp', {
    qrCode,
    secret,
    requireSetup: req.session.requireTotpSetup,
    error: 'Invalid code',
  });
});

app.get('/register', async (req, res) => {
  const count = await getUserCount();
  if (count > 0) {
    return res.redirect('/login');
  }
  res.render('register', { error: '' });
});

app.post('/register', async (req, res) => {
  const { company, email, password } = req.body;
  try {
    const passwordHash = await bcrypt.hash(password, 10);
    const companyId = await createCompany(company);
    const userId = await createUser(email, passwordHash, companyId);
    await assignUserToCompany(userId, companyId, true, true, true, true, true, true, true);
    req.session.userId = userId;
    req.session.companyId = companyId;
    req.session.hasForms = false;
    res.redirect('/');
  } catch (err) {
    res.render('register', { error: 'Registration failed' });
  }
});

app.get('/verify', (req, res) => {
  res.render('verify', { result: null, adminName: null });
});

app.post('/verify', async (req, res) => {
  const ip = req.ip || 'unknown';
  const now = Date.now();
  const record = verifyAttempts[ip];
  if (!record || now > record.reset) {
    verifyAttempts[ip] = { count: 1, reset: now + 60 * 1000 };
  } else {
    if (record.count >= 3) {
      return res.status(429).render('verify', { result: 'rate', adminName: null });
    }
    record.count++;
  }

  const code = (req.body.code || '').trim();
  const verification = await getVerificationByCode(code);
  if (verification) {
    res.render('verify', { result: 'valid', adminName: verification.admin_name });
  } else {
    res.render('verify', { result: 'invalid', adminName: null });
  }
});

app.get('/logout', (req, res) => {
  req.session.destroy(() => {
    res.redirect('/login');
  });
});

app.get('/force-password-change', ensureAuth, (req, res) => {
  res.render('force-password-change', { error: '' });
});

app.post('/force-password-change', ensureAuth, async (req, res) => {
  const { newPassword } = req.body;
  const userId = req.session.userId!;
  const hash = await bcrypt.hash(newPassword, 10);
  await updateUserPassword(userId, hash);
  await setUserForcePasswordChange(userId, false);
  req.session.mustChangePassword = false;
  res.redirect('/');
});

app.post('/change-password', ensureAuth, async (req, res) => {
  const { currentPassword, newPassword } = req.body;
  const user = await getUserById(req.session.userId!);
  if (!user || !(await bcrypt.compare(currentPassword, user.password_hash))) {
    req.session.passwordError = 'Invalid current password';
    return res.redirect('/admin#account');
  }
  const hash = await bcrypt.hash(newPassword, 10);
  await updateUserPassword(user.id, hash);
  req.session.passwordSuccess = 'Password updated';
  res.redirect('/admin#account');
});

app.post('/change-name', ensureAuth, async (req, res) => {
  const { firstName, lastName } = req.body;
  if (!firstName || !lastName) {
    req.session.nameError = 'First name and last name are required';
    return res.redirect('/admin#account');
  }
  await updateUserName(req.session.userId!, firstName, lastName);
  req.session.nameSuccess = 'Name updated successfully';
  res.redirect('/admin#account');
});

app.post('/change-mobile', ensureAuth, async (req, res) => {
  const mobilePhone = (req.body.mobilePhone || '').trim();
  if (!mobilePhone) {
    req.session.mobileError = 'Mobile phone is required';
    return res.redirect('/admin#account');
  }
  await updateUserMobile(req.session.userId!, mobilePhone);
  req.session.mobileSuccess = 'Mobile phone updated';
  res.redirect('/admin#account');
});

app.get('/', ensureAuth, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  if (!req.session.companyId && companies.length > 0) {
    req.session.companyId = companies[0].company_id;
  }
  const company = req.session.companyId
    ? await getCompanyById(req.session.companyId)
    : null;
  const current = companies.find((c) => c.company_id === req.session.companyId);
  let activeUsers = 0;
  let licenseStats: { name: string; count: number; used: number; unused: number }[] = [];
  let assetCount = 0;
  let paidInvoices = 0;
  let unpaidInvoices = 0;

  if (company) {
    if (current?.can_manage_staff || req.session.userId === 1) {
      const staff = await getStaffByCompany(company.id);
      activeUsers = staff.filter((s) => s.enabled === 1).length;
    }

    if (current?.can_manage_licenses || req.session.userId === 1) {
      const licenses = await getLicensesByCompany(company.id);
      licenseStats = licenses.map((l) => {
        const used = l.allocated || 0;
        return { name: l.name, count: l.count, used, unused: l.count - used };
      });
    }

    if (current?.can_manage_assets || req.session.userId === 1) {
      const assets = await getAssetsByCompany(company.id);
      assetCount = assets.length;
    }

    if (current?.can_manage_invoices || req.session.userId === 1) {
      const invoices = await getInvoicesByCompany(company.id);
      paidInvoices = invoices.filter(
        (i) => i.status.toLowerCase() === 'paid'
      ).length;
      unpaidInvoices = invoices.length - paidInvoices;
    }
  }

  res.render('business', {
    company,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
    activeUsers,
    licenseStats,
    assetCount,
    paidInvoices,
    unpaidInvoices,
  });
});

async function ensureLicenseAccess(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.can_manage_licenses) {
    return next();
  }
  return res.redirect('/');
}

async function ensureStaffAccess(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  if (req.session.userId === 1) {
    return next();
  }
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.can_manage_staff) {
    return next();
  }
  return res.redirect('/');
}

async function ensureAssetsAccess(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.can_manage_assets) {
    return next();
  }
  return res.redirect('/');
}

async function ensureInvoicesAccess(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.can_manage_invoices) {
    return next();
  }
  return res.redirect('/');
}

async function ensureShopAccess(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.can_access_shop) {
    return next();
  }
  return res.redirect('/');
}
app.get('/licenses', ensureAuth, ensureLicenseAccess, async (req, res) => {
  const licenses = await getLicensesByCompany(req.session.companyId!);
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('licenses', {
    licenses,
    isAdmin: req.session.userId === 1 || (current?.is_admin ?? 0),
    companies,
    currentCompanyId: req.session.companyId,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get(
  '/licenses/:id/allocated',
  ensureAuth,
  ensureLicenseAccess,
  async (req, res) => {
    const licenseId = parseInt(req.params.id, 10);
    const license = await getLicenseById(licenseId);
    if (!license || license.company_id !== req.session.companyId) {
      return res.status(404).json({ error: 'License not found' });
    }
    const staff = await getStaffForLicense(licenseId);
    res.json(staff);
  }
);

app.post('/licenses/:id/order', ensureAuth, async (req, res) => {
  const { quantity } = req.body;
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (!current || !current.can_order_licenses) {
    return res.status(403).json({ error: 'Not allowed' });
  }
  const { LICENSES_WEBHOOK_URL, LICENSES_WEBHOOK_API_KEY } = process.env;
  if (LICENSES_WEBHOOK_URL && LICENSES_WEBHOOK_API_KEY) {
    try {
      await fetch(LICENSES_WEBHOOK_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': LICENSES_WEBHOOK_API_KEY,
        },
        body: JSON.stringify({
          companyId: req.session.companyId,
          licenseId: parseInt(req.params.id, 10),
          quantity: parseInt(quantity, 10),
          action: 'order',
        }),
      });
    } catch (err) {
      console.error('Webhook error', err);
    }
  }
  res.json({ success: true });
});

app.post('/licenses/:id/remove', ensureAuth, async (req, res) => {
  const { quantity } = req.body;
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (!current || !current.can_order_licenses) {
    return res.status(403).json({ error: 'Not allowed' });
  }
  const { LICENSES_WEBHOOK_URL, LICENSES_WEBHOOK_API_KEY } = process.env;
  if (LICENSES_WEBHOOK_URL && LICENSES_WEBHOOK_API_KEY) {
    try {
      await fetch(LICENSES_WEBHOOK_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': LICENSES_WEBHOOK_API_KEY,
        },
        body: JSON.stringify({
          companyId: req.session.companyId,
          licenseId: parseInt(req.params.id, 10),
          quantity: parseInt(quantity, 10),
          action: 'remove',
        }),
      });
    } catch (err) {
      console.error('Webhook error', err);
    }
  }
  res.json({ success: true });
});

app.put('/licenses/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { name, platform, count, expiryDate, contractTerm } = req.body;
  if (!req.session.companyId) {
    return res.status(400).json({ error: 'No company selected' });
  }
  await updateLicense(
    parseInt(req.params.id, 10),
    req.session.companyId!,
    name,
    platform,
    count,
    expiryDate || null,
    contractTerm
  );
  res.json({ success: true });
});

app.delete('/licenses/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteLicense(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.get('/staff', ensureAuth, ensureStaffAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const enabledFilter = req.query.enabled as string | undefined;
  const enabledParam =
    enabledFilter === '1' ? true : enabledFilter === '0' ? false : undefined;
  const companyId = req.session.companyId;
  const staff = companyId
    ? await getStaffByCompany(companyId, enabledParam)
    : [];
  const current = companies.find((c) => c.company_id === companyId);
  const company = companyId ? await getCompanyById(companyId) : null;
  res.render('staff', {
    staff,
    companies,
    currentCompanyId: companyId,
    isAdmin: req.session.userId === 1 || (current?.is_admin ?? 0),
    isSuperAdmin: req.session.userId === 1,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
    enabledFilter: enabledFilter ?? '',
    syncroCompanyId: company?.syncro_company_id ?? null,
  });
});

app.post('/staff', ensureAuth, ensureAdmin, async (req, res) => {
  const {
    firstName,
    lastName,
    email,
    mobilePhone,
    dateOnboarded,
    dateOffboarded,
    enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
  } = req.body;
  if (req.session.companyId) {
    await addStaff(
      req.session.companyId,
      firstName,
      lastName,
      email,
      mobilePhone || null,
      toDate(dateOnboarded),
      toDateTime(dateOffboarded),
      !!enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
    null,
    null
  );
  }
  res.redirect('/staff');
});

app.put('/staff/:id', ensureAuth, ensureStaffAccess, async (req, res) => {
  const {
    firstName,
    lastName,
    email,
    mobilePhone,
    dateOnboarded,
    dateOffboarded,
    enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
    accountAction,
  } = req.body;
  if (!req.session.companyId) {
    return res.status(400).json({ error: 'No company selected' });
  }
  const id = parseInt(req.params.id, 10);
  const isSuperAdmin = req.session.userId === 1;
  let existing: any;
  if (!isSuperAdmin) {
    existing = await getStaffById(id);
    if (!existing) {
      return res.status(404).json({ error: 'Staff not found' });
    }
  }
  const accountActionValue = isSuperAdmin ? accountAction : existing!.account_action;
  await updateStaff(
    id,
    req.session.companyId,
    isSuperAdmin ? firstName : existing!.first_name,
    isSuperAdmin ? lastName : existing!.last_name,
    isSuperAdmin ? email : existing!.email,
    isSuperAdmin ? mobilePhone : existing!.mobile_phone || null,
    isSuperAdmin ? toDate(dateOnboarded) : existing!.date_onboarded,
    toDateTime(dateOffboarded),
    isSuperAdmin ? !!enabled : !!existing!.enabled,
    isSuperAdmin ? street : existing!.street,
    isSuperAdmin ? city : existing!.city,
    isSuperAdmin ? state : existing!.state,
    isSuperAdmin ? postcode : existing!.postcode,
    isSuperAdmin ? country : existing!.country,
    isSuperAdmin ? department : existing!.department,
    isSuperAdmin ? jobTitle : existing!.job_title,
    isSuperAdmin ? company : existing!.org_company,
    isSuperAdmin ? managerName : existing!.manager_name,
    accountActionValue,
    existing?.syncro_contact_id || null
  );
  res.json({ success: true });
});

app.delete('/staff/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteStaff(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.post('/staff/enabled', ensureAuth, ensureStaffAccess, async (req, res) => {
  const { staffId, enabled } = req.body;
  await updateStaffEnabled(parseInt(staffId, 10), !!enabled);
  res.redirect('/staff');
});

app.post('/staff/:id/verify', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  const staff = await getStaffById(id);
  if (!staff || !staff.mobile_phone) {
    return res.status(400).json({ error: 'No mobile phone for staff member' });
  }
  const code = Math.floor(100000 + Math.random() * 900000).toString();
  const admin = await getUserById(req.session.userId!);
  const adminName = admin
    ? [admin.first_name, admin.last_name].filter(Boolean).join(' ').trim()
    : '';
  await setStaffVerificationCode(id, code, adminName);
  const url = process.env.VERIFY_WEBHOOK_URL;
  const apiKey = process.env.VERIFY_API_KEY;
  let status: number | null = null;
  if (url) {
    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (apiKey) {
        headers.Authorization = apiKey;
      }
      const companyName = res.locals.siteSettings?.company_name || '';
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          mobilePhone: staff.mobile_phone,
          code,
          adminName,
          companyName,
        }),
      });
      status = response.status;
    } catch (err) {
      console.error('Verify webhook failed', err);
    }
  }
  res.json({ success: status === 202, status, code });
});

app.post('/staff/:id/invite', ensureAuth, ensureStaffAccess, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  const staff = await getStaffById(id);
  if (!staff || !staff.email) {
    return res.status(400).json({ error: 'No email for staff member' });
  }
  const existing = await getUserByEmail(staff.email);
  if (existing) {
    return res.status(400).json({ error: 'User already exists' });
  }
  const tempPassword = crypto.randomBytes(12).toString('base64url');
  const passwordHash = await bcrypt.hash(tempPassword, 10);
  const userId = await createUser(staff.email, passwordHash, staff.company_id, true);
  await assignUserToCompany(
    userId,
    staff.company_id,
    false,
    false,
    false,
    false,
    false,
    false,
    false
  );
  await updateUserName(userId, staff.first_name || '', staff.last_name || '');
  const [template, siteSettings] = await Promise.all([
    getEmailTemplate('staff_invitation'),
    getSiteSettings(),
  ]);
  if (template) {
    const portalUrl =
      process.env.PORTAL_URL || `${req.protocol}://${req.get('host')}/login`;
    const html = template.body
      .replace(/\{\{companyName\}\}/g, siteSettings?.company_name || '')
      .replace(/\{\{tempPassword\}\}/g, tempPassword)
      .replace(/\{\{portalUrl\}\}/g, portalUrl)
      .replace(/\{\{loginLogo\}\}/g, siteSettings?.login_logo || '');
    const subject = template.subject.replace(
      /\{\{companyName\}\}/g,
      siteSettings?.company_name || ''
    );
    try {
      await sendEmail(staff.email, subject, html);
    } catch (err) {
      console.error('Failed to send invitation email', err);
    }
  }
  res.json({ success: true });
});

app.get('/assets', ensureAuth, ensureAssetsAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const assets = req.session.companyId
    ? await getAssetsByCompany(req.session.companyId)
    : [];
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('assets', {
    assets,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.delete('/assets/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteAsset(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.get('/invoices', ensureAuth, ensureInvoicesAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const invoices = req.session.companyId
    ? await getInvoicesByCompany(req.session.companyId)
    : [];
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('invoices', {
    invoices,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.delete('/invoices/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteInvoice(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.get('/forms', ensureAuth, async (req, res) => {
  const forms = await getFormsForUser(req.session.userId!);
  req.session.hasForms = forms.length > 0;
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('forms', {
    forms,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get('/forms/company', ensureAuth, ensureAdmin, async (req, res) => {
  const formId = req.query.formId ? parseInt(req.query.formId as string, 10) : NaN;
  const [forms, companies, users] = await Promise.all([
    getFormsByCompany(req.session.companyId!),
    getCompaniesForUser(req.session.userId!),
    getUserCompanyAssignments(req.session.companyId!),
  ]);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const permissions = !isNaN(formId)
    ? await getFormPermissions(formId, req.session.companyId!)
    : [];
  res.render('forms-company', {
    forms,
    users,
    companies,
    selectedFormId: isNaN(formId) ? null : formId,
    permissions,
    currentCompanyId: req.session.companyId,
    isAdmin: true,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.post('/forms/company', ensureAuth, ensureAdmin, async (req, res) => {
  const { formId } = req.body;
  let { userIds } = req.body as { userIds?: string | string[] };
  const ids = Array.isArray(userIds)
    ? userIds.map((id) => parseInt(id, 10))
    : userIds
    ? [parseInt(userIds, 10)]
    : [];
  const allowedForms = await getFormsByCompany(req.session.companyId!);
  const idNum = parseInt(formId, 10);
  if (!allowedForms.some((f) => f.id === idNum)) {
    return res.redirect('/forms/company');
  }
  await updateFormPermissions(idNum, req.session.companyId!, ids);
  res.redirect(`/forms/company?formId=${formId}`);
});

app.get('/shop', ensureAuth, ensureShopAccess, async (req, res) => {
  const categoryId = req.query.category
    ? parseInt(req.query.category as string, 10)
    : undefined;
  const [products, companies, categories] = await Promise.all([
    getAllProducts(false, req.session.companyId, categoryId),
    getCompaniesForUser(req.session.userId!),
    getAllCategories(),
  ]);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const isVip = current?.is_vip === 1;
  const adjusted = products.map((p) => ({
    ...p,
    price: isVip && p.vip_price !== null ? p.vip_price : p.price,
  }));
  const error = req.session.cartError;
  req.session.cartError = undefined;
  res.render('shop', {
    products: adjusted,
    categories,
    currentCategory: categoryId,
    cartError: error,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.post('/cart/add', ensureAuth, ensureShopAccess, async (req, res) => {
  const { productId, quantity } = req.body;
  const product = await getProductById(
    parseInt(productId, 10),
    false,
    req.session.companyId
  );
  if (product) {
    const companies = await getCompaniesForUser(req.session.userId!);
    const current = companies.find((c) => c.company_id === req.session.companyId);
    const isVip = current?.is_vip === 1;
    const price = isVip && product.vip_price !== null ? product.vip_price : product.price;
    if (!req.session.cart) {
      req.session.cart = [];
    }
    const qty = parseInt(quantity, 10);
    const existing = req.session.cart.find((i) => i.productId === product.id);
    const existingQty = existing ? existing.quantity : 0;
    if (existingQty + qty > product.stock) {
      req.session.cartError = `Cannot add item. Only ${
        product.stock - existingQty
      } left in stock.`;
    } else {
      if (existing) {
        existing.quantity += qty;
      } else {
        req.session.cart.push({
          productId: product.id,
          name: product.name,
          sku: product.sku,
          vendorSku: product.vendor_sku,
          description: product.description,
          imageUrl: product.image_url,
          // Ensure price is stored as a number since MySQL may return strings
          price: Number(price),
          quantity: qty,
        });
      }
    }
  }
  res.redirect('/shop');
});

app.get('/cart', ensureAuth, ensureShopAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const message = req.session.orderMessage;
  req.session.orderMessage = undefined;
  const cart = req.session.cart || [];
  const total = cart.reduce(
    (sum, item) => sum + item.price * item.quantity,
    0
  );
  res.render('cart', {
    cart,
    total,
    orderMessage: message,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.post('/cart/remove', ensureAuth, ensureShopAccess, (req, res) => {
  const { remove } = req.body;
  if (req.session.cart && remove) {
    const toRemove = Array.isArray(remove)
      ? remove.map((id: string) => parseInt(id, 10))
      : [parseInt(remove, 10)];
    req.session.cart = req.session.cart.filter(
      (item) => !toRemove.includes(item.productId)
    );
  }
  res.redirect('/cart');
});

app.post('/cart/place-order', ensureAuth, ensureShopAccess, async (req, res) => {
  const { poNumber } = req.body;
  if (req.session.companyId && req.session.cart && req.session.cart.length > 0) {
    const { SHOP_WEBHOOK_URL, SHOP_WEBHOOK_API_KEY } = process.env;
    if (SHOP_WEBHOOK_URL && SHOP_WEBHOOK_API_KEY) {
      try {
        await fetch(SHOP_WEBHOOK_URL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-api-key': SHOP_WEBHOOK_API_KEY,
          },
          body: JSON.stringify({ cart: req.session.cart }),
        });
      } catch (err) {
        console.error('Failed to call webhook', err);
      }
    }
    let orderNumber = 'ORD';
    for (let i = 0; i < 12; i++) {
      orderNumber += Math.floor(Math.random() * 10).toString();
    }
    for (const item of req.session.cart) {
      await createOrder(
        req.session.userId!,
        req.session.companyId,
        item.productId,
        item.quantity,
        orderNumber,
        'pending',
        poNumber || null
      );
    }
    req.session.cart = [];
    req.session.orderMessage = 'Your order is being processed.';
  }
  res.redirect('/cart');
});

app.get('/orders', ensureAuth, ensureShopAccess, async (req, res) => {
  const orders = req.session.companyId
    ? await getOrderSummariesByCompany(req.session.companyId)
    : [];
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const statusCounts = orders.reduce((acc: Record<string, number>, o) => {
    acc[o.status] = (acc[o.status] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);
  const shippingStatusCounts = orders.reduce(
    (acc: Record<string, number>, o) => {
      acc[o.shipping_status] = (acc[o.shipping_status] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );
  const smsSubscriptions = await getSmsSubscriptionsForUser(
    req.session.userId!
  );
  res.render('orders', {
    orders,
    statusCounts,
    shippingStatusCounts,
    smsSubscriptions,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get('/orders/:orderNumber', ensureAuth, ensureShopAccess, async (req, res) => {
  const orderNumber = req.params.orderNumber;
  const items =
    req.session.companyId
      ? await getOrderItems(orderNumber, req.session.companyId)
      : [];
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const status = items[0]?.status || '';
  const notes = items[0]?.notes || '';
  const poNumber = items[0]?.po_number || '';
  const shippingStatus = items[0]?.shipping_status || '';
  const consignmentId = items[0]?.consignment_id || '';
  const eta = items[0]?.eta || null;
  const smsSubscribed = await isUserSubscribedToOrder(
    orderNumber,
    req.session.userId!
  );
  res.render('order-details', {
    orderNumber,
    items,
    status,
    notes,
    poNumber,
    shippingStatus,
    consignmentId,
    eta,
    smsSubscribed,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.post(
  '/orders/:orderNumber/shipping',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    if (!req.session.companyId) {
      return res.redirect('/orders');
    }
    const { shippingStatus, consignmentId, eta } = req.body;
    await updateOrderShipping(
      req.params.orderNumber,
      req.session.companyId,
      shippingStatus,
      consignmentId || null,
      eta || null
    );
    await sendSmsUpdate(
      req.params.orderNumber,
      shippingStatus,
      eta || null
    );
    res.redirect(`/orders/${req.params.orderNumber}`);
  }
);

app.post('/orders/:orderNumber/sms', ensureAuth, async (req, res) => {
  const subscribe = !!req.body.subscribe;
  await setSmsSubscription(
    req.params.orderNumber,
    req.session.userId!,
    subscribe
  );
  if (subscribe) {
    const items = req.session.companyId
      ? await getOrderItems(req.params.orderNumber, req.session.companyId)
      : [];
    const shippingStatus = items[0]?.shipping_status || '';
    const eta = items[0]?.eta || null;
    await sendSmsUpdate(
      req.params.orderNumber,
      shippingStatus,
      eta ? eta.toISOString() : null
    );
  }
  res.json({ success: true });
});

app.post(
  '/orders/:orderNumber/delete',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const orderNumber = req.params.orderNumber;
    if (req.session.companyId) {
      await deleteOrder(orderNumber, req.session.companyId);
    }
    res.redirect('/orders');
  }
);

app.get('/shop/admin', ensureAuth, ensureSuperAdmin, (req, res) => {
  res.redirect('/admin');
});

app.post(
  '/shop/admin/product',
  ensureAuth,
  ensureSuperAdmin,
  upload.single('image'),
  async (req, res) => {
    const { name, sku, vendor_sku, description, price, vip_price, stock, category_id } =
      req.body;
    const imageUrl = req.file ? `/uploads/${req.file.filename}` : null;
    await createProduct(
      name,
      sku,
      vendor_sku,
      description,
      imageUrl,
      parseFloat(price),
      vip_price ? parseFloat(vip_price) : null,
      parseInt(stock, 10),
      category_id ? parseInt(category_id, 10) : null
    );
    res.redirect('/admin');
  }
);

app.post(
  '/shop/admin/product/:id',
  ensureAuth,
  ensureSuperAdmin,
  upload.single('image'),
  async (req, res) => {
    const { name, sku, vendor_sku, description, price, vip_price, stock, category_id } =
      req.body;
    const imageUrl = req.file ? `/uploads/${req.file.filename}` : null;
    await updateProduct(
      parseInt(req.params.id, 10),
      name,
      sku,
      vendor_sku,
      description,
      imageUrl,
      parseFloat(price),
      vip_price ? parseFloat(vip_price) : null,
      parseInt(stock, 10),
      category_id ? parseInt(category_id, 10) : null
    );
    res.redirect('/admin');
  }
);

app.post('/shop/admin/product/:id/archive', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await archiveProduct(parseInt(req.params.id, 10));
  res.redirect('/admin');
});

app.post('/shop/admin/product/:id/unarchive', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await unarchiveProduct(parseInt(req.params.id, 10));
  res.redirect('/admin?showArchived=1');
});

app.post('/shop/admin/product/:id/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteProduct(parseInt(req.params.id, 10));
  res.redirect('/admin');
});

app.post(
  '/shop/admin/product/:id/visibility',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const body = req.body.excluded;
    const ids = Array.isArray(body) ? body : body ? [body] : [];
    const companyIds = ids.map((id: string) => parseInt(id, 10)).filter((n) => !isNaN(n));
    await setProductCompanyExclusions(
      parseInt(req.params.id, 10),
      companyIds
    );
    res.redirect('/admin');
  }
);

app.post(
  '/shop/admin/category',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const { name } = req.body;
    if (name) {
      await createCategory(name);
    }
    res.redirect('/admin');
  }
);

app.post(
  '/shop/admin/category/:id/delete',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    await deleteCategory(parseInt(req.params.id, 10));
    res.redirect('/admin');
  }
);

app.post('/switch-company', ensureAuth, async (req, res) => {
  const { companyId } = req.body;
  const companies = await getCompaniesForUser(req.session.userId!);
  if (companies.some((c) => c.company_id === parseInt(companyId, 10))) {
    req.session.companyId = parseInt(companyId, 10);
  }
  res.redirect('/');
});

app.get('/forms/admin', ensureAuth, ensureSuperAdmin, (req, res) => {
  const params = new URLSearchParams(req.query as Record<string, string>);
  const query = params.toString();
  res.redirect(`/admin${query ? '?' + query : ''}`);
});

app.post('/forms/admin', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { name, url, description } = req.body;
  await createForm(name, url, description);
  res.redirect('/admin');
});

app.post('/forms/admin/permissions', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { formId, companyId } = req.body;
  let { userIds } = req.body as { userIds?: string | string[] };
  const ids = Array.isArray(userIds)
    ? userIds.map((id) => parseInt(id, 10))
    : userIds
    ? [parseInt(userIds, 10)]
    : [];
  await updateFormPermissions(
    parseInt(formId, 10),
    parseInt(companyId, 10),
    ids
  );
  res.redirect(`/admin?formId=${formId}&companyId=${companyId}`);
});

app.post('/forms/admin/edit', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { id, name, url, description } = req.body;
  await updateForm(parseInt(id, 10), name, url, description);
  res.redirect('/admin');
});

app.post('/forms/admin/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { id } = req.body;
  await deleteForm(parseInt(id, 10));
  res.redirect('/admin');
});

app.post(
  '/forms/admin/permissions/delete',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const { formId, userId, companyId } = req.body;
    await deleteFormPermission(
      parseInt(formId, 10),
      parseInt(userId, 10),
      parseInt(companyId, 10)
    );
    res.redirect('/admin');
  }
);

app.post('/apps', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { sku, name, price, contractTerm } = req.body;
  await createApp(sku, name, parseFloat(price), contractTerm);
  res.redirect('/admin#apps');
});

app.post('/apps/price', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { companyId, appId, price } = req.body;
  await upsertCompanyAppPrice(
    parseInt(companyId, 10),
    parseInt(appId, 10),
    parseFloat(price)
  );
  res.redirect('/admin#apps');
});

app.post('/apps/:appId/add', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const appId = parseInt(req.params.appId, 10);
  const { companyId, quantity } = req.body;
  const appInfo = await getAppById(appId);
  if (!appInfo) {
    res.status(404).send('App not found');
    return;
  }
  await createLicense(
    parseInt(companyId, 10),
    appInfo.name,
    appInfo.sku,
    parseInt(quantity, 10),
    null,
    appInfo.contract_term
  );
  res.status(204).end();
});

app.get('/admin', ensureAuth, async (req, res) => {
  const isSuperAdmin = req.session.userId === 1;
  const formId = req.query.formId ? parseInt(req.query.formId as string, 10) : NaN;
  const companyIdParam = req.query.companyId ? parseInt(req.query.companyId as string, 10) : NaN;
  const includeArchived = req.query.showArchived === '1';
  let allCompanies: Company[] = [];
  let users: User[] = [];
  let assignments: UserCompany[] = [];
  let apiKeys: ApiKeyWithUsage[] = [];
  let apps: App[] = [];
  let companyPrices: any[] = [];
  let forms: any[] = [];
  let formUsers: UserCompany[] = [];
  let permissions: number[] = [];
  let formAccess: any[] = [];
  let categories: Category[] = [];
  let products: any[] = [];
  let productRestrictions: Record<number, ProductCompanyRestriction[]> = {};
  if (isSuperAdmin) {
    allCompanies = await getAllCompanies();
    users = await getAllUsers();
    assignments = await getUserCompanyAssignments();
    apiKeys = await getApiKeysWithUsage();
    apps = await getAllApps();
    companyPrices = await getCompanyAppPrices();
    forms = await getAllForms();
    formAccess = await getAllFormPermissionEntries();
    if (!isNaN(formId) && !isNaN(companyIdParam)) {
      formUsers = await getUserCompanyAssignments(companyIdParam);
      permissions = await getFormPermissions(formId, companyIdParam);
    }
    const [prodList, restrictionsList, catList] = await Promise.all([
      getAllProducts(includeArchived),
      getProductCompanyRestrictions(),
      getAllCategories(),
    ]);
    products = prodList;
    categories = catList;
    restrictionsList.forEach((r) => {
      if (!productRestrictions[r.product_id]) {
        productRestrictions[r.product_id] = [];
      }
      productRestrictions[r.product_id].push(r);
    });
  } else {
    const companyId = req.session.companyId!;
    const company = await getCompanyById(companyId);
    allCompanies = company ? [company] : [];
    users = [];
    assignments = await getUserCompanyAssignments(companyId);
    forms = await getAllForms();
    formAccess = await getAllFormPermissionEntries();
    if (!isNaN(formId) && !isNaN(companyIdParam)) {
      formUsers = await getUserCompanyAssignments(companyIdParam);
      permissions = await getFormPermissions(formId, companyIdParam);
    }
  }
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const currentUser = await getUserById(req.session.userId!);
  const totpAuthenticators = await getUserTotpAuthenticators(req.session.userId!);
  let newTotpQr: string | null = null;
  let newTotpSecret = req.session.newTotpSecret || null;
  let newTotpName = req.session.newTotpName || '';
  const totpError = req.session.newTotpError || '';
  if (newTotpSecret && currentUser) {
    const otpauth = authenticator.keyuri(
      currentUser.email,
      'MyPortal',
      newTotpSecret
    );
    newTotpQr = await QRCode.toDataURL(otpauth);
  }
  const passwordError = req.session.passwordError || '';
  const passwordSuccess = req.session.passwordSuccess || '';
  const nameError = req.session.nameError || '';
  const nameSuccess = req.session.nameSuccess || '';
  const mobileError = req.session.mobileError || '';
  const mobileSuccess = req.session.mobileSuccess || '';
  req.session.passwordError = undefined;
  req.session.passwordSuccess = undefined;
  req.session.newTotpError = undefined;
  req.session.nameError = undefined;
  req.session.nameSuccess = undefined;
  req.session.mobileError = undefined;
  req.session.mobileSuccess = undefined;
  const isAdmin = req.session.userId === 1 || (current?.is_admin ?? 0);
  res.render('admin', {
    allCompanies,
    users,
    assignments,
    apiKeys,
    apps,
    companyPrices,
    forms,
    formUsers,
    permissions,
    formAccess,
    categories,
    products,
    productRestrictions,
    showArchived: includeArchived,
    selectedFormId: isNaN(formId) ? null : formId,
    selectedCompanyId: isNaN(companyIdParam) ? null : companyIdParam,
    isAdmin,
    isSuperAdmin,
    companies,
    currentCompanyId: req.session.companyId,
    currentUserId: req.session.userId,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
    totpAuthenticators,
    newTotpQr,
    newTotpSecret,
    newTotpName,
    totpError,
    passwordError,
    passwordSuccess,
    nameError,
    nameSuccess,
    mobileError,
    mobileSuccess,
    currentUserFirstName: currentUser?.first_name || '',
    currentUserLastName: currentUser?.last_name || '',
    currentUserMobilePhone: currentUser?.mobile_phone || '',
    siteSettings: res.locals.siteSettings,
  });
});

app.post('/admin/totp/start', ensureAuth, (req, res) => {
  const { name } = req.body;
  req.session.newTotpName = name;
  req.session.newTotpSecret = authenticator.generateSecret();
  res.redirect('/admin#account');
});

app.post('/admin/totp/verify', ensureAuth, async (req, res) => {
  if (!req.session.newTotpSecret || !req.session.newTotpName) {
    return res.redirect('/admin#account');
  }
  const valid = authenticator.verify({
    token: req.body.token,
    secret: req.session.newTotpSecret,
  });
  if (valid) {
    await addUserTotpAuthenticator(
      req.session.userId!,
      req.session.newTotpName,
      req.session.newTotpSecret
    );
    req.session.newTotpSecret = undefined;
    req.session.newTotpName = undefined;
    return res.redirect('/admin#account');
  }
  req.session.newTotpError = 'Invalid code';
  res.redirect('/admin#account');
});

app.post('/admin/totp/cancel', ensureAuth, (req, res) => {
  req.session.newTotpSecret = undefined;
  req.session.newTotpName = undefined;
  req.session.newTotpError = undefined;
  res.redirect('/admin#account');
});

app.post('/admin/totp/:id/delete', ensureAuth, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  await deleteUserTotpAuthenticator(id);
  res.redirect('/admin#account');
});

app.get('/audit-logs', ensureAuth, ensureAdmin, async (req, res) => {
  const isSuperAdmin = req.session.userId === 1;
  const companiesForSidebar = await getCompaniesForUser(req.session.userId!);
  const current = companiesForSidebar.find((c) => c.company_id === req.session.companyId);
  let selectedCompanyId: number | null = null;
  let logs: AuditLog[] = [];
  let allCompanies: Company[] = [];
  if (isSuperAdmin) {
    allCompanies = await getAllCompanies();
    selectedCompanyId = req.query.companyId ? parseInt(req.query.companyId as string, 10) : null;
    logs = await getAuditLogs(selectedCompanyId || undefined);
  } else {
    selectedCompanyId = req.session.companyId || null;
    if (selectedCompanyId) {
      logs = await getAuditLogs(selectedCompanyId);
    }
  }
  res.render('audit-logs', {
    logs,
    isSuperAdmin,
    companies: companiesForSidebar,
    filterCompanies: allCompanies,
    selectedCompanyId,
    currentCompanyId: req.session.companyId,
    isAdmin: true,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get('/office-groups', ensureAuth, ensureStaffAccess, async (req, res) => {
  const isSuperAdmin = req.session.userId === 1;
  const [officeGroups, staff] = await Promise.all([
    getOfficeGroupsByCompany(req.session.companyId!),
    getStaffByCompany(req.session.companyId!),
  ]);
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const isAdmin = req.session.userId === 1 || (current?.is_admin ?? 0);
  res.render('office-groups', {
    isAdmin,
    isSuperAdmin,
    companies,
    currentCompanyId: req.session.companyId,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
    officeGroups,
    staff,
  });
});

app.post('/admin/company', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { name, isVip, syncroCompanyId, xeroId } = req.body;
  await createCompany(
    name,
    undefined,
    parseCheckbox(isVip),
    syncroCompanyId,
    xeroId
  );
  res.redirect('/admin');
});

app.post('/admin/company/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { syncroCompanyId, xeroId, isVip } = req.body;
  await updateCompanyIds(
    parseInt(req.params.id, 10),
    syncroCompanyId || null,
    xeroId || null,
    parseCheckbox(isVip)
  );
  res.redirect('/admin');
});

app.get('/admin/syncro/customers', ensureAuth, ensureSuperAdmin, async (req, res) => {
  try {
    const showHidden = req.query.showHidden === '1';
    const [customers, hiddenIds, allCompanies, companies] = await Promise.all([
      getSyncroCustomers(),
      getHiddenSyncroCustomerIds(),
      getAllCompanies(),
      getCompaniesForUser(req.session.userId!),
    ]);
    const current = companies.find(
      (c) => c.company_id === req.session.companyId
    );
    const importedIds = allCompanies
      .filter((c) => c.syncro_company_id)
      .map((c) => c.syncro_company_id!);
    const visibleCustomers = showHidden
      ? customers
      : customers.filter((c) => !hiddenIds.includes(String(c.id)));
    res.render('syncro-customers', {
      customers: visibleCustomers,
      importedIds,
      hiddenIds,
      showHidden,
      companies,
      currentCompanyId: req.session.companyId,
      isAdmin: true,
      canManageLicenses: current?.can_manage_licenses ?? 0,
      canManageStaff: current?.can_manage_staff ?? 0,
      canManageAssets: current?.can_manage_assets ?? 0,
      canManageInvoices: current?.can_manage_invoices ?? 0,
      canAccessShop: current?.can_access_shop ?? 0,
    });
  } catch (err) {
    console.error('Failed to fetch Syncro customers', err);
    res.status(500).send('Failed to fetch Syncro customers');
  }
});

app.post('/admin/syncro/import', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { customerId, showHidden } = req.body;
  const redirectUrl = `/admin/syncro/customers${
    showHidden === '1' ? '?showHidden=1' : ''
  }`;
  if (!customerId) {
    return res.redirect(redirectUrl);
  }
  try {
    const customer = await getSyncroCustomer(customerId);
    if (customer) {
      const name =
        customer.business_name ||
        [customer.first_name, customer.last_name].filter(Boolean).join(' ') ||
        `Customer ${customer.id}`;
      const parts = [
        customer.address1,
        customer.address2,
        customer.city,
        customer.state,
        customer.zip,
      ].filter((p) => p);
      const address = parts.length ? parts.join(', ') : null;
      const existing = await getCompanyBySyncroId(String(customer.id));
      if (existing) {
        await updateCompany(existing.id, name, address);
      } else {
        await createCompany(name, address || undefined, false, String(customer.id));
      }
    }
  } catch (err) {
    console.error('Syncro import failed', err);
  }
  res.redirect(redirectUrl);
});

app.post(
  '/admin/syncro/import-contacts',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const { syncroCompanyId } = req.body;
    if (!syncroCompanyId) {
      return res.status(400).send('syncroCompanyId required');
    }
    try {
      const company = await getCompanyBySyncroId(syncroCompanyId);
      if (!company) {
        return res.status(404).send('Company not found');
      }
      const [contacts, existingStaff] = await Promise.all([
        getSyncroContacts(syncroCompanyId),
        getStaffByCompany(company.id),
      ]);
      for (const contact of contacts) {
        const fullName = [
          contact.first_name,
          contact.last_name,
          contact.name,
        ]
          .filter(Boolean)
          .join(' ')
          .trim();
        if (/ex staff/i.test(fullName)) {
          continue;
        }
        const firstName =
          contact.first_name || fullName.split(' ')[0] || '';
        const lastName =
          contact.last_name ||
          fullName
            .split(' ')
            .slice(1)
            .join(' ');
        const email = (contact as any).email || (contact as any).email_address || null;
        const phone = (contact as any).mobile || (contact as any).phone || null;
        const existing = findExistingStaff(
          existingStaff,
          firstName,
          lastName,
          email
        );
        if (existing) {
          await updateStaff(
            existing.id,
            company.id,
            firstName,
            lastName,
            email || existing.email,
            phone || existing.mobile_phone || null,
            existing.date_onboarded,
            existing.date_offboarded || null,
            existing.enabled === 1,
            existing.street || null,
            existing.city || null,
            existing.state || null,
            existing.postcode || null,
            existing.country || null,
            existing.department || null,
            existing.job_title || null,
            existing.org_company || null,
            existing.manager_name || null,
            existing.account_action || null,
            String(contact.id)
          );
        } else {
          await addStaff(
            company.id,
            firstName,
            lastName,
            email || '',
            phone || null,
            null,
            null,
            true,
            (contact as any).address1 || (contact as any).address || null,
            (contact as any).city || null,
            (contact as any).state || null,
            (contact as any).zip || null,
            (contact as any).country || null,
            null,
            (contact as any).title || null,
            null,
            null,
            String(contact.id)
          );
          existingStaff.push({
            id: 0,
            company_id: company.id,
            first_name: firstName,
            last_name: lastName,
            email: email || '',
            mobile_phone: phone || null,
            date_onboarded: null,
            date_offboarded: null,
            enabled: 1,
            street: (contact as any).address1 || (contact as any).address || null,
            city: (contact as any).city || null,
            state: (contact as any).state || null,
            postcode: (contact as any).zip || null,
            country: (contact as any).country || null,
            department: null,
            job_title: (contact as any).title || null,
            org_company: null,
            manager_name: null,
            account_action: null,
            syncro_contact_id: String(contact.id),
          } as any);
        }
      }
      res.sendStatus(200);
    } catch (err) {
      console.error('Syncro contacts import failed', err);
      res.status(500).send('Failed to import Syncro contacts');
    }
  }
);

app.post('/admin/syncro/hide', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { customerId, showHidden } = req.body;
  if (customerId) {
    await hideSyncroCustomer(customerId);
  }
  res.redirect(`/admin/syncro/customers${showHidden === '1' ? '?showHidden=1' : ''}`);
});

app.post('/admin/syncro/unhide', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { customerId, showHidden } = req.body;
  if (customerId) {
    await unhideSyncroCustomer(customerId);
  }
  res.redirect(`/admin/syncro/customers${showHidden === '1' ? '?showHidden=1' : ''}`);
});

app.post('/admin/user', ensureAuth, ensureAdmin, async (req, res) => {
  const { email, password } = req.body;
  const isSuperAdmin = req.session.userId === 1;
  const passwordHash = await bcrypt.hash(password, 10);
  const companyId = isSuperAdmin
    ? parseInt(req.body.companyId, 10)
    : req.session.companyId!;
  const userId = await createUser(email, passwordHash, companyId);
  await assignUserToCompany(userId, companyId, false, false, false, false, false, false, false);
  res.redirect('/admin');
});

app.delete('/admin/user/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteUser(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.post('/admin/invite', ensureAuth, ensureAdmin, async (req, res) => {
  const { email, firstName, lastName } = req.body;
  const isSuperAdmin = req.session.userId === 1;
  const companyId = isSuperAdmin
    ? parseInt(req.body.companyId, 10)
    : req.session.companyId!;
  const tempPassword = crypto.randomBytes(12).toString('base64url');
  const passwordHash = await bcrypt.hash(tempPassword, 10);
  const userId = await createUser(email, passwordHash, companyId, true);
  await assignUserToCompany(
    userId,
    companyId,
    false,
    false,
    false,
    false,
    false,
    false,
    false
  );
  if (firstName || lastName) {
    await updateUserName(userId, firstName || '', lastName || '');
  }
  const [template, siteSettings] = await Promise.all([
    getEmailTemplate('staff_invitation'),
    getSiteSettings(),
  ]);
  if (template) {
    const portalUrl =
      process.env.PORTAL_URL || `${req.protocol}://${req.get('host')}/login`;
    const html = template.body
      .replace(/\{\{companyName\}\}/g, siteSettings?.company_name || '')
      .replace(/\{\{tempPassword\}\}/g, tempPassword)
      .replace(/\{\{portalUrl\}\}/g, portalUrl)
      .replace(/\{\{loginLogo\}\}/g, siteSettings?.login_logo || '');
    const subject = template.subject.replace(
      /\{\{companyName\}\}/g,
      siteSettings?.company_name || ''
    );
    try {
      await sendEmail(email, subject, html);
    } catch (err) {
      console.error('Failed to send invitation email', err);
    }
  }
  res.redirect('/admin');
});

app.post('/admin/api-key', ensureAuth, ensureAdmin, async (req, res) => {
  const { description, expiryDate } = req.body;
  const key = crypto.randomBytes(32).toString('hex');
  await createApiKey(key, description, expiryDate);
  res.redirect('/admin');
});

app.post('/admin/api-key/delete', ensureAuth, ensureAdmin, async (req, res) => {
  const { id } = req.body;
  await deleteApiKey(parseInt(id, 10));
  res.redirect('/admin');
});

app.post(
  '/admin/site-settings',
  ensureAuth,
  ensureSuperAdmin,
  memoryUpload.fields([
    { name: 'loginLogo', maxCount: 1 },
    { name: 'sidebarLogo', maxCount: 1 },
  ]),
  async (req, res) => {
    const { companyName } = req.body;
    const files = req.files as {
      [fieldname: string]: Express.Multer.File[];
    };
    let loginLogo: string | undefined;
    let sidebarLogo: string | undefined;
    if (files && files.loginLogo && files.loginLogo[0]) {
      const file = files.loginLogo[0];
      loginLogo = `data:${file.mimetype};base64,${file.buffer.toString('base64')}`;
    }
    if (files && files.sidebarLogo && files.sidebarLogo[0]) {
      const file = files.sidebarLogo[0];
      sidebarLogo = `data:${file.mimetype};base64,${file.buffer.toString('base64')}`;
    }
    await updateSiteSettings(companyName, loginLogo, sidebarLogo);
    res.redirect('/admin#site-settings');
  }
);

app.get('/admin/email-templates', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const [template, companies] = await Promise.all([
    getEmailTemplate('staff_invitation'),
    getCompaniesForUser(req.session.userId!),
  ]);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('email-templates', {
    template,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: true,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.post('/admin/email-templates', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { subject, body } = req.body;
  await upsertEmailTemplate('staff_invitation', subject, body);
  res.redirect('/admin/email-templates');
});

app.post('/admin/assign', ensureAuth, ensureAdmin, async (req, res) => {
  const { userId } = req.body;
  const companyId = req.session.userId === 1
    ? parseInt(req.body.companyId, 10)
    : req.session.companyId!;
  await assignUserToCompany(
    parseInt(userId, 10),
    companyId,
    false,
    false,
    false,
    false,
    false,
    false,
    false
  );
  res.redirect('/admin');
});

app.delete('/admin/assign/:companyId/:userId', ensureAuth, ensureSuperAdmin, async (
  req,
  res
) => {
  await unassignUserFromCompany(
    parseInt(req.params.userId, 10),
    parseInt(req.params.companyId, 10)
  );
  res.json({ success: true });
});

function parseCheckbox(value: unknown): boolean {
  if (Array.isArray(value)) {
    value = value[value.length - 1];
  }
  return value === '1' || value === 'on' || value === true;
}

app.post('/admin/permission', ensureAuth, ensureAdmin, async (req, res) => {
  const {
    userId,
    companyId,
    canManageLicenses,
    canManageStaff,
    canManageAssets,
    canManageInvoices,
    canOrderLicenses,
    canAccessShop,
    isAdmin: isAdminField,
  } = req.body;
  const uid = parseInt(userId, 10);
  const cid = req.session.userId === 1 ? parseInt(companyId, 10) : req.session.companyId!;
  if (typeof canManageLicenses !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_manage_licenses',
      parseCheckbox(canManageLicenses)
    );
  }
  if (typeof canManageStaff !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_manage_staff',
      parseCheckbox(canManageStaff)
    );
  }
  if (typeof canManageAssets !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_manage_assets',
      parseCheckbox(canManageAssets)
    );
  }
  if (typeof canManageInvoices !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_manage_invoices',
      parseCheckbox(canManageInvoices)
    );
  }
  if (typeof canOrderLicenses !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_order_licenses',
      parseCheckbox(canOrderLicenses)
    );
  }
  if (typeof canAccessShop !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_access_shop',
      parseCheckbox(canAccessShop)
    );
  }
  if (typeof isAdminField !== 'undefined') {
    const isAdminValue = parseCheckbox(isAdminField);
    if (uid !== req.session.userId || req.session.userId === 1 || isAdminValue) {
      await updateUserCompanyPermission(
        uid,
        cid,
        'is_admin',
        isAdminValue
      );
    }
  }
  res.redirect('/admin');
});

app.post('/office-groups', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await createOfficeGroup(req.session.companyId!, req.body.name);
  res.redirect('/office-groups');
});

app.post(
  '/office-groups/:id/members',
  ensureAuth,
  ensureStaffAccess,
  async (req, res) => {
    const raw = req.body.staffIds;
    const ids = Array.isArray(raw)
      ? raw.map((s: string) => parseInt(s, 10))
      : raw
      ? [parseInt(raw, 10)]
      : [];
    await updateOfficeGroupMembers(parseInt(req.params.id, 10), ids);
    res.redirect('/office-groups');
  }
);

app.post('/office-groups/:id/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteOfficeGroup(parseInt(req.params.id, 10));
  res.redirect('/office-groups');
});

const api = express.Router();

api.use(async (req, res, next) => {
  const key = req.header('x-api-key');
  if (!key) {
    return res.status(401).json({ error: 'API key required' });
  }
  const record = await getApiKeyRecord(key);
  if (!record) {
    return res.status(403).json({ error: 'Invalid API key' });
  }
  req.apiKey = key;
  const ip = (req.headers['cf-connecting-ip'] as string) || req.ip || '';
  await recordApiKeyUsage(record.id, ip);
  next();
});

/**
 * @openapi
 * /api/apps:
 *   get:
 *     tags:
 *       - Apps
 *     summary: List all apps
 *     responses:
 *       200:
 *         description: Array of apps
 *   post:
 *     tags:
 *       - Apps
 *     summary: Create a new app
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               sku:
 *                 type: string
 *               name:
 *                 type: string
 *               defaultPrice:
 *                 type: number
 *               contractTerm:
 *                 type: string
 *     responses:
 *       200:
 *         description: App created
 */
api.route('/apps')
  .get(async (req, res) => {
    const apps = await getAllApps();
    res.json(apps);
  })
  .post(async (req, res) => {
    const { sku, name, defaultPrice, contractTerm } = req.body;
    const id = await createApp(sku, name, defaultPrice, contractTerm);
    res.json({ id });
  });

/**
 * @openapi
 * /api/apps/{id}:
 *   get:
 *     tags:
 *       - Apps
 *     summary: Get an app by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: App details
 *       404:
 *         description: App not found
 *   put:
 *     tags:
 *       - Apps
 *     summary: Update an app
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               sku:
 *                 type: string
 *               name:
 *                 type: string
 *               defaultPrice:
 *                 type: number
 *               contractTerm:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 *   delete:
 *     tags:
 *       - Apps
 *     summary: Delete an app
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.route('/apps/:id')
  .get(async (req, res) => {
    const app = await getAppById(parseInt(req.params.id, 10));
    if (!app) {
      return res.status(404).json({ error: 'App not found' });
    }
    res.json(app);
  })
  .put(async (req, res) => {
    const { sku, name, defaultPrice, contractTerm } = req.body;
    await updateApp(
      parseInt(req.params.id, 10),
      sku,
      name,
      defaultPrice,
      contractTerm
    );
    res.json({ success: true });
  })
  .delete(async (req, res) => {
    await deleteApp(parseInt(req.params.id, 10));
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/shop/products:
 *   get:
 *     tags:
 *       - Shop
 *     summary: List all products
 *     responses:
 *       200:
 *         description: Array of products
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   name:
 *                     type: string
 *                   sku:
 *                     type: string
 *                   vendor_sku:
 *                     type: string
 *                   description:
 *                     type: string
 *                   image_url:
 *                     type: string
 *                   price:
 *                     type: number
 *                   stock:
 *                     type: integer
 *                   category_id:
 *                     type: integer
 *                   category_name:
 *                     type: string
 *   post:
 *     tags:
 *       - Shop
 *     summary: Create a product
 *     requestBody:
 *       required: true
 *       content:
 *         multipart/form-data:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *               sku:
 *                 type: string
 *               vendor_sku:
 *                 type: string
 *               description:
 *                 type: string
 *               price:
 *                 type: number
 *               vip_price:
 *                 type: number
 *               stock:
 *                 type: integer
 *               category_id:
 *                 type: integer
 *               image:
 *                 type: string
 *                 format: binary
 *     responses:
 *       200:
 *         description: ID of created product
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 */
api.route('/shop/products')
  .get(async (req, res) => {
    const includeArchived = req.query.includeArchived === '1';
    const companyId = req.query.companyId
      ? parseInt(req.query.companyId as string, 10)
      : undefined;
    const categoryId = req.query.categoryId
      ? parseInt(req.query.categoryId as string, 10)
      : undefined;
    const products = await getAllProducts(
      includeArchived,
      companyId,
      categoryId
    );
    res.json(products);
  })
  .post(upload.single('image'), async (req, res) => {
    const { name, sku, vendor_sku, description, price, vip_price, stock, category_id } =
      req.body;
    const imageUrl = req.file ? `/uploads/${req.file.filename}` : null;
    const id = await createProduct(
      name,
      sku,
      vendor_sku,
      description,
      imageUrl,
      parseFloat(price),
      vip_price ? parseFloat(vip_price) : null,
      parseInt(stock, 10),
      category_id ? parseInt(category_id, 10) : null
    );
    res.json({ id });
  });

/**
 * @openapi
 * /api/shop/products/{id}:
 *   get:
 *     tags:
 *       - Shop
 *     summary: Get a product by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Product details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 name:
 *                   type: string
 *                 sku:
 *                   type: string
 *                 vendor_sku:
 *                   type: string
 *                 description:
 *                   type: string
 *                 image_url:
 *                   type: string
 *                 price:
 *                   type: number
 *                 stock:
 *                   type: integer
 *                 category_id:
 *                   type: integer
 *                 category_name:
 *                   type: string
 *                 category_id:
 *                   type: integer
 *                 category_name:
 *                   type: string
 *       404:
 *         description: Product not found
 *   put:
 *     tags:
 *       - Shop
 *     summary: Update a product
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         multipart/form-data:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *               sku:
 *                 type: string
 *               vendor_sku:
 *                 type: string
 *               description:
 *                 type: string
 *               price:
 *                 type: number
 *               vip_price:
 *                 type: number
 *               stock:
 *                 type: integer
 *               category_id:
 *                 type: integer
 *               image:
 *                 type: string
 *                 format: binary
 *     responses:
 *       200:
 *         description: Update successful
 *   delete:
 *     tags:
 *       - Shop
 *     summary: Delete a product
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.route('/shop/products/:id')
  .get(async (req, res) => {
    const includeArchived = req.query.includeArchived === '1';
    const companyId = req.query.companyId
      ? parseInt(req.query.companyId as string, 10)
      : undefined;
    const product = await getProductById(
      parseInt(req.params.id, 10),
      includeArchived,
      companyId
    );
    if (!product) {
      return res.status(404).json({ error: 'Product not found' });
    }
    res.json(product);
  })
  .put(upload.single('image'), async (req, res) => {
    const { name, sku, vendor_sku, description, price, vip_price, stock, category_id } =
      req.body;
    const imageUrl = req.file ? `/uploads/${req.file.filename}` : null;
    await updateProduct(
      parseInt(req.params.id, 10),
      name,
      sku,
      vendor_sku,
      description,
      imageUrl,
      parseFloat(price),
      vip_price ? parseFloat(vip_price) : null,
      parseInt(stock, 10),
      category_id ? parseInt(category_id, 10) : null
    );
    res.json({ success: true });
  })
  .delete(async (req, res) => {
    await deleteProduct(parseInt(req.params.id, 10));
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/shop/products/sku/{sku}:
 *   get:
 *     tags:
 *       - Shop
 *     summary: Get a product by SKU
 *     parameters:
 *       - in: path
 *         name: sku
 *         required: true
 *         schema:
 *           type: string
 *     responses:
 *       200:
 *         description: Product details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 name:
 *                   type: string
 *                 sku:
 *                   type: string
 *                 vendor_sku:
 *                   type: string
 *                 description:
 *                   type: string
 *                 image_url:
 *                   type: string
 *                 price:
 *                   type: number
 *                 stock:
 *                   type: integer
 *       404:
 *         description: Product not found
 */
api.get('/shop/products/sku/:sku', async (req, res) => {
  const includeArchived = req.query.includeArchived === '1';
  const companyId = req.query.companyId
    ? parseInt(req.query.companyId as string, 10)
    : undefined;
  const product = await getProductBySku(
    req.params.sku,
    includeArchived,
    companyId
  );
  if (!product) {
    return res.status(404).json({ error: 'Product not found' });
  }
  res.json(product);
});

/**
 * @openapi
 * /api/shop/categories:
 *   get:
 *     tags:
 *       - Shop
 *     summary: List all categories
 *     responses:
 *       200:
 *         description: Array of categories
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   name:
 *                     type: string
 *   post:
 *     tags:
 *       - Shop
 *     summary: Create a category
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *     responses:
 *       200:
 *         description: ID of created category
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 */
api.route('/shop/categories')
  .get(async (req, res) => {
    const categories = await getAllCategories();
    res.json(categories);
  })
  .post(async (req, res) => {
    const { name } = req.body;
    const id = await createCategory(name);
    res.json({ id });
  });

/**
 * @openapi
 * /api/shop/categories/{id}:
 *   get:
 *     tags:
 *       - Shop
 *     summary: Get a category by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Category details
 *   put:
 *     tags:
 *       - Shop
 *     summary: Update a category
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 *   delete:
 *     tags:
 *       - Shop
 *     summary: Delete a category
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.route('/shop/categories/:id')
  .get(async (req, res) => {
    const category = await getCategoryById(parseInt(req.params.id, 10));
    if (!category) {
      return res.status(404).json({ error: 'Category not found' });
    }
    res.json(category);
  })
  .put(async (req, res) => {
    const { name } = req.body;
    await updateCategory(parseInt(req.params.id, 10), name);
    res.json({ success: true });
  })
  .delete(async (req, res) => {
    await deleteCategory(parseInt(req.params.id, 10));
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/shop/orders:
 *   get:
 *     tags:
 *       - Shop
 *     summary: List order items for a company
 *     parameters:
 *       - in: query
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Array of order items
 *   post:
 *     tags:
 *       - Shop
 *     summary: Create an order item
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               userId:
 *                 type: integer
 *               companyId:
 *                 type: integer
 *               productId:
 *                 type: integer
 *               quantity:
 *                 type: integer
 *               orderNumber:
 *                 type: string
 *               poNumber:
 *                 type: string
 *               status:
 *                 type: string
 *     responses:
 *       200:
 *         description: Order item created
 */
api
  .route('/shop/orders')
  .get(async (req, res) => {
    const companyId = req.query.companyId;
    if (!companyId) {
      return res.status(400).json({ error: 'companyId required' });
    }
    const orders = await getOrdersByCompany(parseInt(companyId as string, 10));
    res.json(orders);
  })
  .post(async (req, res) => {
    const {
      userId,
      companyId,
      productId,
      quantity,
      orderNumber,
      poNumber,
      status,
    } = req.body;
    let num = orderNumber as string | undefined;
    if (!num) {
      num = 'ORD';
      for (let i = 0; i < 12; i++) {
        num += Math.floor(Math.random() * 10).toString();
      }
    }
    const uId = parseInt(userId, 10);
    const cId = parseInt(companyId, 10);
    const pId = parseInt(productId, 10);
    const qty = parseInt(quantity, 10);
    const { SHOP_WEBHOOK_URL, SHOP_WEBHOOK_API_KEY } = process.env;
    if (SHOP_WEBHOOK_URL && SHOP_WEBHOOK_API_KEY) {
      try {
        await fetch(SHOP_WEBHOOK_URL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-api-key': SHOP_WEBHOOK_API_KEY,
          },
          body: JSON.stringify({
            userId: uId,
            companyId: cId,
            productId: pId,
            quantity: qty,
            orderNumber: num,
          }),
        });
      } catch (err) {
        console.error('Webhook error', err);
      }
    }
    await createOrder(
      uId,
      cId,
      pId,
      qty,
      num,
      status || 'pending',
      poNumber || null
    );
    res.json({ success: true, orderNumber: num });
  });

/**
 * @openapi
 * /api/shop/orders/{orderNumber}:
 *   put:
 *     tags:
 *       - Shop
 *     summary: Update an order
 *     parameters:
 *       - in: path
 *         name: orderNumber
 *         required: true
 *         schema:
 *           type: string
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               companyId:
 *                 type: integer
 *               status:
 *                 type: string
 *               notes:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 *   delete:
 *     tags:
 *       - Shop
 *     summary: Delete an order
 *     parameters:
 *       - in: path
 *         name: orderNumber
 *         required: true
 *         schema:
 *           type: string
 *       - in: query
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api
  .route('/shop/orders/:orderNumber')
  .put(async (req, res) => {
    const { status, notes, companyId } = req.body;
    if (!companyId) {
      return res.status(400).json({ error: 'companyId required' });
    }
    await updateOrder(
      req.params.orderNumber,
      parseInt(companyId, 10),
      status,
      notes || null
    );
    res.json({ success: true });
  })
  .delete(async (req, res) => {
    const companyId = req.query.companyId;
    if (!companyId) {
      return res.status(400).json({ error: 'companyId required' });
    }
    await deleteOrder(
      req.params.orderNumber,
      parseInt(companyId as string, 10)
    );
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/shop/orders/consignment/{consignmentId}:
 *   get:
 *     tags:
 *       - Shop
 *     summary: Get orders by consignment ID
 *     parameters:
 *       - in: path
 *         name: consignmentId
 *         required: true
 *         schema:
 *           type: string
 *     responses:
 *       200:
 *         description: Orders retrieved
 *   put:
 *     tags:
 *       - Shop
 *     summary: Update shipping status by consignment ID
 *     parameters:
 *       - in: path
 *         name: consignmentId
 *         required: true
 *         schema:
 *           type: string
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               shippingStatus:
 *                 type: string
 *               eta:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api
  .route('/shop/orders/consignment/:consignmentId')
  .get(async (req, res) => {
    const orders = await getOrdersByConsignmentId(
      req.params.consignmentId
    );
    res.json(orders);
  })
  .put(async (req, res) => {
    const { shippingStatus, eta } = req.body;
    await updateShippingStatusByConsignmentId(
      req.params.consignmentId,
      shippingStatus,
      eta || null
    );
    const orders = await getOrdersByConsignmentId(req.params.consignmentId);
    for (const order of orders) {
      await sendSmsUpdate(
        order.order_number,
        shippingStatus,
        eta || null
      );
    }
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/apps/{appId}/companies/{companyId}/price:
 *   get:
 *     tags:
 *       - Apps
 *     summary: Get company-specific price for an app
 *     parameters:
 *       - in: path
 *         name: appId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Price details
 *       404:
 *         description: Price not found
 *   post:
 *     tags:
 *       - Apps
 *     summary: Set company-specific price for an app
 *     parameters:
 *       - in: path
 *         name: appId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               price:
 *                 type: number
 *     responses:
 *       200:
 *         description: Price set
 *   put:
 *     tags:
 *       - Apps
 *     summary: Update company-specific price for an app
 *     parameters:
 *       - in: path
 *         name: appId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               price:
 *                 type: number
 *     responses:
 *       200:
 *         description: Update successful
 *   delete:
 *     tags:
 *       - Apps
 *     summary: Delete company-specific price for an app
 *     parameters:
 *       - in: path
 *         name: appId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api
  .route('/apps/:appId/companies/:companyId/price')
  .get(async (req, res) => {
    const price = await getAppPrice(
      parseInt(req.params.companyId, 10),
      parseInt(req.params.appId, 10)
    );
    if (price === null) {
      return res.status(404).json({ error: 'Price not found' });
    }
    res.json({ price });
  })
  .post(async (req, res) => {
    const { price } = req.body;
    await upsertCompanyAppPrice(
      parseInt(req.params.companyId, 10),
      parseInt(req.params.appId, 10),
      price
    );
    res.json({ success: true });
  })
  .put(async (req, res) => {
    const { price } = req.body;
    await upsertCompanyAppPrice(
      parseInt(req.params.companyId, 10),
      parseInt(req.params.appId, 10),
      price
    );
    res.json({ success: true });
  })
  .delete(async (req, res) => {
    await deleteCompanyAppPrice(
      parseInt(req.params.companyId, 10),
      parseInt(req.params.appId, 10)
    );
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/companies:
 *   get:
 *     tags:
 *       - Companies
 *     summary: List all companies
 *     responses:
 *       200:
 *         description: Array of companies
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   name:
 *                     type: string
 *                   address:
 *                     type: string
 */
api.get('/companies', async (_req, res) => {
  const companies = await getAllCompanies();
  res.json(companies);
});

/**
 * @openapi
 * /api/companies:
 *   post:
 *     tags:
 *       - Companies
 *     summary: Create a company
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - name
 *             properties:
 *               name:
 *                 type: string
 *               address:
 *                 type: string
 *               syncroCompanyId:
 *                 type: string
 *               xeroId:
 *                 type: string
 *               isVip:
 *                 type: boolean
 *     responses:
 *       200:
 *         description: Company created
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 */
api.post('/companies', async (req, res) => {
  const { name, address, syncroCompanyId, xeroId, isVip } = req.body;
  const id = await createCompany(
    name,
    address,
    parseCheckbox(isVip),
    syncroCompanyId,
    xeroId
  );
  res.json({ id });
});

/**
 * @openapi
 * /api/companies/{id}:
 *   get:
 *     tags:
 *       - Companies
 *     summary: Get a company by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Company details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 name:
 *                   type: string
 *                 address:
 *                   type: string
 *                 syncroCompanyId:
 *                   type: string
 *                 xeroId:
 *                   type: string
 *                 isVip:
 *                   type: boolean
 *       404:
 *         description: Company not found
 */
api.get('/companies/:id', async (req, res) => {
  const company = await getCompanyById(parseInt(req.params.id, 10));
  if (!company) {
    return res.status(404).json({ error: 'Company not found' });
  }
  res.json(company);
});

/**
 * @openapi
 * /api/companies/{id}:
 *   put:
 *     tags:
 *       - Companies
 *     summary: Update a company
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *               address:
 *                 type: string
 *               syncroCompanyId:
 *                 type: string
 *               xeroId:
 *                 type: string
 *               isVip:
 *                 type: boolean
 *     responses:
 *       200:
 *         description: Update successful
 */
export async function updateCompanyHandler(
  req: express.Request,
  res: express.Response,
  deps: {
    getCompanyById: typeof getCompanyById;
    updateCompany: typeof updateCompany;
    updateCompanyIds: typeof updateCompanyIds;
  } = {
    getCompanyById,
    updateCompany,
    updateCompanyIds,
  }
): Promise<void> {
  const { name, address, syncroCompanyId, xeroId, isVip } = req.body;
  const id = parseInt(req.params.id, 10);
  if (name !== undefined || address !== undefined) {
    let newName = name;
    let newAddress = address;
    if (name === undefined || address === undefined) {
      const current = await deps.getCompanyById(id);
      if (!current) {
        res.status(404).json({ error: 'Company not found' });
        return;
      }
      if (newName === undefined) {
        newName = current.name;
      }
      if (newAddress === undefined) {
        newAddress = current.address || null;
      }
    }
    await deps.updateCompany(id, newName, newAddress || null);
  }
  if (
    syncroCompanyId !== undefined ||
    xeroId !== undefined ||
    isVip !== undefined
  ) {
    await deps.updateCompanyIds(
      id,
      syncroCompanyId || null,
      xeroId || null,
      parseCheckbox(isVip)
    );
  }
  res.json({ success: true });
}

api.put('/companies/:id', (req, res) => updateCompanyHandler(req, res));

/**
 * @openapi
 * /api/companies/{id}:
 *   delete:
 *     tags:
 *       - Companies
 *     summary: Delete a company
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/companies/:id', async (req, res) => {
  await deleteCompany(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/users:
 *   get:
 *     tags:
 *       - Users
 *     summary: List all users
 *     responses:
 *       200:
 *         description: Array of users
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   email:
 *                     type: string
 *                   company_id:
 *                     type: integer
 */
api.get('/users', async (_req, res) => {
  const users = await getAllUsers();
  res.json(users);
});

/**
 * @openapi
 * /api/users:
 *   post:
 *     tags:
 *       - Users
 *     summary: Create a user
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - email
 *               - password
 *               - companyId
 *             properties:
 *               email:
 *                 type: string
 *               password:
 *                 type: string
 *               companyId:
 *                 type: integer
 *     responses:
 *       200:
 *         description: User created
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 */
api.post('/users', async (req, res) => {
  const { email, password, companyId } = req.body;
  const passwordHash = await bcrypt.hash(password, 10);
  const id = await createUser(email, passwordHash, companyId);
  res.json({ id });
});

/**
 * @openapi
 * /api/users/{id}:
 *   get:
 *     tags:
 *       - Users
 *     summary: Get a user by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: User details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 email:
 *                   type: string
 *                 company_id:
 *                   type: integer
 *       404:
 *         description: User not found
 */
api.get('/users/:id', async (req, res) => {
  const user = await getUserById(parseInt(req.params.id, 10));
  if (!user) {
    return res.status(404).json({ error: 'User not found' });
  }
  res.json(user);
});

/**
 * @openapi
 * /api/users/{id}:
 *   put:
 *     tags:
 *       - Users
 *     summary: Update a user
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               email:
 *                 type: string
 *               password:
 *                 type: string
 *               companyId:
 *                 type: integer
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/users/:id', async (req, res) => {
  const { email, password, companyId } = req.body;
  const id = parseInt(req.params.id, 10);
  let newEmail = email;
  let newCompanyId = companyId;
  let newPasswordHash: string | undefined;
  if (
    email === undefined ||
    password === undefined ||
    companyId === undefined
  ) {
    const current = await getUserById(id);
    if (!current) {
      return res.status(404).json({ error: 'User not found' });
    }
    if (newEmail === undefined) {
      newEmail = current.email;
    }
    if (newCompanyId === undefined) {
      newCompanyId = current.company_id;
    }
    if (password === undefined) {
      newPasswordHash = current.password_hash;
    }
  }
  if (password !== undefined) {
    newPasswordHash = await bcrypt.hash(password, 10);
  }
  await updateUser(id, newEmail, newPasswordHash!, newCompanyId);
  res.json({ success: true });
});

/**
 * @openapi
 * /api/users/{id}:
 *   delete:
 *     tags:
 *       - Users
 *     summary: Delete a user
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/users/:id', async (req, res) => {
  await deleteUser(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/licenses:
 *   get:
 *     tags:
 *       - Licenses
 *     summary: List all licenses
 *     responses:
 *       200:
 *         description: Array of licenses
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   company_id:
 *                     type: integer
 *                   name:
 *                     type: string
 *                   platform:
 *                     type: string
 *                   count:
 *                     type: integer
 *                   allocated:
 *                     type: integer
 *                   expiry_date:
 *                     type: string
 *                     format: date
 *                   contract_term:
 *                     type: string
 */
api.get('/licenses', async (_req, res) => {
  const licenses = await getAllLicenses();
  res.json(licenses);
});

/**
 * @openapi
 * /api/licenses:
 *   post:
 *     tags:
 *       - Licenses
 *     summary: Create a license
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - companyId
 *               - name
 *               - platform
 *               - count
 *             properties:
 *               companyId:
 *                 type: integer
 *               name:
 *                 type: string
 *               platform:
 *                 type: string
 *               count:
 *                 type: integer
 *               expiryDate:
 *                 type: string
 *                 format: date
 *               contractTerm:
 *                 type: string
 *     responses:
 *       200:
 *         description: License created
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 */
api.post('/licenses', async (req, res) => {
  const { companyId, name, platform, count, expiryDate, contractTerm } = req.body;
  const id = await createLicense(
    companyId,
    name,
    platform,
    count,
    expiryDate,
    contractTerm
  );
  res.json({ id });
});

/**
 * @openapi
 * /api/licenses/{id}:
 *   get:
 *     tags:
 *       - Licenses
 *     summary: Get a license by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: License details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 company_id:
 *                   type: integer
 *                 name:
 *                   type: string
 *                 platform:
 *                   type: string
 *                 count:
 *                   type: integer
 *                 allocated:
 *                   type: integer
 *                 expiry_date:
 *                   type: string
 *                   format: date
 *                 contract_term:
 *                   type: string
 *       404:
 *         description: License not found
 */
api.get('/licenses/:id', async (req, res) => {
  const license = await getLicenseById(parseInt(req.params.id, 10));
  if (!license) {
    return res.status(404).json({ error: 'License not found' });
  }
  res.json(license);
});

/**
 * @openapi
 * /api/licenses/{id}:
 *   put:
 *     tags:
 *       - Licenses
 *     summary: Update a license
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               companyId:
 *                 type: integer
 *               name:
 *                 type: string
 *               platform:
 *                 type: string
 *               count:
 *                 type: integer
 *               expiryDate:
 *                 type: string
 *                 format: date
 *                 nullable: true
 *               contractTerm:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/licenses/:id', async (req, res) => {
  const { companyId, name, platform, count, expiryDate, contractTerm } = req.body;
  const id = parseInt(req.params.id, 10);
  let newCompanyId = companyId;
  let newName = name;
  let newPlatform = platform;
  let newCount = count;
  let newExpiryDate = expiryDate;
  let newContractTerm = contractTerm;
  if (
    companyId === undefined ||
    name === undefined ||
    platform === undefined ||
    count === undefined ||
    expiryDate === undefined ||
    contractTerm === undefined
  ) {
    const current = await getLicenseById(id);
    if (!current) {
      return res.status(404).json({ error: 'License not found' });
    }
    if (newCompanyId === undefined) newCompanyId = current.company_id;
    if (newName === undefined) newName = current.name;
    if (newPlatform === undefined) newPlatform = current.platform;
    if (newCount === undefined) newCount = current.count;
    if (newExpiryDate === undefined) newExpiryDate = current.expiry_date;
    if (newContractTerm === undefined)
      newContractTerm = current.contract_term;
  }
  await updateLicense(
    id,
    newCompanyId!,
    newName!,
    newPlatform!,
    newCount!,
    newExpiryDate || null,
    newContractTerm!
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/licenses/{id}:
 *   delete:
 *     tags:
 *       - Licenses
 *     summary: Delete a license
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/licenses/:id', async (req, res) => {
  await deleteLicense(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/staff:
 *   get:
 *     tags:
 *       - Staff
 *     summary: List all staff members
 *     parameters:
 *       - in: query
 *         name: accountAction
 *         schema:
 *           type: string
 *         required: false
 *         description: Filter by account action
 *       - in: query
 *         name: email
 *         schema:
 *           type: string
 *         required: false
 *         description: Filter by email address
 *     responses:
 *       200:
 *         description: Array of staff
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   companyId:
 *                     type: integer
 *                   firstName:
 *                     type: string
 *                   lastName:
 *                     type: string
 *                   email:
 *                     type: string
 *                   dateOnboarded:
 *                     type: string
 *                     format: date
 *                   dateOffboarded:
 *                     type: string
 *                     format: date-time
 *                     nullable: true
 *                   enabled:
 *                     type: boolean
 *                   street:
 *                     type: string
 *                     nullable: true
 *                   city:
 *                     type: string
 *                     nullable: true
 *                   state:
 *                     type: string
 *                     nullable: true
 *                   postcode:
 *                     type: string
 *                     nullable: true
 *                   country:
 *                     type: string
 *                     nullable: true
 *                   department:
 *                     type: string
 *                     nullable: true
 *                   jobTitle:
 *                     type: string
 *                     nullable: true
 *                   company:
 *                     type: string
 *                     nullable: true
 *                   managerName:
 *                     type: string
 *                     nullable: true
 *                   accountAction:
 *                     type: string
 *                     nullable: true
 */
api.get('/staff', async (req, res) => {
  const accountAction = req.query.accountAction as string | undefined;
  const email = req.query.email as string | undefined;
  const staff = await getAllStaff(accountAction, email);
  res.json(staff.map(mapStaff));
});

/**
 * @openapi
 * /api/staff:
 *   post:
 *     tags:
 *       - Staff
 *     summary: Create a staff member
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - companyId
 *               - firstName
 *               - lastName
 *               - email
 *             properties:
 *               companyId:
 *                 type: integer
 *               firstName:
 *                 type: string
 *               lastName:
 *                 type: string
 *               email:
 *                 type: string
 *               mobilePhone:
 *                 type: string
 *               dateOnboarded:
 *                 type: string
 *                 format: date
 *               dateOffboarded:
 *                 type: string
 *                 format: date-time
 *               enabled:
 *                 type: boolean
 *               street:
 *                 type: string
 *               city:
 *                 type: string
 *               state:
 *                 type: string
 *               postcode:
 *                 type: string
 *               country:
 *                 type: string
 *               department:
 *                 type: string
 *               jobTitle:
 *                 type: string
 *               company:
 *                 type: string
 *               managerName:
 *                 type: string
 *               accountAction:
 *                 type: string
 *     responses:
 *       200:
 *         description: Staff member created
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 success:
 *                   type: boolean
 */
api.post('/staff', async (req, res) => {
  const {
    companyId,
    firstName,
    lastName,
    email,
    mobilePhone,
    dateOnboarded,
    dateOffboarded,
    enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
    accountAction,
  } = req.body;
  await addStaff(
    companyId,
    firstName,
    lastName,
    email,
    mobilePhone || null,
    toDate(dateOnboarded),
    toDateTime(dateOffboarded),
    !!enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
    accountAction || null,
    null
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/staff/{id}:
 *   get:
 *     tags:
 *       - Staff
 *     summary: Get a staff member by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Staff details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 companyId:
 *                   type: integer
 *                 firstName:
 *                   type: string
 *                 lastName:
 *                   type: string
 *                 email:
 *                   type: string
 *                 mobilePhone:
 *                   type: string
 *                   nullable: true
 *                 dateOnboarded:
 *                   type: string
 *                   format: date
 *                 dateOffboarded:
 *                   type: string
 *                   format: date-time
 *                   nullable: true
 *                 enabled:
 *                   type: boolean
 *                 street:
 *                   type: string
 *                   nullable: true
 *                 city:
 *                   type: string
 *                   nullable: true
 *                 state:
 *                   type: string
 *                   nullable: true
 *                 postcode:
 *                   type: string
 *                   nullable: true
 *                 country:
 *                   type: string
 *                   nullable: true
 *                 department:
 *                   type: string
 *                   nullable: true
 *                 jobTitle:
 *                   type: string
 *                   nullable: true
 *                 company:
 *                   type: string
 *                   nullable: true
 *                 managerName:
 *                   type: string
 *                   nullable: true
 *                 accountAction:
 *                   type: string
 *                   nullable: true
 *                 verificationCode:
 *                   type: string
 *                   nullable: true
 *       404:
 *         description: Staff not found
 */
api.get('/staff/:id', async (req, res) => {
  const staff = await getStaffById(parseInt(req.params.id, 10));
  if (!staff) {
    return res.status(404).json({ error: 'Staff not found' });
  }
  res.json(mapStaff(staff));
});

/**
 * @openapi
 * /api/staff/{id}:
 *   put:
 *     tags:
 *       - Staff
 *     summary: Update a staff member
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               companyId:
 *                 type: integer
 *               firstName:
 *                 type: string
 *               lastName:
 *                 type: string
 *               email:
 *                 type: string
 *               mobilePhone:
 *                 type: string
 *               dateOnboarded:
 *                 type: string
 *                 format: date
 *               dateOffboarded:
 *                 type: string
 *                 format: date-time
 *               enabled:
 *                 type: boolean
 *               street:
 *                 type: string
 *               city:
 *                 type: string
 *               state:
 *                 type: string
 *               postcode:
 *                 type: string
 *               country:
 *                 type: string
 *               department:
 *                 type: string
 *               jobTitle:
 *                 type: string
 *               company:
 *                 type: string
 *               managerName:
 *                 type: string
 *               accountAction:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/staff/:id', async (req, res) => {
  const {
    companyId,
    firstName,
    lastName,
    email,
    mobilePhone,
    dateOnboarded,
    dateOffboarded,
    enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
    accountAction,
  } = req.body;
  const id = parseInt(req.params.id, 10);
  let current: Staff | null = null;
  if (
    [
      companyId,
      firstName,
      lastName,
      email,
      dateOnboarded,
      dateOffboarded,
      enabled,
      street,
      city,
      state,
      postcode,
      country,
      department,
      jobTitle,
      company,
      managerName,
      accountAction,
    ].some((v) => v === undefined)
  ) {
    current = await getStaffById(id);
    if (!current) {
      return res.status(404).json({ error: 'Staff not found' });
    }
  }
  await updateStaff(
    id,
    companyId !== undefined ? companyId : current!.company_id,
    firstName !== undefined ? firstName : current!.first_name,
    lastName !== undefined ? lastName : current!.last_name,
    email !== undefined ? email : current!.email,
    mobilePhone !== undefined ? mobilePhone : current!.mobile_phone || null,
    dateOnboarded !== undefined
      ? toDate(dateOnboarded)
      : current!.date_onboarded,
    dateOffboarded !== undefined
      ? toDateTime(dateOffboarded)
      : current!.date_offboarded ?? null,
    enabled !== undefined ? !!enabled : !!current!.enabled,
    street !== undefined ? street : current!.street ?? null,
    city !== undefined ? city : current!.city ?? null,
    state !== undefined ? state : current!.state ?? null,
    postcode !== undefined ? postcode : current!.postcode ?? null,
    country !== undefined ? country : current!.country ?? null,
    department !== undefined ? department : current!.department ?? null,
    jobTitle !== undefined ? jobTitle : current!.job_title ?? null,
    company !== undefined ? company : current!.org_company ?? null,
    managerName !== undefined ? managerName : current!.manager_name ?? null,
    accountAction !== undefined
      ? accountAction
      : current!.account_action ?? null,
    current?.syncro_contact_id || null
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/staff/{id}:
 *   delete:
 *     tags:
 *       - Staff
 *     summary: Delete a staff member
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/staff/:id', async (req, res) => {
  await deleteStaff(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/companies/{companyId}/users/{userId}:
 *   post:
 *     tags:
 *       - Companies
 *     summary: Assign a user to a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: userId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               canManageLicenses:
 *                 type: boolean
 *               canManageStaff:
 *                 type: boolean
 *               canManageAssets:
 *                 type: boolean
 *               canManageInvoices:
 *                 type: boolean
 *               canOrderLicenses:
 *                 type: boolean
 *               canAccessShop:
 *                 type: boolean
 *               isAdmin:
 *                 type: boolean
 *     responses:
 *       200:
 *         description: Assignment successful
 */
api.post('/companies/:companyId/users/:userId', async (req, res) => {
  const {
    canManageLicenses,
    canManageStaff,
    canManageAssets,
    canManageInvoices,
    isAdmin,
    canOrderLicenses,
    canAccessShop,
  } = req.body;
  await assignUserToCompany(
    parseInt(req.params.userId, 10),
    parseInt(req.params.companyId, 10),
    !!canManageLicenses,
    !!canManageStaff,
    !!canManageAssets,
    !!canManageInvoices,
    !!isAdmin,
    !!canOrderLicenses,
    !!canAccessShop
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/companies/{companyId}/users/{userId}:
 *   delete:
 *     tags:
 *       - Companies
 *     summary: Unassign a user from a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: userId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Unassignment successful
 */
api.delete('/companies/:companyId/users/:userId', async (req, res) => {
  await unassignUserFromCompany(
    parseInt(req.params.userId, 10),
    parseInt(req.params.companyId, 10)
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/licenses/{licenseId}/staff/{staffId}:
 *   post:
 *     tags:
 *       - Licenses
 *     summary: Link a staff member to a license
 *     parameters:
 *       - in: path
 *         name: licenseId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: staffId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Link created
 */
api.post('/licenses/:licenseId/staff/:staffId', async (req, res) => {
  await linkStaffToLicense(
    parseInt(req.params.staffId, 10),
    parseInt(req.params.licenseId, 10)
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/licenses/{licenseId}/staff/{staffId}:
 *   delete:
 *     tags:
 *       - Licenses
 *     summary: Unlink a staff member from a license
 *     parameters:
 *       - in: path
 *         name: licenseId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: staffId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Link removed
 */
api.delete('/licenses/:licenseId/staff/:staffId', async (req, res) => {
  await unlinkStaffFromLicense(
    parseInt(req.params.staffId, 10),
    parseInt(req.params.licenseId, 10)
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/companies/{companyId}/assets:
 *   get:
 *     tags:
 *       - Assets
 *     summary: List assets for a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: List of assets
 *   post:
 *     tags:
 *       - Assets
 *     summary: Add an asset to a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *               type:
 *                 type: string
 *               serialNumber:
 *                 type: string
 *               status:
 *                 type: string
 *     responses:
 *       200:
 *         description: Asset saved
 */
api.get('/companies/:companyId/assets', async (req, res) => {
  const assets = await getAssetsByCompany(parseInt(req.params.companyId, 10));
  res.json(assets);
});

api.post('/companies/:companyId/assets', async (req, res) => {
  const { name, type, serialNumber, status } = req.body;
  await upsertAsset(
    parseInt(req.params.companyId, 10),
    name,
    type,
    serialNumber,
    status
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/assets/{id}:
 *   get:
 *     tags:
 *       - Assets
 *     summary: Get an asset by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Asset details
 *       404:
 *         description: Asset not found
 */
api.get('/assets/:id', async (req, res) => {
  const asset = await getAssetById(parseInt(req.params.id, 10));
  if (!asset) {
    return res.status(404).json({ error: 'Asset not found' });
  }
  res.json(asset);
});

/**
 * @openapi
 * /api/assets/{id}:
 *   put:
 *     tags:
 *       - Assets
 *     summary: Update an asset
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               companyId:
 *                 type: integer
 *               name:
 *                 type: string
 *               type:
 *                 type: string
 *               serialNumber:
 *                 type: string
 *               status:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/assets/:id', async (req, res) => {
  const { companyId, name, type, serialNumber, status } = req.body;
  const id = parseInt(req.params.id, 10);
  let current: Asset | null = null;
  if (
    [companyId, name, type, serialNumber, status].some(
      (v) => v === undefined
    )
  ) {
    current = await getAssetById(id);
    if (!current) {
      return res.status(404).json({ error: 'Asset not found' });
    }
  }
  await updateAsset(
    id,
    companyId !== undefined ? companyId : current!.company_id,
    name !== undefined ? name : current!.name,
    type !== undefined ? type : current!.type,
    serialNumber !== undefined ? serialNumber : current!.serial_number,
    status !== undefined ? status : current!.status
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/assets/{id}:
 *   delete:
 *     tags:
 *       - Assets
 *     summary: Delete an asset
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/assets/:id', async (req, res) => {
  await deleteAsset(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/companies/{companyId}/invoices:
 *   get:
 *     tags:
 *       - Invoices
 *     summary: List invoices for a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: List of invoices
 *   post:
 *     tags:
 *       - Invoices
 *     summary: Add an invoice to a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               invoiceNumber:
 *                 type: string
 *               amount:
 *                 type: number
 *               dueDate:
 *                 type: string
 *                 format: date
 *               status:
 *                 type: string
 *     responses:
 *       200:
 *         description: Invoice saved
 */
api.get('/companies/:companyId/invoices', async (req, res) => {
  const invoices = await getInvoicesByCompany(parseInt(req.params.companyId, 10));
  res.json(invoices);
});

api.post('/companies/:companyId/invoices', async (req, res) => {
  const { invoiceNumber, amount, dueDate, status } = req.body;
  await upsertInvoice(
    parseInt(req.params.companyId, 10),
    invoiceNumber,
    amount,
    dueDate,
    status
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/invoices/{id}:
 *   get:
 *     tags:
 *       - Invoices
 *     summary: Get an invoice by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Invoice details
 *       404:
 *         description: Invoice not found
 */
api.get('/invoices/:id', async (req, res) => {
  const invoice = await getInvoiceById(parseInt(req.params.id, 10));
  if (!invoice) {
    return res.status(404).json({ error: 'Invoice not found' });
  }
  res.json(invoice);
});

/**
 * @openapi
 * /api/invoices/{id}:
 *   put:
 *     tags:
 *       - Invoices
 *     summary: Update an invoice
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               companyId:
 *                 type: integer
 *               invoiceNumber:
 *                 type: string
 *               amount:
 *                 type: number
 *               dueDate:
 *                 type: string
 *                 format: date
 *               status:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/invoices/:id', async (req, res) => {
  const { companyId, invoiceNumber, amount, dueDate, status } = req.body;
  const id = parseInt(req.params.id, 10);
  let current: Invoice | null = null;
  if (
    [companyId, invoiceNumber, amount, dueDate, status].some(
      (v) => v === undefined
    )
  ) {
    current = await getInvoiceById(id);
    if (!current) {
      return res.status(404).json({ error: 'Invoice not found' });
    }
  }
  await updateInvoice(
    id,
    companyId !== undefined ? companyId : current!.company_id,
    invoiceNumber !== undefined ? invoiceNumber : current!.invoice_number,
    amount !== undefined ? amount : current!.amount,
    dueDate !== undefined ? dueDate : current!.due_date,
    status !== undefined ? status : current!.status
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/invoices/{id}:
 *   delete:
 *     tags:
 *       - Invoices
 *     summary: Delete an invoice
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/invoices/:id', async (req, res) => {
  await deleteInvoice(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.use('/api', api);

const port = parseInt(process.env.PORT || '3000', 10);
const host = process.env.HOST || '0.0.0.0';

async function start() {
  await runMigrations();
  app.listen(port, host, () => {
    console.log(`Server running at http://${host}:${port}`);
  });
}

if (require.main === module) {
  start();
}

export { app, api, start };
