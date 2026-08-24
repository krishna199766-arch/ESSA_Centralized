// Checks the one date format this app shows: DD-MM-YYYY.
//
//     node frontend/tools/date_format_test.mjs
//
// Two formats live in this system on purpose and the pair is easy to break.
// Dates are STORED ISO — `date_from`/`date_to` compare as plain text in SQL, and
// only ISO sorts the way a calendar does — and SHOWN day-first, because that is
// what every register page, invoice and cheque in this business is written in.
// So there are two directions to get wrong, and the expensive one is silent:
// 03-04-2026 read back as the 4th of March looks entirely plausible and is a
// different day.
//
// The helpers are lifted out of App.jsx by source rather than imported, the same
// way pricing_test.mjs does it: the file is a React module and pulling it in
// would need a bundler and a DOM for five pure functions.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, '..', 'src', 'App.jsx'), 'utf8');

// The date helpers are one contiguous block — ISO_RE through fmtLoose, ending
// where DateField's comment begins — so they are sliced out by their boundary
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

const { toISODate, readableDate, fmtDate, fmtLoose } = new Function(
  slice('const ISO_RE = ', '// ---------- picking one ----------')
  + '\nreturn { toISODate, readableDate, fmtDate, fmtLoose }'
)();

let fails = 0;
const ok = (what, got, want) => {
  const pass = got === want;
  if (!pass) fails += 1;
  console.log(`  ${pass ? 'ok  ' : 'FAIL'}  ${what}`
    + (pass ? '' : `\n          got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`));
};

console.log('\nwhat is stored — ISO, whatever the page it came off used');
ok('a picker writes ISO and it stays ISO', toISODate('2026-07-31'), '2026-07-31');
ok('a supplier invoice is day-first', toISODate('31/07/2026'), '2026-07-31');
ok('so is a register page', toISODate('31-7-26'), '2026-07-31');
ok('an e-invoice names its month', toISODate('31 Jul 2026'), '2026-07-31');
ok('03/04 is the 3rd of April, not the 4th of March', toISODate('03/04/2026'), '2026-04-03');
ok('13/04 can only be day-first', toISODate('13/04/2026'), '2026-04-13');
ok('04/13 can only be month-first', toISODate('04/13/2026'), '2026-04-13');
ok('a day that is not a day is not a date', toISODate('2026-02-30'), '');
ok('nor is OCR noise', toISODate('3l/07/26'), '');

console.log('\nwhat is shown — DD-MM-YYYY, everywhere, one format');
ok('a stored date reads back day-first', readableDate('2026-07-31'), '31-07-2026');
ok('dashes, not slashes', readableDate('2026-04-03'), '03-04-2026');
ok('a day-first value is already right', readableDate('31/07/2026'), '31-07-2026');
ok('and comes back with dashes', readableDate('31/07/2026').includes('/'), false);
ok('nothing readable, nothing shown', readableDate('3l/07/26'), '');

console.log('\nfmtDate — the form every display site uses');
ok('a date', fmtDate('2026-07-31'), '31-07-2026');
ok('a stored timestamp is a date with a time on it', fmtDate('2026-07-31T09:14:22'), '31-07-2026');
ok('a space-separated timestamp too', fmtDate('2026-07-31 09:14:22'), '31-07-2026');
ok('nothing at all gets the dash', fmtDate(null), '—');
ok('so does an empty string', fmtDate(''), '—');
ok('a caller can ask for a blank instead', fmtDate('', ''), '');
// A register cell may hold text vision could not read. Blanking it on screen
// would look exactly like the date had been lost, and the next save would make
// it true — the same rule services/dates.py keeps on the way in.
ok('what cannot be read is shown as it came', fmtDate('3l/07/26'), '3l/07/26');

console.log('\nfmtLoose — a date hiding in a cell that could be anything');
ok('an ISO date in a report cell', fmtLoose('2026-07-31'), '31-07-2026');
ok('an ISO timestamp in a report cell', fmtLoose('2026-07-31T09:14:22'), '31-07-2026');
ok('an invoice code is not a date', fmtLoose('INV-2026-07'), 'INV-2026-07');
ok('nor is a number that starts like one', fmtLoose('2026'), '2026');
ok('a quantity passes through untouched', fmtLoose(1234), 1234);
ok('so does a null', fmtLoose(null), null);
// fmtLoose runs over EVERY report cell and every CSV field, so it has to be
// deliberately narrow: a day-first date typed into a master record is already
// in the house format and must not be re-read as if it were ISO.
ok('an already-day-first value is left alone', fmtLoose('31/07/2026'), '31/07/2026');

console.log(fails ? `\n${fails} failing\n` : '\nall passing\n');
process.exit(fails ? 1 : 0);
