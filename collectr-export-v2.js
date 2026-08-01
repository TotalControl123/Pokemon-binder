/*
 * CollectR showcase -> CSV  (v2)
 *
 * Paste into the DevTools Console on your showcase page.
 * It scrolls to the bottom, harvesting rows as it goes (so it survives
 * virtualised lists), then downloads a CSV.
 *
 * Run with:   await collectrExport()
 *
 * Values export in whatever currency the page is displaying. Switch the
 * toggle in the page header before running if you want a different one.
 */

// Matches "£314.46", "-$4.35", "+€3.05", "1,234.00" etc.
const MONEY = /^[-+]?\s*[£$€¥₹]?\s*[\d,]+\.\d{2}$/;
const PCT = /^\([-+]?\d+(\.\d+)?%\)$/;
const QTY = /^Qty:\s*(\d+)$/;
const BULLET = '\u2022';

// Rows harvested so far, keyed by raw line content to avoid duplicates.
// A null value marks a record we saw but could not parse.
const seen = new Map();
const unparsed = [];

function harvest() {
  const lines = document.body.innerText
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);

  // Skip the page header; the item list begins after the Filters control.
  let start = lines.lastIndexOf('Filters');
  start = start === -1 ? 0 : start + 1;

  let buf = [];
  for (let i = start; i < lines.length; i++) {
    const line = lines[i];
    const q = line.match(QTY);
    if (!q) {
      buf.push(line);
      continue;
    }

    const rec = buf;
    buf = [];
    const key = rec.join('|') + '|' + q[1];
    if (seen.has(key)) continue;

    const n = rec.length;
    const priceOk =
      n >= 4 &&
      MONEY.test(rec[n - 3]) &&
      MONEY.test(rec[n - 2]) &&
      PCT.test(rec[n - 1]);

    if (!priceOk) {
      unparsed.push(rec);
      seen.set(key, null);
      continue;
    }

    const fields = rec.slice(0, n - 3).filter((x) => x !== BULLET);
    seen.set(key, {
      name: fields[0] || '',
      set: fields[1] || '',
      rarity: fields[2] || '',
      number: fields[3] || '',
      condition: fields[4] || '',
      finish: fields[5] || '',
      qty: q[1],
      value: rec[n - 3],
      change: rec[n - 2],
      changePct: rec[n - 1].replace(/[()]/g, ''),
    });
  }
}

// Find whatever element actually scrolls - it is not always the window.
function scroller() {
  const els = [document.scrollingElement, ...document.querySelectorAll('*')];
  for (const el of els) {
    if (el && el.scrollHeight > el.clientHeight + 200) return el;
  }
  return document.scrollingElement;
}

async function collectrExport({ pause = 1200, patience = 4 } = {}) {
  const el = scroller();
  let stable = 0;
  let last = -1;

  harvest();
  while (stable < patience) {
    el.scrollTop = el.scrollHeight;
    await new Promise((r) => setTimeout(r, pause));
    harvest();
    console.log('rows so far:', seen.size);
    if (seen.size === last) stable++;
    else {
      stable = 0;
      last = seen.size;
    }
  }

  const rows = [...seen.values()].filter(Boolean);
  const cols = [
    'name',
    'set',
    'rarity',
    'number',
    'condition',
    'finish',
    'qty',
    'value',
    'change',
    'changePct',
  ];
  const esc = (v) => '"' + String(v).replace(/"/g, '""') + '"';
  const csv = [
    cols.join(','),
    ...rows.map((r) => cols.map((c) => esc(r[c])).join(',')),
  ].join('\n');

  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = 'collectr-export.csv';
  a.click();

  const totalQty = rows.reduce((t, r) => t + Number(r.qty), 0);
  console.log('exported rows:', rows.length, '| total qty:', totalQty);
  if (unparsed.length) {
    console.warn('records that did not parse:', unparsed.length);
    console.log(unparsed.slice(0, 10));
  }
  return { rows, unparsed };
}
