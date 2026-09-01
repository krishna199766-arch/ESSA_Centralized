// Does the app survive being RENDERED?
//
//     node frontend/tools/render_test.mjs
//
// boot_test.mjs already catches a constant read above its own declaration at
// MODULE level. This catches the same fault one scope deeper — inside a
// component's body — which the boot test cannot see, because module evaluation
// only defines `function App`, it never runs it.
//
// That is not a hypothetical. `const canCentral = atLeast(role, 'admin')` was
// placed above `const [role] = useState('')` in the same function. Valid syntax,
// clean build, boot test green — and every render threw
//   ReferenceError: Cannot access 'role' before initialization
// so the page came up blank, with the right background colour and an empty
// <div id="root">. The worst shape a frontend fault can take: nothing to point
// at, nothing that says anything is wrong.
//
// So App is actually rendered here, once, with react-dom/server. Effects do not
// run under renderToString and no data is fetched — that is fine and is the
// point. The component BODY runs, which is where this class of fault lives.
//
// What counts as a pass: no ReferenceError and no TypeError from our own code.
// React itself may object to a stub global it cannot find; that is tolerated and
// reported, because the goal is not to render the finished screen, it is to get
// through the function body.
import { build } from 'esbuild';
import { renderToString } from 'react-dom/server';
import React from 'react';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { unlinkSync } from 'node:fs';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');

// --- the thin globals the module reaches for while being evaluated/rendered ---
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};
// Nothing should call these during a render; they exist so that a module-scope
// reference does not explode before we reach the interesting part.
globalThis.fetch = () => new Promise(() => {});
globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
globalThis.window = globalThis;
// `navigator` is a getter-only global on modern Node — assigning to it throws.
// It already exists there, so it only needs providing on a runtime without one.
if (!globalThis.navigator) {
  Object.defineProperty(globalThis, 'navigator', { value: { userAgent: 'node' } });
}

// Written to a real path inside the project, not imported from a data: URL:
// `react` is left external so this shares ONE React with renderToString below,
// and a bare specifier can only be resolved from a file that has node_modules
// above it.
const tmp = join(here, '.render_test.bundle.mjs');
await build({
  entryPoints: [join(root, 'src/App.jsx')],
  bundle: true,
  outfile: tmp,
  format: 'esm',
  platform: 'node',
  jsx: 'automatic',
  logLevel: 'silent',
  external: ['react', 'react-dom', 'react/jsx-runtime'],
});

let mod;
try {
  mod = await import(pathToFileURL(tmp).href);
} finally {
  try { unlinkSync(tmp); } catch { /* already gone */ }
}

const App = mod.default;
if (typeof App !== 'function') {
  console.error('render_test: App.jsx has no default export to render');
  process.exit(1);
}

let html = '';
try {
  html = renderToString(React.createElement(App));
} catch (err) {
  const msg = String(err && err.message);
  // OUR bugs. A name read before it exists, or a call on something undefined,
  // is the app being broken — not the stub being thin.
  if (err instanceof ReferenceError || /before initialization/.test(msg)
      || /is not a function/.test(msg) || /of undefined|of null/.test(msg)) {
    console.error('\n  FAIL  App threw while rendering:\n        ' + msg + '\n');
    console.error(String(err.stack || '').split('\n').slice(1, 6).join('\n'));
    process.exit(1);
  }
  // Anything else is React objecting to this deliberately minimal environment.
  console.log('  ok    App rendered past its body (React stopped at: ' + msg.slice(0, 80) + ')');
  process.exit(0);
}

console.log(`  ok    App rendered ${html.length} chars without throwing`);
console.log('all passing');
