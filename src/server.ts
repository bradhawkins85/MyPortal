import express from 'express';
import session from 'express-session';
import path from 'path';
import bcrypt from 'bcrypt';
import dotenv from 'dotenv';
import crypto from 'crypto';
import swaggerUi from 'swagger-ui-express';
import swaggerJSDoc from 'swagger-jsdoc';
import {
  getUserByEmail,
  getCompanyById,
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
  createLicense,
  updateCompany,
  deleteCompany,
  updateUser,
  deleteUser,
  updateLicense,
  deleteLicense,
  unassignUserFromCompany,
  linkStaffToLicense,
  unlinkStaffFromLicense,
  getStaffForLicense,
  getApiKeys,
  createApiKey,
  deleteApiKey,
  getApiKeyRecord,
  getAssetsByCompany,
  getAssetById,
  updateAsset,
  deleteAsset,
  getInvoicesByCompany,
  getInvoiceById,
  updateInvoice,
  deleteInvoice,
  getAllProducts,
  createProduct,
  getProductById,
  getProductBySku,
  updateProduct,
  deleteProduct,
  createOrder,
  getOrdersByCompany,
  upsertAsset,
  upsertInvoice,
  getExternalApiSettings,
  upsertExternalApiSettings,
  getAllApps,
  createApp,
  getAppById,
  updateApp,
  deleteApp,
  getAppPrice,
  getCompanyAppPrices,
  deleteCompanyAppPrice,
  upsertCompanyAppPrice,
  Company,
  User,
  UserCompany,
  ApiKey,
  App,
} from './queries';
import { runMigrations } from './db';

dotenv.config();

const app = express();
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));
app.use(
  session({
    secret: process.env.SESSION_SECRET || 'secret',
    resave: false,
    saveUninitialized: false,
  })
);

app.use((req, res, next) => {
  res.locals.isSuperAdmin = req.session.userId === 1;
  res.locals.cart = req.session.cart || [];
  next();
});

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
  const { email, password, remember } = req.body;
  const user = await getUserByEmail(email);
  if (user && (await bcrypt.compare(password, user.password_hash))) {
    req.session.userId = user.id;
    const companies = await getCompaniesForUser(user.id);
    req.session.companyId = companies[0]?.company_id;
    if (remember === 'on') {
      req.session.cookie.maxAge = 8 * 60 * 60 * 1000; // 8 hours
    } else {
      req.session.cookie.expires = undefined;
      req.session.cookie.maxAge = undefined;
    }
    res.redirect('/');
  } else {
    res.render('login', { error: 'Invalid credentials' });
  }
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
    res.redirect('/');
  } catch (err) {
    res.render('register', { error: 'Registration failed' });
  }
});

app.get('/logout', (req, res) => {
  req.session.destroy(() => {
    res.redirect('/login');
  });
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
    const staff = await getStaffByCompany(company.id);
    activeUsers = staff.filter((s) => s.enabled === 1).length;

    const licenses = await getLicensesByCompany(company.id);
    licenseStats = licenses.map((l) => {
      const used = l.allocated || 0;
      return { name: l.name, count: l.count, used, unused: l.count - used };
    });

    const assets = await getAssetsByCompany(company.id);
    assetCount = assets.length;

    const invoices = await getInvoicesByCompany(company.id);
    paidInvoices = invoices.filter((i) => i.status.toLowerCase() === 'paid').length;
    unpaidInvoices = invoices.length - paidInvoices;
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
  const settings = await getExternalApiSettings(req.session.companyId!);
  if (settings?.webhook_url && settings.webhook_api_key) {
    try {
      await fetch(settings.webhook_url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': settings.webhook_api_key,
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
  const settings = await getExternalApiSettings(req.session.companyId!);
  if (settings?.webhook_url && settings.webhook_api_key) {
    try {
      await fetch(settings.webhook_url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': settings.webhook_api_key,
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

app.get('/staff', ensureAuth, ensureStaffAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const staff = req.session.companyId
    ? await getStaffByCompany(req.session.companyId)
    : [];
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('staff', {
    staff,
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

app.post('/staff', ensureAuth, ensureStaffAccess, async (req, res) => {
  const { firstName, lastName, email, dateOnboarded, enabled } = req.body;
  if (req.session.companyId) {
    await addStaff(
      req.session.companyId,
      firstName,
      lastName,
      email,
      dateOnboarded,
      !!enabled
    );
  }
  res.redirect('/staff');
});

app.post('/staff/enabled', ensureAuth, ensureStaffAccess, async (req, res) => {
  const { staffId, enabled } = req.body;
  await updateStaffEnabled(parseInt(staffId, 10), !!enabled);
  res.redirect('/staff');
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

app.get('/shop', ensureAuth, ensureShopAccess, async (req, res) => {
  const products = await getAllProducts();
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('shop', {
    products,
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
  const product = await getProductById(parseInt(productId, 10));
  if (product) {
    if (!req.session.cart) {
      req.session.cart = [];
    }
    const existing = req.session.cart.find((i) => i.productId === product.id);
    if (existing) {
      existing.quantity += parseInt(quantity, 10);
    } else {
      req.session.cart.push({
        productId: product.id,
        name: product.name,
        quantity: parseInt(quantity, 10),
      });
    }
  }
  res.redirect('/shop');
});

app.get('/cart', ensureAuth, ensureShopAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const message = req.session.orderMessage;
  req.session.orderMessage = undefined;
  res.render('cart', {
    cart: req.session.cart || [],
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

app.post('/cart/place-order', ensureAuth, ensureShopAccess, async (req, res) => {
  if (req.session.companyId && req.session.cart && req.session.cart.length > 0) {
    const settings = await getExternalApiSettings(req.session.companyId);
    if (settings?.webhook_url && settings?.webhook_api_key) {
      try {
        await fetch(settings.webhook_url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-api-key': settings.webhook_api_key,
          },
          body: JSON.stringify({ cart: req.session.cart }),
        });
      } catch (err) {
        console.error('Failed to call webhook', err);
      }
    }
    for (const item of req.session.cart) {
      await createOrder(
        req.session.userId!,
        req.session.companyId,
        item.productId,
        item.quantity
      );
    }
    req.session.cart = [];
    req.session.orderMessage = 'Your order is being processed.';
  }
  res.redirect('/cart');
});

app.get('/orders', ensureAuth, ensureShopAccess, async (req, res) => {
  const orders = req.session.companyId
    ? await getOrdersByCompany(req.session.companyId)
    : [];
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('orders', {
    orders,
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

app.get('/shop/admin', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const products = await getAllProducts();
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('shop-admin', {
    products,
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

app.post('/shop/admin/product', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { name, sku, description, price, stock } = req.body;
  await createProduct(name, sku, description, parseFloat(price), parseInt(stock, 10));
  res.redirect('/shop/admin');
});

app.post('/shop/admin/product/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { name, sku, description, price, stock } = req.body;
  await updateProduct(
    parseInt(req.params.id, 10),
    name,
    sku,
    description,
    parseFloat(price),
    parseInt(stock, 10)
  );
  res.redirect('/shop/admin');
});

app.post('/shop/admin/product/:id/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteProduct(parseInt(req.params.id, 10));
  res.redirect('/shop/admin');
});

app.post('/switch-company', ensureAuth, async (req, res) => {
  const { companyId } = req.body;
  const companies = await getCompaniesForUser(req.session.userId!);
  if (companies.some((c) => c.company_id === parseInt(companyId, 10))) {
    req.session.companyId = parseInt(companyId, 10);
  }
  res.redirect('/');
});

app.get('/external-apis', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const settings = req.session.companyId
    ? await getExternalApiSettings(req.session.companyId)
    : null;
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('external-apis', {
    settings,
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

app.post('/external-apis', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const {
    xeroEndpoint,
    xeroApiKey,
    syncroEndpoint,
    syncroApiKey,
    webhookUrl,
    webhookApiKey,
  } = req.body;
  if (req.session.companyId) {
    await upsertExternalApiSettings(
      req.session.companyId,
      xeroEndpoint,
      xeroApiKey,
      syncroEndpoint,
      syncroApiKey,
      webhookUrl,
      webhookApiKey
    );
  }
  res.redirect('/external-apis');
});

app.get('/apps', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const apps = await getAllApps();
  const companiesForUser = await getCompaniesForUser(req.session.userId!);
  const current = companiesForUser.find(
    (c) => c.company_id === req.session.companyId
  );
  const allCompanies = await getAllCompanies();
  const companyPrices = await getCompanyAppPrices();
  res.render('apps', {
    apps,
    companies: companiesForUser,
    allCompanies,
     companyPrices,
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

app.post('/apps', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { sku, name, price, contractTerm } = req.body;
  await createApp(sku, name, parseFloat(price), contractTerm);
  res.redirect('/apps');
});

app.post('/apps/price', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { companyId, appId, price } = req.body;
  await upsertCompanyAppPrice(
    parseInt(companyId, 10),
    parseInt(appId, 10),
    parseFloat(price)
  );
  res.redirect('/apps');
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

app.get('/admin', ensureAuth, ensureAdmin, async (req, res) => {
  const isSuperAdmin = req.session.userId === 1;
  let allCompanies: Company[] = [];
  let users: User[] = [];
  let assignments: UserCompany[] = [];
  let apiKeys: ApiKey[] = [];
  if (isSuperAdmin) {
    allCompanies = await getAllCompanies();
    users = await getAllUsers();
    assignments = await getUserCompanyAssignments();
    apiKeys = await getApiKeys();
  } else {
    const companyId = req.session.companyId!;
    const company = await getCompanyById(companyId);
    allCompanies = company ? [company] : [];
    users = [];
    assignments = await getUserCompanyAssignments(companyId);
  }
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('admin', {
    allCompanies,
    users,
    assignments,
    apiKeys,
    isAdmin: true,
    isSuperAdmin,
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

app.post('/admin/company', ensureAuth, ensureAdmin, async (req, res) => {
  const { name } = req.body;
  await createCompany(name);
  res.redirect('/admin');
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
    await updateUserCompanyPermission(
      uid,
      cid,
      'is_admin',
      parseCheckbox(isAdminField)
    );
  }
  res.redirect('/admin');
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
 *                   description:
 *                     type: string
 *                   price:
 *                     type: number
 *                   stock:
 *                     type: integer
 *   post:
 *     tags:
 *       - Shop
 *     summary: Create a product
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *               sku:
 *                 type: string
 *               description:
 *                 type: string
 *               price:
 *                 type: number
 *               stock:
 *                 type: integer
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
    const products = await getAllProducts();
    res.json(products);
  })
  .post(async (req, res) => {
    const { name, sku, description, price, stock } = req.body;
    const id = await createProduct(
      name,
      sku,
      description,
      parseFloat(price),
      parseInt(stock, 10)
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
 *                 description:
 *                   type: string
 *                 price:
 *                   type: number
 *                 stock:
 *                   type: integer
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
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *               sku:
 *                 type: string
 *               description:
 *                 type: string
 *               price:
 *                 type: number
 *               stock:
 *                 type: integer
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
    const product = await getProductById(parseInt(req.params.id, 10));
    if (!product) {
      return res.status(404).json({ error: 'Product not found' });
    }
    res.json(product);
  })
  .put(async (req, res) => {
    const { name, sku, description, price, stock } = req.body;
    await updateProduct(
      parseInt(req.params.id, 10),
      name,
      sku,
      description,
      parseFloat(price),
      parseInt(stock, 10)
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
 *                 description:
 *                   type: string
 *                 price:
 *                   type: number
 *                 stock:
 *                   type: integer
 *       404:
 *         description: Product not found
 */
api.get('/shop/products/sku/:sku', async (req, res) => {
  const product = await getProductBySku(req.params.sku);
  if (!product) {
    return res.status(404).json({ error: 'Product not found' });
  }
  res.json(product);
});

api.route('/shop/orders')
  .get(async (req, res) => {
    const companyId = req.query.companyId;
    if (!companyId) {
      return res.status(400).json({ error: 'companyId required' });
    }
    const orders = await getOrdersByCompany(parseInt(companyId as string, 10));
    res.json(orders);
  })
  .post(async (req, res) => {
    const { userId, companyId, productId, quantity } = req.body;
    await createOrder(
      parseInt(userId, 10),
      parseInt(companyId, 10),
      parseInt(productId, 10),
      parseInt(quantity, 10)
    );
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
  const { name, address } = req.body;
  const id = await createCompany(name, address);
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
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/companies/:id', async (req, res) => {
  const { name, address } = req.body;
  await updateCompany(parseInt(req.params.id, 10), name, address);
  res.json({ success: true });
});

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
  const passwordHash = await bcrypt.hash(password, 10);
  await updateUser(parseInt(req.params.id, 10), email, passwordHash, companyId);
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
 *               contractTerm:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/licenses/:id', async (req, res) => {
  const { companyId, name, platform, count, expiryDate, contractTerm } = req.body;
  await updateLicense(
    parseInt(req.params.id, 10),
    companyId,
    name,
    platform,
    count,
    expiryDate,
    contractTerm
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
 *                   company_id:
 *                     type: integer
 *                   first_name:
 *                     type: string
 *                   last_name:
 *                     type: string
 *                   email:
 *                     type: string
 *                   date_onboarded:
 *                     type: string
 *                     format: date
 *                   enabled:
 *                     type: integer
 */
api.get('/staff', async (_req, res) => {
  const staff = await getAllStaff();
  res.json(staff);
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
 *               dateOnboarded:
 *                 type: string
 *                 format: date
 *               enabled:
 *                 type: boolean
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
  const { companyId, firstName, lastName, email, dateOnboarded, enabled } = req.body;
  await addStaff(
    companyId,
    firstName,
    lastName,
    email,
    dateOnboarded,
    !!enabled
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
 *                 company_id:
 *                   type: integer
 *                 first_name:
 *                   type: string
 *                 last_name:
 *                   type: string
 *                 email:
 *                   type: string
 *                 date_onboarded:
 *                   type: string
 *                   format: date
 *                 enabled:
 *                   type: integer
 *       404:
 *         description: Staff not found
 */
api.get('/staff/:id', async (req, res) => {
  const staff = await getStaffById(parseInt(req.params.id, 10));
  if (!staff) {
    return res.status(404).json({ error: 'Staff not found' });
  }
  res.json(staff);
});

/**
 * @openapi
 * /api/staff/{id}:
 *   put:
 *     tags:
 *       - Staff
 *     summary: Update a staff member
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
 *               dateOnboarded:
 *                 type: string
 *                 format: date
 *               enabled:
 *                 type: boolean
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/staff/:id', async (req, res) => {
  const { companyId, firstName, lastName, email, dateOnboarded, enabled } = req.body;
  await updateStaff(
    parseInt(req.params.id, 10),
    companyId,
    firstName,
    lastName,
    email,
    dateOnboarded,
    !!enabled
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
  await updateAsset(
    parseInt(req.params.id, 10),
    companyId,
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
  await updateInvoice(
    parseInt(req.params.id, 10),
    companyId,
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

start();
