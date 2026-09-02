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

console.log('test_portal_shell_responsive: ok');
