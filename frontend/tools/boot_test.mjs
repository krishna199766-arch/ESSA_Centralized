// Does the built bundle survive being LOADED?
//
//     npm run build && node frontend/tools/boot_test.mjs
//
// `vite build` parses, bundles and writes. It evaluates nothing, and it was
// never going to: a constant read above its own declaration is valid syntax, so
// the build is clean and the module throws ReferenceError the instant a browser
// evaluates it. That takes the whole bundle with it, and what you get is a page
// painted in the right background colour with an empty <div id="root"> in it.
//
// That shipped. It is the worst shape a frontend fault can take — no broken
// screen to point at, nothing in the UI that says anything is wrong, and a page
// that looks like it is still loading — and every check we had said the build
// was clean, because it was.
//
// So the bundle is RUN here, in a VM with a deliberately thin DOM. It is not
// expected to finish: React reaches for something this stub does not have and
// gives up, usually at error #299 (createRoot got a container that is not an
// element). That is the pass. What is checked is that it got PAST module
// evaluation, because that is where this class of fault lives.
//
// Keeping the stub thin is deliberate. Every global added here is one more thing
// to maintain, and the goal is not to run the app — it is to get far enough into
// it that our own module has been evaluated. If a future React needs one more
// global to reach that point, add it; if the run starts succeeding all the way
// through, something is wrong with the stub, not right with the app.
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));
const DIST = process.argv[2] || join(HERE, '..', 'dist', 'assets');

let file;
try {
  file = readdirSync(DIST).filter((f) => f.endsWith('.js')).sort().pop();
} catch {
  console.error(`No build to check in ${DIST}.\nRun \`npm run build\` in frontend/ first.`);
  process.exit(2);
}
if (!file) {
  console.error(`No .js bundle in ${DIST} — did the build write anywhere else?`);
  process.exit(2);
}
const code = readFileSync(join(DIST, file), 'utf8');
console.log(`bundle: ${file}  ${(code.length / 1024).toFixed(0)}kB`);

const noop = () => {};
const el = () => new Proxy({}, {
  get: (t, k) => (k in t ? t[k] : (k === 'style' || k === 'dataset' ? {} : noop)),
  set: () => true,
});
const doc = {
  createElement: el, createElementNS: el, createTextNode: el, createComment: el,
  head: el(), body: el(), documentElement: el(),
  getElementById: () => el(), querySelector: () => el(), querySelectorAll: () => [],
  addEventListener: noop, removeEventListener: noop, defaultView: null,
};
const store = { getItem: () => null, setItem: noop, removeItem: noop, clear: noop };
const win = {
  document: doc, localStorage: store, sessionStorage: store,
  location: { href: 'https://example.test/', pathname: '/', search: '', origin: 'https://example.test' },
  navigator: { userAgent: 'node', language: 'en' },
  addEventListener: noop, removeEventListener: noop,
  matchMedia: () => ({ matches: false, addListener: noop, addEventListener: noop }),
  requestAnimationFrame: noop, cancelAnimationFrame: noop,
  // never resolves: nothing in here should reach the network, and a rejected
  // fetch would look like a fault in code that is behaving correctly
  fetch: () => new Promise(() => {}),
  setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask,
  console, Promise, URL, URLSearchParams, TextEncoder, TextDecoder,
  performance: { now: () => 0 },
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  open: noop, alert: noop, confirm: () => false, prompt: () => null,
  history: { pushState: noop, replaceState: noop }, screen: { width: 1280 },
  devicePixelRatio: 1, structuredClone: (v) => v,
  // the handful React itself reaches for while it is still initialising. Without
  // these the run dies inside the vendor half of the bundle and never reaches
  // our own module, which would make this whole check worthless — it would pass
  // on a broken build. That happened on the first attempt at writing it.
  MutationObserver: class { observe() {} disconnect() {} takeRecords() { return []; } },
  IntersectionObserver: class { observe() {} disconnect() {} unobserve() {} },
  ResizeObserver: class { observe() {} disconnect() {} unobserve() {} },
  Event: class { constructor(t) { this.type = t; } },
  CustomEvent: class { constructor(t) { this.type = t; } },
  Node: class {}, Element: class {}, HTMLElement: class {}, Text: class {},
  HTMLIFrameElement: class {}, SVGElement: class {}, DocumentFragment: class {},
  Range: class {}, FormData: class {}, Headers: class {}, Request: class {},
  Response: class {}, Image: class {}, Blob: class {}, File: class {},
  XMLHttpRequest: class {},
  AbortController: class { constructor() { this.signal = {}; } abort() {} },
  crypto: { getRandomValues: (a) => a, randomUUID: () => 'x' },
};
win.window = win;
win.self = win;
win.globalThis = win;

let thrown = null;
try {
  vm.createContext(win);
  new vm.Script(code, { filename: file }).runInContext(win, { timeout: 20000 });
} catch (e) {
  thrown = e;
}

// The shapes that mean OUR code is broken rather than the stub being thin. A
// name that cannot be accessed yet is always ours; a name that is simply not
// defined is ours unless it is something a browser would have provided.
const BROWSERISH = /document|window|navigator|HTML|Element|Node|Event|Observer|matchMedia|crypto|Storage|Worker|fetch|Audio|Canvas/i;
const fatal = thrown && (
  /before initialization/i.test(thrown.message)
  || (/is not defined/i.test(thrown.message) && !BROWSERISH.test(thrown.message))
);

if (fatal) {
  console.error('\nFAIL — the bundle throws while it is still being evaluated:');
  console.error(`  ${thrown.name}: ${thrown.message}`);
  console.error('\nIn a browser that is a blank page, not a broken screen: the module');
  console.error('never finishes, so nothing renders and nothing says why.');
  console.error('\nUsually a module-level const used above where it is declared —');
  console.error('check anything interpolated into a top-level array or object literal.');
  process.exit(1);
}
console.log(thrown
  ? `ok   module evaluated; stopped later on the thin DOM — ${thrown.name}: ${String(thrown.message).slice(0, 90)}`
  : 'ok   module evaluated clean');
console.log('\nall passed');
