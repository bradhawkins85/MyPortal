import crypto from 'crypto';
import dotenv from 'dotenv';

dotenv.config();

const rawKey = process.env.TOTP_ENCRYPTION_KEY;
if (!rawKey) {
  throw new Error('TOTP_ENCRYPTION_KEY is not set');
}
const key = crypto.createHash('sha256').update(rawKey).digest();

export function encryptSecret(secret: string): string {
  const iv = crypto.randomBytes(12); // GCM standard IV length
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const encrypted = Buffer.concat([cipher.update(secret, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `${iv.toString('base64')}:${tag.toString('base64')}:${encrypted.toString('base64')}`;
}

export function decryptSecret(payload: string): string {
  if (!payload.includes(':')) {
    // Assume plaintext from older entries
    return payload;
  }
  const [ivB64, tagB64, dataB64] = payload.split(':');
  const iv = Buffer.from(ivB64, 'base64');
  const tag = Buffer.from(tagB64, 'base64');
  const data = Buffer.from(dataB64, 'base64');
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  const decrypted = Buffer.concat([decipher.update(data), decipher.final()]);
  return decrypted.toString('utf8');
}
