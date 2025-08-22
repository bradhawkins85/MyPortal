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
    newTotpSecret?: string;
    newTotpName?: string;
    newTotpError?: string;
    passwordError?: string;
    passwordSuccess?: string;
    nameError?: string;
    nameSuccess?: string;
    mobileError?: string;
    mobileSuccess?: string;
    mustChangePassword?: boolean;
    pendingForcePassword?: boolean;
  }
}

declare global {
  namespace Express {
    interface Request {
      apiKey?: string;
    }
  }
}

declare module 'connect-redis' {
  import { Store } from 'express-session';
  import { RedisClientType } from 'redis';
  interface RedisStoreOptions {
    client: RedisClientType;
    prefix?: string;
    ttl?: number;
    disableTouch?: boolean;
  }
  export default class RedisStore extends Store {
    constructor(options: RedisStoreOptions);
  }
}
