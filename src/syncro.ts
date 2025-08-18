import dotenv from 'dotenv';

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

async function syncroRequest(path: string, init: RequestInit = {}): Promise<any> {
  const base = process.env.SYNCRO_WEBHOOK_URL;
  if (!base) {
    throw new Error('SYNCRO_WEBHOOK_URL not set');
  }
  const url = `${base}${path}`;
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string>),
  };
  if (process.env.SYNCRO_API_KEY) {
    headers['Authorization'] = `Bearer ${process.env.SYNCRO_API_KEY}`;
  }
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    throw new Error(`Syncro API request failed: ${res.status}`);
  }
  return res.json();
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

export { syncroRequest };
