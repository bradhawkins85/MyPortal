import dotenv from 'dotenv';
import { logInfo, logError } from './logger';

dotenv.config();

export interface SyncroCustomer {
  id: number;
  business_name?: string;
  first_name?: string;
  last_name?: string;
  address1?: string;
  address2?: string;
  city?: string;
  state?: string;
  zip?: string;
  [key: string]: any;
}

export interface SyncroContact {
  id: number;
  first_name?: string;
  last_name?: string;
  name?: string;
  email?: string;
  phone?: string;
  mobile?: string;
  address1?: string;
  address2?: string;
  city?: string;
  state?: string;
  zip?: string;
  [key: string]: any;
}

export interface SyncroAsset {
  id: number;
  name?: string;
  os_name?: string;
  cpu_name?: string;
  ram_gb?: number;
  hdd_size?: string;
  last_sync?: string;
  motherboard_manufacturer?: string;
  form_factor?: string;
  last_user?: string;
  cpu_age?: number;
  performance_score?: number;
  warranty_status?: string;
  warranty_end_date?: string;
  serial_number?: string;
  type?: string;
  status?: string;
  [key: string]: any;
}

async function syncroRequest(path: string, init: RequestInit = {}): Promise<any> {
  let base = process.env.SYNCRO_WEBHOOK_URL;
  if (!base) {
    throw new Error('SYNCRO_WEBHOOK_URL not set');
  }
  base = base.replace(/\/$/, '');
  if (!/\/api\/v1$/.test(base)) {
    base += '/api/v1';
  }
  const url = `${base}${path.startsWith('/') ? path : `/${path}`}`;
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string>),
  };
  if (process.env.SYNCRO_API_KEY) {
    headers['Authorization'] = `Bearer ${process.env.SYNCRO_API_KEY}`;
  }
  logInfo('Calling Syncro API', { url, method: init.method || 'GET' });
  try {
    const res = await fetch(url, { ...init, headers });
    logInfo('Syncro API response', { url, status: res.status });
    if (!res.ok) {
      if (res.status === 404) {
        return null;
      }
      throw new Error(`Syncro API request failed: ${res.status}`);
    }
    if (res.status === 204) {
      return null;
    }
    return res.json();
  } catch (err) {
    logError('Syncro API request error', { url, error: (err as Error).message });
    throw err;
  }
}

export async function getSyncroCustomers(): Promise<SyncroCustomer[]> {
  const data = await syncroRequest('/customers');
  if (Array.isArray(data)) {
    return data as SyncroCustomer[];
  }
  if (Array.isArray(data?.customers)) {
    return data.customers as SyncroCustomer[];
  }
  if (Array.isArray(data?.data)) {
    return data.data as SyncroCustomer[];
  }
  return [];
}

export async function getSyncroCustomer(id: string | number): Promise<SyncroCustomer | null> {
  const data = await syncroRequest(`/customers/${id}`);
  if (data?.customer) {
    return data.customer as SyncroCustomer;
  }
  return (data ?? null) as SyncroCustomer | null;
}

export async function getSyncroContacts(
  customerId: string | number
): Promise<SyncroContact[]> {
  const data = await syncroRequest(`/contacts?customer_id=${customerId}`);
  if (Array.isArray(data)) {
    return data as SyncroContact[];
  }
  if (Array.isArray((data as any)?.contacts)) {
    return (data as any).contacts as SyncroContact[];
  }
  if (Array.isArray((data as any)?.data)) {
    return (data as any).data as SyncroContact[];
  }
  return [];
}

export async function getSyncroAssets(
  customerId: string | number
): Promise<SyncroAsset[]> {
  const results: SyncroAsset[] = [];
  for (let page = 1; page <= 100; page++) {
    const data = await syncroRequest(
      `/customer_assets?customer_id=${customerId}&page=${page}`
    );
    if (!data) break;
    let assets: SyncroAsset[] = [];
    if (Array.isArray(data)) {
      assets = data as SyncroAsset[];
    } else if (Array.isArray((data as any)?.assets)) {
      assets = (data as any).assets as SyncroAsset[];
    } else if (Array.isArray((data as any)?.data)) {
      assets = (data as any).data as SyncroAsset[];
    }
    if (!assets.length) break;
    results.push(...assets);
    const totalPages =
      (data as any)?.meta?.total_pages ||
      (data as any)?.pagination?.total_pages;
    if (totalPages && page >= totalPages) break;
  }
  return results;
}

export { syncroRequest };
