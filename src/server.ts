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
} from './queries';

dotenv.config();

const app = express();
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

app.use(express.urlencoded({ extended: true }));
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
    req.session.companyId = user.company_id;
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
  const company = await getCompanyById(req.session.companyId!);
  res.render('business', { company });
});

app.get('/licenses', ensureAuth, async (req, res) => {
  const licenses = await getLicensesByCompany(req.session.companyId!);
  res.render('licenses', { licenses });
});

const port = parseInt(process.env.PORT || '3000', 10);
const host = process.env.HOST || '0.0.0.0';

app.listen(port, host, () => {
  console.log(`Server running at http://${host}:${port}`);
});
