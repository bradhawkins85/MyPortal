import { Client } from '@microsoft/microsoft-graph-client';
import { ConfidentialClientApplication } from '@azure/msal-node';
import 'isomorphic-fetch';
import {
  createLicense,
  updateLicense,
  getLicenseByCompanyAndSku,
} from '../queries';
import { logInfo, logError } from '../logger';

const tenantId = process.env.AZURE_AD_TENANT_ID;
const clientId = process.env.AZURE_AD_CLIENT_ID;
const clientSecret = process.env.AZURE_AD_CLIENT_SECRET;

function createCca(): ConfidentialClientApplication | null {
  if (!tenantId || !clientId || !clientSecret) {
    return null;
  }
  return new ConfidentialClientApplication({
    auth: {
      clientId,
      authority: `https://login.microsoftonline.com/${tenantId}`,
      clientSecret,
    },
  });
}

async function getClient(): Promise<Client> {
  const app = createCca();
  if (!app) {
    throw new Error('Missing Azure AD credentials');
  }
  const result = await app.acquireTokenByClientCredential({
    scopes: ['https://graph.microsoft.com/.default'],
  });
  const token = result && result.accessToken ? result.accessToken : '';
  return Client.init({
    authProvider: (done) => {
      done(null, token);
    },
  });
}

export async function syncM365Licenses(companyId: number): Promise<void> {
  try {
    if (!tenantId || !clientId || !clientSecret) {
      logError('Missing Azure AD credentials for Microsoft 365 sync');
      return;
    }
    const client = await getClient();
    const skus = await client.api('/subscribedSkus').get();
    if (skus.value && Array.isArray(skus.value)) {
      for (const sku of skus.value) {
        const partNumber = sku.skuPartNumber as string;
        const count = sku.prepaidUnits?.enabled || 0;
        const existing = await getLicenseByCompanyAndSku(companyId, partNumber);
        if (existing) {
          await updateLicense(
            existing.id,
            companyId,
            existing.name,
            partNumber,
            count,
            existing.expiry_date,
            existing.contract_term
          );
        } else {
          await createLicense(companyId, partNumber, partNumber, count, null, '');
        }
      }
    }
    logInfo(`Synced Microsoft 365 licenses for company ${companyId}`);
  } catch (err) {
    logError('Failed to sync Microsoft 365 licenses', { err });
  }
}

export async function getUserLicenseDetails(userId: string) {
  const client = await getClient();
  return client.api(`/users/${userId}/licenseDetails`).get();
}
