/*
 * MyPortal: start the MeshCentral session a MyPortal link asked for.
 *
 * MyPortal's MeshCentral links open the device on the chosen view (desktop,
 * terminal, files or registry), but MeshCentral itself has no link option to
 * connect, so the technician would still press Connect. Installed on the
 * MeshCentral server, this script presses it once for links that carry
 * myportalconnect=1, and does nothing on any other page.
 *
 * Install: copy this file to meshcentral-web/public/scripts/ on the MeshCentral
 * server and load it on the main page, either by naming it custom.js (or adding
 * its contents to an existing custom.js) or through the domain's customFiles
 * setting, e.g. "customFiles": { "myportal": { "js": ["myportal-autoconnect.js"],
 * "scope": ["default", "default3"] } }. Restart MeshCentral after changing config.json.
 */
(function () {
  'use strict';

  // MeshCentral tidies the address bar once the page has loaded, so read it now.
  var params = new URLSearchParams(window.location.search);
  if (params.get('myportalconnect') !== '1') {
    return;
  }
  var view = parseInt(params.get('viewmode'), 10);
  // The Connect button on each of MeshCentral's device views.
  var BUTTONS = { 9: 'p9Connect', 11: 'connectbutton1', 12: 'connectbutton2', 13: 'p13Connect' };
  var buttonId = BUTTONS[view];
  if (!buttonId) {
    return;
  }
  var POLL_MS = 250;
  var GIVE_UP_MS = 60 * 1000;
  var started = Date.now();

  function currentView() {
    // MeshCentral keeps the shown view in a page-wide variable.
    return typeof window.xxcurrentView === 'number' ? window.xxcurrentView : -1;
  }

  function tick() {
    if (Date.now() - started > GIVE_UP_MS) {
      return;
    }
    var button = document.getElementById(buttonId);
    // Wait until the device view is showing and its Connect button is usable
    // (MeshCentral enables it once the device is online).
    if (currentView() === view && button && !button.disabled && button.offsetParent !== null) {
      button.click();
      return;
    }
    window.setTimeout(tick, POLL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tick);
  } else {
    tick();
  }
})();
