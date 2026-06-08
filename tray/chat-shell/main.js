'use strict';

/**
 * MyPortal Chat Shell — Electron main process
 *
 * A minimal dedicated chat window for the MyPortal Tray App. This shell wraps
 * the MyPortal /tray/chat page in an isolated Electron BrowserWindow so that
 * the chat session is completely separate from the user's personal browser
 * sessions and opens as a standalone app window rather than a browser tab.
 *
 * Usage (launched by myportal-tray-ui):
 *   myportal-tray-chat --url=https://portal.example.com/tray/chat?token=...
 *
 * The URL is expected to carry a short-lived one-time auth token issued by
 * POST /api/tray/chat-token so the user is authenticated automatically.
 */

const { app, BrowserWindow, shell, nativeImage } = require('electron');
const path = require('path');
const { URL } = require('url');

// ---------------------------------------------------------------------------
// Parse --url argument
// ---------------------------------------------------------------------------

function getChatURL() {
  for (const arg of process.argv.slice(2)) {
    if (arg.startsWith('--url=')) {
      return arg.slice('--url='.length).trim();
    }
  }
  return null;
}

const chatURL = getChatURL();
if (!chatURL) {
  console.error('[MyPortal Chat] Error: --url=<chat-url> argument is required.');
  app.quit();
  process.exit(1);
}

// Validate that the URL is well-formed before handing it to Electron.
let parsedURL;
try {
  parsedURL = new URL(chatURL);
} catch {
  console.error('[MyPortal Chat] Error: --url value is not a valid URL.');
  app.quit();
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Session isolation
// ---------------------------------------------------------------------------
// Use a persistent named partition so the chat session survives across
// invocations (the user stays logged in between chats) but remains completely
// separate from both the user's default browser and the default Electron
// session.
const CHAT_PARTITION = 'persist:myportal-tray-chat';

// ---------------------------------------------------------------------------
// Single-instance lock
// ---------------------------------------------------------------------------
// Prevent multiple chat windows from opening simultaneously. If a second
// instance is launched with a different URL, focus the existing window and
// navigate it to the new URL.

const gotLock = app.requestSingleInstanceLock({ chatURL });
if (!gotLock) {
  app.quit();
  process.exit(0);
}

let mainWindow = null;

app.on('second-instance', (_event, _argv, _cwd, additionalData) => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
    // Navigate to the new chat URL provided by the second instance.
    const newURL = (additionalData && additionalData.chatURL) || chatURL;
    mainWindow.loadURL(newURL).catch(() => {});
  }
});

// ---------------------------------------------------------------------------
// Window creation
// ---------------------------------------------------------------------------

app.whenReady().then(() => {
  const ses = require('electron').session.fromPartition(CHAT_PARTITION);

  // Block non-chat navigation to prevent accidental browsing within the shell.
  ses.webRequest.onBeforeRequest({ urls: ['<all_urls>'] }, (details, callback) => {
    try {
      const reqURL = new URL(details.url);
      // Allow same origin, data: and about: URLs.
      if (
        reqURL.origin === parsedURL.origin ||
        details.url.startsWith('data:') ||
        details.url.startsWith('about:') ||
        details.url.startsWith('devtools:')
      ) {
        callback({ cancel: false });
      } else {
        // Redirect off-origin navigations to the system browser.
        if (details.resourceType === 'mainFrame') {
          shell.openExternal(details.url).catch(() => {});
        }
        callback({ cancel: true });
      }
    } catch {
      callback({ cancel: false });
    }
  });

  mainWindow = new BrowserWindow({
    width: 920,
    height: 680,
    minWidth: 480,
    minHeight: 400,
    title: 'MyPortal Chat',
    autoHideMenuBar: true,
    backgroundColor: '#ffffff',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      partition: CHAT_PARTITION,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // Suppress the default Electron menu entirely.
  mainWindow.setMenu(null);

  // Open target=_blank links in the system browser.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url).catch(() => {});
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.loadURL(chatURL).catch((err) => {
    console.error('[MyPortal Chat] Failed to load URL:', err.message);
  });
});

// Quit when all windows are closed (standard desktop app behaviour).
app.on('window-all-closed', () => app.quit());
