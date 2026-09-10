/* text-size.js — per-viewer text magnification, on any page that includes it.
 *
 * Glen, 2026-09-09: a magnification control on each page for the font size.
 *
 * Scales the root font size, so every `rem` on the page grows with it. Fixed-px
 * chrome (the 52px portal rail, header heights) deliberately does not move: text
 * gets bigger, the furniture stays put, and no layout breaks.
 *
 * Deliberately mirrors theme-mode.js: one singleton on window, localStorage in a
 * try/catch, a mountControl(container) the page calls where it wants the buttons,
 * a pure step function exported for unit tests, and an auto-run guarded so Node
 * can require this file.
 *
 * Usage:
 *   <script src="/static/text-size.js"></script>
 *   window.RMTextSize.mountControl(document.getElementById('textSize'));
 *
 * The saved size applies on load whether or not anything is mounted, so a viewer
 * who set it once keeps it on every page that includes the script.
 */
(function () {
  if (window.RMTextSize) return;

  var KEY = 'rm-text-scale';
  // Percentages of the browser's own base size. 100 is the default, so a viewer
  // who never touches this sees exactly what they saw before.
  var STEPS = [100, 115, 130, 150, 175];
  var DEFAULT = 100;

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  /* Pure: any input -> a percentage that is actually one of our steps. Unit
     tested. Anything unparseable or out of range lands on the nearest step, so a
     stale or hand-edited value can never leave the page at an absurd size. */
  function _normalize(value) {
    var n = parseFloat(value);
    if (!isFinite(n)) return DEFAULT;
    var best = STEPS[0];
    for (var i = 0; i < STEPS.length; i++) {
      if (Math.abs(STEPS[i] - n) < Math.abs(best - n)) best = STEPS[i];
    }
    return best;
  }

  /* Pure: current percentage + direction -> the next step, clamped at both ends. */
  function _step(current, direction) {
    var i = STEPS.indexOf(_normalize(current));
    var next = i + (direction > 0 ? 1 : direction < 0 ? -1 : 0);
    if (next < 0) next = 0;
    if (next > STEPS.length - 1) next = STEPS.length - 1;
    return STEPS[next];
  }

  function get() { return _normalize(lsGet(KEY)); }

  function apply() {
    var pct = get();
    var root = document.documentElement;
    // 100 clears the override rather than writing 100%, so the browser's own
    // font-size setting keeps working for anyone who has changed it.
    if (pct === DEFAULT) root.style.removeProperty('font-size');
    else root.style.fontSize = pct + '%';
    root.setAttribute('data-text-scale', String(pct));
    try {
      document.dispatchEvent(new CustomEvent('rm-text-size-change',
        { detail: { scale: pct } }));
    } catch (e) {}
    return pct;
  }

  function set(pct) {
    lsSet(KEY, String(_normalize(pct)));
    return apply();
  }

  function bump(direction) { return set(_step(get(), direction)); }
  function reset() { return set(DEFAULT); }

  // ── styles (once) ──────────────────────────────────────────────────────────
  // Self-injected so the control looks right on a page that never loads
  // shell.css. Shape and colours follow .rm-theme-seg, with its own class names:
  // shell.js finds the theme control by `.rm-theme-seg`, and sharing the class
  // would let it grab this one instead.
  function styles() {
    if (document.getElementById('rm-text-size-styles')) return;
    var css = ''
      + '.rm-text-seg{display:inline-flex;border:1px solid var(--hair,#21472d);'
      +   'border-radius:999px;overflow:hidden;flex:0 0 auto}'
      + '.rm-text-seg-btn{appearance:none;border:none;background:transparent;'
      +   'color:var(--dim,#8fa89b);padding:5px 9px;cursor:pointer;'
      +   'display:inline-flex;align-items:center;justify-content:center;'
      +   'font:inherit;line-height:1}'
      + '.rm-text-seg-btn+.rm-text-seg-btn{border-left:1px solid var(--hair,#21472d)}'
      + '.rm-text-seg-btn[disabled]{opacity:.4;cursor:default}'
      + '.rm-text-seg-btn:focus-visible{outline:2px solid var(--gold,#d4a843);'
      +   'outline-offset:2px}'
      + '.rm-text-seg-sm{font-size:.78rem}'
      + '.rm-text-seg-lg{font-size:1.05rem}'
      + '.rm-text-seg-now{font-variant-numeric:tabular-nums;min-width:3.2em;'
      +   'text-align:center}';
    var st = document.createElement('style');
    st.id = 'rm-text-size-styles';
    st.textContent = css;
    (document.head || document.documentElement).appendChild(st);
  }

  function mountControl(container) {
    if (!container) return null;
    styles();
    var seg = document.createElement('div');
    seg.className = 'rm-text-seg';
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', 'Text size');

    function button(cls, label, title, onClick) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'rm-text-seg-btn ' + cls;
      b.textContent = label;
      b.title = title;
      b.setAttribute('aria-label', title);
      b.onclick = onClick;
      return b;
    }

    var smaller = button('rm-text-seg-sm', 'A', 'Smaller text',
      function () { bump(-1); });
    var now = button('rm-text-seg-now', '100%', 'Text size, click for normal',
      function () { reset(); });
    var bigger = button('rm-text-seg-lg', 'A', 'Larger text',
      function () { bump(1); });

    seg.appendChild(smaller);
    seg.appendChild(now);
    seg.appendChild(bigger);
    container.appendChild(seg);

    function refresh() {
      var pct = get();
      now.textContent = pct + '%';
      smaller.disabled = pct === STEPS[0];
      bigger.disabled = pct === STEPS[STEPS.length - 1];
    }
    document.addEventListener('rm-text-size-change', refresh);
    refresh();
    return seg;
  }

  /* Put the control next to the theme toggle, wherever that toggle ended up.
   *
   * Three different places mount the theme toggle: shell.js into the ribbon,
   * op-nav.js into the console nav, and about a dozen pages into their own
   * #themeToggle. Rather than edit all three plus every page, this watches for
   * `.rm-theme-seg` to appear and sits beside it. The observer is needed because
   * the ribbon is built after this script runs.
   *
   * Does nothing on a page with no theme toggle. Such a page can still call
   * mountControl itself, and the saved size already applies either way.
   */
  function autoMount() {
    if (!document.body) return false;
    if (document.querySelector('.rm-text-seg')) return true;   // already placed
    var theme = document.querySelector('.rm-theme-seg');
    if (!theme || !theme.parentNode) return false;
    var holder = document.createElement('span');
    holder.style.display = 'inline-flex';
    holder.style.marginLeft = '6px';
    theme.parentNode.insertBefore(holder, theme.nextSibling);
    return !!mountControl(holder);
  }

  function watchForToggle() {
    if (autoMount()) return;
    if (typeof MutationObserver === 'undefined') return;
    var obs = new MutationObserver(function () {
      if (autoMount()) obs.disconnect();
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    // Stop watching after a while rather than observing the document forever.
    setTimeout(function () { obs.disconnect(); }, 15000);
  }

  function init() {
    apply();
    // Follow a change made in another same-origin document, the way theme-mode does.
    window.addEventListener('storage', function (e) {
      if (e.key === KEY) apply();
    });
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', watchForToggle);
    } else {
      watchForToggle();
    }
  }

  window.RMTextSize = {
    STEPS: STEPS, DEFAULT: DEFAULT,
    _normalize: _normalize, _step: _step,
    get: get, set: set, bump: bump, reset: reset, apply: apply,
    mountControl: mountControl, autoMount: autoMount, init: init
  };

  if (typeof document !== 'undefined' && document.documentElement
      && typeof localStorage !== 'undefined') {
    init();
  }
})();
