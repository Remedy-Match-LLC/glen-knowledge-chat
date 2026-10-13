// A product's order note, shown after checkout in its own card (Glen, 2026-10-13).
// The server sends order_notes as [{name, paras}]; today only Microbiome carries one.
// Built with textContent, so nothing in a name or paragraph is read as HTML.
// Used by the buy page, the practitioner pages, the client checkout and the invoice.
(function (root) {
  function renderOrderNotes(host, notes) {
    if (!host) return 0;
    while (host.firstChild) host.removeChild(host.firstChild);
    var n = 0;
    (Array.isArray(notes) ? notes : []).forEach(function (note) {
      var paras = (note && Array.isArray(note.paras)) ? note.paras : [];
      if (!note || !note.name || !paras.length) return;
      var head = host.ownerDocument.createElement('p');
      var b = host.ownerDocument.createElement('strong');
      b.textContent = String(note.name);
      head.appendChild(b);
      host.appendChild(head);
      paras.forEach(function (t) {
        var p = host.ownerDocument.createElement('p');
        p.textContent = String(t);
        host.appendChild(p);
      });
      n += 1;
    });
    host.hidden = n === 0;
    if (host.classList) host.classList.toggle('hidden', n === 0);
    return n;
  }

  // A card payment leaves for Stripe, so the page keeps the notes for its return.
  var KEY = 'order_notes_pending';
  function keepOrderNotes(notes) {
    try {
      if (Array.isArray(notes) && notes.length) sessionStorage.setItem(KEY, JSON.stringify(notes));
      else sessionStorage.removeItem(KEY);
    } catch (e) { /* storage blocked: the receipt still carries the note */ }
  }
  function takeOrderNotes() {
    try {
      var raw = sessionStorage.getItem(KEY);
      sessionStorage.removeItem(KEY);
      var v = raw ? JSON.parse(raw) : [];
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }

  root.renderOrderNotes = renderOrderNotes;
  root.keepOrderNotes = keepOrderNotes;
  root.takeOrderNotes = takeOrderNotes;
})(typeof window !== 'undefined' ? window : globalThis);
