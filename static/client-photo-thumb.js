/* Client photo thumbnails for console pages (Glen, 2026-11-23).
 *
 * Any element with data-photo-email="<address>" gets a small round photo of that
 * client, if one is on file. Nothing shows when there is none: no broken image.
 *
 * The console key travels in the X-Console-Key header only, read from the same
 * localStorage entry every console page uses. It never goes in a URL. A browser
 * signed in by cookie needs no key at all, so the header is left off when empty.
 *
 * Light on the server: one request per address (cached), only for elements near the
 * screen, and at most 4 requests in flight at once. New rows are picked up as pages
 * re-render, through a MutationObserver.
 */
(function () {
  "use strict";
  if (window.ClientPhotoThumb) return;

  var MAX_IN_FLIGHT = 4;
  var cache = {};          // email -> Promise resolving to a data: URL or ""
  var queue = [];
  var inFlight = 0;

  function consoleKey() {
    try { return localStorage.getItem("console_key") || ""; } catch (e) { return ""; }
  }

  function pump() {
    while (inFlight < MAX_IN_FLIGHT && queue.length) {
      var job = queue.shift();
      inFlight++;
      var headers = {};
      var k = consoleKey();
      if (k) headers["X-Console-Key"] = k;
      fetch("/api/console/client-thumb?email=" + encodeURIComponent(job.email),
            { headers: headers, credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          var ok = d && d.ok && d.image && /^image\/(jpeg|png|webp)$/.test(d.content_type || "");
          return ok ? "data:" + d.content_type + ";base64," + d.image : "";
        })
        .catch(function () { return ""; })
        .then(function (url) {
          inFlight--;
          job.resolve(url);
          pump();
        });
    }
  }

  function photoFor(email) {
    var e = String(email || "").trim().toLowerCase();
    if (!e || e.indexOf("@") < 0) return Promise.resolve("");
    if (!cache[e]) {
      cache[e] = new Promise(function (resolve) {
        queue.push({ email: e, resolve: resolve });
        pump();
      });
    }
    return cache[e];
  }

  function fill(el) {
    if (el.getAttribute("data-photo-done")) return;
    el.setAttribute("data-photo-done", "1");
    photoFor(el.getAttribute("data-photo-email")).then(function (url) {
      if (!url) return;
      var size = parseInt(el.getAttribute("data-photo-size") || "32", 10) || 32;
      var img = document.createElement("img");
      img.alt = "";
      img.src = url;
      img.width = size; img.height = size;
      img.style.cssText = "width:" + size + "px;height:" + size + "px;border-radius:50%;"
        + "object-fit:cover;vertical-align:middle;margin-right:8px;flex:none;"
        + "border:1px solid rgba(127,127,127,.35)";
      img.onerror = function () { if (img.parentNode) img.parentNode.removeChild(img); };
      el.appendChild(img);
    });
  }

  var io = ("IntersectionObserver" in window) ? new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (en.isIntersecting) { io.unobserve(en.target); fill(en.target); }
    });
  }, { rootMargin: "200px" }) : null;

  function watch(el) {
    if (el.getAttribute("data-photo-seen")) return;
    el.setAttribute("data-photo-seen", "1");
    if (io) io.observe(el); else fill(el);
  }

  function scan(root) {
    var r = root || document;
    if (r.nodeType === 1 && r.hasAttribute && r.hasAttribute("data-photo-email")) watch(r);
    if (!r.querySelectorAll) return;
    var els = r.querySelectorAll("[data-photo-email]");
    for (var i = 0; i < els.length; i++) watch(els[i]);
  }

  /* Markup helper for pages that build rows as strings. */
  function html(email, size) {
    var e = String(email || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
    if (!e) return "";
    return '<span class="client-photo-thumb" data-photo-email="' + e + '"'
      + (size ? ' data-photo-size="' + Number(size) + '"' : "") + "></span>";
  }

  window.ClientPhotoThumb = { scan: scan, html: html, photoFor: photoFor };

  function start() {
    scan(document);
    if ("MutationObserver" in window) {
      new MutationObserver(function (muts) {
        muts.forEach(function (m) {
          for (var i = 0; i < m.addedNodes.length; i++) {
            if (m.addedNodes[i].nodeType === 1) scan(m.addedNodes[i]);
          }
        });
      }).observe(document.body, { childList: true, subtree: true });
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
