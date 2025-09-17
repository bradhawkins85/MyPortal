import express, { Request, Response } from 'express';
import session from 'express-session';
import RedisStore from 'connect-redis';
import { createClient } from 'redis';
import path from 'path';
import fs from 'fs';
import { XMLParser } from 'fast-xml-parser';
import bcrypt from 'bcrypt';
import dotenv from 'dotenv';
import crypto from 'crypto';
import { encryptSecret, decryptSecret } from './crypto';
import cookieParser from 'cookie-parser';
import csurf from 'csurf';
import { authenticator } from 'otplib';
import QRCode from 'qrcode';
import swaggerUi from 'swagger-ui-express';
import swaggerJSDoc from 'swagger-jsdoc';
import multer from 'multer';
import nodemailer from 'nodemailer';
import { ConfidentialClientApplication } from '@azure/msal-node';
import { Client } from '@microsoft/microsoft-graph-client';
import 'isomorphic-fetch';
import cron from 'node-cron';
import { getRandomDailyCron } from './cron';
import { execFile } from 'child_process';
import util from 'util';
import rateLimit from 'express-rate-limit';
import helmet from 'helmet';
import {
  getSyncroCustomers,
  getSyncroCustomer,
  getSyncroContacts,
  getSyncroAssets,
  extractAssetDetails,
} from './syncro';
import { findExistingStaff } from './staff-import';
import { syncM365Licenses } from './services/m365Licenses';
import {
  TEMPLATE_VARIABLES,
  applyTemplateVariables,
  buildTemplateReplacementMap,
} from './services/templateVariables';
import {
  getUserByEmail,
  getCompanyById,
  getCompanyBySyncroId,
  getLicensesByCompany,
  getAllLicenses,
  getLicenseById,
  getUserCount,
  createCompany,
  createUser,
  getCompaniesForUser,
  getAllCompanies,
  getAllUsers,
  getUserById,
  assignUserToCompany,
  getUserCompanyAssignments,
  updateUserCompanyPermission,
  updateUserCompanyStaffPermission,
  getStaffByCompany,
  getAllStaff,
  getStaffById,
  addStaff,
  updateStaffEnabled,
  updateStaff,
  deleteStaff,
  setStaffVerificationCode,
  purgeExpiredVerificationCodes,
  getVerificationByCode,
  createLicense,
  updateCompany,
  deleteCompany,
  updateUser,
  deleteUser,
  updateUserPassword,
  updateUserName,
  updateUserMobile,
  setUserForcePasswordChange,
  createPasswordToken,
  getUserIdByPasswordToken,
  markPasswordTokenUsed,
  getUserTotpAuthenticators,
  addUserTotpAuthenticator,
  deleteUserTotpAuthenticator,
  updateLicense,
  deleteLicense,
  unassignUserFromCompany,
  linkStaffToLicense,
  unlinkStaffFromLicense,
  getStaffForLicense,
  getApiKeysWithUsage,
  createApiKey,
  deleteApiKey,
  getApiKeyRecord,
  recordApiKeyUsage,
  hashExistingApiKeys,
  encryptExistingTotpSecrets,
  logAudit,
  getAuditLogs,
  getAssetsByCompany,
  getAssetById,
  updateAsset,
  deleteAsset,
  getInvoicesByCompany,
  getInvoiceById,
  updateInvoice,
  deleteInvoice,
  getM365Credentials,
  upsertM365Credentials,
  deleteM365Credentials,
  getAllForms,
  getFormsByCompany,
  createForm,
  updateForm,
  deleteForm,
  getFormsForUser,
  getFormPermissions,
  updateFormPermissions,
  getAllFormPermissionEntries,
  deleteFormPermission,
  Form,
  getAllCategories,
  getCategoryById,
  getCategoryByName,
  createCategory,
  updateCategory,
  deleteCategory,
  getAllProducts,
  createProduct,
  getProductById,
  getProductBySku,
  getActiveProductPriceAlertByProductId,
  createProductPriceAlert,
  markProductPriceAlertEmailed,
  resolveProductPriceAlerts,
  getActiveProductPriceAlerts,
  updateProduct,
  upsertProductFromFeed,
  clearStockFeed,
  insertStockFeedItem,
  getStockFeedItemBySku,
  archiveProduct,
  unarchiveProduct,
  deleteProduct,
  getProductCompanyRestrictions,
  setProductCompanyExclusions,
  getOfficeGroupsByCompany,
  createOfficeGroup,
  updateOfficeGroupMembers,
  deleteOfficeGroup,
  createOrder,
  getOrdersByCompany,
  getOrderSummariesByCompany,
  getOrderItems,
  deleteOrder,
  updateOrder,
  updateOrderShipping,
  getOrdersByConsignmentId,
  updateShippingStatusByConsignmentId,
  getSmsSubscriptionsForUser,
  setSmsSubscription,
  getSmsSubscribersByOrder,
  getOrderPoNumber,
  getUsersMobilePhones,
  isUserSubscribedToOrder,
  upsertAsset,
  upsertInvoice,
  getAllApps,
  createApp,
  getAppById,
  updateApp,
  deleteApp,
  getAppPrice,
  getCompanyAppPrices,
  deleteCompanyAppPrice,
  upsertCompanyAppPrice,
  addAppPriceOption,
  getAppPriceOptions,
  deleteAppPriceOption,
  getAppPriceOption,
  updateCompanyIds,
  getHiddenSyncroCustomerIds,
  hideSyncroCustomer,
  unhideSyncroCustomer,
  getSiteSettings,
  updateSiteSettings,
  getEmailTemplate,
  upsertEmailTemplate,
  getScheduledTasks,
  getScheduledTask,
  createScheduledTask,
  updateScheduledTask,
  deleteScheduledTask,
  markScheduledTaskRun,
  Company,
  User,
  UserCompany,
  ApiKey,
  ApiKeyWithUsage,
  AuditLog,
  App,
  AppPriceOption,
  Product,
  ProductPriceAlertWithProduct,
  ProductCompanyRestriction,
  Category,
  Asset,
  Invoice,
  Staff,
  OfficeGroupWithMembers,
  EmailTemplate,
} from './queries';
import { runMigrations } from './db';
import { logInfo, logError } from './logger';

dotenv.config();

const sessionSecret = process.env.SESSION_SECRET || '';
if (!sessionSecret) {
  throw new Error('SESSION_SECRET environment variable is required');
}

const defaultOpnformBaseUrl = '/forms/';
const configuredOpnformBaseUrl = process.env.OPNFORM_BASE_URL;
const opnformBaseUrl = configuredOpnformBaseUrl
  ? configuredOpnformBaseUrl.endsWith('/')
    ? configuredOpnformBaseUrl
    : `${configuredOpnformBaseUrl}/`
  : defaultOpnformBaseUrl;

const formProxyAllowedHosts = new Set<string>();

function addAllowedFormProxyHost(value: string): void {
  if (!value) {
    return;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return;
  }
  if (trimmed.includes('://')) {
    try {
      const parsed = new URL(trimmed);
      if (parsed.hostname) {
        formProxyAllowedHosts.add(parsed.hostname.toLowerCase());
      }
      if (parsed.host) {
        formProxyAllowedHosts.add(parsed.host.toLowerCase());
      }
    } catch (error) {
      logError('Invalid form proxy host provided', {
        host: trimmed,
        ...buildErrorMeta(error),
      });
    }
    return;
  }
  formProxyAllowedHosts.add(trimmed.toLowerCase());
}

const rawFormProxyHosts = process.env.FORM_PROXY_ALLOWED_HOSTS;
if (rawFormProxyHosts) {
  for (const candidate of rawFormProxyHosts.split(',')) {
    addAllowedFormProxyHost(candidate);
  }
}

if (configuredOpnformBaseUrl && configuredOpnformBaseUrl.includes('://')) {
  addAllowedFormProxyHost(configuredOpnformBaseUrl);
}

addAllowedFormProxyHost('form.hawkinsit.au');

const FORM_PROXY_TIMEOUT_MS = 15000;
const FORM_PROXY_SUCCESS_CSP =
  "default-src 'self' https: data: blob:; " +
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; " +
  "style-src 'self' 'unsafe-inline' https:; " +
  "img-src 'self' data: https:; " +
  "font-src 'self' data: https:; " +
  "connect-src 'self' https:; " +
  "frame-src 'self' https:; " +
  "form-action 'self' https:;";

const FORM_PROXY_ERROR_CSP =
  "default-src 'self'; " +
  "style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data:; " +
  "connect-src 'self'; " +
  "frame-src 'self';";

type FormWithEmbedUrl = Form & { embedUrl: string | null };

function buildErrorMeta(error: unknown): Record<string, unknown> {
  if (error instanceof Error) {
    return {
      message: error.message,
      name: error.name,
      stack: error.stack,
    };
  }
  return { error: String(error) };
}

function renderFormProxyError(message: string): string {
  const safeMessage = escapeHtml(message);
  return `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Form unavailable</title>
    <style>
      body {
        margin: 0;
        padding: 2rem;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: #f8fafc;
        color: #111827;
      }
      .message {
        max-width: 520px;
        margin: 0 auto;
        background: #ffffff;
        border-radius: 12px;
        padding: 1.75rem;
        box-shadow: 0 25px 50px -12px rgba(15, 23, 42, 0.25);
        border: 1px solid #e2e8f0;
      }
      h1 {
        margin: 0 0 0.75rem 0;
        font-size: 1.25rem;
        color: #1f2937;
      }
      p {
        margin: 0;
        line-height: 1.55;
      }
    </style>
  </head>
  <body data-form-proxy-error="true" data-error-message="${safeMessage}">
    <div class="message">
      <h1>Form unavailable</h1>
      <p>${safeMessage}</p>
      <p style="margin-top: 0.75rem; color: #475569; font-size: 0.95rem;">
        Return to the portal and use the “Open form in new tab” button to continue.
      </p>
    </div>
  </body>
</html>`;
}

function sendFormProxyError(res: Response, status: number, message: string): void {
  res.status(status);
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Security-Policy', FORM_PROXY_ERROR_CSP);
  res.type('html').send(renderFormProxyError(message));
}

function injectBaseTag(html: string, url: URL): string {
  const baseHref = new URL('./', url.toString()).toString();
  if (/<base\s/i.test(html)) {
    return html;
  }
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(
      /<head([^>]*)>/i,
      `<head$1><base href="${escapeHtml(baseHref)}">`
    );
  }
  return `<head><base href="${escapeHtml(baseHref)}"></head>${html}`;
}

function canProxyFormUrl(url: URL, portalOrigin: string): boolean {
  if (url.origin === portalOrigin) {
    return false;
  }
  if (url.protocol !== 'https:') {
    return false;
  }
  const host = url.host.toLowerCase();
  const hostname = url.hostname.toLowerCase();
  return formProxyAllowedHosts.has(host) || formProxyAllowedHosts.has(hostname);
}

class ExternalFormFetchError extends Error {
  public readonly statusCode: number;

  public readonly userMessage: string;

  constructor(message: string, statusCode: number, userMessage: string) {
    super(message);
    this.name = 'ExternalFormFetchError';
    this.statusCode = statusCode;
    this.userMessage = userMessage;
  }
}

async function fetchExternalForm(url: URL): Promise<string> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FORM_PROXY_TIMEOUT_MS);
  try {
    const response = await fetch(url.toString(), {
      headers: {
        Accept: 'text/html,application/xhtml+xml',
      },
      redirect: 'follow',
      signal: controller.signal,
    });
    if (!response.ok) {
      if (response.status === 404) {
        throw new ExternalFormFetchError(
          `Form not found at ${url.toString()}`,
          404,
          'The requested form could not be found.'
        );
      }
      throw new ExternalFormFetchError(
        `Unexpected status ${response.status} when fetching form ${url.toString()}`,
        response.status,
        'The form provider returned an unexpected response.'
      );
    }
    const contentType = response.headers.get('content-type');
    if (contentType && !contentType.includes('text/html')) {
      throw new ExternalFormFetchError(
        `Unsupported content type ${contentType} for form ${url.toString()}`,
        502,
        'The form provider returned an unsupported response.'
      );
    }
    return await response.text();
  } catch (error) {
    if (error instanceof ExternalFormFetchError) {
      throw error;
    }
    const isAbortError = error instanceof Error && error.name === 'AbortError';
    throw new ExternalFormFetchError(
      isAbortError
        ? `Timed out fetching form ${url.toString()}`
        : `Failed to fetch form ${url.toString()}: ${String(error)}`,
      502,
      'We could not contact the form provider.'
    );
  } finally {
    clearTimeout(timeout);
  }
}

function getPortalBaseUrl(req: Request): string {
  return process.env.PORTAL_URL || `${req.protocol}://${req.get('host')}`;
}

function getEmbedUrlForForm(
  hydratedUrl: string,
  formId: number,
  portalBaseUrl: string
): string | null {
  let resolved: URL;
  try {
    resolved = new URL(hydratedUrl, portalBaseUrl);
  } catch (error) {
    logError('Invalid form URL after template replacement', {
      url: hydratedUrl,
      ...buildErrorMeta(error),
    });
    return null;
  }
  const portalOrigin = new URL(portalBaseUrl).origin;
  if (resolved.origin === portalOrigin) {
    return resolved.toString();
  }
  return canProxyFormUrl(resolved, portalOrigin) ? `/forms/embed/${formId}` : null;
}

async function buildFormsContext(
  req: Request
): Promise<{
  hydratedForms: FormWithEmbedUrl[];
  companies: Awaited<ReturnType<typeof getCompaniesForUser>>;
  currentCompanyAssignment: Awaited<ReturnType<typeof getCompaniesForUser>>[number] | undefined;
  portalBaseUrl: string;
}> {
  const userId = req.session.userId!;
  const [forms, companies, currentUser] = await Promise.all([
    getFormsForUser(userId),
    getCompaniesForUser(userId),
    getUserById(userId),
  ]);
  const currentCompanyAssignment = companies.find(
    (company) => company.company_id === req.session.companyId
  );
  const currentCompanyDetails = currentCompanyAssignment?.company_id
    ? await getCompanyById(currentCompanyAssignment.company_id)
    : null;
  const portalBaseUrl = getPortalBaseUrl(req);
  const replacements = buildTemplateReplacementMap({
    user: currentUser
      ? {
          id: currentUser.id,
          email: currentUser.email,
          firstName: currentUser.first_name ?? '',
          lastName: currentUser.last_name ?? '',
        }
      : undefined,
    company: currentCompanyAssignment
      ? {
          id: currentCompanyAssignment.company_id,
          name: currentCompanyDetails?.name ?? currentCompanyAssignment.company_name ?? '',
          syncroCustomerId: currentCompanyDetails?.syncro_company_id ?? null,
        }
      : undefined,
    portal: {
      baseUrl: portalBaseUrl,
      loginUrl: `${portalBaseUrl}/login`,
    },
  });
  const hydratedForms: FormWithEmbedUrl[] = forms.map((form) => {
    const hydratedUrl = applyTemplateVariables(form.url, replacements);
    return {
      ...form,
      url: hydratedUrl,
      embedUrl: getEmbedUrlForForm(hydratedUrl, form.id, portalBaseUrl),
    };
  });
  return { hydratedForms, companies, currentCompanyAssignment, portalBaseUrl };
}

let appVersion = 'unknown';
let appBuild = 'unknown';
try {
  appVersion = fs.readFileSync(path.join(__dirname, '..', 'version.txt'), 'utf8').trim();
} catch (err) {
  console.error('Failed to read version file', err);
}
try {
  appBuild = fs.readFileSync(path.join(__dirname, '..', 'build.txt'), 'utf8').trim();
} catch (err) {
  console.error('Failed to read build file', err);
}

const smtpUser = process.env.SMTP_USER || process.env.SMTP_USERNAME;
const smtpPass = process.env.SMTP_PASS || process.env.SMTP_PASSWORD;

const transporter = nodemailer.createTransport({
  host: process.env.SMTP_HOST,
  port: parseInt(process.env.SMTP_PORT || '587', 10),
  secure:
    process.env.SMTP_SECURE === 'true' ||
    parseInt(process.env.SMTP_PORT || '587', 10) === 465,
  auth:
    smtpUser && smtpPass
      ? {
          user: smtpUser,
          pass: smtpPass,
        }
      : undefined,
});

const execFileAsync = util.promisify(execFile);

async function sendEmail(to: string, subject: string, html: string) {
  await transporter.sendMail({
    from: process.env.SMTP_FROM || smtpUser,
    to,
    subject,
    html,
  });
}

async function sendSmsUpdate(
  orderNumber: string,
  shippingStatus: string,
  eta: string | null
) {
  const { SMS_WEBHOOK_URL, SMS_WEBHOOK_API_KEY } = process.env;
  if (SMS_WEBHOOK_URL && SMS_WEBHOOK_API_KEY) {
    try {
      const [poNumber, subscriberIds] = await Promise.all([
        getOrderPoNumber(orderNumber),
        getSmsSubscribersByOrder(orderNumber),
      ]);
      if (!subscriberIds.length) {
        return;
      }
      const recipients = await getUsersMobilePhones(subscriberIds);
      if (!recipients.length) {
        return;
      }
      logInfo('Calling SMS webhook', {
        url: SMS_WEBHOOK_URL,
        orderNumber,
        recipientCount: recipients.length,
      });
      const res = await fetch(SMS_WEBHOOK_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': SMS_WEBHOOK_API_KEY,
        },
        body: JSON.stringify({
          type: 'Shipping Update',
          orderNumber,
          poNumber,
          shippingStatus,
          eta,
          recipients,
        }),
      });
      logInfo('SMS webhook responded', {
        url: SMS_WEBHOOK_URL,
        status: res.status,
        orderNumber,
      });
    } catch (err) {
      logError('Failed to call SMS webhook', {
        error: (err as Error).message,
        url: SMS_WEBHOOK_URL,
      });
    }
  }
}

function getCurrentUtcTimestamp(): string {
  return new Date().toISOString().slice(0, 19).replace('T', ' ');
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function handleProductPricingAlert(product: Product): Promise<void> {
  const buyPrice = product.buy_price;
  if (buyPrice === null || buyPrice === undefined || buyPrice <= 0) {
    await resolveProductPriceAlerts(product.id, getCurrentUtcTimestamp());
    return;
  }
  const thresholdPrice = Number((buyPrice * 1.1).toFixed(2));
  const priceBelowThreshold = product.price < thresholdPrice;
  const vipPriceBelowThreshold =
    product.vip_price !== null && product.vip_price < thresholdPrice;
  if (!priceBelowThreshold && !vipPriceBelowThreshold) {
    await resolveProductPriceAlerts(product.id, getCurrentUtcTimestamp());
    return;
  }
  const existingAlert = await getActiveProductPriceAlertByProductId(product.id);
  if (existingAlert) {
    return;
  }
  const triggeredAt = getCurrentUtcTimestamp();
  const alertId = await createProductPriceAlert(
    product.id,
    product.price,
    product.vip_price,
    buyPrice,
    thresholdPrice,
    triggeredAt
  );
  const superAdmin = await getUserById(1);
  if (!superAdmin?.email) {
    return;
  }
  const escapedName = escapeHtml(product.name);
  const escapedSku = escapeHtml(product.sku);
  const htmlLines = [
    `<p>Product <strong>${escapedName}</strong> (${escapedSku}) has a current price of $${product.price.toFixed(
      2
    )}.</p>`,
    `<p>The supplier DBP plus 10% is $${thresholdPrice.toFixed(2)}, which is higher than the${
      vipPriceBelowThreshold && product.vip_price !== null ? ' configured VIP price' : ' configured price'
    }.</p>`,
    `<p>VIP Price: ${
      product.vip_price !== null
        ? `$${product.vip_price.toFixed(2)}`
        : 'Not configured'
    }</p>`,
  ];
  const html = `${htmlLines.join('')}
    <p>Please review the product pricing to ensure margins remain protected.</p>`;
  const subject = `Product pricing alert: ${product.name}`;
  try {
    await sendEmail(superAdmin.email, subject, html);
    await markProductPriceAlertEmailed(alertId, getCurrentUtcTimestamp());
  } catch (err) {
    logError('Failed to send product pricing alert email', {
      error: (err as Error).message,
      productId: product.id,
    });
  }
}

const scheduledJobs = new Map<number, cron.ScheduledTask>();
let systemUpdateInProgress = false;

async function importSyncroContactsForCompany(companyId: number) {
  const company = await getCompanyById(companyId);
  if (!company || !company.syncro_company_id) {
    return;
  }
  try {
    const [contacts, existingStaff] = await Promise.all([
      getSyncroContacts(company.syncro_company_id),
      getStaffByCompany(company.id),
    ]);
    for (const contact of contacts) {
      const fullName = [
        contact.first_name,
        contact.last_name,
        (contact as any).name,
      ]
        .filter(Boolean)
        .join(' ')
        .trim();
      if (/ex staff/i.test(fullName)) {
        continue;
      }
      const firstName = contact.first_name || fullName.split(' ')[0] || '';
      const lastName =
        contact.last_name ||
        fullName
          .split(' ')
          .slice(1)
          .join(' ');
      const email = (contact as any).email || (contact as any).email_address || null;
      const phone = (contact as any).mobile || (contact as any).phone || null;
      const existing = findExistingStaff(
        existingStaff,
        firstName,
        lastName,
        email
      );
      if (existing) {
        await updateStaff(
          existing.id,
          company.id,
          firstName,
          lastName,
          email || existing.email,
          phone || existing.mobile_phone || null,
          existing.date_onboarded,
          existing.date_offboarded || null,
          existing.enabled === 1,
          existing.street || null,
          existing.city || null,
          existing.state || null,
          existing.postcode || null,
          existing.country || null,
          (contact as any).department || existing.department || null,
          existing.job_title || null,
          existing.org_company || null,
          existing.manager_name || null,
          existing.account_action || null,
          String((contact as any).id)
        );
      } else {
        await addStaff(
          company.id,
          firstName,
          lastName,
          email || '',
          phone || null,
          null,
          null,
          true,
          (contact as any).address1 || (contact as any).address || null,
          (contact as any).city || null,
          (contact as any).state || null,
          (contact as any).zip || null,
          (contact as any).country || null,
          null,
          (contact as any).title || null,
          null,
          null,
          String((contact as any).id)
        );
        existingStaff.push({
          id: 0,
          company_id: company.id,
          first_name: firstName,
          last_name: lastName,
          email: email || '',
          mobile_phone: phone || null,
          date_onboarded: null,
          date_offboarded: null,
          enabled: 1,
          street: (contact as any).address1 || (contact as any).address || null,
          city: (contact as any).city || null,
          state: (contact as any).state || null,
          postcode: (contact as any).zip || null,
          country: (contact as any).country || null,
          department: (contact as any).department || null,
          job_title: (contact as any).title || null,
          org_company: null,
          manager_name: null,
          account_action: null,
          syncro_contact_id: String((contact as any).id),
        } as any);
      }
    }
  } catch (err) {
    console.error('Syncro contacts import failed', err);
  }
}

async function importSyncroAssetsForCompany(companyId: number) {
  const company = await getCompanyById(companyId);
  if (!company || !company.syncro_company_id) {
    return;
  }
  try {
    const assets = await getSyncroAssets(company.syncro_company_id);
    for (const asset of assets) {
      const details = extractAssetDetails(asset);
      const name = details.name || asset.name || 'Asset';
      const type = details.type || '';
      const serial = details.serial_number || null;
      const status = details.status || '';
      const osName = details.os_name || null;
      const cpuName = details.cpu_name || null;
      const ramGb = details.ram_gb ?? null;
      const hddSize = details.hdd_size || null;
      const lastSync = details.last_sync || null;
      const motherboardManufacturer =
        details.motherboard_manufacturer || null;
      const formFactor = details.form_factor || null;
      const lastUser = details.last_user || null;
      const approxAge = details.cpu_age ?? null;
      const performanceScore = details.performance_score ?? null;
      const warrantyStatus = details.warranty_status || null;
      const warrantyEndDate = details.warranty_end_date || null;
      const syncroId = asset.id?.toString() || null;
      await upsertAsset(
        company.id,
        name,
        type,
        serial,
        status,
        osName,
        cpuName,
        ramGb,
        hddSize,
        lastSync,
        motherboardManufacturer,
        formFactor,
        lastUser,
        approxAge,
        performanceScore,
        warrantyStatus,
        warrantyEndDate,
        syncroId
      );
    }
  } catch (err) {
    console.error('Syncro assets import failed', err);
  }
}

const STOCK_FEED_FILE = path.join(__dirname, '..', 'stock-feed.xml');
const xmlParser = new XMLParser({ ignoreAttributes: false });

async function downloadStockFeed(): Promise<void> {
  const url = process.env.STOCK_FEED_URL;
  if (!url) {
    throw new Error('STOCK_FEED_URL not set');
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to download feed: ${res.status}`);
  const text = await res.text();
  await fs.promises.writeFile(STOCK_FEED_FILE, text);
  const parsed = xmlParser.parse(text);
  const items = parsed?.rss?.channel?.item || parsed?.items || parsed?.item || [];
  const arr = Array.isArray(items) ? items : [items];
  await clearStockFeed();
  for (const item of arr) {
    const sku = item.StockCode || item['@_StockCode'];
    if (!sku) continue;
    await insertStockFeedItem({
      sku,
      product_name: item.ProductName || '',
      product_name2: item.ProductName2 || '',
      rrp: item.RRP ? parseFloat(item.RRP) : null,
      category_name: item.CategoryName || null,
      on_hand_nsw: parseInt(item.OnHandChanelNsw || '0', 10),
      on_hand_qld: parseInt(item.OnHandChanelQld || '0', 10),
      on_hand_vic: parseInt(item.OnHandChanelVic || '0', 10),
      on_hand_sa: parseInt(item.OnHandChanelSa || '0', 10),
      dbp: item.DBP ? parseFloat(item.DBP) : null,
      weight: item.Weight ? parseFloat(item.Weight) : null,
      length: item.Length ? parseFloat(item.Length) : null,
      width: item.Width ? parseFloat(item.Width) : null,
      height: item.Height ? parseFloat(item.Height) : null,
      pub_date: formatDate(item.pubDate || null),
      warranty_length: item.WarrantyLength || null,
      manufacturer: item.Manufacturer || null,
      image_url: item.ImageUrl || null,
    });
  }
}

function formatDate(d: string | null): string | null {
  if (!d) return null;
  const slashParts = d.split('/');
  if (slashParts.length === 3) {
    const [day, month, year] = slashParts;
    return `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}`;
  }
  const parsed = new Date(d);
  if (!isNaN(parsed.getTime())) {
    let offsetMinutes = 0;
    const gmt = d.match(/GMT([+-]\d{4})/);
    if (gmt) {
      const sign = gmt[1][0] === '+' ? 1 : -1;
      const hours = parseInt(gmt[1].slice(1, 3), 10);
      const minutes = parseInt(gmt[1].slice(3, 5), 10);
      offsetMinutes = sign * (hours * 60 + minutes);
    } else {
      const off = d.match(/([+-]\d{2}):?(\d{2})/);
      if (off) {
        const sign = off[1][0] === '+' ? 1 : -1;
        const hours = parseInt(off[1].slice(1), 10);
        const minutes = parseInt(off[2], 10);
        offsetMinutes = sign * (hours * 60 + minutes);
      }
    }
    if (offsetMinutes) {
      parsed.setTime(parsed.getTime() + offsetMinutes * 60000);
    }
    return parsed.toISOString().slice(0, 10);
  }
  return null;
}

async function processFeedItem(item: any, existing?: any): Promise<void> {
  const code =
    item.StockCode ||
    item['@_StockCode'] ||
    item.stock_code ||
    item.sku;
  if (!code) return;
  const feedName = item.ProductName || item.product_name || '';
  const description = item.ProductName2 || item.product_name2 || '';
  const price = existing
    ? Number(existing.price)
    : item.RRP
    ? parseFloat(item.RRP)
    : item.rrp !== undefined
    ? Number(item.rrp)
    : 0;
  const vipPrice = existing
    ? existing.vip_price ?? Number(existing.price)
    : price;
  const categoryName = item.CategoryName || item.category_name || '';
  let categoryId: number | null = null;
  if (categoryName) {
    const existing = await getCategoryByName(categoryName);
    categoryId = existing ? existing.id : await createCategory(categoryName);
  }
  const stockNsw = parseInt(
    item.OnHandChanelNsw || item.on_hand_nsw || '0',
    10
  );
  const stockQld = parseInt(
    item.OnHandChanelQld || item.on_hand_qld || '0',
    10
  );
  const stockVic = parseInt(
    item.OnHandChanelVic || item.on_hand_vic || '0',
    10
  );
  const stockSa = parseInt(
    item.OnHandChanelSa || item.on_hand_sa || '0',
    10
  );
  const stock = stockNsw + stockQld + stockVic + stockSa;
  const buyPrice = item.DBP
    ? parseFloat(item.DBP)
    : item.dbp !== undefined
    ? Number(item.dbp)
    : null;
  const weight = item.Weight
    ? parseFloat(item.Weight)
    : item.weight !== undefined
    ? Number(item.weight)
    : null;
  const length = item.Length
    ? parseFloat(item.Length)
    : item.length !== undefined
    ? Number(item.length)
    : null;
  const width = item.Width
    ? parseFloat(item.Width)
    : item.width !== undefined
    ? Number(item.width)
    : null;
  const height = item.Height
    ? parseFloat(item.Height)
    : item.height !== undefined
    ? Number(item.height)
    : null;
  const stockAt = formatDate(
    (item.pubDate || item.pub_date || null) as string | null
  );
  const warrantyLength = item.WarrantyLength || item.warranty_length || null;
  const manufacturer = item.Manufacturer || item.manufacturer || null;
  let imageUrl: string | null = null;
  const imageSrc = item.ImageUrl || item.image_url;
  if (imageSrc) {
    try {
      const res = await fetch(imageSrc);
      if (res.ok) {
        const buf = Buffer.from(await res.arrayBuffer());
        const ext = path.extname(new URL(imageSrc).pathname) || '.jpg';
        const fileName = `${code}${ext}`;
        const dest = path.join(__dirname, 'public', 'uploads', fileName);
        await fs.promises.writeFile(dest, buf);
        imageUrl = `/uploads/${fileName}`;
      }
    } catch (err) {
      console.error('Image download failed', err);
    }
  }
  let name = feedName;
  if (existing) {
    const existingSku = (existing.vendor_sku || existing.sku || '').toLowerCase();
    if (existing.name && existing.name.toLowerCase() !== existingSku) {
      name = existing.name;
    }
  }
  await upsertProductFromFeed({
    name,
    sku: code,
    vendorSku: code,
    description,
    imageUrl,
    price,
    vipPrice,
    stock,
    categoryId,
    stockNsw,
    stockQld,
    stockVic,
    stockSa,
    buyPrice,
    weight,
    length,
    width,
    height,
    stockAt,
    warrantyLength,
    manufacturer,
  });
  const updatedProduct = await getProductBySku(String(code));
  if (updatedProduct) {
    await handleProductPricingAlert(updatedProduct);
  }
}

async function updateProductsFromFeed(): Promise<void> {
  const products = await getAllProducts(true);
  for (const p of products) {
    const sku = p.vendor_sku || p.sku;
    if (!sku) continue;
    const item = await getStockFeedItemBySku(String(sku));
    if (!item) continue;
    await processFeedItem(item, p);
  }
}

async function importProductByVendorSku(
  vendorSku: string
): Promise<boolean> {
  const item = await getStockFeedItemBySku(vendorSku);
  if (!item) return false;
  await processFeedItem(item);
  return true;
}

async function runScheduledTask(id: number) {
  const task = await getScheduledTask(id);
  if (!task) return;
  try {
    switch (task.command) {
      case 'sync_staff':
        if (task.company_id) await importSyncroContactsForCompany(task.company_id);
        break;
      case 'sync_assets':
        if (task.company_id) await importSyncroAssetsForCompany(task.company_id);
        break;
      case 'sync_o365':
        if (task.company_id) await syncM365Licenses(task.company_id);
        break;
      case 'update_stock_feed':
        await downloadStockFeed();
        break;
      case 'update_products':
        await updateProductsFromFeed();
        break;
      case 'system_update':
        systemUpdateInProgress = true;
        try {
          await execFileAsync(path.join(__dirname, '..', 'update.sh'));
        } finally {
          systemUpdateInProgress = false;
        }
        break;
      default:
        console.log(`Task ${task.command} not implemented`);
    }
    await markScheduledTaskRun(id);
  } catch (err) {
    console.error('Scheduled task failed', err);
  }
}

async function scheduleAllTasks() {
  scheduledJobs.forEach((job) => job.stop());
  scheduledJobs.clear();
  const tasks = await getScheduledTasks();
  tasks.forEach((t) => {
    if (t.active) {
      const job = cron.schedule(
        t.cron,
        () => runScheduledTask(t.id),
        { timezone: process.env.CRON_TIMEZONE || 'Etc/UTC' }
      );
      scheduledJobs.set(t.id, job);
    }
  });
}

async function createDefaultSchedulesForCompany(companyId: number) {
  const cronExpr = getRandomDailyCron();
  await createScheduledTask(
    companyId,
    'Sync Staff From Syncro',
    'sync_staff',
    cronExpr
  );
  await createScheduledTask(
    companyId,
    'Sync Office 365 License Counts',
    'sync_o365',
    cronExpr
  );
  await createScheduledTask(
    companyId,
    'Sync Xero Invoices',
    'sync_xero',
    cronExpr
  );
  await createScheduledTask(
    companyId,
    'Sync Assets From Syncro',
    'sync_assets',
    cronExpr
  );
  await scheduleAllTasks();
}

async function createDefaultSystemSchedules() {
  const tasks = await getScheduledTasks();
  if (!tasks.some((t) => t.command === 'update_stock_feed')) {
    await createScheduledTask(null, 'Update Stock Feed', 'update_stock_feed', getRandomDailyCron());
  }
  if (!tasks.some((t) => t.command === 'update_products')) {
    await createScheduledTask(null, 'Update Products', 'update_products', getRandomDailyCron());
  }
}

setInterval(() => {
  purgeExpiredVerificationCodes().catch((err) =>
    console.error('Failed to purge verification codes', err)
  );
}, 60 * 1000).unref();

function toDateTime(value?: string): string | null {
  return value ? value.replace('T', ' ') : null;
}

function toDate(value?: string): string | null {
  return value ? value.split('T')[0] : null;
}

function mapStaff(s: Staff) {
  return {
    id: s.id,
    companyId: s.company_id,
    firstName: s.first_name,
    lastName: s.last_name,
    email: s.email,
    mobilePhone: s.mobile_phone ?? null,
    dateOnboarded: s.date_onboarded,
    dateOffboarded: s.date_offboarded ?? null,
    enabled: !!s.enabled,
    street: s.street ?? null,
    city: s.city ?? null,
    state: s.state ?? null,
    postcode: s.postcode ?? null,
    country: s.country ?? null,
    department: s.department ?? null,
    jobTitle: s.job_title ?? null,
    company: s.org_company ?? null,
    managerName: s.manager_name ?? null,
    accountAction: s.account_action ?? null,
    verificationCode: s.verification_code ?? null,
  };
}

const app = express();
app.set('trust proxy', 1);
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

// Log API calls to pm2 logs when enabled
if (process.env.API_DEBUG === '1' || process.env.API_DEBUG === 'true') {
  app.use('/api', (req, _res, next) => {
    console.debug('API Call:', req.method, req.originalUrl);
    next();
  });
}

// Register security middleware early
app.use(
  helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: [
          "'self'",
          "'unsafe-inline'",
          'https://cdn.datatables.net',
          'https://cdn.jsdelivr.net',
          'https://code.jquery.com',
          'https://static.cloudflareinsights.com',
        ],
        styleSrc: [
          "'self'",
          "'unsafe-inline'",
          'https://cdnjs.cloudflare.com',
          'https://cdn.datatables.net',
        ],
        imgSrc: ["'self'", 'data:', 'https:'],
        connectSrc: ["'self'"],
        frameSrc: ["'self'", 'https://form.hawkinsit.au'],
        fontSrc: ["'self'", 'https:', 'data:'],
        objectSrc: ["'none'"],
        upgradeInsecureRequests: [],
      },
    },
    referrerPolicy: { policy: 'no-referrer' },
    crossOriginEmbedderPolicy: false,
  })
);

// Register core middleware needed for all requests first
app.use(cookieParser());
const sessionTtlMs = 1000 * 60 * 60 * 24; // 1 day
let sessionStore: session.Store;
if (process.env.REDIS_URL) {
  const redisClient = createClient({ url: process.env.REDIS_URL });
  redisClient.on('error', (err) => console.error('Redis error', err));
  redisClient.connect().catch((err) => console.error('Redis connect error', err));
  sessionStore = new RedisStore({
    client: redisClient,
    prefix: 'sess:',
    ttl: sessionTtlMs / 1000,
  });
} else {
  sessionStore = new session.MemoryStore();
}
app.use(
  session({
    store: sessionStore,
    secret: sessionSecret,
    resave: false,
    saveUninitialized: false,
    cookie: {
      httpOnly: true,
      secure: true,
      sameSite: 'lax',
      maxAge: sessionTtlMs,
    },
  })
);

// Attach the audit logger before body parsing so that even requests that
// fail in the parsers (e.g. malformed JSON) are still recorded
app.use(auditLogger);

// Body parsing and static serving come after the audit logger
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

const csrfProtection = csurf();
function csrfMiddleware(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  if (req.session.userId) {
    if (req.is('multipart/form-data')) {
      return next();
    }
    csrfProtection(req, res, (err) => {
      if (err) return next(err);
      res.locals.csrfToken = req.csrfToken();
      next();
    });
  } else {
    next();
  }
}

app.use(csrfMiddleware);

// Secure file upload handling
const privateUploadDir = path.join(__dirname, '..', 'private_uploads');
if (!fs.existsSync(privateUploadDir)) {
  fs.mkdirSync(privateUploadDir, { recursive: true, mode: 0o700 });
}

const storage = multer.diskStorage({
  destination: (_req, _file, cb) => cb(null, privateUploadDir),
  filename: (_req, file, cb) => {
    const safeName = file.originalname
      .toLowerCase()
      .replace(/[^a-z0-9.]/g, '_');
    const uniqueName = `${Date.now()}-${Math.round(Math.random() * 1e9)}-${safeName}`;
    cb(null, uniqueName);
  },
});

const allowedMimes = [
  'image/jpeg',
  'image/png',
  'image/gif',
  'image/x-icon',
  'image/vnd.microsoft.icon',
  'image/svg+xml',
];
const upload = multer({
  storage,
  limits: { fileSize: 5 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => {
    if (allowedMimes.includes(file.mimetype)) {
      cb(null, true);
    } else {
      cb(new Error('Invalid file type'));
    }
  },
});

async function scanFileForViruses(filePath: string): Promise<void> {
  try {
    await execFileAsync('clamscan', ['--no-summary', filePath]);
  } catch (err: any) {
    if (err) {
      if (typeof err.code === 'number') {
        if (err.code === 1) {
          throw new Error('Virus detected in uploaded file');
        }
        console.warn('Virus scan failed or clamscan not installed', err);
        throw new Error('Virus scan failed');
      }
      if (err.code === 'ENOENT') {
        console.warn('clamscan not found, skipping virus scan');
        return;
      }
    }
    console.warn('Failed to run virus scan', err);
    throw new Error('Virus scan failed');
  }
}

const enforceFilePermissions: express.RequestHandler = async (req, _res, next) => {
  if (req.file) {
    try {
      await scanFileForViruses(req.file.path);
      await fs.promises.chmod(req.file.path, 0o600);
    } catch (err) {
      try {
        await fs.promises.unlink(req.file.path);
      } catch (unlinkErr) {
        console.error('Failed to remove uploaded file after scan error', unlinkErr);
      }
      return next(err);
    }
  }
  next();
};

const uploadMiddleware = [
  upload.single('image'),
  enforceFilePermissions,
  csrfProtection,
];
const memoryUpload = multer({
  limits: { fileSize: 1 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => {
    if (allowedMimes.includes(file.mimetype)) {
      cb(null, true);
    } else {
      cb(new Error('Invalid file type'));
    }
  },
});

// Serve uploaded files via controlled route
app.get('/uploads/:filename', ensureAuth, (req, res) => {
  const filePath = path.join(privateUploadDir, path.basename(req.params.filename));
  res.sendFile(filePath);
});

const verifyAttempts: Record<string, { count: number; reset: number }> = {};

const failedLoginAttempts: Record<string, { count: number; lockUntil?: number }> = {};
const MAX_FAILED_ATTEMPTS = 5;
const LOCK_TIME_MS = 15 * 60 * 1000;

const loginLimiter = rateLimit({
  windowMs: LOCK_TIME_MS,
  max: MAX_FAILED_ATTEMPTS,
  standardHeaders: true,
  legacyHeaders: false,
  keyGenerator: (req) => (req.body.email || req.ip),
  handler: (req, res) => {
    res.status(429);
    res.render('login', {
      error: 'Too many login attempts. Please try again later.',
    });
  },
});

// Populate common template variables
app.use(async (req, res, next) => {
  res.locals.isSuperAdmin = Number(req.session.userId) === 1;
  res.locals.cart = req.session.cart || [];
  res.locals.hasForms = req.session.hasForms ?? false;
  res.locals.version = appVersion;
  res.locals.build = appBuild;
  res.locals.opnformBaseUrl = opnformBaseUrl;
  res.locals.templateVariables = TEMPLATE_VARIABLES;
  try {
    res.locals.siteSettings = await getSiteSettings();
  } catch (err) {
    console.error('Failed to load site settings', err);
    res.locals.siteSettings = {
      company_name: null,
      login_logo: null,
      sidebar_logo: null,
      favicon: null,
    };
  }
  next();
});

function generateTrustedDeviceToken(userId: number): string {
  const expires = Date.now() + 24 * 60 * 60 * 1000;
  const data = `${userId}.${expires}`;
  const hmac = crypto
    .createHmac('sha256', sessionSecret)
    .update(data)
    .digest('hex');
  return `${data}.${hmac}`;
}

function verifyTrustedDeviceToken(token: string, userId: number): boolean {
  const parts = token.split('.');
  if (parts.length !== 3) return false;
  const [id, expires, signature] = parts;
  if (Number(id) !== userId) return false;
  if (Number(expires) < Date.now()) return false;
  const data = `${id}.${expires}`;
  const hmac = crypto
    .createHmac('sha256', sessionSecret)
    .update(data)
    .digest('hex');
  return hmac === signature;
}

function sanitizeSensitiveData(obj: any): any {
  if (!obj || typeof obj !== 'object') return obj;
  const result: any = Array.isArray(obj) ? [] : {};
  for (const [key, value] of Object.entries(obj)) {
    if (value && typeof value === 'object') {
      result[key] = sanitizeSensitiveData(value);
    } else {
      const lower = key.toLowerCase();
      if (lower.includes('password') || (lower.includes('api') && lower.includes('key'))) {
        result[key] = '***';
      } else {
        result[key] = value;
      }
    }
  }
  return result;
}

function auditLogger(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  res.on('finish', async () => {
    const sanitizedBody = sanitizeSensitiveData(req.body || {});
    let companyId: number | null = null;

    const sources: any[] = [
      req.body?.companyId,
      req.body?.organisationId,
      req.query.companyId,
      req.query.organisationId,
      (req.params as any).companyId,
      (req.params as any).organisationId,
    ];
    for (const s of sources) {
      const n = parseInt(s as string, 10);
      if (!isNaN(n)) {
        companyId = n;
        break;
      }
    }

    if (!companyId) {
      const lookupMappings: Array<[
        string,
        (id: number) => Promise<{ company_id: number } | null>
      ]> = [
        ['userId', getUserById],
        ['staffId', getStaffById],
        ['assetId', getAssetById],
        ['invoiceId', getInvoiceById],
      ];
      for (const [param, getter] of lookupMappings) {
        const raw = (req.params as any)[param] || (req.body as any)?.[param];
        if (raw) {
          const idNum = parseInt(raw as string, 10);
          if (!isNaN(idNum)) {
            try {
              const record = await getter(idNum);
              if (record && 'company_id' in record && record.company_id) {
                companyId = record.company_id;
                break;
              }
            } catch {}
          }
        }
      }
      if (!companyId && req.params.id) {
        const idNum = parseInt(req.params.id, 10);
        if (!isNaN(idNum)) {
          try {
            if (req.originalUrl.includes('/users/')) {
              const r = await getUserById(idNum);
              companyId = r?.company_id || null;
            } else if (req.originalUrl.includes('/staff/')) {
              const r = await getStaffById(idNum);
              companyId = r?.company_id || null;
            } else if (req.originalUrl.includes('/assets/')) {
              const r = await getAssetById(idNum);
              companyId = r?.company_id || null;
            } else if (req.originalUrl.includes('/invoices/')) {
              const r = await getInvoiceById(idNum);
              companyId = r?.company_id || null;
            }
          } catch {}
        }
      }
    }

    const ip = (req.headers['cf-connecting-ip'] as string) || req.ip;
    logAudit({
      userId: req.session.userId || null,
      companyId,
      action: `${req.method} ${req.originalUrl}`,
      previousValue: null,
      newValue: JSON.stringify(sanitizedBody),
      apiKey: req.apiKey,
      ipAddress: ip,
    }).catch(() => {});
  });
  next();
}

app.use(auditLogger);

const swaggerSpec = swaggerJSDoc({
  definition: {
    openapi: '3.0.0',
    info: {
      title: 'MyPortal API',
      version: '1.0.0',
    },
    tags: [
      { name: 'Apps' },
      { name: 'Companies' },
      { name: 'Users' },
      { name: 'Licenses' },
      { name: 'Staff' },
      { name: 'Assets' },
      { name: 'Invoices' },
      { name: 'Shop' },
    ],
    components: {
      securitySchemes: {
        ApiKeyAuth: {
          type: 'apiKey',
          in: 'header',
          name: 'x-api-key',
        },
      },
    },
    security: [{ ApiKeyAuth: [] }],
  },
  apis: [path.join(__dirname, '../src/**/*.ts')],
});

app.use(
  '/swagger',
  ensureAuth,
  ensureSuperAdmin,
  swaggerUi.serve,
  swaggerUi.setup(swaggerSpec)
);

function ensureAuth(req: express.Request, res: express.Response, next: express.NextFunction) {
  if (!req.session.userId) {
    return res.redirect('/login');
  }
  if (
    req.session.mustChangePassword &&
    req.path !== '/force-password-change' &&
    req.path !== '/logout'
  ) {
    return res.redirect('/force-password-change');
  }
  next();
}

async function ensureAdmin(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  if (Number(req.session.userId) === 1) {
    return next();
  }
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.is_admin) {
    return next();
  }
  return res.redirect('/');
}

function ensureSuperAdmin(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  if (Number(req.session.userId) === 1) {
    return next();
  }
  return res.redirect('/');
}

async function completeLogin(req: express.Request, userId: number) {
  const [companies, forms] = await Promise.all([
    getCompaniesForUser(userId),
    getFormsForUser(userId),
  ]);
  req.session.userId = userId;
  req.session.companyId = companies[0]?.company_id;
  req.session.hasForms = forms.length > 0;
  req.session.cookie.expires = undefined;
  req.session.cookie.maxAge = undefined;
}

app.get('/api-docs', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('api-docs', {
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: true,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get('/login', async (req, res) => {
  const count = await getUserCount();
  if (count === 0) {
    return res.redirect('/register');
  }
  res.render('login', { error: '' });
});

app.post('/login', loginLimiter, async (req, res) => {
  const { email, password } = req.body;
  const identifier = email || req.ip;
  logInfo('Login attempt', { email, ip: req.ip });
  const attempt = failedLoginAttempts[identifier];
  if (attempt?.lockUntil) {
    if (attempt.lockUntil > Date.now()) {
      logInfo('Login locked', { email, ip: req.ip });
      return res
        .status(423)
        .render('login', { error: 'Account locked. Try again later.' });
    }
    attempt.count = 0;
    attempt.lockUntil = undefined;
  }

  const user = await getUserByEmail(email);
  if (user && (await bcrypt.compare(password, user.password_hash))) {
    delete failedLoginAttempts[identifier];
    loginLimiter.resetKey(identifier);
    const trusted = req.cookies[`trusted_${user.id}`];
    if (trusted && verifyTrustedDeviceToken(trusted, user.id)) {
      await completeLogin(req, user.id);
      logInfo('Login success', { userId: user.id, ip: req.ip });
      if (user.force_password_change) {
        req.session.mustChangePassword = true;
        return res.redirect('/force-password-change');
      }
      return res.redirect('/');
    }
    req.session.tempUserId = user.id;
    req.session.pendingForcePassword = !!user.force_password_change;
    const totpAuths = await getUserTotpAuthenticators(user.id);
    if (totpAuths.length === 0) {
      req.session.pendingTotpSecret = authenticator.generateSecret();
      req.session.requireTotpSetup = true;
    } else {
      req.session.requireTotpSetup = false;
    }
    return res.redirect('/totp');
  }

  const record = failedLoginAttempts[identifier] || { count: 0 };
  record.count += 1;
  if (record.count >= MAX_FAILED_ATTEMPTS) {
    record.lockUntil = Date.now() + LOCK_TIME_MS;
  }
  failedLoginAttempts[identifier] = record;
  const message = record.lockUntil
    ? 'Account locked due to too many failed attempts. Try again later.'
    : 'Invalid credentials';
  logInfo('Login failed', { email, ip: req.ip });
  res.status(401).render('login', { error: message });
});

app.get('/totp', async (req, res) => {
  if (!req.session.tempUserId) {
    return res.redirect('/login');
  }
  let qrCode: string | null = null;
  let secret: string | null = null;
  if (req.session.requireTotpSetup && req.session.pendingTotpSecret) {
    const user = await getUserById(req.session.tempUserId);
    secret = req.session.pendingTotpSecret;
    const otpauth = authenticator.keyuri(
      user!.email,
      'MyPortal',
      secret
    );
    qrCode = await QRCode.toDataURL(otpauth);
  }
  res.render('totp', { qrCode, secret, requireSetup: req.session.requireTotpSetup, error: '' });
});

app.post('/totp', async (req, res) => {
  if (!req.session.tempUserId) {
    return res.redirect('/login');
  }
  const userId = req.session.tempUserId;
  let valid = false;
  if (req.session.requireTotpSetup && req.session.pendingTotpSecret) {
    const secret = req.session.pendingTotpSecret;
    valid = authenticator.verify({ token: req.body.token, secret });
    if (valid) {
      const name = req.body.deviceName || 'Authenticator';
      await addUserTotpAuthenticator(userId, name, secret);
    }
  } else {
    const auths = await getUserTotpAuthenticators(userId);
    valid = auths.some((a) =>
      authenticator.verify({
        token: req.body.token,
        secret: decryptSecret(a.secret),
      })
    );
  }
  if (valid) {
    if (req.body.trust) {
      const token = generateTrustedDeviceToken(userId);
      res.cookie(`trusted_${userId}`, token, {
        maxAge: 24 * 60 * 60 * 1000,
        httpOnly: true,
      });
    }
    await completeLogin(req, userId);
    req.session.tempUserId = undefined;
    req.session.pendingTotpSecret = undefined;
    req.session.requireTotpSetup = undefined;
    if (req.session.pendingForcePassword) {
      req.session.mustChangePassword = true;
      req.session.pendingForcePassword = undefined;
      return res.redirect('/force-password-change');
    }
    return res.redirect('/');
  }
  let qrCode: string | null = null;
  let secret: string | null = null;
  if (req.session.requireTotpSetup && req.session.pendingTotpSecret) {
    const user = await getUserById(userId);
    secret = req.session.pendingTotpSecret;
    const otpauth = authenticator.keyuri(
      user!.email,
      'MyPortal',
      secret
    );
    qrCode = await QRCode.toDataURL(otpauth);
  }
  res.render('totp', {
    qrCode,
    secret,
    requireSetup: req.session.requireTotpSetup,
    error: 'Invalid code',
  });
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
    await createDefaultSchedulesForCompany(companyId);
    const userId = await createUser(email, passwordHash, companyId);
    await assignUserToCompany(userId, companyId, true, 3, true, true, true, true, true, true);
    req.session.userId = userId;
    req.session.companyId = companyId;
    req.session.hasForms = false;
    res.redirect('/');
  } catch (err) {
    res.render('register', { error: 'Registration failed' });
  }
});

app.get('/password-setup', async (req, res) => {
  const token = req.query.token as string;
  if (!token) {
    return res.status(400).send('Invalid token');
  }
  const userId = await getUserIdByPasswordToken(token);
  if (!userId) {
    return res.status(400).send('Invalid or expired token');
  }
  const siteSettings = await getSiteSettings();
  res.render('password-setup', { error: '', token, siteSettings });
});

app.post('/password-setup', async (req, res) => {
  const { token, newPassword } = req.body;
  if (!token || !newPassword) {
    const siteSettings = await getSiteSettings();
    return res
      .status(400)
      .render('password-setup', { error: 'Invalid token', token: '', siteSettings });
  }
  const userId = await getUserIdByPasswordToken(token);
  if (!userId) {
    const siteSettings = await getSiteSettings();
    return res
      .status(400)
      .render('password-setup', { error: 'Invalid or expired token', token: '', siteSettings });
  }
  const passwordHash = await bcrypt.hash(newPassword, 10);
  await updateUserPassword(userId, passwordHash);
  await markPasswordTokenUsed(token);
  res.redirect('/login');
});

app.get('/verify', (req, res) => {
  res.render('verify', { result: null, adminName: null });
});

app.post('/verify', async (req, res) => {
  const ip = req.ip || 'unknown';
  const now = Date.now();
  const record = verifyAttempts[ip];
  if (!record || now > record.reset) {
    verifyAttempts[ip] = { count: 1, reset: now + 60 * 1000 };
  } else {
    if (record.count >= 3) {
      return res.status(429).render('verify', { result: 'rate', adminName: null });
    }
    record.count++;
  }

  const code = (req.body.code || '').trim();
  const verification = await getVerificationByCode(code);
  if (verification) {
    res.render('verify', { result: 'valid', adminName: verification.admin_name });
  } else {
    res.render('verify', { result: 'invalid', adminName: null });
  }
});

app.get('/logout', (req, res) => {
  req.session.destroy(() => {
    res.redirect('/login');
  });
});

app.get('/force-password-change', ensureAuth, (req, res) => {
  res.render('force-password-change', { error: '' });
});

app.post('/force-password-change', ensureAuth, async (req, res) => {
  const { newPassword } = req.body;
  const userId = req.session.userId!;
  const hash = await bcrypt.hash(newPassword, 10);
  await updateUserPassword(userId, hash);
  await setUserForcePasswordChange(userId, false);
  req.session.mustChangePassword = false;
  res.redirect('/');
});

app.post('/change-password', ensureAuth, async (req, res) => {
  const { currentPassword, newPassword } = req.body;
  const user = await getUserById(req.session.userId!);
  if (!user || !(await bcrypt.compare(currentPassword, user.password_hash))) {
    req.session.passwordError = 'Invalid current password';
    return res.redirect('/admin#account');
  }
  const hash = await bcrypt.hash(newPassword, 10);
  await updateUserPassword(user.id, hash);
  req.session.passwordSuccess = 'Password updated';
  res.redirect('/admin#account');
});

app.post('/change-name', ensureAuth, async (req, res) => {
  const { firstName, lastName } = req.body;
  if (!firstName || !lastName) {
    req.session.nameError = 'First name and last name are required';
    return res.redirect('/admin#account');
  }
  await updateUserName(req.session.userId!, firstName, lastName);
  req.session.nameSuccess = 'Name updated successfully';
  res.redirect('/admin#account');
});

app.post('/change-mobile', ensureAuth, async (req, res) => {
  const mobilePhone = (req.body.mobilePhone || '').trim();
  if (!mobilePhone) {
    req.session.mobileError = 'Mobile phone is required';
    return res.redirect('/admin#account');
  }
  await updateUserMobile(req.session.userId!, mobilePhone);
  req.session.mobileSuccess = 'Mobile phone updated';
  res.redirect('/admin#account');
});

app.get('/', ensureAuth, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  if (!req.session.companyId && companies.length > 0) {
    req.session.companyId = companies[0].company_id;
  }
  const company = req.session.companyId
    ? await getCompanyById(req.session.companyId)
    : null;
  const current = companies.find((c) => c.company_id === req.session.companyId);
  let activeUsers = 0;
  let licenseStats: { name: string; count: number; used: number; unused: number }[] = [];
  let assetCount = 0;
  let paidInvoices = 0;
  let unpaidInvoices = 0;

  if (company) {
    if ((current?.staff_permission ?? 0) > 0 || Number(req.session.userId) === 1) {
      const staff = await getStaffByCompany(company.id);
      activeUsers = staff.filter((s) => s.enabled === 1).length;
    }

    if (current?.can_manage_licenses || Number(req.session.userId) === 1) {
      const licenses = await getLicensesByCompany(company.id);
      licenseStats = licenses.map((l) => {
        const used = l.allocated || 0;
        return { name: l.display_name || l.platform, count: l.count, used, unused: l.count - used };
      });
    }

    if (current?.can_manage_assets || Number(req.session.userId) === 1) {
      const assets = await getAssetsByCompany(company.id);
      assetCount = assets.length;
    }

    if (current?.can_manage_invoices || Number(req.session.userId) === 1) {
      const invoices = await getInvoicesByCompany(company.id);
      paidInvoices = invoices.filter(
        (i) => i.status.toLowerCase() === 'paid'
      ).length;
      unpaidInvoices = invoices.length - paidInvoices;
    }
  }

  res.render('business', {
    company,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
    activeUsers,
    licenseStats,
    assetCount,
    paidInvoices,
    unpaidInvoices,
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

async function ensureStaffAccess(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  if (Number(req.session.userId) === 1) {
    return next();
  }
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if ((current?.staff_permission ?? 0) > 0) {
    return next();
  }
  return res.redirect('/');
}

async function ensureOfficeGroupAccess(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  if (Number(req.session.userId) === 1) {
    return next();
  }
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.can_manage_office_groups) {
    return next();
  }
  return res.redirect('/');
}

async function ensureAssetsAccess(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.can_manage_assets) {
    return next();
  }
  return res.redirect('/');
}

async function ensureInvoicesAccess(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.can_manage_invoices) {
    return next();
  }
  return res.redirect('/');
}

async function ensureShopAccess(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (current && current.can_access_shop) {
    return next();
  }
  return res.redirect('/');
}
app.get('/licenses', ensureAuth, ensureLicenseAccess, async (req, res) => {
  const licenses = await getLicensesByCompany(req.session.companyId!);
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('licenses', {
    licenses,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    companies,
    currentCompanyId: req.session.companyId,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get('/m365', ensureAuth, ensureLicenseAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const credential = await getM365Credentials(req.session.companyId!);
  const error = req.query.error as string | undefined;
  res.render('m365', {
    credential,
    error,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    companies,
    currentCompanyId: req.session.companyId,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get('/m365/admin', ensureAuth, ensureSuperAdmin, (req, res) => {
  res.redirect('/admin#m365-admin');
});

app.post('/m365/admin/:companyId', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { tenantId, clientId, clientSecret } = req.body;
  const secret = encryptSecret(clientSecret);
  await upsertM365Credentials(
    parseInt(req.params.companyId, 10),
    tenantId,
    clientId,
    secret
  );
  res.redirect('/admin#m365-admin');
});

app.post('/m365/admin/:companyId/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteM365Credentials(parseInt(req.params.companyId, 10));
  res.redirect('/admin#m365-admin');
});

app.get('/m365/admin/:companyId/authorize', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { M365_ADMIN_CLIENT_ID, M365_ADMIN_CLIENT_SECRET } = process.env;
  if (!M365_ADMIN_CLIENT_ID || !M365_ADMIN_CLIENT_SECRET) {
    return res.status(500).send('Missing Azure AD admin credentials');
  }
  const companyId = parseInt(req.params.companyId, 10);
  const cca = new ConfidentialClientApplication({
    auth: {
      clientId: M365_ADMIN_CLIENT_ID,
      authority: 'https://login.microsoftonline.com/common',
      clientSecret: M365_ADMIN_CLIENT_SECRET,
    },
  });
  const authUrl = await cca.getAuthCodeUrl({
    scopes: ['offline_access', 'Application.ReadWrite.All', 'AppRoleAssignment.ReadWrite.All'],
    redirectUri: `${req.protocol}://${req.get('host')}/m365/admin/callback`,
    state: String(companyId),
  });
  res.redirect(authUrl);
});

app.get('/m365/admin/callback', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { M365_ADMIN_CLIENT_ID, M365_ADMIN_CLIENT_SECRET } = process.env;
  if (!M365_ADMIN_CLIENT_ID || !M365_ADMIN_CLIENT_SECRET) {
    return res.status(500).send('Missing Azure AD admin credentials');
  }
  const code = req.query.code as string;
  const state = req.query.state as string;
  const companyId = parseInt(state, 10);
  const cca = new ConfidentialClientApplication({
    auth: {
      clientId: M365_ADMIN_CLIENT_ID,
      authority: 'https://login.microsoftonline.com/common',
      clientSecret: M365_ADMIN_CLIENT_SECRET,
    },
  });
  try {
    const token: any = await cca.acquireTokenByCode({
      code,
      scopes: ['offline_access', 'Application.ReadWrite.All', 'AppRoleAssignment.ReadWrite.All'],
      redirectUri: `${req.protocol}://${req.get('host')}/m365/admin/callback`,
    });
    const tenantId = token.idTokenClaims?.tid as string;
    const graphClient = Client.init({
      authProvider: (done) => done(null, token.accessToken),
    });
    const spRes = await graphClient
      .api('/servicePrincipals')
      .filter("appId eq '00000003-0000-0000-c000-000000000000'")
      .get();
    const graphSp = spRes.value[0];
    const requiredRoles = ['User.Read.All', 'Group.Read.All'];
    const resourceAccess = graphSp.appRoles
      .filter((r: any) => requiredRoles.includes(r.value))
      .map((r: any) => ({ id: r.id, type: 'Role' }));
    const app = await graphClient.api('/applications').post({
      displayName: `MyPortal ${companyId}`,
      requiredResourceAccess: [
        {
          resourceAppId: '00000003-0000-0000-c000-000000000000',
          resourceAccess,
        },
      ],
    });
    const password = await graphClient
      .api(`/applications/${app.id}/addPassword`)
      .post({ passwordCredential: { displayName: 'Client secret' } });
    const appSpRes = await graphClient
      .api('/servicePrincipals')
      .filter(`appId eq '${app.appId}'`)
      .get();
    const appSp = appSpRes.value[0];
    for (const ra of resourceAccess) {
      await graphClient
        .api(`/servicePrincipals/${appSp.id}/appRoleAssignments`)
        .post({
          principalId: appSp.id,
          resourceId: graphSp.id,
          appRoleId: ra.id,
        });
    }
    await upsertM365Credentials(
      companyId,
      tenantId,
      app.appId,
      encryptSecret(password.secretText)
    );
  } catch (err) {
    logError('Failed to authorize Microsoft 365', { err });
  }
  res.redirect('/admin#m365-admin');
});

app.get('/m365/connect', ensureAuth, ensureLicenseAccess, async (req, res) => {
  const companyId = req.session.companyId!;
  const creds = await getM365Credentials(companyId);
  if (!creds) return res.redirect('/m365');
  const appCca = new ConfidentialClientApplication({
    auth: {
      clientId: creds.client_id,
      authority: `https://login.microsoftonline.com/${creds.tenant_id}`,
      clientSecret: decryptSecret(creds.client_secret),
    },
  });
  const authUrl = await appCca.getAuthCodeUrl({
    scopes: ['openid', 'profile', 'offline_access', 'User.Read'],
    redirectUri: `${req.protocol}://${req.get('host')}/m365/callback`,
    state: String(companyId),
  });
  res.redirect(authUrl);
});

app.get('/m365/callback', async (req, res) => {
  const code = req.query.code as string;
  const state = req.query.state as string;
  const companyId = parseInt(state, 10);
  const creds = await getM365Credentials(companyId);
  if (!creds) return res.status(400).send('Missing credentials');
  const appCca = new ConfidentialClientApplication({
    auth: {
      clientId: creds.client_id,
      authority: `https://login.microsoftonline.com/${creds.tenant_id}`,
      clientSecret: decryptSecret(creds.client_secret),
    },
  });
  try {
    const token: any = await appCca.acquireTokenByCode({
      code,
      scopes: ['openid', 'profile', 'offline_access', 'User.Read', 'https://graph.microsoft.com/.default'],
      redirectUri: `${req.protocol}://${req.get('host')}/m365/callback`,
    });
    await upsertM365Credentials(
      companyId,
      creds.tenant_id,
      creds.client_id,
      creds.client_secret,
      token?.refreshToken ? encryptSecret(token.refreshToken) : null,
      token?.accessToken ? encryptSecret(token.accessToken) : null,
      token?.expiresOn
        ? token.expiresOn.toISOString().slice(0, 19).replace('T', ' ')
        : null
    );
    return res.redirect('/m365');
  } catch (err) {
    logError('Failed to complete Microsoft 365 OAuth', {
      error: err,
      companyId,
    });
    const message = encodeURIComponent(
      'Authorization with Microsoft 365 failed. Please try again.'
    );
    return res.redirect(`/m365?error=${message}`);
  }
});

app.get(
  '/licenses/:id/allocated',
  ensureAuth,
  ensureLicenseAccess,
  async (req, res) => {
    const licenseId = parseInt(req.params.id, 10);
    const license = await getLicenseById(licenseId);
    if (!license || license.company_id !== req.session.companyId) {
      return res.status(404).json({ error: 'License not found' });
    }
    const staff = await getStaffForLicense(licenseId);
    res.json(staff);
  }
);

app.post('/licenses/:id/order', ensureAuth, async (req, res) => {
  const { quantity } = req.body;
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (!current || !current.can_order_licenses) {
    return res.status(403).json({ error: 'Not allowed' });
  }
  const { LICENSES_WEBHOOK_URL, LICENSES_WEBHOOK_API_KEY } = process.env;
  if (LICENSES_WEBHOOK_URL && LICENSES_WEBHOOK_API_KEY) {
    try {
      await fetch(LICENSES_WEBHOOK_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': LICENSES_WEBHOOK_API_KEY,
        },
        body: JSON.stringify({
          companyId: req.session.companyId,
          licenseId: parseInt(req.params.id, 10),
          quantity: parseInt(quantity, 10),
          action: 'order',
        }),
      });
    } catch (err) {
      console.error('Webhook error', err);
    }
  }
  res.json({ success: true });
});

app.post('/licenses/:id/remove', ensureAuth, async (req, res) => {
  const { quantity } = req.body;
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  if (!current || !current.can_order_licenses) {
    return res.status(403).json({ error: 'Not allowed' });
  }
  const { LICENSES_WEBHOOK_URL, LICENSES_WEBHOOK_API_KEY } = process.env;
  if (LICENSES_WEBHOOK_URL && LICENSES_WEBHOOK_API_KEY) {
    try {
      await fetch(LICENSES_WEBHOOK_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': LICENSES_WEBHOOK_API_KEY,
        },
        body: JSON.stringify({
          companyId: req.session.companyId,
          licenseId: parseInt(req.params.id, 10),
          quantity: parseInt(quantity, 10),
          action: 'remove',
        }),
      });
    } catch (err) {
      console.error('Webhook error', err);
    }
  }
  res.json({ success: true });
});

app.put('/licenses/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { name, platform, count, expiryDate, contractTerm } = req.body;
  if (!req.session.companyId) {
    return res.status(400).json({ error: 'No company selected' });
  }
  await updateLicense(
    parseInt(req.params.id, 10),
    req.session.companyId!,
    name,
    platform,
    count,
    expiryDate || null,
    contractTerm
  );
  res.json({ success: true });
});

app.delete('/licenses/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteLicense(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.get('/staff', ensureAuth, ensureStaffAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const enabledFilter = req.query.enabled as string | undefined;
  const departmentFilter = (req.query.department as string | undefined) || '';
  const enabledParam =
    enabledFilter === '1' ? true : enabledFilter === '0' ? false : undefined;
  const companyId = req.session.companyId;
  let staff = companyId ? await getStaffByCompany(companyId, enabledParam) : [];
  const current = companies.find((c) => c.company_id === companyId);
  const company = companyId ? await getCompanyById(companyId) : null;
  const staffPermission = current?.staff_permission ?? 0;
  let departments: string[] = [];
  if (staffPermission === 1 || staffPermission === 2) {
    const user = await getUserById(req.session.userId!);
    const currentStaff = staff.find(
      (s) => s.email && s.email.toLowerCase() === (user?.email || '').toLowerCase()
    );
    const userDept = currentStaff?.department || '';
    staff = staff.filter((s) => {
      if (staffPermission === 1) {
        return (
          !!userDept && !!s.department && s.department.toLowerCase() === userDept.toLowerCase()
        );
      }
      return (
        (!!userDept && !!s.department && s.department.toLowerCase() === userDept.toLowerCase()) ||
        !s.department
      );
    });
  } else if (staffPermission === 3) {
    if (departmentFilter) {
      staff = staff.filter((s) => s.department === departmentFilter);
    }
    departments = Array.from(
      new Set(staff.filter((s) => s.department).map((s) => s.department as string))
    ).sort();
  }
  res.render('staff', {
    staff,
    companies,
    currentCompanyId: companyId,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    isSuperAdmin: Number(req.session.userId) === 1,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: staffPermission > 0 ? 1 : 0,
    staffPermission,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
    enabledFilter: enabledFilter ?? '',
    departmentFilter,
    departments,
    syncroCompanyId: company?.syncro_company_id ?? null,
  });
});

app.post('/staff', ensureAuth, ensureAdmin, async (req, res) => {
  const {
    firstName,
    lastName,
    email,
    mobilePhone,
    dateOnboarded,
    dateOffboarded,
    enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
  } = req.body;
  if (req.session.companyId) {
    await addStaff(
      req.session.companyId,
      firstName,
      lastName,
      email,
      mobilePhone || null,
      toDate(dateOnboarded),
      toDateTime(dateOffboarded),
      !!enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
    null,
    null
  );
  }
  res.redirect('/staff');
});

app.put('/staff/:id', ensureAuth, ensureStaffAccess, async (req, res) => {
  const {
    firstName,
    lastName,
    email,
    mobilePhone,
    dateOnboarded,
    dateOffboarded,
    enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
    accountAction,
  } = req.body;
  if (!req.session.companyId) {
    return res.status(400).json({ error: 'No company selected' });
  }
  const id = parseInt(req.params.id, 10);
  const isSuperAdmin = Number(req.session.userId) === 1;
  let existing: any;
  if (!isSuperAdmin) {
    existing = await getStaffById(id);
    if (!existing) {
      return res.status(404).json({ error: 'Staff not found' });
    }
  }
  const accountActionValue = isSuperAdmin ? accountAction : existing!.account_action;
  await updateStaff(
    id,
    req.session.companyId,
    isSuperAdmin ? firstName : existing!.first_name,
    isSuperAdmin ? lastName : existing!.last_name,
    isSuperAdmin ? email : existing!.email,
    isSuperAdmin ? mobilePhone : existing!.mobile_phone || null,
    isSuperAdmin ? toDate(dateOnboarded) : existing!.date_onboarded,
    toDateTime(dateOffboarded),
    isSuperAdmin ? !!enabled : !!existing!.enabled,
    isSuperAdmin ? street : existing!.street,
    isSuperAdmin ? city : existing!.city,
    isSuperAdmin ? state : existing!.state,
    isSuperAdmin ? postcode : existing!.postcode,
    isSuperAdmin ? country : existing!.country,
    isSuperAdmin ? department : existing!.department,
    isSuperAdmin ? jobTitle : existing!.job_title,
    isSuperAdmin ? company : existing!.org_company,
    isSuperAdmin ? managerName : existing!.manager_name,
    accountActionValue,
    existing?.syncro_contact_id || null
  );
  res.json({ success: true });
});

app.delete('/staff/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteStaff(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.post('/staff/enabled', ensureAuth, ensureStaffAccess, async (req, res) => {
  const { staffId, enabled } = req.body;
  await updateStaffEnabled(parseInt(staffId, 10), !!enabled);
  res.redirect('/staff');
});

app.post('/staff/:id/verify', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  const staff = await getStaffById(id);
  if (!staff || !staff.mobile_phone) {
    return res.status(400).json({ error: 'No mobile phone for staff member' });
  }
  const code = Math.floor(100000 + Math.random() * 900000).toString();
  const admin = await getUserById(req.session.userId!);
  const adminName = admin
    ? [admin.first_name, admin.last_name].filter(Boolean).join(' ').trim()
    : '';
  await setStaffVerificationCode(id, code, adminName);
  const url = process.env.VERIFY_WEBHOOK_URL;
  const apiKey = process.env.VERIFY_API_KEY;
  let status: number | null = null;
  if (url) {
    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (apiKey) {
        headers.Authorization = apiKey;
      }
      const companyName = res.locals.siteSettings?.company_name || '';
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          mobilePhone: staff.mobile_phone,
          code,
          adminName,
          companyName,
        }),
      });
      status = response.status;
    } catch (err) {
      console.error('Verify webhook failed', err);
    }
  }
  res.json({ success: status === 202, status, code });
});

app.post('/staff/:id/invite', ensureAuth, ensureStaffAccess, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  const staff = await getStaffById(id);
  if (!staff || !staff.email) {
    return res.status(400).json({ error: 'No email for staff member' });
  }
  const existing = await getUserByEmail(staff.email);
  if (existing) {
    return res.status(400).json({ error: 'User already exists' });
  }
  const placeholderPassword = crypto.randomBytes(12).toString('base64url');
  const passwordHash = await bcrypt.hash(placeholderPassword, 10);
  const userId = await createUser(staff.email, passwordHash, staff.company_id);
  const token = crypto.randomBytes(32).toString('base64url');
  await createPasswordToken(
    userId,
    token,
    new Date(Date.now() + 60 * 60 * 1000)
  );
  await assignUserToCompany(
    userId,
    staff.company_id,
    false,
    0,
    false,
    false,
    false,
    false,
    false,
    false
  );
  await updateUserName(userId, staff.first_name || '', staff.last_name || '');
  const [template, siteSettings] = await Promise.all([
    getEmailTemplate('staff_invitation'),
    getSiteSettings(),
  ]);
  if (template) {
    const baseUrl =
      process.env.PORTAL_URL || `${req.protocol}://${req.get('host')}`;
    const portalUrl = `${baseUrl}/login`;
    const setupLink = `${baseUrl}/password-setup?token=${token}`;
    const html = template.body
      .replace(/\{\{companyName\}\}/g, siteSettings?.company_name || '')
      .replace(/\{\{setupLink\}\}/g, setupLink)
      .replace(/\{\{portalUrl\}\}/g, portalUrl)
      .replace(/\{\{loginLogo\}\}/g, siteSettings?.login_logo || '');
    const subject = template.subject.replace(
      /\{\{companyName\}\}/g,
      siteSettings?.company_name || ''
    );
    try {
      await sendEmail(staff.email, subject, html);
    } catch (err) {
      console.error('Failed to send invitation email', err);
    }
  }
  res.json({ success: true });
});

const assetColumns = [
  { key: 'name', label: 'Name' },
  { key: 'type', label: 'Type' },
  { key: 'serial_number', label: 'Serial Number' },
  { key: 'status', label: 'Status' },
  { key: 'os_name', label: 'OS Name' },
  { key: 'cpu_name', label: 'CPU Name' },
  { key: 'ram_gb', label: 'RAM (GB)' },
  { key: 'hdd_size', label: 'HDD Size' },
  { key: 'last_sync', label: 'Last Sync' },
  { key: 'motherboard_manufacturer', label: 'Motherboard Manufacturer' },
  { key: 'form_factor', label: 'Form Factor' },
  { key: 'last_user', label: 'Last User' },
  { key: 'approx_age', label: 'Approx Age' },
  { key: 'performance_score', label: 'Performance Score' },
  { key: 'warranty_status', label: 'Warranty Status' },
  { key: 'warranty_end_date', label: 'Warranty End Date' },
];

app.get('/assets', ensureAuth, ensureAssetsAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const assets = req.session.companyId
    ? await getAssetsByCompany(req.session.companyId)
    : [];
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('assets', {
    assets,
    columns: assetColumns,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.delete('/assets/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteAsset(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.get('/invoices', ensureAuth, ensureInvoicesAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const invoices = req.session.companyId
    ? await getInvoicesByCompany(req.session.companyId)
    : [];
  const current = companies.find((c) => c.company_id === req.session.companyId);
  res.render('invoices', {
    invoices,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.delete('/invoices/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteInvoice(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.get('/forms', ensureAuth, async (req, res) => {
  const { hydratedForms, companies, currentCompanyAssignment } = await buildFormsContext(
    req
  );
  req.session.hasForms = hydratedForms.length > 0;
  const current = currentCompanyAssignment;
  res.render('forms', {
    forms: hydratedForms,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get('/forms/embed/:id', ensureAuth, async (req, res) => {
  const formId = Number.parseInt(req.params.id, 10);
  if (!Number.isFinite(formId)) {
    return sendFormProxyError(res, 400, 'The requested form identifier is invalid.');
  }
  const { hydratedForms, portalBaseUrl } = await buildFormsContext(req);
  const target = hydratedForms.find((form) => form.id === formId);
  if (!target) {
    return sendFormProxyError(res, 404, 'The requested form is no longer assigned to your account.');
  }
  let resolvedUrl: URL;
  try {
    resolvedUrl = new URL(target.url, portalBaseUrl);
  } catch (error) {
    logError('Failed to resolve hydrated form URL', {
      formId,
      url: target.url,
      ...buildErrorMeta(error),
    });
    return sendFormProxyError(res, 400, 'The form URL could not be resolved.');
  }
  const portalOrigin = new URL(portalBaseUrl).origin;
  if (resolvedUrl.origin === portalOrigin) {
    return res.redirect(resolvedUrl.toString());
  }
  if (!canProxyFormUrl(resolvedUrl, portalOrigin)) {
    return sendFormProxyError(
      res,
      403,
      'This form cannot be embedded securely. Use the “Open form in new tab” button to continue.'
    );
  }
  try {
    const html = await fetchExternalForm(resolvedUrl);
    const responseHtml = injectBaseTag(html, resolvedUrl);
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Content-Security-Policy', FORM_PROXY_SUCCESS_CSP);
    res.type('html').send(responseHtml);
  } catch (error) {
    if (error instanceof ExternalFormFetchError) {
      logError('External form fetch error', {
        formId,
        url: resolvedUrl.toString(),
        statusCode: error.statusCode,
        ...buildErrorMeta(error),
      });
      return sendFormProxyError(res, error.statusCode, error.userMessage);
    }
    logError('Unexpected error while embedding form', {
      formId,
      url: resolvedUrl.toString(),
      ...buildErrorMeta(error),
    });
    return sendFormProxyError(
      res,
      502,
      'We could not load this form. Use the “Open form in new tab” button to continue.'
    );
  }
});

app.get('/forms/company', ensureAuth, ensureAdmin, async (req, res) => {
  const formId = req.query.formId ? parseInt(req.query.formId as string, 10) : NaN;
  const [forms, companies, users] = await Promise.all([
    getFormsByCompany(req.session.companyId!),
    getCompaniesForUser(req.session.userId!),
    getUserCompanyAssignments(req.session.companyId!),
  ]);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const permissions = !isNaN(formId)
    ? await getFormPermissions(formId, req.session.companyId!)
    : [];
  res.render('forms-company', {
    forms,
    users,
    companies,
    selectedFormId: isNaN(formId) ? null : formId,
    permissions,
    currentCompanyId: req.session.companyId,
    isAdmin: true,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.post('/forms/company', ensureAuth, ensureAdmin, async (req, res) => {
  const { formId } = req.body;
  let { userIds } = req.body as { userIds?: string | string[] };
  const ids = Array.isArray(userIds)
    ? userIds.map((id) => parseInt(id, 10))
    : userIds
    ? [parseInt(userIds, 10)]
    : [];
  const allowedForms = await getFormsByCompany(req.session.companyId!);
  const idNum = parseInt(formId, 10);
  if (!allowedForms.some((f) => f.id === idNum)) {
    return res.redirect('/forms/company');
  }
  await updateFormPermissions(idNum, req.session.companyId!, ids);
  res.redirect(`/forms/company?formId=${formId}`);
});

app.get('/shop', ensureAuth, ensureShopAccess, async (req, res) => {
  const categoryId = req.query.category
    ? parseInt(req.query.category as string, 10)
    : undefined;
  const showOutOfStock = req.query.showOutOfStock === '1';
  const [products, companies, categories] = await Promise.all([
    getAllProducts(false, req.session.companyId, categoryId),
    getCompaniesForUser(req.session.userId!),
    getAllCategories(),
  ]);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const isVip = current?.is_vip === 1;
  const filtered = products.filter(
    (p) => p.name !== p.sku && (showOutOfStock || p.stock > 0)
  );
  const adjusted = filtered.map((p) => ({
    ...p,
    price: isVip && p.vip_price !== null ? p.vip_price : p.price,
  }));
  const error = req.session.cartError;
  req.session.cartError = undefined;
  res.render('shop', {
    products: adjusted,
    categories,
    currentCategory: categoryId,
    showOutOfStock,
    cartError: error,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.post('/cart/add', ensureAuth, ensureShopAccess, async (req, res) => {
  const { productId, quantity } = req.body;
  const product = await getProductById(
    parseInt(productId, 10),
    false,
    req.session.companyId
  );
  if (product) {
    const companies = await getCompaniesForUser(req.session.userId!);
    const current = companies.find((c) => c.company_id === req.session.companyId);
    const isVip = current?.is_vip === 1;
    const price = isVip && product.vip_price !== null ? product.vip_price : product.price;
    if (!req.session.cart) {
      req.session.cart = [];
    }
    const qty = parseInt(quantity, 10);
    const existing = req.session.cart.find((i) => i.productId === product.id);
    const existingQty = existing ? existing.quantity : 0;
    if (existingQty + qty > product.stock) {
      req.session.cartError = `Cannot add item. Only ${
        product.stock - existingQty
      } left in stock.`;
    } else {
      if (existing) {
        existing.quantity += qty;
      } else {
        req.session.cart.push({
          productId: product.id,
          name: product.name,
          sku: product.sku,
          vendorSku: product.vendor_sku,
          description: product.description,
          imageUrl: product.image_url,
          // Ensure price is stored as a number since MySQL may return strings
          price: Number(price),
          quantity: qty,
        });
      }
    }
  }
  res.redirect('/shop');
});

app.get('/cart', ensureAuth, ensureShopAccess, async (req, res) => {
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const message = req.session.orderMessage;
  req.session.orderMessage = undefined;
  const cart = req.session.cart || [];
  const total = cart.reduce(
    (sum, item) => sum + item.price * item.quantity,
    0
  );
  res.render('cart', {
    cart,
    total,
    orderMessage: message,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.post('/cart/remove', ensureAuth, ensureShopAccess, (req, res) => {
  const { remove } = req.body;
  if (req.session.cart && remove) {
    const toRemove = Array.isArray(remove)
      ? remove.map((id: string) => parseInt(id, 10))
      : [parseInt(remove, 10)];
    req.session.cart = req.session.cart.filter(
      (item) => !toRemove.includes(item.productId)
    );
  }
  res.redirect('/cart');
});

app.post('/cart/place-order', ensureAuth, ensureShopAccess, async (req, res) => {
  const { poNumber } = req.body;
  if (req.session.companyId && req.session.cart && req.session.cart.length > 0) {
    const { SHOP_WEBHOOK_URL, SHOP_WEBHOOK_API_KEY } = process.env;
    if (SHOP_WEBHOOK_URL && SHOP_WEBHOOK_API_KEY) {
      try {
        await fetch(SHOP_WEBHOOK_URL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-api-key': SHOP_WEBHOOK_API_KEY,
          },
          body: JSON.stringify({ cart: req.session.cart }),
        });
      } catch (err) {
        console.error('Failed to call webhook', err);
      }
    }
    let orderNumber = 'ORD';
    for (let i = 0; i < 12; i++) {
      orderNumber += Math.floor(Math.random() * 10).toString();
    }
    for (const item of req.session.cart) {
      await createOrder(
        req.session.userId!,
        req.session.companyId,
        item.productId,
        item.quantity,
        orderNumber,
        'pending',
        poNumber || null
      );
    }
    req.session.cart = [];
    req.session.orderMessage = 'Your order is being processed.';
  }
  res.redirect('/cart');
});

app.get('/orders', ensureAuth, ensureShopAccess, async (req, res) => {
  const orders = req.session.companyId
    ? await getOrderSummariesByCompany(req.session.companyId)
    : [];
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const statusCounts = orders.reduce((acc: Record<string, number>, o) => {
    acc[o.status] = (acc[o.status] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);
  const shippingStatusCounts = orders.reduce(
    (acc: Record<string, number>, o) => {
      acc[o.shipping_status] = (acc[o.shipping_status] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );
  const smsSubscriptions = await getSmsSubscriptionsForUser(
    req.session.userId!
  );
  res.render('orders', {
    orders,
    statusCounts,
    shippingStatusCounts,
    smsSubscriptions,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get('/orders/:orderNumber', ensureAuth, ensureShopAccess, async (req, res) => {
  const orderNumber = req.params.orderNumber;
  const items =
    req.session.companyId
      ? await getOrderItems(orderNumber, req.session.companyId)
      : [];
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const status = items[0]?.status || '';
  const notes = items[0]?.notes || '';
  const poNumber = items[0]?.po_number || '';
  const shippingStatus = items[0]?.shipping_status || '';
  const consignmentId = items[0]?.consignment_id || '';
  const eta = items[0]?.eta || null;
  const smsSubscribed = await isUserSubscribedToOrder(
    orderNumber,
    req.session.userId!
  );
  res.render('order-details', {
    orderNumber,
    items,
    status,
    notes,
    poNumber,
    shippingStatus,
    consignmentId,
    eta,
    smsSubscribed,
    companies,
    currentCompanyId: req.session.companyId,
    isAdmin: Number(req.session.userId) === 1 || (current?.is_admin ?? 0),
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.post(
  '/orders/:orderNumber/shipping',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    if (!req.session.companyId) {
      return res.redirect('/orders');
    }
    const { shippingStatus, consignmentId, eta } = req.body;
    await updateOrderShipping(
      req.params.orderNumber,
      req.session.companyId,
      shippingStatus,
      consignmentId || null,
      eta || null
    );
    await sendSmsUpdate(
      req.params.orderNumber,
      shippingStatus,
      eta || null
    );
    res.redirect(`/orders/${req.params.orderNumber}`);
  }
);

app.post('/orders/:orderNumber/sms', ensureAuth, async (req, res) => {
  const subscribe = !!req.body.subscribe;
  await setSmsSubscription(
    req.params.orderNumber,
    req.session.userId!,
    subscribe
  );
  if (subscribe) {
    const items = req.session.companyId
      ? await getOrderItems(req.params.orderNumber, req.session.companyId)
      : [];
    const shippingStatus = items[0]?.shipping_status || '';
    const eta = items[0]?.eta || null;
    await sendSmsUpdate(
      req.params.orderNumber,
      shippingStatus,
      eta ? eta.toISOString() : null
    );
  }
  res.json({ success: true });
});

app.post(
  '/orders/:orderNumber/delete',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const orderNumber = req.params.orderNumber;
    if (req.session.companyId) {
      await deleteOrder(orderNumber, req.session.companyId);
    }
    res.redirect('/orders');
  }
);

app.get('/shop/admin', ensureAuth, ensureSuperAdmin, (req, res) => {
  res.redirect('/admin');
});

app.post(
  '/shop/admin/product/import',
  ensureAuth,
  ensureSuperAdmin,
  async (req: express.Request, res: express.Response) => {
    const { vendor_sku } = req.body;
    try {
      await importProductByVendorSku(vendor_sku);
    } catch (err) {
      console.error('Product import failed', err);
    }
    res.redirect('/admin');
  }
);

app.post(
  '/shop/admin/product',
  ensureAuth,
  ensureSuperAdmin,
  uploadMiddleware,
  async (req: express.Request, res: express.Response) => {
    const { name, sku, vendor_sku, description, price, vip_price, stock, category_id } =
      req.body;
    const imageUrl = req.file ? `/uploads/${req.file.filename}` : null;
    const createdProductId = await createProduct(
      name,
      sku,
      vendor_sku,
      description,
      imageUrl,
      parseFloat(price),
      vip_price ? parseFloat(vip_price) : null,
      parseInt(stock, 10),
      category_id ? parseInt(category_id, 10) : null
    );
    const createdProduct = await getProductById(createdProductId, true);
    if (createdProduct) {
      await handleProductPricingAlert(createdProduct);
    }
    const trimmedName = typeof name === 'string' ? name.trim() : '';
    const trimmedVendorSku =
      typeof vendor_sku === 'string' ? vendor_sku.trim() : '';
    const normalizedName = trimmedName ? trimmedName.toLowerCase() : '';
    const normalizedVendorSku = trimmedVendorSku
      ? trimmedVendorSku.toLowerCase()
      : '';
    if (normalizedName && normalizedName === normalizedVendorSku) {
      try {
        await importProductByVendorSku(trimmedVendorSku);
      } catch (err) {
        console.error(
          `Automatic stock feed update failed for product ${createdProductId}`,
          err
        );
      }
    }
    res.redirect('/admin');
  }
);

app.post(
  '/shop/admin/product/:id',
  ensureAuth,
  ensureSuperAdmin,
  uploadMiddleware,
  async (req: express.Request, res: express.Response) => {
    const { name, sku, vendor_sku, description, price, vip_price, stock, category_id } =
      req.body;
    const imageUrl = req.file ? `/uploads/${req.file.filename}` : null;
    await updateProduct(
      parseInt(req.params.id, 10),
      name,
      sku,
      vendor_sku,
      description,
      imageUrl,
      parseFloat(price),
      vip_price ? parseFloat(vip_price) : null,
      parseInt(stock, 10),
      category_id ? parseInt(category_id, 10) : null
    );
    const updatedProduct = await getProductById(parseInt(req.params.id, 10), true);
    if (updatedProduct) {
      await handleProductPricingAlert(updatedProduct);
    }
    res.redirect('/admin');
  }
);

app.post('/shop/admin/product/:id/archive', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await archiveProduct(parseInt(req.params.id, 10));
  res.redirect('/admin');
});

app.post('/shop/admin/product/:id/unarchive', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await unarchiveProduct(parseInt(req.params.id, 10));
  res.redirect('/admin?showArchived=1');
});

app.post('/shop/admin/product/:id/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteProduct(parseInt(req.params.id, 10));
  res.redirect('/admin');
});

app.post(
  '/shop/admin/product/:id/visibility',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const body = req.body.excluded;
    const ids = Array.isArray(body) ? body : body ? [body] : [];
    const companyIds = ids.map((id: string) => parseInt(id, 10)).filter((n) => !isNaN(n));
    await setProductCompanyExclusions(
      parseInt(req.params.id, 10),
      companyIds
    );
    res.redirect('/admin');
  }
);

app.post(
  '/shop/admin/category',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const { name } = req.body;
    if (name) {
      await createCategory(name);
    }
    res.redirect('/admin');
  }
);

app.post(
  '/shop/admin/category/:id/delete',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    await deleteCategory(parseInt(req.params.id, 10));
    res.redirect('/admin');
  }
);

app.post('/switch-company', ensureAuth, async (req, res) => {
  const { companyId } = req.body;
  const companies = await getCompaniesForUser(req.session.userId!);
  if (companies.some((c) => c.company_id === parseInt(companyId, 10))) {
    req.session.companyId = parseInt(companyId, 10);
  }
  res.redirect('/');
});

app.get('/forms/admin', ensureAuth, ensureSuperAdmin, (req, res) => {
  const params = new URLSearchParams(req.query as Record<string, string>);
  const query = params.toString();
  res.redirect(`/admin${query ? '?' + query : ''}`);
});

app.post('/forms/admin', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { name, url, description } = req.body;
  await createForm(name, url, description);
  res.redirect('/admin');
});

app.post('/forms/admin/permissions', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { formId, companyId } = req.body;
  let { userIds } = req.body as { userIds?: string | string[] };
  const ids = Array.isArray(userIds)
    ? userIds.map((id) => parseInt(id, 10))
    : userIds
    ? [parseInt(userIds, 10)]
    : [];
  await updateFormPermissions(
    parseInt(formId, 10),
    parseInt(companyId, 10),
    ids
  );
  res.redirect(`/admin?formId=${formId}&companyId=${companyId}`);
});

app.post('/forms/admin/edit', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { id, name, url, description } = req.body;
  await updateForm(parseInt(id, 10), name, url, description);
  res.redirect('/admin');
});

app.post('/forms/admin/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { id } = req.body;
  await deleteForm(parseInt(id, 10));
  res.redirect('/admin');
});

app.post(
  '/forms/admin/permissions/delete',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const { formId, userId, companyId } = req.body;
    await deleteFormPermission(
      parseInt(formId, 10),
      parseInt(userId, 10),
      parseInt(companyId, 10)
    );
    res.redirect('/admin');
  }
);

  app.post('/apps', ensureAuth, ensureSuperAdmin, async (req, res) => {
    const { sku, vendorSku, name } = req.body;
    await createApp(
      sku,
      vendorSku || null,
      name
    );
    res.redirect('/admin#apps');
  });

app.post('/apps/prices', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { appId, paymentTerm, contractTerm, price } = req.body;
  await addAppPriceOption(
    parseInt(appId, 10),
    paymentTerm,
    contractTerm,
    parseFloat(price)
  );
  res.redirect('/admin#apps');
});

app.post('/apps/prices/:id/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteAppPriceOption(parseInt(req.params.id, 10));
  res.redirect('/admin#apps');
});

app.post('/apps/price', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { companyId, combo, price } = req.body;
  const [appId, paymentTerm, contractTerm] = combo.split('|');
  await upsertCompanyAppPrice(
    parseInt(companyId, 10),
    parseInt(appId, 10),
    paymentTerm,
    contractTerm,
    parseFloat(price)
  );
  res.redirect('/admin#apps');
});

app.post('/apps/price/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { companyId, appId, paymentTerm, contractTerm } = req.body;
  await deleteCompanyAppPrice(
    parseInt(companyId, 10),
    parseInt(appId, 10),
    paymentTerm,
    contractTerm
  );
  res.redirect('/admin#apps');
});

  app.post('/apps/:id/update', ensureAuth, ensureSuperAdmin, async (req, res) => {
    const { sku, vendorSku, name } = req.body;
    await updateApp(
      parseInt(req.params.id, 10),
      sku,
      vendorSku || null,
      name
    );
    res.redirect('/admin#apps');
  });

app.post('/apps/:id/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteApp(parseInt(req.params.id, 10));
  res.redirect('/admin#apps');
});

app.post('/apps/:appId/add', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const appId = parseInt(req.params.appId, 10);
  const { companyId, quantity, contractTerm } = req.body;
  const appInfo = await getAppById(appId);
  if (!appInfo) {
    res.status(404).send('App not found');
    return;
  }
  await createLicense(
    parseInt(companyId, 10),
    appInfo.name,
    appInfo.sku,
    parseInt(quantity, 10),
    null,
    contractTerm
  );
  res.status(204).end();
});

app.get('/admin', ensureAuth, async (req, res) => {
  const isSuperAdmin = Number(req.session.userId) === 1;
  const formId = req.query.formId ? parseInt(req.query.formId as string, 10) : NaN;
  const companyIdParam = req.query.companyId ? parseInt(req.query.companyId as string, 10) : NaN;
  const includeArchived = req.query.showArchived === '1';
  let allCompanies: Company[] = [];
  let users: User[] = [];
  let assignments: UserCompany[] = [];
  let apiKeys: ApiKeyWithUsage[] = [];
  let apps: App[] = [];
  let appPrices: AppPriceOption[] = [];
  let companyPrices: any[] = [];
  let forms: any[] = [];
  let formUsers: UserCompany[] = [];
  let permissions: number[] = [];
  let formAccess: any[] = [];
  let categories: Category[] = [];
  let products: any[] = [];
  let productRestrictions: Record<number, ProductCompanyRestriction[]> = {};
  let tasks: any[] = [];
  let systemTasks: any[] = [];
  let companyTasks: any[] = [];
  let emailTemplate: EmailTemplate | null = null;
  if (isSuperAdmin) {
    allCompanies = await getAllCompanies();
    users = await getAllUsers();
    assignments = await getUserCompanyAssignments();
    apiKeys = await getApiKeysWithUsage();
    apps = await getAllApps();
    appPrices = await getAppPriceOptions();
    companyPrices = await getCompanyAppPrices();
    forms = await getAllForms();
    formAccess = await getAllFormPermissionEntries();
    if (!isNaN(formId) && !isNaN(companyIdParam)) {
      formUsers = await getUserCompanyAssignments(companyIdParam);
      permissions = await getFormPermissions(formId, companyIdParam);
    }
    const [prodList, restrictionsList, catList] = await Promise.all([
      getAllProducts(includeArchived),
      getProductCompanyRestrictions(),
      getAllCategories(),
    ]);
    products = prodList;
    categories = catList;
    restrictionsList.forEach((r) => {
      if (!productRestrictions[r.product_id]) {
        productRestrictions[r.product_id] = [];
      }
      productRestrictions[r.product_id].push(r);
    });
    const rawTasks = await getScheduledTasks();
    tasks = rawTasks.map((t) => ({
      ...t,
      company_name:
        allCompanies.find((c) => c.id === t.company_id)?.name || null,
    }));
    systemTasks = tasks.filter((t) => t.company_id === null);
    companyTasks = tasks.filter((t) => t.company_id !== null);
    emailTemplate = await getEmailTemplate('staff_invitation');
  } else {
    const companyId = req.session.companyId!;
    const company = await getCompanyById(companyId);
    allCompanies = company ? [company] : [];
    users = [];
    assignments = await getUserCompanyAssignments(companyId);
    forms = await getAllForms();
    formAccess = await getAllFormPermissionEntries();
    if (!isNaN(formId) && !isNaN(companyIdParam)) {
      formUsers = await getUserCompanyAssignments(companyIdParam);
      permissions = await getFormPermissions(formId, companyIdParam);
    }
  }
  let credentials: Record<number, { tenant_id: string; client_id: string }> = {};
  if (isSuperAdmin) {
    for (const c of allCompanies) {
      const cred = await getM365Credentials(c.id);
      if (cred) {
        credentials[c.id] = { tenant_id: cred.tenant_id, client_id: cred.client_id };
      }
    }
  }
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const currentUser = await getUserById(req.session.userId!);
  const totpAuthenticators = await getUserTotpAuthenticators(req.session.userId!);
  let newTotpQr: string | null = null;
  let newTotpSecret = req.session.newTotpSecret || null;
  let newTotpName = req.session.newTotpName || '';
  const totpError = req.session.newTotpError || '';
  if (newTotpSecret && currentUser) {
    const otpauth = authenticator.keyuri(
      currentUser.email,
      'MyPortal',
      newTotpSecret
    );
    newTotpQr = await QRCode.toDataURL(otpauth);
  }
  const passwordError = req.session.passwordError || '';
  const passwordSuccess = req.session.passwordSuccess || '';
  const nameError = req.session.nameError || '';
  const nameSuccess = req.session.nameSuccess || '';
  const mobileError = req.session.mobileError || '';
  const mobileSuccess = req.session.mobileSuccess || '';
  req.session.passwordError = undefined;
  req.session.passwordSuccess = undefined;
  req.session.newTotpError = undefined;
  req.session.nameError = undefined;
  req.session.nameSuccess = undefined;
  req.session.mobileError = undefined;
  req.session.mobileSuccess = undefined;
  const isAdmin = Number(req.session.userId) === 1 || (current?.is_admin ?? 0);
  const priceAlerts: ProductPriceAlertWithProduct[] = isSuperAdmin
    ? await getActiveProductPriceAlerts()
    : [];
  res.render('admin', {
    allCompanies,
    users,
    assignments,
    apiKeys,
    apps,
    appPrices,
    companyPrices,
    forms,
    formUsers,
    permissions,
    formAccess,
    categories,
    products,
    productRestrictions,
    tasks,
    systemTasks,
    companyTasks,
    credentials,
    showArchived: includeArchived,
    selectedFormId: isNaN(formId) ? null : formId,
    selectedCompanyId: isNaN(companyIdParam) ? null : companyIdParam,
    isAdmin,
    isSuperAdmin,
    companies,
    currentCompanyId: req.session.companyId,
    currentUserId: req.session.userId,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
    totpAuthenticators,
    newTotpQr,
    newTotpSecret,
    newTotpName,
    totpError,
    passwordError,
    passwordSuccess,
    nameError,
    nameSuccess,
    mobileError,
    mobileSuccess,
    currentUserFirstName: currentUser?.first_name || '',
    currentUserLastName: currentUser?.last_name || '',
    currentUserMobilePhone: currentUser?.mobile_phone || '',
    siteSettings: res.locals.siteSettings,
    emailTemplate,
    priceAlerts,
  });
});

app.post('/admin/totp/start', ensureAuth, (req, res) => {
  const { name } = req.body;
  req.session.newTotpName = name;
  req.session.newTotpSecret = authenticator.generateSecret();
  res.redirect('/admin#account');
});

app.post('/admin/totp/verify', ensureAuth, async (req, res) => {
  if (!req.session.newTotpSecret || !req.session.newTotpName) {
    return res.redirect('/admin#account');
  }
  const valid = authenticator.verify({
    token: req.body.token,
    secret: req.session.newTotpSecret,
  });
  if (valid) {
    await addUserTotpAuthenticator(
      req.session.userId!,
      req.session.newTotpName,
      req.session.newTotpSecret
    );
    req.session.newTotpSecret = undefined;
    req.session.newTotpName = undefined;
    return res.redirect('/admin#account');
  }
  req.session.newTotpError = 'Invalid code';
  res.redirect('/admin#account');
});

app.post('/admin/totp/cancel', ensureAuth, (req, res) => {
  req.session.newTotpSecret = undefined;
  req.session.newTotpName = undefined;
  req.session.newTotpError = undefined;
  res.redirect('/admin#account');
});

app.post('/admin/totp/:id/delete', ensureAuth, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  await deleteUserTotpAuthenticator(id);
  res.redirect('/admin#account');
});

app.get('/audit-logs', ensureAuth, ensureAdmin, async (req, res) => {
  const isSuperAdmin = Number(req.session.userId) === 1;
  const companiesForSidebar = await getCompaniesForUser(req.session.userId!);
  const current = companiesForSidebar.find((c) => c.company_id === req.session.companyId);
  let selectedCompanyId: number | null = null;
  let logs: AuditLog[] = [];
  let allCompanies: Company[] = [];
  if (isSuperAdmin) {
    allCompanies = await getAllCompanies();
    selectedCompanyId = req.query.companyId ? parseInt(req.query.companyId as string, 10) : null;
    logs = await getAuditLogs(selectedCompanyId || undefined);
  } else {
    selectedCompanyId = req.session.companyId || null;
    if (selectedCompanyId) {
      logs = await getAuditLogs(selectedCompanyId);
    }
  }
  res.render('audit-logs', {
    logs,
    isSuperAdmin,
    companies: companiesForSidebar,
    filterCompanies: allCompanies,
    selectedCompanyId,
    currentCompanyId: req.session.companyId,
    isAdmin: true,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
  });
});

app.get('/office-groups', ensureAuth, ensureOfficeGroupAccess, async (req, res) => {
  const isSuperAdmin = Number(req.session.userId) === 1;
  const [officeGroups, staff] = await Promise.all([
    getOfficeGroupsByCompany(req.session.companyId!),
    getStaffByCompany(req.session.companyId!),
  ]);
  const companies = await getCompaniesForUser(req.session.userId!);
  const current = companies.find((c) => c.company_id === req.session.companyId);
  const isAdmin = Number(req.session.userId) === 1 || (current?.is_admin ?? 0);
  res.render('office-groups', {
    isAdmin,
    isSuperAdmin,
    companies,
    currentCompanyId: req.session.companyId,
    canManageLicenses: current?.can_manage_licenses ?? 0,
    canManageStaff: current?.staff_permission ? 1 : 0,
    staffPermission: current?.staff_permission ?? 0,
    canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
    canManageAssets: current?.can_manage_assets ?? 0,
    canManageInvoices: current?.can_manage_invoices ?? 0,
    canOrderLicenses: current?.can_order_licenses ?? 0,
    canAccessShop: current?.can_access_shop ?? 0,
    officeGroups,
    staff,
  });
});

app.post('/admin/company', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { name, isVip, syncroCompanyId, xeroId } = req.body;
  const companyId = await createCompany(
    name,
    undefined,
    parseCheckbox(isVip),
    syncroCompanyId,
    xeroId
  );
  await createDefaultSchedulesForCompany(companyId);
  res.redirect('/admin');
});

app.post('/admin/company/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { syncroCompanyId, xeroId, isVip } = req.body;
  await updateCompanyIds(
    parseInt(req.params.id, 10),
    syncroCompanyId || null,
    xeroId || null,
    parseCheckbox(isVip)
  );
  res.redirect('/admin');
});

app.get('/admin/schedules', ensureAuth, ensureSuperAdmin, (req, res) => {
  res.redirect('/admin#schedules');
});

app.post('/admin/schedules', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { command, cron: cronExpr, companyId } = req.body;
  const company = companyId ? parseInt(companyId, 10) : null;
  let name = command;
  if (company) {
    const c = await getCompanyById(company);
    if (c) {
      name = `${c.name} ${command}`;
    }
  }
  await createScheduledTask(company, name, command, cronExpr);
  await scheduleAllTasks();
  res.redirect('/admin#schedules');
});

app.post('/admin/schedules/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  const { cron: cronExpr } = req.body;
  const task = await getScheduledTask(id);
  if (task) {
    let name = task.command;
    if (task.company_id) {
      const c = await getCompanyById(task.company_id);
      if (c) {
        name = `${c.name} ${task.command}`;
      }
    }
    await updateScheduledTask(id, task.company_id, name, task.command, cronExpr);
  }
  await scheduleAllTasks();
  res.redirect('/admin#schedules');
});

app.post('/admin/schedules/:id/run', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  const task = await getScheduledTask(id);
  runScheduledTask(id).catch((err) => console.error('Scheduled task failed', err));
  if (task?.command === 'system_update') {
    res.render('update-progress');
  } else {
    res.redirect('/admin#schedules');
  }
});

app.get('/admin/system-update-status', ensureAuth, ensureSuperAdmin, (req, res) => {
  res.json({ complete: !systemUpdateInProgress });
});

app.post('/admin/schedules/:id/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  await deleteScheduledTask(id);
  await scheduleAllTasks();
  res.redirect('/admin#schedules');
});

app.get('/admin/syncro/customers', ensureAuth, ensureSuperAdmin, async (req, res) => {
  try {
    const showHidden = req.query.showHidden === '1';
    const [customers, hiddenIds, allCompanies, companies] = await Promise.all([
      getSyncroCustomers(),
      getHiddenSyncroCustomerIds(),
      getAllCompanies(),
      getCompaniesForUser(req.session.userId!),
    ]);
    const current = companies.find(
      (c) => c.company_id === req.session.companyId
    );
    const importedIds = allCompanies
      .filter((c) => c.syncro_company_id)
      .map((c) => c.syncro_company_id!);
    const visibleCustomers = showHidden
      ? customers
      : customers.filter((c) => !hiddenIds.includes(String(c.id)));
    res.render('syncro-customers', {
      customers: visibleCustomers,
      importedIds,
      hiddenIds,
      showHidden,
      companies,
      currentCompanyId: req.session.companyId,
      isAdmin: true,
      canManageLicenses: current?.can_manage_licenses ?? 0,
      canManageStaff: current?.staff_permission ? 1 : 0,
      staffPermission: current?.staff_permission ?? 0,
      canManageOfficeGroups: current?.can_manage_office_groups ?? 0,
      canManageAssets: current?.can_manage_assets ?? 0,
      canManageInvoices: current?.can_manage_invoices ?? 0,
      canAccessShop: current?.can_access_shop ?? 0,
    });
  } catch (err) {
    console.error('Failed to fetch Syncro customers', err);
    res.status(500).send('Failed to fetch Syncro customers');
  }
});

app.post('/admin/syncro/import', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { customerId, showHidden } = req.body;
  const redirectUrl = `/admin/syncro/customers${
    showHidden === '1' ? '?showHidden=1' : ''
  }`;
  if (!customerId) {
    return res.redirect(redirectUrl);
  }
  try {
    const customer = await getSyncroCustomer(customerId);
    if (customer) {
      const name =
        customer.business_name ||
        [customer.first_name, customer.last_name].filter(Boolean).join(' ') ||
        `Customer ${customer.id}`;
      const parts = [
        customer.address1,
        customer.address2,
        customer.city,
        customer.state,
        customer.zip,
      ].filter((p) => p);
      const address = parts.length ? parts.join(', ') : null;
      const existing = await getCompanyBySyncroId(String(customer.id));
      if (existing) {
        await updateCompany(existing.id, name, address);
      } else {
        const newId = await createCompany(
          name,
          address || undefined,
          false,
          String(customer.id)
        );
        await createDefaultSchedulesForCompany(newId);
      }
    }
  } catch (err) {
    console.error('Syncro import failed', err);
  }
  res.redirect(redirectUrl);
});

app.post(
  '/admin/syncro/import-contacts',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const { syncroCompanyId } = req.body;
    if (!syncroCompanyId) {
      return res.status(400).send('syncroCompanyId required');
    }
    try {
      const company = await getCompanyBySyncroId(syncroCompanyId);
      if (!company) {
        return res.status(404).send('Company not found');
      }
      const [contacts, existingStaff] = await Promise.all([
        getSyncroContacts(syncroCompanyId),
        getStaffByCompany(company.id),
      ]);
      for (const contact of contacts) {
        const fullName = [
          contact.first_name,
          contact.last_name,
          contact.name,
        ]
          .filter(Boolean)
          .join(' ')
          .trim();
        if (/ex staff/i.test(fullName)) {
          continue;
        }
        const firstName =
          contact.first_name || fullName.split(' ')[0] || '';
        const lastName =
          contact.last_name ||
          fullName
            .split(' ')
            .slice(1)
            .join(' ');
        const email = (contact as any).email || (contact as any).email_address || null;
        const phone = (contact as any).mobile || (contact as any).phone || null;
        const existing = findExistingStaff(
          existingStaff,
          firstName,
          lastName,
          email
        );
        if (existing) {
          await updateStaff(
            existing.id,
            company.id,
            firstName,
            lastName,
            email || existing.email,
            phone || existing.mobile_phone || null,
            existing.date_onboarded,
            existing.date_offboarded || null,
            existing.enabled === 1,
            existing.street || null,
            existing.city || null,
            existing.state || null,
            existing.postcode || null,
            existing.country || null,
            (contact as any).department || existing.department || null,
            existing.job_title || null,
            existing.org_company || null,
            existing.manager_name || null,
            existing.account_action || null,
            String(contact.id)
          );
        } else {
          await addStaff(
            company.id,
            firstName,
            lastName,
            email || '',
            phone || null,
            null,
            null,
            true,
            (contact as any).address1 || (contact as any).address || null,
            (contact as any).city || null,
            (contact as any).state || null,
            (contact as any).zip || null,
            (contact as any).country || null,
            (contact as any).department || null,
            (contact as any).title || null,
            null,
            null,
            String(contact.id)
          );
          existingStaff.push({
            id: 0,
            company_id: company.id,
            first_name: firstName,
            last_name: lastName,
            email: email || '',
            mobile_phone: phone || null,
            date_onboarded: null,
            date_offboarded: null,
            enabled: 1,
            street: (contact as any).address1 || (contact as any).address || null,
            city: (contact as any).city || null,
            state: (contact as any).state || null,
            postcode: (contact as any).zip || null,
            country: (contact as any).country || null,
            department: (contact as any).department || null,
            job_title: (contact as any).title || null,
            org_company: null,
            manager_name: null,
            account_action: null,
            syncro_contact_id: String(contact.id),
          } as any);
        }
      }
      res.sendStatus(200);
    } catch (err) {
      console.error('Syncro contacts import failed', err);
      res.status(500).send('Failed to import Syncro contacts');
    }
  }
);

app.post(
  '/admin/syncro/import-assets',
  ensureAuth,
  ensureSuperAdmin,
  async (req, res) => {
    const { syncroCompanyId } = req.body;
    if (!syncroCompanyId) {
      return res.status(400).send('syncroCompanyId required');
    }
    try {
      const company = await getCompanyBySyncroId(syncroCompanyId);
      if (!company) {
        return res.status(404).send('Company not found');
      }
      await importSyncroAssetsForCompany(company.id);
      res.sendStatus(200);
    } catch (err) {
      console.error('Syncro assets import failed', err);
      res.status(500).send('Failed to import Syncro assets');
    }
  }
);

app.post('/admin/syncro/hide', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { customerId, showHidden } = req.body;
  if (customerId) {
    await hideSyncroCustomer(customerId);
  }
  res.redirect(`/admin/syncro/customers${showHidden === '1' ? '?showHidden=1' : ''}`);
});

app.post('/admin/syncro/unhide', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { customerId, showHidden } = req.body;
  if (customerId) {
    await unhideSyncroCustomer(customerId);
  }
  res.redirect(`/admin/syncro/customers${showHidden === '1' ? '?showHidden=1' : ''}`);
});

app.post('/admin/user', ensureAuth, ensureAdmin, async (req, res) => {
  const { email, password } = req.body;
  const isSuperAdmin = Number(req.session.userId) === 1;
  const passwordHash = await bcrypt.hash(password, 10);
  const companyId = isSuperAdmin
    ? parseInt(req.body.companyId, 10)
    : req.session.companyId!;
  const userId = await createUser(email, passwordHash, companyId);
  await assignUserToCompany(userId, companyId, false, 0, false, false, false, false, false, false);
  res.redirect('/admin');
});

app.delete('/admin/user/:id', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteUser(parseInt(req.params.id, 10));
  res.json({ success: true });
});

app.post('/admin/invite', ensureAuth, ensureAdmin, async (req, res) => {
  const { email, firstName, lastName } = req.body;
  const isSuperAdmin = Number(req.session.userId) === 1;
  const companyId = isSuperAdmin
    ? parseInt(req.body.companyId, 10)
    : req.session.companyId!;
  const tempPassword = crypto.randomBytes(12).toString('base64url');
  const passwordHash = await bcrypt.hash(tempPassword, 10);
  const userId = await createUser(email, passwordHash, companyId, true);
  await assignUserToCompany(
    userId,
    companyId,
    false,
    0,
    false,
    false,
    false,
    false,
    false,
    false
  );
  if (firstName || lastName) {
    await updateUserName(userId, firstName || '', lastName || '');
  }
  const [template, siteSettings] = await Promise.all([
    getEmailTemplate('staff_invitation'),
    getSiteSettings(),
  ]);
  if (template) {
    const portalUrl =
      process.env.PORTAL_URL || `${req.protocol}://${req.get('host')}/login`;
    const html = template.body
      .replace(/\{\{companyName\}\}/g, siteSettings?.company_name || '')
      .replace(/\{\{tempPassword\}\}/g, tempPassword)
      .replace(/\{\{portalUrl\}\}/g, portalUrl)
      .replace(/\{\{loginLogo\}\}/g, siteSettings?.login_logo || '');
    const subject = template.subject.replace(
      /\{\{companyName\}\}/g,
      siteSettings?.company_name || ''
    );
    try {
      await sendEmail(email, subject, html);
    } catch (err) {
      console.error('Failed to send invitation email', err);
    }
  }
  res.redirect('/admin');
});

app.post('/admin/api-key', ensureAuth, ensureAdmin, async (req, res) => {
  const { description, expiryDate } = req.body;
  const key = await createApiKey(description, expiryDate);
  res.json({ key });
});

app.post('/admin/api-key/delete', ensureAuth, ensureAdmin, async (req, res) => {
  const { id } = req.body;
  await deleteApiKey(parseInt(id, 10));
  res.redirect('/admin');
});

app.post(
  '/admin/site-settings',
  ensureAuth,
  ensureSuperAdmin,
  memoryUpload.fields([
    { name: 'loginLogo', maxCount: 1 },
    { name: 'sidebarLogo', maxCount: 1 },
    { name: 'favicon', maxCount: 1 },
  ]),
  csrfProtection,
  async (req, res) => {
    const { companyName } = req.body;
    const files = req.files as {
      [fieldname: string]: Express.Multer.File[];
    };
    let loginLogo: string | undefined;
    let sidebarLogo: string | undefined;
    let favicon: string | undefined;
    if (files && files.loginLogo && files.loginLogo[0]) {
      const file = files.loginLogo[0];
      if (allowedMimes.includes(file.mimetype)) {
        loginLogo = `data:${file.mimetype};base64,${file.buffer.toString('base64')}`;
      } else {
        return res.status(400).send('Invalid file type');
      }
    }
    if (files && files.sidebarLogo && files.sidebarLogo[0]) {
      const file = files.sidebarLogo[0];
      if (allowedMimes.includes(file.mimetype)) {
        sidebarLogo = `data:${file.mimetype};base64,${file.buffer.toString('base64')}`;
      } else {
        return res.status(400).send('Invalid file type');
      }
    }
    if (files && files.favicon && files.favicon[0]) {
      const file = files.favicon[0];
      if (allowedMimes.includes(file.mimetype)) {
        favicon = `data:${file.mimetype};base64,${file.buffer.toString('base64')}`;
      } else {
        return res.status(400).send('Invalid file type');
      }
    }
    await updateSiteSettings(companyName, loginLogo, sidebarLogo, favicon);
    res.redirect('/admin#site-settings');
  }
);

app.get('/admin/email-templates', ensureAuth, ensureSuperAdmin, (req, res) => {
  res.redirect('/admin#email-templates');
});

app.post('/admin/email-templates', ensureAuth, ensureSuperAdmin, async (req, res) => {
  const { subject, body } = req.body;
  await upsertEmailTemplate('staff_invitation', subject, body);
  res.redirect('/admin#email-templates');
});

app.post('/admin/assign', ensureAuth, ensureAdmin, async (req, res) => {
  const { userId } = req.body;
  const companyId = Number(req.session.userId) === 1
    ? parseInt(req.body.companyId, 10)
    : req.session.companyId!;
  await assignUserToCompany(
    parseInt(userId, 10),
    companyId,
    false,
    0,
    false,
    false,
    false,
    false,
    false,
    false
  );
  res.redirect('/admin');
});

app.delete('/admin/assign/:companyId/:userId', ensureAuth, ensureSuperAdmin, async (
  req,
  res
) => {
  await unassignUserFromCompany(
    parseInt(req.params.userId, 10),
    parseInt(req.params.companyId, 10)
  );
  res.json({ success: true });
});

function parseCheckbox(value: unknown): boolean {
  if (Array.isArray(value)) {
    value = value[value.length - 1];
  }
  return value === '1' || value === 'on' || value === true;
}

app.post('/admin/permission', ensureAuth, ensureAdmin, async (req, res) => {
  const {
    userId,
    companyId,
    canManageLicenses,
    staffPermission,
    canManageOfficeGroups,
    canManageAssets,
    canManageInvoices,
    canOrderLicenses,
    canAccessShop,
    isAdmin: isAdminField,
  } = req.body;
  const uid = parseInt(userId, 10);
  const cid = Number(req.session.userId) === 1 ? parseInt(companyId, 10) : req.session.companyId!;
  if (typeof canManageLicenses !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_manage_licenses',
      parseCheckbox(canManageLicenses)
    );
  }
  if (typeof staffPermission !== 'undefined') {
    await updateUserCompanyStaffPermission(
      uid,
      cid,
      parseInt(staffPermission, 10)
    );
  }
  if (typeof canManageOfficeGroups !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_manage_office_groups',
      parseCheckbox(canManageOfficeGroups)
    );
  }
  if (typeof canManageAssets !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_manage_assets',
      parseCheckbox(canManageAssets)
    );
  }
  if (typeof canManageInvoices !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_manage_invoices',
      parseCheckbox(canManageInvoices)
    );
  }
  if (typeof canOrderLicenses !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_order_licenses',
      parseCheckbox(canOrderLicenses)
    );
  }
  if (typeof canAccessShop !== 'undefined') {
    await updateUserCompanyPermission(
      uid,
      cid,
      'can_access_shop',
      parseCheckbox(canAccessShop)
    );
  }
  if (typeof isAdminField !== 'undefined') {
    const isAdminValue = parseCheckbox(isAdminField);
    if (uid !== Number(req.session.userId) || Number(req.session.userId) === 1 || isAdminValue) {
      await updateUserCompanyPermission(
        uid,
        cid,
        'is_admin',
        isAdminValue
      );
    }
  }
  res.redirect('/admin');
});

app.post('/office-groups', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await createOfficeGroup(req.session.companyId!, req.body.name);
  res.redirect('/office-groups');
});

app.post(
  '/office-groups/:id/members',
  ensureAuth,
  ensureOfficeGroupAccess,
  async (req, res) => {
    const raw = req.body.staffIds;
    const ids = Array.isArray(raw)
      ? raw.map((s: string) => parseInt(s, 10))
      : raw
      ? [parseInt(raw, 10)]
      : [];
    await updateOfficeGroupMembers(parseInt(req.params.id, 10), ids);
    res.redirect('/office-groups');
  }
);

app.post('/office-groups/:id/delete', ensureAuth, ensureSuperAdmin, async (req, res) => {
  await deleteOfficeGroup(parseInt(req.params.id, 10));
  res.redirect('/office-groups');
});

const api = express.Router();

api.use(async (req, res, next) => {
  const key = req.header('x-api-key');
  if (!key) {
    return res.status(401).json({ error: 'API key required' });
  }
  const record = await getApiKeyRecord(key);
  if (!record) {
    return res.status(403).json({ error: 'Invalid API key' });
  }
  req.apiKey = key;
  const ip = (req.headers['cf-connecting-ip'] as string) || req.ip || '';
  await recordApiKeyUsage(record.id, ip);
  next();
});

/**
 * @openapi
 * /api/apps:
 *   get:
 *     tags:
 *       - Apps
 *     summary: List all apps
 *     responses:
 *       200:
 *         description: Array of apps
 *   post:
 *     tags:
 *       - Apps
 *     summary: Create a new app
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
  *             properties:
  *               sku:
  *                 type: string
  *               vendorSku:
  *                 type: string
  *               name:
  *                 type: string
  *     responses:
 *       200:
 *         description: App created
 */
api.route('/apps')
  .get(async (req, res) => {
    const apps = await getAllApps();
    res.json(apps);
  })
  .post(async (req, res) => {
      const { sku, vendorSku, name } = req.body;
      const id = await createApp(
        sku,
        vendorSku || null,
        name
      );
      res.json({ id });
    });

/**
 * @openapi
 * /api/apps/{id}:
 *   get:
 *     tags:
 *       - Apps
 *     summary: Get an app by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: App details
 *       404:
 *         description: App not found
 *   put:
 *     tags:
 *       - Apps
 *     summary: Update an app
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
  *             properties:
  *               sku:
  *                 type: string
  *               vendorSku:
  *                 type: string
  *               name:
  *                 type: string
  *     responses:
 *       200:
 *         description: Update successful
 *   delete:
 *     tags:
 *       - Apps
 *     summary: Delete an app
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.route('/apps/:id')
  .get(async (req, res) => {
    const app = await getAppById(parseInt(req.params.id, 10));
    if (!app) {
      return res.status(404).json({ error: 'App not found' });
    }
    res.json(app);
  })
    .put(async (req, res) => {
      const { sku, vendorSku, name } = req.body;
      await updateApp(
        parseInt(req.params.id, 10),
        sku,
        vendorSku || null,
        name
      );
      res.json({ success: true });
    })
  .delete(async (req, res) => {
    await deleteApp(parseInt(req.params.id, 10));
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/shop/products:
 *   get:
 *     tags:
 *       - Shop
 *     summary: List all products
 *     responses:
 *       200:
 *         description: Array of products
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   name:
 *                     type: string
 *                   sku:
 *                     type: string
 *                   vendor_sku:
 *                     type: string
 *                   description:
 *                     type: string
 *                   image_url:
 *                     type: string
 *                   price:
 *                     type: number
 *                   stock:
 *                     type: integer
 *                   category_id:
 *                     type: integer
 *                   category_name:
 *                     type: string
 *   post:
 *     tags:
 *       - Shop
 *     summary: Create a product
 *     requestBody:
 *       required: true
 *       content:
 *         multipart/form-data:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *               sku:
 *                 type: string
 *               vendor_sku:
 *                 type: string
 *               description:
 *                 type: string
 *               price:
 *                 type: number
 *               vip_price:
 *                 type: number
 *               stock:
 *                 type: integer
 *               category_id:
 *                 type: integer
 *               image:
 *                 type: string
 *                 format: binary
 *     responses:
 *       200:
 *         description: ID of created product
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 */
api.route('/shop/products')
  .get(async (req, res) => {
    const includeArchived = req.query.includeArchived === '1';
    const companyId = req.query.companyId
      ? parseInt(req.query.companyId as string, 10)
      : undefined;
    const categoryId = req.query.categoryId
      ? parseInt(req.query.categoryId as string, 10)
      : undefined;
    const products = await getAllProducts(
      includeArchived,
      companyId,
      categoryId
    );
    res.json(products);
  })
.post(uploadMiddleware, async (req: express.Request, res: express.Response) => {
    const { name, sku, vendor_sku, description, price, vip_price, stock, category_id } =
      req.body;
    const imageUrl = req.file ? `/uploads/${req.file.filename}` : null;
    const id = await createProduct(
      name,
      sku,
      vendor_sku,
      description,
      imageUrl,
      parseFloat(price),
      vip_price ? parseFloat(vip_price) : null,
      parseInt(stock, 10),
      category_id ? parseInt(category_id, 10) : null
    );
    res.json({ id });
  });

/**
 * @openapi
 * /api/shop/products/{id}:
 *   get:
 *     tags:
 *       - Shop
 *     summary: Get a product by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Product details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 name:
 *                   type: string
 *                 sku:
 *                   type: string
 *                 vendor_sku:
 *                   type: string
 *                 description:
 *                   type: string
 *                 image_url:
 *                   type: string
 *                 price:
 *                   type: number
 *                 stock:
 *                   type: integer
 *                 category_id:
 *                   type: integer
 *                 category_name:
 *                   type: string
 *       404:
 *         description: Product not found
 *   put:
 *     tags:
 *       - Shop
 *     summary: Update a product
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         multipart/form-data:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *               sku:
 *                 type: string
 *               vendor_sku:
 *                 type: string
 *               description:
 *                 type: string
 *               price:
 *                 type: number
 *               vip_price:
 *                 type: number
 *               stock:
 *                 type: integer
 *               category_id:
 *                 type: integer
 *               image:
 *                 type: string
 *                 format: binary
 *     responses:
 *       200:
 *         description: Update successful
 *   delete:
 *     tags:
 *       - Shop
 *     summary: Delete a product
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.route('/shop/products/:id')
  .get(async (req, res) => {
    const includeArchived = req.query.includeArchived === '1';
    const companyId = req.query.companyId
      ? parseInt(req.query.companyId as string, 10)
      : undefined;
    const product = await getProductById(
      parseInt(req.params.id, 10),
      includeArchived,
      companyId
    );
    if (!product) {
      return res.status(404).json({ error: 'Product not found' });
    }
    res.json(product);
  })
  .put(uploadMiddleware, async (req: express.Request, res: express.Response) => {
    const { name, sku, vendor_sku, description, price, vip_price, stock, category_id } =
      req.body;
    const imageUrl = req.file ? `/uploads/${req.file.filename}` : null;
    await updateProduct(
      parseInt(req.params.id, 10),
      name,
      sku,
      vendor_sku,
      description,
      imageUrl,
      parseFloat(price),
      vip_price ? parseFloat(vip_price) : null,
      parseInt(stock, 10),
      category_id ? parseInt(category_id, 10) : null
    );
    res.json({ success: true });
  })
  .delete(async (req, res) => {
    await deleteProduct(parseInt(req.params.id, 10));
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/shop/products/sku/{sku}:
 *   get:
 *     tags:
 *       - Shop
 *     summary: Get a product by SKU
 *     parameters:
 *       - in: path
 *         name: sku
 *         required: true
 *         schema:
 *           type: string
 *     responses:
 *       200:
 *         description: Product details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 name:
 *                   type: string
 *                 sku:
 *                   type: string
 *                 vendor_sku:
 *                   type: string
 *                 description:
 *                   type: string
 *                 image_url:
 *                   type: string
 *                 price:
 *                   type: number
 *                 stock:
 *                   type: integer
 *       404:
 *         description: Product not found
 */
api.get('/shop/products/sku/:sku', async (req, res) => {
  const includeArchived = req.query.includeArchived === '1';
  const companyId = req.query.companyId
    ? parseInt(req.query.companyId as string, 10)
    : undefined;
  const product = await getProductBySku(
    req.params.sku,
    includeArchived,
    companyId
  );
  if (!product) {
    return res.status(404).json({ error: 'Product not found' });
  }
  res.json(product);
});

/**
 * @openapi
 * /api/shop/categories:
 *   get:
 *     tags:
 *       - Shop
 *     summary: List all categories
 *     responses:
 *       200:
 *         description: Array of categories
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   name:
 *                     type: string
 *   post:
 *     tags:
 *       - Shop
 *     summary: Create a category
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *     responses:
 *       200:
 *         description: ID of created category
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 */
api.route('/shop/categories')
  .get(async (req, res) => {
    const categories = await getAllCategories();
    res.json(categories);
  })
  .post(async (req, res) => {
    const { name } = req.body;
    const id = await createCategory(name);
    res.json({ id });
  });

/**
 * @openapi
 * /api/shop/categories/{id}:
 *   get:
 *     tags:
 *       - Shop
 *     summary: Get a category by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Category details
 *   put:
 *     tags:
 *       - Shop
 *     summary: Update a category
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 *   delete:
 *     tags:
 *       - Shop
 *     summary: Delete a category
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.route('/shop/categories/:id')
  .get(async (req, res) => {
    const category = await getCategoryById(parseInt(req.params.id, 10));
    if (!category) {
      return res.status(404).json({ error: 'Category not found' });
    }
    res.json(category);
  })
  .put(async (req, res) => {
    const { name } = req.body;
    await updateCategory(parseInt(req.params.id, 10), name);
    res.json({ success: true });
  })
  .delete(async (req, res) => {
    await deleteCategory(parseInt(req.params.id, 10));
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/shop/orders:
 *   get:
 *     tags:
 *       - Shop
 *     summary: List order items for a company
 *     parameters:
 *       - in: query
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Array of order items
 *   post:
 *     tags:
 *       - Shop
 *     summary: Create an order item
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               userId:
 *                 type: integer
 *               companyId:
 *                 type: integer
 *               productId:
 *                 type: integer
 *               quantity:
 *                 type: integer
 *               orderNumber:
 *                 type: string
 *               poNumber:
 *                 type: string
 *               status:
 *                 type: string
 *     responses:
 *       200:
 *         description: Order item created
 */
api
  .route('/shop/orders')
  .get(async (req, res) => {
    const companyId = req.query.companyId;
    if (!companyId) {
      return res.status(400).json({ error: 'companyId required' });
    }
    const orders = await getOrdersByCompany(parseInt(companyId as string, 10));
    res.json(orders);
  })
  .post(async (req, res) => {
    const {
      userId,
      companyId,
      productId,
      quantity,
      orderNumber,
      poNumber,
      status,
    } = req.body;
    let num = orderNumber as string | undefined;
    if (!num) {
      num = 'ORD';
      for (let i = 0; i < 12; i++) {
        num += Math.floor(Math.random() * 10).toString();
      }
    }
    const uId = parseInt(userId, 10);
    const cId = parseInt(companyId, 10);
    const pId = parseInt(productId, 10);
    const qty = parseInt(quantity, 10);
    const { SHOP_WEBHOOK_URL, SHOP_WEBHOOK_API_KEY } = process.env;
    if (SHOP_WEBHOOK_URL && SHOP_WEBHOOK_API_KEY) {
      try {
        await fetch(SHOP_WEBHOOK_URL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-api-key': SHOP_WEBHOOK_API_KEY,
          },
          body: JSON.stringify({
            userId: uId,
            companyId: cId,
            productId: pId,
            quantity: qty,
            orderNumber: num,
          }),
        });
      } catch (err) {
        console.error('Webhook error', err);
      }
    }
    await createOrder(
      uId,
      cId,
      pId,
      qty,
      num,
      status || 'pending',
      poNumber || null
    );
    res.json({ success: true, orderNumber: num });
  });

/**
 * @openapi
 * /api/shop/orders/{orderNumber}:
 *   put:
 *     tags:
 *       - Shop
 *     summary: Update an order
 *     parameters:
 *       - in: path
 *         name: orderNumber
 *         required: true
 *         schema:
 *           type: string
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               companyId:
 *                 type: integer
 *               status:
 *                 type: string
 *               notes:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 *   delete:
 *     tags:
 *       - Shop
 *     summary: Delete an order
 *     parameters:
 *       - in: path
 *         name: orderNumber
 *         required: true
 *         schema:
 *           type: string
 *       - in: query
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api
  .route('/shop/orders/:orderNumber')
  .put(async (req, res) => {
    const { status, notes, companyId } = req.body;
    if (!companyId) {
      return res.status(400).json({ error: 'companyId required' });
    }
    await updateOrder(
      req.params.orderNumber,
      parseInt(companyId, 10),
      status,
      notes || null
    );
    res.json({ success: true });
  })
  .delete(async (req, res) => {
    const companyId = req.query.companyId;
    if (!companyId) {
      return res.status(400).json({ error: 'companyId required' });
    }
    await deleteOrder(
      req.params.orderNumber,
      parseInt(companyId as string, 10)
    );
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/shop/orders/consignment/{consignmentId}:
 *   get:
 *     tags:
 *       - Shop
 *     summary: Get orders by consignment ID
 *     parameters:
 *       - in: path
 *         name: consignmentId
 *         required: true
 *         schema:
 *           type: string
 *     responses:
 *       200:
 *         description: Orders retrieved
 *   put:
 *     tags:
 *       - Shop
 *     summary: Update shipping status by consignment ID
 *     parameters:
 *       - in: path
 *         name: consignmentId
 *         required: true
 *         schema:
 *           type: string
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               shippingStatus:
 *                 type: string
 *               eta:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api
  .route('/shop/orders/consignment/:consignmentId')
  .get(async (req, res) => {
    const orders = await getOrdersByConsignmentId(
      req.params.consignmentId
    );
    res.json(orders);
  })
  .put(async (req, res) => {
    const { shippingStatus, eta } = req.body;
    await updateShippingStatusByConsignmentId(
      req.params.consignmentId,
      shippingStatus,
      eta || null
    );
    const orders = await getOrdersByConsignmentId(req.params.consignmentId);
    for (const order of orders) {
      await sendSmsUpdate(
        order.order_number,
        shippingStatus,
        eta || null
      );
    }
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/apps/{appId}/companies/{companyId}/price:
 *   get:
 *     tags:
 *       - Apps
 *     summary: Get price for an app for a company
 *     parameters:
 *       - in: path
 *         name: appId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Effective price, returns default app price when no company-specific price exists
  *       404:
  *         description: App not found
 *   post:
 *     tags:
 *       - Apps
 *     summary: Set company-specific price for an app
 *     parameters:
 *       - in: path
 *         name: appId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               price:
 *                 type: number
 *     responses:
 *       200:
 *         description: Price set
 *   put:
 *     tags:
 *       - Apps
 *     summary: Update company-specific price for an app
 *     parameters:
 *       - in: path
 *         name: appId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               price:
 *                 type: number
 *     responses:
 *       200:
 *         description: Update successful
 *   delete:
 *     tags:
 *       - Apps
 *     summary: Delete company-specific price for an app
 *     parameters:
 *       - in: path
 *         name: appId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */

export async function getCompanyAppPriceHandler(
  req: express.Request,
  res: express.Response,
  deps: {
    getAppPrice: typeof getAppPrice;
    getAppPriceOption: typeof getAppPriceOption;
  } = {
    getAppPrice,
    getAppPriceOption,
  }
): Promise<void> {
  const companyId = parseInt(req.params.companyId, 10);
  const appId = parseInt(req.params.appId, 10);
  const paymentTerm = String(req.query.paymentTerm || 'monthly');
  const contractTerm = String(req.query.contractTerm || 'monthly');
  let price = await deps.getAppPrice(companyId, appId, paymentTerm, contractTerm);
  if (price === null) {
    const option = await deps.getAppPriceOption(appId, paymentTerm, contractTerm);
    if (!option) {
      res.status(404).json({ error: 'App price not found' });
      return;
    }
    price = option.price;
  }
  res.json({ price });
}

api
  .route('/apps/:appId/companies/:companyId/price')
  .get((req, res) => getCompanyAppPriceHandler(req, res))
  .post(async (req, res) => {
    const { price, paymentTerm, contractTerm } = req.body;
    await upsertCompanyAppPrice(
      parseInt(req.params.companyId, 10),
      parseInt(req.params.appId, 10),
      paymentTerm,
      contractTerm,
      price
    );
    res.json({ success: true });
  })
  .put(async (req, res) => {
    const { price, paymentTerm, contractTerm } = req.body;
    await upsertCompanyAppPrice(
      parseInt(req.params.companyId, 10),
      parseInt(req.params.appId, 10),
      paymentTerm,
      contractTerm,
      price
    );
    res.json({ success: true });
  })
  .delete(async (req, res) => {
    const { paymentTerm, contractTerm } = req.body;
    await deleteCompanyAppPrice(
      parseInt(req.params.companyId, 10),
      parseInt(req.params.appId, 10),
      paymentTerm,
      contractTerm
    );
    res.json({ success: true });
  });

/**
 * @openapi
 * /api/companies:
 *   get:
 *     tags:
 *       - Companies
 *     summary: List all companies
 *     responses:
 *       200:
 *         description: Array of companies
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   name:
 *                     type: string
 *                   address:
 *                     type: string
 */
api.get('/companies', async (_req, res) => {
  const companies = await getAllCompanies();
  res.json(companies);
});

/**
 * @openapi
 * /api/companies:
 *   post:
 *     tags:
 *       - Companies
 *     summary: Create a company
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - name
 *             properties:
 *               name:
 *                 type: string
 *               address:
 *                 type: string
 *               syncroCompanyId:
 *                 type: string
 *               xeroId:
 *                 type: string
 *               isVip:
 *                 type: boolean
 *     responses:
 *       200:
 *         description: Company created
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 */
api.post('/companies', async (req, res) => {
  const { name, address, syncroCompanyId, xeroId, isVip } = req.body;
  const id = await createCompany(
    name,
    address,
    parseCheckbox(isVip),
    syncroCompanyId,
    xeroId
  );
  await createDefaultSchedulesForCompany(id);
  res.json({ id });
});

/**
 * @openapi
 * /api/companies/{id}:
 *   get:
 *     tags:
 *       - Companies
 *     summary: Get a company by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Company details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 name:
 *                   type: string
 *                 address:
 *                   type: string
 *                 syncroCompanyId:
 *                   type: string
 *                 xeroId:
 *                   type: string
 *                 isVip:
 *                   type: boolean
 *       404:
 *         description: Company not found
 */
api.get('/companies/:id', async (req, res) => {
  const company = await getCompanyById(parseInt(req.params.id, 10));
  if (!company) {
    return res.status(404).json({ error: 'Company not found' });
  }
  res.json(company);
});

/**
 * @openapi
 * /api/companies/{id}:
 *   put:
 *     tags:
 *       - Companies
 *     summary: Update a company
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *               address:
 *                 type: string
 *               syncroCompanyId:
 *                 type: string
 *               xeroId:
 *                 type: string
 *               isVip:
 *                 type: boolean
 *     responses:
 *       200:
 *         description: Update successful
 */
export async function updateCompanyHandler(
  req: express.Request,
  res: express.Response,
  deps: {
    getCompanyById: typeof getCompanyById;
    updateCompany: typeof updateCompany;
    updateCompanyIds: typeof updateCompanyIds;
  } = {
    getCompanyById,
    updateCompany,
    updateCompanyIds,
  }
): Promise<void> {
  const { name, address, syncroCompanyId, xeroId, isVip } = req.body;
  const id = parseInt(req.params.id, 10);
  if (name !== undefined || address !== undefined) {
    let newName = name;
    let newAddress = address;
    if (name === undefined || address === undefined) {
      const current = await deps.getCompanyById(id);
      if (!current) {
        res.status(404).json({ error: 'Company not found' });
        return;
      }
      if (newName === undefined) {
        newName = current.name;
      }
      if (newAddress === undefined) {
        newAddress = current.address || null;
      }
    }
    await deps.updateCompany(id, newName, newAddress || null);
  }
  if (
    syncroCompanyId !== undefined ||
    xeroId !== undefined ||
    isVip !== undefined
  ) {
    await deps.updateCompanyIds(
      id,
      syncroCompanyId || null,
      xeroId || null,
      parseCheckbox(isVip)
    );
  }
  res.json({ success: true });
}

api.put('/companies/:id', (req, res) => updateCompanyHandler(req, res));

/**
 * @openapi
 * /api/companies/{id}:
 *   delete:
 *     tags:
 *       - Companies
 *     summary: Delete a company
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/companies/:id', async (req, res) => {
  await deleteCompany(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/users:
 *   get:
 *     tags:
 *       - Users
 *     summary: List all users
 *     responses:
 *       200:
 *         description: Array of users
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   email:
 *                     type: string
 *                   company_id:
 *                     type: integer
 */
api.get('/users', async (_req, res) => {
  const users = await getAllUsers();
  res.json(users);
});

/**
 * @openapi
 * /api/users:
 *   post:
 *     tags:
 *       - Users
 *     summary: Create a user
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - email
 *               - password
 *               - companyId
 *             properties:
 *               email:
 *                 type: string
 *               password:
 *                 type: string
 *               companyId:
 *                 type: integer
 *     responses:
 *       200:
 *         description: User created
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 */
api.post('/users', async (req, res) => {
  const { email, password, companyId } = req.body;
  const passwordHash = await bcrypt.hash(password, 10);
  const id = await createUser(email, passwordHash, companyId);
  res.json({ id });
});

/**
 * @openapi
 * /api/users/{id}:
 *   get:
 *     tags:
 *       - Users
 *     summary: Get a user by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: User details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 email:
 *                   type: string
 *                 company_id:
 *                   type: integer
 *       404:
 *         description: User not found
 */
api.get('/users/:id', async (req, res) => {
  const user = await getUserById(parseInt(req.params.id, 10));
  if (!user) {
    return res.status(404).json({ error: 'User not found' });
  }
  res.json(user);
});

/**
 * @openapi
 * /api/users/{id}:
 *   put:
 *     tags:
 *       - Users
 *     summary: Update a user
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               email:
 *                 type: string
 *               password:
 *                 type: string
 *               companyId:
 *                 type: integer
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/users/:id', async (req, res) => {
  const { email, password, companyId } = req.body;
  const id = parseInt(req.params.id, 10);
  let newEmail = email;
  let newCompanyId = companyId;
  let newPasswordHash: string | undefined;
  if (
    email === undefined ||
    password === undefined ||
    companyId === undefined
  ) {
    const current = await getUserById(id);
    if (!current) {
      return res.status(404).json({ error: 'User not found' });
    }
    if (newEmail === undefined) {
      newEmail = current.email;
    }
    if (newCompanyId === undefined) {
      newCompanyId = current.company_id;
    }
    if (password === undefined) {
      newPasswordHash = current.password_hash;
    }
  }
  if (password !== undefined) {
    newPasswordHash = await bcrypt.hash(password, 10);
  }
  await updateUser(id, newEmail, newPasswordHash!, newCompanyId);
  res.json({ success: true });
});

/**
 * @openapi
 * /api/users/{id}:
 *   delete:
 *     tags:
 *       - Users
 *     summary: Delete a user
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/users/:id', async (req, res) => {
  await deleteUser(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/licenses:
 *   get:
 *     tags:
 *       - Licenses
 *     summary: List all licenses
 *     responses:
 *       200:
 *         description: Array of licenses
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   company_id:
 *                     type: integer
 *                   name:
 *                     type: string
 *                   platform:
 *                     type: string
 *                   count:
 *                     type: integer
 *                   allocated:
 *                     type: integer
 *                   expiry_date:
 *                     type: string
 *                     format: date
 *                   contract_term:
 *                     type: string
 */
api.get('/licenses', async (_req, res) => {
  const licenses = await getAllLicenses();
  res.json(licenses);
});

/**
 * @openapi
 * /api/licenses:
 *   post:
 *     tags:
 *       - Licenses
 *     summary: Create a license
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - companyId
 *               - name
 *               - platform
 *               - count
 *             properties:
 *               companyId:
 *                 type: integer
 *               name:
 *                 type: string
 *               platform:
 *                 type: string
 *               count:
 *                 type: integer
 *               expiryDate:
 *                 type: string
 *                 format: date
 *               contractTerm:
 *                 type: string
 *     responses:
 *       200:
 *         description: License created
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 */
api.post('/licenses', async (req, res) => {
  const { companyId, name, platform, count, expiryDate, contractTerm } = req.body;
  const id = await createLicense(
    companyId,
    name,
    platform,
    count,
    expiryDate,
    contractTerm
  );
  res.json({ id });
});

/**
 * @openapi
 * /api/licenses/{id}:
 *   get:
 *     tags:
 *       - Licenses
 *     summary: Get a license by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: License details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 company_id:
 *                   type: integer
 *                 name:
 *                   type: string
 *                 platform:
 *                   type: string
 *                 count:
 *                   type: integer
 *                 allocated:
 *                   type: integer
 *                 expiry_date:
 *                   type: string
 *                   format: date
 *                 contract_term:
 *                   type: string
 *       404:
 *         description: License not found
 */
api.get('/licenses/:id', async (req, res) => {
  const license = await getLicenseById(parseInt(req.params.id, 10));
  if (!license) {
    return res.status(404).json({ error: 'License not found' });
  }
  res.json(license);
});

/**
 * @openapi
 * /api/licenses/{id}:
 *   put:
 *     tags:
 *       - Licenses
 *     summary: Update a license
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               companyId:
 *                 type: integer
 *               name:
 *                 type: string
 *               platform:
 *                 type: string
 *               count:
 *                 type: integer
 *               expiryDate:
 *                 type: string
 *                 format: date
 *                 nullable: true
 *               contractTerm:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/licenses/:id', async (req, res) => {
  const { companyId, name, platform, count, expiryDate, contractTerm } = req.body;
  const id = parseInt(req.params.id, 10);
  let newCompanyId = companyId;
  let newName = name;
  let newPlatform = platform;
  let newCount = count;
  let newExpiryDate = expiryDate;
  let newContractTerm = contractTerm;
  if (
    companyId === undefined ||
    name === undefined ||
    platform === undefined ||
    count === undefined ||
    expiryDate === undefined ||
    contractTerm === undefined
  ) {
    const current = await getLicenseById(id);
    if (!current) {
      return res.status(404).json({ error: 'License not found' });
    }
    if (newCompanyId === undefined) newCompanyId = current.company_id;
    if (newName === undefined) newName = current.name;
    if (newPlatform === undefined) newPlatform = current.platform;
    if (newCount === undefined) newCount = current.count;
    if (newExpiryDate === undefined) newExpiryDate = current.expiry_date;
    if (newContractTerm === undefined)
      newContractTerm = current.contract_term;
  }
  await updateLicense(
    id,
    newCompanyId!,
    newName!,
    newPlatform!,
    newCount!,
    newExpiryDate || null,
    newContractTerm!
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/licenses/{id}:
 *   delete:
 *     tags:
 *       - Licenses
 *     summary: Delete a license
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/licenses/:id', async (req, res) => {
  await deleteLicense(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/staff:
 *   get:
 *     tags:
 *       - Staff
 *     summary: List all staff members
 *     parameters:
 *       - in: query
 *         name: accountAction
 *         schema:
 *           type: string
 *         required: false
 *         description: Filter by account action
 *       - in: query
 *         name: email
 *         schema:
 *           type: string
 *         required: false
 *         description: Filter by email address
 *     responses:
 *       200:
 *         description: Array of staff
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 type: object
 *                 properties:
 *                   id:
 *                     type: integer
 *                   companyId:
 *                     type: integer
 *                   firstName:
 *                     type: string
 *                   lastName:
 *                     type: string
 *                   email:
 *                     type: string
 *                   dateOnboarded:
 *                     type: string
 *                     format: date
 *                   dateOffboarded:
 *                     type: string
 *                     format: date-time
 *                     nullable: true
 *                   enabled:
 *                     type: boolean
 *                   street:
 *                     type: string
 *                     nullable: true
 *                   city:
 *                     type: string
 *                     nullable: true
 *                   state:
 *                     type: string
 *                     nullable: true
 *                   postcode:
 *                     type: string
 *                     nullable: true
 *                   country:
 *                     type: string
 *                     nullable: true
 *                   department:
 *                     type: string
 *                     nullable: true
 *                   jobTitle:
 *                     type: string
 *                     nullable: true
 *                   company:
 *                     type: string
 *                     nullable: true
 *                   managerName:
 *                     type: string
 *                     nullable: true
 *                   accountAction:
 *                     type: string
 *                     nullable: true
 */
api.get('/staff', async (req, res) => {
  const accountAction = req.query.accountAction as string | undefined;
  const email = req.query.email as string | undefined;
  const staff = await getAllStaff(accountAction, email);
  res.json(staff.map(mapStaff));
});

/**
 * @openapi
 * /api/staff:
 *   post:
 *     tags:
 *       - Staff
 *     summary: Create a staff member
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - companyId
 *               - firstName
 *               - lastName
 *               - email
 *             properties:
 *               companyId:
 *                 type: integer
 *               firstName:
 *                 type: string
 *               lastName:
 *                 type: string
 *               email:
 *                 type: string
 *               mobilePhone:
 *                 type: string
 *               dateOnboarded:
 *                 type: string
 *                 format: date
 *               dateOffboarded:
 *                 type: string
 *                 format: date-time
 *               enabled:
 *                 type: boolean
 *               street:
 *                 type: string
 *               city:
 *                 type: string
 *               state:
 *                 type: string
 *               postcode:
 *                 type: string
 *               country:
 *                 type: string
 *               department:
 *                 type: string
 *               jobTitle:
 *                 type: string
 *               company:
 *                 type: string
 *               managerName:
 *                 type: string
 *               accountAction:
 *                 type: string
 *     responses:
 *       200:
 *         description: Staff member created
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 success:
 *                   type: boolean
 */
api.post('/staff', async (req, res) => {
  const {
    companyId,
    firstName,
    lastName,
    email,
    mobilePhone,
    dateOnboarded,
    dateOffboarded,
    enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
    accountAction,
  } = req.body;
  await addStaff(
    companyId,
    firstName,
    lastName,
    email,
    mobilePhone || null,
    toDate(dateOnboarded),
    toDateTime(dateOffboarded),
    !!enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
    accountAction || null,
    null
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/staff/{id}:
 *   get:
 *     tags:
 *       - Staff
 *     summary: Get a staff member by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Staff details
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 id:
 *                   type: integer
 *                 companyId:
 *                   type: integer
 *                 firstName:
 *                   type: string
 *                 lastName:
 *                   type: string
 *                 email:
 *                   type: string
 *                 mobilePhone:
 *                   type: string
 *                   nullable: true
 *                 dateOnboarded:
 *                   type: string
 *                   format: date
 *                 dateOffboarded:
 *                   type: string
 *                   format: date-time
 *                   nullable: true
 *                 enabled:
 *                   type: boolean
 *                 street:
 *                   type: string
 *                   nullable: true
 *                 city:
 *                   type: string
 *                   nullable: true
 *                 state:
 *                   type: string
 *                   nullable: true
 *                 postcode:
 *                   type: string
 *                   nullable: true
 *                 country:
 *                   type: string
 *                   nullable: true
 *                 department:
 *                   type: string
 *                   nullable: true
 *                 jobTitle:
 *                   type: string
 *                   nullable: true
 *                 company:
 *                   type: string
 *                   nullable: true
 *                 managerName:
 *                   type: string
 *                   nullable: true
 *                 accountAction:
 *                   type: string
 *                   nullable: true
 *                 verificationCode:
 *                   type: string
 *                   nullable: true
 *       404:
 *         description: Staff not found
 */
api.get('/staff/:id', async (req, res) => {
  const staff = await getStaffById(parseInt(req.params.id, 10));
  if (!staff) {
    return res.status(404).json({ error: 'Staff not found' });
  }
  res.json(mapStaff(staff));
});

/**
 * @openapi
 * /api/staff/{id}:
 *   put:
 *     tags:
 *       - Staff
 *     summary: Update a staff member
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               companyId:
 *                 type: integer
 *               firstName:
 *                 type: string
 *               lastName:
 *                 type: string
 *               email:
 *                 type: string
 *               mobilePhone:
 *                 type: string
 *               dateOnboarded:
 *                 type: string
 *                 format: date
 *               dateOffboarded:
 *                 type: string
 *                 format: date-time
 *               enabled:
 *                 type: boolean
 *               street:
 *                 type: string
 *               city:
 *                 type: string
 *               state:
 *                 type: string
 *               postcode:
 *                 type: string
 *               country:
 *                 type: string
 *               department:
 *                 type: string
 *               jobTitle:
 *                 type: string
 *               company:
 *                 type: string
 *               managerName:
 *                 type: string
 *               accountAction:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/staff/:id', async (req, res) => {
  const {
    companyId,
    firstName,
    lastName,
    email,
    mobilePhone,
    dateOnboarded,
    dateOffboarded,
    enabled,
    street,
    city,
    state,
    postcode,
    country,
    department,
    jobTitle,
    company,
    managerName,
    accountAction,
  } = req.body;
  const id = parseInt(req.params.id, 10);
  let current: Staff | null = null;
  if (
    [
      companyId,
      firstName,
      lastName,
      email,
      dateOnboarded,
      dateOffboarded,
      enabled,
      street,
      city,
      state,
      postcode,
      country,
      department,
      jobTitle,
      company,
      managerName,
      accountAction,
    ].some((v) => v === undefined)
  ) {
    current = await getStaffById(id);
    if (!current) {
      return res.status(404).json({ error: 'Staff not found' });
    }
  }
  await updateStaff(
    id,
    companyId !== undefined ? companyId : current!.company_id,
    firstName !== undefined ? firstName : current!.first_name,
    lastName !== undefined ? lastName : current!.last_name,
    email !== undefined ? email : current!.email,
    mobilePhone !== undefined ? mobilePhone : current!.mobile_phone || null,
    dateOnboarded !== undefined
      ? toDate(dateOnboarded)
      : current!.date_onboarded,
    dateOffboarded !== undefined
      ? toDateTime(dateOffboarded)
      : current!.date_offboarded ?? null,
    enabled !== undefined ? !!enabled : !!current!.enabled,
    street !== undefined ? street : current!.street ?? null,
    city !== undefined ? city : current!.city ?? null,
    state !== undefined ? state : current!.state ?? null,
    postcode !== undefined ? postcode : current!.postcode ?? null,
    country !== undefined ? country : current!.country ?? null,
    department !== undefined ? department : current!.department ?? null,
    jobTitle !== undefined ? jobTitle : current!.job_title ?? null,
    company !== undefined ? company : current!.org_company ?? null,
    managerName !== undefined ? managerName : current!.manager_name ?? null,
    accountAction !== undefined
      ? accountAction
      : current!.account_action ?? null,
    current?.syncro_contact_id || null
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/staff/{id}:
 *   delete:
 *     tags:
 *       - Staff
 *     summary: Delete a staff member
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/staff/:id', async (req, res) => {
  await deleteStaff(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/companies/{companyId}/users/{userId}:
 *   post:
 *     tags:
 *       - Companies
 *     summary: Assign a user to a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: userId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               canManageLicenses:
 *                 type: boolean
 *               staffPermission:
 *                 type: integer
*               canManageOfficeGroups:
*                 type: boolean
 *               canManageAssets:
 *                 type: boolean
 *               canManageInvoices:
 *                 type: boolean
 *               canOrderLicenses:
 *                 type: boolean
 *               canAccessShop:
 *                 type: boolean
 *               isAdmin:
 *                 type: boolean
 *     responses:
 *       200:
 *         description: Assignment successful
 */
api.post('/companies/:companyId/users/:userId', async (req, res) => {
  const {
    canManageLicenses,
    staffPermission,
    canManageOfficeGroups,
    canManageAssets,
    canManageInvoices,
    isAdmin,
    canOrderLicenses,
    canAccessShop,
  } = req.body;
  await assignUserToCompany(
    parseInt(req.params.userId, 10),
    parseInt(req.params.companyId, 10),
    !!canManageLicenses,
    parseInt(staffPermission, 10) || 0,
    !!canManageOfficeGroups,
    !!canManageAssets,
    !!canManageInvoices,
    !!isAdmin,
    !!canOrderLicenses,
    !!canAccessShop
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/companies/{companyId}/users/{userId}:
 *   delete:
 *     tags:
 *       - Companies
 *     summary: Unassign a user from a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: userId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Unassignment successful
 */
api.delete('/companies/:companyId/users/:userId', async (req, res) => {
  await unassignUserFromCompany(
    parseInt(req.params.userId, 10),
    parseInt(req.params.companyId, 10)
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/licenses/{licenseId}/staff/{staffId}:
 *   post:
 *     tags:
 *       - Licenses
 *     summary: Link a staff member to a license
 *     parameters:
 *       - in: path
 *         name: licenseId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: staffId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Link created
 */
api.post('/licenses/:licenseId/staff/:staffId', async (req, res) => {
  await linkStaffToLicense(
    parseInt(req.params.staffId, 10),
    parseInt(req.params.licenseId, 10)
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/licenses/{licenseId}/staff/{staffId}:
 *   delete:
 *     tags:
 *       - Licenses
 *     summary: Unlink a staff member from a license
 *     parameters:
 *       - in: path
 *         name: licenseId
 *         required: true
 *         schema:
 *           type: integer
 *       - in: path
 *         name: staffId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Link removed
 */
api.delete('/licenses/:licenseId/staff/:staffId', async (req, res) => {
  await unlinkStaffFromLicense(
    parseInt(req.params.staffId, 10),
    parseInt(req.params.licenseId, 10)
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/companies/{companyId}/assets:
 *   get:
 *     tags:
 *       - Assets
 *     summary: List assets for a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: List of assets
 *   post:
 *     tags:
 *       - Assets
 *     summary: Add an asset to a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               name:
 *                 type: string
 *               type:
 *                 type: string
 *               serialNumber:
 *                 type: string
 *               status:
 *                 type: string
 *     responses:
 *       200:
 *         description: Asset saved
 */
api.get('/companies/:companyId/assets', async (req, res) => {
  const assets = await getAssetsByCompany(parseInt(req.params.companyId, 10));
  res.json(assets);
});

api.post('/companies/:companyId/assets', async (req, res) => {
  const { name, type, serialNumber, status } = req.body;
  await upsertAsset(
    parseInt(req.params.companyId, 10),
    name,
    type,
    serialNumber,
    status
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/assets/{id}:
 *   get:
 *     tags:
 *       - Assets
 *     summary: Get an asset by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Asset details
 *       404:
 *         description: Asset not found
 */
api.get('/assets/:id', async (req, res) => {
  const asset = await getAssetById(parseInt(req.params.id, 10));
  if (!asset) {
    return res.status(404).json({ error: 'Asset not found' });
  }
  res.json(asset);
});

/**
 * @openapi
 * /api/assets/{id}:
 *   put:
 *     tags:
 *       - Assets
 *     summary: Update an asset
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               companyId:
 *                 type: integer
 *               name:
 *                 type: string
 *               type:
 *                 type: string
 *               serialNumber:
 *                 type: string
 *               status:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/assets/:id', async (req, res) => {
  const {
    companyId,
    name,
    type,
    serialNumber,
    status,
    osName,
    cpuName,
    ramGb,
    hddSize,
    lastSync,
    motherboardManufacturer,
    formFactor,
    lastUser,
    approxAge,
    performanceScore,
    warrantyStatus,
    warrantyEndDate,
  } = req.body;
  const id = parseInt(req.params.id, 10);
  let current: Asset | null = null;
  if (
    [
      companyId,
      name,
      type,
      serialNumber,
      status,
      osName,
      cpuName,
      ramGb,
      hddSize,
      lastSync,
      motherboardManufacturer,
      formFactor,
      lastUser,
      approxAge,
      performanceScore,
      warrantyStatus,
      warrantyEndDate,
    ].some(
      (v) => v === undefined
    )
  ) {
    current = await getAssetById(id);
    if (!current) {
      return res.status(404).json({ error: 'Asset not found' });
    }
  }
  await updateAsset(
    id,
    companyId !== undefined ? companyId : current!.company_id,
    name !== undefined ? name : current!.name,
    type !== undefined ? type : current!.type,
    serialNumber !== undefined ? serialNumber : current!.serial_number,
    status !== undefined ? status : current!.status,
    osName !== undefined ? osName : current!.os_name || null,
    cpuName !== undefined ? cpuName : current!.cpu_name || null,
    ramGb !== undefined ? ramGb : current!.ram_gb || null,
    hddSize !== undefined ? hddSize : current!.hdd_size || null,
    lastSync !== undefined ? lastSync : current!.last_sync || null,
    motherboardManufacturer !== undefined
      ? motherboardManufacturer
      : current!.motherboard_manufacturer || null,
    formFactor !== undefined ? formFactor : current!.form_factor || null,
    lastUser !== undefined ? lastUser : current!.last_user || null,
    approxAge !== undefined ? approxAge : current!.approx_age || null,
    performanceScore !== undefined
      ? performanceScore
      : current!.performance_score || null,
    warrantyStatus !== undefined
      ? warrantyStatus
      : current!.warranty_status || null,
    warrantyEndDate !== undefined
      ? warrantyEndDate
      : current!.warranty_end_date || null
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/assets/{id}:
 *   delete:
 *     tags:
 *       - Assets
 *     summary: Delete an asset
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/assets/:id', async (req, res) => {
  await deleteAsset(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/companies/{companyId}/invoices:
 *   get:
 *     tags:
 *       - Invoices
 *     summary: List invoices for a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: List of invoices
 *   post:
 *     tags:
 *       - Invoices
 *     summary: Add an invoice to a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               invoiceNumber:
 *                 type: string
 *               amount:
 *                 type: number
 *               dueDate:
 *                 type: string
 *                 format: date
 *               status:
 *                 type: string
 *     responses:
 *       200:
 *         description: Invoice saved
 */
api.get('/companies/:companyId/invoices', async (req, res) => {
  const invoices = await getInvoicesByCompany(parseInt(req.params.companyId, 10));
  res.json(invoices);
});

api.post('/companies/:companyId/invoices', async (req, res) => {
  const { invoiceNumber, amount, dueDate, status } = req.body;
  await upsertInvoice(
    parseInt(req.params.companyId, 10),
    invoiceNumber,
    amount,
    dueDate,
    status
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/invoices/{id}:
 *   get:
 *     tags:
 *       - Invoices
 *     summary: Get an invoice by ID
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Invoice details
 *       404:
 *         description: Invoice not found
 */
api.get('/invoices/:id', async (req, res) => {
  const invoice = await getInvoiceById(parseInt(req.params.id, 10));
  if (!invoice) {
    return res.status(404).json({ error: 'Invoice not found' });
  }
  res.json(invoice);
});

/**
 * @openapi
 * /api/invoices/{id}:
 *   put:
 *     tags:
 *       - Invoices
 *     summary: Update an invoice
 *     description: Partial update; include only fields to change
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               companyId:
 *                 type: integer
 *               invoiceNumber:
 *                 type: string
 *               amount:
 *                 type: number
 *               dueDate:
 *                 type: string
 *                 format: date
 *               status:
 *                 type: string
 *     responses:
 *       200:
 *         description: Update successful
 */
api.put('/invoices/:id', async (req, res) => {
  const { companyId, invoiceNumber, amount, dueDate, status } = req.body;
  const id = parseInt(req.params.id, 10);
  let current: Invoice | null = null;
  if (
    [companyId, invoiceNumber, amount, dueDate, status].some(
      (v) => v === undefined
    )
  ) {
    current = await getInvoiceById(id);
    if (!current) {
      return res.status(404).json({ error: 'Invoice not found' });
    }
  }
  await updateInvoice(
    id,
    companyId !== undefined ? companyId : current!.company_id,
    invoiceNumber !== undefined ? invoiceNumber : current!.invoice_number,
    amount !== undefined ? amount : current!.amount,
    dueDate !== undefined ? dueDate : current!.due_date,
    status !== undefined ? status : current!.status
  );
  res.json({ success: true });
});

/**
 * @openapi
 * /api/invoices/{id}:
 *   delete:
 *     tags:
 *       - Invoices
 *     summary: Delete an invoice
 *     parameters:
 *       - in: path
 *         name: id
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deletion successful
 */
api.delete('/invoices/:id', async (req, res) => {
  await deleteInvoice(parseInt(req.params.id, 10));
  res.json({ success: true });
});

/**
 * @openapi
 * /api/companies/{companyId}/m365-credentials:
 *   get:
 *     tags:
 *       - Office365
 *     summary: Get Office 365 credentials for a company
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Credentials
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 tenantId:
 *                   type: string
 *                 clientId:
 *                   type: string
 *                 tokenExpiresAt:
 *                   type: string
 *                   format: date-time
 *                   nullable: true
 *   post:
 *     tags:
 *       - Office365
 *     summary: Create or update Office 365 credentials
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - tenantId
 *               - clientId
 *               - clientSecret
 *             properties:
 *               tenantId:
 *                 type: string
 *               clientId:
 *                 type: string
 *               clientSecret:
 *                 type: string
 *     responses:
 *       200:
 *         description: Saved
 *   delete:
 *     tags:
 *       - Office365
 *     summary: Delete Office 365 credentials
 *     parameters:
 *       - in: path
 *         name: companyId
 *         required: true
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: Deleted
 */
api.get('/companies/:companyId/m365-credentials', async (req, res) => {
  const companyId = parseInt(req.params.companyId, 10);
  const cred = await getM365Credentials(companyId);
  if (!cred) return res.json(null);
  res.json({
    tenantId: cred.tenant_id,
    clientId: cred.client_id,
    tokenExpiresAt: cred.token_expires_at
      ? new Date(cred.token_expires_at).toISOString()
      : null,
  });
});

api.post('/companies/:companyId/m365-credentials', async (req, res) => {
  const companyId = parseInt(req.params.companyId, 10);
  const { tenantId, clientId, clientSecret } = req.body;
  const secret = encryptSecret(clientSecret);
  await upsertM365Credentials(companyId, tenantId, clientId, secret);
  res.json({ success: true });
});

api.delete('/companies/:companyId/m365-credentials', async (req, res) => {
  const companyId = parseInt(req.params.companyId, 10);
  await deleteM365Credentials(companyId);
  res.json({ success: true });
});

app.use('/api', api);

app.use(
  (
    err: any,
    _req: express.Request,
    res: express.Response,
    next: express.NextFunction
  ) => {
    if (err.code === 'EBADCSRFTOKEN') {
      return res.status(403).send('Invalid CSRF token');
    }
    next(err);
  }
);

const port = parseInt(process.env.PORT || '3000', 10);
const host = process.env.HOST || '0.0.0.0';

async function start() {
  await runMigrations();
  await hashExistingApiKeys();
  await encryptExistingTotpSecrets();
  await createDefaultSystemSchedules();
  await scheduleAllTasks();
  app.listen(port, host, () => {
    logInfo('Server running', { url: `http://${host}:${port}` });
  });
}

if (require.main === module) {
  start();
}

export {
  app,
  api,
  start,
  csrfMiddleware,
  csrfProtection,
  ensureAdmin,
  ensureSuperAdmin,
};
