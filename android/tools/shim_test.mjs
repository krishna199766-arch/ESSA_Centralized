// Checks the JavaScript that MainActivity injects into the warehouse page.
//
//     node android/tools/shim_test.mjs
//
// The shim is the fragile seam in this app. It replaces the page's camera
// scanner — which cannot run in a WebView over plain HTTP — and to do that it
// reaches into the page's own globals: openScanner, resolveCode, S, render,
// toast. None of that is an interface anybody agreed to; rename one in
// backend/app/mobile/index.html and the phone's scan button quietly stops
// working, with nothing failing to say so. This runs the real injected code
// against stand-ins, and first checks those globals are still there.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = join(HERE, '..', '..', 'backend', 'app', 'mobile', 'index.html');

const JAVA = join(HERE, '..', 'app', 'src', 'main', 'java', 'com', 'essa', 'warehouse', 'MainActivity.java');
const java = readFileSync(JAVA, 'utf8');
// The field runs from `INJECTED_SCANNER_SHIM =` to the `;` that ends it — the one
// followed by the next annotation, not any of the many inside the JS itself.
const field = java.match(/INJECTED_SCANNER_SHIM\s*=([\s\S]*?);\s*\n\s*@/);
if (!field) {
  console.error('FAIL  could not find INJECTED_SCANNER_SHIM in MainActivity.java');
  process.exit(1);
}
const shim = [...field[1].matchAll(/"((?:[^"\\]|\\.)*)"/g)]
  .map(m => m[1].replace(/\\(.)/g, (_, c) => (c === 'n' ? '\n' : c)))
  .join('');

let fail = 0;
const ok = (name, cond, extra = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ' + extra}`);
  if (!cond) fail++;
};

// --- the page still has to provide what the shim reaches for -----------------
const page = readFileSync(PAGE, 'utf8');
for (const [what, needle] of [
  ['openScanner', 'function openScanner('],
  ['resolveCode', 'async function resolveCode('],
  ['toast', 'function toast('],
  ['render', 'function render('],
  ['S (page state)', 'const S = {'],
  ["the page's own BarcodeDetector guard", "'BarcodeDetector' in window"],
]) {
  ok(`page still defines ${what}`, page.includes(needle),
     `"${needle}" is gone from backend/app/mobile/index.html — the shim will silently stop working`);
}

// --- the page, as far as the shim can see it ---------------------------------
const PRODUCTS = { 'ESSA-00002': { sku: 'ESSA-00002' } };
let scanCalls = 0, toasts = [], renders = 0;

globalThis.window = globalThis;
globalThis.S = { selected: null, detailBack: null, screen: 'list' };
globalThis.render = () => { renders++; };
globalThis.toast = (m) => { toasts.push(m); };
globalThis.resolveCode = async (code) => PRODUCTS[code] || null;
globalThis.AndroidHost = { scan: () => { scanCalls++; } };

// The page guards on this; a WebView on http:// genuinely lacks it.
ok('WebView starts without BarcodeDetector', !('BarcodeDetector' in globalThis));

eval(shim);

// --- what the page checks before it will scan at all -------------------------
ok("the page's guard now passes", 'BarcodeDetector' in globalThis);
ok('shim is idempotent', (() => { const before = scanCalls; eval(shim); return scanCalls === before; })());

// --- call site 1: openScanner() with no callback -> open that product --------
globalThis.openScanner();
ok('native scanner was launched', scanCalls === 1, `calls=${scanCalls}`);
await globalThis.__essaScanResult('ESSA-00002');
ok('scanned product is opened', S.screen === 'detail' && S.selected?.sku === 'ESSA-00002');
ok('back target set to the list', S.detailBack === 'list');
ok('the page was re-rendered', renders === 1);

// an unknown code must say so rather than open nothing
S.screen = 'list'; S.selected = null;
globalThis.openScanner();
await globalThis.__essaScanResult('NOPE');
ok('unknown code is reported', toasts.at(-1) === 'Scanned, but no product matches that code');
ok('and does not change screen', S.screen === 'list');

// --- call site 2 and 3: openScanner(cb) -> the callback gets the raw code ----
let got = null;
globalThis.openScanner((code) => { got = code; });
ok('launched again for a callback scan', scanCalls === 3, `calls=${scanCalls}`);
await globalThis.__essaScanResult('EU1|ESSA-00002-007|ESSA-00002');
ok('callback receives the raw payload', got === 'EU1|ESSA-00002-007|ESSA-00002', `got=${got}`);

// a cancelled scan must not fire the callback or open anything
got = null; renders = 0;
globalThis.openScanner((code) => { got = code; });
await globalThis.__essaScanResult('');
ok('cancelled scan does nothing', got === null && renders === 0);

// the callback is consumed, so the next bare scan is not hijacked by it
got = null;
globalThis.openScanner();
await globalThis.__essaScanResult('ESSA-00002');
ok('stale callback is not reused', got === null && S.selected?.sku === 'ESSA-00002');

console.log(fail ? `\n${fail} FAILED` : '\nall passed');
process.exit(fail ? 1 : 0);
