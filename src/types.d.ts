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
  }
}
