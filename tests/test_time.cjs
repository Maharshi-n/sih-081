// Run with: node tests/test_time.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const start = html.indexOf('const istFormatter=');
assert.ok(start >= 0, 'Dashboard has an IST formatter');
const format = vm.runInNewContext(html.slice(start, html.indexOf('const leadLabel=', start)) + '\nistTime;');
for (const timezone of ['UTC', 'America/Los_Angeles', 'Asia/Kolkata']) {
  process.env.TZ = timezone;
  assert.equal(format('2026-08-17T00:00:00+00:00'), '2026-08-17 05:30 IST');
  assert.equal(format('2026-08-18T03:00:00Z'), '2026-08-18 08:30 IST');
  assert.equal(format('2026-12-31T20:00:00Z'), '2027-01-01 01:30 IST');
  assert.equal(format('2024-02-29T20:00:00Z'), '2024-03-01 01:30 IST');
  assert.equal(format('2026-08-17T05:30:00+05:30'), '2026-08-17 05:30 IST');
  assert.equal(format('2026-07-03'), '2026-07-03 05:30 IST');
}
assert.equal(format(null), 'Time not recorded');
assert.equal(format('invalid'), 'Time not recorded');
console.log('IST display checks passed');
