import { Client } from '@microsoft/microsoft-graph-client';
import { ConfidentialClientApplication } from '@azure/msal-node';
import 'isomorphic-fetch';
import {
  createLicense,
  updateLicense,
  getLicenseByCompanyAndSku,
  getM365Credentials,
  upsertM365Credentials,
  getStaffByCompanyAndEmail,
  linkStaffToLicense,
  getStaffForLicense,
  unlinkStaffFromLicense,
} from '../queries';
import { decryptSecret, encryptSecret } from '../crypto';
import { logInfo, logError } from '../logger';
import { m365SkuNameMap } from '../data/m365SkuMap';
async function createCca(companyId: number): Promise<ConfidentialClientApplication> {
  const creds = await getM365Credentials(companyId);
  if (!creds) {
    throw new Error('Missing Azure AD credentials');
  }
  return new ConfidentialClientApplication({
    auth: {
      clientId: creds.client_id,
      authority: `https://login.microsoftonline.com/${creds.tenant_id}`,
      clientSecret: decryptSecret(creds.client_secret),
    },
  });
}

async function getClient(companyId: number): Promise<Client> {
  const app = await createCca(companyId);
  let token = '';
  const creds = await getM365Credentials(companyId);
  try {
    if (creds?.refresh_token) {
      const result: any = await app.acquireTokenByRefreshToken({
        refreshToken: decryptSecret(creds.refresh_token),
        scopes: ['https://graph.microsoft.com/.default'],
      });
      token = result?.accessToken || '';
      await upsertM365Credentials(
        companyId,
        creds.tenant_id,
        creds.client_id,
        creds.client_secret,
        result?.refreshToken
          ? encryptSecret(result.refreshToken)
          : creds.refresh_token,
        result?.accessToken ? encryptSecret(result.accessToken) : null,
        result?.expiresOn
          ? result.expiresOn.toISOString().slice(0, 19).replace('T', ' ')
          : null
      );
    } else {
      const result: any = await app.acquireTokenByClientCredential({
        scopes: ['https://graph.microsoft.com/.default'],
      });
      token = result?.accessToken || '';
    }
  } catch (err) {
    logError('Failed to acquire Microsoft 365 token', { err });
    throw err;
  }
  return Client.init({
    authProvider: (done) => {
      done(null, token);
    },
  });
}

export async function syncM365Licenses(companyId: number): Promise<void> {
  try {
    const client = await getClient(companyId);
    const skus = await client.api('/subscribedSkus').get();
    if (skus.value && Array.isArray(skus.value)) {
      for (const sku of skus.value) {
        const partNumber = sku.skuPartNumber as string;
        const skuId = sku.skuId as string;
        const count = sku.prepaidUnits?.enabled || 0;
        const name =
          m365SkuNameMap[partNumber as keyof typeof m365SkuNameMap] ??
          partNumber;
        const existing = await getLicenseByCompanyAndSku(companyId, partNumber);
        let licenseId: number;
        if (existing) {
          await updateLicense(
            existing.id,
            companyId,
            name,
            partNumber,
            count,
            existing.expiry_date,
            existing.contract_term
          );
          licenseId = existing.id;
        } else {
          licenseId = await createLicense(
            companyId,
            name,
            partNumber,
            count,
            null,
            ''
          );
        }

        const users = await client
          .api(`/subscribedSkus/${skuId}/users`)
          .select(['id', 'userPrincipalName', 'mail'])
          .get();
        const assignedEmails: string[] = [];
        if (users.value && Array.isArray(users.value)) {
          for (const u of users.value) {
            const email = String(u.mail || u.userPrincipalName || '').toLowerCase();
            if (!email) continue;
            const staff = await getStaffByCompanyAndEmail(companyId, email);
            if (staff) {
              assignedEmails.push(staff.email.toLowerCase());
              await linkStaffToLicense(staff.id, licenseId);
            }
          }
        }
        const current = await getStaffForLicense(licenseId);
        for (const s of current) {
          if (!assignedEmails.includes(s.email.toLowerCase())) {
            await unlinkStaffFromLicense(s.id, licenseId);
          }
        }
      }
    }
    logInfo(`Synced Microsoft 365 licenses for company ${companyId}`);
  } catch (err) {
    logError('Failed to sync Microsoft 365 licenses', { err });
  }
}

export async function getUserLicenseDetails(
  companyId: number,
  userId: string
) {
  const client = await getClient(companyId);
  return client.api(`/users/${userId}/licenseDetails`).get();
}
