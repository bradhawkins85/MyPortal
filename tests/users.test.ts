import { test } from 'node:test';
import supertest from 'supertest';

test('POST /api/users missing email returns 400', { concurrency: false }, async (t) => {
  process.env.NODE_ENV = 'test';
  const app = (await import('../src/server')).default;
  await supertest(app)
    .post('/api/users')
    .set('x-api-key', 'test')
    .send({ password: 'pw', companyId: 1 })
    .expect(400);
});

test('POST /api/users missing password returns 400', { concurrency: false }, async (t) => {
  process.env.NODE_ENV = 'test';
  const app = (await import('../src/server')).default;
  await supertest(app)
    .post('/api/users')
    .set('x-api-key', 'test')
    .send({ email: 'a@b.com', companyId: 1 })
    .expect(400);
});

test('POST /api/users missing companyId returns 400', { concurrency: false }, async (t) => {
  process.env.NODE_ENV = 'test';
  const app = (await import('../src/server')).default;
  await supertest(app)
    .post('/api/users')
    .set('x-api-key', 'test')
    .send({ email: 'a@b.com', password: 'pw' })
    .expect(400);
});

test('PUT /api/users/:id missing email returns 400', { concurrency: false }, async (t) => {
  process.env.NODE_ENV = 'test';
  const app = (await import('../src/server')).default;
  await supertest(app)
    .put('/api/users/1')
    .set('x-api-key', 'test')
    .send({ password: 'pw', companyId: 1 })
    .expect(400);
});

test('PUT /api/users/:id missing password returns 400', { concurrency: false }, async (t) => {
  process.env.NODE_ENV = 'test';
  const app = (await import('../src/server')).default;
  await supertest(app)
    .put('/api/users/1')
    .set('x-api-key', 'test')
    .send({ email: 'a@b.com', companyId: 1 })
    .expect(400);
});

test('PUT /api/users/:id missing companyId returns 400', { concurrency: false }, async (t) => {
  process.env.NODE_ENV = 'test';
  const app = (await import('../src/server')).default;
  await supertest(app)
    .put('/api/users/1')
    .set('x-api-key', 'test')
    .send({ email: 'a@b.com', password: 'pw' })
    .expect(400);
});
