// Which modules are allowed to stay open, and whether the list is real.
//
//     node frontend/tools/tabs_test.mjs
//
// A kept tab is a MOUNTED component: that is the only way React keeps state,
// and it is also why this is an allow-list rather than "all of them". Two things
// about that list can be silently wrong, and neither shows up in a build:
//
//   * a KEY THAT DOES NOT EXIST. A typo — 'purchase_order' for
//     'purchase_orders' — makes a module that looks kept never keep anything.
//     Nothing errors; the work is simply lost on every switch, which is the
//     exact failure the feature was built to prevent.
//   * a SCREEN THAT SHOULD NOT BE ON IT. Every kept module holds its timers,
//     its polling and its memory for as long as the tab is open. Reports and
//     the masters are read and left; keeping them is cost with no return.
//
// The strip's own rule is checked too: it must not draw itself for a single
// tab, because one tab is not a set of tabs.
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, '..', 'src', 'App.jsx'), 'utf8').replace(/\r\n/g, '\n');

const slice = (from, to) => {
  const a = SRC.indexOf(from);
  const b = SRC.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error(`could not slice ${from} … ${to} out of App.jsx`);
  return SRC.slice(a, b);
};

const HARNESS = join(HERE, '.tabs_harness.mjs');
writeFileSync(HARNESS, `
${slice('const COMPANY_ONLY =', 'const POS_HOME =')}
${slice('const POS_HOME =', 'const POS_ITEMS =')}
const POS_ITEMS = [POS_HOME, null, ...POS_SCREENS]
${slice('const KEEPALIVE = new Set(', '// Which open tabs hold work')}
export { MODULES, POS_ITEMS, POS_SCREENS, KEEPALIVE, keepAlive }
`);

const { MODULES, POS_ITEMS, POS_SCREENS, KEEPALIVE, keepAlive } =
  await import('file://' + HARNESS);
unlinkSync(HARNESS);

let bad = 0;
const eq = (what, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { bad++; console.log(`  FAIL  ${what}\n        got  ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`); }
  else console.log(`  ok    ${what}`);
};
const head = (t) => console.log(`\n${t}`);

// ===========================================================================
head('the allow-list names modules that exist');

const moduleKeys = new Set(MODULES.map((m) => m.key));
[...KEEPALIVE].forEach((k) => {
  eq(`“${k}” is a real module`, moduleKeys.has(k), true);
});

// ===========================================================================
head('and only the ones that hold unfinished work');

// Each of these is a screen somebody can be halfway through: an order half
// typed, a consignment half keyed, an invoice being read against its
// photograph, a receipt being counted.
['purchase_orders', 'lr', 'documents', 'purchases', 'stock_audit'].forEach((k) => {
  eq(`${k} is kept`, keepAlive(k), true);
});

// Read, acted on, and left. Re-opening one costs a fetch — which is what it
// costs today, so keeping it mounted buys nothing and costs a live screen.
['reports', 'masters', 'dashboard', 'central', 'locations', 'users',
 'catalogues', 'labels', 'suppliers', 'payments', 'deadstock',
 'inventory', 'locator', 'labelprint', 'outward', 'inward', 'returns',
 'pickwh'].forEach((k) => {
  eq(`${k} is not kept`, keepAlive(k), false);
});

eq('nothing outside the two rules above is kept',
   MODULES.filter((m) => keepAlive(m.key)).length, KEEPALIVE.size);

// ===========================================================================
head('the till is kept — the strongest case of all');

// The shop is a separate app in a frame. Unmounting the frame destroys the cart
// mid-sale, and there is no way to get it back.
POS_SCREENS.forEach((p) => {
  eq(`${p.key} is kept`, keepAlive(p.key), true);
});
eq('every POS screen is covered by the prefix, not by being listed',
   [...KEEPALIVE].some((k) => k.startsWith('pos:')), false);

// ===========================================================================
head('an unknown key is never kept by accident');

['', 'nonsense', 'po', 'purchase_order', 'POS:counter', 'documentsx']
  .forEach((k) => eq(`“${k}” is not kept`, keepAlive(k), false));

// ===========================================================================
head('every open tab can be named in the strip');

// The strip shows a label, and a key it cannot name would draw a raw slug.
const labelFor = (k) => (POS_ITEMS.find((p) => p && p.key === k)
  || MODULES.find((m) => m.key === k) || {}).label || k;
[...KEEPALIVE, ...POS_SCREENS.map((p) => p.key)].forEach((k) => {
  const l = labelFor(k);
  eq(`“${k}” has a label`, l !== k && !!l, true);
});

// ===========================================================================
console.log('\n' + '='.repeat(64));
if (bad) { console.log(`${bad} FAILED`); process.exit(1); }
console.log('all tab checks passing');
