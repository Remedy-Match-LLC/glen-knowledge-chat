/* Portal header search (plan 2026-11-06-portal-header-site-search, step 1).
   Injected only while PORTAL_SEARCH_ENABLED is on (dashboard/portal_search.py).
   A magnifier in the portal header opens a search box. Escape or a second click
   closes it. Results come from /api/portal/<token>/site-search, 10 to a page.
   Titles are escaped here. snippet_html arrives escaped and bolded by the server. */
(function () {
  if (window.__portalSearchBooted) return;
  window.__portalSearchBooted = true;

  var m = location.pathname.match(/^\/portal\/([^\/?#]+)/);
  if (!m) return;
  var TOKEN = m[1];
  var ON_HOME = /^\/portal\/[^\/]+\/?$/.test(location.pathname);
  var MIN = 2, DEBOUNCE_MS = 250;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }

  var ICON = '<svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" width="16" height="16" ' +
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<circle cx="11" cy="11" r="7"></circle><path d="M21 21l-4.35-4.35"></path></svg>';

  var css = document.createElement("style");
  css.textContent =
    ".ps-btn{display:inline-flex;align-items:center;justify-content:center;gap:4px;cursor:pointer;" +
    "background:transparent;color:inherit;border:1px solid var(--line,rgba(128,128,128,.4));" +
    "border-radius:15px;height:30px;min-width:34px;padding:0 9px}" +
    ".ps-btn[aria-expanded=true]{background:var(--brand-soft,rgba(128,128,128,.18))}" +
    ".ps-btn.ps-float{position:fixed;top:12px;right:12px;z-index:10030;" +
    "background:var(--card,#fff);color:var(--ink,#1E2A2A)}" +
    ".ps-panel{position:fixed;top:56px;right:12px;z-index:10031;width:min(560px,calc(100vw - 24px));" +
    "max-height:calc(100vh - 80px);overflow:auto;background:var(--card,#fff);color:var(--ink,#1E2A2A);" +
    "border:1px solid var(--line,#ddd);border-radius:12px;box-shadow:0 10px 28px rgba(0,0,0,.28);padding:12px}" +
    ".ps-panel[hidden]{display:none}" +
    ".ps-input{width:100%;box-sizing:border-box;font:inherit;padding:8px 10px;border-radius:8px;" +
    "border:1px solid var(--line,#ccc);background:var(--card2,transparent);color:inherit}" +
    ".ps-status{font-size:.85rem;color:var(--muted,#5F6B6B);margin:8px 2px}" +
    ".ps-list{list-style:none;margin:0;padding:0}" +
    ".ps-item{padding:9px 2px;border-top:1px solid var(--line,#eee)}" +
    ".ps-item a{color:var(--brand,#2D7A6A);font-weight:700;text-decoration:none}" +
    ".ps-item a:hover{text-decoration:underline}" +
    ".ps-label{display:inline-block;font-size:.7rem;font-weight:700;margin-left:6px;padding:1px 7px;" +
    "border-radius:9px;background:var(--brand-soft,rgba(128,128,128,.18));color:var(--muted,#5F6B6B)}" +
    ".ps-snip{font-size:.85rem;margin-top:3px;line-height:1.4}" +
    ".ps-buy{font-size:.8rem;margin-top:3px;color:var(--muted,#5F6B6B)}" +
    ".ps-pager{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:10px}" +
    ".ps-pager button{font:inherit;cursor:pointer;padding:4px 10px;border-radius:8px;" +
    "border:1px solid var(--line,#ccc);background:transparent;color:inherit}" +
    ".ps-pager button[disabled]{opacity:.4;cursor:default}";
  document.head.appendChild(css);

  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ps-btn";
  btn.id = "portalSearchBtn";
  btn.setAttribute("aria-label", "Search");
  btn.setAttribute("aria-expanded", "false");
  btn.setAttribute("aria-controls", "portalSearchPanel");
  btn.title = "Search";
  btn.innerHTML = ICON;

  var panel = document.createElement("div");
  panel.className = "ps-panel";
  panel.id = "portalSearchPanel";
  panel.setAttribute("role", "search");
  panel.hidden = true;
  panel.innerHTML =
    '<input class="ps-input" id="portalSearchInput" type="search" autocomplete="off" ' +
    'placeholder="Search your records, the store and our pages" aria-label="Search">' +
    '<div class="ps-status" id="portalSearchStatus" aria-live="polite">Type at least 2 letters.</div>' +
    '<ul class="ps-list" id="portalSearchList"></ul>' +
    '<div class="ps-pager" id="portalSearchPager" hidden>' +
    '<button type="button" id="portalSearchPrev">Previous</button>' +
    '<span class="ps-status" id="portalSearchPageNo"></span>' +
    '<button type="button" id="portalSearchNext">Next</button></div>';

  var input = panel.querySelector("#portalSearchInput");
  var statusEl = panel.querySelector("#portalSearchStatus");
  var list = panel.querySelector("#portalSearchList");
  var pager = panel.querySelector("#portalSearchPager");
  var prev = panel.querySelector("#portalSearchPrev");
  var next = panel.querySelector("#portalSearchNext");
  var pageNo = panel.querySelector("#portalSearchPageNo");

  var state = {q: "", page: 1, pages: 1, seq: 0, timer: null, ctl: null};

  // Every input change and every clear starts a new generation and aborts the fetch in
  // flight, so an older response can never fill a cleared or changed search.
  function supersede() {
    state.seq++;
    if (state.ctl) { try { state.ctl.abort(); } catch (e) {} }
    state.ctl = null;
    return state.seq;
  }

  function open() {
    panel.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    input.focus();
  }
  function close() {
    panel.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  }
  btn.addEventListener("click", function () { panel.hidden ? open() : close(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !panel.hidden) { close(); btn.focus(); }
  });

  function clear(msg) {
    supersede();
    list.innerHTML = "";
    pager.hidden = true;
    statusEl.textContent = msg;
  }

  function render(d) {
    var rows = (d && d.results) || [];
    state.page = d.page || 1;
    state.pages = d.pages || 1;
    if (!rows.length) { clear("Nothing found."); return; }
    statusEl.textContent = d.total + (d.total === 1 ? " result" : " results");
    list.innerHTML = rows.map(function (r, i) {
      var buy = "";
      if (r.source === "store") {
        buy = '<div class="ps-buy">' + (r.orderable
          ? (r.price ? esc(r.price) + " · " : "") + "Available to order"
          : "Not available to order right now") + "</div>";
      }
      return '<li class="ps-item"><a href="' + esc(r.url) + '" data-ps-i="' + i + '">' +
        esc(r.title) + '</a><span class="ps-label">' + esc(r.label) + "</span>" +
        '<div class="ps-snip">' + (r.snippet_html || "") + "</div>" + buy + "</li>";
    }).join("");
    list._rows = rows;
    pager.hidden = state.pages <= 1;
    prev.disabled = state.page <= 1;
    next.disabled = state.page >= state.pages;
    pageNo.textContent = "Page " + state.page + " of " + state.pages;
  }

  function run(page) {
    var q = state.q;
    if (q.length < MIN) { clear("Type at least 2 letters."); return; }
    var seq = supersede();
    var ctl = (typeof AbortController === "function") ? new AbortController() : null;
    state.ctl = ctl;
    statusEl.textContent = "Searching…";
    fetch("/api/portal/" + encodeURIComponent(TOKEN) + "/site-search?q=" +
          encodeURIComponent(q) + "&page=" + (page || 1),
          {credentials: "same-origin", signal: ctl ? ctl.signal : undefined})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (seq !== state.seq) return;          // a newer search has started
        if (!d || !d.ok) { clear("Search is not available right now."); return; }
        render(d);
      })
      .catch(function () { if (seq === state.seq) clear("Search is not available right now."); })
      .then(function () { if (state.ctl === ctl) state.ctl = null; });
  }

  input.addEventListener("input", function () {
    state.q = input.value.trim();
    clearTimeout(state.timer);
    supersede();
    state.timer = setTimeout(function () { run(1); }, DEBOUNCE_MS);
  });
  prev.addEventListener("click", function () { if (state.page > 1) run(state.page - 1); });
  next.addEventListener("click", function () { if (state.page < state.pages) run(state.page + 1); });

  // A record link is a portal route (#orders, #chat, #biofield). On the portal home it
  // routes in place; on another portal page it opens the portal home at that route.
  list.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[data-ps-i]");
    if (!a) return;
    var r = (list._rows || [])[+a.getAttribute("data-ps-i")];
    if (!r || r.source !== "records" || (r.url || "").charAt(0) !== "#") return;
    e.preventDefault();
    close();
    if (!ON_HOME) { location.href = "/portal/" + encodeURIComponent(TOKEN) + r.url; return; }
    // Search covers the account holder only. A caregiver viewing a member (?member=)
    // returns to their own view: drop member and scan_date from the address, then
    // reload the portal in place, on the clicked report when there is one.
    var params = new URLSearchParams(location.search);
    var viewingMember = params.has("member");
    if (viewingMember) {
      params.delete("member");
      params.delete("scan_date");
      var qs = params.toString();
      history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash);
    }
    if (r.scan_date || viewingMember) {
      // selectedScanDate and load() are the portal page's own globals.
      try { selectedScanDate = r.scan_date || null; load(); } catch (err) {}
    }
    if (location.hash === r.url) window.dispatchEvent(new HashChangeEvent("hashchange"));
    else location.hash = r.url;
  });

  // Mount in the portal header (shell.js's #journey-shell) beside the theme control.
  // When there is no header after a short wait, float the button at the top right.
  function mount() {
    var bar = document.getElementById("journey-shell");
    if (!bar) return false;
    var theme = bar.querySelector(".rm-theme-seg");
    bar.insertBefore(btn, theme || null);
    return true;
  }
  function start() {
    document.body.appendChild(panel);
    if (mount()) return;
    var tries = 0;
    var iv = setInterval(function () {
      if (mount()) { clearInterval(iv); return; }
      if (++tries >= 20) {
        clearInterval(iv);
        btn.classList.add("ps-float");
        document.body.appendChild(btn);
      }
    }, 150);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
  else start();
})();
