'use strict';
/**
 * Minimal security preload for MyPortal Chat Shell.
 *
 * contextIsolation: true + nodeIntegration: false (set in main.js) means no
 * Node.js APIs are accessible from the renderer. This preload intentionally
 * exposes nothing — it exists only to satisfy Electron's preload requirement
 * and to serve as the right place to add any future safe IPC bridges if needed.
 */
