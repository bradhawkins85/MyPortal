/*
 * MyPortal Outlook signature add-in event handlers.
 *
 * Served inside /outlook-addin/{id}/launch.js after MSAL.js and a
 * MYPORTAL_OUTLOOK_ADDIN config object. Runs in Outlook's event-based
 * runtime: it never shows UI, and every path ends with event.completed() so
 * composing is never blocked when MyPortal or Microsoft sign-in is unavailable.
 */
(function () {
  "use strict";

  var config = typeof MYPORTAL_OUTLOOK_ADDIN === "object" ? MYPORTAL_OUTLOOK_ADDIN : {};
  var clientPromise = null;

  function getClient() {
    if (!clientPromise) {
      clientPromise = msal.createNestablePublicClientApplication({
        auth: {
          clientId: config.clientId,
          authority: "https://login.microsoftonline.com/common",
        },
      });
    }
    return clientPromise;
  }

  function getToken() {
    return getClient().then(function (client) {
      return client.acquireTokenSilent({ scopes: [config.scope] });
    }).then(function (result) {
      return result.accessToken;
    });
  }

  function fetchSignature(token) {
    return fetch(config.signatureUrl, {
      method: "GET",
      headers: { Authorization: "Bearer " + token, Accept: "application/json" },
      credentials: "omit",
      cache: "no-store",
    }).then(function (response) {
      if (response.status !== 200) {
        return null;
      }
      return response.json();
    });
  }

  function officeCall(method, args) {
    return new Promise(function (resolve, reject) {
      method.apply(null, args.concat(function (result) {
        if (result.status === Office.AsyncResultStatus.Succeeded) {
          resolve(result.value);
        } else {
          reject(result.error);
        }
      }));
    });
  }

  function insertSignature(event) {
    var item = Office.context.mailbox.item;
    getToken()
      .then(fetchSignature)
      .then(function (data) {
        if (!data || !data.html) {
          return null;
        }
        var disable = item.disableClientSignatureAsync
          ? officeCall(item.disableClientSignatureAsync.bind(item), [])
          : Promise.resolve();
        return disable.then(function () {
          return officeCall(item.body.setSignatureAsync.bind(item.body), [
            data.html,
            { coercionType: Office.CoercionType.Html },
          ]);
        });
      })
      .catch(function (error) {
        console.warn("MyPortal signature was not added", error);
      })
      .then(function () {
        event.completed();
      });
  }

  Office.onReady(function () {});
  Office.actions.associate("onNewMessageComposeHandler", insertSignature);
  Office.actions.associate("onNewAppointmentOrganizerHandler", insertSignature);
})();
