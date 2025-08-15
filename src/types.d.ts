import 'express-session';

declare module 'express-session' {
  interface SessionData {
    userId?: number;
    companyId?: number;
    cart?: {
      productId: number;
      name: string;
      sku: string;
      vendorSku: string;
      description: string;
      imageUrl: string | null;
      price: number;
      quantity: number;
    }[];
    orderMessage?: string;
    cartError?: string;
    hasForms?: boolean;
    tempUserId?: number;
    pendingTotpSecret?: string;
    requireTotpSetup?: boolean;
  }
}

declare global {
  namespace Express {
    interface Request {
      apiKey?: string;
    }
  }
}
