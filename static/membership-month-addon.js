/* The unticked "one month of membership at a price you choose" add-on.
   Glen, platform tab, 2026-11-26: "yes, unticked add-on at every checkout, store page kept".
   MonthAddon.mount(beforeEl) shows it when the server says the offer is on.
   MonthAddon.value() is the chosen price in cents, null when unticked, or throws a
   plain message when the box holds something the checkout would refuse. */
(function () {
  var offer = null, box = null, forced = false;
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    for (var k in (attrs || {})) e.setAttribute(k, attrs[k]);
    if (text) e.textContent = text;
    return e;
  }
  // opts.forced: the month's own page, where the month IS the purchase. No tickbox;
  // the price box shows at once and must be filled.
  function mount(beforeEl, base, opts) {
    if (!beforeEl || box) return;
    forced = !!(opts && opts.forced);
    fetch((base || '') + '/begin/membership-month-offer', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (o) {
        if (!o || !o.on || box) return;
        offer = o;
        box = el('div', { 'class': 'month-addon', id: 'month-addon' });
        var label = el('label', { 'class': 'month-addon-line' });
        var cb = el('input', { type: 'checkbox', id: 'month-addon-check' });
        label.appendChild(cb);
        label.appendChild(document.createTextNode(' ' + o.line));
        var row = el('div', forced ? { 'class': 'month-addon-price' }
                                   : { 'class': 'month-addon-price', hidden: '' });
        row.appendChild(document.createTextNode('$ '));
        var amt = el('input', { type: 'number', id: 'month-addon-dollars', min: String(o.min_cents / 100),
                                max: String(o.max_cents / 100), step: '1', inputmode: 'numeric' });
        row.appendChild(amt);
        var help = el('p', { 'class': 'month-addon-help' }, o.help);
        cb.addEventListener('change', function () {
          if (cb.checked) { row.removeAttribute('hidden'); amt.focus(); }
          else { row.setAttribute('hidden', ''); }
        });
        if (!forced) box.appendChild(label);
        box.appendChild(row); box.appendChild(help);
        beforeEl.parentNode.insertBefore(box, beforeEl);
      })
      .catch(function () { /* no add-on: the checkout works as before */ });
  }
  function value() {
    if (!box || !offer) return null;
    if (!forced && !document.getElementById('month-addon-check').checked) return null;
    var raw = (document.getElementById('month-addon-dollars').value || '').trim();
    var lo = offer.min_cents / 100, hi = offer.max_cents / 100;
    if (!/^\d+$/.test(raw) || +raw < lo || +raw > hi) {
      throw new Error('Please choose a whole-dollar price from $' + lo + ' to $' + hi +
                      ' for the month of membership' + (forced ? '.' : ', or untick it.'));
    }
    return (+raw) * 100;
  }
  window.MonthAddon = { mount: mount, value: value };
})();
