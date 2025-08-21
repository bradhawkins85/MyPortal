import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import session from 'express-session';
import request from 'supertest';
import { csrfMiddleware } from '../src/server';

function buildApp(authenticated: boolean) {
  const app = express();
  app.use(express.urlencoded({ extended: true }));
  app.use(
    session({ secret: 'test', resave: false, saveUninitialized: true })
  );
  if (authenticated) {
    app.use((req, _res, next) => {
      req.session.userId = 1;
      next();
    });
  }
  app.use(csrfMiddleware);
  app.get('/token', (req, res) => {
    res.json({ token: res.locals.csrfToken || null });
  });
  app.post('/submit', (req, res) => {
    res.json({ success: true });
  });
  return app;
}

test('authenticated POST requires CSRF token', async () => {
  const app = buildApp(true);
  const agent = request.agent(app);
  const tokenRes = await agent.get('/token');
  const token = tokenRes.body.token;
  assert.ok(token, 'token should be provided');
  await agent
    .post('/submit')
    .type('form')
    .send({ _csrf: token })
    .expect(200);
  await agent.post('/submit').type('form').send({}).expect(403);
});

test('unauthenticated POST skips CSRF check', async () => {
  const app = buildApp(false);
  await request(app).post('/submit').expect(200);
});
