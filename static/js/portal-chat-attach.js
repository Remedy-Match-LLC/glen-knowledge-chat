// static/js/portal-chat-attach.js
// The portal chat's upload button (Glen, 2026-11-20; plan
// platform/plans/2026-11-20-portal-chat-upload). A paperclip beside each chat box.
// The file goes to POST /api/portal/<token>/chat/upload, which stores it in My Records
// and queues what is read for Glen's or Rae's approval, exactly as a My Records upload.
// sendChatMessage() then sends the ids, so the chat can read the file and answer.
//
// The page rebuilds its cards on every poll, so the buttons are re-added by an
// observer, never by id lookups alone. Nothing here throws into the page.
(function () {
  var TYPES = {
    "application/pdf": 10 * 1024 * 1024,
    "image/jpeg": 5 * 1024 * 1024,
    "image/png": 5 * 1024 * 1024,
    "image/webp": 5 * 1024 * 1024
  };
  var MAX_IN_CONVERSATION = 5;
  // Per message, as the server reads them (round 2): 2 documents and 3 photos.
  var PER_MESSAGE = { document: 2, image: 3 };
  var pending = [];   // uploaded, waiting to go with the next message
  var earlier = [];   // already sent in this conversation, kept for follow-up questions

  function tok() {
    try { return (typeof token !== "undefined" && token) ? token : ""; } catch (e) { return ""; }
  }

  function chipsHost(row) {
    var host = row.nextElementSibling;
    if (!host || !host.classList || !host.classList.contains("chat-attach-chips")) {
      host = document.createElement("div");
      host.className = "chat-attach-chips";
      row.parentNode.insertBefore(host, row.nextSibling);
    }
    return host;
  }

  function renderChips() {
    var rows = document.querySelectorAll(".chat-input-row[data-attach]");
    for (var r = 0; r < rows.length; r++) {
      var host = chipsHost(rows[r]);
      host.textContent = "";
      for (var i = 0; i < pending.length; i++) {
        var c = document.createElement("span");
        c.className = "chat-attach-chip" + (pending[i].state === "error" ? " err" : "");
        c.textContent = "\u{1F4CE} " + pending[i].name +
          (pending[i].state === "uploading" ? " …" : "") +
          (pending[i].state === "saved" ? ": saved for review" : "") +
          (pending[i].state === "error" ? ": " + pending[i].error : "");
        if (pending[i].state !== "uploading") {
          var x = document.createElement("button");
          x.type = "button";
          x.className = "chat-attach-x";
          x.setAttribute("aria-label", "Remove " + pending[i].name);
          x.textContent = "×";
          x.dataset.idx = String(i);
          c.appendChild(x);
        }
        host.appendChild(c);
      }
    }
  }

  function upload(file) {
    var ctype = (file.type || "").toLowerCase();
    var kind = ctype === "application/pdf" ? "document" : "image";
    var item = { name: file.name || "file", state: "uploading", id: null, error: "", kind: kind };
    var limit = TYPES[ctype];
    var sameKind = pending.filter(function (p) {
      return p.kind === kind && p.state !== "error";
    }).length;
    if (!limit) {
      item.state = "error"; item.error = "please send a PDF, or a JPEG, PNG or WebP photo";
    } else if (file.size > limit) {
      item.state = "error"; item.error = "over " + (limit / 1048576) + " MB";
    } else if (sameKind >= PER_MESSAGE[kind]) {
      item.state = "error";
      item.error = "up to 2 documents and 3 photos per message";
    }
    pending.push(item);
    renderChips();
    if (item.state === "error") return;
    var fd = new FormData();
    fd.append("file", file);
    fetch("/api/portal/" + encodeURIComponent(tok()) + "/chat/upload",
          { method: "POST", credentials: "same-origin", body: fd })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (j) {
        // A copy the team already holds and has not shared is saved, not read.
        if (j && j.ok) { item.id = j.id; item.state = j.readable === false ? "saved" : "ready"; }
        else { item.state = "error"; item.error = (j && j.error) || "could not upload"; }
        renderChips();
      })
      .catch(function () { item.state = "error"; item.error = "could not upload"; renderChips(); });
  }

  function addButton(row) {
    if (row.getAttribute("data-attach")) return;
    if (!row.querySelector("#chatInput, #chatCardInput")) return;
    row.setAttribute("data-attach", "1");
    var input = document.createElement("input");
    input.type = "file";
    input.accept = "application/pdf,image/jpeg,image/png,image/webp";
    input.multiple = true;
    input.hidden = true;
    input.className = "chat-attach-input";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chat-attach-btn";
    btn.setAttribute("aria-label", "Attach a document or photo");
    btn.title = "Attach a document or photo";
    btn.textContent = "\u{1F4CE}";
    btn.addEventListener("click", function () { input.click(); });
    input.addEventListener("change", function () {
      var fs = input.files || [];
      for (var i = 0; i < fs.length; i++) upload(fs[i]);
      input.value = "";
    });
    var send = row.querySelector("#chatSend, #chatCardSend");
    row.insertBefore(btn, send || null);
    row.appendChild(input);
    renderChips();
  }

  function scan() {
    var rows = document.querySelectorAll(".chat-input-row");
    for (var i = 0; i < rows.length; i++) addButton(rows[i]);
  }

  document.addEventListener("click", function (e) {
    var x = e.target && e.target.closest && e.target.closest(".chat-attach-x");
    if (!x) return;
    pending.splice(Number(x.dataset.idx), 1);
    renderChips();
  });

  try {
    new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });
  } catch (e) { /* older browsers: the first scan still runs */ }
  scan();

  window.PortalChatAttach = {
    // True while a file is still on its way up: the message waits for it.
    busy: function () {
      return pending.some(function (p) { return p.state === "uploading"; });
    },
    ready: function () {
      return pending.filter(function (p) { return p.state === "ready"; });
    },
    // Called when a message is sent. Returns what to send, and moves the new files
    // to "earlier" so follow-up questions can still be about them.
    take: function () {
      var fresh = pending.filter(function (p) { return p.state === "ready"; });
      var newIds = fresh.map(function (p) { return p.id; });
      earlier = earlier.concat(fresh).slice(-MAX_IN_CONVERSATION);
      // A file still uploading stays for the next message (round 2).
      pending = pending.filter(function (p) { return p.state === "uploading"; });
      renderChips();
      return {
        new_files: newIds,
        files: earlier.map(function (p) { return p.id; }),
        names: fresh.map(function (p) { return p.name; })
      };
    }
  };
})();
