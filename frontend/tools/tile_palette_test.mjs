// The dashboard tile accents, measured rather than eyeballed.
//
//     node frontend/tools/tile_palette_test.mjs
//
// Colour on a dashboard is not decoration here: it is how a figure is found in a
// wall of near-identical boxes, and four things about it can be silently wrong.
// None of them shows up in a build, and all four look fine on the designer's
// monitor:
//
//   * TEXT THAT CANNOT BE READ. A tint deep enough to be pretty is a tint the
//     11px uppercase label fails AA against. --muted measures 4.1–4.3:1 on these
//     washes, which is why tinted tiles use --text-2 instead.
//   * AN ACCENT BAR THAT IS NOT THERE. The 3px bar is the family's strongest
//     signal and a UI boundary under WCAG 1.4.11, so it owes 3:1 against the
//     tile it sits on — the same rule --gold-line exists to satisfy.
//   * TWO FAMILIES NOBODY CAN TELL APART. If money and stock look alike the
//     colours have cost work and bought nothing.
//   * A RESTING TILE THAT LOOKS LIKE AN ALARM. --warn means *act on this*. A
//     decorative family close to it puts the attention colour on tiles at rest,
//     and the signal stops meaning anything.
//
// Values are read out of styles.css, not copied here — a test holding its own
// copy of the palette passes happily while the app ships something else.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(HERE, '..', 'src', 'styles.css'), 'utf8')
// The shop keeps its own copy of these tokens — it is a separate Flask app with
// its own stylesheet, and it says so at the top of that file. Two copies of a
// palette is exactly the kind of thing that rots quietly, so both are read here
// and compared, the same way test_warehouse_sync.py holds the shop's rebuilt QR
// payload against the warehouse's own.
const SHOP_CSS_PATH = join(HERE, '..', '..', 'Textile Retail Shop', 'app', 'static', 'css', 'app.css')
const SHOP_CSS = readFileSync(SHOP_CSS_PATH, 'utf8')

const from = (css, where) => (name) => {
  const m = css.match(new RegExp(`--${name}\\s*:\\s*(#[0-9A-Fa-f]{6})`))
  if (!m) throw new Error(`${where} has no --${name}`)
  return m[1].toUpperCase()
}
const token = from(CSS, 'styles.css')
const shopToken = from(SHOP_CSS, "the shop's app.css")

// ---- colour maths -----------------------------------------------------------
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16))
const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4 }
const lum = (h) => { const [r, g, b] = hex(h); return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b) }
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }
const fl = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (t * 841) / 108 + 4 / 29)
function lab(h) {
  const [r, g, b] = hex(h).map(lin)
  const X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
  const Y = 0.2126 * r + 0.7152 * g + 0.0722 * b
  const Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
  return [116 * fl(Y) - 16, 500 * (fl(X) - fl(Y)), 200 * (fl(Y) - fl(Z))]
}
const dE = (a, b) => Math.hypot(...lab(a).map((v, i) => v - lab(b)[i]))

// ---- what the app actually ships -------------------------------------------
const FAMILIES = ['count', 'stock', 'move', 'money', 'back', 'adjust']
const fam = Object.fromEntries(FAMILIES.map((f) => [f, {
  bg: token(`tile-${f}-bg`), line: token(`tile-${f}-line`),
  bar: token(`tile-${f}-bar`), val: token(`tile-${f}-val`),
  hover: token(`tile-${f}-hover`),
}]))
const PAGE = token('bg')
const PLAIN = token('panel-2')       // the untinted tile
const TEXT2 = token('text-2')        // the label on a tinted tile
const MUTED = token('muted')
const WARN_BG = token('warn-bg')

let bad = 0
const need = (label, got, min, unit = '') => {
  const ok = got >= min
  if (!ok) bad++
  console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${label.padEnd(40)} ${got.toFixed(2)}${unit} (need ${min})`)
}

console.log('every tinted tile can be read')
for (const [name, c] of Object.entries(fam)) {
  need(`${name}: value on its tint`, ratio(c.val, c.bg), 4.5)
  need(`${name}: label/sub (--text-2) on its tint`, ratio(TEXT2, c.bg), 4.5)
  need(`${name}: value on its HOVER tint`, ratio(c.val, c.hover), 4.5)
  need(`${name}: label on its HOVER tint`, ratio(TEXT2, c.hover), 4.5)
}

console.log('\nthe accent bar is a UI boundary, not decoration (WCAG 1.4.11)')
for (const [name, c] of Object.entries(fam)) {
  need(`${name}: bar on its tint`, ratio(c.bar, c.bg), 3.0)
  need(`${name}: bar on its hover tint`, ratio(c.bar, c.hover), 3.0)
}

console.log('\nthe families are told apart by their bars (ΔE)')
for (let i = 0; i < FAMILIES.length; i++)
  for (let j = i + 1; j < FAMILIES.length; j++)
    need(`${FAMILIES[i]} vs ${FAMILIES[j]}`, dE(fam[FAMILIES[i]].bar, fam[FAMILIES[j]].bar), 20)

console.log('\ntheir tints only have to be perceptibly apart (ΔE)')
for (let i = 0; i < FAMILIES.length; i++)
  for (let j = i + 1; j < FAMILIES.length; j++)
    need(`${FAMILIES[i]} vs ${FAMILIES[j]}`, dE(fam[FAMILIES[i]].bg, fam[FAMILIES[j]].bg), 3)

console.log('\nno tile at rest may look like one that needs someone (ΔE from --warn-bg)')
for (const [name, c] of Object.entries(fam)) need(`${name}`, dE(c.bg, WARN_BG), 8)

console.log('\nand a coloured tile reads as coloured')
for (const [name, c] of Object.entries(fam)) {
  need(`${name}: against the page`, dE(c.bg, PAGE), 3)
  need(`${name}: against an untinted tile`, dE(c.bg, PLAIN), 3)
  need(`${name}: hover is a visible change`, dE(c.bg, c.hover), 1.5)
}

// The reason tinted tiles switch their label colour at all. If this ever passes,
// the switch to --text-2 has stopped being necessary and the rule can go.
console.log('\nwhy tinted tiles do not use --muted for the label')
let mutedFails = 0
for (const [name, c] of Object.entries(fam)) {
  const r = ratio(MUTED, c.bg)
  if (r < 4.5) mutedFails++
  console.log(`  ${r < 4.5 ? 'as expected' : 'NOTE       '} --muted on ${name}: ${r.toFixed(2)}`)
}
if (mutedFails === 0) {
  console.log('  NOTE: --muted now passes on every tint — the --text-2 switch could be dropped.')
}

// The CSS has to actually wire each family up, or the tokens are decoration.
console.log('\nevery family is wired to a class, and warn still overrules them')
for (const f of FAMILIES) {
  const wired = new RegExp(`\\.dtile\\.t-${f}\\b`).test(CSS)
  need(`.dtile.t-${f} exists`, wired ? 1 : 0, 1)
}
const warnAt = CSS.indexOf('.dtile.warn {')
const lastFamilyAt = Math.max(...FAMILIES.map((f) => CSS.indexOf(`.dtile.t-${f} {`)))
need('.dtile.warn is ordered after the families', warnAt > lastFamilyAt ? 1 : 0, 1)
need('.dtile.ok is styled at all', /\.dtile\.ok\s*\{/.test(CSS) ? 1 : 0, 1)

// The inline strip and the shop's cards carry the same families, or a figure
// changes colour depending on which screen it is shown on.
console.log('\nthe other two card systems carry the same families')
for (const f of FAMILIES) {
  need(`.stat.t-${f} exists`, new RegExp(`\\.stat\\.t-${f}\\b`).test(CSS) ? 1 : 0, 1)
  need(`shop .stat-card.t-${f} exists`,
    new RegExp(`\\.stat-card\\.t-${f}\\b`).test(SHOP_CSS) ? 1 : 0, 1)
}

console.log("\nand the shop's copy of the palette has not drifted")
for (const f of FAMILIES)
  for (const part of ['bg', 'line', 'bar', 'val', 'hover']) {
    const name = `tile-${f}-${part}`
    const same = token(name) === shopToken(name)
    if (!same) {
      bad++
      console.log(`  FAIL --${name}: styles.css ${token(name)} vs shop ${shopToken(name)}`)
    }
  }
if (!bad) console.log('  ok   all 30 values identical in both stylesheets')

console.log('\n' + '='.repeat(64))
if (bad) { console.log(`${bad} FAILING`); process.exit(1) }
console.log('all tile palette checks passing')
