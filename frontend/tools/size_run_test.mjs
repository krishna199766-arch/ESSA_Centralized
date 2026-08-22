// The size run, end to end: "28-2-38" → sizes → quantities → invoice lines.
//
//     node frontend/tools/size_run_test.mjs
//
// Five pieces of arithmetic that a screenshot cannot check and a build will
// never notice:
//
//   parseSizeRun    what "28-2-38" means, and what "16*22" does NOT
//   spreadQty       eighteen pieces over six sizes, and what happens when it
//                   does not divide
//   runFromSize     reading a run off the size column when the quantity proves
//                   the step
//   expandRows      one invoice line becoming several WITHOUT moving Σ qty or
//                   Σ value — the property the whole feature stands on
//   groupItems      which of the resulting lines fold back together
//
// Everything is lifted out of App.jsx by source rather than imported, the way
// pricing_test.mjs does it: the file is a React module and pulling it in would
// need a bundler and a DOM for a handful of pure functions. The slice markers
// are real declarations, so anything that moves them breaks this loudly — which
// is the right failure. A test that quietly stops covering the code is worse
// than no test.
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
// git checks this tree out with CRLF on Windows; the markers below are LF
const SRC = readFileSync(join(HERE, '..', 'src', 'App.jsx'), 'utf8').replace(/\r\n/g, '\n');

const slice = (from, to) => {
  const a = SRC.indexOf(from);
  const b = SRC.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error(`could not slice ${from} … ${to} out of App.jsx`);
  return SRC.slice(a, b);
};

// --- build one module out of the three regions under test --------------------
const round3 = (n) => Math.round((+n || 0) * 1000) / 1000;
const HARNESS = join(HERE, '.size_run_harness.mjs');
writeFileSync(HARNESS, `
const num = (v) => (v === '' || v == null ? null : isNaN(+v) ? v : +v)
${slice('const nf = (v) => {', '// ---------- line items ----------')}
${slice('const ITEM_GROUP_BY =', 'const ITEM_CALC =')}
// the component's own methods, with the React around them stubbed out
let items = [], runSpec = '', runRows = []
const setItems = (v) => { items = v }
const setRunRows = (v) => { runRows = v }
const setRunFor = () => {}, setRunSpec = () => {}, setRunNote = () => {}
export let asked = null, answer = true
const window = { confirm: (q) => { asked = q; return answer } }
export const say = (v) => { answer = v }
${slice('  const expandRows = (it, rows) => {', '  const SIZE_STEPS =')}
${slice('  const SIZE_STEPS =', '  const openRun =')}
export const generated = (list, spec, i) => {
  items = list; runSpec = spec; runRows = []; genRows(i); return runRows
}
export const split = (list, spec, i, edit) => {
  items = list; runSpec = spec; runRows = []
  genRows(i)
  if (edit) runRows = edit(runRows)
  splitRun(i)
  return items
}
export const splitAll = (list, spec, i) => {
  items = list; runSpec = spec; runRows = []
  genRows(i); splitRunAll(i)
  return items
}
export const alike = (list, i) => { items = list; return sameSizeAs(i) }
export { parseSizeRun, spreadQty, runFromSize, groupItems, itemGroupable }
`);
let M;
try {
  M = await import('file://' + HARNESS.replace(/\\/g, '/'));
} finally {
  try { unlinkSync(HARNESS); } catch { /* leave it if Windows is holding it */ }
}

let bad = 0;
const eq = (what, got, want) => {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g !== w) { bad++; console.log(`  FAIL  ${what}\n        got  ${g}\n        want ${w}`); }
  else console.log(`  ok    ${what}`);
};
const sum = (rows, k) => Math.round(rows.reduce((s, r) => s + (+r[k] || 0), 0) * 100) / 100;
const head = (t) => console.log(`\n${t}`);

// ===========================================================================
head('reading the run');
const P = M.parseSizeRun;
eq('28-2-38 is six sizes in twos', P('28-2-38').sizes, ['28', '30', '32', '34', '36', '38']);
eq('spaces and slashes read the same', P('28 - 2 - 38').sizes, P('28/2/38').sizes);
eq('28-38 steps by one', P('28-38').sizes.length, 11);
eq('38-2-28 is the same run counted down', P('38-2-28').sizes,
   ['38', '36', '34', '32', '30', '28']);
eq('decimals survive', P('7.5-.5-9.5').sizes, ['7.5', '8', '8.5', '9', '9.5']);
eq('a run that overshoots stops short of the end', P('28-3-38').sizes,
   ['28', '31', '34', '37']);
eq('one size is a run of one', P('32-2-32').sizes, ['32']);
eq('blank is not an error', P(''), { sizes: [], why: '' });
eq('letters are not a run', P('S-M-L').sizes, []);
eq('one number is not a run', P('28').sizes, []);
eq('a zero step is refused', P('28-0-38').sizes, []);
eq('a step of .2 is a typo, not fifty-one sizes', P('28-.2-38').sizes, []);
eq('and every refusal says why', !!P('S-M-L').why, true);

head('…and what is NOT a run');
// "16*22" is a size range on one supplier's bills and a bedsheet on another.
// Two numbers and no step, so it is never a run on its own say-so.
for (const m of ['16*22', '16x22', '127 X 200', '30×40', '16*2*22']) {
  eq(`${m} has no step of its own`, P(m).sizes, []);
}
eq('and it asks for one', /step/.test(P('16*22').why), true);

head('spreading the pieces');
const S = M.spreadQty;
eq('18 over 6 is three each', S(18, 6), [3, 3, 3, 3, 3, 3]);
eq('20 over 6 puts the remainder first, never drops it', S(20, 6), [4, 4, 3, 3, 3, 3]);
eq('4 over 6 leaves two sizes at zero', S(4, 6), [1, 1, 1, 1, 0, 0]);
eq('nothing to spread', S(0, 3), [0, 0, 0]);
eq('nowhere to spread it', S(18, 0), []);
eq('10.5 over 4, for goods sold by the metre', S(10.5, 4), [2.625, 2.625, 2.625, 2.625]);
for (const [t, n] of [[18, 6], [20, 6], [10, 3], [7.3, 3], [10.5, 4], [1, 7]]) {
  eq(`${t} over ${n} adds back up to ${t}`,
     round3(S(t, n).reduce((a, b) => a + b, 0)), round3(t));
}

head('reading the run off the size column');
const R = M.runFromSize;
// the step is whichever one the line's quantity divides across exactly
for (const q of [4, 8, 12]) eq(`16*22 with ${q} pcs proves step 2`, R('16*22', q), '16-2-22');
eq('and that makes 16, 18, 20, 22', P(R('16*22', 4)).sizes, ['16', '18', '20', '22']);
eq('7 pieces prove step 1 instead', R('16*22', 7), '16-1-22');
eq('5 pieces prove nothing', R('16*22', 5), '');
eq('no quantity, no suggestion', R('16*22', null), '');
eq('a run written out in full is taken as written', R('28-2-38', 17), '28-2-38');
eq('a bedsheet stays a bedsheet', R('127 X 200', 6), '');
eq('…whatever the quantity — 74 sizes is past the cap', R('127 X 200', 74), '');
eq('a plain size suggests nothing', R('XL', 4), '');
eq('nor a single number', R('32', 4), '');
eq('nor one size repeated', R('22*22', 4), '');

head('splitting an invoice line');
const line = (over = {}) => ({
  description: 'FROCK', design: '4313', hsn: '620469', uom: 'PCS', size: '16*22',
  qty: 4, rate: 1350, amount: 5400, taxable_value: 5400, ...over,
});
{
  const before = [line(), { description: 'SHIRT', qty: 8, rate: 500, amount: 4000, taxable_value: 4000 }];
  const after = M.split(before, '16-2-22', 0);
  eq('one line becomes four', after.length, 5);
  eq('one size each', after.slice(0, 4).map((r) => r.size), ['16', '18', '20', '22']);
  eq('one piece each', after.slice(0, 4).map((r) => r.qty), [1, 1, 1, 1]);
  eq('the rest of the line carries across',
     [after[0].description, after[0].design, after[0].hsn, after[0].rate],
     ['FROCK', '4313', '620469', 1350]);
  eq('the line beside it is untouched', after[4].description, 'SHIRT');
  eq('Σ qty does not move', sum(after, 'qty'), 12);
  eq('Σ value does not move', sum(after, 'amount'), 9400);
  eq('Σ taxable does not move', sum(after, 'taxable_value'), 9400);
}
{
  // the list is generated, not applied — correcting it is the whole point
  const before = [line({ qty: 12, amount: 16200, taxable_value: 16200 })];
  eq('generated as an even spread', M.generated(before, '28-2-34', 0),
     [{ size: '28', qty: '3' }, { size: '30', qty: '3' },
      { size: '32', qty: '3' }, { size: '34', qty: '3' }]);
  const edited = M.split(before, '28-2-34', 0, (rows) => rows.map((r) =>
    (r.size === '28' ? { ...r, qty: '4' } : r.size === '34' ? { ...r, qty: '2' } : r)));
  eq('a hand-set count is the count that lands',
     edited.map((r) => `${r.size}=${r.qty}`), ['28=4', '30=3', '32=3', '34=2']);
  eq('Σ qty still 12', sum(edited, 'qty'), 12);
  eq('Σ value still 16200', sum(edited, 'amount'), 16200);
  eq('and the value follows the pieces', edited.map((r) => r.amount),
     [5400, 4050, 4050, 2700]);

  const dropped = M.split(before, '28-2-34', 0,
    (rows) => rows.filter((r) => r.size !== '34').map((r) => ({ ...r, qty: '4' })));
  eq('a size that did not come makes no line', dropped.map((r) => r.size), ['28', '30', '32']);
  eq('Σ qty still 12', sum(dropped, 'qty'), 12);
  const zeroed = M.split(before, '28-2-34', 0, (rows) => rows.map((r) =>
    (r.size === '34' ? { ...r, qty: '0' } : { ...r, qty: '4' })));
  eq('nor does a size set to zero', zeroed.length, 3);
  const added = M.split(before, '28-2-32', 0, (rows) =>
    [...rows.map((r) => ({ ...r, qty: '3' })), { size: '36', qty: '3' }]);
  eq('a size the run never covered can be added', added.map((r) => r.size),
     ['28', '30', '32', '36']);
  eq('Σ qty still 12', sum(added, 'qty'), 12);
}
{
  // a line the invoice priced only as a total, and one that states its own taxable
  const noRate = M.split([{ description: 'ASSORTED', qty: 7, rate: null,
                            amount: 1000, taxable_value: 1000 }], '28-2-34', 0);
  eq('7 over 4 with no rate to re-derive from', noRate.map((r) => r.qty), [2, 2, 2, 1]);
  eq('Σ amount still 1000', sum(noRate, 'amount'), 1000);
  const stated = M.split([{ description: 'SHIRT', qty: 12, rate: 500,
                            amount: 6000, taxable_value: 5400 }], '38-2-44', 0);
  eq('a stated taxable value is shared, not copied', sum(stated, 'taxable_value'), 5400);
  eq('and the amount still comes to 6000', sum(stated, 'amount'), 6000);
  const odd = M.split([{ description: 'KURTA', qty: 7, rate: 143,
                         amount: 1001, taxable_value: 953.33 }], '28-2-34', 0);
  eq('an odd total survives the rounding', sum(odd, 'taxable_value'), 953.33);
}
{
  // the whole Frock invoice in one press
  const before = [['4313', 4, 1350], ['4437', 8, 1350], ['4396', 4, 1250],
                  ['4445', 4, 1295], ['4444', 4, 1350], ['4501', 8, 1150]]
    .map(([design, qty, rate]) => line({ design, qty, rate,
                                         amount: qty * rate, taxable_value: qty * rate }));
  eq('all six lines read the same way', M.alike(before, 0), [0, 1, 2, 3, 4, 5]);
  const after = M.splitAll(before, '16-2-22', 0);
  eq('six lines become twenty-four', after.length, 24);
  eq('Σ qty holds at 32', sum(after, 'qty'), 32);
  eq('Σ value holds at the invoice total', sum(after, 'amount'), 40980);
  eq('4 pcs is one of each', after.slice(0, 4).map((r) => r.qty), [1, 1, 1, 1]);
  eq('8 pcs is two of each', after.slice(4, 8).map((r) => r.qty), [2, 2, 2, 2]);
  eq('each keeps its own rate', [after[0].rate, after[8].rate, after[20].rate],
     [1350, 1250, 1150]);
  eq('and it asked before rewriting lines nobody looked at',
     /all 6 line\(s\)/.test(M.asked), true);

  const mixed = [line(), { ...line({ description: 'SHIRT', size: '38-2-44' }) }, line()];
  eq('only the lines that match', M.alike(mixed, 0), [0, 2]);
  const part = M.splitAll(mixed, '16-2-22', 0);
  eq('the odd one out is left whole',
     part.filter((r) => r.description === 'SHIRT').length, 1);
  M.say(false);
  eq('and cancelling changes nothing', M.splitAll([line(), line()], '16-2-22', 0).length, 2);
  M.say(true);
  eq('a blank size matches nothing', M.alike([{ description: 'A', size: '' },
                                              { description: 'B', size: null }], 0), []);
}

head('folding them back together');
const G = M.groupItems;
const shape = (its) => G(its).map((g) => `${g.from}-${g.to}`);
const frock = (design, size, over = {}) => ({
  description: 'FROCK', design, hsn: '620469', uom: 'PCS', size, qty: 1,
  rate: 1350, mrp: null, discount_pct: null, sale_price: null, ...over,
});
{
  const items = [];
  for (const d of ['4313', '4437', '4396']) {
    for (const z of ['16', '18', '20', '22']) items.push(frock(d, z));
  }
  eq('three garments, four sizes each', shape(items), ['0-4', '4-8', '8-12']);
}
eq('a different design breaks the group',
   shape([frock('4313', '16'), frock('4313', '18'), frock('4444', '20')]), ['0-2', '2-3']);
eq('so does a different rate',
   shape([frock('4313', '16'), frock('4313', '18', { rate: 1250 })]), ['0-1', '1-2']);
eq('…a different HSN',
   shape([frock('4313', '16'), frock('4313', '18', { hsn: '620462' })]), ['0-1', '1-2']);
eq('…a corrected MRP on one line',
   shape([frock('4313', '16'), frock('4313', '18', { mrp: 1800 })]), ['0-1', '1-2']);
eq('blank rows never group', shape([{}, {}, {}]), ['0-1', '1-2', '2-3']);
eq('nor a line with no size', shape([frock('4313', ''), frock('4313', '')]), ['0-1', '1-2']);
eq('case and padding are the same garment',
   shape([frock('4313', '16'), frock('4313', '18', { description: ' frock ' })]), ['0-2']);
eq('two identical runs with a stranger between them stay two',
   shape([frock('4313', '16'), frock('4313', '18'),
          frock('4313', '20', { description: 'SHIRT' }),
          frock('4313', '20'), frock('4313', '22')]), ['0-2', '2-3', '3-5']);
{
  const items = [frock('4313', '16'), frock('4313', '18'), {},
                 frock('4444', '20'), frock('4444', '22')];
  eq('every line is covered exactly once, in order',
     G(items).reduce((n, g) => (g.from === n ? g.to : -1), 0), items.length);
}

console.log(bad ? `\n${bad} FAILED` : '\nall passing');
process.exit(bad ? 1 : 0);
