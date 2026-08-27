// Past Intake inside My Clinical Record. Historical snapshots are immutable;
// the only client write is an explicit copy of selected editable fields into
// the current profile through the token-scoped server route.
function escapeHistorical(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

function historicalValue(value) {
  if (Array.isArray(value)) return value.map(function (row) {
    return Object.keys(row || {}).map(function (key) {
      return escapeHistorical(key) + ': ' + escapeHistorical(row[key]);
    }).join(' · ');
  }).join('<br>');
  return escapeHistorical(value);
}

function renderHistoricalIntakes(payload) {
  var items = (payload && payload.items) || [];
  if (!items.length) {
    return '<div class="card quiet"><h2>Past Intake</h2>' +
      '<p class="muted">No reviewed historical intake records are available yet.</p></div>';
  }
  var cards = items.map(function (item) {
    var fields = (item.fields || []).map(function (field) {
      var changed = field.differs_from_current ?
        '<span class="pill">Changed since this intake</span>' : '';
      var copy = field.copyable ?
        '<label class="historical-copy"><input type="checkbox" data-historical-field="' +
        escapeHistorical(field.id) + '"> Use as a starting point</label>' : '';
      return '<div class="health-field"><div class="health-field-head"><span class="health-label">' +
        escapeHistorical(field.label) + '</span>' + changed + '</div><p class="health-value">' +
        historicalValue(field.value) + '</p>' + copy + '</div>';
    }).join('');
    return '<article class="card historical-intake" data-snapshot-id="' + escapeHistorical(item.id) + '">' +
      '<p class="eyebrow">Imported historical record</p><h2>' + escapeHistorical(item.form_name) + '</h2>' +
      '<p class="muted small">Completed ' + escapeHistorical(item.form_date || 'date unavailable') +
      ' · Source: ' + escapeHistorical(item.source_label) + '</p>' +
      '<div class="historical-warning">This is a historical response and may no longer describe you.</div>' +
      fields + '<button type="button" class="btn historical-copy-btn">Copy selected to my current profile</button>' +
      '<p class="historical-copy-status muted small" role="status"></p></article>';
  }).join('');
  return '<div class="historical-intakes"><h2 class="clinical-record-section-label">Past Intake</h2>' + cards + '</div>';
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {renderHistoricalIntakes: renderHistoricalIntakes};
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  window.loadHistoricalIntakes = function loadHistoricalIntakes() {
    var mount = document.getElementById('portal-historical-intakes-mount');
    var match = location.pathname.match(/\/portal\/([^\/]+)/);
    if (!mount || !match) return Promise.resolve();
    var token = match[1];
    return fetch('/api/portal/' + token + '/clinical-record/intakes')
      .then(function (response) { return response.ok ? response.json() : {items: []}; })
      .then(function (payload) { mount.innerHTML = renderHistoricalIntakes(payload); })
      .catch(function () { mount.innerHTML = renderHistoricalIntakes({items: []}); });
  };
  document.addEventListener('click', function (event) {
    var button = event.target.closest && event.target.closest('.historical-copy-btn');
    if (!button) return;
    var card = button.closest('.historical-intake');
    var status = card.querySelector('.historical-copy-status');
    var fields = Array.from(card.querySelectorAll('[data-historical-field]:checked'))
      .map(function (box) { return box.getAttribute('data-historical-field'); });
    if (!fields.length) { status.textContent = 'Select at least one field.'; return; }
    var match = location.pathname.match(/\/portal\/([^\/]+)/);
    if (!match) return;
    button.disabled = true; status.textContent = 'Updating your current profile…';
    fetch('/api/portal/' + match[1] + '/clinical-record/intakes/' +
          card.getAttribute('data-snapshot-id') + '/copy-to-current', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({fields: fields})
    }).then(function (response) { return response.json().then(function (body) {
      return {ok: response.ok, body: body};
    }); }).then(function (result) {
      if (!result.ok) throw new Error(result.body.error || 'Update failed');
      status.textContent = 'Copied to your current health profile.';
      window.setTimeout(function () { location.hash = 'health'; }, 300);
    }).catch(function (error) {
      status.textContent = error.message || 'Update failed. Please try again.';
      button.disabled = false;
    });
  });
  document.addEventListener('DOMContentLoaded', window.loadHistoricalIntakes);
}
