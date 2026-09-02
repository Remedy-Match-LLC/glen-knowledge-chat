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

// Task 8 (portal-shell-ia): the top-of-page chat composer. Present on every door
// (rendered into #portalShellMount, which sits outside every [data-panel] section
// in static/client-portal.html), so a client can ask a question without leaving
// whatever door they are on. One line, not a second transcript: it carries only
// the input static/client-portal.html's sendChatMessage() already reads by id, and
// a Send button, no message-bubble container. static/client-portal.html renders
// this only under the shell, and drops the composer row from the legacy "Ask Dr.
// Glen" card in that case, so id="chatInput" still appears exactly once.
function renderComposer() {
  return '<div class="shell-composer" id="shellComposer">' +
    '<div class="chat-input-row">' +
    '<input id="chatInput" type="text" placeholder="Ask me anything, or tell me what you need" autocomplete="off">' +
    '<button type="button" class="btn" id="chatSend">Send</button>' +
    '</div></div>';
}

// Task 9 (portal-shell-ia): question text to a door. Deliberately conservative: an
// unmatched question returns null and the concierge answers in prose. A wrong
// destination costs more than no destination, because the client lands somewhere
// irrelevant and stops asking.
//
// Fix round 1: several of these were bare dictionary words that also occur in
// ordinary English unrelated to the door they were meant to catch, so they
// over-matched. Notably: "pay", "bill" and "charge" are ordinary words, and this
// practice works in energetic medicine, where clients routinely talk about an
// energy "charge" ("my energy charge feels low") with no billing intent at all.
// Every ambiguous bare word below was replaced with a phrase that requires money,
// scheduling, or possession context, per Glen's ruling on the billing pattern.
// Fix round 2: a reviewer probed with sentences neither prior pass had imagined
// and found the same class of bug in three more bare words, plus a redundant
// alternative. "recommend"/"suggest" fired on a client recommending a friend or
// a book, "protocol" fired on a scheduling question ("the protocol for
// canceling"), and "profile" fired on a description of Dr. Glen's public
// standing ("a great profile as a healer"), none of which are the door they
// were meant to catch. "my card" under billing was redundant with the charge
// pattern and also caught "I lost my card at the clinic".
var INTENTS = [
  ['billing',   /\b(invoice|invoices|billing|receipt|receipts|owe|owed|unpaid|payment|payments|paid|refund)\b|\bpay(?:ing)?\s+(?:my|the|for|it|this|now|online)\b|\bcharged?\s+(?:me|my|twice|again|for)\b|\bbill(?:ed)?\s+(?:me|my)\b/i],
  // "report" and "findings" alone also mean "report a problem with my order" or
  // "report an issue", nothing to do with a scan. Require the client's own
  // possessive ("my report") so a complaint about the order does not land here.
  ['scans',     /\b(scan|scans|biofield|voice analysis|5-element|five element|healing path)\b|\bmy (report|findings)\b|\b(report|findings) card\b/i],
  // "dose" alone also appears in idiom ("a dose of reality") and in a symptom
  // description ("a sharp dose of pain"). Require a determiner that marks it as
  // a question about the client's own medication, not a figure of speech.
  // "protocol" alone also appears in a scheduling or process question ("what is
  // the protocol for canceling a session"), nothing to do with a remedy.
  ['remedies',  /\b(reorder|re-order|refill|my remedies|what do i take|dosage|cart|wishlist)\b|\b(what|which|my|the) dose\b|\bmy protocol\b|\bprotocol\s+for\s+(?:taking|my)\b|\bdosing protocol\b/i],
  // "good for" alone also closes a scheduling exchange ("3pm is good for me"),
  // and bare "condition" is used for a car, a garden, an order, anything, not
  // just health. Exclude the pronoun form of "good for" and require condition to
  // carry a determiner that actually points at the client's own health.
  // "recommend"/"suggest" alone also fire when a client recommends a friend, a
  // book, or a practitioner to someone else, not a request for a remedy.
  // "suggest" has no unambiguous form worth keeping, so it was dropped outright.
  ['solutions', /\b(help(s)? with|what should i take|support for|symptom)\b|\bgood for\b(?!\s+(?:me|him|her|us|you|it)\b)|\b(?:my|a|this|that|health|medical|skin) condition\b|\bwhat do you recommend\b|\brecommend\s+(?:something|anything|a remedy|a supplement|for)\b/i],
  // Bare "course" and "class" over-match "of course" and "main course" (food),
  // and an unrelated "cooking class". Require a determiner that marks a specific
  // class or course being discussed, so a plain affirmation never routes here.
  ['learn',     /\b(masterclass|body map|calendar|event|webinar|classes|courses)\b|\b(?:a|my|the|next|which|this|any) (?:class|course)\b|\b(?:enroll|register|sign up)\b/i],
  // Bare "address" and "photo" over-match "address my concern" (a verb, nothing
  // to do with a mailing address) and "photo of my dog". Require the noun form
  // to carry the client's own possessive, or an explicit update/change verb.
  // Bare "profile" also over-matches a description of someone's public
  // standing ("a great profile as a healer"), nothing to do with the account.
  ['account',   /\b(my account|password|referral|ambassador|membership|preferences)\b|\b(?:my|shipping|billing|home|mailing|new) address\b|\b(?:update|change|upload) (?:my )?(?:address|photo)\b|\b(?:my|profile) photo\b|\bmy profile\b/i],
  // Bare "set up"/"setup" over-matches "set up a call" (scheduling, not
  // onboarding) and "my setup at home" (their equipment). The progressive
  // "setting up" reads as onboarding language on its own; "where am i" only
  // means the hub when it is the whole question, not buried in another ask.
  ['home',      /\b(next step|what.s next|setting up|get started|onboarding)\b|\bwhere am i\??\s*$/i]
];

function routeIntent(text) {
  if (!text || typeof text !== 'string') return null;
  for (var i = 0; i < INTENTS.length; i++) {
    if (INTENTS[i][1].test(text)) return INTENTS[i][0];
  }
  return null;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    DOORS: DOORS, panelsForDoor: panelsForDoor,
    doorForPanel: doorForPanel, allPanels: allPanels,
    renderRail: renderRail, renderPhoneHeader: renderPhoneHeader, renderComposer: renderComposer,
    escapeHtml: escapeHtml, routeIntent: routeIntent
  };
}
if (typeof window !== 'undefined') {
  window.PortalShell = {
    DOORS: DOORS, panelsForDoor: panelsForDoor,
    doorForPanel: doorForPanel, allPanels: allPanels,
    renderRail: renderRail, renderPhoneHeader: renderPhoneHeader, renderComposer: renderComposer,
    escapeHtml: escapeHtml, routeIntent: routeIntent
  };
}
