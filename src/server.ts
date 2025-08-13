import express from 'express';
import session from 'express-session';
import path from 'path';
import bcrypt from 'bcrypt';
import dotenv from 'dotenv';
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
} from './queries';
import { runMigrations } from './db';

dotenv.config();

const app = express();
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

app.use(express.urlencoded({ extended: true }));
app.use(express.static(path.join(__dirname, 'public')));
app.use(
  session({
    secret: process.env.SESSION_SECRET || 'secret',
    resave: false,
    saveUninitialized: false,
  })
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
    await assignUserToCompany(userId, companyId, true);
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
  res.render('business', {
    company,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1,
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
app.get('/licenses', ensureAuth, ensureLicenseAccess, async (req, res) => {
  const licenses = await getLicensesByCompany(req.session.companyId!);
  const companies = await getCompaniesForUser(req.session.userId!);
  res.render('licenses', {
    licenses,
    isAdmin: req.session.userId === 1,
    companies,
    currentCompanyId: req.session.companyId,
  });
});

app.get('/staff', ensureAuth, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const staff = req.session.companyId
    ? await getStaffByCompany(req.session.companyId)
    : [];
  res.render('staff', {
    staff,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: req.session.userId === 1,
  });
});

app.post('/staff', ensureAuth, async (req, res) => {
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

app.post('/staff/enabled', ensureAuth, async (req, res) => {
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
  const companies = await getCompaniesForUser(req.session.userId!);
  res.render('admin', {
    allCompanies,
    users,
    assignments,
    isAdmin: true,
    companies,
    currentCompanyId: req.session.companyId,
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
  await assignUserToCompany(userId, parseInt(companyId, 10), false);
  res.redirect('/admin');
});

app.post('/admin/assign', ensureAuth, ensureAdmin, async (req, res) => {
  const { userId, companyId } = req.body;
  await assignUserToCompany(parseInt(userId, 10), parseInt(companyId, 10), false);
  res.redirect('/admin');
});

app.post('/admin/permission', ensureAuth, ensureAdmin, async (req, res) => {
  const { userId, companyId, canManageLicenses } = req.body;
  await updateUserCompanyPermission(
    parseInt(userId, 10),
    parseInt(companyId, 10),
    !!canManageLicenses
  );
  res.redirect('/admin');
});

const port = parseInt(process.env.PORT || '3000', 10);
const host = process.env.HOST || '0.0.0.0';

async function start() {
  await runMigrations();
  app.listen(port, host, () => {
    console.log(`Server running at http://${host}:${port}`);
  });
}

start();
