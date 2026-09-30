// Dependency-free full dashboard script smoke test. No browser or network access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const saved = JSON.parse(fs.readFileSync(path.join(root, 'output/state.json'), 'utf8'));
const latest = JSON.parse(fs.readFileSync(path.join(root, 'output/issued/latest.json'), 'utf8'));
const clone = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(tag = 'div', id = '') {
    this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.style = {};
    this.hidden = false; this.textContent = ''; this._value = ''; this._html = '';
    this.listeners = {}; this.attributes = {};
    const classes = new Set();
    this.classList = {toggle: (name, active) => active ? classes.add(name) : classes.delete(name), contains: name => classes.has(name)};
  }
  get value() { return this._value; }
  set value(value) { this._value = String(value); }
  get innerHTML() { return this._html; }
  set innerHTML(value) { this._html = String(value); this.children = []; }
  append(child) { child.parentElement = this; this.children.push(child); if (this.tagName === 'SELECT' && this.children.length === 1) this.value = child.value; }
  replaceChildren() { this.children = []; this._html = ''; if (this.tagName === 'SELECT') this.value = ''; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  fire(type) { for (const callback of this.listeners[type] || []) callback({preventDefault() {}}); }
  querySelector(tag) { return this.children.find(child => child.tagName === tag.toUpperCase()) || null; }
  getBoundingClientRect() { return {top: this.id === 'overview' ? 0 : 1000}; }
  scrollIntoView() {}
}

async function dashboard(state, issued) {
  const elements = new Map();
  for (const match of html.matchAll(/<(\w+)\b[^>]*\bid="([^"]+)"/g)) elements.set(match[2], new Element(match[1], match[2]));
  const get = id => { assert(elements.has(id), `Unknown dashboard element ${id}`); return elements.get(id); };
  get('warning').append(new Element('strong')); get('warning').append(new Element('span'));
  get('probFill').parentElement = new Element();
  for (const source of ['all', 'IFS', 'GFS', 'AIFS']) { const option = new Element('option'); option.value = source; get('outage').append(option); }
  for (const value of ['point', 'probability']) { const option = new Element('option'); option.value = value; get('weightMode').append(option); }
  const links = ['overview', 'contributions', 'verification', 'provenance'].map(id => { const link = new Element('a'); link.hash = '#' + id; return link; });
  const location = {hash: ''};
  const context = vm.createContext({console, Intl, Date, Number, String, Math, Object, Set, Map,
    document: {getElementById: get, createElement: tag => new Element(tag),
      querySelectorAll: selector => { assert.equal(selector, 'aside nav a'); return links; },
      querySelector: selector => get(selector.slice(1)), documentElement: {scrollHeight: 10000}},
    window: {innerHeight: 900, scrollY: 0, addEventListener() {}}, location,
    history: {pushState: (_, __, hash) => { location.hash = hash; }},
    matchMedia: () => ({matches: true}), requestAnimationFrame: callback => callback(),
    fetch: async url => { assert(['/api/state', '/api/issued'].includes(url)); return {ok: true, json: async () => url === '/api/state' ? state : issued}; }
  });
  vm.runInContext(script, context, {filename: 'web/index.html'});
  await new Promise(resolve => setImmediate(resolve));
  const error = get('warning').querySelector('strong').textContent;
  if (state.data_kind !== 'synthetic_demo') assert.notEqual(error, 'Data unavailable', get('warning').querySelector('span').textContent);
  return {get, context, links, state};
}

function content(element) { return [element.textContent, element.innerHTML, ...element.children.map(content)].join(' '); }
function change(app, id, value) { app.get(id).value = value; app.get(id).fire('change'); }

(async () => {
  const app = await dashboard(clone(saved), clone(latest));
  assert.equal(app.get('variable').value, 'rain', 'Rainfall must open first');
  assert.match(app.get('detailTime').textContent, /IST/);
  assert.equal(app.get('issuedRows').children.length, latest.records.length);
  assert.match(app.get('statConfidenceSub').textContent, /not a weather probability/);
  assert.match(app.get('statConfidence').textContent, /\/ 100/);
  assert.match(app.get('statMaeSub').textContent, /Average absolute difference/);
  assert.match(app.get('selectionSummary').textContent, /selected on development data/);
  assert.match(content(app.get('weightRows')), /source-value/);
  const initialForecast = app.get('statForecast').textContent;
  app.get('presentation').fire('click');
  assert(app.get('appShell').classList.contains('presentation'));
  assert.equal(app.get('presentation').getAttribute('aria-pressed'), 'true');
  assert.equal(app.get('statForecast').textContent, initialForecast);
  change(app, 'weightMode', 'probability');
  assert(app.get('appShell').classList.contains('presentation'), 'Contribution changes preserve focus view');
  app.get('presentation').fire('click');
  assert(!app.get('appShell').classList.contains('presentation'));
  assert.equal(app.get('presentation').getAttribute('aria-pressed'), 'false');
  change(app, 'weightMode', 'point');
  const cityNames = new Set(saved.coverage.regions);
  const cityLabels = [...app.get('map').innerHTML.matchAll(/<text x="([\d.]+)" y="([\d.]+)" text-anchor="(start|end)"[^>]*>([^<]+)<\/text>/g)]
    .filter(match => cityNames.has(match[4])).map(([, x, y, anchor, name]) => {
      const width = name.length * 7.2;
      return {name, left: Number(x) - (anchor === 'end' ? width : 0), right: Number(x) + (anchor === 'start' ? width : 0), top: Number(y) - 11, bottom: Number(y) + 3};
    });
  assert.equal(cityLabels.length, cityNames.size);
  for (const [i, label] of cityLabels.entries()) {
    assert(label.left >= 0 && label.right <= 380, `${label.name} label must stay inside the map`);
    for (const other of cityLabels.slice(i + 1)) {
      assert(label.right <= other.left || other.right <= label.left || label.bottom <= other.top || other.bottom <= label.top,
        `${label.name} and ${other.name} map labels overlap`);
    }
  }
  for (const link of app.links) { link.fire('click'); assert(link.classList.contains('active')); assert.equal(app.links.filter(x => x.classList.contains('active')).length, 1); }
  for (const variable of [...new Set(saved.cases.map(c => c.variable))]) {
    change(app, 'variable', variable);
    for (const lead of [...app.get('lead').children.map(x => x.value)]) {
      change(app, 'lead', lead);
    for (const region of [...app.get('region').children.map(x => x.value)]) {
      change(app, 'region', region);
      for (const outage of ['all', 'IFS', 'GFS', 'AIFS']) {
        change(app, 'outage', outage);
        for (const mode of ['point', 'probability']) change(app, 'weightMode', mode);
        app.get('reveal').fire('click'); assert.equal(app.get('reveal').attributes['aria-pressed'], 'true');
        assert.notEqual(app.get('detailObserved').textContent, 'Hidden for replay');
        app.get('reveal').fire('click'); assert.equal(app.get('reveal').attributes['aria-pressed'], 'false');
      }
      app.get('heavyCase').fire('click'); app.get('ordinaryCase').fire('click');
    }
    }
  }

  // Explicit new-schema branches supplement the saved snapshot, which may be old.
  const enriched = clone(saved), issued = clone(latest);
  for (const product of enriched.results) {
    product.selection_basis = 'development only'; product.fresh_test_calendar = false;
    product.event_approval = Object.fromEntries(Object.keys(product.event_scores.validation).map(name => [name, {approved: false, reasons: ['Quality-controlled gauge evidence is required.']}]));
    const bins = [{min_score: 0, max_score_exclusive: 40, n: 1, mae: 4}, {min_score: 40, max_score_exclusive: 70, n: 2, mae: 2}, {min_score: 70, max_score_exclusive: 101, n: 1, mae: 1}];
    product.confidence_evaluation = {validation: {bins, score_error_rank_association: -.2}, test: {bins, score_error_rank_association: -.3}};
    product.ablation = [{method: 'Forecast router', point: product.test.point.equal, probability: product.test.probability.raw, events: product.event_scores.test}];
    for (const source of ['IFS', 'GFS', 'AIFS']) {
      product.outages[source].event_approval = clone(product.event_approval);
      product.outages[source].event_scores = product.event_scores;
      product.outages[source].probability_test = product.test.probability.raw;
      product.outages[source].probability_validation = product.validation.probability.raw;
    }
  }
  for (const row of enriched.cases) {
    delete row.outage_confidence; // Older reports must not reuse full-source confidence during outages.
    row.confidence = {score: 55, label: 'Moderate evidence', factors: {agreement: {value: .5, weight: .2, status: 'available', detail: 'Measured source spread'}}, drivers: ['Relevant evidence is limited.']};
    row.disagreement = {std: 2, range: 5}; row.router_weights = {IFS: .4, GFS: .2, AIFS: .4};
    row.router_explanation = {reason: 'Released-label ridge candidate', predicted_errors: {IFS: 2}, drivers: {IFS: [{feature: 'rolling_mae_IFS', error_contribution: -.5}]}, shrinkage: .4, evidence_dates: 25};
    row.outage_probabilities = {IFS: .3, GFS: .3, AIFS: .3}; row.outage_weights = {IFS: {GFS: .5, AIFS: .5}, GFS: {IFS: .5, AIFS: .5}, AIFS: {IFS: .5, GFS: .5}};
  }
  for (const record of issued.records) for (const info of Object.values(record.source_status || {})) info.freshness = {status: 'too stale', age_hours: 48, reason: 'Beyond configured run-age limit'};
  const fresh = await dashboard(enriched, issued);
  assert.match(fresh.get('confidenceScore').textContent, /55.0 \/ 100/);
  assert(fresh.get('confidenceFactors').children.length);
  assert(fresh.get('routerRows').children.length);
  assert(fresh.get('ablationRows').children.length);
  assert(fresh.get('eventRows').children.length);
  assert.match(content(fresh.get('issuedRows')), /too stale/);
  change(fresh, 'outage', 'IFS');
  assert.equal(fresh.get('statHazard').textContent, 'Withheld');
  assert.match(fresh.get('probReason').textContent, /gauge evidence/);
  assert.equal(fresh.get('confidenceScore').textContent, 'Not assessed for this scenario');
  assert.equal(fresh.get('statConfidence').textContent, 'Not assessed');
  assert.equal(fresh.get('publicationDetails').hidden, false);
  const selected = vm.runInContext('selected', fresh.context);
  const product = enriched.results.find(r => r.variable === selected.variable && r.lead_hours === selected.lead_hours);
  product.outages.IFS.event_approval.heavy = {approved: true, reasons: []};
  product.outages.IFS.approved = false;
  fresh.get('outage').fire('change');
  assert.equal(fresh.get('statForecast').textContent, 'Withheld', 'Point and event approval must be separate');
  assert.equal(fresh.get('statHazard').textContent, '30%');
  const rejected = await dashboard({...clone(saved), data_kind: 'synthetic_demo'}, clone(latest));
  assert.match(rejected.get('warning').querySelector('span').textContent, /generated sample data/);
  console.log('Dashboard script checks passed: saved data, filters, outages, replay, IST, navigation, new evidence panels, freshness and synthetic-data rejection.');
})().catch(error => { console.error(error); process.exitCode = 1; });
