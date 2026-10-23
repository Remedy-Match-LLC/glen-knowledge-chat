/* mic-input.js — drop-in voice-to-text for any field marked with data-mic.
 *
 * Usage: add `data-mic` to a <textarea> or <input>, then include this script.
 *   <textarea id="notes" data-mic></textarea>
 *   <script src="/static/mic-input.js"></script>
 *
 * It injects a small mic button at the field's bottom-right. Click to record,
 * click again to stop; the audio is POSTed to /transcribe (Whisper) and the
 * returned text is inserted into the field. Dynamically-rendered fields are
 * picked up via a MutationObserver. No-op on browsers without MediaRecorder.
 *
 * Reuses the recorder pattern from embed.html.
 *
 * Voice needs a signed-in visitor (Glen, 2026-10-22). The script asks
 * /voice/sign-in-status once. When the visitor is not signed in, the mic button
 * shows a short sign-in note with an email box instead of recording, so nobody
 * speaks and is then refused. The box posts to /auth/magic-link/request, which
 * works for any address, and the emailed link returns them to this page. A 401
 * from /transcribe shows the same note. Typing is never affected.
 */
(function () {
  var SUPPORTED = !!(window.MediaRecorder && navigator.mediaDevices &&
                     navigator.mediaDevices.getUserMedia);
  if (!SUPPORTED) return;

  // ── styles (once) ──────────────────────────────────────────────────────────
  var css = ''
    + '.mic-wrap{position:relative;display:block;flex:1 1 auto;}'
    + '.mic-btn{position:absolute;bottom:6px;right:6px;z-index:5;'
    +   'width:26px;height:26px;display:flex;align-items:center;justify-content:center;'
    +   'border:1px solid #3a3a45;border-radius:6px;background:#1a1a22;color:#9aa0b4;'
    +   'font-size:13px;cursor:pointer;opacity:.75;'
    +   'transition:color .15s,border-color .15s,background .15s,opacity .15s;}'
    + '.mic-btn:hover{opacity:1;color:#e6edf3;border-color:#5a5a66;}'
    + '.mic-btn.recording{color:#f85149;border-color:#c0392b;background:#2d1010;opacity:1;'
    +   'animation:mic-pulse 1s ease-in-out infinite;}'
    + '.mic-btn.busy{opacity:.45;cursor:not-allowed;}'
    + '.mic-signin{display:block;margin-top:6px;font-size:12.5px;line-height:1.4;color:#9aa0b4;}'
    + '.mic-signin-box{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:4px;}'
    + '.mic-signin-box input{flex:1 1 160px;min-width:0;padding:5px 8px;font-size:13px;}'
    + '.mic-signin-box button{padding:5px 10px;font-size:13px;cursor:pointer;}'
    + '.mic-signin-msg{flex-basis:100%;}'
    + '@keyframes mic-pulse{0%,100%{box-shadow:0 0 0 0 rgba(248,81,73,.5);}50%{box-shadow:0 0 0 5px rgba(248,81,73,0);}}';
  var st = document.createElement('style');
  st.id = 'mic-input-styles';
  st.textContent = css;
  document.head.appendChild(st);

  // ── sign-in status (asked once per page) ──────────────────────────────────
  var SIGN_IN_MESSAGE = 'Please sign in to use voice. We will email you a link.';
  var signIn = { known: false, signedIn: true };

  function consoleHeaders() {
    // Staff pages keep their console key in localStorage. Sending it lets the
    // server recognise staff when the login cookie is absent. Same origin only.
    var h = {};
    try { var k = localStorage.getItem('console_key'); if (k) h['X-Console-Key'] = k; } catch (e) {}
    return h;
  }

  function checkSignIn() {
    return fetch('/voice/sign-in-status', { credentials: 'same-origin', headers: consoleHeaders() })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        signIn.known = true;
        signIn.signedIn = !!d.signed_in;
      })
      .catch(function () { /* unknown: let recording try, the 401 path still catches it */ });
  }

  function showSignIn(wrap, btn) {
    btn.title = 'Sign in to use voice';
    var note = wrap.querySelector('.mic-signin');
    if (!note) {
      note = document.createElement('span');
      note.className = 'mic-signin';
      note.setAttribute('role', 'status');
      wrap.appendChild(note);
    }
    if (note.querySelector('.mic-signin-box')) return;   // already offered
    note.textContent = SIGN_IN_MESSAGE + ' ';
    // A span, not a form: the field this button serves often sits in a form.
    var box = document.createElement('span');
    box.className = 'mic-signin-box';
    var input = document.createElement('input');
    input.type = 'email';
    input.placeholder = 'Your email';
    input.setAttribute('aria-label', 'Your email');
    var send = document.createElement('button');
    send.type = 'button';
    send.textContent = 'Email me a link';
    var msg = document.createElement('span');
    msg.className = 'mic-signin-msg';
    msg.setAttribute('role', 'status');
    function go(ev) {
      if (ev && ev.preventDefault) ev.preventDefault();
      if (ev && ev.stopPropagation) ev.stopPropagation();
      requestLink(input, send, msg);
    }
    send.addEventListener('click', go);
    input.addEventListener('keydown', function (ev) { if (ev && ev.key === 'Enter') go(ev); });
    box.appendChild(input); box.appendChild(send); box.appendChild(msg);
    note.appendChild(box);
  }

  function requestLink(input, send, msg) {
    var email = (input.value || '').trim();
    if (send.disabled) return;   // one request at a time; Enter repeats it (review round 2)
    if (email.indexOf('@') < 1) { msg.textContent = 'Please enter your email.'; return; }
    send.disabled = true;
    msg.textContent = 'Sending your link.';
    // Path only: a query string can carry ?key=<console secret>, which must never
    // travel in an email. The server allowlists the path again.
    var next = (window.location && window.location.pathname) || '/';
    fetch('/auth/magic-link/request', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, next: next }),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.ok) {
          msg.textContent = (d && d.note) || 'Check your email for the sign-in link.';
          if (d && d.note && d.note.indexOf('could not') !== -1) send.disabled = false;
        } else {
          msg.textContent = 'Please enter a valid email.';
          send.disabled = false;
        }
      });
    }).catch(function () {
      msg.textContent = 'Could not send the link. Please try again.';
      send.disabled = false;
    });
  }

  // ── per-button recorder ────────────────────────────────────────────────────
  function setState(btn, s) {
    btn.classList.remove('recording', 'busy');
    if (s === 'recording') { btn.classList.add('recording'); btn.textContent = '⏺'; }
    else if (s === 'busy') { btn.classList.add('busy'); btn.textContent = '…'; }
    else { btn.textContent = '🎙'; }
  }

  function insertText(field, text) {
    if (!text) return;
    var cur = field.value || '';
    if (!cur.trim()) field.value = text;
    else {
      // append at cursor if focused, else at end
      var sep = /\s$/.test(cur) ? '' : ' ';
      field.value = cur + sep + text;
    }
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.focus();
  }

  function wire(field) {
    if (field.dataset.micReady) return;
    field.dataset.micReady = '1';

    // wrap the field so the button can sit in its corner
    var wrap = document.createElement('span');
    wrap.className = 'mic-wrap';
    field.parentNode.insertBefore(wrap, field);
    wrap.appendChild(field);
    field.style.width = '100%';

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'mic-btn';
    btn.title = 'Dictate (voice to text)';
    btn.textContent = '🎙';
    wrap.appendChild(btn);

    var recorder = null, chunks = [], stream = null, recording = false;

    function stopTracks() { if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; } }

    function start() {
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
        stream = s;
        var mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus'
                 : (MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : '');
        recorder = mime ? new MediaRecorder(s, { mimeType: mime }) : new MediaRecorder(s);
        chunks = [];
        recorder.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
        recorder.onstop = onStop;
        recorder.start();
        recording = true;
        setState(btn, 'recording');
      }).catch(function (e) {
        console.warn('[mic] getUserMedia failed:', e);
        setState(btn, 'idle');
      });
    }

    function onStop() {
      stopTracks();
      setState(btn, 'busy');
      var type = (recorder && recorder.mimeType) || 'audio/webm';
      var blob = new Blob(chunks, { type: type });
      chunks = []; recorder = null; recording = false;
      var ext = type.indexOf('mp4') !== -1 ? 'clip.mp4' : 'clip.webm';
      var fd = new FormData();
      fd.append('audio', blob, ext);
      fetch('/transcribe', { method: 'POST', body: fd, credentials: 'same-origin', headers: consoleHeaders() })
        .then(function (r) {
          if (r.status === 401) {
            signIn.known = true; signIn.signedIn = false;
            showSignIn(wrap, btn);
            return null;
          }
          return r.ok ? r.json() : null;
        })
        .then(function (d) { insertText(field, d && d.text ? d.text.trim() : ''); })
        .catch(function (e) { console.warn('[mic] /transcribe failed:', e); })
        .finally(function () { setState(btn, 'idle'); });
    }

    btn.addEventListener('click', function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      if (btn.classList.contains('busy')) return;
      if (recording && recorder) { try { recorder.stop(); } catch (e) {} }
      else if (signIn.known && !signIn.signedIn) { showSignIn(wrap, btn); }
      else if (signIn.known) { start(); }
      else {
        // Not answered yet: ask first, so a stranger's audio is never uploaded.
        checkSignIn().then(function () {
          if (signIn.known && !signIn.signedIn) showSignIn(wrap, btn);
          else start();
        });
      }
    });
  }

  function scan(root) {
    var nodes = (root.querySelectorAll ? root.querySelectorAll('[data-mic]') : []);
    for (var i = 0; i < nodes.length; i++) wire(nodes[i]);
    if (root.matches && root.matches('[data-mic]')) wire(root);
  }

  function init() {
    checkSignIn();
    scan(document);
    new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var added = muts[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          if (added[j].nodeType === 1) scan(added[j]);
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
