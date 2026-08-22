(function () {
  "use strict";

  function mount(data) {
    var host = document.querySelector(".brandbar") || document.body;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "page-share-button";
    button.textContent = "Share this page";
    button.setAttribute("aria-label", "Share this page with your personal referral link");
    button.title = data.disclosure || "Share your personal referral link";

    var style = document.createElement("style");
    style.textContent =
      ".page-share-button{position:fixed;top:14px;left:14px;z-index:9998;" +
      "border:1px solid rgba(212,168,67,.55);border-radius:999px;padding:9px 15px;" +
      "background:#111f16;color:#fdf4d8;font:600 13px/1.2 system-ui,sans-serif;" +
      "cursor:pointer;box-shadow:0 4px 18px rgba(0,0,0,.2)}" +
      ".page-share-button:hover,.page-share-button:focus-visible{border-color:#d4a843;outline:none}" +
      "@media(max-width:560px){.page-share-button{position:static;margin-left:auto;padding:8px 12px}}";
    document.head.appendChild(style);
    host.appendChild(button);

    button.addEventListener("click", async function () {
      try {
        if (navigator.share) {
          await navigator.share({title: data.title || document.title, url: data.share_url});
          return;
        }
        await navigator.clipboard.writeText(data.share_url);
        button.textContent = "Link copied";
      } catch (err) {
        if (err && err.name === "AbortError") return;
        window.prompt("Copy your personal referral link:", data.share_url);
      }
      window.setTimeout(function () { button.textContent = "Share this page"; }, 2200);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    fetch("/api/page-share-link?path=" + encodeURIComponent(location.pathname), {
      credentials: "same-origin"
    }).then(function (response) {
      return response.ok ? response.json() : null;
    }).then(function (data) {
      if (data && data.ok && data.share_url) mount(data);
    }).catch(function () {});
  });
})();

