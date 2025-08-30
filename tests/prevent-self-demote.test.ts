import { test } from 'node:test';
import assert from 'node:assert';

function parseCheckbox(value: unknown): boolean {
  if (Array.isArray(value)) {
    value = value[value.length - 1];
  }
  return value === '1' || value === 'on' || value === true;
}

test('user cannot remove own admin permission when session id is string', async () => {
  const calls: any[] = [];
  async function updateUserCompanyPermission(
    uid: number,
    cid: number,
    field: string,
    value: boolean,
  ) {
    calls.push({ uid, cid, field, value });
  }

  const req: any = {
    body: { userId: '2', companyId: '1', isAdmin: '0' },
    session: { userId: '2', companyId: 1 },
  };

  const uid = parseInt(req.body.userId, 10);
  const cid = req.session.companyId;
  const isAdminField = req.body.isAdmin;
  if (typeof isAdminField !== 'undefined') {
    const isAdminValue = parseCheckbox(isAdminField);
    if (
      uid !== Number(req.session.userId) ||
      Number(req.session.userId) === 1 ||
      isAdminValue
    ) {
      await updateUserCompanyPermission(uid, cid, 'is_admin', isAdminValue);
    }
  }

  assert.strictEqual(calls.length, 0);
});
