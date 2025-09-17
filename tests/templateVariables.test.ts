import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  TEMPLATE_VARIABLES,
  applyTemplateVariables,
  buildTemplateReplacementMap,
} from '../src/services/templateVariables';

test('buildTemplateReplacementMap returns raw and encoded values', () => {
  const replacements = buildTemplateReplacementMap({
    user: {
      id: 5,
      email: 'user@example.com',
      firstName: 'Ada',
      lastName: 'Lovelace',
    },
    company: {
      id: 42,
      name: 'Example Co',
      syncroCustomerId: 'SYNC-123',
    },
    portal: {
      baseUrl: 'https://portal.example.com',
      loginUrl: 'https://portal.example.com/login',
    },
  });

  assert.equal(replacements['{{user.email}}'], 'user@example.com');
  assert.equal(replacements['{{user.email}}UrlEncoded'], 'user%40example.com');
  assert.equal(replacements['{{user.fullName}}'], 'Ada Lovelace');
  assert.equal(replacements['{{company.id}}'], '42');
  assert.equal(replacements['{{company.syncroId}}'], 'SYNC-123');
});

test('applyTemplateVariables replaces tokens in a string', () => {
  const replacements = {
    '{{user.email}}': 'user@example.com',
    '{{user.email}}UrlEncoded': 'user%40example.com',
  };
  const url = 'mailto:{{user.email}}?to={{user.email}}UrlEncoded';
  const rendered = applyTemplateVariables(url, replacements);
  assert.equal(rendered, 'mailto:user@example.com?to=user%40example.com');
});

test('missing values result in empty strings without undefined leakage', () => {
  const replacements = buildTemplateReplacementMap({});
  for (const variable of TEMPLATE_VARIABLES) {
    const token = `{{${variable.key}}}`;
    assert.equal(replacements[token], '');
    assert.equal(replacements[`${token}UrlEncoded`], '');
  }
});
