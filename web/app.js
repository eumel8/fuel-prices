const COLORS = { DE: '#f0a020', PL: '#4aa8d8', EU: '#7f8fa0' };
const OIL_COLOR = '#c060d0';

const state = { data: null, fuel: 'e5', net: false, days: 365, chart: null };

const $ = (sel) => document.querySelector(sel);

function fmtDate(iso) {
  return new Date(iso + 'T00:00:00Z').toLocaleDateString('de-DE', {
    day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC',
  });
}

function shortDate(iso) {
  return new Date(iso + 'T00:00:00Z').toLocaleDateString('de-DE', {
    day: '2-digit', month: '2-digit', timeZone: 'UTC',
  });
}

function seriesFor(fuel, net) {
  const basis = net ? 'excl_taxes' : 'incl_taxes';
  return (state.data.series || []).filter(
    (s) => s.fuel === fuel && s.basis === basis && ['DE', 'PL', 'EU'].includes(s.country),
  );
}

function dailyOf(s) {
  return s.find((x) => x.granularity === 'daily');
}

function renderCards() {
  const all = seriesFor(state.fuel, state.net);
  const box = $('#cards');
  const cards = [];

  for (const country of ['DE', 'PL', 'EU']) {
    const candidates = all.filter((s) => s.country === country);
    if (!candidates.length) continue;
    const primary = dailyOf(candidates) || candidates[0];
    const pts = primary.points;
    const last = pts[pts.length - 1];
    const prev = pts[pts.length - 2];
    const delta = prev ? last[1] - prev[1] : null;
    const gran = primary.granularity === 'daily' ? 'Tagesmittel' : 'Wochenmittel';
    cards.push(`
      <div class="card" style="border-left-color:${COLORS[country]}">
        <div class="k">${country === 'DE' ? 'Deutschland' : country === 'PL' ? 'Polen' : 'EU-Durchschnitt'} · ${gran}</div>
        <div class="v">${last[1].toFixed(3)} <small>EUR/l</small></div>
        <div class="m">${fmtDate(last[0])}${
          delta === null ? '' :
          ` · <span class="${delta > 0 ? 'up' : delta < 0 ? 'down' : ''}">${delta > 0 ? '+' : ''}${delta.toFixed(3)}</span>`
        }${primary.sample_size ? ` · n=${primary.sample_size}` : ''}</div>
      </div>`);
  }

  for (const s of state.data.oil || []) {
    const pts = s.points;
    const last = pts[pts.length - 1];
    const prev = pts[pts.length - 2];
    const delta = prev ? last[1] - prev[1] : null;
    cards.push(`
      <div class="card" style="border-left-color:${OIL_COLOR}">
        <div class="k">${s.label}</div>
        <div class="v">${last[1].toFixed(2)} <small>USD/Barrel</small></div>
        <div class="m">${fmtDate(last[0])}${
          delta === null ? '' :
          ` · <span class="${delta > 0 ? 'up' : delta < 0 ? 'down' : ''}">${delta > 0 ? '+' : ''}${delta.toFixed(2)}</span>`
        }</div>
      </div>`);
  }
  box.innerHTML = cards.join('');
}

function renderChart() {
  const sets = seriesFor(state.fuel, state.net);
  // Eine Serie je (Land, Aufloesung): Tagesdaten durchgezogen, Wochenmittel
  // gestrichelt. Sonst wuerde jede Wochenserie doppelt gezeichnet.
  const seen = new Set();
  const daily = [];
  const weekly = [];
  for (const s of sets) {
    const key = `${s.country}:${s.granularity}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const isDaily = s.granularity === 'daily';
    (isDaily ? daily : weekly).push({
      label: s.label,
      data: s.points.map(([d, v]) => ({ x: d, y: v })),
      borderColor: COLORS[s.country],
      backgroundColor: COLORS[s.country],
      borderWidth: isDaily ? 1.8 : 1.4,
      borderDash: isDaily ? [] : [5, 4],
      pointRadius: 0,
      tension: 0.15,
      spanGaps: true,
    });
  }

  const datasets = [
    ...daily,
    ...weekly,
    ...(state.data.oil || []).map((s) => ({
      label: s.label,
      data: s.points.map(([d, v]) => ({ x: d, y: v })),
      yAxisID: 'y2',
      borderColor: OIL_COLOR,
      backgroundColor: OIL_COLOR,
      borderWidth: 1.2,
      borderDash: [2, 3],
      pointRadius: 0,
      tension: 0.2,
      spanGaps: true,
    })),
  ];

  const config = {
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          type: 'time',
          time: { unit: 'month', tooltipFormat: 'dd.MM.yyyy' },
          ticks: {
            color: '#93a4b5',
            maxRotation: 0,
            autoSkipPadding: 16,
            callback: function (value) {
              return shortDate(new Date(value).toISOString().slice(0, 10));
            },
          },
          grid: { color: '#27384a' },
        },
        y: {
          position: 'left',
          title: { display: true, text: 'EUR / Liter', color: '#93a4b5' },
          ticks: { color: '#93a4b5', callback: (v) => v.toFixed(2) },
          grid: { color: '#27384a' },
        },
        y2: {
          position: 'right',
          title: { display: true, text: 'USD / Barrel', color: OIL_COLOR },
          ticks: { color: OIL_COLOR, callback: (v) => v.toFixed(0) },
          grid: { drawOnChartArea: false },
        },
      },
      plugins: {
        legend: { labels: { color: '#e8eef5', boxWidth: 18, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(3)} ${
              ctx.dataset.yAxisID === 'y2' ? 'USD/Barrel' : 'EUR/l'}`,
          },
        },
      },
    },
  };

  if (state.chart) state.chart.destroy();
  state.chart = new Chart($('#chart'), { type: 'line', ...config });
}

function renderMeta() {
  $('#sources').innerHTML = (state.data.sources || [])
    .map((s) => `<li><strong>${s.label}</strong> <code>${s.id}</code></li>`).join('');
  $('#notes').innerHTML = (state.data.notes || []).map((n) => `<li>${n}</li>`).join('');
  const w = state.data.window || {};
  $('#status').textContent = `Fenster ${fmtDate(w.from)} – ${fmtDate(w.to)} · Daten erzeugt ${state.data.generated_at?.slice(0, 16).replace('T', ' ') ?? '?'} UTC`;
}

function hasNet() {
  return (state.data.series || []).some((s) => s.basis === 'excl_taxes' && s.country !== 'DE');
}

async function load() {
  const params = new URLSearchParams();
  if (state.days > 0) {
    const to = new Date();
    const from = new Date(to.getTime() - state.days * 86400000);
    params.set('from', from.toISOString().slice(0, 10));
    params.set('to', to.toISOString().slice(0, 10));
  }
  $('#status').className = 'status';
  $('#status').textContent = 'lade Daten…';
  try {
    const res = await fetch(`/api/series?${params}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
    $('#net').disabled = !hasNet();
    renderCards();
    renderChart();
    renderMeta();
  } catch (err) {
    $('#status').className = 'status err';
    $('#status').textContent = `Fehler: ${err.message}`;
  }
}

$('#range').addEventListener('change', (e) => { state.days = Number(e.target.value); load(); });
$('#fuel').addEventListener('change', (e) => { state.fuel = e.target.value; state.net = false; $('#net').checked = false; load(); });
$('#net').addEventListener('change', (e) => { state.net = e.target.checked; load(); });

load();