// Runs app/static/js/outlook_signature_addin.js against stubbed Office and
// MSAL globals and prints a JSON trace of what happened.
// Usage: node outlook_signature_addin_harness.js <handler.js> <scenario>
"use strict";
const fs = require("fs");
const vm = require("vm");

const [handlerPath, scenario] = process.argv.slice(2);
const trace = [];
let resolveReady;
let bridgeReady = false;
let pcaCreations = 0;

const sandbox = {
  console: { warn: (...a) => trace.push(["warn", String(a[0])]), error: (...a) => trace.push(["error", String(a[0])]), info() {}, log() {} },
  Promise,
  setTimeout,
  MYPORTAL_OUTLOOK_ADDIN: { clientId: "c", scope: "api://x/c/access_as_user", signatureUrl: "https://x/api/outlook-addin/signature" },
  fetch: async () => ({ status: 200, json: async () => ({ html: "<p>sig</p>" }) }),
  msal: {
    createNestablePublicClientApplication: async () => {
      pcaCreations += 1;
      const nested = bridgeReady;
      return {
        acquireTokenSilent: async () => {
          if (!nested) { const e = new Error("no_account_error"); e.errorCode = "no_account_error"; throw e; }
          return { accessToken: "token" };
        },
      };
    },
  },
};
const handlers = {};
let body = null;
sandbox.Office = {
  AsyncResultStatus: { Succeeded: "succeeded" },
  CoercionType: { Html: "html" },
  onReady: (cb) => {
    const p = new Promise((r) => { resolveReady = r; }).then(() => { if (cb) cb(); });
    return p;
  },
  actions: { associate: (name, fn) => { handlers[name] = fn; } },
  context: {
    mailbox: {
      item: {
        disableClientSignatureAsync: (cb) => cb({ status: "succeeded" }),
        body: { setSignatureAsync: (html, opts, cb) => { body = html; cb({ status: "succeeded" }); } },
      },
    },
  },
};

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(handlerPath, "utf8"), sandbox);

function fire() {
  return new Promise((resolve) => handlers.onNewMessageComposeHandler({ completed: () => resolve() }));
}

(async () => {
  if (scenario === "event-before-ready") {
    // Outlook fires the compose event before Office (and its NAA bridge) is ready.
    const done = fire();
    await new Promise((r) => setTimeout(r, 10));
    bridgeReady = true;
    resolveReady();
    await done;
  } else if (scenario === "retry-after-failure") {
    // The first event gets no bridge; a later one must not reuse that client.
    resolveReady();
    await fire();
    bridgeReady = true;
    await fire();
  }
  process.stdout.write(JSON.stringify({ body, pcaCreations, trace }));
})();
