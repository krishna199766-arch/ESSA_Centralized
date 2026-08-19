// Checks the line-item pricing chain: cost → sell price → MRP.
//
//     node frontend/tools/pricing_test.mjs
//
// Money, and two percentages that are easy to mix up — the buffer over the cost
// and the buffer over the shelf price are different numbers spanning different
// pairs of prices, and swapping them produces figures that look plausible on
// every line. Worth a test that states the arithmetic in the terms the trade
// uses rather than in the terms the code does.
//
// recalcLine and its helpers are lifted out of App.jsx by source rather than
// imported: the file is a React module and pulling it in would need a bundler
// and a DOM for four pure functions.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, '..', 'src', 'App.jsx'), 'utf8');

// The pricing helpers are one contiguous block in App.jsx — nf and r2 above it,
// then fromMrp through recalcLine — so they are sliced out by their boundary
// lines rather than parsed. Anything that moves them breaks this loudly, which
// is the right failure: the test is only meaningful against the real code.
function slice(fromLine, toLine) {
  const lines = SRC.split('\n');
  const a = lines.findIndex(l => l.startsWith(fromLine));
  const b = lines.findIndex(l => l.startsWith(toLine));
  if (a < 0 || b < 0 || b <= a) {
    throw new Error(`could not slice ${fromLine} … ${toLine} — has App.jsx been reorganised?`);
  }
  return lines.slice(a, b).join('\n');
}

const { recalcLine } = new Function(
  slice('const nf = (v) => {', 'function taxBase(')
  + '\nreturn { recalcLine }'
)();

let fails = 0;
const check = (ok, label, detail = '') => {
  if (ok) { console.log(`  ok    ${label}`); return; }
  fails++; console.error(`  FAIL  ${label}${detail ? ' — ' + detail : ''}`);
};
const near = (a, b, tol = 0.02) => a != null && Math.abs(a - b) < tol;

// ---------------------------------------------------------------------------
console.log('the worked example: 320 → +40% → 448 → +25% → 560');

// goods arrive with a cost and no tag; both buffers are the shop's decision
let row = { qty: 1, rate: 320, buffer_pct: 40 };
row = recalcLine(row, 'buffer_pct', {});
check(near(row.sale_price, 448), 'rate 320 + 40% = sell 448', `got ${row.sale_price}`);

row = recalcLine({ ...row, mrp_buffer_pct: 25 }, 'mrp_buffer_pct', row);
check(near(row.mrp, 560), 'sell 448 + 25% = MRP 560', `got ${row.mrp}`);

console.log('\nthe percentages that follow from those prices:');
check(near(row.sale_discount_pct, 20), 'sale disc = (560−448)/560 = 20%', `got ${row.sale_discount_pct}`);
check(near(row.discount_pct, 42.86), 'purchase disc = (560−320)/560 = 42.86%', `got ${row.discount_pct}`);

console.log('\na printed MRP is not overwritten:');
let printed = recalcLine({ qty: 1, rate: 320, mrp: 995, buffer_pct: 40, mrp_buffer_pct: 25 },
                         'buffer_pct', {});
check(printed.mrp === 995, 'invoice MRP 995 stands when the buffer moves', `got ${printed.mrp}`);
check(near(printed.sale_price, 448), 'the sell price is still priced off cost', `got ${printed.sale_price}`);

console.log('\n…but typing the MRP buffer is an instruction, and wins:');
const retagged = recalcLine({ ...printed, mrp_buffer_pct: 25 }, 'mrp_buffer_pct', printed);
check(near(retagged.mrp, 560), 'typed MRP buffer re-prices the tag to 560', `got ${retagged.mrp}`);

console.log('\ntyping a sell price back-solves the buffer, and is not overwritten:');
let typed = recalcLine({ qty: 1, rate: 320, buffer_pct: 40, sale_price: 480 }, 'sale_price',
                       { rate: 320, buffer_pct: 40, sale_price: 448 });
check(near(typed.sale_price, 480), 'the typed 480 survives', `got ${typed.sale_price}`);
check(near(typed.buffer_pct, 50), 'buffer restated as (480−320)/320 = 50%', `got ${typed.buffer_pct}`);

console.log('\nthe purchase side still works from the tag down:');
let bill = recalcLine({ qty: 2, mrp: 995, discount_pct: 34.67 }, 'discount_pct', {});
check(near(bill.rate, 650, 0.05), 'MRP 995 − 34.67% = rate 650 (the % is rounded)', `got ${bill.rate}`);
check(near(bill.amount, 1300, 0.10), 'qty 2 × 650 = 1300 (the % is rounded)', `got ${bill.amount}`);

console.log('\nround trip — a rounded price reads its buffer back:');
const back = recalcLine({ qty: 1, rate: 320, sale_price: 448 }, 'rate', {});
check(near(bufferOf(448, 320), 40), '448 against 320 is 40%');
function bufferOf(top, base) { return Math.round((top / base - 1) * 10000) / 100; }

console.log(fails ? `\n${fails} failing` : '\nall passing');
process.exit(fails ? 1 : 0);
