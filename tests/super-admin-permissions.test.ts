import { test } from 'node:test';
import express from 'express';
import session from 'express-session';
import request from 'supertest';

process.env.TOTP_ENCRYPTION_KEY = 'test';
process.env.SESSION_SECRET = 'test';
// Import after setting env variables
const { ensureAdmin } = require('../src/server');

test('super admin string id can access admin routes', async () => {
  const app = express();
  app.use(session({ secret: 'test', resave: false, saveUninitialized: true }));
  app.use((req, _res, next) => {
    // simulate userId stored as string in session
    req.session.userId = '1' as any;
    next();
  });
  app.get('/secure', ensureAdmin, (_req, res) => {
    res.json({ ok: true });
  });
  await request(app).get('/secure').expect(200);
});
