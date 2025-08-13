import 'express-session';

declare module 'express-session' {
  interface SessionData {
    userId?: number;
    companyId?: number;
    cart?: {
      productId: number;
      name: string;
      sku: string;
      description: string;
      price: number;
      quantity: number;
    }[];
    orderMessage?: string;
  }
}
