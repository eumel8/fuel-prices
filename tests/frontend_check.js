// Prueft die Frontend-Logik ohne Browser: laedt app.js in jsdom, mockt Chart
// und fetch, und wertet aus, welche Datasets Chart.js bekommen wuerde.
//
// Voraussetzungen: node, npm install jsdom, laufender Server auf PORT.
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PORT = process.env.PORT || '8099';
const appjs = fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8');
const html = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8')
  .replace(/<script src="https:\/\/[^"]+"><\/script>/g, '')
  .replace('<script src="/static/app.js"></script>', '');

let captured = null;
const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost/' });

// Chart-Stub: merkt sich Datasets und Optionen.
class ChartStub {
  constructor(el, config) { this.el = el; this.config = config; captured = config; }
  destroy() {}
}
dom.window.Chart = ChartStub;

// fetch auf den laufenden Server umleiten.
dom.window.fetch = (url, opts) =>
  fetch(`http://127.0.0.1:${PORT}${url}`, opts);

const failures = [];
function check(name, cond, detail) {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? ' :: ' + detail : ''}`);
  if (!cond) failures.push(name);
}

(async () => {
  const { window } = dom;
  window.eval(appjs);
  // load() ist am Skriptende asynchron gestartet; aufzeichnen abwarten.
  await new Promise((r) => setTimeout(r, 2500));

  check('Chart wurde erzeugt', !!captured);
  if (!captured) { process.exit(1); }

  const ds = captured.data.datasets;
  const labels = ds.map((d) => d.label);
  console.log('\nDatasets:');
  for (const d of ds) {
    console.log(`  ${d.label.padEnd(46)} n=${String(d.data.length).padStart(4)} achse=${d.yAxisID || 'y'} dash=${JSON.stringify(d.borderDash)}`);
  }

  // Bug 1: keine doppelten Labels mehr.
  const dupes = labels.filter((l, i) => labels.indexOf(l) !== i);
  check('keine doppelten Datasets', dupes.length === 0, dupes.join(', ') || 'alle Labels eindeutig');

  // DE-Tagesdaten muessen durchgezogen sein, falls welche vorliegen.
  // Ohne eigenen Tankerkönig-Key liefert die API nur den Demo-Key; die
  // Pruefung darf daran nicht scheitern.
  const deDaily = ds.filter((d) => d.label.includes('Deutschland') && d.label.includes('Tagesmittel'));
  if (deDaily.length > 0) {
    check('DE-Tagesserie durchgezogen', deDaily[0].borderDash.length === 0);
  } else {
    console.log('SKIP  DE-Tagesserie durchgezogen :: keine Tagesdaten (Demo-Key)');
  }

  // Wochenserie gestrichelt.
  const weeklyDs = ds.filter((d) => d.label.includes('Wochenmittel'));
  check('Wochenmittel gestrichelt', weeklyDs.length > 0 && weeklyDs.every((d) => d.borderDash.length === 2),
    `${weeklyDs.length} Wochenserien`);

  // Oel auf zweiter Achse.
  const oil = ds.filter((d) => d.yAxisID === 'y2');
  check('Oel auf y2-Achse', oil.length >= 1, `${oil.length} Rohoelserien`);

  check('x-Achse ist time', captured.options.scales.x.type === 'time');
  check('y-Achse EUR/l', captured.options.scales.y.title.text === 'EUR / Liter');

  // Datumspunkte muessen ISO-Strings sein, sonst findet date-fns nichts.
  const allX = ds.flatMap((d) => d.data.map((p) => p.x));
  check('alle x-Werte sind ISO-Daten', allX.every((x) => typeof x === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(x)),
    `${allX.length} Punkte, Beispiel ${allX[0]}`);

  // Karten und Meta gefuellt?
  check('Karten gerendert', window.document.querySelectorAll('#cards .card').length > 0,
    `${window.document.querySelectorAll('#cards .card').length} Karten`);
  check('Quellenliste gefuellt', window.document.querySelectorAll('#sources li').length > 0);
  const status = window.document.querySelector('#status').textContent;
  check('Status ohne Fehler', !status.startsWith('Fehler'), status);
  check('net-Checkbox aktiviert', !window.document.querySelector('#net').disabled);

  // Auf Netto umschalten: EU und PL muessen dann Netto-Serien zeigen.
  window.document.querySelector('#net').checked = true;
  window.document.querySelector('#net').dispatchEvent(new window.Event('change'));
  await new Promise((r) => setTimeout(r, 2500));
  const netDs = (captured.data.datasets || []).map((d) => d.label);
  console.log('\nNetto-Modus Datasets:');
  for (const l of netDs) console.log(`  ${l}`);
  check('Netto: Polen dabei', netDs.some((l) => l.includes('Polen') && l.includes('netto')));
  check('Netto: EU dabei', netDs.some((l) => l.includes('EU-Durchschnitt') && l.includes('netto')));

  console.log(`\n${failures.length === 0 ? 'ALLE CHECKS BESTANDEN' : failures.length + ' FEHLGESCHLAGEN: ' + failures.join(', ')}`);
  process.exit(failures.length === 0 ? 0 : 1);
})();