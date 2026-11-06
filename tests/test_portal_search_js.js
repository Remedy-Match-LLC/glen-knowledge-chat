// static/js/portal-search.js against a small fake DOM. Plain node + assert, like the
// other tests/*.js files. PORTAL_SEARCH_JS overrides the file under test (used to
// confirm a test fails on an older copy).
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(process.env.PORTAL_SEARCH_JS ||
  path.join(__dirname, "..", "static", "js", "portal-search.js"), "utf8");

function fakeEl(tag) {
  const kids = {};
  const handlers = {};
  return {
    tag, id: "", className: "", hidden: false, disabled: false, value: "", title: "",
    textContent: "", innerHTML: "", attrs: {}, classList: {add() {}, toggle() {}},
    style: {},
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(t, fn) { (handlers[t] = handlers[t] || []).push(fn); },
    fire(t, ev) { (handlers[t] || []).forEach(fn => fn(ev || {})); },
    querySelector(sel) { return kids[sel] || (kids[sel] = fakeEl(sel)); },
    appendChild() {}, insertBefore() {}, focus() {},
  };
}

function reply(rows) {
  return {ok: true, json: () => ({ok: true, total: rows.length, page: 1, pages: 1, results: rows})};
}
const flush = () => new Promise(r => setImmediate(r));

async function typeAndRun(env, input, value) {
  input.value = value;
  input.fire("input");
  const fn = env.timers.pop();
  if (fn) fn();
  await flush();
}

(async function main() {
  // Boot the script, keeping every element it creates so its panel can be found.
  function bootCapturing(search) {
    const created = [];
    const origCreate = fakeEl;
    const env = (function () {
      const timers = [], fetches = [], replaced = [], loads = [];
      const bar = origCreate("div");
      const doc = {
        readyState: "complete", head: origCreate("head"), body: origCreate("body"),
        createElement: (t) => { const e = origCreate(t); created.push(e); return e; },
        getElementById: (id) => (id === "journey-shell" ? bar : null),
        addEventListener() {},
      };
      const location = {pathname: "/portal/tok123", search: search || "", hash: ""};
      const ctx = {
        document: doc, location, URLSearchParams, AbortController, encodeURIComponent,
        console, HashChangeEvent: function () {},
        history: {replaceState(_s, _t, url) {
          replaced.push(url);
          const q = url.indexOf("?"), h = url.indexOf("#");
          location.search = q >= 0 ? url.slice(q, h >= 0 ? h : undefined) : "";
        }},
        setTimeout(fn) { timers.push(fn); return timers.length; },
        clearTimeout() {}, setInterval() { return 1; }, clearInterval() {},
        fetch(url, opts) {
          let resolve;
          const p = new Promise(r => { resolve = r; });
          fetches.push({url, signal: opts && opts.signal, resolve});
          return p;
        },
        load() { loads.push(ctx.selectedScanDate); },
        selectedScanDate: null,
      };
      ctx.window = ctx;
      vm.createContext(ctx);
      vm.runInContext(SRC, ctx);
      return {ctx, timers, fetches, replaced, loads, location};
    })();
    const panel = created.find(e => e.className === "ps-panel");
    env.input = panel.querySelector("#portalSearchInput");
    env.list = panel.querySelector("#portalSearchList");
    return env;
  }

  // 1. A response that arrives after the box was cleared never fills the list.
  {
    const env = bootCapturing("");
    await typeAndRun(env, env.input, "zeph");
    assert.strictEqual(env.fetches.length, 1);
    env.input.value = "";
    env.input.fire("input");                       // cleared before the timer fires
    env.fetches[0].resolve(reply([{source: "page", label: "Page", title: "Old", url: "/x", snippet_html: "old"}]));
    await flush(); await flush();
    assert.ok(!/Old/.test(env.list.innerHTML), "stale response repopulated a cleared search");
    assert.ok(env.fetches[0].signal && env.fetches[0].signal.aborted, "superseded fetch not aborted");
  }

  // 2. A slower older response never overwrites a newer one.
  {
    const env = bootCapturing("");
    await typeAndRun(env, env.input, "zeph");
    await typeAndRun(env, env.input, "zephyr");
    assert.strictEqual(env.fetches.length, 2);
    env.fetches[1].resolve(reply([{source: "page", label: "Page", title: "New", url: "/n", snippet_html: "n"}]));
    await flush(); await flush();
    env.fetches[0].resolve(reply([{source: "page", label: "Page", title: "Old", url: "/o", snippet_html: "o"}]));
    await flush(); await flush();
    assert.ok(/New/.test(env.list.innerHTML) && !/Old/.test(env.list.innerHTML));
    assert.ok(env.fetches[0].signal.aborted);
  }

  // 3. A caregiver viewing a member clicks their own report: member is dropped and the
  //    clicked report loads for the account holder.
  {
    const env = bootCapturing("?member=kid%40example.com&scan_date=2026-01-01");
    await typeAndRun(env, env.input, "zeph");
    env.fetches[0].resolve(reply([{source: "records", label: "Your records", kind: "report",
      title: "Remedy Match, 2026-10-02", url: "#biofield", scan_date: "2026-10-02", snippet_html: ""}]));
    await flush(); await flush();
    const a = {getAttribute: () => "0"};
    env.list.fire("click", {target: {closest: () => a}, preventDefault() {}});
    assert.strictEqual(env.replaced.length, 1, "member was not dropped from the address");
    assert.ok(!/member=/.test(env.replaced[0]) && !/scan_date=/.test(env.replaced[0]));
    assert.deepStrictEqual(env.loads, ["2026-10-02"]);
    assert.strictEqual(env.location.hash, "#biofield");
  }

  // 4. Without ?member= an order result just routes; nothing reloads.
  {
    const env = bootCapturing("");
    await typeAndRun(env, env.input, "zeph");
    env.fetches[0].resolve(reply([{source: "records", label: "Your records", kind: "order",
      title: "Order", url: "#orders", snippet_html: ""}]));
    await flush(); await flush();
    env.list.fire("click", {target: {closest: () => ({getAttribute: () => "0"})}, preventDefault() {}});
    assert.strictEqual(env.replaced.length, 0);
    assert.deepStrictEqual(env.loads, []);
    assert.strictEqual(env.location.hash, "#orders");
  }
  console.log("portal-search.js: 4 checks passed");
})().catch(e => { console.error(e); process.exit(1); });

