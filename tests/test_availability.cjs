// Run with: node tests/test_availability.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const state = JSON.parse(fs.readFileSync(path.join(root, 'output/state.json'), 'utf8'));
const issued = JSON.parse(fs.readFileSync(path.join(root, 'output/issued/latest.json'), 'utf8'));
let scenario = 'all';
const {view, issuedReason} = vm.runInNewContext(
  html.slice(html.indexOf('function outageReason('), html.indexOf('function mapHtml(')) + '\n({view, issuedReason});',
  {state, $: () => ({value: scenario}), fmt: (v,d) => Number.isFinite(v)?v.toFixed(d):'n/a', methodName: s => s, probabilityName: s => s});
for (const product of state.results) {
  const c = state.cases.find(c => c.variable === product.variable && c.lead_hours === product.lead_hours);
  scenario = 'all';
  assert.equal(view(c).point, c.point);
  for (const source of ['IFS', 'GFS', 'AIFS']) {
    scenario = source;
    const v = view(c);
    const event = product.variable === 'rain' ? 'heavy' : 'defined_event';
    const approved = product.outages[source].event_approval?.[event]?.approved;
    if(approved && Number.isFinite(c.outage_probabilities?.[source]))assert.equal(v.probability,c.outage_probabilities[source]);
    else {
      assert.equal(v.probability, null);
      assert.ok(v.probabilityReason.includes(source));
      assert.ok(v.probabilityReason.includes('checks') || product.outages[source].event_approval?.[event]?.reasons?.some(reason=>v.probabilityReason.includes(reason)));
    }
    if (product.outages[source].approved) {
      assert.ok(Number.isFinite(v.point));
    } else {
      assert.equal(v.point, null);
      assert.ok(v.pointReason.includes(source) && v.pointReason.includes('All sources'));
      assert.ok(v.pointReason.includes('above the allowed') || v.pointReason.includes('at least 15') || v.pointReason.includes('approval'));
    }
  }
}
for (const item of issued.records) {
  const product = state.results.find(p => p.variable === item.variable && p.lead_hours === item.lead_hours);
  if(item.reason?.includes('fewer than 10'))assert.ok(issuedReason(item, product).includes('previous evidence policy'));
  const event = item.variable === 'rain' ? 'heavy' : 'defined_event';
  if(item.point!=null&&item.hazard_probability==null&&item.event_reasons?.[event]?.length)assert.ok(item.event_reasons[event].every(reason=>issuedReason(item,product).includes(reason)));
}
assert.ok(issuedReason({point:null, missing_sources:['IFS','GFS','AIFS']}).includes('No forecast values received'));
assert.ok(issuedReason({point:null,reason:'insufficient released historical cases'}).includes('30'));
assert.ok(issuedReason({point:1,reason:'probability fallback unvalidated'}).includes('remaining sources'));
new Function(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
const warning = {strong:{}, span:{}};
vm.runInNewContext('let state;\n'+html.slice(html.indexOf('async function init()'),html.indexOf('</script>')), {
  fetch: async () => ({ok:true, json:async () => ({data_kind:'synthetic_demo'})}),
  $: () => ({querySelector: selector => warning[selector]})
}).then(() => {
  assert.ok(warning.span.textContent.includes('generated sample data'), 'Synthetic data is rejected before rendering forecasts');
  console.log('Availability explanations, synthetic-data guard and dashboard syntax passed');
}).catch(error => {console.error(error); process.exitCode=1;});
