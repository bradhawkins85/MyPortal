import express from 'express';
import session from 'express-session';
import path from 'path';
import bcrypt from 'bcrypt';
import dotenv from 'dotenv';
import { getUserByEmail, getCompanyById, getLicensesByCompany } from './queries';

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

app.get('/login', (req, res) => {
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

const port = process.env.PORT || 3000;
app.listen(port, () => {
  console.log(`Server running on port ${port}`);
});
