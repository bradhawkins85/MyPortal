export function logInfo(message: string, meta?: Record<string, unknown>): void {
  const timestamp = new Date().toISOString();
  if (meta) {
    console.log(`[${timestamp}] INFO: ${message}`, meta);
  } else {
    console.log(`[${timestamp}] INFO: ${message}`);
  }
}

export function logError(message: string, meta?: Record<string, unknown>): void {
  const timestamp = new Date().toISOString();
  if (meta) {
    console.error(`[${timestamp}] ERROR: ${message}`, meta);
  } else {
    console.error(`[${timestamp}] ERROR: ${message}`);
  }
}
