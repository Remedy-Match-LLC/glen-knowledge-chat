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

  // A card payment leaves for Stripe, so the page keeps the notes for its return. They
  // are tied to the order: the return names its order, and only a match is shown, so
  // an earlier checkout's notes never appear on a later order's return (review).
  var KEY = 'order_notes_pending';
  function keepOrderNotes(notes, orderRef) {
    try {
      if (Array.isArray(notes) && notes.length && orderRef)
        sessionStorage.setItem(KEY, JSON.stringify({ref: String(orderRef), notes: notes}));
      else sessionStorage.removeItem(KEY);
    } catch (e) { /* storage blocked: the page still shows what it was sent */ }
  }
  function takeOrderNotes(orderRef) {
    try {
      var raw = sessionStorage.getItem(KEY);
      var v = raw ? JSON.parse(raw) : null;
      if (!v || !orderRef || String(v.ref) !== String(orderRef)) return [];
      sessionStorage.removeItem(KEY);
      return Array.isArray(v.notes) ? v.notes : [];
    } catch (e) { return []; }
  }

  root.renderOrderNotes = renderOrderNotes;
  root.keepOrderNotes = keepOrderNotes;
  root.takeOrderNotes = takeOrderNotes;
})(typeof window !== 'undefined' ? window : globalThis);
