const assert = require('assert');
const {renderHistoricalIntakes} = require('../static/js/portal-historical-intakes.js');

const empty = renderHistoricalIntakes({items: []});
assert.ok(empty.includes('Past Intake'));
assert.ok(empty.includes('No reviewed historical intake records'));

const rendered = renderHistoricalIntakes({items: [{
  id: 4, form_name: 'Practice Better Intake', form_date: '2025-01-02',
  source_label: 'Practice Better', fields: [{
    id: 'sleep', label: 'Sleep', value: 'Woke often', copyable: true,
    differs_from_current: true
  }]
}]});
assert.ok(rendered.includes('historical response and may no longer describe you'));
assert.ok(rendered.includes('Changed since this intake'));
assert.ok(rendered.includes('Use as a starting point'));
assert.ok(rendered.includes('data-snapshot-id="4"'));

const escaped = renderHistoricalIntakes({items: [{
  id: 5, form_name: '<img src=x onerror=alert(1)>', form_date: '', source_label: 'PB',
  fields: [{id: 'sleep', label: '<script>x</script>', value: '<b>bad</b>', copyable: false}]
}]});
assert.ok(!escaped.includes('<img src=x'));
assert.ok(!escaped.includes('<script>x</script>'));
assert.ok(!escaped.includes('<b>bad</b>'));
assert.ok(escaped.includes('&lt;b&gt;bad&lt;/b&gt;'));

console.log('ok - portal historical intakes tile');
