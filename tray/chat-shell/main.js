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

const { app, BrowserWindow, shell } = require('electron');
const path = require('path');
const { URL } = require('url');

// ---------------------------------------------------------------------------
// Parse --url argument
// ---------------------------------------------------------------------------

function getChatURL() {
  for (const arg of process.argv.slice(2)) {
    if (arg.startsWith('--url=')) {
      const val = arg.slice('--url='.length).trim();
      return val || null;
    }
  }
  return null;
}

const chatURL = getChatURL();

// ---------------------------------------------------------------------------
// Session isolation
// ---------------------------------------------------------------------------
// Use a persistent named partition so the chat session survives across
// invocations (the user stays logged in between chats) but remains completely
// separate from both the user's default browser and the default Electron
// session.
const CHAT_PARTITION = 'persist:myportal-tray-chat';

// How long (ms) to display the informational window when the app is run without
// a --url= argument (e.g. manually by a user).
const INFO_WINDOW_DISPLAY_MS = 8000;

// ---------------------------------------------------------------------------
// Single-instance lock
// ---------------------------------------------------------------------------
// Prevent multiple chat windows from opening simultaneously. If a second
// instance is launched with a different URL, focus the existing window and
// navigate it to the new URL.

const gotLock = app.requestSingleInstanceLock({ chatURL: chatURL || '' });
if (!gotLock) {
  app.quit();
  process.exit(0);
}

let mainWindow = null;
// Track the info window and its auto-close timer so the second-instance
// handler can cancel the timer and open a real chat window.
let infoWindow = null;
let infoCloseTimer = null;

// ---------------------------------------------------------------------------
// Chat window factory
// ---------------------------------------------------------------------------
// Extracted so that both app.whenReady() and the second-instance handler can
// open a proper chat window without duplicating setup code.

function createChatWindow(url) {
  let parsedURL;
  try {
    parsedURL = new URL(url);
  } catch {
    console.error('[MyPortal Chat] Error: invalid URL for chat window:', url);
    return;
  }

  const ses = require('electron').session.fromPartition(CHAT_PARTITION);

  // Block non-chat navigation to prevent accidental browsing within the shell.
  // Replacing the handler on each invocation is intentional — the origin may
  // change if the tray server URL is reconfigured between sessions.
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
          shell.openExternal(details.url).catch((err) => {
            console.error('[MyPortal Chat] Failed to open external URL:', err);
          });
        }
        callback({ cancel: true });
      }
    } catch (err) {
      console.error('[MyPortal Chat] webRequest filter error:', err);
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
  mainWindow.webContents.setWindowOpenHandler(({ url: newUrl }) => {
    shell.openExternal(newUrl).catch(() => {});
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.loadURL(url).catch((err) => {
    console.error('[MyPortal Chat] Failed to load URL:', err.message);
  });
}

// ---------------------------------------------------------------------------
// Second-instance handler
// ---------------------------------------------------------------------------

app.on('second-instance', (_event, _argv, _cwd, additionalData) => {
  const newURL = (additionalData && additionalData.chatURL) || null;

  if (mainWindow) {
    // A chat window is already open — focus it and navigate to the new URL.
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
    if (newURL) {
      mainWindow.loadURL(newURL).catch(() => {});
    }
    return;
  }

  if (newURL) {
    // The first instance either showed the info window (no URL was passed on
    // first launch) or its chat window was closed while a new click arrived
    // before the app fully quit.  In either case, open a proper chat window.
    //
    // Create mainWindow BEFORE destroying infoWindow so that window-all-closed
    // does not fire in the gap and trigger a premature app.quit().
    if (infoCloseTimer !== null) {
      clearTimeout(infoCloseTimer);
      infoCloseTimer = null;
    }
    createChatWindow(newURL);
    if (infoWindow && !infoWindow.isDestroyed()) {
      infoWindow.destroy();
      infoWindow = null;
    }
  }
});

// ---------------------------------------------------------------------------
// Window creation
// ---------------------------------------------------------------------------

app.whenReady().then(() => {
  // When no --url= argument is supplied (e.g. the user ran the EXE manually),
  // show a brief informational window instead of silently exiting, so the
  // user understands how the app is intended to be launched.
  if (!chatURL) {
    infoWindow = new BrowserWindow({
      width: 480,
      height: 220,
      title: 'MyPortal Chat',
      autoHideMenuBar: true,
      resizable: false,
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    infoWindow.setMenu(null);
    infoWindow.loadURL(
      'data:text/html,' +
        encodeURIComponent(
          '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:24px;margin:0">' +
            '<h3 style="margin-top:0">MyPortal Chat</h3>' +
            '<p>This application opens automatically when you click ' +
            '<strong>Support Chat</strong> in the MyPortal tray menu.</p>' +
            '<p style="color:#888;font-size:0.9em">This window will close in a few seconds.</p>' +
            '</body></html>'
        )
    );
    infoWindow.on('closed', () => {
      infoWindow = null;
    });
    // Auto-close after 8 seconds so it does not linger.
    infoCloseTimer = setTimeout(() => {
      infoCloseTimer = null;
      app.quit();
    }, INFO_WINDOW_DISPLAY_MS);
    return;
  }

  createChatWindow(chatURL);
});

// Quit when all windows are closed (standard desktop app behaviour).
app.on('window-all-closed', () => app.quit());
