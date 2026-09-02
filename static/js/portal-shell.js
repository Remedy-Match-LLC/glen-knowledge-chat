// Portal shell: the seven doors, their panel membership, and the rail markup.
//
// Pure by design (no DOM reads, no fetch) so node can unit-test it, following the
// same dual-module pattern as static/js/portal-documents.js. The page loads it with
// a <script src> and calls into it; tests require() it.
//
// A door reveals every [data-panel] section that carries its key as data-door, so a
// door can hold several sections stacked. That is what lets the oversized `current`
// panel be split into sections later without touching the router.

var DOORS = [
  { key: 'home', label: 'Home',
    icon: 'M3 10.5 12 3l9 7.5M5.5 9.5V21h13V9.5',
    panels: ['hub'] },
  { key: 'scans', label: 'Scans & Reports',
    icon: 'M2 12h3l2.5-7 3 14 3-10.5L16 15l2-3h4',
    panels: ['current', 'voice', 'history'] },
  { key: 'solutions', label: 'Find Solutions',
    icon: 'M15.5 15.5 21 21M17 10.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0',
    panels: ['shop'] },
  { key: 'remedies', label: 'My Remedies',
    icon: 'M10 2h4v4l3.5 5.5A4 4 0 0 1 14 18h-4a4 4 0 0 1-3.5-6.5L10 6zM6.8 13h10.4',
    panels: ['remedies', 'oasis', 'cart'] },
  { key: 'billing', label: 'Billing',
    icon: 'M6 2.5h12v19l-3-2-3 2-3-2-3 2zM9.5 8h5M9.5 12h5',
    panels: ['orders'] },
  { key: 'learn', label: 'Learn & Ask',
    icon: 'M3 4.5h6a3 3 0 0 1 3 3v12a2.5 2.5 0 0 0-2.5-2.5H3zM21 4.5h-6a3 3 0 0 0-3 3v12a2.5 2.5 0 0 1 2.5-2.5H21z',
    panels: ['ask', 'bodymap', 'classes', 'calendar'] },
  { key: 'account', label: 'Account',
    icon: 'M15.6 8a3.6 3.6 0 1 1-7.2 0 3.6 3.6 0 0 1 7.2 0M4.5 20.5a7.5 7.5 0 0 1 15 0',
    panels: ['account', 'photo', 'intake', 'records', 'refer', 'referrals', 'offers', 'finder'] }
];

function panelsForDoor(key) {
  for (var i = 0; i < DOORS.length; i++) {
    if (DOORS[i].key === key) return DOORS[i].panels.slice();
  }
  return [];
}

function doorForPanel(panel) {
  for (var i = 0; i < DOORS.length; i++) {
    if (DOORS[i].panels.indexOf(panel) !== -1) return DOORS[i].key;
  }
  return null;
}

function allPanels() {
  var out = [];
  DOORS.forEach(function (d) { d.panels.forEach(function (p) { out.push(p); }); });
  return out;
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderRail(activeDoor, opts) {
  opts = opts || {};
  var items = DOORS.map(function (d) {
    var active = (d.key === activeDoor) ? ' is-active' : '';
    return '<button type="button" class="rail-item' + active + '" data-door="' +
      escapeHtml(d.key) + '">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="' + escapeHtml(d.icon) + '"/></svg>' +
      '<span>' + escapeHtml(d.label) + '</span></button>';
  }).join('');
  return '<nav class="portal-rail' + (opts.open ? ' is-open' : '') +
    '" id="portalRail" aria-label="Portal sections">' + items +
    '<button type="button" class="rail-item rail-toggle" data-shell-toggle="1" ' +
    'aria-label="Open and close the menu">' +
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>' +
    '<span>Close</span></button></nav>';
}

function renderPhoneHeader() {
  return '<header class="shell-bar" id="shellBar">' +
    '<button type="button" class="shell-burger" data-shell-open="1" aria-label="Open the menu">' +
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>' +
    '</button><span class="shell-title">Your healing home</span></header>';
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    DOORS: DOORS, panelsForDoor: panelsForDoor,
    doorForPanel: doorForPanel, allPanels: allPanels,
    renderRail: renderRail, renderPhoneHeader: renderPhoneHeader, escapeHtml: escapeHtml
  };
}
if (typeof window !== 'undefined') {
  window.PortalShell = {
    DOORS: DOORS, panelsForDoor: panelsForDoor,
    doorForPanel: doorForPanel, allPanels: allPanels,
    renderRail: renderRail, renderPhoneHeader: renderPhoneHeader, escapeHtml: escapeHtml
  };
}
