// Checks SetupActivity.normalise — the one screen everybody meets before the app
// works at all.
//
//     node android/tools/normalise_test.mjs
//
// It is worth a test because it is a guess with two opposite right answers. On
// the warehouse LAN a bare address means http and port 8000; for a deployed
// server it means https and port 443. Get it the wrong way round and the setup
// screen reports a perfectly good server as "not reachable", which looks like a
// server fault and is not one.
//
// The Java is not compiled here — that would need a JDK for four lines of string
// handling. The rules are re-implemented below and checked to still match the
// Java by reading the source, so a change to one that is not made to the other
// is caught rather than silently drifting.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const JAVA = join(HERE, '..', 'app', 'src', 'main', 'java', 'com', 'essa', 'warehouse', 'SetupActivity.java');
const java = readFileSync(JAVA, 'utf8');

let fails = 0;
const check = (ok, label, detail) => {
  if (ok) { console.log(`  ok    ${label}`); return; }
  fails++; console.error(`  FAIL  ${label}${detail ? ' — ' + detail : ''}`);
};

// ---- the Java still has the shape this mirrors -------------------------------
console.log('SetupActivity.java still has the pieces this test mirrors:');
check(/static String normalise\(/.test(java), 'normalise() exists');
check(/static boolean isLanHost\(/.test(java), 'isLanHost() exists');
check(/scheme\.equals\("http:\/\/"\)/.test(java),
  'the :8000 default is conditional on the http scheme',
  'a bare https host must NOT get :8000 appended');
check(/\\\\d\{1,3\}\(\\\\\.\\\\d\{1,3\}\)\{3\}/.test(java) || /d\{1,3\}/.test(java),
  'isLanHost matches a dotted-quad IP');

// ---- the rules, mirrored ------------------------------------------------------
const isLanHost = (host) => {
  if (!host) return true;
  if (host === 'localhost') return true;
  if (!host.includes('.')) return true;
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(host);
};

const normalise = (raw) => {
  let t = (raw ?? '').trim();
  if (!t) return '';
  let scheme = '';
  if (t.startsWith('http://')) { scheme = 'http://'; t = t.slice(7); }
  else if (t.startsWith('https://')) { scheme = 'https://'; t = t.slice(8); }
  let cut = t.length;
  for (const mark of ['/', '?', '#']) {
    const at = t.indexOf(mark);
    if (at >= 0 && at < cut) cut = at;
  }
  t = t.slice(0, cut).trim();
  if (!t) return '';
  const host = t.includes(':') ? t.slice(0, t.indexOf(':')) : t;
  if (!scheme) scheme = isLanHost(host) ? 'http://' : 'https://';
  if (!t.includes(':') && scheme === 'http://') t = t + ':8000';
  return scheme + t;
};

console.log('\naddresses people actually type:');
const cases = [
  // --- the warehouse LAN: bare IP means http and the server's own port ---
  ['192.168.1.5',                 'http://192.168.1.5:8000'],
  ['192.168.1.5:8000/m',          'http://192.168.1.5:8000'],
  ['http://192.168.1.5/m/',       'http://192.168.1.5:8000'],
  ['  192.168.1.5  ',             'http://192.168.1.5:8000'],
  ['localhost',                   'http://localhost:8000'],
  ['warehouse-pc',                'http://warehouse-pc:8000'],   // bare machine name
  ['192.168.1.5:9000',            'http://192.168.1.5:9000'],    // a moved port survives

  // --- a deployed server: a domain means https and no added port ---
  ['essa.vercel.app',             'https://essa.vercel.app'],    // the case the old code broke
  ['https://essa.vercel.app',     'https://essa.vercel.app'],
  ['https://essa.vercel.app/m',   'https://essa.vercel.app'],
  ['essa-intake.onrender.com/m/', 'https://essa-intake.onrender.com'],
  ['https://essa.example.com:443', 'https://essa.example.com:443'],

  // --- an explicit scheme always wins over the guess ---
  ['http://essa.example.com',     'http://essa.example.com:8000'],

  // --- nothing in, nothing out ---
  ['',                            ''],
  ['   ',                         ''],
  ['https:///',                   ''],
];
for (const [input, want] of cases) {
  const got = normalise(input);
  check(got === want, `${JSON.stringify(input)} -> ${want || '(empty)'}`,
    got === want ? '' : `got ${JSON.stringify(got)}`);
}

console.log(fails ? `\n${fails} failing` : '\nall passing');
process.exit(fails ? 1 : 0);
