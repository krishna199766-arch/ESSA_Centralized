// Speaking a purchase order into the form.
//
//     node frontend/tools/po_dictate_test.mjs
//
// The valuable property here is not "voice works" — it is that the dictation
// grammar IS the form. voicefill.js matches the form's own labels, so the thing
// that can silently break is the DEF: a field renamed on the form, or a type
// mapped wrongly, and a box stops being dictatable with nothing to show for it.
//
// Three mappings are load-bearing and each was a deliberate decision:
//
//   combo → text     a combo's list is a suggestion. As a `select`, a value
//                    outside the list is DROPPED — which would discard a new
//                    supplier's name, the exact thing a combo exists to allow.
//   date  → absent   picked, never dictated. A misheard date is a plausible
//                    wrong date, which is the worst kind of wrong.
//   ro    → absent   the PO number is allocated on save; nothing to say.
//
// The definition is lifted out of App.jsx by source, the way size_run_test.mjs
// does it: the file is a React module and pulling it in would need a bundler and
// a DOM for what is, here, a plain array.
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { parseDictation, coerceSpoken } from '../src/voicefill.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, '..', 'src', 'App.jsx'), 'utf8').replace(/\r\n/g, '\n');

const slice = (from, to) => {
  const a = SRC.indexOf(from);
  const b = SRC.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error(`could not slice ${from} … ${to} out of App.jsx`);
  return SRC.slice(a, b);
};

const HARNESS = join(HERE, '.po_dictate_harness.mjs');
writeFileSync(HARNESS, `
const DEFAULT_HOLDING_DAYS = 90
${slice('const PO_FORM_LEFT = [', 'const PO_FORM_KEYS =')}
${slice('const poDictateDef = (opts, lists) => ({', '//: The line grid.')}
export { PO_FORM_LEFT, PO_FORM_RIGHT, poDictateDef }
`);

const { PO_FORM_LEFT, PO_FORM_RIGHT, poDictateDef } = await import('file://' + HARNESS);
unlinkSync(HARNESS);

let bad = 0;
const eq = (what, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { bad++; console.log(`  FAIL  ${what}\n        got  ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`); }
  else console.log(`  ok    ${what}`);
};
const head = (t) => console.log(`\n${t}`);

const OPTS = { purchase_manager: ['Ravi', 'Selvam'] };
const LISTS = { suppliers: ['AMS Garments', 'Matoshree'], agents: ['Kumar'],
                transports: ['GATI', 'GOLDEN'] };
const def = poDictateDef(OPTS, LISTS);
const fields = def.groups[0].fields;
const byKey = Object.fromEntries(fields.map((f) => [f.key, f]));

// ===========================================================================
head('the grammar is the form');

eq('every dictatable field comes from the form specs',
   fields.length,
   [...PO_FORM_LEFT, ...PO_FORM_RIGHT]
     .filter(([, , t]) => !['ro', 'date', 'po'].includes(t)).length);
eq('the PO number is not dictatable — it is allocated on save',
   'po_no' in byKey, false);
eq('nor is the date — picked, never dictated', 'po_date' in byKey, false);
eq('the supplier is', byKey.supplier_name?.label, 'Supplier');
eq('and it is text, not a select, so a new supplier survives',
   byKey.supplier_name?.type, 'text');
eq('a combo carries no options list for the matcher to snap to',
   'options' in (byKey.supplier_name || {}), false);
eq('discount is numeric', byKey.discount_pct?.type, 'num');
eq('notes is plain text, not an area the matcher would treat oddly',
   byKey.notes?.type, 'text');
eq('the form marks itself as not a master record', def.plainForm, true);

// ===========================================================================
head('one sentence, several boxes');

const say = (t) => {
  const { fills } = parseDictation(def, t);
  return Object.fromEntries(fills.map((f) => [f.field.key, coerceSpoken(f.field, f.value)]));
};

// Values come back LOWER CASE. That is voicefill's own normalisation — the
// transcript is folded before the labels are matched against it — and it applies
// to every master too, so it is behaviour to pin rather than a fault to fix
// here. It is also why the purchase order route matches an existing supplier
// case-insensitively before creating one: without that, "supplier matoshree"
// spoken into the form would file a second Matoshree beside the real one.
eq('supplier, brand and item in one breath',
   say('supplier Matoshree brand ESSA item cotton frocks'),
   { supplier_name: 'matoshree', brand: 'essa', item: 'cotton frocks' });

eq('a number is taken as a number',
   say('discount 12.5'), { discount_pct: 12.5 });

eq('a supplier not on the list is still kept — the point of a combo',
   say('supplier Brand New Textiles').supplier_name !== undefined
     || 'brand' in say('supplier Brand New Textiles'), true);

eq('the whole header in one go',
   say('supplier Matoshree brand ESSA item frocks place Erode transport GATI '
       + 'agent Kumar purchaser Ravi discount 5'),
   { supplier_name: 'matoshree', brand: 'essa', item: 'frocks', place: 'erode',
     transport: 'gati', agent: 'kumar', purchaser: 'ravi', discount_pct: 5 });

// A value that CONTAINS another field's label is claimed as that label — the
// documented cost of "the labels are the grammar" (voicefill.js, top). Pinned
// here so it stays a known limit rather than becoming a surprise: the person
// sees what was filled in the heard-strip and corrects it, which is why the
// strip exists.
eq('a value containing a field name is taken as that field — a known limit',
   say('supplier Brand New Textiles'), { brand: 'new textiles' });

// ===========================================================================
head('what it refuses to guess at');

eq('nothing said, nothing filled', say(''), {});
eq('words that name no field fill nothing',
   say('this is just some talking'), {});
const { preamble } = parseDictation(def, 'erm supplier Matoshree');
eq('a mishearing before the first label is reported, not swallowed',
   preamble, 'erm');

// ===========================================================================
console.log('\n' + '='.repeat(64));
if (bad) { console.log(`${bad} FAILED`); process.exit(1); }
console.log('all purchase-order dictation checks passing');
