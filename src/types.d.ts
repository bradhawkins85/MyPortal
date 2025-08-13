import 'express-session';

declare module 'express-session' {
  interface SessionData {
    userId?: number;
    companyId?: number;
    cart?: { productId: number; name: string; quantity: number }[];
    orderMessage?: string;
  }
}
