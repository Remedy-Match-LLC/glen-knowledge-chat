// tests/test_portal_shell_responsive.js
// Run: node tests/test_portal_shell_responsive.js
//
// The rules live in a media query, so assert on the parsed rule text rather than on
// a rendered box. Render verification against a real portal happens in Task 12.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const page = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'client-portal.html'), 'utf8');
const css = (page.match(/<style>[\s\S]*?<\/style>/g) || []).join('\n');

function ruleFor(selector, within) {
  const scope = within ? (new RegExp('@media[^{]*' + within + '[^{]*\\{([\\s\\S]*?)\\n  \\}')
    .exec(css) || [, ''])[1] : css;
  const m = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') +
    '\\s*\\{([^}]*)\\}').exec(scope);
  return m ? m[1] : '';
}

// phone: the bar is shown, the rail is off-canvas until opened
const phoneBar = ruleFor('.shell-bar', 'max-width:760px');
assert.ok(/display\s*:\s*flex/.test(phoneBar), 'the phone header must show under 760px');
const phoneRail = ruleFor('.portal-rail', 'max-width:760px');
assert.ok(/position\s*:\s*fixed|position\s*:\s*absolute/.test(phoneRail),
  'the phone rail must overlay, not take layout width');
assert.ok(/translateX\(-100%\)|left\s*:\s*-/.test(phoneRail),
  'the phone rail must start off-canvas');

// desktop: the bar is gone, the rail holds layout width
assert.ok(/display\s*:\s*none/.test(ruleFor('.shell-bar')),
  'the header must be hidden at desktop width by default');
const deskRail = ruleFor('.portal-rail');
assert.ok(/width\s*:\s*52px/.test(deskRail), 'the desktop rail is 52px collapsed');
assert.ok(/\.portal-rail\.is-open[^{]*\{[^}]*width\s*:\s*176px/.test(css),
  'the open rail is 176px');

// reduced motion is honoured
assert.ok(/prefers-reduced-motion/.test(css));

// The scrim dims the page behind the phone drawer. On desktop the rail widens in
// place, so an activation rule at the top level dimmed and click-blocked the whole
// content column at every width. Pin it inside the phone block so that cannot return.
const ACT = '.portal-rail.is-open ~ .shell-scrim';
assert.strictEqual(css.split(ACT).length - 1, 1,
  'the scrim activation rule must appear exactly once');
const mq760 = css.indexOf('@media (max-width:760px)');
const mq760End = css.indexOf('@media (prefers-reduced-motion', mq760);
const actAt = css.indexOf(ACT);
assert.ok(mq760 !== -1 && actAt > mq760 && actAt < mq760End,
  'the scrim activation must live inside the max-width:760px block, not at top level');

// Rail gutter: the rail is position:fixed and out of flow, so the gutter it needs
// must be reserved on body (padding-left), not by overriding .wrap's own centring
// with a margin-left. A margin-left on .wrap breaks max-width:648px;margin:0 auto,
// leaving .wrap pinned to the left edge of the remaining space instead of centred
// in it, while .shell-composer (outside .wrap, centred independently) drifts off
// to a different, wider centre. See the live measurement in the commit message.
assert.strictEqual(/body\.has-shell\s+\.wrap\s*\{[^}]*margin-left/.test(css), false,
  'body.has-shell .wrap must not override .wrap centring with margin-left');
assert.ok(/body\.has-shell\s*\{[^}]*padding-left\s*:\s*52px/.test(css),
  'body.has-shell must reserve the rail gutter via padding-left:52px instead');
const phonePad = ruleFor('body.has-shell', 'max-width:760px');
assert.ok(/padding-left\s*:\s*0/.test(phonePad),
  'the max-width:760px block must zero body.has-shell padding-left, not .wrap margin-left');
assert.strictEqual(/max-width:760px[\s\S]*?body\.has-shell\s+\.wrap\s*\{[^}]*margin-left/.test(css), false,
  'the phone block must not carry a body.has-shell .wrap margin-left override either');

// Task 13 (portal-shell-ia): rail badges and descriptions must not blow out the
// 176px phone drawer or the 52px collapsed desktop rail.
//
// The description wraps rather than overflows, and is hidden until the rail is
// open (collapsed by default, the same on/off shape .rail-text already used).
const railDesc = ruleFor('.rail-desc');
assert.ok(/white-space\s*:\s*normal/.test(railDesc),
  'the description must wrap at the 176px drawer width, not overflow in one line');
assert.ok(/display\s*:\s*none/.test(railDesc),
  'the description must be hidden by default, shown only once the rail opens');
assert.ok(/\.portal-rail\.is-open \.rail-desc\s*\{[^}]*display\s*:\s*block/.test(css),
  'the description must be shown once the rail opens');

// .rail-text is a flex child beside the icon; without min-width:0 a long
// description cannot shrink/wrap and can push the rail wider than 176px.
const railText = ruleFor('.rail-text');
assert.ok(/min-width\s*:\s*0/.test(railText),
  '.rail-text needs min-width:0 so the description cannot force the rail wider than 176px');

// The icon-corner badge must not add layout width to the 52px collapsed rail
// (it overlays the icon via position:absolute).
//
// It must also be a SOLID fill, not the translucent .pill background. The pill
// treatment works beside a label, where there is room and a card behind it, but
// as a 15px corner badge on a dark rail it renders as a superscript digit rather
// than a count and is easy to miss entirely, which defeats the point of having
// it. Verified by rendering, not by reading. The .pill itself still carries the
// count beside the label once the rail opens, which is where that treatment
// belongs.
const railBadge = ruleFor('.rail-badge');
assert.ok(/position\s*:\s*absolute/.test(railBadge),
  'the collapsed-rail badge must overlay the icon, not add layout width at 52px');
assert.ok(/background\s*:\s*var\(--brand\)/.test(railBadge),
  'the badge needs a solid fill to read as a count in the collapsed rail');
assert.ok(!/background\s*:\s*var\(--brand-soft\)/.test(railBadge),
  'the translucent pill background disappears at badge size, do not reuse it here');
assert.ok(/box-shadow\s*:[^;]*var\(--card2\)/.test(railBadge),
  'the badge needs a ring in the rail colour to separate it from the icon beneath');
// it hides once the rail opens, handing off to the .pill count beside the label
assert.ok(/\.portal-rail\.is-open \.rail-item \.rail-badge\s*\{[^}]*display\s*:\s*none/.test(css),
  'the collapsed-rail badge must hide once the rail opens');

console.log('test_portal_shell_responsive: ok');
