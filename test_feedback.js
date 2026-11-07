// Run with node test_feedback.js; no browser or dependencies needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(`${__dirname}/index.html`, 'utf8').split('function feedbackURL() {')[1].split('\nfunction showQR()')[0];
const context = {
  navigator: {userAgent: 'Mozilla/5.0 (iPhone) AppleWebKit Safari'}, URLSearchParams,
  state: {version: '2026-11-07', phone: 'http://private-host.local:8768', lights: {
    'private-device-id': {sku: 'H805D', name: 'Private porch', ip: '10.0.0.2'},
    other: {sku: 'Private porch 10.0.0.2'}
  }, found: [{id: 'another-private-id', sku: 'H619A'}, {sku: 'H805D'}]}
};
const url = new URL(vm.runInNewContext('function feedbackURL() {' + source + '\nfeedbackURL()', context));
assert.equal(url.origin + url.pathname, 'https://github.com/SaarthurR/govee-lights-app/issues/new');
assert.equal(url.searchParams.get('labels'), 'feedback');
assert.equal(url.searchParams.get('title'), 'Lights feedback');
assert.equal(url.searchParams.get('body'), 'App: 2026-11-07\nOS: iOS\nBrowser: Safari\nModels: H619A, H805D\n\nWhat happened:\n\nWhat you expected:\n');
for (const secret of ['private-device-id', 'another-private-id', 'Private porch', '10.0.0.2', 'private-host', 'api-key']) {
  assert(!decodeURIComponent(url.href).includes(secret));
}
console.log('Feedback URL: allowed fields only; no device IDs, IPs or names.');
