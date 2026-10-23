/* voice-signin.js: the sign-in note for pages with their own voice recorder.
 *
 * Glen, 2026-10-22: voice journaling and dictation require the visitor to be
 * signed in by an emailed link. The server refuses voice with HTTP 401. This
 * helper lets a page ask up front (so nobody records and is then refused) and
 * show one short note with an email box. The box posts to
 * /auth/magic-link/request, which works for any address, and the emailed link
 * brings the visitor back to this page. Typed chat is never affected.
 *
 * mic-input.js carries its own copy of this logic so it stays a one-tag drop-in.
 *
 *   VoiceSignIn.check().then(function (s) { if (s.signedIn === false) ... });
 *   VoiceSignIn.render(el)           // writes the note and email box into el
 *   VoiceSignIn.handle(resp, el)     // true (and renders) when resp is a 401
 */
(function () {
  if (window.VoiceSignIn) return;

  var MESSAGE = 'Please sign in to use voice. We will email you a link.';
  var REQUEST_URL = '/auth/magic-link/request';
  var state = { signedIn: null };
  var pending = null;

  (function styles() {
    if (document.getElementById('voice-signin-styles')) return;
    var st = document.createElement('style');
    st.id = 'voice-signin-styles';
    st.textContent = '.voice-signin-box{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px;}'
      + '.voice-signin-box input{flex:1 1 180px;min-width:0;padding:6px 8px;font-size:14px;}'
      + '.voice-signin-box button{padding:6px 10px;font-size:14px;cursor:pointer;}'
      + '.voice-signin-msg{flex-basis:100%;font-size:13px;}';
    (document.head || document.documentElement).appendChild(st);
  })();

  function headers() {
    var h = {};
    try { var k = localStorage.getItem('console_key'); if (k) h['X-Console-Key'] = k; } catch (e) {}
    // Staff who opened the page with ?key= are staff for the status check too. It goes
    // in a header to our own server, never into an email (review round 2).
    try {
      var q = new URLSearchParams(window.location.search).get('key');
      if (q && !h['X-Console-Key']) h['X-Console-Key'] = q;
    } catch (e) {}
    return h;
  }

  function check() {
    if (pending) return pending;
    pending = fetch('/voice/sign-in-status', { credentials: 'same-origin', headers: headers() })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d) state.signedIn = !!d.signed_in;
        return { signedIn: state.signedIn };
      })
      .catch(function () { return { signedIn: null }; });
    return pending;
  }

  // Path only. A query string can carry ?key=<console secret>, which must never
  // travel in an email. The server allowlists the path again.
  function here() { return (window.location && window.location.pathname) || '/'; }

  function send(input, btn, msg) {
    var email = (input.value || '').trim();
    if (btn.disabled) return;   // one request at a time; Enter repeats it (review round 2)
    if (email.indexOf('@') < 1) { msg.textContent = 'Please enter your email.'; return; }
    btn.disabled = true;
    msg.textContent = 'Sending your link.';
    fetch(REQUEST_URL, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, next: here() }),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.ok) {
          msg.textContent = (d && d.note) || 'Check your email for the sign-in link.';
          if (d && d.note && d.note.indexOf('could not') !== -1) btn.disabled = false;
        } else {
          msg.textContent = 'Please enter a valid email.';
          btn.disabled = false;
        }
      });
    }).catch(function () {
      msg.textContent = 'Could not send the link. Please try again.';
      btn.disabled = false;
    });
  }

  function render(el) {
    if (!el) return;
    el.textContent = MESSAGE + ' ';
    // A div, not a form: a page's own form may already enclose this spot.
    var box = document.createElement('span');
    box.className = 'voice-signin-box';
    var input = document.createElement('input');
    input.type = 'email';
    input.autocomplete = 'email';
    input.placeholder = 'Your email';
    input.setAttribute('aria-label', 'Your email');
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Email me a link';
    var msg = document.createElement('span');
    msg.className = 'voice-signin-msg';
    msg.setAttribute('role', 'status');
    btn.addEventListener('click', function (ev) {
      if (ev && ev.preventDefault) ev.preventDefault();
      send(input, btn, msg);
    });
    input.addEventListener('keydown', function (ev) {
      if (ev && ev.key === 'Enter') { ev.preventDefault(); send(input, btn, msg); }
    });
    box.appendChild(input);
    box.appendChild(btn);
    box.appendChild(msg);
    el.appendChild(box);
  }

  function handle(resp, el) {
    if (!resp || resp.status !== 401) return Promise.resolve(false);
    state.signedIn = false;
    render(el);
    return Promise.resolve(true);
  }

  window.VoiceSignIn = {
    MESSAGE: MESSAGE, check: check, render: render, handle: handle, headers: headers,
  };
})();
