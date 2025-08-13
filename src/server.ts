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
  getUserCount,
  createCompany,
  createUser,
  getCompaniesForUser,
  getAllCompanies,
  getAllUsers,
  assignUserToCompany,
  getUserCompanyAssignments,
  updateUserCompanyPermission,
  getStaffByCompany,
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
  getApiKeys,
  createApiKey,
  deleteApiKey,
  getApiKeyRecord,
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

const swaggerSpec = swaggerJSDoc({
  definition: {
    openapi: '3.0.0',
    info: {
      title: 'MyPortal API',
      version: '1.0.0',
    },
    tags: [
      { name: 'Companies' },
      { name: 'Users' },
      { name: 'Licenses' },
      { name: 'Staff' },
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
  '/api-docs',
  ensureAuth,
  ensureAdmin,
  swaggerUi.serve,
  swaggerUi.setup(swaggerSpec)
);

function ensureAuth(req: express.Request, res: express.Response, next: express.NextFunction) {
  if (!req.session.userId) {
    return res.redirect('/login');
  }
  next();
}

function ensureAdmin(req: express.Request, res: express.Response, next: express.NextFunction) {
  if (req.session.userId !== 1) {
    return res.redirect('/');
  }
  next();
}

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
    req.session.userId = user.id;
    const companies = await getCompaniesForUser(user.id);
    req.session.companyId = companies[0]?.company_id;
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
    await assignUserToCompany(userId, companyId, true, true);
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
  res.render('business', {
    company,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
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
app.get('/licenses', ensureAuth, ensureLicenseAccess, async (req, res) => {
  const licenses = await getLicensesByCompany(req.session.companyId!);
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('licenses', {
    licenses,
    isAdmin: req.session.userId === 1,
    companies,
    currentCompanyId: req.session.companyId,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
  });
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
    isAdmin: req.session.userId === 1,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
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

app.post('/switch-company', ensureAuth, async (req, res) => {
  const { companyId } = req.body;
  const companies = await getCompaniesForUser(req.session.userId!);
  if (companies.some((c) => c.company_id === parseInt(companyId, 10))) {
    req.session.companyId = parseInt(companyId, 10);
  }
  res.redirect('/');
});

app.get('/admin', ensureAuth, ensureAdmin, async (req, res) => {
  const allCompanies = await getAllCompanies();
  const users = await getAllUsers();
  const assignments = await getUserCompanyAssignments();
  const apiKeys = await getApiKeys();
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('admin', {
    allCompanies,
    users,
    assignments,
    apiKeys,
    isAdmin: true,
    companies,
    currentCompanyId: req.session.companyId,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.can_manage_staff ?? 0,
  });
});

app.post('/admin/company', ensureAuth, ensureAdmin, async (req, res) => {
  const { name } = req.body;
  await createCompany(name);
  res.redirect('/admin');
});

app.post('/admin/user', ensureAuth, ensureAdmin, async (req, res) => {
  const { email, password, companyId } = req.body;
  const passwordHash = await bcrypt.hash(password, 10);
  const userId = await createUser(email, passwordHash, parseInt(companyId, 10));
  await assignUserToCompany(userId, parseInt(companyId, 10), false, false);
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
  const { userId, companyId } = req.body;
  await assignUserToCompany(
    parseInt(userId, 10),
    parseInt(companyId, 10),
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
  const { userId, companyId, canManageLicenses, canManageStaff } = req.body;
  const uid = parseInt(userId, 10);
  const cid = parseInt(companyId, 10);
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
 *     responses:
 *       200:
 *         description: Assignment successful
 */
api.post('/companies/:companyId/users/:userId', async (req, res) => {
  const { canManageLicenses, canManageStaff } = req.body;
  await assignUserToCompany(
    parseInt(req.params.userId, 10),
    parseInt(req.params.companyId, 10),
    !!canManageLicenses,
    !!canManageStaff
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
