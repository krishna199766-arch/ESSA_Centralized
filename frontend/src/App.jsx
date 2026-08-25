import React, { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { api, session, setUnauthorizedHandler } from './api.js'
import { parseDictation, coerceSpoken, dictationTargets } from './voicefill.js'

// ---------- helpers ----------
const num = (v) => (v === '' || v == null ? null : isNaN(+v) ? v : +v)
const money = (v) => (v == null || v === '' ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const confClass = (c) => (c == null ? '' : c >= 0.9 ? 'hi' : c >= 0.6 ? 'mid' : 'lo')
// Steps a headline figure down one size once it stops fitting on a single line.
// A rupee total is three times the width of a count, and at the headline size
// "₹ 1,03,389.00" broke across two lines mid-number — which reads as a mistake
// and, worse, made that one tile taller than every other tile in its row.
// Counted in characters because no CSS length unit knows how many digits a
// number has.
const longValue = (v) => String(v ?? '').length >= 12

// text filter helpers
const matches = (obj, q, fields) => {
  if (!q) return true
  const s = q.toLowerCase()
  return fields.some((f) => String(obj?.[f] ?? '').toLowerCase().includes(s))
}
// ==========================================================================
//  Search · Filter · Minimize
//  ------------------------------------------------------------------------
//  Three controls, one behaviour, on every list screen — GRN, Inventory,
//  Purchase Return, Stock Outward, Stock Inward, Documents, Suppliers, LR and
//  Reports. Someone who learns them on one screen already knows them on the
//  rest, which is the whole point: this is a warehouse tool used by people who
//  were trained on the screen next to them, not on a manual.
//
//  The rules they all keep:
//    * ⌕ SEARCH filters what is already on screen. Esc clears it.
//    * ⛭ FILTER decides what is on screen at all, and always says how many
//      filters are active — a filtered list that looks unfiltered is how someone
//      concludes stock has gone missing.
//    * − MINIMIZE collapses a panel and remembers it, per screen, across
//      navigation and reloads. A collapsed panel still shows what it holds.
//  Every icon carries a title= too. An icon on its own teaches nobody.
// ==========================================================================

// ==========================================================================
//  Pagination
//  ------------------------------------------------------------------------
//  Every list in this app grows without limit — a register gains rows every
//  day, inventory gains a product per variant received, the category master
//  already holds 686. Rendering all of them put thousands of DOM nodes on a
//  screen nobody can read, and "how many are there" could only be answered by
//  scrolling to the bottom.
//
//  One hook and one control, used everywhere, so paging behaves identically
//  whichever list you are on: the same page sizes, the same "1–50 of 686", the
//  same keys. A component that each screen implemented its own way would drift
//  into six subtly different behaviours.
// ==========================================================================
const PAGE_SIZES = [25, 50, 100, 200]

function usePaged(rows, initial = 50) {
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(initial)
  const list = rows || []
  const total = list.length
  const pages = size === 0 ? 1 : Math.max(1, Math.ceil(total / size))
  // A filter that shortens the list must not strand you on page 9 of 3 looking
  // at an empty screen — which reads as "the search found nothing".
  useEffect(() => { if (page > pages) setPage(1) }, [pages, page])
  const start = size === 0 ? 0 : (page - 1) * size
  return {
    page, setPage, size, setSize, total, pages,
    from: total === 0 ? 0 : start + 1,
    to: size === 0 ? total : Math.min(total, start + size),
    slice: size === 0 ? list : list.slice(start, start + size),
  }
}

function Pager({ page, setPage, size, setSize, total, pages, from, to,
                 noun = 'row', nouns, style }) {
  // Nothing to page through, and no page-size choice worth offering: a bar under
  // a list of four is noise. The screens carry their own counts already.
  if (total <= PAGE_SIZES[0]) return null
  const go = (p) => setPage(Math.min(pages, Math.max(1, p)))
  return (
    <div className="pager" style={style}>
      <span className="pager-count">
        <b>{from.toLocaleString('en-IN')}–{to.toLocaleString('en-IN')}</b>
        {' of '}<b>{total.toLocaleString('en-IN')}</b>{' '}
        {total === 1 ? noun : (nouns || noun + 's')}
      </span>
      <label className="pager-size">
        Show
        <select value={size} onChange={(e) => { setSize(+e.target.value); setPage(1) }}>
          {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          <option value={0}>All</option>
        </select>
      </label>
      <div className="pager-nav">
        <button className="btn" disabled={page <= 1} onClick={() => go(1)} title="First page">«</button>
        <button className="btn" disabled={page <= 1} onClick={() => go(page - 1)}>‹ Previous</button>
        <span className="pager-page">Page {page} of {pages}</span>
        <button className="btn" disabled={page >= pages} onClick={() => go(page + 1)}>Next ›</button>
        <button className="btn" disabled={page >= pages} onClick={() => go(pages)} title="Last page">»</button>
      </div>
    </div>
  )
}

function SearchBox({ value, onChange, placeholder, style, title }) {
  return (
    <div className="searchbox" style={style}
      title={title || 'Search within what is shown. Esc clears it.'}>
      <span className="searchicon">⌕</span>
      <input value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder || 'Search…'}
        onKeyDown={(e) => { if (e.key === 'Escape' && value) { e.preventDefault(); onChange('') } }} />
      {value && <button className="searchclear" title="Clear the search (Esc)"
        onClick={() => onChange('')}>×</button>}
    </div>
  )
}

// Mutually exclusive scopes — the common filter, and the one worth showing
// open rather than behind a button. `options`: [value, label, count?, tooltip?]
function FilterChips({ value, onChange, options, title }) {
  return (
    <div className="chiprow" title={title || 'Filter which records are listed'}>
      {options.map(([v, label, count, tip]) => (
        <button key={v} className={'fchip' + (value === v ? ' on' : '')}
          title={tip || `Show ${String(label).toLowerCase()}`}
          onClick={() => onChange(v)}>
          {label}{count != null && <span className="n">{count}</span>}
        </button>
      ))}
    </div>
  )
}

// Anything richer than a chip row lives behind this: a labelled disclosure that
// states how many filters are on, and can always be cleared in one click.
function FilterButton({ open, onToggle, active, title }) {
  return (
    <button className={'filterbtn' + (open || active ? ' on' : '')} onClick={onToggle}
      title={title || (active
        ? `${active} filter${active === 1 ? '' : 's'} active — click to change or clear`
        : 'Filter which records are listed')}>
      <span aria-hidden="true">⛭</span> Filters
      {active > 0 && <span className="count">{active}</span>}
      <span style={{ color: 'var(--muted)' }}>{open ? '▾' : '▸'}</span>
    </button>
  )
}

function FilterPanel({ open, active, onClear, onApply, children, hint }) {
  if (!open) return null
  return (
    <div className="filterpanel">
      <div className="row">{children}</div>
      <div className="filterfoot">
        <span className="small" style={{ color: 'var(--muted)' }}>
          {hint || (active ? `${active} filter${active === 1 ? '' : 's'} active` : 'No filters set — everything is listed')}
        </span>
        <div style={{ flex: 1 }} />
        {onApply && <button className="btn primary" onClick={onApply} title="Run with these filters">Apply</button>}
        <button className="btn" onClick={onClear} disabled={!active}
          title={active ? 'Remove every filter' : 'Nothing to clear'}>Clear all</button>
      </div>
    </div>
  )
}

// Collapsed panels are remembered per screen and survive a reload, because a
// warehouse screen is set up once for how someone works and then left alone.
const MINI_KEY = 'essa_minimized'
const readMinimized = () => {
  try { return JSON.parse(localStorage.getItem(MINI_KEY) || '{}') } catch { return {} }
}
function useMinimized(id, defaultOpen = true) {
  const [open, setOpen] = useState(() => {
    const saved = readMinimized()[id]
    return saved === undefined ? defaultOpen : !saved
  })
  const toggle = () => setOpen((o) => {
    const next = !o
    try {
      const all = readMinimized()
      if (next === defaultOpen) delete all[id]; else all[id] = !next
      localStorage.setItem(MINI_KEY, JSON.stringify(all))
    } catch { /* private mode — the panel still toggles, it just won't persist */ }
    return next
  })
  return [open, toggle]
}

// The list down the left of most screens, with a collapse.
//
// Same three rules the panels keep: it slides away, it is remembered per screen
// across navigation and reloads, and collapsed it still says what it holds — the
// title and the count stay legible down the rail. A list that vanishes with no
// trace of itself is a missing feature, not a hidden one, and the way back has to
// be visible from where it went.
//
// The content stays mounted and is clipped rather than unmounted, so the width
// genuinely animates instead of the panel popping between two states. It is
// `visibility: hidden` while collapsed, which also takes it out of the tab order —
// a keyboard user should not travel through a list they cannot see.
function Sidebar({ id, label, children, width }) {
  const [open, toggle] = useMinimized('side.' + id, true)
  return (
    <div className={'sidebar' + (open ? '' : ' collapsed')}
      style={width && open ? { width, minWidth: width } : undefined}>
      {open ? (
        <button className="sidehide" onClick={toggle}
          title={`Hide the ${label.toLowerCase()} list — the screen keeps this setting`}>«</button>
      ) : (
        <button className="siderail" onClick={toggle} title={`Show the ${label.toLowerCase()} list`}>
          <span className="chev" aria-hidden="true">»</span>
          <span className="raillabel">{label}</span>
        </button>
      )}
      <div className="sidebody" style={width ? { width, minWidth: width } : undefined}>{children}</div>
    </div>
  )
}

// A section with a minimize control. `summary` is what it says while collapsed —
// a minimized panel that doesn't say what it holds is just a missing panel.
function Section({ id, title, summary, actions, children, defaultOpen = true, style }) {
  const [open, toggle] = useMinimized(id, defaultOpen)
  return (
    <div className="section" style={style}>
      <div className="panelhead" onClick={toggle}
        title={open ? 'Minimize this panel' : 'Expand this panel'}>
        <button className="mini" aria-expanded={open}
          title={open ? 'Minimize' : 'Expand'} onClick={(e) => { e.stopPropagation(); toggle() }}>
          {open ? '−' : '+'}
        </button>
        <h4>{title}</h4>
        {!open && summary ? <span className="panelsum">{summary}</span> : null}
        {actions && open ? <span onClick={(e) => e.stopPropagation()}>{actions}</span> : null}
      </div>
      {open && children}
    </div>
  )
}

// The ESSA-AI logo mark — the letters "AI" (rotating during the login intro)
function AiLogo({ size = 120 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="lg" x1="0" y1="0" x2="120" y2="120">
          <stop offset="0" stopColor="#7A4C3D" /><stop offset="1" stopColor="#5A3428" />
        </linearGradient>
      </defs>
      {/* gradient ring + a rotating orbit dot for flair */}
      <circle cx="60" cy="60" r="54" stroke="url(#lg)" strokeWidth="2.5" opacity="0.3" />
      <circle cx="60" cy="60" r="54" stroke="url(#lg)" strokeWidth="2.5" strokeDasharray="70 250" strokeLinecap="round" />
      <circle cx="114" cy="60" r="3.5" fill="url(#lg)" />
      {/* the letters A I */}
      <text x="60" y="63" textAnchor="middle" dominantBaseline="central"
        fontFamily="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
        fontSize="52" fontWeight="800" letterSpacing="2" fill="url(#lg)">AI</text>
    </svg>
  )
}

function LoginScreen({ onLogin }) {
  const [phase, setPhase] = useState('intro')   // intro (logo spins) -> form (smoke reveal)
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { const t = setTimeout(() => setPhase('form'), 2200); return () => clearTimeout(t) }, [])

  const submit = async (e) => {
    e.preventDefault(); setErr(''); setBusy(true)
    try {
      const r = await api.login(username.trim(), password)
      onLogin(r.token, r.user, r.role, r.permissions)
    } catch (e) { setErr(e.detail || 'Login failed'); setBusy(false) }
  }

  return (
    <div className="login-wrap">
      <div className="login-bg" />
      <div className={'login-logo ' + phase}><AiLogo size={130} /></div>
      {phase === 'form' && <>
        {[0, 1, 2, 3, 4].map((i) => <div key={i} className={'smoke smoke-' + i} />)}
        <form className="login-card" onSubmit={submit}>
          <div className="login-brand">ESSA <span>·</span> AI</div>
          <div className="login-sub">Document Intelligence · sign in to continue</div>
          <div className="field"><label>Username</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus /></div>
          <div className="field"><label>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••" /></div>
          {err && <div className="login-err">{err}</div>}
          <button className="btn primary" style={{ width: '100%', marginTop: 6 }} disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
      </>}
    </div>
  )
}

// ---------- dates ----------
// Every date in this app is a calendar picker, and every date it stores is ISO
// `YYYY-MM-DD`. The picker is the easy half; the hard half is that dates already
// in the database came off supplier invoices and register pages in whatever form
// those used — 31/07/2026, 31-7-26, "31 Jul 2026". A native <input type="date">
// shows a non-ISO value as BLANK, so pointing one at that data would look exactly
// like the date had been lost, and the first save would make it true.
//
// So a value is normalised on the way in, and one that cannot be read is not
// silently swallowed: the field stays empty (it has nothing valid to show) but the
// original text is displayed beneath it, and it is left in the record untouched
// until someone actually picks a replacement.
const ISO_RE = /^\d{4}-\d{2}-\d{2}$/
const D_FIRST = [/^(\d{1,2})[/\-. ](\d{1,2})[/\-. ](\d{4})$/, /^(\d{1,2})[/\-. ](\d{1,2})[/\-. ](\d{2})$/]
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
const pad2 = (n) => String(n).padStart(2, '0')

// A year no purchase ledger will carry. A two-digit year and a mis-OCR'd digit
// both produce dates that parse perfectly and are nonsense; the same guard is in
// services/dates.py, and the two must agree or the screen and the server disagree
// about whether a value is a date at all.
// Round-tripped through Date, not merely range-checked: 2026-02-30 passes every
// bounds test and is not a day. JS rolls it forward to March 2, so a value that
// does not come back as itself was never a date — the same answer strptime gives
// on the server, which is what keeps the two in step.
const saneDate = (y, m, d) => {
  if (+y < 1990 || +y > 2099) return ''
  const iso = `${y}-${pad2(m)}-${pad2(d)}`
  const t = new Date(iso + 'T00:00:00Z')
  return !isNaN(t) && t.toISOString().slice(0, 10) === iso ? iso : ''
}

// Mirrors services/dates.py — day-first, because every supplier and register in
// this business writes 03/04/2026 for the 3rd of April.
function toISODate(value) {
  if (!value) return ''
  const s = String(value).trim()
  const ymd = s.match(/^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$/)
  if (ISO_RE.test(s) || ymd) {
    const [, y, m, d] = ymd || s.match(/^(\d{4})-(\d{2})-(\d{2})$/)
    return saneDate(y, m, d)
  }
  for (const re of D_FIRST) {
    const m = s.match(re)
    if (!m) continue
    let [, d, mo, y] = m
    if (y.length === 2) y = String(2000 + +y)
    const iso = saneDate(y, mo, d)
    if (iso) return iso
    // day-first didn't hold, so this can only be month-first — 04/13/2026 off a
    // supplier running US-defaulted software. Same fallback as services/dates.py.
    const swapped = saneDate(y, d, mo)
    if (swapped) return swapped
  }
  // "31 Jul 2026" / "31-Jul-2026", off e-invoices
  const named = s.match(/^(\d{1,2})[\s\-/]([A-Za-z]{3,})[\s\-/](\d{2,4})$/)
  if (named) {
    const mi = MONTHS.indexOf(named[2].slice(0, 3).toLowerCase())
    const y = named[3].length === 2 ? String(2000 + +named[3]) : named[3]
    if (mi >= 0) return saneDate(y, mi + 1, named[1])
  }
  return ''
}

// ---------- how a date is READ ----------
// Stored ISO, shown DD-MM-YYYY, and those are two separate decisions rather than
// one inconsistency. ISO is what a date COLUMN must hold: `date_from`/`date_to`
// compare as plain text in SQL, and only ISO sorts the way a calendar does.
// DD-MM-YYYY is what this business WRITES — on every register page, supplier
// invoice, cheque and delivery note in the building — so it is what every screen
// shows. Nothing renders a raw `2026-07-31` any more: one house format means
// nobody has to work out which of two numbers is the month.
const DISPLAY_SEP = '-'

const readableDate = (value) => {
  const s = toISODate(value)
  if (!s) return ''
  const [y, m, d] = s.split('-')
  return [d, m, y].join(DISPLAY_SEP)
}

//: `2026-07-31T09:14:22` — a stored timestamp is a date with a time bolted on
const ISO_STAMP_RE = /^(\d{4}-\d{2}-\d{2})[T ]/

// The form used at every display site. Hands back DD-MM-YYYY for anything that
// is a date, `blank` for nothing at all, and the value UNTOUCHED for anything
// else — a register cell may hold text vision could not read, and blanking it
// on screen would look exactly like the date had been lost.
const fmtDate = (value, blank = '—') => {
  if (value == null || value === '') return blank
  const s = String(value)
  return readableDate(s) || readableDate((s.match(ISO_STAMP_RE) || [])[1]) || s
}

// A date hiding inside a value that could be anything — a report cell, a CSV
// field, a filter chip. Deliberately narrower than fmtDate, which is only ever
// pointed at a column already known to hold dates: here nothing is known about
// the value, so ONLY a full ISO date is rewritten. `10-12-24` off a size run or
// a bin code parses perfectly as a date and is not one, and silently reprinting
// it as `10-12-2024` in a report is a wrong figure nobody would think to check.
// The server's dates.display_cell draws the same line, so a CSV downloaded from
// the browser and one downloaded from the API agree.
const ISO_CELL_RE = /^\d{4}-\d{2}-\d{2}(?:[T ].*)?$/
const fmtLoose = (v) => (typeof v === 'string' && ISO_CELL_RE.test(v.trim())
  ? (readableDate(v.trim().slice(0, 10)) || v) : v)

// ---------- picking one ----------
// The box reads DD-MM-YYYY and the calendar behind it is one click away. A bare
// <input type="date"> is deliberately not used for the reading half: what it
// PRINTS is chosen by the browser's locale, so the same record shows 31/07/2026
// on one machine, 07/31/2026 on the next and 2026-07-31 on a third — none of
// them this app's format, and none of them under its control. So the value is
// shown in a text box the app does own, anything typed into it goes through the
// same day-first parser the rest of the system uses, and the native picker is
// kept for anyone who would rather point at a calendar than type.
function DateField({ label, value, onChange, style, width, required, title, inline }) {
  const iso = toISODate(value)
  const unreadable = value && !iso
  const picker = useRef(null)
  const [text, setText] = useState(() => readableDate(iso))
  // Follow the value when something OUTSIDE the box changes it — a form reset, a
  // row filling itself in from a matched invoice. Keyed on the ISO value, so it
  // cannot fire mid-typing and snatch back half a date.
  useEffect(() => { setText(readableDate(iso)) }, [iso])
  const commit = () => {
    const s = text.trim()
    if (!s) { setText(''); if (iso) onChange(''); return }
    const next = toISODate(s)
    // Unreadable: put back what the field actually holds. Leaving half-typed
    // text sitting there would look saved, and isn't.
    if (!next) { setText(readableDate(iso)); return }
    setText(readableDate(next))
    if (next !== iso) onChange(next)
  }
  const input = (
    <>
      <span className="datebox">
        <input className="datetext" value={text} placeholder="dd-mm-yyyy" inputMode="numeric"
          title={title || 'Type dd-mm-yyyy, or pick from the calendar'}
          onChange={(e) => setText(e.target.value)} onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} />
        {/* Kept rendered rather than display:none — a hidden input cannot open
            its own picker — but covered by the button and out of the tab order,
            so it is never the thing anyone types into. */}
        <input ref={picker} type="date" className="datepick" tabIndex={-1} aria-hidden="true"
          value={iso} onChange={(e) => onChange(e.target.value)} />
        <button type="button" className="datebtn" tabIndex={-1} title="Pick from a calendar"
          onClick={() => {
            const el = picker.current
            if (!el) return
            try { el.showPicker() } catch { el.focus() }
          }}>📅</button>
      </span>
      {unreadable && (
        <div className="flagnote" title="Kept exactly as it was read — pick a date to replace it">
          ⚠ can’t read “{String(value)}” — kept as-is
        </div>
      )}
    </>
  )
  if (inline) return input
  return (
    <div className="field" style={{ ...(width ? { width } : null), ...style }}>
      <label>{label}{required ? ' *' : ''}</label>
      {input}
    </div>
  )
}

// `calc` marks a field the arithmetic keeps in step — tinted, with an ƒ and a
// tooltip saying what it comes from. It stays editable: typing into it makes it
// the input and its counterpart moves instead.
function Field({ label, value, onChange, flagged, note, wide, source, date, calc }) {
  return (
    <div className={'field' + (flagged ? ' flag' : '') + (source && !flagged ? ' fromlr' : '')
      + (calc ? ' calc' : '')}
      style={wide ? { gridColumn: '1 / -1' } : null}>
      <label title={calc || undefined}>{label}{calc ? ' ƒ' : ''}</label>
      {date
        ? <DateField inline value={value} onChange={onChange} />
        : <input value={value ?? ''} title={calc || undefined}
            onChange={(e) => onChange(e.target.value)} />}
      {flagged && <div className="flagnote">⚠ needs review{note ? ' · ' + note : ''}</div>}
      {!flagged && note && <div className="srcnote">{note}</div>}
      {source && !flagged && <div className="srcnote">🔗 from {source}</div>}
    </div>
  )
}

// ==========================================================================
//  Auto-calculation — the arithmetic an invoice already contains
//  ------------------------------------------------------------------------
//  An invoice's figures are not independent. MRP less a discount IS the rate;
//  qty times rate IS the amount; a tax rate over the taxable value IS the tax
//  amount. Typing all of them and hoping they agree is how a review screen
//  produces a document that reconciles against nothing.
//
//  ONE RULE decides everything below: **the field you just edited is the input,
//  and only its dependents move.** Type 5 into IGST % and the amount appears;
//  type 933 into IGST Amount and the % appears. Neither overwrites the other,
//  because at any moment exactly one of them is the thing a human just asserted.
//
//  And nothing recalculates on LOAD. This screen exists to review what was read
//  off a photograph; recomputing on arrival would overwrite the extraction with
//  its own idea of itself and hide precisely the disagreements the warnings
//  panel is there to show. Everything here fires on an edit, or on the explicit
//  Recalculate button.
// ==========================================================================
const nf = (v) => {
  if (v === '' || v == null) return null
  const x = parseFloat(String(v).replace(/,/g, ''))
  return Number.isFinite(x) ? x : null
}
const r2 = (v) => (v == null ? null : Math.round(v * 100) / 100)
// quantities are floats — rounded, and compared, the way the server posts them
const round3 = (n) => Math.round((+n || 0) * 1000) / 1000

// Fields a human can type on a line, and what each one drives. `amount` and
// `taxable_value` are outputs of the row above them but inputs when typed
// directly — a supplier who bills a flat line amount is stating the amount, and
// the rate is then whatever divides into it.
// `prev` is the row as it stood BEFORE this edit. It is needed for exactly one
// judgement — whether Taxable was tracking Amount or had been set apart by hand —
// and that can only be told from the values that were there a moment ago.
// MRP less a percentage is a price. ONE relationship, and this business runs it
// twice — on opposite sides of itself:
//
//   MRP − discount % = RATE          what we pay the supplier (the invoice)
//   MRP − discount % = SALE PRICE    what the shop charges  (the price tag)
//
// The two are never the same number and must never be confused (see
// models.Product.sale_discount_pct, which exists precisely because the purchase
// discount had already taken the plain name). The arithmetic behind them IS the
// same though, so it is solved once, here, for whichever of the three corners
// was not just typed. `edited` is 'pct' | 'price' | 'mrp'.
function fromMrp(mrp, pct, price, edited) {
  if (!mrp) return [pct, price]
  if (edited === 'pct') return pct == null ? [pct, price] : [pct, r2(mrp * (1 - pct / 100))]
  if (edited === 'price') return price == null ? [pct, price] : [r2((1 - price / mrp) * 100), price]
  if (edited === 'mrp') {
    // a corrected MRP keeps the percentage that was agreed and re-prices; with no
    // percentage recorded it is the percentage that is unknown, not the price
    if (pct != null) return [pct, r2(mrp * (1 - pct / 100))]
    if (price != null) return [r2((1 - price / mrp) * 100), price]
  }
  return [pct, price]
}

//: the fields that describe what ONE piece costs. Only an edit to one of these
//: can move the discount — correcting a description or an HSN must not make a
//: discount appear on a line that never had one.
const PRICE_KEYS = new Set(['mrp', 'discount_pct', 'discount_amount', 'rate',
                            'buffer_pct', 'mrp_buffer_pct', 'amount'])

//: the edits that re-run the upward chain (cost → sell → MRP). `sale_price` is
//: in it so typing a shelf price back-solves the buffer above the cost, and
//: `mrp` is NOT: correcting a printed tag must not re-price the goods.
const CHAIN_KEYS = new Set(['rate', 'buffer_pct', 'mrp_buffer_pct', 'sale_price', 'amount'])

// ---- the gap between cost and MRP, named from both ends ----
//
// MRP and Rate are two numbers with one gap between them, and the trade states
// that gap two different ways depending on which end it is standing at:
//
//   * **Discount %** — off the printed price. (MRP − Rate) ÷ MRP. This is what a
//     supplier's bill says, because the bill starts from the tag.
//   * **Buffer %** — on top of cost. (MRP − Rate) ÷ Rate. This is what you say
//     when there is no tag yet: the goods cost 650, put 53% on it and print 995.
//
// They are never the same number for the same gap (345 off 995 is 34.7%; 345 on
// 650 is 53.1%), which is exactly why both are offered rather than one being
// made to serve as the other. Either can be typed; the other follows, and so
// does the price at the far end.
//
// Buffer is DERIVED, never stored: it is recomputed from Rate and MRP whenever
// either moves, so a line reloaded from a saved invoice shows the same buffer it
// was entered at without a column existing to go stale.
function bufferFrom(top, base) {
  if (top == null || !base) return null
  return r2((top / base - 1) * 100)
}
function markup(base, pct) {
  if (base == null || pct == null) return null
  // A WHOLE rupee, not two decimals. These are printed prices — 448 and 560,
  // never 447.99 — and the percentage behind them is a round figure somebody
  // chose, so the product always carries paise no tag would show. Rounding here
  // is what makes the chain produce prices that can go on a label, and it
  // closes the round trip: 320 + 40% → 448, and 448 against 320 reads back as
  // 40%.
  return Math.round(base * (1 + pct / 100))
}
function unmarkup(top, pct) {
  if (top == null || pct == null || pct <= -100) return null
  return r2(top / (1 + pct / 100))
}

// The SELLING side of the triangle above: MRP − Discount % = Sale price, in
// whichever direction was typed. Used on the GRN breakdown, where retail pricing
// is set per variant — a shop's price belongs to "L / Red", not to the bundle
// the supplier billed.
//
// Values in and out are the strings a form holds, blanks included: clearing the
// discount empties that box and leaves the price alone, rather than resolving to
// zero and marking the goods down to nothing.
const SALE_KEYS = { mrp: 'mrp', sale_discount_pct: 'pct', sale_price: 'price' }

// `blank` is what an emptied box becomes: '' for the form-shaped rows of the GRN
// breakdown, null for the invoice's line items, which hold numbers.
function recalcSale(row, edited, blank = '') {
  const corner = SALE_KEYS[edited]
  if (!corner) return row
  const [pct, price] = fromMrp(nf(row.mrp), nf(row.sale_discount_pct),
                               nf(row.sale_price), corner)
  return { ...row, sale_discount_pct: pct ?? blank, sale_price: price ?? blank }
}

function recalcLine(row, edited, prev = row) {
  const L = { ...row }
  const qty = nf(L.qty)
  let mrp = nf(L.mrp)
  let rate = nf(L.rate), disc = nf(L.discount_pct), amount = nf(L.amount)
  const discAmt = nf(L.discount_amount)
  const buffer = nf(L.buffer_pct)

  // --- MRP ─ discount ─ rate, in whichever direction was just typed ---
  //
  // All of it PER PIECE, because MRP and Rate are per piece and a discount
  // between them that quietly switched to a line total could not be checked
  // against either. 995 − 650 = 345 is a sum anyone can do against the invoice;
  // 4140 is one nobody can.
  if (edited === 'buffer_pct' || edited === 'mrp_buffer_pct') {
    // Handled by the markup chain below — both percentages build prices
    // upwards from the cost, so neither belongs in this MRP-down block.
  } else if (edited === 'discount_amount' && mrp != null && discAmt != null) {
    // the one corner fromMrp does not carry: rupees off rather than percent off
    rate = r2(mrp - discAmt)
    if (mrp) disc = r2((discAmt / mrp) * 100)
  } else if (edited === 'discount_pct' || edited === 'rate' || edited === 'mrp') {
    [disc, rate] = fromMrp(mrp, disc, rate,
      edited === 'discount_pct' ? 'pct' : edited === 'rate' ? 'price' : 'mrp')
  }

  // --- qty × rate = amount, or amount ÷ qty = rate when the amount was typed ---
  if (edited === 'amount' && qty) {
    rate = r2(amount / qty)
    if (mrp) disc = r2((1 - rate / mrp) * 100)
  } else if (qty != null && rate != null) {
    amount = r2(qty * rate)
  }

  // --- the markup chain: cost → sell price → MRP ------------------------
  //
  //     rate 320  ─ +40% ─►  sell 448  ─ +25% ─►  MRP 560
  //
  // Prices are built UPWARDS from what the goods cost, which is the direction
  // the shop actually prices in: the cost is the known number and both the
  // shelf price and the printed tag are decisions taken on top of it. The two
  // percentages are different questions — what to charge, and how much room to
  // leave above it for a visible discount — so they are two columns.
  //
  // The MRP-down block above is untouched and still runs for a bill that STATES
  // its retail price and a discount off it; this is the other half, for goods
  // that arrive with no tag.
  const mrpBuffer = nf(L.mrp_buffer_pct)
  let sell = nf(L.sale_price)

  if (CHAIN_KEYS.has(edited)) {
    // A sell price that was TYPED stands — it is the decision, and the buffer
    // above the cost is what follows from it (restated at the foot of this
    // function). Recomputing it from the cost here would overwrite the number
    // somebody just entered with the one it used to be.
    if (edited === 'sale_price') {
      if (rate == null && buffer != null) rate = unmarkup(sell, buffer)
    } else if (buffer != null && rate != null) sell = markup(rate, buffer)
    // …and its mirror, for a line priced from the shelf back down: the same
    // buffer says what the cost behind that price must have been.
    else if (buffer != null && sell != null && rate == null) rate = unmarkup(sell, buffer)

    // An MRP the invoice PRINTED is what the supplier billed and what the tag
    // says, so it stands. The chain fills it only where there is none — except
    // when the MRP buffer is what was just typed, which is somebody saying
    // plainly what the tag should be.
    if (mrpBuffer != null && sell != null && (mrp == null || edited === 'mrp_buffer_pct')) {
      mrp = markup(sell, mrpBuffer)
    }
    // Both discounts restated from wherever the prices landed. They are the
    // same gaps read from the top: what the supplier gave off the tag, and what
    // the shop shows off it.
    if (mrp) {
      if (rate != null) disc = r2((1 - rate / mrp) * 100)
      if (sell != null) L.sale_discount_pct = r2((1 - sell / mrp) * 100)
    }
    L.sale_price = sell
  }

  L.rate = rate
  L.discount_pct = disc
  L.amount = amount
  L.mrp = mrp                     // moved only by the markup chain; see above
  // The discount in rupees, PER PIECE — the same base as the % beside it and as
  // the MRP and Rate it sits between. Touched only when one of those actually
  // moved: an edit to the description or the HSN has nothing to say about
  // pricing, and a discount that materialises out of one is a figure the invoice
  // never stated.
  if (PRICE_KEYS.has(edited) && mrp != null && rate != null) {
    L.discount_amount = r2(mrp - rate)
  }
  // The buffer, restated from wherever the two prices ended up. Skipped when the
  // buffer is what was typed — recomputing it from the MRP it just produced is a
  // round trip that only introduces rounding, and it would fight the cursor.
  // Each markup restated from the two prices it spans — buffer over the cost,
  // MRP buffer over the shelf price. Skipped for whichever was just typed:
  // recomputing it from the price it produced is a round trip that only adds
  // rounding, and it would fight the cursor.
  // CHAIN_KEYS as well as PRICE_KEYS: typing a SELL price moves the buffer above
  // the cost, and sale_price is deliberately not a PRICE_KEY — it says nothing
  // about what the supplier charged, so it must not stir the purchase discount.
  const movedPrices = PRICE_KEYS.has(edited) || CHAIN_KEYS.has(edited)
  if (movedPrices && edited !== 'buffer_pct') {
    L.buffer_pct = bufferFrom(L.sale_price, rate)
  }
  if (movedPrices && edited !== 'mrp_buffer_pct') {
    L.mrp_buffer_pct = bufferFrom(mrp, L.sale_price)
  }
  // The taxable value is the line amount unless the invoice states its own. It
  // keeps up while it merely echoes the amount; the moment someone types a
  // different figure into it, that is an assertion about the invoice and nothing
  // here overwrites it again.
  const wasTracking = nf(prev.taxable_value) == null
    || nf(prev.taxable_value) === nf(prev.amount)
  if (edited !== 'taxable_value' && wasTracking) L.taxable_value = amount
  // The SELLING triangle, hanging off the same MRP: MRP − Sale disc % = Sell
  // price. A correction to the MRP moves both sides of the line at once, which
  // is right — one printed retail price feeds what we pay AND what we charge —
  // while the two discounts stay strictly apart.
  return recalcSale(L, edited, null)
}

//: the taxable figure a rate is charged on — the invoice's own when it has one,
//: otherwise what the lines add up to
function taxBase(data) {
  const stated = nf(data.totals?.taxable_total)
  if (stated) return stated
  return r2((data.line_items || []).reduce(
    (s, it) => s + (nf(it.taxable_value) ?? nf(it.amount) ?? 0), 0))
}

//: every % ─ amount pair in the tax block. Both directions, always.
const TAX_PAIRS = [
  ['cgst_rate', 'cgst_amount'], ['sgst_rate', 'sgst_amount'],
  ['igst_rate', 'igst_amount'],
  // not a tax, but the same arithmetic and the same complaint: a special
  // discount typed as 979.25 should say what percentage it is, and one typed as
  // 5% should say what it costs
  ['special_discount_pct', 'special_discount'],
]

function recalcTaxes(tax, base, edited) {
  const t = { ...tax }
  for (const [rk, ak] of TAX_PAIRS) {
    if (edited === rk && base) t[ak] = r2(base * (nf(t[rk]) || 0) / 100)
    else if (edited === ak && base) t[rk] = r2((nf(t[ak]) || 0) / base * 100)
  }
  // CGST and SGST are one tax split in half — a state levy is never 9% one side
  // and 2% the other. Mirrored only when the other half is blank or was tracking
  // this one, so a deliberately odd pair is never quietly straightened.
  const mirror = (from, to) => {
    const was = nf(tax[from]), other = nf(tax[to])
    if (other == null || other === 0 || other === was) {
      t[to] = nf(t[from])
      if (base) t[to.replace('_rate', '_amount')] = r2(base * (nf(t[to]) || 0) / 100)
    }
  }
  if (edited === 'cgst_rate') mirror('cgst_rate', 'sgst_rate')
  if (edited === 'sgst_rate') mirror('sgst_rate', 'cgst_rate')
  return t
}

// The foot of the invoice, derived from everything above it. Deliberately the
// same shape the server reconciles against (extraction/validate.py), so the
// figure this screen computes is the one that will NOT raise a warning on save.
function recalcTotals(data) {
  const items = data.line_items || []
  const tax = data.taxes || {}
  const tot = { ...(data.totals || {}) }
  const sub = r2(items.reduce((s, it) => s + (nf(it.amount) || 0), 0))
  const taxable = r2(items.reduce((s, it) => s + (nf(it.taxable_value) ?? nf(it.amount) ?? 0), 0))
  tot.total_qty = r2(items.reduce((s, it) => s + (nf(it.qty) || 0), 0))
  tot.sub_total = sub
  tot.taxable_total = taxable || sub
  tot.tax_total = r2((nf(tax.cgst_amount) || 0) + (nf(tax.sgst_amount) || 0)
                     + (nf(tax.igst_amount) || 0))
  tot.grand_total = r2((tot.taxable_total || 0) + tot.tax_total
                       + (nf(tax.other_charges) || 0) + (nf(tax.freight) || 0)
                       - (nf(tax.special_discount) || 0) + (nf(tax.round_off) || 0))
  return tot
}

// --- size runs: "28-2-38" is six rows of three ---
// A garment bundle is bought as a RUN, not as a list. 28 to 38 in twos is six
// sizes with the eighteen pieces spread evenly over them, and written out by hand
// that is six rows, six sizes and six identical quantities — six chances to key
// one of them wrong, for something the packing slip already says in six
// characters. So it is typed the way it is written, once, and the rows are worked
// out from it. What it makes is ordinary afterwards: change a quantity, delete a
// size, add one the run didn't cover.
//
// Two screens read one. On the INVOICE, a billed bundle becomes the six lines it
// is really made of, so the sizes are on the document from the start and the GRN,
// the products and the QR labels all follow from them. On the GRN BREAKDOWN, the
// same run becomes the six stock items that came out of the carton. Same
// arithmetic either way — which screen it is typed on is only a question of when
// somebody knows the mix.
//
// Mind the separator. In a size COLUMN "30-2" means two of size 30 — that is what
// services/size_split.py reads off the supplier's own bill, and it is careful
// about it because guessing wrong invents stock. Here it cannot mean that: this
// box is only ever start-step-end, which is the one thing it is for. Two numbers
// ("28-38") step by one.
// A step keyed as .2 instead of 2 turns a six-size run into fifty-one rows, and
// nothing in this trade is a run of forty sizes — so that is a typo, not a run.
const SIZE_RUN_MAX = 40
const parseSizeRun = (spec) => {
  const text = String(spec || '').trim()
  if (!text) return { sizes: [], why: '' }
  // "16*22" written with an x or a * is two numbers and NO step. On one supplier's
  // bills it is a size range — sixteen to twenty-two, in twos — and on another
  // "127 X 200" is a bedsheet. Nothing in the text itself separates them, and
  // reading either as a run off a dash would turn one line into seven sizes that
  // were never on the bill.
  //
  // So this form is never a run on its own say-so. It becomes one only where the
  // quantity proves the step (runFromSize, on the invoice grid: four pieces over
  // 16-2-22 is one of each, and the bedsheet's 73 divides by nothing) — and what
  // that produces is a SUGGESTION in this box, written out in full, for a human
  // to look at. Typed in by hand it is refused, and the message asks for the step
  // rather than pretending the run cannot be read.
  //
  // The server guards the same ambiguity from the other end, on what the supplier
  // printed: services/size_split.py, where "30x2" is refused unless the
  // arithmetic proves it.
  if (/[x×*]/i.test(text)) {
    return { sizes: [], why: `“${text}” does not say its step — write it start-step-end, like 16-2-22.` }
  }
  const nums = text.split(/[^0-9.]+/).filter(Boolean).map(Number)
  if (nums.length < 2 || nums.length > 3 || nums.some((n) => Number.isNaN(n))) {
    return { sizes: [], why: 'Write the run as start-step-end — 28-2-38.' }
  }
  const [start, step, end] = nums.length === 3 ? nums : [nums[0], 1, nums[1]]
  if (!(step > 0)) return { sizes: [], why: 'The step has to be more than zero — 28-2-38.' }
  const count = Math.floor(round3(Math.abs(end - start) / step)) + 1
  if (count > SIZE_RUN_MAX) {
    return { sizes: [], why: `That is ${count} sizes — check the step.` }
  }
  // 38-2-28 is the same six sizes counted down, which is how some slips write it
  const stride = end < start ? -step : step
  return { sizes: Array.from({ length: count }, (_, i) => String(round3(start + i * stride))), why: '' }
}
// 18 over 6 sizes is 3 each. 20 over 6 is 4, 4, 3, 3, 3, 3 — what will not divide
// goes to the first rows rather than being dropped, because the thing that has to
// be true before this GRN can post is that every piece is placed somewhere.
const spreadQty = (total, n) => {
  const t = Math.max(0, +total || 0)
  if (n <= 0) return []
  if (Number.isInteger(t)) {
    const base = Math.floor(t / n)
    return Array.from({ length: n }, (_, i) => base + (i < t - base * n ? 1 : 0))
  }
  // metres and kilos divide unevenly; the rounding drift lands on the last row so
  // the rows still add up to exactly what arrived
  const each = round3(t / n)
  return Array.from({ length: n }, (_, i) => (i === n - 1 ? round3(t - each * (n - 1)) : each))
}

// The attribute vocabularies — the masters in data/product_attributes.json, merged
// with whatever is already recorded against a product. The same lists the GRN
// breakdown and the phone's detail form offer, and that is the point of them
// being one call: a brand typed three ways is three brands, and the way to stop
// that is to show the operator the nine that already exist before they type a
// tenth. Free text still goes through — the list guides, it does not gate.
function useProductOptions() {
  const [opts, setOpts] = useState({})
  useEffect(() => { api.productOptions().then(setOpts).catch(() => {}) }, [])
  return opts
}

// ---------- line items ----------
// Full per-line field set; the table scrolls horizontally so nothing is dropped.
//
// `barcode` is deliberately NOT a column. It still comes off the invoice and is
// still stored on the line — `inventory.match_product` keys a re-buy on it, which
// is what keeps one item's cost history in one product — but it is the supplier's
// number, not ours, and nobody reviewing an invoice checks it by eye. The
// trade-off: a misread supplier barcode can no longer be corrected here.
// `calc` marks a column the arithmetic maintains — it is tinted, and its tooltip
// says what it is derived from, so nobody wonders why a figure they did not type
// appeared. Every one of them is still editable: typing into a derived cell makes
// it the input and moves the others instead (see recalcLine).
const ITEM_COLS = [
  ['description', 'Description', false, 200],
  ['brand', 'Brand', false, 90], ['design', 'Design', false, 90], ['size', 'Size', false, 116],
  ['hsn', 'HSN', false, 80], ['qty', 'Qty', true, 60], ['uom', 'UOM', false, 60],
  // MRP → less the discount → Rate, read left to right, every one of them PER
  // PIECE so the row can be checked against the invoice by eye: 995 − 345 = 650.
  // Only Taxable and Amount are line totals, and they sit apart at the end.
  ['mrp', 'MRP', true, 70, 'Printed retail price, per piece. Worked out for you if you type a Rate and a Buffer %.'],
  ['discount_pct', 'Discount %', true, 92, 'Off MRP, per piece: (MRP − Rate) ÷ MRP. Type a Rate instead and this fills itself.'],
  ['rate', 'Rate', true, 74, 'The COST, per piece — what you pay. Type it and both percentages fill themselves.'],
  // The same gap as Discount %, named from the other end — so the two flank the
  // Rate they describe: MRP −discount→ Rate, and Rate +buffer→ MRP. Which one
  // somebody types depends on which price they already know, and that is the
  // whole point of carrying both.
  ['buffer_pct', 'Buffer %', true, 124, 'On top of COST: Sell price = Rate × (1 + buffer) — e.g. 320 + 40% = 448. Type it and the sell price is worked out.'],
  // The retail end of the same MRP. Not on the supplier's bill — nobody prints
  // your shelf price for you — but set here the product is born priced instead
  // of landing in Inventory with nothing on it. Kept well clear of the purchase
  // discount above: same MRP, two different percentages, never the same number.
  // The second half of the chain. Two markups because they answer two
  // questions: what to charge, and how much room to leave above that for a
  // discount the customer can see on the tag.
  ['mrp_buffer_pct', 'MRP Buffer %', true, 128, 'On top of the SELL price: MRP = Sell × (1 + this) — e.g. 448 + 25% = 560. The room above your price that the printed tag shows.'],
  ['sale_discount_pct', 'Sale Disc %', true, 96, 'Off MRP for the SHOP — what the tag shows the customer saving. Follows from the two buffers; type a sell price instead and this fills itself.'],
  ['sale_price', 'Sell Price', true, 92, 'What you CHARGE: MRP less the sale discount — e.g. 995 − 20% = 796. Type it and its % follows.'],
  ['taxable_value', 'Taxable', true, 90, 'Line total. The Amount, unless the invoice states its own.'],
  ['amount', 'Amount', true, 90, 'Line total: Qty × Rate. Type an amount and the rate is worked back out of it.'],
]
//: The invoice columns that have a master vocabulary behind them, as
//: column -> the key it is listed under. Brand is the one that matters here: it
//: is identity — an ESSA t-shirt and a YUVA t-shirt in the same size are two
//: stock items — so it is the field where a spelling invents a product, and the
//: cure is showing the three hundred that exist before somebody types the three
//: hundred and first. `uom` and `size` have lists too and take one line each,
//: but a size cell on this screen is as often a run as a size.
const ITEM_LISTED = { brand: 'brand' }

//: Columns where ONE value for the whole invoice is a sensible thing to say, and
//: what kind of box says it. A supplier's bill is very often all one brand, all
//: one HSN, all one unit and all one markup — and typing 620520 into twenty-six
//: lines is the kind of work that gets abandoned halfway, which leaves an invoice
//: where some lines carry an HSN and some do not.
//:
//: Three columns are deliberately absent:
//:
//:   qty              the one figure that is genuinely per line. It is what the
//:                    invoice is counting; setting every line to the same number
//:                    is not a thing anybody means. (Same reason the GRN
//:                    breakdown grid leaves it out.)
//:   taxable_value    both are line TOTALS, derived from qty × rate. Filling them
//:   amount           with one number back-solves a different rate onto every
//:                    line, which is arithmetic nobody asked for.
const ITEM_FILL = {
  brand: 'text', design: 'text', size: 'text', hsn: 'text', uom: 'text',
  mrp: 'num', discount_pct: 'pct', rate: 'num',
  buffer_pct: 'pct', mrp_buffer_pct: 'pct',
  sale_discount_pct: 'pct', sale_price: 'num',
}

// --- size groups: the lines one bundle was split into ---
// Four lines that agree on everything except the size and the count are one
// garment seen four ways — same description, same design, same HSN, same price.
// That is what makes them a group, and NOTHING is stamped on the rows to say so:
// it is read off the values themselves, so it survives a save and a reload, it
// costs the invoice no field it did not have, and a line edited until it no
// longer matches simply leaves the group. Folding is a view of the lines. It
// changes none of them.
//
// Consecutive only. Two identical runs at opposite ends of a fifty-nine-line
// bill are two entries on the paper and stay two here; collapsing across the
// rows between them would fold a part of the invoice that is not there.
const ITEM_GROUP_BY = ['description', 'brand', 'design', 'hsn', 'uom',
                       'rate', 'mrp', 'discount_pct', 'sale_price']
const itemGroupKey = (it) =>
  ITEM_GROUP_BY.map((k) => String(it?.[k] ?? '').trim().toLowerCase()).join('|')
//: only a line that says something can be grouped. Two untouched blank rows agree
//: on everything, and folding them would be folding nothing into nothing.
const itemGroupable = (it) =>
  !!String(it?.description ?? '').trim() && !!String(it?.size ?? '').trim()
//: the invoice as it is READ: every line, with the runs gathered. `to` is
//: exclusive, so `to - from > 1` is what makes an entry a group.
const groupItems = (items) => {
  const out = []
  for (let i = 0; i < items.length;) {
    let j = i + 1
    if (itemGroupable(items[i])) {
      const k = itemGroupKey(items[i])
      while (j < items.length && itemGroupable(items[j]) && itemGroupKey(items[j]) === k) j++
    }
    out.push({ sig: itemGroupKey(items[i]), from: i, to: j })
    i = j
  }
  return out
}
const ITEM_CALC = new Set(['rate', 'discount_pct', 'buffer_pct', 'mrp_buffer_pct',
  'mrp', 'amount', 'taxable_value', 'sale_discount_pct', 'sale_price'])

function LineItems({ items, setItems }) {
  // A two-page invoice runs to sixty lines and more, and the whole set in one
  // table means scrolling past forty rows to reach the totals under it. Paged at
  // 25 — the same control the document list uses, so it is not a second idea
  // about what paging looks like.
  //
  // The row's index in the WHOLE list is what upd and delRow take: page 2 row 1
  // is item 25, and passing the position on the page would silently edit the
  // first row of the invoice instead. `from` is 1-based, hence the −1.
  // Paged over the VIEW, not the lines: a folded group is one thing to page past,
  // and "1–25 of 24" would otherwise count rows nobody can see.
  const view = useMemo(() => groupItems(items), [items])
  const [folded, setFolded] = useState({})       // group signature -> folded away
  const page = usePaged(view, 25)
  const opts = useProductOptions()
  // Which line has its size run open, and what has been typed into it. One box
  // serves the whole table because only one line is ever being split.
  const [runFor, setRunFor] = useState(null)
  const [runSpec, setRunSpec] = useState('')
  const [runNote, setRunNote] = useState('')     // where a suggested run came from
  const [runRows, setRunRows] = useState([])     // the size/qty list, before it is applied
  // the edited field is the input; recalcLine moves only what depends on it
  const upd = (i, k, v) => setItems(items.map((x, j) =>
    (j === i ? recalcLine({ ...x, [k]: num(v) }, k, x) : { ...x })))
  // The new row goes on the end, which on a paged table is not the page being
  // looked at — pressing "add line" on page 1 of 3 otherwise appears to do
  // nothing at all. Follow it.
  const addRow = () => {
    const next = [...items, { description: '', qty: null, rate: null, amount: null, uom: 'PCS' }]
    setItems(next)
    if (page.size) page.setPage(Math.ceil((view.length + 1) / page.size))
  }
  // A line read off an invoice arrives with an MRP and a Rate and no buffer —
  // the bill states a discount, never a markup. The buffer is that same gap
  // read the other way, so it is shown from the two prices rather than left
  // blank until somebody types in the column to find out what is already true.
  const cell = (it, k) => {
    if (k === 'buffer_pct' && (it.buffer_pct == null || it.buffer_pct === '')) {
      const b = bufferFrom(nf(it.sale_price), nf(it.rate))
      return b == null ? '' : b
    }
    if (k === 'mrp_buffer_pct' && (it.mrp_buffer_pct == null || it.mrp_buffer_pct === '')) {
      const b = bufferFrom(nf(it.mrp), nf(it.sale_price))
      return b == null ? '' : b
    }
    return it[k] ?? ''
  }
  const delRow = (i) => setItems(items.filter((_, j) => j !== i))
  const delGroup = (g) => {
    if (!window.confirm(`Delete all ${g.to - g.from} lines of “${items[g.from].description}”?`)) return
    setItems(items.filter((_, j) => j < g.from || j >= g.to))
  }
  // One cell of a folded group. The sizes are listed, the three line totals are
  // summed, and anything the lines disagree on is shown as a dash rather than as
  // whichever of them happened to be first.
  const groupCell = (g, k) => {
    const rows = items.slice(g.from, g.to)
    if (k === 'size') {
      const sizes = rows.map((r) => String(r.size ?? '').trim()).filter(Boolean)
      return sizes.length > 6
        ? `${sizes.slice(0, 5).join(', ')}, … ${sizes[sizes.length - 1]}`
        : sizes.join(', ')
    }
    if (k === 'qty' || k === 'amount' || k === 'taxable_value') {
      return round3(rows.reduce((n2, r) => n2 + (+r[k] || 0), 0))
    }
    const vals = new Set(rows.map((r) => String(cell(r, k) ?? '')))
    return vals.size === 1 ? [...vals][0] : '—'
  }
  // One billed bundle becomes the lines it is really made of: same description,
  // same design, same HSN, same pricing, one size each and the quantity shared
  // out. Done HERE, while the invoice is being keyed, the sizes are on the
  // document from the start — the GRN, the products and the QR labels all follow
  // from them, and nobody breaks the same bundle down a second time at the dock.
  //
  // The invoice still has to reconcile against the paper it was read off, so the
  // line TOTALS are shared out rather than copied: six lines each carrying the
  // whole amount would multiply the bill by six. Σ qty and Σ value come out of
  // this exactly as they went in, which is what makes it safe to do on a
  // document that has already been checked against its image.
  // One line, spread across a list of {size, qty} — the list the operator has in
  // front of them, not one recomputed behind their back, so a count they changed
  // is the count that lands on the invoice. A size with nothing against it is
  // dropped: zero of a size means that size did not come, and a line of no
  // pieces is not a line.
  const expandRows = (it, rows) => {
    const kept = rows.filter((r) => String(r.size ?? '').trim() !== '' && +r.qty > 0)
    if (!kept.length) return [it]
    const qtys = kept.map((r) => +r.qty)
    // a line total, shared in the same proportion as the quantity, with the
    // rounding drift on the last line so the column still adds up to what it did
    const share = (total) => {
      if (total == null) return kept.map(() => null)
      const sum = qtys.reduce((a, b) => a + b, 0)
      const out = qtys.map((q) => r2(sum ? total * q / sum : total / kept.length))
      out[out.length - 1] = r2(total - out.slice(0, -1).reduce((a, b) => a + b, 0))
      return out
    }
    const amounts = share(nf(it.amount))
    const taxables = share(nf(it.taxable_value))
    // through recalcLine with the ORIGINAL as `prev`: qty × rate re-derives the
    // amount where the line has a rate, and a taxable value that was merely
    // echoing the amount keeps echoing it, while one the invoice STATED in its
    // own right keeps the share worked out above
    return kept.map((r, n) => recalcLine(
      { ...it, size: String(r.size).trim(), qty: qtys[n],
        amount: amounts[n], taxable_value: taxables[n] }, 'qty', it))
  }
  //: what a freshly generated run is, before anybody touches it: the sizes, and
  //: the line's pieces spread evenly over them
  const evenRows = (it, sizes) => {
    const qtys = spreadQty(nf(it.qty) ?? 0, sizes.length)
    return sizes.map((size, n) => ({ size, qty: String(qtys[n]) }))
  }
  const closeRun = () => { setRunFor(null); setRunSpec(''); setRunNote(''); setRunRows([]) }
  // Generate makes a LIST, not lines. Nothing on the invoice moves until Split is
  // pressed, and that gap is the whole point of the list: an even spread is only
  // ever a guess about what is in the carton, and the person holding the packing
  // slip is the one who knows that 22 came four short and 18 came four over. So
  // the arithmetic offers its answer and they correct it — change a count, drop a
  // size that was not sent, add one the run did not cover.
  const genRows = (i) => {
    const { sizes } = parseSizeRun(runSpec)
    if (sizes.length) setRunRows(evenRows(items[i], sizes))
  }
  const updRunRow = (n, k, v) => setRunRows(runRows.map((r, j) => (j === n ? { ...r, [k]: v } : r)))
  const dropRunRow = (n) => setRunRows(runRows.filter((_, j) => j !== n))
  const addRunRow = () => setRunRows([...runRows, { size: '', qty: '' }])
  const splitRun = (i) => {
    if (!runRows.length) return
    setItems([...items.slice(0, i), ...expandRows(items[i], runRows), ...items.slice(i + 1)])
    closeRun()
  }
  // …and the same sizes against every line that reads the same way.
  //
  // A bill written as a size run writes it on EVERY line: six Frocks, six design
  // numbers, "16*22" in all six size cells. Splitting them one at a time is the
  // same decision taken six times, and on a fifty-nine-line bill it is the kind
  // of work that gets abandoned halfway — which leaves an invoice where some
  // lines carry sizes and some do not, and that is worse than none of them doing.
  //
  // It takes the SIZES off the list, so a size dropped there is dropped
  // everywhere — but each line's own quantity is spread evenly over them, because
  // a count hand-set for a line of four means nothing to a line of eight. The
  // confirm says so: this button is blunt by nature and must not be quiet about
  // it.
  //
  // "Reads the same way" is the size cell, matched as text. Deliberately not the
  // run a line would suggest: two lines can suggest the same run from different
  // quantities and different size cells, and rewriting a line somebody never
  // looked at is exactly what this must not do. A blank size matches nothing —
  // there is nothing there to have read.
  const sameSizeAs = (i) => {
    const key = String(items[i]?.size ?? '').trim().toLowerCase()
    if (!key) return []
    return items.reduce((out, x, j) =>
      (String(x.size ?? '').trim().toLowerCase() === key ? [...out, j] : out), [])
  }
  const splitRunAll = (i) => {
    const sizes = runRows.map((r) => String(r.size ?? '').trim()).filter(Boolean)
    const targets = new Set(sameSizeAs(i))
    if (!sizes.length || !targets.size) return
    if (!window.confirm(
      `Split all ${targets.size} line(s) whose Size reads “${items[i].size}” into these sizes?\n\n`
      + `${sizes.join(', ')}\n\n`
      + `Each line's OWN quantity is spread evenly across them, so a count you set `
      + `by hand above applies to line ${i + 1} only.\n`
      + `${targets.size} line(s) become ${targets.size * sizes.length}. `
      + 'Σ qty and Σ value do not move.')) return
    setItems(items.flatMap((x, j) => (targets.has(j) ? expandRows(x, evenRows(x, sizes)) : [x])))
    closeRun()
  }
  const SIZE_STEPS = [2, 1, 3, 4, 5]     // garment runs go up in twos far more often than ones
  const runFromSize = (v, qty) => {
    const nums = String(v || '').trim().split(/[^0-9.]+/).filter(Boolean).map(Number)
    if (nums.some((n) => Number.isNaN(n))) return ''
    if (nums.length === 3) {              // the run in full: it states its own step
      const spec = nums.join('-')
      return parseSizeRun(spec).sizes.length ? spec : ''
    }
    if (nums.length !== 2 || !(qty > 0)) return ''
    const [start, end] = nums
    const span = Math.abs(end - start)
    for (const step of SIZE_STEPS) {
      if (!span || span % step) continue
      const n = span / step + 1
      if (n > 1 && n <= SIZE_RUN_MAX && qty % n === 0) return `${start}-${step}-${end}`
    }
    return ''
  }
  const openRun = (i) => {
    if (runFor === i) { closeRun(); return }
    const it = items[i]
    const spec = runFromSize(it.size, nf(it.qty))
    setRunFor(i); setRunSpec(spec)
    // a run that was READ rather than typed has already been proved against the
    // quantity, so its list is shown straight away — the answer belongs on screen,
    // not one press away
    setRunRows(spec ? evenRows(it, parseSizeRun(spec).sizes) : [])
    setRunNote(spec ? `read off the size column — “${it.size}”, and ${nf(it.qty)} divides across it exactly` : '')
  }
  // Fill a whole column with one percentage.
  //
  // Both buffers are usually one decision for the whole delivery — everything on
  // this bill is marked up the same — and typing 40 into fifty-nine rows is the
  // kind of work that gets abandoned halfway, leaving half the lines priced and
  // half not, which is worse than none.
  //
  // On a button rather than as you type: a column that rewrote itself on every
  // keystroke would overwrite the rows somebody had already set by hand, and
  // "4" is a value on the way to "40". Applied, each row still edits normally.
  const [fill, setFill] = useState({})
  //: whether there is anything to apply. A number column needs a NUMBER, which
  //: is why this asks nf and not num: num hands back "abc" unchanged — it only
  //: converts what converts — so a guard written on it lets unreadable text
  //: through and writes it to every line on the invoice. nf answers null for
  //: anything that is not a figure, and 0 for "0", which is a number somebody
  //: may well mean.
  //:
  //: Blank does NOTHING rather than clearing the column. Emptying twenty-six
  //: lines is not something to put behind the same button as filling them.
  const fillReady = (key) => (ITEM_FILL[key] === 'text'
    ? !!(fill[key] || '').trim()
    : nf(fill[key]) != null)
  const applyFill = (key) => {
    if (!fillReady(key)) return
    const text = ITEM_FILL[key] === 'text'
    const v = text ? fill[key].trim() : nf(fill[key])
    // A price goes through recalcLine, because setting the number without
    // re-pricing would show a 40% buffer over prices that never moved. A brand
    // or an HSN is not part of that arithmetic and is set as typed.
    setItems(items.map((it) => (text
      ? { ...it, [key]: v }
      : recalcLine({ ...it, [key]: v }, key, it))))
  }
  const fillCell = (key, label) => {
    const kind = ITEM_FILL[key]
    return (
      <div className={'fillbox' + (kind === 'text' ? ' text' : '')}>
        <input value={fill[key] || ''} list={ITEM_LISTED[key] ? 'essa-item-' + key : undefined}
          inputMode={kind === 'text' ? undefined : 'decimal'}
          placeholder={kind === 'pct' ? '%' : 'all'}
          title={`Set ${label} on every line of this invoice`}
          onChange={(e) => setFill({ ...fill, [key]: e.target.value })}
          onKeyDown={(e) => { if (e.key === 'Enter') applyFill(key) }} />
        <button className="btn" disabled={!fillReady(key)}
          onClick={() => applyFill(key)}
          title={`Apply ${fill[key] || '…'}${kind === 'pct' ? '%' : ''} to all ${items.length} line(s)`}>
          Apply all</button>
      </div>
    )
  }


  //: the groups there are to fold, and whether they already are
  const runGroups = view.filter((g) => g.to - g.from > 1)
  const allFolded = runGroups.length > 0 && runGroups.every((g) => folded[g.sig])
  const qtySum = items.reduce((s, x) => s + (+x.qty || 0), 0)
  const amtSum = items.reduce((s, x) => s + (+(x.taxable_value ?? x.amount) || 0), 0)
  // The per-piece gap between MRP and cost, taken out to the line — the one
  // place the whole-invoice figure is wanted. Derived from the two prices rather
  // than from a stored difference, so it agrees with the columns on screen even
  // on a line where nothing was typed into the pricing at all.
  const discSum = items.reduce((s, x) => {
    const gap = (+x.mrp || 0) - (+x.rate || 0)
    return s + (gap > 0 ? gap * (+x.qty || 0) : 0)
  }, 0)
  return (
    <div>
      <div className="tablewrap">
      <table className="items" style={{ minWidth: 1310 }}>
        {/* The line number is the column that makes a paged table usable: it is
            how a row on screen is matched to the numbered row on the paper,
            which is the whole job when checking a 59-line bill against it. */}
        <thead><tr><th style={{ minWidth: 34 }} title="Line number on the invoice">#</th>
          {ITEM_COLS.map(([k, l, , w, tip]) =>
          <th key={k} style={{ minWidth: w }} title={tip}
            className={ITEM_CALC.has(k) ? 'calc' : undefined}>{l}{tip ? ' ƒ' : ''}</th>)}<th></th></tr>
          {/* Under the headings, not in a toolbar above the table: the control
              belongs to the column it fills, and aligned under it there is
              nothing to explain about which is which. */}
          <tr className="fillrow"><th></th>
            {ITEM_COLS.map(([k, l]) => (
              <th key={k}>{ITEM_FILL[k] ? fillCell(k, l) : null}</th>
            ))}<th></th></tr>
        </thead>
        <tbody>
          {page.slice.map((g) => {
            const grouped = g.to - g.from > 1
            // FOLDED — the whole group as one row. It is a VIEW of the lines, not
            // another line: every figure on it is theirs, summed or shared, so
            // none of it is an input. Open it to change anything.
            if (grouped && folded[g.sig]) {
              const kids = items.slice(g.from, g.to)
              return (
                <tr key={g.sig + g.from} className="foldrow">
                  <td className="rowno" title={`Lines ${g.from + 1} to ${g.to} of the invoice`}>
                    {g.from + 1}–{g.to}</td>
                  {ITEM_COLS.map(([k, , isNum, , tip]) => (
                    <td key={k} className={(isNum ? 'num' : '') + (ITEM_CALC.has(k) ? ' calc' : '')}
                      title={tip}>
                      {k === 'size' ? (
                        <div className="sizecell">
                          <span className="foldsizes"
                            title={kids.map((r) => `${r.size} → ${r.qty}`).join('\n')}>
                            {groupCell(g, k)}</span>
                          <button className="runbtn on wide"
                            title={`Show all ${kids.length} size lines again`}
                            onClick={() => setFolded({ ...folded, [g.sig]: false })}>
                            ≡ {kids.length}</button>
                        </div>
                      ) : groupCell(g, k)}
                    </td>
                  ))}
                  <td><button className="btn" style={{ padding: '2px 7px' }}
                    title={`Delete all ${kids.length} lines`}
                    onClick={() => delGroup(g)}>×</button></td>
                </tr>
              )
            }
            // OPEN — the lines themselves, each editable as before
            return items.slice(g.from, g.to).map((it, n) => {
            const i = g.from + n                 // its index in the whole invoice
            const run = runFor === i ? parseSizeRun(runSpec) : null
            const qtys = run?.sizes.length ? spreadQty(nf(it.qty) ?? 0, run.sizes.length) : []
            const even = qtys.length > 0 && qtys.every((q) => q === qtys[0])
            const alike = run ? sameSizeAs(i) : []
            const listed = !run ? '' : run.sizes.length > 10
              ? `${run.sizes.slice(0, 9).join(', ')}, … ${run.sizes[run.sizes.length - 1]}`
              : run.sizes.join(', ')
            // the running check, and what it is checked against. The line's
            // quantity is what the supplier BILLED, and the sizes are how that
            // quantity breaks down — so they have to come to the same number or
            // the invoice stops agreeing with the paper it was read off.
            const billed = nf(it.qty) ?? 0
            const assigned = round3(runRows.reduce((n2, r) => n2 + (+r.qty || 0), 0))
            const balanced = billed ? sameQty(assigned, billed) : assigned > 0
            const keeping = runRows.filter((r) => String(r.size ?? '').trim() && +r.qty > 0).length
            // per piece: the rate where the bill states one, otherwise whatever
            // the line amount works out to across the pieces
            const unit = nf(it.rate) ?? (billed ? (nf(it.amount) || 0) / billed : 0)
            return (
              <React.Fragment key={i}>
              <tr>
                <td className="rowno" title="Line number on the invoice">{i + 1}</td>
                {ITEM_COLS.map(([k, , isNum, , tip]) => (
                  <td key={k} className={(isNum ? 'num' : '') + (ITEM_CALC.has(k) ? ' calc' : '')}
                    title={tip}>
                    {/* A bundle billed as one line is several stock items, and the
                        control that says so belongs IN the size column — beside the
                        "16*22" somebody is already looking at, not at the far end
                        of fifteen columns of pricing where nobody scrolls to find
                        it. It lights up when the size and the quantity between them
                        prove a run, which on a bill written this way is every
                        line. */}
                    {k === 'size' ? (
                      <div className="sizecell">
                        <input value={cell(it, k)} onChange={(e) => upd(i, k, e.target.value)} />
                        {/* Two jobs, and which one it has is settled by whether
                            this line is already one size OF a run. A line that is
                            has nothing left to split, so ≡ folds its group away
                            instead — which is the only thing anybody wants from a
                            row that says FROCK · 4313 · 16 · 1. */}
                        <button className={'runbtn' + (grouped ? ' fold wide'
                          : runFromSize(it.size, nf(it.qty)) ? ' on' : '')}
                          title={grouped
                            ? `Fold these ${g.to - g.from} sizes into one row`
                            : runFor === i ? 'Close the size detail'
                              : runFromSize(it.size, nf(it.qty))
                                ? `Split this line into ${parseSizeRun(runFromSize(it.size, nf(it.qty))).sizes.join(', ')}`
                                : 'Split this line into a size run — 28-2-38 becomes six lines, one per size'}
                          onClick={() => (grouped
                            ? setFolded({ ...folded, [g.sig]: true })
                            : openRun(i))}>
                          {grouped ? `≡ ${g.to - g.from}` : runFor === i ? '×' : '≡'}</button>
                      </div>
                    ) : (
                      <input value={cell(it, k)} list={ITEM_LISTED[k] ? 'essa-item-' + k : undefined}
                        onChange={(e) => upd(i, k, e.target.value)} />
                    )}
                  </td>
                ))}
                <td><button className="btn" style={{ padding: '2px 7px' }} onClick={() => delRow(i)}>×</button></td>
              </tr>
              {run && (
                <tr>
                  <td colSpan={ITEM_COLS.length + 2} className="runcell">
                    {/* the run, and what it would make */}
                    <div className="rowedit-bar sizerun">
                      <span className="runlabel">Line {i + 1} · size detail</span>
                      <input value={runSpec} placeholder="Start-Increment-End" autoFocus
                        onChange={(e) => { setRunSpec(e.target.value); setRunNote('') }}
                        onKeyDown={(e) => { if (e.key === 'Enter') genRows(i) }}
                        title={'Start – step – end, the way the packing slip writes it.\n'
                          + '28-2-38 is 28, 30, 32, 34, 36, 38 — and 28-38 steps by one.'} />
                      <button className="btn" disabled={!run.sizes.length} onClick={() => genRows(i)}
                        title={run.sizes.length
                          ? `List ${run.sizes.join(', ')} with the ${billed} pieces spread over them`
                          : 'Write the run as start-step-end first'}>⊕ Generate</button>
                      {runNote && <span className="runfrom">↳ {runNote}</span>}
                      <span className={'why' + (run.why ? ' bad' : '')}>
                        {run.why || (run.sizes.length
                          ? <><b>{listed}</b>
                              {` — ${run.sizes.length} sizes, `}
                              {even ? `${qtys[0]} each` : `${Math.min(...qtys)}–${Math.max(...qtys)} each`}</>
                          : 'Start–step–end. Generates the sizes below with the quantity spread over '
                            + 'them — change any of it before splitting.')}
                      </span>
                    </div>

                    {/* …and the list it makes, which is the part that gets
                        corrected. Nothing on the invoice has moved yet. */}
                    {runRows.length > 0 && (
                      <div className="runrows">
                        <table>
                          <thead><tr>
                            <th style={{ width: 24 }} />
                            <th>Size</th><th className="num">Qty</th>
                            <th className="num">Value</th><th style={{ width: 30 }} />
                          </tr></thead>
                          <tbody>
                            {runRows.map((r, n2) => (
                              <tr key={n2}>
                                <td className="rowno">{n2 + 1})</td>
                                <td><input value={r.size}
                                  onChange={(e) => updRunRow(n2, 'size', e.target.value)} /></td>
                                <td className="num"><input value={r.qty} inputMode="decimal"
                                  onChange={(e) => updRunRow(n2, 'qty', e.target.value)} /></td>
                                <td className="num money">{money((+r.qty || 0) * unit)}</td>
                                <td><button className="btn" style={{ padding: '1px 6px' }}
                                  title="Drop this size — it was not in the carton"
                                  onClick={() => dropRunRow(n2)}>×</button></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        <div className="runfoot">
                          <button className="btn" onClick={addRunRow}
                            title="A size the run did not cover">+ add size</button>
                          <span className={'tally ' + (balanced ? 'ok' : 'bad')}>
                            {billed
                              ? <>{assigned} of {round3(billed)} assigned{balanced ? ' ✓'
                                  : assigned < billed
                                    ? ` · ${round3(billed - assigned)} still to place`
                                    : ` · ${round3(assigned - billed)} more than the line was billed`}</>
                              : <>{assigned} assigned · this line was billed no quantity</>}
                          </span>
                          <span className="tally">{money(assigned * unit)}</span>
                        </div>
                      </div>
                    )}

                    {/* what to do with it */}
                    <div className="rowedit-bar sizerun">
                      <button className="btn primary" disabled={!keeping || !balanced}
                        title={!keeping ? 'Generate the sizes first'
                          : balanced ? `Line ${i + 1} becomes ${keeping} lines`
                            : 'The sizes have to account for every piece the line was billed'}
                        onClick={() => splitRun(i)}>
                        {keeping ? `Split into ${keeping} lines` : 'Split into lines'}</button>
                      {alike.length > 1 && (
                        <button className="btn" disabled={!keeping}
                          title={`Apply these sizes to all ${alike.length} lines whose Size reads “${it.size}” `
                            + '— each line\'s own quantity is spread evenly over them'}
                          onClick={() => splitRunAll(i)}>Split all {alike.length} lines</button>
                      )}
                      <button className="btn" disabled={!runRows.length}
                        title="Empty the list and start again"
                        onClick={() => setRunRows([])}>Clear</button>
                      <button className="btn" onClick={closeRun}>Close</button>
                    </div>
                  </td>
                </tr>
              )}
              </React.Fragment>
            )
            })
          })}
        </tbody>
      </table>
      </div>
      <Pager {...page} noun="line" />
      {/* one datalist for the whole table, not one per row */}
      {Object.entries(ITEM_LISTED).map(([k, src]) => (
        <datalist key={k} id={'essa-item-' + k}>
          {(opts[src] || []).map((v) => <option key={v} value={v} />)}
        </datalist>
      ))}
      <div className="items-foot">
        <span>{items.length} lines</span>
        <span>Σ qty <b>{qtySum.toLocaleString('en-IN')}</b></span>
        {discSum > 0 && <span title="Σ ((MRP − Rate) × Qty) — the whole invoice's gap between printed price and cost">
          Σ MRP − cost <b>{money(discSum)}</b></span>}
        <span>Σ value <b>{money(amtSum)}</b></span>
        {/* A twenty-four-line invoice that is really six garments in four sizes
            each is six things to check, not twenty-four — but only if they can be
            put away all at once. One line at a time is not an offer anybody
            takes on a bill this shape. */}
        {runGroups.length > 0 && (
          <button className="btn" style={{ padding: '3px 10px', marginLeft: 'auto' }}
            title={allFolded ? 'Show every size line again'
              : `Fold each run of sizes into one row — ${runGroups.length} of them on this invoice`}
            onClick={() => setFolded(allFolded ? {}
              : Object.fromEntries(runGroups.map((g) => [g.sig, true])))}>
            {allFolded ? '⊞ show every size'
              : `⊟ fold ${runGroups.length} size group${runGroups.length === 1 ? '' : 's'}`}
          </button>
        )}
        <button className="btn" style={{ padding: '3px 10px', marginLeft: runGroups.length ? 0 : 'auto' }}
          onClick={addRow}>+ add line</button>
      </div>
    </div>
  )
}

// ---------- LR register candidate picker ----------
// Shown when no invoice/LR number lines up. These rows agree on supplier only,
// which is never enough to fill automatically — a supplier ships repeatedly — so
// the quantity column carries the tell and the operator makes the call.
const QTY_MARK = { ok: ['✓', 'qty matches the invoice'], mismatch: ['⚠', 'qty differs from the invoice'] }

function LrCandidates({ info, busy, onLink, onClose }) {
  const rows = info.rows || []
  // The transport block sits mid-page, so a panel appended under it lands
  // off-screen — telling the user rows are "listed below" and showing them
  // nothing. Bring it to them instead.
  const box = useRef(null)
  useEffect(() => { box.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }, [info])
  return (
    <div className="lrcands" ref={box}>
      <div className="lrcands-head">
        <b>{rows.length ? `Pick this invoice's consignment — ${rows.length} likely row(s) in the register` : 'Nothing in the register to link'}</b>
        <button className="lrcands-x" title="Dismiss" onClick={onClose}>×</button>
      </div>
      <div className="lrcands-why">{info.detail}</div>
      {rows.length ? (
        <div className="lrcands-scroll">
          <table className="lrcands-tbl">
            <thead>
              <tr><th>LR No</th><th>LR Date</th><th>Transport</th>
                <th>Register invoice</th><th>Qty</th><th>Item</th><th>Fills</th><th /></tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const [mark, tip] = QTY_MARK[c.qty_agrees] || ['', 'qty unknown on one side']
                return (
                  <tr key={c.id}>
                    <td><b>{c.lr_no || '—'}</b></td>
                    <td>{fmtDate(c.lr_date)}</td>
                    <td>{c.transport || '—'}</td>
                    <td>{c.inv_no || '—'}{c.inv_date ? ` · ${fmtDate(c.inv_date)}` : ''}</td>
                    <td title={tip} className={'qty-' + c.qty_agrees}>{c.qty ?? '—'} {mark}</td>
                    <td>{c.item || '—'}</td>
                    <td className="small">{c.would_fill?.length ? c.would_fill.join(', ') : 'nothing blank'}</td>
                    <td>
                      <button className="btn" style={{ padding: '2px 9px' }} disabled={busy}
                        title={c.already_linked ? 'This row is already linked to another invoice' : 'Link this consignment to this invoice'}
                        onClick={() => onLink(c.id)}>{c.already_linked ? 'relink' : 'link'}</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

// ---------- review panel ----------
//
// Four tabs, one open at a time, laid out horizontally above the fields. The
// screen used to be seven stacked panels: reviewing a bill meant scrolling past
// six of them to reach the seventh, and the invoice image beside it scrolled out
// of reach on the way — which is the one thing a review screen cannot afford,
// since every field on it is being checked against that image.
//
// `paths` is what each tab OWNS, and it earns its keep twice: it counts the
// fields inside a closed tab that are flagged for review (a hidden warning is a
// warning that does not exist), and it is the single place that says where a
// field lives, so a field added to a panel cannot go uncounted.
const REVIEW_TABS = [
  { key: 'party', label: 'Supplier & Buyer', paths: ['supplier.', 'buyer.'],
    hint: 'Who sold it and who is billed — including the supplier’s bank' },
  { key: 'invoice', label: 'Invoice & Transport', paths: ['invoice.'],
    hint: 'Invoice numbers and dates, e-invoice, e-way bill and the consignment' },
  { key: 'money', label: 'Line Items, Taxes & Totals', paths: ['line_items', 'taxes.', 'totals.'],
    hint: 'The goods and the arithmetic — one calculation, one panel' },
  { key: 'grn', label: 'GRN & Notes', paths: ['meta.'],
    hint: 'The handwritten GRN number, who received it, and anything else' },
]

function Review({ docId, onSaved, onCreateGrn, toast }) {
  const [doc, setDoc] = useState(null)
  const [data, setData] = useState(null)
  const [flags, setFlags] = useState({})
  const [warnings, setWarnings] = useState([])
  const [train, setTrain] = useState(true)
  const [saving, setSaving] = useState(false)
  const [lrCands, setLrCands] = useState(null)   // register rows offered to link
  // which panel is open. Reset per document below: opening the next invoice on
  // the tab you happened to leave the last one on is how a field gets skipped.
  const [tab, setTab] = useState('party')
  // the invoice photograph, foldable. Remembered across documents and sessions
  // (useMinimized persists it) — how much room someone wants for the image is a
  // standing preference about how they work, not a per-invoice decision.
  const [imgOpen, toggleImg] = useMinimized('rev.image', true)

  // A document with no extraction is not a document still loading. Reading a
  // dense two-page invoice can outlast the request that started it, and what is
  // left behind is a stored, readable document with nothing attached — which
  // showed "Loading…" for ever, because the screen had no way to tell the two
  // apart.
  const [unread, setUnread] = useState(false)
  const [reading, setReading] = useState(false)

  useEffect(() => {
    if (!docId) return
    setTab('party'); setUnread(false)
    api.getDocument(docId).then((d) => {
      setDoc(d.document)
      setUnread(!d.extraction)
      setData(structuredClone(d.extraction?.data || {}))
      setFlags(d.extraction?.field_flags || {})
      setWarnings(d.extraction?.warnings || [])
    })
  }, [docId])

  // Two documents with the same invoice number are one bill uploaded a page at
  // a time — the ordinary result of not knowing the upload takes both at once.
  // Neither half reconciles, because the totals are printed on the last page
  // and belong to lines the other document holds. Worth spotting for them
  // rather than leaving two plausible-looking invoices in the list.
  const [twin, setTwin] = useState(null)
  const [merging, setMerging] = useState(false)
  const invNo = data?.invoice?.number
  useEffect(() => {
    if (!docId || !invNo) { setTwin(null); return }
    let live = true
    api.listDocuments().then((all) => {
      if (!live) return
      const norm = (v) => String(v || '').replace(/\s+/g, '').toUpperCase()
      setTwin(all.find((d) => d.id !== docId && d.status !== 'posted'
        && norm(d.invoice_number) === norm(invNo)) || null)
    }).catch(() => {})
    return () => { live = false }
  }, [docId, invNo])

  const mergeTwin = async () => {
    if (!twin) return
    if (!window.confirm(
      `Fold "${twin.filename}" into this document?

`
      + 'Its pages are added here in page order and the whole invoice is read '
      + 'again. That document is then deleted, along with any draft GRN built '
      + 'from it.')) return
    setMerging(true)
    try {
      const d = await api.mergeDocuments(docId, twin.id)
      setDoc(d.document); setTwin(null)
      // The pages are joined whether or not the re-read finished. When it did
      // not, this drops into the same "uploaded but never read" screen, which
      // already offers to read it — rather than reporting a failure for work
      // that actually succeeded.
      setUnread(!d.extraction)
      setData(structuredClone(d.extraction?.data || {}))
      setFlags(d.extraction?.field_flags || {})
      setWarnings(d.extraction?.warnings || [])
      onSaved && onSaved()
      toast(d.extraction
        ? `Merged — ${d.merged?.pages} pages, ${d.extraction?.data?.line_items?.length || 0} lines`
        : `Merged ${d.merged?.pages} pages — reading timed out, press “Read it now”`,
        d.extraction ? 'ok' : 'err')
    } catch (e) {
      toast(e.detail || 'Could not merge them', 'err')
    }
    setMerging(false)
  }

  const readAgain = async () => {
    setReading(true)
    try {
      const d = await api.reExtract(docId)
      setDoc(d.document); setUnread(false)
      setData(structuredClone(d.extraction?.data || {}))
      setFlags(d.extraction?.field_flags || {})
      setWarnings(d.extraction?.warnings || [])
      onSaved && onSaved()
      toast(`Read ${d.extraction?.data?.line_items?.length || 0} lines`, 'ok')
    } catch (e) {
      toast(e.detail || 'Could not read it — try again', 'err')
    }
    setReading(false)
  }

  if (!docId) return <div className="empty">Select a document from the left, or upload a new invoice to extract it.</div>
  if (unread) return (
    <div className="empty" style={{ margin: 'auto', maxWidth: 460, lineHeight: 1.7 }}>
      <div style={{ fontSize: 26, marginBottom: 6 }}>📄</div>
      <b>{doc?.filename || 'This document'}</b> was uploaded but never read.<br />
      Reading a long invoice can take longer than the upload was given.
      The pages are stored, so it can be read now without uploading them again.
      <div style={{ marginTop: 14 }}>
        <button className="btn primary" disabled={reading} onClick={readAgain}>
          {reading ? 'Reading — this can take a minute…' : 'Read it now'}</button>
      </div>
    </div>
  )
  if (!data) return <div className="empty">Loading…</div>

  const setPath = (obj, path, val) => {
    const c = structuredClone(obj); const ks = path.split('.'); let o = c
    for (let i = 0; i < ks.length - 1; i++) o = o[ks[i]] = o[ks[i]] || {}
    o[ks[ks.length - 1]] = val; return c
  }
  const get = (path) => path.split('.').reduce((o, k) => (o == null ? o : o[k]), data)
  // fields the LR register supplied (not read off the page) — badged, so nobody
  // trusts a docket number to be printed on the invoice when it wasn't. The badge
  // drops as soon as the value is edited: it vouches for a value, not a field.
  const lrFilled = data.meta?.lr_source?.filled || {}
  const fromLr = (path) => {
    if (!path.startsWith('invoice.')) return null
    const k = path.slice('invoice.'.length)
    return k in lrFilled && String(get(path) ?? '') === String(lrFilled[k] ?? '')
      ? 'LR register' : null
  }
  const f = (path, label, opts = {}) => (
    <Field label={label} value={get(path)} flagged={!!flags[path]} wide={opts.wide}
      source={fromLr(path)} date={opts.date} calc={opts.calc} note={opts.note}
      onChange={(v) => setData(setPath(data, path, opts.raw ? v : num(v)))} />
  )
  // A tax field, with its opposite number kept in step: type a rate and the
  // amount appears, type an amount and the rate appears. The totals follow both,
  // so the foot of the invoice is never left disagreeing with the block above it.
  const ft = (key, label, opts = {}) => (
    <Field label={label} value={tax[key]} flagged={!!flags['taxes.' + key]}
      calc={opts.calc} note={opts.note}
      onChange={(v) => {
        const taxes = recalcTaxes({ ...tax, [key]: num(v) }, taxBase(data), key)
        const next = { ...data, taxes }
        setData({ ...next, totals: recalcTotals(next) })
      }} />
  )
  // Recompute the whole foot from the lines up. Explicit, and never automatic on
  // load: this screen reviews what was read off a photograph, and an arrival that
  // quietly restated its own figures would hide the very disagreement the checks
  // above are reporting.
  const recalcAll = () => {
    const items = (data.line_items || []).map((it) => recalcLine(it, 'qty'))
    let next = { ...data, line_items: items }
    const base = taxBase(next)
    let taxes = { ...(next.taxes || {}) }
    for (const [rk, ak] of TAX_PAIRS) {
      if (nf(taxes[rk])) taxes = recalcTaxes(taxes, base, rk)
      else if (nf(taxes[ak])) taxes = recalcTaxes(taxes, base, ak)
    }
    next = { ...next, taxes }
    setData({ ...next, totals: recalcTotals(next) })
    toast('⟲ Recalculated from the lines up — check it against the image before saving', 'ok')
  }
  // a date read off the page: same review flags and LR-source badge as any other
  // field, but picked from a calendar instead of retyped
  const fd = (path, label) => f(path, label, { raw: true, date: true })

  // The register is often photographed after the invoices it covers, so the
  // automatic pass on upload can find nothing. This re-runs it on demand, and
  // when no invoice/LR number lines up it offers this supplier's register rows
  // to pick from rather than dead-ending on "no match".
  const applyLrResult = (r) => {
    if (r.data) setData(structuredClone(r.data))
    if (r.notes?.length) setWarnings((w) => [...w, ...r.notes])
  }
  const fetchFromLr = async () => {
    setSaving(true)
    try {
      const r = await api.fetchTransport(docId)
      const n = Object.keys(r.filled || {}).length
      if (n) {
        applyLrResult(r); setLrCands(null)
        toast(`🔗 Filled ${n} transport field(s) from the LR register`, 'ok')
      } else {
        if (r.candidates) setLrCands({ detail: r.detail, rows: r.candidates })
        toast(r.detail || 'Nothing to fill from the LR register', 'warn')
      }
    } catch (e) { toast('LR fetch failed: ' + (e.detail || e.message), 'err') }
    setSaving(false)
  }
  const linkLrRow = async (lrEntryId) => {
    setSaving(true)
    try {
      const r = await api.fetchTransport(docId, lrEntryId)
      applyLrResult(r); setLrCands(null)
      const n = Object.keys(r.filled || {}).length
      toast(n ? `🔗 Linked · filled ${n} field(s)` : 'Linked — nothing blank left to fill',
        n ? 'ok' : 'warn')
    } catch (e) { toast('Link failed: ' + (e.detail || e.message), 'err') }
    setSaving(false)
  }

  const sup = data.supplier || {}, inv = data.invoice || {}, tax = data.taxes || {}, tot = data.totals || {}

  const save = async () => {
    setSaving(true)
    try {
      const res = await api.confirm(docId, data, train)
      toast(res.trained_profile ? '✓ Saved & supplier format trained' : '✓ Saved', 'ok')
      onSaved()
      const d = await api.getDocument(docId); setDoc(d.document)
    } catch (e) { toast('Save failed: ' + (e.detail || e.message), 'err') }
    setSaving(false)
  }

  const createGrn = async () => {
    setSaving(true)
    try {
      // If not confirmed yet, persist the current edits first (train per checkbox),
      // so one click on Create GRN always works from a freshly-extracted document.
      if (!doc || (doc.status !== 'confirmed' && doc.status !== 'posted')) {
        await api.confirm(docId, data, train)
        onSaved()
      }
      const grn = await api.buildGrn(docId)
      toast(`GRN draft created · ${grn.new_products} new product(s)`, 'ok')
      onCreateGrn(grn.id)
    } catch (e) { toast('Could not create GRN: ' + (e.detail || e.message), 'err') }
    setSaving(false)
  }

  return (
    <div className="main">
      {/* The image and the fields compete for one screen, and which one needs the
          room changes with the job: checking a GSTIN against a photograph wants
          the image, keying a twelve-size breakdown wants the table. So it folds
          away — to the same 36mm rail the lists use, carrying its own label,
          because the way back has to be visible from where the image went. */}
      <div className={'viewer' + (imgOpen ? '' : ' collapsed')}>
        {imgOpen ? (
          <>
            <button className="sidehide" onClick={toggleImg}
              title="Hide the invoice image — the screen keeps this setting">«</button>
            <div className="viewerscroll">
              <img src={api.imageUrl(docId, doc?.content_hash)} alt="invoice" />
            </div>
          </>
        ) : (
          <button className="siderail" onClick={toggleImg} title="Show the invoice image">
            <span className="chev" aria-hidden="true">»</span>
            <span className="raillabel">Invoice image</span>
          </button>
        )}
      </div>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div className="editor">
          {twin && (
            <div className="warnbox" style={{ marginBottom: 20 }}>
              <h4>Another document carries this invoice number</h4>
              <div className="small" style={{ lineHeight: 1.7 }}>
                <b>{twin.filename}</b> is also <b>#{twin.invoice_number}</b>
                {twin.grand_total != null && <> at ₹ {money(twin.grand_total)}</>}.
                A bill printed on more than one page uploaded a page at a time
                gives two documents like this, and neither adds up on its own —
                the totals are on the last page.
                <div style={{ marginTop: 10 }}>
                  <button className="btn primary" disabled={merging} onClick={mergeTwin}>
                    {merging ? 'Merging and re-reading…' : 'Merge them into one invoice'}</button>
                </div>
              </div>
            </div>
          )}
          <div className={'warnbox ' + (warnings.filter((w)=>!w.includes('OCR')&&!w.includes('vision')&&!w.includes('sample')).length ? '' : 'clean')} style={{ marginBottom: 20 }}>
            <h4>{warnings.length ? `${warnings.length} check(s)` : 'All internal checks passed'}
              {doc && <span className="small" style={{ float: 'right' }}>via {doc && data.template_key ? '' : ''}extraction · confidence <b className={'conf ' + confClass(doc?.confidence)}>{doc ? Math.round(doc.confidence * 100) + '%' : '—'}</b></span>}
            </h4>
            {warnings.length ? <ul>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
          </div>

          {/* Four tabs, one open at a time. Seven stacked panels made the screen a
              long vertical scroll in which the fields being checked and the
              invoice image were rarely on screen together — and a review done by
              scrolling is a review done from memory.
              The risk this trades for is real and handled: a flagged field inside
              a closed tab is invisible, so each tab carries the count of its own
              fields needing review, and the checks box above stays put. */}
          <div className="revtabs" role="tablist">
            {REVIEW_TABS.map((t) => {
              const n = Object.keys(flags).filter(
                (k) => t.paths.some((p) => k.startsWith(p))).length
              return (
                <button key={t.key} role="tab" aria-selected={tab === t.key}
                  className={'revtab' + (tab === t.key ? ' on' : '') + (n ? ' flagged' : '')}
                  onClick={() => setTab(t.key)}
                  title={n ? `${n} field(s) here need review` : t.hint}>
                  {t.label}
                  {n ? <span className="revtab-n" title={`${n} field(s) need review`}>⚠ {n}</span> : null}
                </button>
              )
            })}
          </div>

          {tab === 'party' && (
          <div className="section">
            <h4>Supplier &amp; Buyer</h4>
            <h5>Supplier</h5>
            <div className="grid">
              {f('supplier.name', 'Name', { raw: true })}
              {f('supplier.legal_name', 'Legal / Trading Name', { raw: true })}
              {f('supplier.gstin', 'GSTIN', { raw: true })}
              {f('supplier.pan', 'PAN', { raw: true })}
              {f('supplier.state', 'State', { raw: true })}
              {f('supplier.state_code', 'State Code', { raw: true })}
              {f('supplier.cin', 'CIN', { raw: true })}
              {f('supplier.phone', 'Phone', { raw: true })}
              {f('supplier.email', 'Email', { raw: true })}
              {f('supplier.manufacturer', 'Manufacturer / MFG by', { raw: true })}
              {f('supplier.address', 'Address', { raw: true, wide: true })}
            </div>
            {/* the bank and the buyer are short blocks — side by side they cost
                one screen between them instead of two */}
            <div className="moneysplit">
              <div>
                <h5>Supplier Bank</h5>
                <div className="grid">
                  {f('supplier.bank.name', 'Bank Name', { raw: true })}
                  {f('supplier.bank.account_no', 'Account No', { raw: true })}
                  {f('supplier.bank.ifsc', 'IFSC', { raw: true })}
                  {f('supplier.bank.branch', 'Branch', { raw: true })}
                </div>
              </div>
              <div>
                <h5>Buyer (bill to)</h5>
                <div className="grid">
                  {f('buyer.name', 'Name', { raw: true })}
                  {f('buyer.gstin', 'GSTIN', { raw: true })}
                  {f('buyer.state', 'State', { raw: true })}
                  {f('buyer.address', 'Address', { raw: true, wide: true })}
                </div>
              </div>
            </div>
          </div>
          )}

          {tab === 'invoice' && (
          <div className="section">
            <h4>Invoice &amp; Transport
              <button className="h4btn" disabled={saving} onClick={fetchFromLr}
                title="Fill LR No, LR Date, Transporter and Book City from the matching LR register row">
                ⟲ fetch from LR register</button>
            </h4>
            <h5>Invoice</h5>
            <div className="grid">
              {f('invoice.number', 'Invoice No', { raw: true })}
              {fd('invoice.date', 'Date')}
              {fd('invoice.due_date', 'Due Date')}
              {f('invoice.challan_no', 'Challan / DC No', { raw: true })}
              {f('invoice.order_no', 'Order No', { raw: true })}
              {fd('invoice.order_date', 'Order Date')}
              {f('invoice.reference_no', 'Reference No', { raw: true })}
              {f('invoice.terms', 'Payment Terms', { raw: true })}
              {f('invoice.agent', 'Agent', { raw: true })}
              {f('invoice.broker', 'Broker', { raw: true })}
            </div>
            <h5>E-invoice &amp; Transport</h5>
            <div className="grid">
              {f('invoice.irn', 'IRN', { raw: true, wide: true })}
              {f('invoice.ack_no', 'ACK No', { raw: true })}
              {fd('invoice.irn_date', 'IRN Date')}
              {f('invoice.eway_bill', 'E-way Bill', { raw: true })}
              {f('invoice.tran_id', 'Transport / EWB Tran ID', { raw: true })}
              {f('invoice.lr_no', 'LR No', { raw: true })}
              {fd('invoice.lr_date', 'LR Date')}
              {f('invoice.transporter', 'Transporter', { raw: true })}
              {f('invoice.destination', 'Destination', { raw: true })}
              {f('invoice.book_city', 'Book City', { raw: true })}
              {f('invoice.delivery_note', 'Delivery Note', { raw: true })}
            </div>
            {lrCands && <LrCandidates info={lrCands} busy={saving}
              onLink={linkLrRow} onClose={() => setLrCands(null)} />}
          </div>
          )}

          {/* Lines, taxes and totals are ONE calculation, so they are one panel.
              Split across three collapsed accordions, the arithmetic that ties
              them together was invisible: you could not see a rate change reach
              the grand total without opening two more sections, and a tax typed
              against the wrong taxable value looked perfectly fine. */}
          {tab === 'money' && (
          <div className="section money">
            <h4>Line Items, Taxes &amp; Totals
              <span className="calcbase" title="The figure every tax rate below is charged on">
                taxable base ₹ {money(taxBase(data))}</span>
              <button className="h4btn" onClick={recalcAll}
                title="Recompute rates, taxes and totals from the line items up. Never runs on its own — check the result against the image.">
                ⟲ recalculate</button>
            </h4>
            <LineItems items={data.line_items || []}
              setItems={(it) => {
                const next = { ...data, line_items: it }
                setData({ ...next, totals: recalcTotals(next) })
              }} />

            <div className="moneysplit">
              <div>
                <h5>Taxes</h5>
                <div className="grid">
                  {ft('cgst_rate', 'CGST %', { calc: 'Half of an intra-state levy — SGST follows it.' })}
                  {ft('cgst_amount', 'CGST Amount', { calc: 'CGST % × taxable base. Type an amount to set the %.' })}
                  {ft('sgst_rate', 'SGST %', { calc: 'The other half — mirrors CGST unless you set it apart.' })}
                  {ft('sgst_amount', 'SGST Amount', { calc: 'SGST % × taxable base. Type an amount to set the %.' })}
                  {ft('igst_rate', 'IGST %', { calc: 'Inter-state levy, charged instead of CGST + SGST.' })}
                  {ft('igst_amount', 'IGST Amount', { calc: 'IGST % × taxable base. Type an amount to set the %.' })}
                  {ft('special_discount_pct', 'Special Discount %', { calc: 'What the discount below comes to as a percentage.' })}
                  {ft('special_discount', 'Special Discount', { calc: '% × taxable base. Type an amount to see its %. Deducted from the grand total.' })}
                  {ft('other_charges', 'Other Charges')}
                  {ft('freight', 'Freight')}
                  {ft('round_off', 'Round Off')}
                  {ft('tds_amount', 'TDS', { note: 'Deducted when the bill is paid, not from the invoice total.' })}
                </div>
              </div>
              <div>
                <h5>Totals</h5>
                <div className="grid">
                  {f('totals.total_qty', 'Total Qty', { calc: 'Σ of the Qty column.' })}
                  {f('totals.sub_total', 'Sub Total', { calc: 'Σ of the Amount column.' })}
                  {f('totals.taxable_total', 'Taxable Total', { calc: 'Σ of the Taxable column — what the tax rates are charged on.' })}
                  {f('totals.tax_total', 'Tax Total', { calc: 'CGST + SGST + IGST.' })}
                  {f('totals.grand_total', 'Grand Total', { calc: 'Taxable + tax + charges + freight − special discount + round off.' })}
                  {f('totals.amount_in_words', 'Amount in Words', { raw: true, wide: true })}
                </div>
              </div>
            </div>
          </div>
          )}

          {tab === 'grn' && (
          <div className="section">
            <h4>GRN &amp; Notes</h4>
            <div className="grid">
              {f('meta.grn_no', 'GRN No', { raw: true })}
              {fd('meta.grn_date', 'GRN Date')}
              {f('meta.received_by', 'Received By', { raw: true })}
              {f('meta.notes', 'Notes', { raw: true, wide: true })}
            </div>
          </div>
          )}
        </div>

        <div className="actionbar">
          <label className="chk"><input type="checkbox" checked={train} onChange={(e) => setTrain(e.target.checked)} />
            Train this supplier's format on save</label>
          <div className="spacer" />
          <a className="btn" href={api.exportUrl(docId, 'csv')} target="_blank" rel="noreferrer">Export CSV</a>
          <button className="btn primary" disabled={saving} onClick={save}>
            {saving ? 'Saving…' : train ? 'Confirm & Train' : 'Confirm'}</button>
          <button className="btn" style={{ borderColor: 'var(--accent-2)', color: 'var(--accent-2)' }}
            disabled={saving}
            title="Confirms the extraction (and trains the supplier if ticked), then builds a GRN"
            onClick={createGrn}>Create GRN →</button>
        </div>
      </div>
    </div>
  )
}

// ---------- suppliers ----------
function Suppliers({ toast }) {
  const [list, setList] = useState([])
  const [sel, setSel] = useState(null)
  const [detail, setDetail] = useState(null)
  const [q, setQ] = useState('')
  useEffect(() => { api.listSuppliers().then(setList) }, [])
  useEffect(() => { if (sel) api.getSupplier(sel).then(setDetail) }, [sel])
  const supShown = list.filter((s) => matches(s, q, ['name', 'gstin', 'state']))
  const supPage = usePaged(supShown, 50)
  return (
    <div className="body">
      <Sidebar id="suppliers" label="Suppliers">
        <div className="head"><h3>Suppliers · {list.length}</h3></div>
        <SearchBox value={q} onChange={setQ} placeholder="Search name, GSTIN, state…" />
        <div className="list">
          {supPage.slice.map((s) => (
            <div key={s.id} className={'sup-row' + (sel === s.id ? ' sel' : '')} onClick={() => setSel(s.id)}>
              <div className="t">{s.name}</div>
              <div className="m">
                <span className={'trainflag ' + (s.has_profile ? 'yes' : 'no')}>{s.has_profile ? `trained v· ${s.profile_samples} sample(s)` : 'not trained'}</span>
                <span>{s.document_count} doc(s)</span>
              </div>
            </div>
          ))}
        </div>
        <Pager {...supPage} noun="supplier" />
      </Sidebar>
      {detail ? (
        <div className="sup-detail">
          <h2 style={{ marginTop: 0 }}>{detail.name}</h2>
          <div className="kv">
            <div className="k">GSTIN</div><div className="mono">{detail.gstin || '—'}</div>
            <div className="k">State</div><div>{detail.state || '—'} ({detail.state_code || '—'})</div>
            <div className="k">Address</div><div>{detail.address || '—'}</div>
            <div className="k">Bank</div><div>{detail.bank?.name ? `${detail.bank.name} · A/C ${detail.bank.account_no} · ${detail.bank.ifsc}` : '—'}</div>
          </div>
          <h4 style={{ marginTop: 24 }}>Learned format {detail.profile ? `(v${detail.profile.version})` : ''}</h4>
          {detail.profile ? (
            <div className="kv">
              <div className="k">Tax mode</div><div>{detail.profile.tax_mode}</div>
              <div className="k">Default rates</div><div className="mono">{JSON.stringify(detail.profile.default_tax_rates)}</div>
              <div className="k">Has TDS</div><div>{detail.profile.has_tds ? 'yes' : 'no'}</div>
              <div className="k">Default UOM</div><div>{detail.profile.uom_default}</div>
              <div className="k">Detect by GSTIN</div><div className="mono">{detail.profile.detect_gstin || '—'}</div>
              <div className="k">Confirmed samples</div><div>{detail.profile.sample_count}</div>
            </div>
          ) : <p className="small">No profile yet.</p>}
        </div>
      ) : <div className="empty">Select a supplier to see its learned format.</div>}
    </div>
  )
}

// ---------- purchases / GRN ----------
// The attributes that make a breakdown row its own stock item — same set the phone
// detail form and the QR payload carry. [key, label, column width]
// The stock master's attribute columns (Attributes Reference.xlsx) less PRODUCT,
// which is the category and has its own cell. Brand leads: it is what the eye
// reaches for on a rack, and it is identity — an ESSA t-shirt and a YUVA t-shirt
// in the same size are two stock items. Every one of these becomes part of the
// variant's identity, its label and its QR.
const SPLIT_ATTRS = [
  ['brand', 'Brand', 105], ['size', 'Size', 90], ['color', 'Colour', 100],
  ['material', 'Material', 100], ['pattern', 'Pattern', 100], ['fit', 'Fit', 95],
  ['style', 'Style', 105], ['sleeve', 'Sleeve', 90], ['product_type', 'Type', 100],
  ['design_no', 'Design No', 95],
]
//: The one figure that is genuinely PER ROW: how many of that variant arrived.
//: It is the whole point of the breakdown, so it stays in the grid.
const SPLIT_QTY = [['qty', 'Qty', 70]]
// Money is NOT here. It used to be four more columns per row — rate, MRP,
// discount %, sale price — which made a grid of ten attributes into a 1,700px
// scroll, and every row of it carried the same four figures anyway: a bundle
// broken into sizes is one product at one price in several sizes. It lives on
// the LINE now, beside the rate and amount it is worked out against, and a
// variant with none of its own takes the line's when the product is created.
const blankVariant = (rate, category) => ({ ...Object.fromEntries(SPLIT_ATTRS.map(([k]) => [k, ''])),
  category: category || '', qty: '', rate: rate ?? '', mrp: '', sale_price: '', sale_discount_pct: '' })
// quantities are floats — compare with the same tolerance the server posts with
const sameQty = (a, b) => Math.abs((+a || 0) - (+b || 0)) < 0.001
const variantLabel = (r) => SPLIT_ATTRS.map(([k]) => r[k]).filter(Boolean).join(' · ')

// --- shortage entry: what was billed and wasn't in the box ---
// Recorded on the GRN before it posts, because that is the last moment anyone can
// know it. Afterwards stock says 40, the invoice says 50, and nothing on the
// system remembers that the two ever disagreed. The phone app is where this is
// normally keyed — the person opening the cartons is the only one who can — and
// this is the desk-side mirror of the same endpoints.
const SHORT_KINDS = [
  ['short', 'Short', 'billed but not in the box'],
  ['damaged', 'Damaged', 'arrived unusable, rejected at the dock'],
  ['excess', 'Excess', 'more arrived than was billed'],
]
const blankShortage = (qty) => ({ kind: 'short', qty: qty != null ? String(qty) : '', variant: '', reason: '', note: '' })
// what the breakdown still has to reach: what ARRIVED, not what was billed
const receivedQty = (l) => +(l.received_qty != null ? l.received_qty : l.qty) || 0

function Purchases({ selId, setSelId, toast }) {
  const [list, setList] = useState([])
  const [grn, setGrn] = useState(null)
  const [q, setQ] = useState('')
  const opts = useProductOptions()                 // attribute option lists
  const [cats, setCats] = useState([])             // category master names
  const [splitFor, setSplitFor] = useState(null)   // line id whose breakdown is open
  const [srows, setSrows] = useState([])           // editable variant rows
  const [runSpec, setRunSpec] = useState('')       // "28-2-38" for the open breakdown
  const [sfill, setSfill] = useState({})           // in-progress "apply to every row" values
  const [shortFor, setShortFor] = useState(null)   // line id whose shortage is open
  const [shrows, setShrows] = useState([])         // editable shortage rows
  const [shortOpts, setShortOpts] = useState({ reasons: [] })
  const [units, setUnits] = useState({ types: [], rules: [] })   // unit master
  const refresh = useCallback(() => api.listPurchases().then(setList), [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => { if (selId) api.getPurchase(selId).then(setGrn); else setGrn(null) }, [selId])
  useEffect(() => { api.unitTypes().then(setUnits).catch(() => {}) }, [])
  useEffect(() => { api.categories().then((c) => setCats((c.items || []).map((i) => i.name))).catch(() => {}) }, [])
  useEffect(() => { api.shortageOptions().then(setShortOpts).catch(() => {}) }, [])
  useEffect(() => { setSplitFor(null); setSrows([]); setShortFor(null); setShrows([]) }, [selId])

  const reload = async () => { const g = await api.getPurchase(selId); setGrn(g); refresh(); return g }

  const post = async () => {
    const sh = grn?.shortages || {}
    // stated at the moment it stops being editable: posting is what turns "40
    // arrived" into the stock figure and "10 short" into a claim
    if (sh.claimable_qty && !window.confirm(
      `Post this GRN?\n\n`
      + `⚠ ${sh.claimable_qty} unit(s) recorded short or damaged (₹ ${money(sh.claimable_value)}).\n\n`
      + `They stay OUT of stock and become a claim against the supplier — raise it as a debit note in Returns.\n`
      + `The invoice keeps its own quantity, so the payables side still reconciles against the supplier's document.`)) return
    try {
      const r = await api.postGrn(selId)
      const sizes = r.size_rows ? ` · ${r.size_rows} size row(s)` : ''
      const short = r.short_qty ? ` · ${r.short_qty} short (₹ ${money(r.short_value)} to claim)` : ''
      // a receipt that turned dozens into pairs has to say so — otherwise the
      // stock figure looks like it lost half of what was billed
      const conv = r.converted?.length ? `\n${r.converted.join('\n')}` : ''
      const labels = r.pieces ? ` · ${r.pieces} QR label(s)` : ''
      toast(`✓ Posted to inventory · ${r.products_created} new, ${r.products_updated} updated${sizes}${labels}${short}${conv}`, 'ok')
      reload()
    } catch (e) { toast('Post failed: ' + (e.detail || e.message), 'err') }
  }

  // --- shortage entry ---
  const openShortage = (l, preset) => {
    setSplitFor(null)
    setShortFor(l.id)
    setShrows(l.shortages?.length
      ? l.shortages.map((s) => ({ kind: s.kind, qty: String(s.qty), variant: s.variant || '',
        reason: s.reason || '', note: s.note || '' }))
      : [blankShortage(preset)])
  }
  const updShrow = (i, k, v) => setShrows(shrows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  const saveShortage = async (l, rows) => {
    try {
      await api.setLineShortages(l.id, rows)
      setShortFor(null); setShrows([])
      await reload()
      const missing = round3(rows.filter((r) => r.kind !== 'excess').reduce((n, r) => n + (+r.qty || 0), 0))
      const extra = round3(rows.filter((r) => r.kind === 'excess').reduce((n, r) => n + (+r.qty || 0), 0))
      toast(rows.length
        ? `✓ Recorded — ${[missing && `${missing} short of ${l.qty} billed`, extra && `${extra} extra`]
            .filter(Boolean).join(', ')}`
        : '✓ Shortage cleared — the whole billed quantity is expected again', 'ok')
    } catch (e) { toast(e.detail || 'Could not record the shortage', 'err') }
  }
  const waive = async (s) => {
    const why = window.prompt('Accept this shortage instead of claiming it?\n\n'
      + 'It stays on the record — this only stops it being offered on the next debit note.\n\nWhy?', 'supplier sending balance')
    if (why === null) return
    try { await api.waiveShortage(s.id, why); await reload(); toast('✓ Shortage waived', 'ok') }
    catch (e) { toast(e.detail || 'Could not waive it', 'err') }
  }
  const unwaive = async (s) => {
    try { await api.unwaiveShortage(s.id); await reload(); toast('✓ Back in play — claimable again', 'ok') }
    catch (e) { toast(e.detail || 'Could not reopen it', 'err') }
  }

  // --- unpost: reverse a posted GRN so it can be corrected and posted again ---
  const unpost = async () => {
    let blockers = []
    try { blockers = (await api.unpostCheck(selId)).blockers || [] } catch { /* server will re-check */ }
    if (blockers.length) {
      toast('Can’t unpost — ' + blockers.join('; '), 'err')
      return
    }
    if (!window.confirm(
      'Unpost this GRN?\n\n'
      + '• the stock it added is reversed (the ledger keeps both rows)\n'
      + '• weighted-average cost is recomputed without it\n'
      + '• products it created, that nothing else has touched, are removed\n'
      + '• the GRN goes back to draft so you can correct it and post again')) return
    try {
      const r = await api.unpostGrn(selId)
      const gone = r.products_removed?.length ? ` · ${r.products_removed.length} product(s) removed` : ''
      const kept = r.products_kept?.length ? ` · ${r.products_kept.length} kept at zero stock` : ''
      toast(`✓ Unposted · ${r.movements_reversed} movement(s), ${r.qty_reversed} units reversed${gone}${kept}`, 'ok')
      reload()
    } catch (e) { toast(e.detail || 'Unpost failed', 'err') }
  }
  const removeGrn = async () => {
    if (!window.confirm('Delete this draft GRN?\n\nThe invoice document stays — you can build a fresh GRN from it.')) return
    try {
      await api.deletePurchase(selId)
      toast('✓ GRN deleted', 'ok')
      setSelId(null); setGrn(null); refresh()
    } catch (e) { toast(e.detail || 'Could not delete the GRN', 'err') }
  }

  // --- attribute breakdown: the supplier bills a bundle, the warehouse enters
  //     what actually arrived (size / colour / material / … per row) ---
  const openSplit = (l) => {
    setSplitFor(l.id)
    setRunSpec('')
    setSfill({})
    const from = (s) => {
      const r = blankVariant(l.rate)
      Object.keys(r).forEach((k) => { if (s[k] != null) r[k] = s[k] })
      return r
    }
    if (l.splits.length) { setSrows(l.splits.map(from)); return }
    // A new row starts from what the LINE already says about itself: its category
    // (or the mapping it would get), and the brand and design number that came off
    // the invoice. The common case — one garment, one brand, one design, several
    // sizes — should need none of that typed twice, and a brand re-keyed per size
    // is a brand that ends up spelled two ways.
    const blank = () => ({
      ...blankVariant(l.rate, l.category || l.category_suggestion?.best),
      ...(l.brand ? { brand: l.brand } : {}),
      ...(l.design_no ? { design_no: l.design_no } : {}),
    })
    // The supplier printed the mix in the size column — "30:2, 32:4, 34:4, 36:2"
    // is the count already done by whoever packed the carton. Re-keying it here
    // is how it gets keyed wrong, so it arrives filled in and the operator checks
    // it rather than types it. Nothing is saved until they press Save.
    const run = l.size_breakdown
    if (run?.rows?.length) {
      setSrows(run.rows.map((r) => ({
        ...blank(), size: String(r.size), qty: String(r.qty),
        mrp: l.mrp ?? '', sale_price: l.sale_price ?? '',
        sale_discount_pct: l.sale_discount_pct ?? '',
      })))
      toast(run.matches
        ? `↳ ${run.rows.length} sizes read off the invoice — ${run.total} of ${receivedQty(l)}, adds up ✓`
        : `↳ ${run.rows.length} sizes read off the invoice — ${run.why || 'check the quantities'}`,
        run.matches ? 'ok' : 'warn')
      return
    }
    setSrows([blank()])
  }
  const setLineCat = async (l, name) => {
    try { await api.editLine(l.id, { category: name }); await reload() }
    catch (e) { toast(e.detail || 'Could not set the category', 'err') }
  }
  // Retail pricing, on the LINE beside the rate and the amount it belongs with.
  // It used to be four columns repeated down the breakdown grid, which is a
  // strange place for it: a bundle broken into sizes is one product at one price
  // in several sizes, and the server has always read a blank on a variant as
  // "the line's" anyway. One place to type it, one place to correct it.
  //
  // The three move each other exactly as they do on the invoice review screen —
  // MRP − Discount % = Sale price, in whichever direction was typed — so the
  // whole trio is worked out here and sent together.
  //: one figure typed on one line, turned into the whole trio that line should
  //: now carry. Pulled out because Apply all needs exactly the same answer for
  //: sixty lines at once, and two copies of this arithmetic would drift.
  const priceBody = (l, k, v) => {
    const row = recalcSale({ mrp: l.mrp ?? '', sale_price: l.sale_price ?? '',
                             sale_discount_pct: l.sale_discount_pct ?? '', [k]: v }, k)
    const n = (x) => (x === '' || x == null ? null : (Number.isNaN(+x) ? null : +x))
    return { mrp: n(row.mrp), sale_price: n(row.sale_price),
             sale_discount_pct: n(row.sale_discount_pct) }
  }
  const setLinePrice = async (l, k, v) => {
    try {
      await api.editLine(l.id, priceBody(l, k, v))
      await reload()
    } catch (e) { toast(e.detail || 'Could not set the price', 'err') }
  }
  // What one of these IS — piece, pair, dozen. It decides how the billed quantity
  // converts into stock and therefore how many QR labels the receipt produces, so
  // it is set here, on the line, before anything is posted. The choice is
  // remembered against the wording: say "pillow cover = PAIR" once and the next
  // invoice arrives already counted in pairs.
  const setLineUnit = async (l, code) => {
    try {
      const line = await api.editLine(l.id, { unit_type: code })
      await reload()
      toast(code
        ? `✓ Counted in ${code} — ${line.unit?.explain || ''}`
        : '✓ Back to automatic — the unit is read off the description', 'ok')
    } catch (e) { toast(e.detail || 'Could not set the unit', 'err') }
  }
  // MRP − Discount % = Sale price, in whichever direction was typed. Only the
  // three retail fields move each other; the purchase rate and the attributes
  // are left exactly as entered.
  const updSrow = (i, k, v) => setSrows(srows.map((r, j) =>
    (j === i ? recalcSale({ ...r, [k]: v }, k) : r)))
  // Fill a whole attribute column with one value.
  //
  // A bundle broken into six sizes is one garment six times: same brand, same
  // colour, same material, same everything except the size and the count. Typing
  // "Moss" into six rows is the kind of work that gets abandoned halfway, and
  // half-filled is worse than empty here — a variant carrying a colour and its
  // neighbour not carrying one are two different stock items to the server, which
  // compares the WHOLE attribute tuple.
  //
  // On a button rather than as you type, for the reason the invoice grid's buffer
  // columns are: a column that rewrote itself on every keystroke would overwrite
  // rows somebody had already set by hand, and "Mos" is a value on the way to
  // "Moss". Applied, each row still edits normally afterwards.
  //
  // Blank does nothing rather than clearing the column. Clearing nine rows is not
  // something to offer behind the same button as filling them.
  const applySfill = (k) => {
    const v = (sfill[k] || '').trim()
    if (!v) return
    setSrows(srows.map((r) => ({ ...r, [k]: v })))
  }
  const fillAttr = (k, label) => (
    <div className="fillbox text">
      <input list={k === 'category' ? 'essa-cats' : 'essa-opt-' + k}
        value={sfill[k] || ''} placeholder="all"
        title={`Set ${label} on every row of this breakdown`}
        onChange={(e) => setSfill({ ...sfill, [k]: e.target.value })}
        onKeyDown={(e) => { if (e.key === 'Enter') applySfill(k) }} />
      <button className="btn" disabled={!(sfill[k] || '').trim()}
        onClick={() => applySfill(k)}
        title={`Apply ${sfill[k] || '…'} to all ${srows.length} row(s)`}>Apply all</button>
    </div>
  )
  const splitSum = srows.reduce((s, r) => s + (+r.qty || 0), 0)
  // Fill the grid from the run. Whatever the rows already share stays on them — a
  // bundle in six sizes is one garment six times, and only the size and the count
  // differ — so a colour and a category keyed once are not keyed again.
  const applySizeRun = (l) => {
    const { sizes, why } = parseSizeRun(runSpec)
    if (!sizes.length) { toast(why || 'Write the run as start-step-end — 28-2-38.', 'err'); return }
    const typed = srows.filter((r) => variantLabel(r) || r.qty)
    if (typed.length && !window.confirm(
      `Replace the ${typed.length} row(s) below with the ${sizes.length} sizes of ${runSpec}?\n\n`
      + sizes.join(', '))) return
    const shared = {}
    SPLIT_ATTRS.forEach(([k]) => {
      if (k === 'size') return
      const v = srows.map((r) => r[k]).find(Boolean)
      if (v) shared[k] = v
    })
    const cat = srows.map((r) => r.category).find(Boolean)
      || l.category || l.category_suggestion?.best
    const qtys = spreadQty(receivedQty(l), sizes.length)
    setSrows(sizes.map((size, i) => ({
      ...blankVariant(l.rate, cat), ...shared, size, qty: String(qtys[i]),
    })))
    // more sizes than pieces: the empty ones are left on the grid rather than
    // dropped, because which of them did not come is the operator's to say
    const empty = qtys.some((q) => !q)
    const even = qtys.every((q) => q === qtys[0])
    toast(empty
      ? `↳ ${sizes.length} sizes · only ${receivedQty(l)} received — set or remove the rows left at zero`
      : `✓ ${sizes.length} sizes · ${receivedQty(l)} spread ${even ? `${qtys[0]} each` : 'as evenly as it divides'}`,
      empty ? 'warn' : 'ok')
  }
  const saveSplit = async (l, rows) => {
    try {
      // Price is the LINE's, in one place, and the breakdown is a count of what
      // arrived. So the rows go up carrying no price at all: the server reads a
      // blank as "the line's" (see _create_product — the variant's own where it
      // has any, otherwise the line's). Stripping it rather than leaving what an
      // older breakdown saved is the point — a stale figure on a row would win
      // over the MRP someone has just corrected on the line, silently.
      const priced = rows.map((r) => {
        const { rate, mrp, sale_price: _sp, sale_discount_pct: _sd, ...rest } = r
        return rest
      })
      await api.setLineSplits(l.id, priced)
      setSplitFor(null); setSrows([])
      // open the line you have just broken down: the rows that appear are the
      // result of the edit, and folding them away would hide the answer
      setOpenSplits((m) => ({ ...m, [l.id]: rows.length > 0 }))
      await reload()
      toast(rows.length ? `✓ ${l.description || 'Line'} broken into ${rows.length} item(s)` : '✓ Breakdown cleared', 'ok')
    } catch (e) { toast(e.detail || 'Could not save the breakdown', 'err') }
  }
  const scanInto = async (l, splitId) => {
    const code = window.prompt('Scan or paste the QR code (or barcode / SKU) for this row:')
    if (!code) return
    try {
      await api.scanLineCode(l.id, code.trim(), splitId ?? null)
      await reload()
      toast('✓ Linked to that product', 'ok')
    } catch (e) { toast(e.detail || 'Code not recognised', 'err') }
  }
  // Set one column on EVERY line of this receipt.
  //
  // The same control the invoice grid carries, and wanted here for the same
  // reason: a bill of fifty-nine lines is very often one category, one unit and
  // one markup, and typing that fifty-nine times is the job that gets abandoned
  // in the middle. Half-set is worse than unset on this screen — an unmapped
  // category is a product that lands in Inventory for somebody to find later.
  //
  // Unlike the invoice grid, every edit here is a SERVER call: these lines are
  // saved rows, not a draft in the browser. So the whole column goes up as one
  // batch and the screen reloads once at the end, rather than fifty-nine times.
  // The button is dead while that is in flight, because a second press would
  // send the same batch again over rows the first is still changing.
  //
  // A price is not copied but RE-DERIVED per line: filling Discount % = 20 gives
  // each line its own sale price from its own MRP. Copying one line's sale price
  // onto a bill of different MRPs is the one thing this must not do.
  const [gfill, setGfill] = useState({})
  const [filling, setFilling] = useState('')
  const GFILL_PRICE = new Set(['mrp', 'sale_price', 'sale_discount_pct'])
  const gfillReady = (k) => (GFILL_PRICE.has(k)
    ? nf(gfill[k]) != null
    : !!String(gfill[k] ?? '').trim())
  const applyGfill = async (k) => {
    const lines = grn?.lines || []
    if (!gfillReady(k) || !lines.length || filling) return
    const v = GFILL_PRICE.has(k) ? nf(gfill[k]) : String(gfill[k]).trim()
    setFilling(k)
    try {
      await Promise.all(lines.map((l) => api.editLine(
        l.id, GFILL_PRICE.has(k) ? priceBody(l, k, v) : { [k]: v })))
      await reload()
      setGfill({ ...gfill, [k]: '' })
      toast(`✓ ${v} on all ${lines.length} line(s)`, 'ok')
    } catch (e) {
      await reload()          // some of them may have taken; show what is true
      toast(e.detail || 'Could not set that on every line', 'err')
    }
    setFilling('')
  }
  const gfillCell = (k, label) => {
    const busyHere = filling === k
    return (
      <div className="fillbox text">
        {k === 'unit_type' ? (
          <select value={gfill[k] ?? ''} title={`Set ${label} on every line`}
            onChange={(e) => setGfill({ ...gfill, [k]: e.target.value })}>
            <option value="">unit…</option>
            {(units.types || []).map((t) => (
              <option key={t.code} value={t.code}>
                {t.code}{t.pieces > 1 ? ` · ${t.pieces} pcs` : ''}</option>
            ))}
          </select>
        ) : (
          <input value={gfill[k] ?? ''} list={k === 'category' ? 'essa-cats' : undefined}
            inputMode={GFILL_PRICE.has(k) ? 'decimal' : undefined}
            placeholder={GFILL_PRICE.has(k) ? (k === 'sale_discount_pct' ? '%' : 'all') : 'all'}
            title={`Set ${label} on every line of this receipt`}
            onChange={(e) => setGfill({ ...gfill, [k]: e.target.value })}
            onKeyDown={(e) => { if (e.key === 'Enter') applyGfill(k) }} />
        )}
        <button className="btn" disabled={!gfillReady(k) || !!filling}
          onClick={() => applyGfill(k)}
          title={`Apply ${gfill[k] || '…'} to all ${(grn?.lines || []).length} line(s)`}>
          {busyHere ? '…' : 'Apply all'}</button>
      </div>
    )
  }

  const unbalanced = (grn?.unbalanced_splits || []).length
  const shortage = grn?.shortages || { claimable_qty: 0, claimable_value: 0, open_qty: 0, open_value: 0 }
  const editable = !!grn && grn.status !== 'posted'
  // the sidebar's scope filter, with its counts — a chip that doesn't say how
  // many it holds makes someone click every one of them to find out
  const [scope, setScope] = useState('all')
  const inScope = (p) => scope === 'all' ? true
    : scope === 'short' ? p.short_qty > 0 : p.status === scope
  const counts = {
    draft: list.filter((p) => p.status === 'draft').length,
    posted: list.filter((p) => p.status === 'posted').length,
    short: list.filter((p) => p.short_qty > 0).length,
  }
  const shown = list.filter(inScope)
    .filter((p) => matches(p, q, ['supplier_name', 'invoice_number', 'status']))
  const grnPage = usePaged(shown, 50)
  const [pcat, setPcat] = useState({})             // in-progress category per line id
  // Which lines are showing their variant rows. A breakdown of a dozen sizes is
  // a dozen rows under one line, each as tall as its attribute list — enough to
  // push the next line off the screen. Folded away by default: the `split · N`
  // badge already says they are there, and this is what opens them for a look.
  const [openSplits, setOpenSplits] = useState({})
  const splitsShown = (l) => !!openSplits[l.id]
  const splitToggle = (l) => (l.splits.length ? (
    <button className="btn splittoggle" onClick={() => setOpenSplits((m) => ({ ...m, [l.id]: !m[l.id] }))}
      title={splitsShown(l) ? 'Fold the breakdown rows away'
        : `Show the ${l.splits.length} row(s) this line breaks into`}>
      {splitsShown(l) ? '▾' : '▸'} {l.splits.length}
    </button>
  ) : null)
  // in-progress price per line, keyed "lineId:field" — the same hold-then-save
  // the category cell uses, so a three-digit MRP is one PATCH and not three
  const [pprice, setPprice] = useState({})
  const priceCell = (l, k, tip) => {
    const key = `${l.id}:${k}`
    return (
      <td className={'num' + (tip ? ' calc' : '')} title={tip}>
        {editable ? (
          <input value={pprice[key] ?? (l[k] ?? '')} placeholder="—"
            onChange={(e) => setPprice((p) => ({ ...p, [key]: e.target.value }))}
            onBlur={() => {
              const v = pprice[key]
              setPprice((p) => { const c = { ...p }; delete c[key]; return c })
              if (v !== undefined && String(v) !== String(l[k] ?? '')) setLinePrice(l, k, v)
            }}
            onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} />
        ) : (l[k] != null ? (k === 'sale_discount_pct' ? `${+l[k]}%` : money(l[k])) : '—')}
      </td>
    )
  }
  return (
    <div className="body">
      <Sidebar id="grn" label="GRNs">
        <div className="head"><h3>GRNs · {shown.length}</h3></div>
        {list.length > 0 && <>
          <SearchBox value={q} onChange={setQ} placeholder="Search supplier, invoice, status…" />
          <div className="toolbar"><FilterChips value={scope} onChange={setScope} options={[
            ['draft', 'Draft', counts.draft, 'Receipts still being worked on'],
            ['posted', 'Posted', counts.posted, 'Receipts already in stock'],
            ['short', 'Short', counts.short, 'Receipts with goods billed but not delivered'],
            ['all', 'All', list.length, 'Every receipt'],
          ]} /></div>
        </>}
        <div className="list">
          {list.length === 0 && <div className="empty" style={{ marginTop: 30, fontSize: 13 }}>No GRNs yet. Open a confirmed document and click “Create GRN”.</div>}
          {list.length > 0 && shown.length === 0 && <div className="empty" style={{ marginTop: 30, fontSize: 13 }}>
            Nothing matches. {q ? 'Clear the search' : 'Try “All”'} to see the other {list.length} receipt(s).</div>}
          {grnPage.slice.map((p) => (
            <div key={p.id} className={'doc-row' + (selId === p.id ? ' sel' : '')} onClick={() => setSelId(p.id)}>
              <div className="t">{p.supplier_name || 'GRN #' + p.id}</div>
              <div className="m">
                <span className={'badge ' + (p.status === 'posted' ? 'confirmed' : 'uploaded')}>{p.status}</span>
                <span>#{p.invoice_number || '—'}</span>
                <span style={{ marginLeft: 'auto' }}>₹ {money(p.grand_total)}</span>
              </div>
              <div className="m"><span>{p.line_count} lines · {p.new_products} new product(s)</span></div>
              {p.short_qty > 0 && (
                <div className="m"><span style={{ color: 'var(--warn)' }}>
                  ⚠ {p.short_qty} short · ₹ {money(p.short_value)} to claim</span></div>
              )}
            </div>
          ))}
        </div>
        <Pager {...grnPage} noun="receipt" />
      </Sidebar>
      {grn ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="editor">
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
              <h2 style={{ margin: 0 }}>{grn.supplier_name}</h2>
              <span className={'badge ' + (grn.status === 'posted' ? 'confirmed' : 'uploaded')}>{grn.status}</span>
            </div>
            <div className="kv" style={{ margin: '12px 0 20px', gridTemplateColumns: '130px 1fr 130px 1fr' }}>
              <div className="k">GRN No</div><div>{grn.grn_no || '—'}</div>
              <div className="k">Invoice</div><div>{grn.invoice_number} · {fmtDate(grn.invoice_date)}</div>
              <div className="k">Taxable</div><div>₹ {money(grn.taxable_total)}</div>
              <div className="k">Grand total</div><div>₹ {money(grn.grand_total)}</div>
            </div>
            <Section id="grn.lines" title="Lines → inventory match"
              summary={`${grn.lines.length} line(s) · ${grn.new_products} new product(s)`}>
              {/* the category master, shared by the line cells and the breakdown editor */}
              <datalist id="essa-cats">{cats.map((c) => <option key={c} value={c} />)}</datalist>
              <div className="tablewrap">
              <table className="items">
                <thead><tr><th>Product</th><th>QR code</th><th>Description</th>
                  <th style={{ minWidth: 150 }}>Category</th><th>HSN</th>
                  <th style={{ textAlign: 'right' }} title="What the supplier invoiced">Billed</th>
                  <th style={{ textAlign: 'right' }} title="What actually came out of the boxes">Received</th>
                  <th style={{ minWidth: 190 }} title="What one of these is — piece, pair, dozen — and what the billed quantity becomes because of it. This is what reaches stock, and how many QR labels print.">Unit → stock</th>
                  <th style={{ textAlign: 'right' }}>Rate</th>
                  <th style={{ textAlign: 'right' }}>Amount</th>
                  {/* The retail trio, beside the purchase figures it is worked
                      out against. Typed once on the line; every variant of a
                      breakdown takes it from here. */}
                  <th style={{ textAlign: 'right', minWidth: 78 }}
                    title="What the supplier printed as the retail price">MRP</th>
                  <th style={{ textAlign: 'right', minWidth: 88 }} className="calc"
                    title="Off MRP. Type a sale price instead and this fills itself.">Discount % ƒ</th>
                  <th style={{ textAlign: 'right', minWidth: 88 }} className="calc"
                    title="MRP less the discount — e.g. 995 − 20% = 796. Type it and the discount % follows.">Sale price ƒ</th>
                  <th>Match</th>
                  {editable && <th></th>}</tr>
                  {/* One value down a whole column. Hand-written to line up with
                      the hand-written headings above it — the two must be kept in
                      step, and there are fourteen of them.

                      Only the five columns that are EDITABLE carry a box. The
                      rest are the supplier's own figures: the description, the
                      HSN, what was billed and at what rate are what the invoice
                      said, and this screen does not get to say otherwise. */}
                  {editable && (
                    <tr className="fillrow">
                      <th /><th /><th />
                      <th>{gfillCell('category', 'Category')}</th>
                      <th /><th /><th />
                      <th>{gfillCell('unit_type', 'Unit')}</th>
                      <th /><th />
                      <th>{gfillCell('mrp', 'MRP')}</th>
                      <th>{gfillCell('sale_discount_pct', 'Discount %')}</th>
                      <th>{gfillCell('sale_price', 'Sale price')}</th>
                      <th /><th />
                    </tr>
                  )}
                </thead>
                <tbody>
                  {grn.lines.map((l) => (
                    <React.Fragment key={l.id}>
                      <tr>
                        <td className="mono" style={{ color: 'var(--muted)' }}>{l.product_sku || '—'}</td>
                        <td className="mono">{l.qr_code || (l.splits.length ? '—' : <span style={{ color: 'var(--muted)' }}>on post</span>)}</td>
                        <td>{l.description}</td>
                        {/* category chosen here means the product is born mapped, instead of
                            landing "unmapped" for someone to fix product by product */}
                        <td>{editable ? (
                          <>
                            <input list="essa-cats" className="mono" style={{ fontSize: 11 }}
                              placeholder={l.category_suggestion?.best || 'unmapped'}
                              value={pcat[l.id] ?? l.category ?? ''}
                              onChange={(e) => setPcat({ ...pcat, [l.id]: e.target.value })}
                              onBlur={() => { const v = pcat[l.id]
                                if (v !== undefined && v !== (l.category ?? '')) setLineCat(l, v)
                                setPcat((p) => { const c = { ...p }; delete c[l.id]; return c }) }}
                              onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} />
                            {!l.category && l.category_suggestion?.best && (
                              <button className="catchip" title={l.category_suggestion.confident
                                ? 'Mapped from the description — click to lock it in'
                                : 'Best guess (not confident) — click to accept'}
                                onClick={() => setLineCat(l, l.category_suggestion.best)}>
                                use {l.category_suggestion.best}{l.category_suggestion.confident ? '' : ' ?'}
                              </button>
                            )}
                          </>
                        ) : (l.category || l.product_category
                          || <span className="badge review">unmapped</span>)}</td>
                        <td>{l.hsn}</td>
                        <td style={{ textAlign: 'right' }}>{l.qty}</td>
                        {/* the number that becomes stock. Equal to billed unless a
                            shortage was recorded at the dock — then the gap is the claim */}
                        <td style={{ textAlign: 'right',
                          color: l.has_shortage ? 'var(--warn)' : undefined,
                          fontWeight: l.has_shortage ? 700 : undefined }}
                          title={l.has_shortage
                            ? `${l.qty} billed, ${l.missing_qty || 0} short/damaged`
                              + (l.excess_qty ? `, ${l.excess_qty} extra` : '') + ` → ${l.received_qty} into stock`
                            : 'All of it arrived'}>
                          {l.received_qty != null ? l.received_qty : l.qty}</td>
                        {/* the conversion, shown before it is committed: a dozen
                            pillow covers is six pairs and six labels, and nobody
                            should find that out after posting */}
                        <td>
                          {editable ? (
                            <select className="mono" style={{ fontSize: 11, width: '100%' }}
                              value={l.unit_type || ''} disabled={l.unit?.locked}
                              title={l.unit?.why || ''}
                              onChange={(e) => setLineUnit(l, e.target.value)}>
                              <option value="">auto{l.unit ? ` · ${l.unit.unit_type}` : ''}</option>
                              {(units.types || []).map((t) => (
                                <option key={t.code} value={t.code}>
                                  {t.code}{t.pieces > 1 ? ` · ${t.pieces} pcs` : ''}</option>
                              ))}
                            </select>
                          ) : (
                            <span className="mono" style={{ fontSize: 11 }}>
                              {l.unit?.unit_type || l.uom || 'PCS'}</span>
                          )}
                          {l.unit && (
                            <div className="small" title={l.unit.why}
                              style={{ marginTop: 3, color: l.unit.whole ? 'var(--muted)' : 'var(--warn)' }}>
                              {l.unit.explain}</div>
                          )}
                        </td>
                        <td style={{ textAlign: 'right' }}>{money(l.rate)}</td>
                        <td style={{ textAlign: 'right' }}>{money(l.amount)}</td>
                        {priceCell(l, 'mrp')}
                        {priceCell(l, 'sale_discount_pct', 'Off MRP. Type a sale price instead and this fills itself.')}
                        {priceCell(l, 'sale_price', 'MRP less the discount. Type it and the discount % follows.')}
                        <td>{l.splits.length
                          ? <span className={'badge ' + (l.split_balanced ? 'confirmed' : 'review')}
                              title={(l.split_balanced ? 'Breakdown adds up to what was received'
                                : `${l.split_remainder} of ${receivedQty(l)} received not yet broken down`)
                                + ' — this bundle line does not receive stock itself; the rows below do'}>
                              split · {l.splits.length}{l.split_balanced ? '' : ' ⚠'}</span>
                          : <span className={'badge ' + (l.is_new_product ? 'review' : 'confirmed')}>
                              {l.is_new_product ? 'new' : 'matched'}</span>}
                          {/* a posted GRN has no actions column, so the fold
                              control rides with the badge that counts them */}
                          {!editable && splitToggle(l)}</td>
                        {editable && (
                          <td style={{ whiteSpace: 'nowrap' }}>
                            <button className={'btn' + (!l.splits.length && l.size_breakdown?.rows?.length ? ' primary' : '')}
                              style={{ padding: '2px 8px' }}
                              onClick={() => (splitFor === l.id ? setSplitFor(null) : openSplit(l))}
                              title={!l.splits.length && l.size_breakdown?.rows?.length
                                ? `The invoice already carries the mix — ${l.size_breakdown.rows
                                    .map((r) => `${r.size} → ${r.qty}`).join(', ')}`
                                  + `\nTotal ${l.size_breakdown.total} of ${receivedQty(l)} received.`
                                  + '\nClick to open it filled in, ready to check.'
                                : 'Break the bundle into what actually arrived — size, colour, material…'}>
                              {splitFor === l.id ? 'Close'
                                : l.splits.length ? 'Edit breakdown'
                                : l.size_breakdown?.rows?.length
                                  ? `Break down · ${l.size_breakdown.rows.length} sizes`
                                  : 'Break down'}</button>
                            <button className="btn" style={{ padding: '2px 8px', marginLeft: 4 }}
                              onClick={() => (shortFor === l.id ? setShortFor(null) : openShortage(l))}
                              title="Record what the supplier billed and the boxes didn't hold — it stays out of stock and becomes a claim">
                              {shortFor === l.id ? 'Close' : l.has_shortage ? '⚠ Shortage' : 'Shortage'}</button>
                            {splitToggle(l)}
                            {!l.splits.length && (
                              <button className="btn" style={{ padding: '2px 8px', marginLeft: 4 }} onClick={() => scanInto(l, null)}
                                title="Scan a QR code to pin this line to an existing product">⌗ QR</button>
                            )}
                          </td>
                        )}
                      </tr>

                      {/* what was billed and didn't arrive — never stock, always a claim */}
                      {shortFor !== l.id && (l.shortages || []).map((s) => (
                        <tr key={'sh' + s.id} style={{ background: 'var(--warn-bg)' }}>
                          <td className="mono" style={{ color: 'var(--muted)' }}>—</td>
                          <td className="mono" style={{ color: 'var(--muted)' }}>—</td>
                          <td style={{ paddingLeft: 22 }}>
                            <span style={{ color: s.kind === 'excess' ? 'var(--ok)' : 'var(--warn)' }}>⚠</span>
                            {' '}<b>{s.kind}</b>{s.variant ? <span> · {s.variant}</span> : null}
                            <span className="small" style={{ marginLeft: 8, color: 'var(--muted)' }}>
                              {s.reason || 'no reason given'}{s.recorded_by ? ` · by ${s.recorded_by}` : ''}</span>
                          </td>
                          <td colSpan={2} className="small" style={{ color: 'var(--muted)' }}>
                            {s.claimable ? 'never entered stock — claimable' : 'extra goods, taken into stock'}</td>
                          <td style={{ textAlign: 'right' }}>—</td>
                          <td style={{ textAlign: 'right', color: 'var(--warn)' }}>
                            {s.kind === 'excess' ? '+' : '−'}{s.qty}</td>
                          {/* a shortage is counted in the BILLED unit — it is a
                              fact about the invoice, not about the stock unit */}
                          <td className="small" style={{ color: 'var(--muted)' }}>
                            {s.kind === 'excess' ? 'into stock with the rest' : 'never converted'}</td>
                          <td style={{ textAlign: 'right' }}>{money(s.rate)}</td>
                          <td style={{ textAlign: 'right' }}>{s.claimable ? money(s.amount) : '—'}</td>
                          {/* goods that never arrived have no retail price to state */}
                          <td colSpan={3} />
                          {/* claiming or waiving is a decision for AFTER the goods are
                              booked in, so it lives on the posted GRN — while the GRN is
                              still a draft the shortage itself is what is being edited */}
                          <td style={{ whiteSpace: 'nowrap' }}>
                            <span className={'badge ' + (s.status === 'claimed' ? 'confirmed' : 'review')}
                              title={s.status === 'claimed' ? 'A posted debit note has claimed this'
                                : s.status === 'waived' ? 'Accepted rather than claimed'
                                  : 'Not yet claimed from the supplier'}>{s.status}</span>
                            {!editable && s.claimable && s.status === 'waived' && (
                              <button className="btn" style={{ padding: '2px 8px', marginLeft: 5 }}
                                onClick={() => unwaive(s)} title="The supplier never did send it">Reopen</button>)}
                            {!editable && s.claimable && (s.status === 'open' || s.status === 'part-claimed') && (
                              <button className="btn" style={{ padding: '2px 8px', marginLeft: 5 }}
                                onClick={() => waive(s)} title="Accept it rather than raise a debit note">Waive</button>)}
                          </td>
                          {editable && <td />}
                        </tr>
                      ))}

                      {/* saved variant rows — one product each once posted.
                          Folded away unless this line's toggle is open. */}
                      {splitFor !== l.id && splitsShown(l) && l.splits.map((s) => (
                        <tr key={s.id} style={{ background: 'var(--panel-2)' }}>
                          <td className="mono" style={{ color: 'var(--muted)' }}>{s.product_sku || '—'}</td>
                          <td className="mono">{s.product_barcode || s.code || <span style={{ color: 'var(--muted)' }}>on post</span>}</td>
                          {/* the prices used to be repeated here as chips; they
                              have columns of their own now, beside the line's */}
                          <td style={{ paddingLeft: 22 }}>↳ <b>{s.label}</b></td>
                          <td className="mono" style={{ fontSize: 11 }}>{s.category || l.category
                            || <span style={{ color: 'var(--muted)' }}>auto</span>}</td>
                          <td>{l.hsn}</td>
                          {/* a variant row IS a received quantity — the supplier never
                              billed it separately, so there is nothing under "Billed" */}
                          <td style={{ textAlign: 'right', color: 'var(--muted)' }}>—</td>
                          <td style={{ textAlign: 'right' }}>{s.qty}</td>
                          <td className="small" title={s.unit?.why}
                            style={{ color: s.unit && !s.unit.whole ? 'var(--warn)' : 'var(--muted)' }}>
                            {s.unit?.explain || '—'}</td>
                          <td style={{ textAlign: 'right' }}>{money(s.rate)}</td>
                          <td style={{ textAlign: 'right' }}>{money(s.amount)}</td>
                          {/* a variant prices off its line unless it was saved
                              with one of its own — the same fallback the server
                              applies when it creates the product */}
                          {['mrp', 'sale_discount_pct', 'sale_price'].map((k) => {
                            const own = s[k] != null
                            const v = own ? s[k] : l[k]
                            return (
                              <td key={k} className="num" style={{ color: own ? undefined : 'var(--muted)' }}
                                title={own ? 'Set on this variant' : 'From the line'}>
                                {v == null ? '—' : k === 'sale_discount_pct' ? `${+v}%` : money(v)}</td>
                            )
                          })}
                          <td>{s.product_id
                            ? <span className="badge confirmed">{s.is_new_product ? 'created' : 'matched'}</span>
                            : <span className="badge review">new</span>}</td>
                          {editable && <td>{!s.product_id && (
                            <button className="btn" style={{ padding: '2px 8px' }} onClick={() => scanInto(l, s.id)}
                              title="Scan the QR of an existing product for this variant">⌗ QR</button>)}</td>}
                        </tr>
                      ))}

                      {/* shortage editor — what was billed and wasn't in the box */}
                      {shortFor === l.id && (() => {
                        const missing = round3(shrows.filter((r) => r.kind !== 'excess')
                          .reduce((n, r) => n + (+r.qty || 0), 0))
                        const extra = round3(shrows.filter((r) => r.kind === 'excess')
                          .reduce((n, r) => n + (+r.qty || 0), 0))
                        const recv = round3((+l.qty || 0) - missing + extra)
                        const over = missing > (+l.qty || 0) + 0.001
                        return (
                          <tr>
                            <td colSpan={editable ? 15 : 14} style={{ background: 'var(--warn-bg)', padding: '12px 14px' }}>
                              <div className="rowedit-bar"
                                style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
                                <b>Shortage on “{l.description}”</b>
                                <span className="small" style={{ color: over ? 'var(--danger)' : missing ? 'var(--warn)' : 'var(--muted)' }}>
                                  {over ? `${missing} short of only ${l.qty} billed — more than the invoice`
                                    : <>Into stock <b>{recv}</b> of {l.qty} billed
                                      {missing ? ` · ${missing} short — ₹ ${money(missing * (+l.rate || 0))} to claim` : ''}
                                      {extra ? ` · ${extra} extra` : ''}</>}
                                </span>
                              </div>
                              <div className="tablewrap">
                              <table className="items entry" style={{ margin: 0 }}>
                                <thead><tr>
                                  <th style={{ minWidth: 230 }}>What happened</th>
                                  <th style={{ minWidth: 90, textAlign: 'right' }}>Qty</th>
                                  <th style={{ minWidth: 170 }}>Reason</th>
                                  <th style={{ minWidth: 150 }}>Which ones</th>
                                  <th style={{ minWidth: 180 }}>Note</th>
                                  <th style={{ minWidth: 90, textAlign: 'right' }}>Value</th>
                                  <th></th></tr></thead>
                                <tbody>{shrows.map((r, i) => (
                                  <tr key={i}>
                                    <td>
                                      <select value={r.kind} onChange={(e) => updShrow(i, 'kind', e.target.value)}>
                                        {SHORT_KINDS.map(([k, label, hint]) =>
                                          <option key={k} value={k}>{label} — {hint}</option>)}
                                      </select>
                                    </td>
                                    <td className="num"><input value={r.qty} placeholder="0"
                                      onChange={(e) => updShrow(i, 'qty', e.target.value)} /></td>
                                    <td><input list="essa-short-reasons" value={r.reason} placeholder="why?"
                                      onChange={(e) => updShrow(i, 'reason', e.target.value)} /></td>
                                    <td><input value={r.variant} placeholder="e.g. S / White — if known"
                                      onChange={(e) => updShrow(i, 'variant', e.target.value)} /></td>
                                    <td><input value={r.note} placeholder="anything the office should see"
                                      onChange={(e) => updShrow(i, 'note', e.target.value)} /></td>
                                    <td className="num" style={{ color: 'var(--muted)' }}>
                                      {r.kind === 'excess' ? '—' : money((+r.qty || 0) * (+l.rate || 0))}</td>
                                    <td><button className="btn" style={{ padding: '2px 7px' }} title="Remove this row"
                                      onClick={() => setShrows(shrows.filter((_, j) => j !== i))}>×</button></td>
                                  </tr>
                                ))}</tbody>
                              </table>
                              </div>
                              <datalist id="essa-short-reasons">
                                {(shortOpts.reasons || []).map((v) => <option key={v} value={v} />)}
                              </datalist>
                              <div className="rowedit-bar"
                                style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
                                <button className="btn" onClick={() => setShrows([...shrows, blankShortage()])}>+ add row</button>
                                {l.has_shortage && (
                                  <button className="btn" onClick={() => saveShortage(l, [])}
                                    title="No shortage after all — the whole billed quantity is expected in stock again">
                                    Clear shortage</button>
                                )}
                                <button className="btn" onClick={() => { setShortFor(null); setShrows([]) }}>Cancel</button>
                                <button className="btn primary" disabled={over}
                                  onClick={() => saveShortage(l, shrows.filter((r) => +r.qty > 0))}>Save shortage</button>
                              </div>
                            </td>
                          </tr>
                        )
                      })()}

                      {/* attribute-breakdown editor */}
                      {splitFor === l.id && (
                        <tr>
                          <td colSpan={editable ? 15 : 14} style={{ background: 'var(--panel-2)', padding: '12px 14px' }}>
                            <div className="rowedit-bar"
                              style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
                              <b>Breakdown of “{l.description}”</b>
                              {/* the target is what ARRIVED — once a shortage is recorded the
                                  rows only have to reach that, which is the point of recording it */}
                              {/* the running check. It is the breakdown, not the
                                  billed bundle, that decides what exists — so it
                                  has to account for every piece before any of
                                  them gets a SKU and a QR */}
                              {(() => {
                                const left = round3(receivedQty(l) - splitSum)
                                const ok = sameQty(splitSum, receivedQty(l))
                                return (
                                  <span className="small" style={{ color: ok ? 'var(--ok)' : 'var(--warn)', fontWeight: 600 }}>
                                    {splitSum} of {receivedQty(l)} assigned
                                    {l.has_shortage ? ` (${l.qty} billed, ${l.missing_qty} short)` : ''}
                                    {ok ? ' ✓'
                                      : left > 0
                                        ? ` · ${left} piece${left === 1 ? '' : 's'} remaining. Please complete the size breakdown before posting to inventory.`
                                        : ` · ${-left} piece${left === -1 ? '' : 's'} more than were received — remove them before posting.`}
                                  </span>
                                )
                              })()}
                              {/* the moment someone notices the sizes don't add up: "the rest
                                  never came" is an answer, and it must be easier than inventing
                                  the difference to make the total work */}
                              {!l.has_shortage && splitSum > 0 && splitSum < receivedQty(l) && (
                                <button className="catchip" style={{ color: 'var(--warn)' }}
                                  title="Record the difference as short — it stays out of stock and becomes a claim"
                                  onClick={() => openShortage(l, round3(receivedQty(l) - splitSum))}>
                                  {round3(receivedQty(l) - splitSum)} not in the box?
                                </button>
                              )}
                            </div>
                            {/* The run, typed once. Six sizes and a count against
                                each of them is what the breakdown of an ordinary
                                garment bundle IS, and "28-2-38" is the whole of it —
                                so it is read straight into the grid below and the
                                pieces that arrived are spread over it. Everything
                                stays editable afterwards: this saves the typing, it
                                does not decide anything. */}
                            {(() => {
                              const run = parseSizeRun(runSpec)
                              const spread = run.sizes.length ? spreadQty(receivedQty(l), run.sizes.length) : []
                              const even = spread.length && spread.every((q) => q === spread[0])
                              const listed = run.sizes.length > 10
                                ? run.sizes.slice(0, 9).join(', ') + `, … ${run.sizes[run.sizes.length - 1]}`
                                : run.sizes.join(', ')
                              return (
                                <div className="rowedit-bar sizerun">
                                  <span className="runlabel">Size run</span>
                                  <input value={runSpec} placeholder="28-2-38"
                                    onChange={(e) => setRunSpec(e.target.value)}
                                    onKeyDown={(e) => { if (e.key === 'Enter') applySizeRun(l) }}
                                    title={'Start – step – end, the way the packing slip writes it.\n'
                                      + '28-2-38 is 28, 30, 32, 34, 36, 38 — and 28-38 steps by one.'} />
                                  <button className="btn" disabled={!run.sizes.length}
                                    onClick={() => applySizeRun(l)}
                                    title={run.sizes.length
                                      ? `Fill the grid with ${run.sizes.length} sizes and spread the ${receivedQty(l)} received over them`
                                      : 'Write the run as start-step-end first'}>Generate sizes</button>
                                  <span className={'why' + (run.why ? ' bad' : '')}>
                                    {run.why || (run.sizes.length
                                      ? <><b>{listed}</b>{` — ${run.sizes.length} sizes, ${receivedQty(l)} received, `}
                                        {even ? `${spread[0]} each` : `${Math.min(...spread)}–${Math.max(...spread)} each`}</>
                                      : 'Start–step–end. Generates the sizes and spreads what arrived evenly '
                                        + 'over them; every row stays editable afterwards.')}
                                  </span>
                                </div>
                              )
                            })()}
                            <div className="tablewrap">
                              <table className="items entry" style={{ margin: 0, minWidth: 1250 }}>
                                <thead>
                                  <tr>
                                  {SPLIT_ATTRS.map(([k, label, w]) => <th key={k} style={{ minWidth: w }}>{label}</th>)}
                                  <th style={{ minWidth: 150 }}>Category</th>
                                  {SPLIT_QTY.map(([k, label, w]) =>
                                    <th key={k} style={{ minWidth: w, textAlign: 'right' }}>{label}</th>)}
                                  <th></th></tr>
                                  {/* Under the headings, not in a toolbar above the
                                      table: the control belongs to the column it
                                      fills, and aligned under it there is nothing
                                      to explain about which is which. Same device,
                                      and the same reasoning, as the invoice grid's
                                      buffer columns.

                                      Qty has none. It is the one figure that is
                                      genuinely per row — it is the whole point of
                                      the breakdown — and the size run above already
                                      spreads it. */}
                                  <tr className="fillrow">
                                    {SPLIT_ATTRS.map(([k, label]) => <th key={k}>{fillAttr(k, label)}</th>)}
                                    <th>{fillAttr('category', 'Category')}</th>
                                    {SPLIT_QTY.map(([k]) => <th key={k}></th>)}
                                    <th></th>
                                  </tr>
                                </thead>
                                <tbody>{srows.map((r, i) => (
                                  <tr key={i}>
                                    {SPLIT_ATTRS.map(([k]) => (
                                      <td key={k}><input list={'essa-opt-' + k} value={r[k]}
                                        onChange={(e) => updSrow(i, k, e.target.value)} /></td>
                                    ))}
                                    <td><input list="essa-cats" className="mono" style={{ fontSize: 11 }}
                                      placeholder={l.category || 'auto'} value={r.category}
                                      onChange={(e) => updSrow(i, 'category', e.target.value)} /></td>
                                    {SPLIT_QTY.map(([k]) => (
                                      <td key={k} className="num">
                                        <input value={r[k]}
                                          onChange={(e) => updSrow(i, k, e.target.value)} /></td>
                                    ))}
                                    <td><button className="btn" style={{ padding: '2px 7px' }} title="Remove this row"
                                      onClick={() => setSrows(srows.filter((_, j) => j !== i))}>×</button></td>
                                  </tr>
                                ))}</tbody>
                              </table>
                            </div>
                            {/* the phone app's option lists, so both ends use one vocabulary */}
                            {SPLIT_ATTRS.map(([k]) => (
                              <datalist key={k} id={'essa-opt-' + k}>
                                {(opts[k] || []).map((v) => <option key={v} value={v} />)}
                              </datalist>
                            ))}
                            <div className="rowedit-bar"
                              style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
                              {/* a new row joins the price the bar above already
                                  set — otherwise adding one would drop the whole
                                  breakdown to "mixed" and blank the bar */}
                              <button className="btn" onClick={() => {
                                const last = srows[srows.length - 1]
                                const row = blankVariant(l.rate,
                                  last?.category || l.category || l.category_suggestion?.best)
                                setSrows([...srows, last
                                  ? { ...row, rate: last.rate, mrp: last.mrp,
                                      sale_price: last.sale_price,
                                      sale_discount_pct: last.sale_discount_pct }
                                  : row])
                              }}>+ add row</button>
                              <button className="btn" title="Copy the last row's attributes into a new row — change just what differs"
                                disabled={!srows.length}
                                onClick={() => setSrows([...srows, { ...srows[srows.length - 1], qty: '' }])}>⧉ duplicate last</button>
                              {l.splits.length > 0 && (
                                <button className="btn" onClick={() => saveSplit(l, [])}
                                  title="Remove the breakdown — the line posts as one product again">Clear breakdown</button>
                              )}
                              <button className="btn" onClick={() => { setSplitFor(null); setSrows([]) }}>Cancel</button>
                              <button className="btn primary"
                                onClick={() => saveSplit(l, srows.filter((r) => variantLabel(r) || r.qty))}>Save breakdown</button>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
              </div>
              <div className="items-foot"><span>{grn.lines.length} lines</span>
                {shortage.claimable_qty > 0 && (
                  <span style={{ color: 'var(--warn)' }}>
                    ⚠ {shortage.claimable_qty} short or damaged · ₹ {money(shortage.claimable_value)} to claim
                    {shortage.open_qty > 0 && shortage.open_qty !== shortage.claimable_qty
                      ? ` (₹ ${money(shortage.open_value)} unclaimed)` : ''}
                  </span>
                )}
                <span>{grn.new_products} will be created as new products</span></div>
            </Section>
          </div>
          <div className="actionbar">
            <span className="small">{grn.status === 'posted'
              ? shortage.open_qty > 0
                ? `Posted. ⚠ ${shortage.open_qty} unit(s) short — ₹ ${money(shortage.open_value)} still to claim. Raise it in Returns, or waive it on the row.`
                : 'Posted — stock has been updated in Inventory. Unpost to correct it, then post again.'
              : unbalanced
                ? `⚠ ${unbalanced} line(s): please complete the size breakdown before posting to inventory — every piece has to be placed, or recorded as a shortage if it never arrived.`
                : shortage.claimable_qty > 0
                  ? `Posting takes in ${grn.lines.reduce((n, l) => n + receivedQty(l), 0)} unit(s). The ${shortage.claimable_qty} short stay out of stock and become a claim against the supplier.`
                  : 'Posting creates new products, adds inward stock, and updates weighted-average cost.'}</span>
            <div className="spacer" />
            {grn.status === 'posted'
              ? <button className="btn" onClick={unpost}
                  title="Reverse the stock this GRN added and put it back to draft">↺ Unpost</button>
              : <button className="btn" onClick={removeGrn}
                  title="Delete this draft GRN — the invoice document stays">Delete GRN</button>}
            <button className="btn primary" disabled={grn.status === 'posted' || unbalanced > 0} onClick={post}>
              {grn.status === 'posted' ? 'Posted ✓' : 'Post GRN to Inventory'}</button>
          </div>
        </div>
      ) : <div className="empty">Select a GRN, or create one from a confirmed document.</div>}
    </div>
  )
}

// ---------- inventory (master corrections + stock adjust) ----------
// Shown read-only on the product panel: these are keyed on the GRN breakdown and
// corrected in the phone app, never re-typed here.
const PRODUCT_ATTRS = [
  ['brand', 'Brand'], ['size', 'Size'], ['color', 'Colour'], ['material', 'Material'],
  ['pattern', 'Pattern'], ['fit', 'Fit'], ['style', 'Style'], ['sleeve', 'Sleeve'],
  ['product_type', 'Type'], ['design_no', 'Design No'],
  ['sale_price', 'Sale price'], ['sale_discount_pct', 'Discount %'],
]
function Inventory({ toast }) {
  const [summary, setSummary] = useState(null)
  const [products, setProducts] = useState([])
  const [detail, setDetail] = useState(null)
  const [adjQty, setAdjQty] = useState('')
  const [adjNote, setAdjNote] = useState('')
  const [q, setQ] = useState('')
  const load = useCallback(() => {
    api.inventorySummary().then(setSummary); api.listProducts().then(setProducts)
  }, [])
  useEffect(() => { load() }, [load])
  const open = (id) => api.getProduct(id).then((d) => {
    setDetail(d); setAdjQty(''); setAdjNote('')
  })
  const doAdjust = async () => {
    if (adjQty === '') return
    await api.adjustStock(detail.id, +adjQty, adjNote || 'manual adjustment')
    toast('✓ Stock adjusted', 'ok'); await open(detail.id); load()
  }
  const genBarcode = async () => {
    try {
      const r = await api.generateBarcode(detail.id)
      toast(r.identifiers_generated?.length ? `✓ Generated ${r.identifiers_generated.join(' + ')}` : '✓ Already had its SKU', 'ok')
      await open(detail.id); load()
    } catch (err) { toast(err.detail || 'Could not generate the code', 'err') }
  }
  // --- per-piece codes: clicking a quantity opens the individual garments ---
  const [units, setUnits] = useState(null)      // {product, units[], serialisable, reason}
  const [zoom, setZoom] = useState(null)        // one piece, viewed large
  const openUnits = async (p) => {
    try { setUnits(await api.productUnits(p.id)); setZoom(null) }
    catch (err) {
      // a 404 is the recognisable case: the server was started before piece codes
      // existed and is serving this (newer) page off disk. Say that, rather than
      // leaving someone to guess at "could not load".
      toast(err.status === 404
        ? 'This server was started before piece codes existed — restart the ESSA server and reload.'
        : (err.detail || 'Could not load the piece codes'), 'err')
    }
  }
  const reloadUnits = async () => { if (units) setUnits(await api.productUnits(units.product.id)) }
  // up to 500 piece codes under one SKU — see units.MAX_PER_RECEIPT
  const unitPage = usePaged(units?.units || [], 100)
  // printing is recorded, so the sheet has to be reloaded to show the new counts
  const printUnits = (ids) => {
    if (units?.can_print === false) { toast(units.print_block, 'err'); return }
    window.open(api.unitLabelsUrl(units.product.id, ids), '_blank')
    setTimeout(reloadUnits, 1200)
  }
  const printOne = (u) => { window.open(api.unitLabelUrl(u.id), '_blank'); setTimeout(reloadUnits, 1200) }
  const makeUnits = async () => {
    try {
      const r = await api.generateUnits(units.product.id)
      toast(`✓ ${r.created} piece code(s) created`, 'ok')
      await reloadUnits(); load()
    } catch (err) { toast(err.detail || 'Could not create piece codes', 'err') }
  }

  const [scan, setScan] = useState('')
  const lookup = async (code) => {
    const c = (code ?? scan).trim(); if (!c) return
    try { const p = await api.lookupByCode(c); await open(p.id); setScan(''); toast(`✓ ${p.sku} · ${p.name || p.description}`, 'ok') }
    catch (err) { toast(err.detail || `No product for “${c}”`, 'err') }
  }
  // --- inventory integrity: what is stock, and what only looks like it ---
  const [scanRep, setScanRep] = useState(null)
  const rescan = useCallback(() => api.integrityScan().then(setScanRep).catch(() => {}), [])
  useEffect(() => { rescan() }, [rescan])
  const repairInventory = async () => {
    let plan
    try { plan = await api.integrityRepair(true) }
    catch (e) { toast(e.detail || 'Could not check what needs repairing', 'err'); return }
    const w = plan.would_remove
    const lines = [
      w.products.length && `• ${w.products.length} product(s): ${w.products.slice(0, 6).join(', ')}${w.products.length > 6 ? '…' : ''}`,
      w.units.length && `• ${w.units.length} QR / piece code(s)`,
      w.bundles.length && `• ${w.bundles.length} carton label(s): ${w.bundles.slice(0, 6).join(', ')}${w.bundles.length > 6 ? '…' : ''}`,
    ].filter(Boolean).join('\n')
    if (!window.confirm(
      'Remove these records permanently?\n\n' + lines
      + '\n\nEvery one of them traces back to no GRN and carries no stock movement, so'
      + ' nothing that a receipt created is affected. Products kept at zero stock after an'
      + ' unpost are NOT touched — they hold warehouse detailing.\n\nThis cannot be undone.')) return
    try {
      const r = await api.integrityRepair(false)
      toast(`✓ Removed ${r.counts.units} piece code(s), ${r.counts.bundles} carton(s), ${r.counts.products} product(s)`, 'ok')
      await rescan(); load()
    } catch (e) { toast(e.detail || 'Repair failed', 'err') }
  }

  const [labelScope, setLabelScope] = useState('detailed')
  const detailedCount = products.filter((p) => p.detailed).length
  const labelCount = labelScope === 'detailed' ? detailedCount : products.length
  // --- the three controls: search (q), scope chips, and the filter panel ---
  const [stockScope, setStockScope] = useState('all')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [catFilter, setCatFilter] = useState('')
  const [supFilter, setSupFilter] = useState('')
  const activeFilters = [catFilter, supFilter].filter(Boolean).length
  const inStockScope = (p) => stockScope === 'all' ? true
    : stockScope === 'detailed' ? !!p.detailed
      : stockScope === 'pending' ? !p.detailed
        : stockScope === 'instock' ? p.stock_qty > 0 : !(p.stock_qty > 0)
  const visible = products.filter(inStockScope)
    .filter((p) => !catFilter || (p.category || '').toLowerCase().includes(catFilter.toLowerCase()))
    .filter((p) => !supFilter || (p.supplier_name || '').toLowerCase().includes(supFilter.toLowerCase()))
    .filter((p) => matches(p, q, ['sku', 'barcode', 'description', 'hsn', 'supplier_name', 'category', 'size']))
  // inventory gains a row per variant received, so it outgrows a screen quickly
  const invPage = usePaged(visible, 50)
  const printLabels = () => {
    if (!products.length) { toast('No products yet — post a GRN to create products first.', 'err'); return }
    if (labelCount === 0) { toast('No detailed products yet. Detail products first, or switch to “All products”.', 'err'); return }
    window.open(api.labelsUrl(null, labelScope), '_blank')
  }
  return (
    <div className="screen">
      {/* the last full-width module without a title band: it opened straight
          onto its stat tiles, so moving here from the dashboard dropped the
          white header bar and the gold rule the other screens all keep */}
      <div className="pagehead">
        <h2>Inventory</h2>
        <div className="pagesub small">
          Stock on hand, labels and per-piece codes
        </div>
      </div>
      <div className="screenbody">
        <div style={{ display: 'flex', gap: 14, marginBottom: 20 }}>
          <Stat label="Products" value={summary?.product_count ?? '—'} />
          <Stat label="Units in stock" value={summary ? summary.total_units.toLocaleString('en-IN') : '—'} />
          <Stat label="Stock value (avg cost)" value={summary ? '₹ ' + money(summary.total_stock_value) : '—'} />
          <Stat label="Detailed (mobile)" value={summary ? `${summary.detailed ?? 0} / ${summary.product_count}` : '—'} />
        </div>
        <div className="toolbar">
          <SearchBox value={q} onChange={setQ} placeholder="Search SKU, barcode, description, HSN, supplier…"
            style={{ width: 340 }} />
          <FilterChips value={stockScope} onChange={setStockScope} options={[
            ['all', 'All', products.length, 'Every product in stock'],
            ['detailed', 'Detailed', detailedCount, 'Inspected and recorded on the phone'],
            ['pending', 'To detail', products.length - detailedCount, 'Still waiting to be looked at'],
            ['instock', 'In stock', products.filter((p) => p.stock_qty > 0).length, 'Quantity above zero'],
            ['zero', 'Zero', products.filter((p) => !(p.stock_qty > 0)).length, 'Nothing on hand'],
          ]} />
          <FilterButton open={filtersOpen} onToggle={() => setFiltersOpen((o) => !o)} active={activeFilters} />
          <div className="spacer" />
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input value={scan} onChange={(e) => setScan(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') lookup() }}
              title="Scan a QR, piece label or barcode to jump straight to that product"
              placeholder="⌗ Scan a code…" style={{ width: 190 }} />
            <button className="btn" onClick={() => lookup()} title="Open the scanned product">Fetch</button>
            <button className="btn" onClick={printLabels}
              title={`Print a label sheet for ${labelCount} product(s) — set which in Filters`}>
              🖨 Labels ({labelCount})</button>
          </div>
        </div>
        <FilterPanel open={filtersOpen} active={activeFilters}
          onClear={() => { setCatFilter(''); setSupFilter(''); setLabelScope('detailed') }}>
          <div className="field" style={{ width: 190 }}><label>Category</label>
            <input list="inv-cats" value={catFilter} placeholder="any category"
              onChange={(e) => setCatFilter(e.target.value)} />
            <datalist id="inv-cats">{[...new Set(products.map((p) => p.category).filter(Boolean))].map((c) => <option key={c} value={c} />)}</datalist>
          </div>
          <div className="field" style={{ width: 190 }}><label>Supplier</label>
            <input list="inv-sups" value={supFilter} placeholder="any supplier"
              onChange={(e) => setSupFilter(e.target.value)} />
            <datalist id="inv-sups">{[...new Set(products.map((p) => p.supplier_name).filter(Boolean))].map((s) => <option key={s} value={s} />)}</datalist>
          </div>
          <div className="field" style={{ width: 190 }}><label>Label sheet covers</label>
            <select value={labelScope} onChange={(e) => setLabelScope(e.target.value)}
              title="Which products the Labels button prints">
              <option value="detailed">Detailed only ({detailedCount})</option>
              <option value="all">All products ({products.length})</option>
            </select>
          </div>
        </FilterPanel>

        {/* Inventory Repair. Only shown when there is something to say — a clean
            database should not carry a permanent maintenance banner. */}
        {scanRep && !scanRep.clean && (
          <div className="section" style={{ borderColor: 'var(--danger)', marginBottom: 14 }}>
            <h4 style={{ color: 'var(--danger)' }}>⚠ Inventory mismatch detected</h4>
            <table className="items" style={{ margin: 0 }}>
              <thead><tr><th>What</th><th style={{ textAlign: 'right' }}>Count</th><th>Meaning</th></tr></thead>
              <tbody>
                {[
                  ['Orphan products', scanRep.counts.orphan_products,
                    'no GRN line, no breakdown row, no stock movement — nothing that could have created them survives'],
                  ['Orphan QR / piece codes', scanRep.counts.orphan_units,
                    'piece codes with no posted receipt behind them'],
                  ['Orphan cartons', scanRep.counts.orphan_bundles,
                    'bundle labels for a receipt that no longer exists'],
                  ['SKUs with a QR count mismatch', scanRep.counts.unit_mismatches,
                    'live piece codes don’t equal what the GRNs received'],
                ].filter(([, n]) => n > 0).map(([what, n, why]) => (
                  <tr key={what}><td><b>{what}</b></td>
                    <td style={{ textAlign: 'right' }}>{n}</td>
                    <td className="small" style={{ color: 'var(--muted)' }}>{why}</td></tr>
                ))}
                {scanRep.counts.unposted_products > 0 && (
                  <tr><td>Kept after unpost</td>
                    <td style={{ textAlign: 'right' }}>{scanRep.counts.unposted_products}</td>
                    <td className="small" style={{ color: 'var(--muted)' }}>
                      excluded from stock but <b>never deleted</b> — these hold detailing recorded
                      by hand in the warehouse</td></tr>
                )}
              </tbody>
            </table>
            <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
              <span className="small" style={{ color: 'var(--muted)' }}>
                {scanRep.counts.removable > 0
                  ? `${scanRep.counts.removable} record(s) can be removed. Nothing traceable to a GRN is touched.`
                  : 'Nothing is safe to delete automatically — the counts above need a human decision.'}
              </span>
              <div style={{ flex: 1 }} />
              <button className="btn" onClick={() => api.integrityScan().then(setScanRep)}>Re-scan</button>
              <button className="btn" disabled={!scanRep.counts.removable} onClick={repairInventory}
                title="Delete only records that trace back to no GRN at all">
                Repair ({scanRep.counts.removable})</button>
            </div>
          </div>
        )}
        <div className="tablewrap">
        <table className="items">
          <thead><tr><th style={{ width: 46 }}>QR</th><th>SKU</th><th>Product</th><th>Size</th><th>Category</th><th>HSN</th><th>Supplier</th>
            <th style={{ textAlign: 'right' }}>Stock</th><th style={{ textAlign: 'right' }}>Avg cost</th>
            <th style={{ textAlign: 'right' }}>Value</th></tr></thead>
          <tbody>
            {invPage.slice.map((p) => (
              <tr key={p.id} style={{ cursor: 'pointer', background: detail?.id === p.id ? 'var(--panel-2)' : '' }} onClick={() => open(p.id)}>
                {/* The real QR, small enough for a list and still scannable off the
                    screen. `lazy` keeps a long list from firing a request per row. */}
                <td style={{ padding: 2 }}>
                  <img src={api.qrSvgUrl(p.id, 2)} alt={`QR ${p.sku}`} loading="lazy"
                    title={`Scan or click to open ${p.sku}`}
                    style={{ width: 34, height: 34, display: 'block', background: '#fff', borderRadius: 3, padding: 1 }} />
                </td>
                <td className="mono" style={{ color: 'var(--muted)' }}>{p.sku}</td>
                {/* What it IS leads; what the supplier's bill called it sits
                    under it. "TISSOT Lycra" is a mill's wording and names
                    nothing anyone picks off a shelf — see display_name. */}
                <td>
                  <div>{p.name || p.description}</div>
                  {p.description && p.description !== (p.name || '') && (
                    <div className="small" style={{ color: 'var(--muted)' }}
                      title="What the supplier's invoice called it">{p.description}</div>
                  )}
                </td>
                {/* sizes split off one bundle line share a description — the size is what tells them apart */}
                <td>{p.size || '—'}</td>
                <td className="mono" style={{ fontSize: 11 }}>{p.category
                  || <span className="badge review" title="No confident category match — open the product to pick one">unmapped</span>}</td>
                <td>{p.hsn}</td><td>{p.supplier_name || '—'}</td>
                {/* the quantity is a way in, not just a number: 8 pcs means eight
                    individually coded garments, and this is where you see them */}
                <td style={{ textAlign: 'right' }}>
                  {/* Negative stock is a real condition (more dispatched than held)
                      and it must look wrong, not sit in the column as an ordinary
                      figure — a minus sign alone is easy to read straight past. */}
                  <button className="qtylink" onClick={(e) => { e.stopPropagation(); openUnits(p) }}
                    style={p.stock_qty < 0 ? { color: 'var(--danger)', fontWeight: 700 } : undefined}
                    title={p.stock_qty < 0
                      ? `Negative stock — more has been dispatched than was received. Click to see the piece codes.`
                      : `Show the ${p.stock_qty} individual piece codes`}>
                    {p.stock_qty < 0 ? '⚠ ' : ''}{p.stock_qty} {p.uom}
                  </button>
                </td>
                <td style={{ textAlign: 'right' }}>{money(p.avg_cost)}</td>
                <td style={{ textAlign: 'right', color: p.stock_value < 0 ? 'var(--danger)' : undefined }}>
                  ₹ {money(p.stock_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        <Pager {...invPage} noun="product" />
        {/* A list shortened by a filter must say so, or it reads as stock that
            has gone missing — the one misreading this screen cannot afford. */}
        {products.length > 0 && (
          <div className="small" style={{ padding: '10px 0', color: 'var(--muted)' }}>
            Showing {visible.length} of {products.length} product(s)
            {visible.length < products.length && <>
              {' — '}
              <button className="link" style={{ padding: 0 }}
                onClick={() => { setQ(''); setStockScope('all'); setCatFilter(''); setSupFilter('') }}>
                clear search and filters</button>
            </>}
          </div>
        )}
      </div>

      {/* Clicking a quantity opens the pieces behind it: one inventory record of 8
          is eight garments, each with its own code and its own label to print. */}
      {units && (
        <div className="piece-wrap" onClick={() => setUnits(null)}>
          <div className="piece-card" onClick={(e) => e.stopPropagation()}>
            <div className="piece-head">
              <b className="mono">{units.product.sku}</b>
              <span>{units.product.name || units.product.description}</span>
              <span className="small">
                {[units.product.size, units.product.color].filter(Boolean).join(' · ')}
              </span>
              {/* one code per stock unit. For a pair product that unit IS two
                  garments, so the panel says how many pieces are behind it —
                  otherwise "6 codes, 12 pillow covers" reads as a shortfall */}
              <span style={{ marginLeft: 'auto' }} className="small">
                {units.count} code{units.count === 1 ? '' : 's'} for {units.product.stock_qty} {units.product.uom} in stock
                {units.product.pieces_per_unit > 1
                  ? ` · 1 ${units.product.unit_type} = ${units.product.pieces_per_unit} pcs`
                  : ''}
              </span>
              <button className="btn" onClick={() => setUnits(null)}>✕</button>
            </div>

            <div className="piece-body">
              {zoom && (
                <div className="piece-zoom">
                  <img src={api.unitQrSvgUrl(zoom.id, 6)} alt={zoom.code} />
                  <b className="mono">{zoom.code}</b>
                  <span className="small">
                    {zoom.print_count ? `printed ${zoom.print_count}×` : 'not printed yet'}
                    {zoom.last_printed_by ? ` · last by ${zoom.last_printed_by}` : ''}
                  </span>
                  <button className="btn" onClick={() => setZoom(null)}>Close</button>
                </div>
              )}
              {units.units.length === 0 ? (
                <p className="small" style={{ padding: '10px 0' }}>
                  {units.serialisable
                    ? 'No individual codes yet — this stock predates them.'
                    : `No individual codes: ${units.reason}.`}
                </p>
              ) : (
                <div className="piece-grid">
                  {unitPage.slice.map((u) => {
                    // a dead code is indistinguishable from a live one — same format,
                    // same QR, same product — so it has to be marked, not left to the eye
                    const dead = u.state && u.state !== 'posted'
                    return (
                      <div key={u.id} className="piece"
                        style={dead ? { opacity: 0.55, borderColor: 'var(--danger)' } : undefined}>
                        <img src={api.unitQrSvgUrl(u.id, 3)} alt={u.code} loading="lazy" />
                        <div className="code">{u.code}</div>
                        <div className="small" style={{ fontSize: 10 }}>
                          {dead
                            ? <span style={{ color: 'var(--danger)' }}>no posted GRN</span>
                            : (u.print_count ? `printed ${u.print_count}×` : 'not printed')}
                        </div>
                        <div className="acts">
                          <button className="btn" onClick={() => setZoom(u)} title="Show this code large">View</button>
                          <button className="btn" disabled={dead} onClick={() => printOne(u)}
                            title={dead ? 'This code belongs to no posted GRN — it is left over, not a garment'
                              : u.print_count ? 'Print this label again' : 'Print this label'}>
                            {u.print_count ? 'Reprint' : 'Print'}
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
              <Pager {...unitPage} noun="piece code" />
            </div>

            <div className="piece-foot">
              <span className="small">
                {units.can_print === false
                  /* Say the number, say the mismatch, say what to do. A label goes on a
                     garment, so printing more of them than there are garments is not a
                     tidiness problem — it is tags on the floor with nothing to attach
                     them to, every one of which scans as real. */
                  ? <span style={{ color: 'var(--danger)' }}>
                      ⚠ {units.print_block}
                      {units.orphan_units > 0 && <> {' '}<b>{units.orphan_units}</b> of the codes
                        shown belong to no posted GRN — Inventory → <b>Repair</b> removes them.</>}
                    </span>
                  : <>Every piece carries the same SKU and its own code, so a scan says which
                      garment it is — not just which product.</>}
              </span>
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                {units.serialisable && units.can_print !== false
                  && units.count < Math.round(units.product.stock_qty || 0) && (
                  <button className="btn" onClick={makeUnits}
                    title="Create the missing codes for stock received before per-piece codes existed">
                    Generate missing ({Math.round(units.product.stock_qty) - units.count})
                  </button>
                )}
                {units.units.length > 0 && (
                  <button className="btn primary" disabled={units.can_print === false}
                    onClick={() => printUnits(null)}
                    title={units.can_print === false
                      ? 'Inventory mismatch detected — ' + units.print_block
                      : `Print a label for each of the ${units.live_units ?? units.count} pieces`}>
                    🖨 Print all {units.live_units ?? units.count}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {detail && (
        <div className="sidebar" style={{ width: 400, borderRight: 'none', borderLeft: '1px solid var(--line)' }}>
          <div className="head"><h3>{detail.sku}</h3>
            <button className="btn" style={{ padding: '2px 9px' }} onClick={() => setDetail(null)}>×</button></div>
          <div style={{ padding: 16, overflowY: 'auto' }}>
            {/* Inventory is a VIEW of the product, not a second entry form. Everything
                here is decided upstream: description / HSN / UOM come off the invoice,
                category and prices are set on the GRN breakdown, and the physical
                attributes are recorded in the phone app. To change any of it, unpost
                the GRN, correct it there and post again — one place, one truth. */}
            <h4 style={{ color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase', margin: '0 0 10px' }}>Product</h4>
            <div className="kv">
              {/* the name is the category; the supplier's wording keeps its own
                  row rather than the name's, because it is still what a re-buy
                  is matched on and it belongs to the invoice, not to us */}
              <div className="k">Name</div><div><b>{detail.name || detail.description}</b></div>
              <div className="k">On the invoice</div>
              <div style={{ color: 'var(--text-2)' }}>{detail.description || '—'}</div>
              <div className="k">HSN</div><div>{detail.hsn || '—'}</div>
              <div className="k">UOM</div><div>{detail.uom || '—'}</div>
              <div className="k">MRP</div><div>{detail.mrp != null ? '₹ ' + money(detail.mrp) : '—'}</div>
              <div className="k">Category</div>
              <div>{detail.category
                ? <>{detail.category}{detail.category_section && <span style={{ color: 'var(--muted)' }}> · {detail.category_section}</span>}</>
                : <span className="badge review" title="No confident match from the description — set it on the GRN line and re-post">unmapped</span>}</div>
              <div className="k">Supplier</div><div>{detail.supplier_name || '—'}</div>
            </div>

            <h4 style={{ color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase', margin: '18px 0 8px' }}>
              Product attributes</h4>
            <div className="kv" style={{ margin: '0 0 6px' }}>
              {PRODUCT_ATTRS.map(([k, label]) => {
                const v = detail[k]
                const shown = v == null || v === '' ? null : (typeof v === 'number' ? money(v) : String(v))
                return (
                  <React.Fragment key={k}>
                    <div className="k">{label}</div>
                    <div>{shown ?? <span style={{ color: 'var(--muted)' }}>—</span>}</div>
                  </React.Fragment>
                )
              })}
            </div>
            {detail.detailed_by && (
              <div className="small" style={{ color: 'var(--muted)', marginBottom: 4 }}>
                Last detailed by <b>{detail.detailed_by}</b>
              </div>
            )}

            <div className="kv" style={{ margin: '18px 0' }}>
              <div className="k">Current stock</div><div><b>{detail.stock_qty}</b> {detail.uom}</div>
              <div className="k">Avg cost</div><div>₹ {money(detail.avg_cost)}</div>
              <div className="k">Value</div><div>₹ {money(detail.stock_value)}</div>
            </div>

            <h4 style={{ color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase', margin: '18px 0 8px' }}>QR code &amp; label</h4>
            {/* Gated on the SKU, not a barcode: the SKU is the only code we issue
                now, and it is what the QR resolves to. */}
            {detail.sku ? (
              <div>
                <div style={{ background: '#fff', border: '1px solid var(--line)', borderRadius: 6, padding: '8px 10px', textAlign: 'center' }}>
                  <img src={api.qrSvgUrl(detail.id)} alt="QR" style={{ width: 150, height: 150 }} />
                  <div className="mono" style={{ fontSize: 12, marginTop: 2, color: '#000' }}>{detail.sku}</div>
                </div>
                {detail.barcode && (
                  <div className="small" style={{ marginTop: 6, color: 'var(--muted)' }}>
                    Supplier's printed code: <span className="mono">{detail.barcode}</span>
                  </div>
                )}
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <a className="btn primary" href={api.labelUrl(detail.id)} target="_blank" rel="noreferrer">🖨 Print label</a>
                  <button className="btn" onClick={genBarcode} title="Re-run identifier assignment">Ensure IDs</button>
                </div>
              </div>
            ) : detail.detailed ? (
              <button className="btn primary" onClick={genBarcode}>Generate SKU + QR</button>
            ) : (
              <p className="small">No SKU or QR yet.</p>
            )}

            <h4 style={{ color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase', margin: '0 0 8px' }}>Adjust stock</h4>
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
              <div className="field" style={{ width: 110 }}><label>Set stock to</label>
                <input value={adjQty} placeholder={detail.stock_qty} onChange={(e) => setAdjQty(e.target.value)} /></div>
              <div className="field" style={{ flex: 1 }}><label>Reason</label>
                <input value={adjNote} placeholder="e.g. physical count" onChange={(e) => setAdjNote(e.target.value)} /></div>
              <button className="btn" onClick={doAdjust}>Apply</button>
            </div>

            <h4 style={{ color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase', margin: '18px 0 8px' }}>
              Physical details {detail.detailed ? <span className="badge confirmed" style={{ marginLeft: 6 }}>detailed</span> : <span className="badge review" style={{ marginLeft: 6 }}>pending</span>}</h4>
            {detail.detailed ? (
              <div className="kv" style={{ marginBottom: 6 }}>
                <div className="k">Color</div><div>{detail.color || '—'}</div>
                <div className="k">Size</div><div>{detail.size || '—'}</div>
                <div className="k">Pattern</div><div>{detail.pattern || '—'}</div>
                <div className="k">Fit</div><div>{detail.fit || '—'}</div>
                <div className="k">Type</div><div>{detail.product_type || '—'}</div>
                <div className="k">Material</div><div>{detail.material || '—'}</div>
                <div className="k">Design No</div><div>{detail.design_no || '—'}</div>
                <div className="k">Sale price</div><div>{detail.sale_price != null ? '₹ ' + money(detail.sale_price) : '—'}</div>
                <div className="k">Discount %</div><div>{detail.sale_discount_pct != null ? detail.sale_discount_pct + '%' : '—'}</div>
                <div className="k">By</div><div>{detail.detailed_by || '—'}{detail.detailed_at ? ' · ' + fmtDate(detail.detailed_at) : ''}</div>
              </div>
            ) : <p className="small">Not yet detailed.</p>}

            <h4 style={{ color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase', margin: '18px 0 8px' }}>Stock movements</h4>
            <table className="items"><thead><tr><th>Kind</th><th style={{ textAlign: 'right' }}>Qty</th>
              <th style={{ textAlign: 'right' }}>Rate</th><th style={{ textAlign: 'right' }}>Balance</th></tr></thead>
              <tbody>{detail.movements.map((m) => (
                <tr key={m.id}><td>{m.kind}</td>
                  <td style={{ textAlign: 'right', color: m.qty_delta >= 0 ? 'var(--ok)' : 'var(--danger)' }}>
                    {m.qty_delta >= 0 ? '+' : ''}{m.qty_delta}</td>
                  <td style={{ textAlign: 'right' }}>{money(m.rate)}</td>
                  <td style={{ textAlign: 'right' }}>{m.balance_after}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------- the product record, as every stock movement has to show it ----------
//
// Stock Outward, Stock Inward and Purchase Return are all somebody standing over
// a carton matching a row on a screen against a garment in their hand. A barcode
// and a description cannot settle that — four sizes of one style share both — so
// these three render the whole record the backend sends (services/stock_view.py):
// the QR that is actually scanned, the name, the attributes that tell one variant
// from its siblings, and the batch the stock was received on.

// One QR, sized for where it is shown. Renders the real code, so it scans off the
// screen — a picker with a phone doesn't have to find the printed label first.
function ProductQr({ product, size = 40, title }) {
  if (!product) return <span style={{ color: 'var(--muted)' }}>—</span>
  return (
    <img className="pqr" src={api.qrSvgUrl(product.product_id, size > 60 ? 4 : 2)}
      alt={`QR ${product.sku || ''}`} loading="lazy"
      title={title || `${product.sku || ''} — scan or click to enlarge`}
      style={{ width: size, height: size }} />
  )
}

// The attribute tuple as chips. Blank attributes are dropped by the backend, so a
// sparsely-detailed product shows what it has instead of a row of dashes.
function ProductAttrs({ product, showCategory = true }) {
  if (!product) return null
  const chips = product.attributes || []
  if (!chips.length && !product.category) {
    return <span className="small" style={{ color: 'var(--muted)' }}>no details recorded yet</span>
  }
  return (
    <div className="attrchips">
      {chips.map((a) => (
        <span key={a.key} className="attrchip" title={a.label}>
          <i>{a.label}</i>{a.value}
        </span>
      ))}
      {showCategory && product.category && <span className="attrchip cat">{product.category}</span>}
    </div>
  )
}

// Which receipt the goods came in on — this system's batch. Stock is pooled per
// SKU, so a dispatch can draw on more than one GRN; the extras are named in the
// tooltip rather than hidden behind a single "the" batch.
function BatchTag({ product }) {
  const b = product?.batch
  if (!b) return <span style={{ color: 'var(--muted)' }}>—</span>
  const more = (product.batches || []).slice(1)
  return (
    <span className="batchtag" title={[
      b.grn_no ? `GRN ${b.grn_no}` : null,
      b.invoice_number ? `Invoice ${b.invoice_number}${b.invoice_date ? ' · ' + fmtDate(b.invoice_date) : ''}` : null,
      b.bundle_code ? `Bundle ${b.bundle_code}` : null,
      b.supplier ? `From ${b.supplier}` : null,
      more.length ? `+ ${more.length} earlier receipt(s): ${more.map((x) => x.label).join(', ')}` : null,
    ].filter(Boolean).join('\n')}>
      {b.label}{more.length ? ` +${more.length}` : ''}
    </span>
  )
}

// The identity block used in a table cell: name, SKU, variant chips.
function ProductIdent({ product, fallback }) {
  if (!product) return <span>{fallback || '—'}</span>
  return (
    <div className="pident">
      <div className="nm">{product.name}</div>
      <div className="sub">
        <span className="mono">{product.sku || product.code}</span>
        {product.uom && <span>{product.uom}</span>}
        {product.hsn && <span>HSN {product.hsn}</span>}
        {product.supplier_barcode && (
          <span className="mono" title="The supplier's own printed code">
            ⌗ {product.supplier_barcode}</span>
        )}
      </div>
      <ProductAttrs product={product} />
    </div>
  )
}

// The full card, opened by clicking a QR — everything about the item at once,
// with the code big enough to scan across a packing bench.
function ProductCardModal({ product, onClose }) {
  if (!product) return null
  const money2 = (v) => (v == null ? '—' : '₹ ' + money(v))
  return (
    <div className="piece-wrap" onClick={onClose}>
      <div className="piece-card" style={{ maxWidth: 620 }} onClick={(e) => e.stopPropagation()}>
        <div className="piece-head">
          <b className="mono">{product.sku || product.code}</b>
          <span>{product.name}</span>
          <button className="btn" style={{ marginLeft: 'auto' }} onClick={onClose}>✕</button>
        </div>
        <div className="piece-body" style={{ display: 'flex', gap: 20 }}>
          <div style={{ textAlign: 'center' }}>
            <img src={api.qrSvgUrl(product.product_id, 5)} alt="QR"
              style={{ width: 190, height: 190, background: '#fff', borderRadius: 6, padding: 8 }} />
            <div className="mono" style={{ fontSize: 12, marginTop: 4 }}>{product.sku || product.code}</div>
            <a className="btn" style={{ marginTop: 8, display: 'inline-block' }}
              href={api.labelUrl(product.product_id)} target="_blank" rel="noreferrer">🖨 Print label</a>
          </div>
          <div style={{ flex: 1 }}>
            <div className="kv" style={{ gridTemplateColumns: '110px 1fr' }}>
              <div className="k">Product</div><div>{product.name}</div>
              <div className="k">Category</div><div>{product.category || '—'}
                {product.category_section ? <span className="small"> · {product.category_section}</span> : null}</div>
              {(product.attributes || []).map((a) => (
                <React.Fragment key={a.key}>
                  <div className="k">{a.label}</div><div>{a.value}</div>
                </React.Fragment>
              ))}
              <div className="k">HSN / UOM</div><div>{product.hsn || '—'} · {product.uom || '—'}</div>
              <div className="k">Batch</div><div><BatchTag product={product} /></div>
              <div className="k">Supplier</div><div>{product.supplier || '—'}</div>
              <div className="k">In stock</div><div><b>{product.stock_qty}</b> {product.uom}</div>
              {/* the two prices, kept visibly apart: one is what we paid, the
                  other what we sell for, and a debit note may only use the first */}
              <div className="k">GRN cost</div><div>{money2(product.grn_cost)}
                <span className="small" style={{ color: 'var(--muted)' }}> — purchase price</span></div>
              <div className="k">MRP / Sale</div><div>{money2(product.mrp)} / {money2(product.sale_price)}
                <span className="small" style={{ color: 'var(--muted)' }}> — selling side</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// One dispatch / receipt / return line, rendered the same way on all three
// screens. `cols` are the screen-specific numbers appended after the identity.
function ProductRow({ line, onZoom, children, style }) {
  const p = line.product
  return (
    <tr style={style}>
      <td style={{ padding: 3, width: 46 }}>
        <span onClick={() => p && onZoom?.(p)} style={{ cursor: p ? 'zoom-in' : 'default' }}>
          <ProductQr product={p} />
        </span>
      </td>
      <td><ProductIdent product={p} fallback={line.description} /></td>
      <td className="small"><BatchTag product={p} /></td>
      {children}
    </tr>
  )
}

// A scan field. The warehouse types nothing here — the imager sends the payload
// and an Enter, which is why submit is on Enter rather than a button.
function ScanBox({ onScan, placeholder, label }) {
  const [code, setCode] = useState('')
  const submit = () => { const c = code.trim(); if (!c) return; setCode(''); onScan(c) }
  return (
    <div className="scanbox">
      <span className="ico">⌗</span>
      <input value={code} autoFocus onChange={(e) => setCode(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
        placeholder={placeholder || 'Scan a QR / piece label / SKU…'} />
      <button className="btn" onClick={submit}>{label || 'Add'}</button>
    </div>
  )
}

// ---------- picking many products at once ----------
// The dropdown on a dispatch row answers one question — "which product is THIS
// line?" — and answers it once per line. A note carrying twenty products was
// twenty blank rows and twenty hunts down the same list, which is where the time
// on this screen went. This is that list as a tick sheet: filter it, tick what is
// going, correct the quantities, and the rows arrive already filled in.
//
// Two rules it keeps, both so that nothing you have already typed can be undone
// from in here:
//   * it only ADDS. A product already on the note is listed and disabled rather
//     than dropped from the list, so "where is it?" is answered on screen.
//   * quantity starts at the whole of what is on hand, because dispatching a
//     line usually means dispatching the line — and it is a text box, not a
//     figure that is committed to.
// Stock it cannot dispatch is not offered at all: a row for something with none
// on hand can only be refused at posting time.
function ProductPicker({ products, already, onAdd, onClose }) {
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState({})        // product_id -> qty, as typed
  const here = new Set((already || []).map(Number))
  const shown = products.filter((p) => (+p.stock_qty || 0) > 0)
    .filter((p) => matches(p, q, ['description', 'sku', 'size', 'color', 'category']))
  const chosen = Object.keys(picked)
  const units = chosen.reduce((n, k) => n + (+picked[k] || 0), 0)

  // What the header tick acts on: the rows ON SCREEN that are not already on the
  // note. So "pillow" then tick-all is "every pillow cover", not the warehouse —
  // and un-ticking it leaves anything picked under an earlier search alone,
  // because taking away a choice the filter is hiding is the one thing a tick
  // box must not do quietly. The footer count is what tells you they are there.
  const selectable = shown.filter((p) => !here.has(+p.id))
  const allOn = selectable.length > 0 && selectable.every((p) => p.id in picked)
  const someOn = selectable.some((p) => p.id in picked)

  const toggle = (p) => setPicked((m) => {
    const next = { ...m }
    if (p.id in next) delete next[p.id]
    else next[p.id] = String(p.stock_qty)
    return next
  })
  const toggleAll = () => setPicked((m) => {
    const next = { ...m }
    selectable.forEach((p) => {
      if (allOn) delete next[p.id]
      else if (!(p.id in next)) next[p.id] = String(p.stock_qty)
    })
    return next
  })
  const add = () => {
    const rows = chosen
      .map((k) => ({ product_id: String(k), qty: String(+picked[k] || 0) }))
      .filter((r) => +r.qty > 0)
    if (rows.length) onAdd(rows)
  }

  return (
    <div className="piece-wrap" onClick={onClose}>
      <div className="piece-card" style={{ maxWidth: 880 }} onClick={(e) => e.stopPropagation()}>
        <div className="piece-head">
          <b>Add products</b>
          <span className="small" style={{ color: 'var(--muted)' }}>{shown.length} in stock</span>
          <button className="btn" style={{ marginLeft: 'auto' }} onClick={onClose}>✕</button>
        </div>
        <div className="piece-body">
          <SearchBox value={q} onChange={setQ} placeholder="Search product / SKU / size / colour…" />
          <div className="tablewrap" style={{ marginTop: 10 }}>
            <table className="items entry">
              <thead><tr>
                <th style={{ width: 30 }}>
                  {/* half-ticked when only some of the shown rows are chosen —
                      a box that reads "none" over a part-selection is a lie */}
                  <input type="checkbox" checked={allOn} disabled={!selectable.length}
                    ref={(el) => { if (el) el.indeterminate = !allOn && someOn }}
                    onChange={toggleAll}
                    title={allOn ? 'Clear the ones shown'
                      : `Tick all ${selectable.length} shown`} />
                </th><th>Product</th><th>Category</th>
                <th style={{ textAlign: 'right', width: 90 }}>On hand</th>
                <th style={{ textAlign: 'right', width: 110 }}>Qty</th>
              </tr></thead>
              <tbody>{shown.map((p) => {
                const on = p.id in picked
                const got = here.has(+p.id)
                return (
                  <tr key={p.id} style={{ opacity: got ? 0.5 : 1, cursor: got ? 'default' : 'pointer' }}
                    onClick={() => { if (!got) toggle(p) }}
                    title={got ? 'Already on this note — change the quantity on the row itself' : ''}>
                    <td><input type="checkbox" checked={on} disabled={got} readOnly /></td>
                    <td>
                      <div>{p.name || p.description}</div>
                      <span className="small mono" style={{ color: 'var(--muted)' }}>
                        {p.sku}{p.size ? ' · ' + p.size : ''}{p.color ? ' · ' + p.color : ''}
                        {got ? ' · on this note' : ''}</span>
                    </td>
                    <td className="small">{p.category || '—'}</td>
                    <td className="num">{p.stock_qty} {p.uom}</td>
                    {/* the quantity box is the one place a click must not tick the row */}
                    <td className="num" onClick={(e) => e.stopPropagation()}>
                      <input value={picked[p.id] ?? ''} disabled={got || !on}
                        onChange={(e) => setPicked((m) => ({ ...m, [p.id]: e.target.value }))} /></td>
                  </tr>
                )
              })}</tbody>
            </table>
          </div>
          {shown.length === 0 && <div className="empty" style={{ marginTop: 24 }}>
            {q ? 'Nothing matches.' : 'No product has stock to dispatch.'}</div>}
        </div>
        <div className="piece-foot">
          <span className="small">{chosen.length} selected · {round3(units)} unit(s)</span>
          {/* the only way to drop ticks a search is currently hiding */}
          {chosen.length > 0 && (
            <button className="btn" style={{ padding: '2px 9px' }} onClick={() => setPicked({})}
              title="Drop every tick, including any the search is hiding">Clear</button>
          )}
          <div style={{ flex: 1 }} />
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!units} onClick={add}>
            Add{chosen.length ? ` ${chosen.length}` : ''}</button>
        </div>
      </div>
    </div>
  )
}

// ---------- stock outward ----------
function StockOutward({ toast }) {
  const [list, setList] = useState([])
  const [products, setProducts] = useState([])
  const [sel, setSel] = useState(null)
  const [creating, setCreating] = useState(false)
  // the sidebar's scope filter — the same chips Stock Inward and Returns use
  const [scope, setScope] = useState('all')
  // Where the goods are being picked from. WAREHOUSE is the normal case; MULTI is
  // for a dispatch drawn from more than one place, so the paperwork says so
  // instead of naming a single location that isn't the whole truth.
  const FROM_LOCATIONS = ['WAREHOUSE', 'MULTI']
  const [form, setForm] = useState({ date: '', to_destination: '', packed_by: '',
    from_location: 'WAREHOUSE', lines: [] })
  const [q, setQ] = useState('')
  // derived AFTER the state it reads — a const referenced above its own
  // declaration is a temporal-dead-zone throw, and in a render that is the whole
  // tab going blank rather than one broken value
  const shown = list.filter((o) => scope === 'all' || o.status === scope)
    .filter((o) => matches(o, q, ['to_destination', 'code', 'status']))
  const outPage = usePaged(shown, 50)
  const [zoom, setZoom] = useState(null)          // a product card, opened large
  const [cards, setCards] = useState({})          // product_id -> full record, for the draft rows
  const [picking, setPicking] = useState(false)   // the tick-sheet over stock is open
  const refresh = useCallback(() => api.listOutwards().then(setList), [])
  useEffect(() => { refresh(); api.listProducts().then(setProducts) }, [refresh])
  useEffect(() => { if (sel) api.getOutward(sel).then(setForm2); function setForm2(o){ setDetail(o) } }, [sel])
  const [detail, setDetail] = useState(null)

  // A line being packed shows the same record the posted one will: pull the card
  // as soon as a product is chosen, so the picker verifies BEFORE it is dispatched
  // rather than reading it back afterwards.
  const loadCard = useCallback(async (id) => {
    if (!id || cards[id]) return
    try { const c = await api.productCard(id); setCards((m) => ({ ...m, [c.product_id]: c })) }
    catch { /* a product with no card is still dispatchable — the row just stays plain */ }
  }, [cards])
  // The same fill for a whole batch of rows. Deduped against what is already
  // held BEFORE the requests go out: loadCard in a loop reads one stale `cards`
  // and asks the server for the same record as many times as it is called.
  const loadCards = useCallback(async (ids) => {
    const want = [...new Set(ids.map(Number))].filter((id) => id && !cards[id])
    await Promise.all(want.map(async (id) => {
      try { const c = await api.productCard(id); setCards((m) => ({ ...m, [c.product_id]: c })) }
      catch { /* as above — the row still dispatches, it just stays plain */ }
    }))
  }, [cards])

  const addLine = () => setForm({ ...form, lines: [...form.lines, { product_id: '', qty: 1 }] })
  // Rows arriving from the picker. They land on the END of the note, and a blank
  // row left over from "+ add item" is consumed rather than left sitting under
  // them looking like an item nobody finished choosing.
  const addPicked = (rows) => {
    setForm((f) => ({ ...f, lines: [...f.lines.filter((l) => l.product_id), ...rows] }))
    setPicking(false)
    loadCards(rows.map((r) => r.product_id))
    toast(`✓ ${rows.length} product${rows.length === 1 ? '' : 's'} added`, 'ok')
  }
  const updLine = (i, k, v) => {
    const l = form.lines.map(x => ({ ...x })); l[i][k] = v; setForm({ ...form, lines: l })
    if (k === 'product_id') loadCard(v)
  }
  const rmLine = (i) => setForm({ ...form, lines: form.lines.filter((_, j) => j !== i) })
  // Scanning is the fast path in and the safe one: the code resolves to exactly
  // one product, so nobody picks the wrong size off a dropdown of look-alikes.
  const addScanned = async (code) => {
    try {
      const c = await api.productCard(code)
      setCards((m) => ({ ...m, [c.product_id]: c }))
      const at = form.lines.findIndex((l) => +l.product_id === c.product_id)
      if (at >= 0) {
        const l = form.lines.map(x => ({ ...x })); l[at].qty = (+l[at].qty || 0) + 1
        setForm({ ...form, lines: l })
        toast(`+1 ${c.name}${c.variant ? ' · ' + c.variant : ''} (${l[at].qty})`, 'ok')
      } else {
        setForm({ ...form, lines: [...form.lines, { product_id: String(c.product_id), qty: 1 }] })
        toast(`✓ ${c.name}${c.variant ? ' · ' + c.variant : ''}`, 'ok')
      }
    } catch (e) { toast(e.detail || `Nothing matches “${code}”`, 'err') }
  }
  const save = async () => {
    const lines = form.lines.filter(l => l.product_id).map(l => ({ product_id: +l.product_id, qty: +l.qty }))
    if (!lines.length) { toast('Add at least one product', 'err'); return }
    const o = await api.createOutward({ ...form, lines })
    toast(`✓ Outward ${o.code} created`, 'ok'); setCreating(false)
    setForm({ date: '', to_destination: '', packed_by: '', from_location: 'WAREHOUSE', lines: [] })
    refresh(); setSel(o.id)
  }
  const post = async () => {
    try { const r = await api.postOutward(sel); toast(`✓ Dispatched · ${r.total_qty} units out`, 'ok'); api.getOutward(sel).then(setDetail); refresh() }
    catch (e) {
      const d = e.detail
      if (d && d.error === 'insufficient_stock') toast('Insufficient stock: ' + d.problems.map(p => `${p.product} (need ${p.requested}, have ${p.on_hand})`).join('; '), 'err')
      else toast('Post failed', 'err')
    }
  }
  // scanning a garment against an open dispatch — is it on this note?
  const verify = async (code) => {
    try {
      const r = await api.verifyOutward(sel, code)
      setZoom(r.product)
      toast(r.matched ? `✓ On this dispatch — ${r.product.name}`
        : `⚠ NOT on this dispatch — ${r.product.name}${r.product.variant ? ' · ' + r.product.variant : ''}`,
        r.matched ? 'ok' : 'err')
    } catch (e) { toast(e.detail || `Nothing matches “${code}”`, 'err') }
  }
  return (
    <div className="body">
      <Sidebar id="outward" label="Outwards">
        <div className="head"><h3>Outwards · {list.length}</h3>
          <button className="btn primary" style={{ padding: '4px 10px' }} onClick={() => { setCreating(true); setSel(null) }}>+ New</button></div>
        {list.length > 0 && <>
          <SearchBox value={q} onChange={setQ} placeholder="Search destination, code, status…" />
          <div className="toolbar"><FilterChips value={scope} onChange={setScope} options={[
            ['draft', 'Draft', list.filter((o) => o.status === 'draft').length, 'Prepared, nothing dispatched yet'],
            ['posted', 'Sent', list.filter((o) => o.status === 'posted').length, 'Dispatched, not yet accepted'],
            ['received', 'Received', list.filter((o) => o.status === 'received').length, 'Accepted at the destination'],
            ['all', 'All', list.length, 'Every dispatch'],
          ]} /></div>
        </>}
        <div className="list">
          {list.length > 0 && shown.length === 0 && <div className="empty" style={{ marginTop: 30, fontSize: 13 }}>
            Nothing matches. Try “All” or clear the search.</div>}
          {outPage.slice.map((o) => (
            <div key={o.id} className={'doc-row' + (sel === o.id && !creating ? ' sel' : '')} onClick={() => { setSel(o.id); setCreating(false) }}>
              <div className="t">{o.to_destination || o.code}</div>
              <div className="m"><span className={'badge ' + (o.status === 'posted' ? 'confirmed' : 'uploaded')}>{o.status}</span>
                <span>{o.code}</span><span style={{ marginLeft: 'auto' }}>{o.total_qty} units</span></div>
            </div>
          ))}
        </div>
        <Pager {...outPage} noun="dispatch" nouns="dispatches" />
      </Sidebar>
      {creating ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="editor">
            <h2 style={{ marginTop: 0 }}>New Stock Outward</h2>
            <div className="grid" style={{ maxWidth: 640 }}>
              <DateField label="Date" value={form.date} onChange={(v) => setForm({ ...form, date: v })} />
              <div className="field"><label>To (destination)</label><input value={form.to_destination} placeholder="e.g. Tasjue Silks, Tirupur" onChange={(e) => setForm({ ...form, to_destination: e.target.value })} /></div>
              <div className="field"><label>Packed by</label><input value={form.packed_by} onChange={(e) => setForm({ ...form, packed_by: e.target.value })} /></div>
              <div className="field"><label>From location</label>
                <select value={form.from_location} style={{ width: '100%' }}
                  onChange={(e) => setForm({ ...form, from_location: e.target.value })}>
                  {FROM_LOCATIONS.map((l) => <option key={l} value={l}>{l}</option>)}
                </select></div>
            </div>
            <Section id="outward.new-items" title="Items to dispatch" style={{ marginTop: 18 }}>
              <ScanBox onScan={addScanned} placeholder="Scan a QR / piece label / SKU to add…" />
              <div className="tablewrap">
              <table className="items">
                <thead><tr><th style={{ width: 46 }}>QR</th><th>Product</th><th>Batch</th>
                  <th style={{ width: 230 }}>Or pick from inventory</th>
                  <th style={{ textAlign: 'right' }}>Qty</th>
                  <th style={{ textAlign: 'right' }}>On hand</th><th></th></tr></thead>
                <tbody>{form.lines.map((l, i) => {
                  const card = cards[+l.product_id]
                  const short = card && +l.qty > (card.stock_qty ?? 0)
                  return (
                    <tr key={i}>
                      <td style={{ padding: 3 }}>
                        <span onClick={() => card && setZoom(card)} style={{ cursor: card ? 'zoom-in' : 'default' }}>
                          <ProductQr product={card} /></span></td>
                      <td><ProductIdent product={card} fallback="— nothing selected —" /></td>
                      <td className="small"><BatchTag product={card} /></td>
                      <td><select value={l.product_id} onChange={(e) => updLine(i, 'product_id', e.target.value)}
                        style={{ width: '100%', background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--line)', borderRadius: 5, padding: '5px' }}>
                        <option value="">— select product —</option>
                        {products.map(p => <option key={p.id} value={p.id}>
                          {p.name || p.description}{p.size ? ' · ' + p.size : ''}{p.color ? ' · ' + p.color : ''} (stock {p.stock_qty})</option>)}
                      </select></td>
                      <td className="num"><input value={l.qty} onChange={(e) => updLine(i, 'qty', e.target.value)} /></td>
                      <td style={{ textAlign: 'right', color: short ? 'var(--danger)' : undefined }}
                        title={short ? 'More than is on hand — posting will be refused' : ''}>
                        {card ? card.stock_qty : '—'}{short ? ' ⚠' : ''}</td>
                      <td><button className="btn" style={{ padding: '2px 7px' }} onClick={() => rmLine(i)}>×</button></td>
                    </tr>
                  )
                })}</tbody>
              </table>
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button className="btn primary" onClick={() => setPicking(true)}
                  title="Tick several products off a list of what is in stock and add them all at once">
                  ⊞ Pick products</button>
                <button className="btn" onClick={addLine}>+ add item</button>
              </div>
            </Section>
          </div>
          <div className="actionbar"><div className="spacer" />
            <button className="btn" onClick={() => setCreating(false)}>Cancel</button>
            <button className="btn primary" onClick={save}>Create Outward</button></div>
        </div>
      ) : detail ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="editor">
            <div style={{ display: 'flex', gap: 12, alignItems: 'baseline' }}>
              <h2 style={{ margin: 0 }}>{detail.to_destination}</h2>
              <span className={'badge ' + (detail.status === 'posted' ? 'confirmed' : 'uploaded')}>{detail.status}</span></div>
            <div className="kv" style={{ margin: '12px 0 20px', gridTemplateColumns: '130px 1fr 130px 1fr' }}>
              <div className="k">Code</div><div>{detail.code}</div><div className="k">Date</div><div>{fmtDate(detail.date)}</div>
              <div className="k">From</div><div>{detail.from_location}</div><div className="k">Packed by</div><div>{detail.packed_by || '—'}</div>
              {detail.status === 'received' && <>
                <div className="k">Received by</div><div>{detail.received_by || '—'}</div>
                <div className="k">Received on</div><div>{fmtDate(detail.received_date || detail.received_at)}</div>
              </>}
            </div>
            {detail.status !== 'draft' && (
              <div style={{ marginBottom: 14 }}>
                <ScanBox onScan={verify} label="Verify"
                  placeholder="Scan a garment to check it belongs to this dispatch…" />
              </div>
            )}
            <Section id="outward.items" title="Items">
              <div className="tablewrap">
              <table className="items">
                <thead><tr><th style={{ width: 46 }}>QR</th><th>Product</th><th>Batch</th>
                  <th style={{ textAlign: 'right' }}>Qty</th>
                  {detail.status === 'received' && <th style={{ textAlign: 'right' }}>Accepted</th>}
                  <th style={{ textAlign: 'right' }}>Cost</th><th style={{ textAlign: 'right' }}>Value</th>
                  <th style={{ textAlign: 'right' }}>On hand</th></tr></thead>
                <tbody>{detail.lines.map(l => (
                  <ProductRow key={l.id} line={l} onZoom={setZoom}>
                    <td style={{ textAlign: 'right' }}>{l.qty}</td>
                    {detail.status === 'received' && (
                      <td style={{ textAlign: 'right', color: l.short_qty > 0 ? 'var(--danger)' : 'var(--ok)' }}>
                        {l.accepted_qty}{l.short_qty > 0 ? ` (−${l.short_qty})` : ''}</td>
                    )}
                    <td style={{ textAlign: 'right' }} title="Weighted-average purchase cost">{money(l.rate)}</td>
                    <td style={{ textAlign: 'right' }}>{money(l.value)}</td>
                    <td style={{ textAlign: 'right' }}>{l.stock_on_hand}</td>
                  </ProductRow>
                ))}</tbody>
              </table>
              </div>
              <div className="items-foot"><span>{detail.lines.length} items</span>
                <span>Σ qty <b>{detail.total_qty}</b></span>
                {detail.status === 'received' && <span>accepted <b>{detail.accepted_qty}</b></span>}
                {detail.shortfall > 0 && <span style={{ color: 'var(--danger)' }}>short <b>{detail.shortfall}</b></span>}
              </div>
            </Section>
          </div>
          <div className="actionbar">
            <span className="small">{detail.status === 'received'
              ? `Received at ${detail.to_destination || 'the destination'}.`
              : detail.status === 'posted'
                ? 'Dispatched — stock reduced in Inventory. Accept it on Stock Inward when it lands.'
                : 'Posting reduces warehouse stock for each item.'}</span>
            <div className="spacer" />
            <button className="btn primary" disabled={detail.status !== 'draft'} onClick={post}>
              {detail.status === 'draft' ? 'Post Outward (reduce stock)' : 'Posted ✓'}</button>
          </div>
        </div>
      ) : <div className="empty">Select an outward, or click “+ New” to dispatch stock.</div>}
      {picking && creating && (
        <ProductPicker products={products} onAdd={addPicked} onClose={() => setPicking(false)}
          already={form.lines.map((l) => l.product_id)} />
      )}
      {zoom && <ProductCardModal product={zoom} onClose={() => setZoom(null)} />}
    </div>
  )
}

// ---------- stock inward (accepting a dispatched transfer at the destination) ----------
function StockInward({ toast }) {
  const [list, setList] = useState([])
  const [sel, setSel] = useState(null)
  const [detail, setDetail] = useState(null)
  const [scope, setScope] = useState('posted')     // awaiting receipt | already received
  const [acc, setAcc] = useState({})               // line_id -> accepted qty (blank = all)
  const [who, setWho] = useState('')
  const [date, setDate] = useState('')
  const [q, setQ] = useState('')
  const [zoom, setZoom] = useState(null)
  const [hit, setHit] = useState(null)             // the line a scan just landed on
  const inwPage = usePaged(
    list.filter((o) => matches(o, q, ['to_destination', 'code', 'status'])), 50)

  const refresh = useCallback(() => api.listOutwards(scope).then(setList), [scope])
  useEffect(() => { refresh() }, [refresh])
  const open = (id) => api.getOutward(id).then((o) => { setSel(id); setDetail(o); setAcc({}); setHit(null) })

  // Scanning while counting the box in: it says which line the garment is, and
  // ticks one more onto its accepted count. Something not on the note is the
  // error worth shouting about — that is a mis-dispatch, not a miscount.
  const scan = async (code) => {
    try {
      const r = await api.verifyOutward(sel, code)
      if (!r.matched) {
        setZoom(r.product)
        toast(`⚠ NOT on this transfer — ${r.product.name}${r.product.variant ? ' · ' + r.product.variant : ''}`, 'err')
        return
      }
      const line = detail.lines.find((l) => l.id === r.line_id)
      const now = (acc[r.line_id] ?? '') === '' ? 1 : +acc[r.line_id] + 1
      if (now > (line?.qty ?? 0)) {
        toast(`All ${line.qty} of ${r.product.name} are already counted in`, 'err'); return
      }
      setAcc({ ...acc, [r.line_id]: now }); setHit(r.line_id)
      toast(`✓ ${r.product.name}${r.product.variant ? ' · ' + r.product.variant : ''} — ${now} of ${line.qty}`, 'ok')
    } catch (e) { toast(e.detail || `Nothing matches “${code}”`, 'err') }
  }

  const receive = async () => {
    try {
      const accepted = {}
      Object.entries(acc).forEach(([k, v]) => { if (v !== '') accepted[k] = +v })
      const r = await api.receiveOutward(sel, { received_by: who, date, accepted })
      toast(r.shortfall > 0
        ? `✓ Received ${r.accepted_qty} of ${r.total_qty} — ${r.shortfall} short`
        : `✓ Received in full · ${r.accepted_qty} units`, r.shortfall > 0 ? 'err' : 'ok')
      open(sel); refresh()
    } catch (e) { toast('Receive failed: ' + (e.detail || e.message), 'err') }
  }

  const editable = detail && detail.status === 'posted'
  const acceptedOf = (l) => ((acc[l.id] ?? '') === '' ? (l.qty || 0) : +acc[l.id] || 0)
  const counted = detail ? detail.lines.reduce((s, l) => s + acceptedOf(l), 0) : 0

  return (
    <div className="body">
      <Sidebar id="inward" label="Stock Inward">
        <div className="head"><h3>Stock Inward · {list.length}</h3></div>
        <div style={{ display: 'flex', gap: 6, padding: '0 12px 8px' }}>
        </div>
        <div className="toolbar"><FilterChips value={scope}
          onChange={(k) => { setScope(k); setSel(null); setDetail(null) }} options={[
            ['posted', 'Awaiting', null, 'Dispatched and waiting to be counted in'],
            ['received', 'Received', null, 'Already accepted'],
            ['all', 'All', null, 'Every transfer'],
          ]} /></div>
        {list.length > 0 && <SearchBox value={q} onChange={setQ} placeholder="Search destination, code…" />}
        <div className="list">
          {list.length === 0 && <div className="empty" style={{ marginTop: 30, fontSize: 13 }}>
            {scope === 'posted' ? 'Nothing in transit — dispatched transfers appear here to be received.' : 'Nothing here yet.'}</div>}
          {inwPage.slice.map((o) => (
            <div key={o.id} className={'doc-row' + (sel === o.id ? ' sel' : '')} onClick={() => open(o.id)}>
              <div className="t">{o.to_destination || o.code}</div>
              <div className="m"><span className={'badge ' + (o.status === 'received' ? 'confirmed' : 'review')}>
                {o.status === 'received' ? 'received' : 'in transit'}</span>
                <span>{o.code}</span><span style={{ marginLeft: 'auto' }}>{o.total_qty} units</span></div>
              {o.status === 'received' && o.shortfall > 0 && (
                <div className="m"><span style={{ color: 'var(--danger)' }}>{o.shortfall} short</span></div>)}
            </div>
          ))}
        </div>
        <Pager {...inwPage} noun="transfer" />
      </Sidebar>
      {detail ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="editor">
            <div style={{ display: 'flex', gap: 12, alignItems: 'baseline' }}>
              <h2 style={{ margin: 0 }}>{detail.to_destination || detail.code}</h2>
              <span className={'badge ' + (detail.status === 'received' ? 'confirmed' : 'review')}>
                {detail.status === 'received' ? 'received' : 'in transit'}</span>
            </div>
            <div className="kv" style={{ margin: '12px 0 18px', gridTemplateColumns: '130px 1fr 130px 1fr' }}>
              <div className="k">Package</div><div className="mono">{detail.code}</div>
              <div className="k">Dispatched</div><div>{fmtDate(detail.date || detail.posted_at)}</div>
              <div className="k">From</div><div>{detail.from_company} · {detail.from_location}</div>
              <div className="k">Packed by</div><div>{detail.packed_by || '—'}</div>
              {detail.status === 'received' && <>
                <div className="k">Received by</div><div>{detail.received_by || '—'}</div>
                <div className="k">Received on</div><div>{fmtDate(detail.received_date || detail.received_at)}</div>
              </>}
            </div>
            {editable && (
              <div style={{ marginBottom: 14 }}>
                <ScanBox onScan={scan} label="Count in"
                  placeholder="Scan each garment as it comes out of the box…" />
              </div>
            )}
            <Section id="inward.lines" title={editable ? 'Check the goods in' : 'Goods received'}>
              <div className="tablewrap">
              <table className="items">
                <thead><tr><th style={{ width: 46 }}>QR</th><th>Product</th><th>Batch</th>
                  <th style={{ textAlign: 'right' }}>Sent</th>
                  <th style={{ textAlign: 'right' }}>Accepted</th>
                  <th style={{ textAlign: 'right' }}>Short</th>
                  <th style={{ textAlign: 'right' }}>Cost</th></tr></thead>
                <tbody>{detail.lines.map((l) => {
                  const a = acceptedOf(l)
                  const short = Math.round((l.qty - a) * 1000) / 1000
                  return (
                    <ProductRow key={l.id} line={l} onZoom={setZoom}
                      style={hit === l.id ? { background: 'var(--info-bg)' } : undefined}>
                      <td style={{ textAlign: 'right' }}>{l.qty}</td>
                      <td className="num">{editable
                        ? <input value={acc[l.id] ?? ''} placeholder={l.qty}
                            onChange={(e) => setAcc({ ...acc, [l.id]: e.target.value })} />
                        : l.accepted_qty}</td>
                      <td style={{ textAlign: 'right', color: (editable ? short : l.short_qty) > 0 ? 'var(--danger)' : 'var(--muted)' }}>
                        {(editable ? short : l.short_qty) > 0 ? (editable ? short : l.short_qty) : '—'}</td>
                      <td style={{ textAlign: 'right' }}>{money(l.rate)}</td>
                    </ProductRow>
                  )
                })}</tbody>
              </table>
              </div>
              <div className="items-foot"><span>{detail.lines.length} items</span>
                <span>sent <b>{detail.total_qty}</b></span>
                <span>accepting <b>{Math.round(counted * 1000) / 1000}</b></span>
                {detail.total_qty - counted > 0 && (
                  <span style={{ color: 'var(--danger)' }}>short <b>{Math.round((detail.total_qty - counted) * 1000) / 1000}</b></span>)}
              </div>
            </Section>
          </div>
          {editable ? (
            <div className="actionbar">
              <div className="field" style={{ width: 170 }}><label>Received by</label>
                <input value={who} onChange={(e) => setWho(e.target.value)} placeholder="who took it in" /></div>
              <DateField label="Date" width={150} value={date} onChange={setDate} />
              <div className="spacer" />
              <button className="btn primary" onClick={receive}>Receive Goods</button>
            </div>
          ) : (
            <div className="actionbar">
              <span className="small">{detail.status === 'received'
                ? (detail.shortfall > 0
                  ? `Received with a shortfall of ${detail.shortfall} unit(s).`
                  : 'Received in full.')
                : 'This transfer has not been dispatched yet — post it on Stock Outward first.'}</span>
              <div className="spacer" />
            </div>
          )}
        </div>
      ) : <div className="empty">Select a transfer to check it in.</div>}
      {zoom && <ProductCardModal product={zoom} onClose={() => setZoom(null)} />}
    </div>
  )
}
function Stat({ label, value }) {
  return (
    <div className="stat">
      <div className="lbl">{label}</div>
      {/* same length-aware step-down the dashboard tiles use */}
      <div className={'val' + (longValue(value) ? ' long' : '')}>{value}</div>
    </div>
  )
}

// ---------- supplier payments (accounts payable) ----------
function Payments({ toast }) {
  const [suppliers, setSuppliers] = useState([])
  const [sel, setSel] = useState(null)
  const [bills, setBills] = useState([])
  const [rows, setRows] = useState({})     // purchase_id -> {sel, cash, discount, tds, debit}
  const [ledger, setLedger] = useState(null)
  const [payments, setPayments] = useState([])
  const [head, setHead] = useState({ date: '', mode: 'NEFT', ref_no: '', remarks: '' })
  const [q, setQ] = useState('')

  const loadSuppliers = useCallback(() => api.listSuppliers().then(setSuppliers), [])
  useEffect(() => { loadSuppliers(); api.listPayments().then(setPayments) }, [loadSuppliers])
  const payPage = usePaged(suppliers.filter((s) => matches(s, q, ['name', 'gstin'])), 50)
  const loadSupplier = (id) => {
    setSel(id)
    api.pendingBills(id).then((b) => {
      setBills(b)
      const r = {}; b.forEach(x => { r[x.purchase_id] = { sel: false, cash: x.outstanding, discount: 0, tds: 0, debit: 0 } }); setRows(r)
    })
    api.supplierLedger(id).then(setLedger)
  }
  const upd = (pid, k, v) => setRows({ ...rows, [pid]: { ...rows[pid], [k]: v } })
  const totals = bills.reduce((a, b) => {
    const r = rows[b.purchase_id]; if (!r || !r.sel) return a
    a.cash += +r.cash || 0; a.disc += +r.discount || 0; a.tds += +r.tds || 0; a.debit += +r.debit || 0
    a.settle += (+r.cash || 0) + (+r.discount || 0) + (+r.tds || 0) + (+r.debit || 0); a.n++
    return a
  }, { cash: 0, disc: 0, tds: 0, debit: 0, settle: 0, n: 0 })

  const record = async () => {
    const allocations = bills.filter(b => rows[b.purchase_id]?.sel).map(b => {
      const r = rows[b.purchase_id]
      return { purchase_id: b.purchase_id, cash: +r.cash || 0, discount: +r.discount || 0, tds: +r.tds || 0, debit_adjust: +r.debit || 0 }
    })
    if (!allocations.length) { toast('Select at least one invoice', 'err'); return }
    const pay = await api.createPayment({ supplier_id: sel, ...head, allocations })
    toast(`✓ Payment ${pay.receipt_no} recorded`, 'ok')
    loadSupplier(sel); loadSuppliers(); api.listPayments().then(setPayments)
    setHead({ date: '', mode: 'NEFT', ref_no: '', remarks: '' })
  }

  return (
    <div className="body">
      <Sidebar id="payables" label="Payables">
        <div className="head"><h3>Suppliers · payables</h3></div>
        <SearchBox value={q} onChange={setQ} placeholder="Search supplier, GSTIN…" />
        <div className="list">
          {payPage.slice.map((s) => (
            <div key={s.id} className={'sup-row' + (sel === s.id ? ' sel' : '')} onClick={() => loadSupplier(s.id)}>
              <div className="t">{s.name}</div>
              <div className="m"><span>{s.document_count} bill(s)</span></div>
            </div>
          ))}
        </div>
        <Pager {...payPage} noun="supplier" />
      </Sidebar>
      {sel ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="editor">
            {ledger && <div style={{ display: 'flex', gap: 14, marginBottom: 18 }}>
              <Stat label="Outstanding" value={'₹ ' + money(ledger.outstanding)} />
              <Stat label="Pending bills" value={bills.length} />
            </div>}
            <Section id="pay.pending-bills" title="Pending bills — select, then set cash / discount / TDS / debit">
              {bills.length === 0 ? <p className="small">No outstanding invoices for this supplier.</p> : (
                <div className="tablewrap">
                <table className="items">
                  <thead><tr><th></th><th>Invoice</th><th>Date</th><th style={{ textAlign: 'right' }}>Days</th>
                    <th style={{ textAlign: 'right' }}>Outstanding</th><th style={{ textAlign: 'right' }}>Cash</th>
                    <th style={{ textAlign: 'right' }}>Discount</th><th style={{ textAlign: 'right' }}>TDS</th>
                    <th style={{ textAlign: 'right' }}>Debit</th></tr></thead>
                  <tbody>{bills.map((b) => {
                    const r = rows[b.purchase_id] || {}
                    return (
                      <tr key={b.purchase_id} style={{ background: r.sel ? 'var(--panel-2)' : '' }}>
                        <td><input type="checkbox" checked={!!r.sel} onChange={(e) => upd(b.purchase_id, 'sel', e.target.checked)} /></td>
                        <td className="mono">{b.invoice_number}</td><td>{fmtDate(b.invoice_date)}</td>
                        <td style={{ textAlign: 'right' }}>{b.days ?? '—'}</td>
                        <td style={{ textAlign: 'right' }}>{money(b.outstanding)}</td>
                        <td className="num"><input value={r.cash ?? ''} disabled={!r.sel} onChange={(e) => upd(b.purchase_id, 'cash', e.target.value)} /></td>
                        <td className="num"><input value={r.discount ?? ''} disabled={!r.sel} onChange={(e) => upd(b.purchase_id, 'discount', e.target.value)} /></td>
                        <td className="num"><input value={r.tds ?? ''} disabled={!r.sel} onChange={(e) => upd(b.purchase_id, 'tds', e.target.value)} /></td>
                        <td className="num"><input value={r.debit ?? ''} disabled={!r.sel} onChange={(e) => upd(b.purchase_id, 'debit', e.target.value)} /></td>
                      </tr>
                    )
                  })}</tbody>
                </table>
                </div>
              )}
              {totals.n > 0 && <div className="items-foot">
                <span>{totals.n} selected</span><span>cash <b>₹{money(totals.cash)}</b></span>
                <span>disc <b>₹{money(totals.disc)}</b></span><span>TDS <b>₹{money(totals.tds)}</b></span>
                <span>debit <b>₹{money(totals.debit)}</b></span><span>settling <b>₹{money(totals.settle)}</b></span></div>}
            </Section>

            {ledger && ledger.rows.length > 0 && <Section id="pay.ledger" title="Supplier ledger" summary={`${ledger.rows.length} row(s)`}>
              <table className="items"><thead><tr><th>Date</th><th>Type</th><th>Ref</th>
                <th style={{ textAlign: 'right' }}>Debit</th><th style={{ textAlign: 'right' }}>Credit</th>
                <th style={{ textAlign: 'right' }}>Balance</th></tr></thead>
                <tbody>{ledger.rows.map((r, i) => (
                  <tr key={i}><td>{fmtDate(r.date)}</td><td>{r.type}</td>
                    <td className="mono">{r.ref}{r.detail ? <span className="small"> · {r.detail}</span> : ''}</td>
                    <td style={{ textAlign: 'right' }}>{r.debit ? money(r.debit) : ''}</td>
                    <td style={{ textAlign: 'right', color: 'var(--ok)' }}>{r.credit ? money(r.credit) : ''}</td>
                    <td style={{ textAlign: 'right' }}><b>{money(r.balance)}</b></td></tr>
                ))}</tbody>
              </table>
            </Section>}
          </div>
          <div className="actionbar">
            <div className="field" style={{ width: 120 }}><label>Mode</label>
              <select value={head.mode} onChange={(e) => setHead({ ...head, mode: e.target.value })}
                style={{ width: '100%', background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--line)', borderRadius: 7, padding: '7px' }}>
                <option>NEFT</option><option>RTGS</option><option>Cash</option><option>Cheque</option></select></div>
            <div className="field" style={{ width: 140 }}><label>Ref / UTR</label><input value={head.ref_no} onChange={(e) => setHead({ ...head, ref_no: e.target.value })} /></div>
            <DateField label="Date" width={150} value={head.date} onChange={(v) => setHead({ ...head, date: v })} />
            <div className="spacer" />
            <div style={{ textAlign: 'right', marginRight: 8 }}><div className="small">paying</div><b style={{ fontSize: 18 }}>₹ {money(totals.cash)}</b></div>
            <button className="btn primary" disabled={totals.n === 0} onClick={record}>Record Payment</button>
          </div>
        </div>
      ) : <div className="empty">Select a supplier to see pending bills and record a payment.</div>}
    </div>
  )
}

// ---------- purchase returns ----------
function Returns({ toast }) {
  const [list, setList] = useState([])
  const [purchases, setPurchases] = useState([])
  const [picking, setPicking] = useState(false)
  const [scope, setScope] = useState('all')
  const [detail, setDetail] = useState(null)
  const [qtys, setQtys] = useState({})
  const [reason, setReason] = useState('')
  const [date, setDate] = useState('')
  const [q, setQ] = useState('')
  const [zoom, setZoom] = useState(null)
  const refresh = useCallback(() => api.listReturns().then(setList), [])
  useEffect(() => { refresh() }, [refresh])

  const openPicker = () => { api.listPurchases().then(p => setPurchases(p.filter(x => x.status === 'posted'))); setPicking(true); setDetail(null) }
  // Received lines come back at 0 — how many go back is still a decision. Shortage
  // lines come back at the quantity counted at the dock, because that one isn't:
  // the pieces are missing and by how many was settled when the boxes were opened.
  // Seeding the editor from the server keeps that pre-fill visible and postable.
  const seedQtys = (r) => Object.fromEntries((r.lines || [])
    .filter((l) => +l.qty > 0).map((l) => [l.id, String(l.qty)]))
  const startReturn = async (purchaseId, shortagesOnly) => {
    try {
      const r = await api.buildReturn(purchaseId, shortagesOnly)
      setDetail(r); setPicking(false); setQtys(seedQtys(r)); setReason(shortagesOnly ? 'Short delivery' : ''); setDate('')
      if (shortagesOnly && !(r.shortage_lines || 0)) toast('No unclaimed shortage on that invoice', 'err')
    } catch (e) { toast(e.detail || 'Could not raise the debit note', 'err') }
  }
  const openReturn = (id) => api.getReturn(id).then(r => { setDetail(r); setPicking(false); setQtys(seedQtys(r)) })
  const post = async () => {
    try {
      const line_qtys = {}; Object.entries(qtys).forEach(([k, v]) => { if (+v > 0) line_qtys[k] = +v })
      if (!Object.keys(line_qtys).length) { toast('Set a return qty on at least one line', 'err'); return }
      const res = await api.postReturn(detail.id, { reason, date, line_qtys })
      const short = res.shortage_lines
        ? ` · ${res.shortage_lines} shortage claim(s), ₹${money(res.shortage_value)} (no stock moved)` : ''
      toast(`✓ Debit note ${detail.code} posted · ₹${money(res.debit_total)}${short}`, 'ok')
      api.getReturn(detail.id).then(setDetail); refresh()
    } catch (e) { toast('Post failed: ' + (e.detail || e.message), 'err') }
  }
  const editable = detail && detail.status !== 'posted'
  const shown = list.filter((r) => scope === 'all' || r.status === scope)
    .filter((r) => matches(r, q, ['supplier_name', 'invoice_number', 'code', 'status']))
  const retPage = usePaged(shown, 50)
  const draftTotal = detail ? detail.lines.reduce((s, l) => s + (+qtys[l.id] || 0) * (l.rate || 0), 0) : 0

  return (
    <div className="body">
      <Sidebar id="returns" label="Returns">
        <div className="head"><h3>Returns · {list.length}</h3>
          <button className="btn primary" style={{ padding: '4px 10px' }} onClick={openPicker}>+ New</button></div>
        {list.length > 0 && <>
          <SearchBox value={q} onChange={setQ} placeholder="Search supplier, invoice, code…" />
          <div className="toolbar"><FilterChips value={scope} onChange={setScope} options={[
            ['draft', 'Draft', list.filter((r) => r.status === 'draft').length, 'Debit notes not yet posted'],
            ['posted', 'Posted', list.filter((r) => r.status === 'posted').length, 'Raised against the supplier'],
            ['all', 'All', list.length, 'Every debit note'],
          ]} /></div>
        </>}
        <div className="list">
          {list.length > 0 && shown.length === 0 && <div className="empty" style={{ marginTop: 30, fontSize: 13 }}>
            Nothing matches. Try “All” or clear the search.</div>}
          {retPage.slice.map((r) => (
            <div key={r.id} className={'doc-row' + (detail?.id === r.id && !picking ? ' sel' : '')} onClick={() => openReturn(r.id)}>
              <div className="t">{r.supplier_name}</div>
              <div className="m"><span className={'badge ' + (r.status === 'posted' ? 'confirmed' : 'uploaded')}>{r.status}</span>
                <span>{r.code}</span><span style={{ marginLeft: 'auto' }}>₹ {money(r.total)}</span></div>
              <div className="m"><span>vs {r.invoice_number}</span></div>
            </div>
          ))}
        </div>
        <Pager {...retPage} noun="return" />
      </Sidebar>
      {picking ? (
        <div className="editor">
          <h2 style={{ marginTop: 0 }}>New Purchase Return — pick a reference invoice</h2>
          <table className="items"><thead><tr><th>Supplier</th><th>Invoice</th><th>Date</th>
            <th style={{ textAlign: 'right' }}>Grand total</th>
            <th style={{ textAlign: 'right' }}>Short</th><th></th></tr></thead>
            <tbody>{purchases.map(p => (
              <tr key={p.id}><td>{p.supplier_name}</td><td className="mono">{p.invoice_number}</td>
                <td>{fmtDate(p.invoice_date)}</td><td style={{ textAlign: 'right' }}>₹ {money(p.grand_total)}</td>
                <td style={{ textAlign: 'right', color: p.short_qty ? 'var(--warn)' : 'var(--muted)' }}
                  title={p.short_qty ? `${p.short_qty} unit(s) counted short or damaged at receiving` : ''}>
                  {p.short_qty ? `${p.short_qty} · ₹ ${money(p.short_value)}` : '—'}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {p.short_qty > 0 && (
                    <button className="btn" style={{ padding: '3px 10px', marginRight: 5 }}
                      title="Debit note for the goods that never arrived — quantities already counted at the dock"
                      onClick={() => startReturn(p.id, true)}>Claim shortage →</button>
                  )}
                  <button className="btn" style={{ padding: '3px 10px' }} onClick={() => startReturn(p.id)}>Return →</button></td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : detail ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="editor">
            <div style={{ display: 'flex', gap: 12, alignItems: 'baseline' }}>
              <h2 style={{ margin: 0 }}>{detail.code} · {detail.supplier_name}</h2>
              <span className={'badge ' + (detail.status === 'posted' ? 'confirmed' : 'uploaded')}>{detail.status}</span></div>
            <div className="kv" style={{ margin: '12px 0 20px', gridTemplateColumns: '140px 1fr 140px 1fr' }}>
              <div className="k">Debit note vs</div><div className="mono">{detail.invoice_number}
                {detail.grn_no ? <span className="small"> · GRN {detail.grn_no}</span> : null}</div>
              <div className="k">Debit total</div><div>₹ {money(detail.status === 'posted' ? detail.total : draftTotal)}{editable ? ' + tax' : ''}</div>
              <div className="k">Priced at</div>
              <div style={{ gridColumn: 'span 3' }}>
                <span className="badge confirmed">{detail.cost_basis_label || 'Purchase / GRN cost'}</span>
              </div>
            </div>
            <Section id="return.lines" title={editable ? 'Set return quantity per line' : 'Returned lines'}>
              <div className="tablewrap">
              <table className="items">
                <thead><tr><th style={{ width: 46 }}>QR</th><th>Product</th><th>Batch</th>
                  <th style={{ textAlign: 'right' }}>Received</th>
                  <th style={{ textAlign: 'right' }}>On hand</th>
                  <th style={{ textAlign: 'right' }} title="The purchase price this item was received at — the debit note's basis">
                    GRN cost</th>
                  <th style={{ textAlign: 'right' }}>{editable ? 'Return qty' : 'Qty'}</th>
                  <th style={{ textAlign: 'right' }}>Amount</th></tr></thead>
                <tbody>{detail.lines.map(l => {
                  const q = editable ? (qtys[l.id] ?? '') : l.qty
                  const amt = editable ? (+qtys[l.id] || 0) * (l.rate || 0) : l.amount
                  if (!editable && !l.qty) return null
                  const over = editable && +q > (l.available_qty ?? Infinity)
                  return (
                    <ProductRow key={l.id} line={l} onZoom={setZoom}
                      style={l.is_shortage_claim ? { background: 'var(--warn-bg)' } : undefined}>
                      <td style={{ textAlign: 'right' }} title={l.is_shortage_claim
                        ? `${l.purchased_qty} counted short at receiving — this line claims goods that never arrived, so posting it moves no stock`
                        : l.already_returned
                          ? `${l.already_returned} already returned on an earlier debit note`
                          : 'Received on this invoice'}>
                        {l.is_shortage_claim
                          ? <span className="badge review">{l.purchased_qty} short</span>
                          : (l.purchased_qty || '—')}
                        {l.already_returned > 0 && <span className="small" style={{ color: 'var(--warn)' }}> −{l.already_returned}</span>}
                      </td>
                      {/* never arrived, so there is no stock figure and posting won't touch one */}
                      <td style={{ textAlign: 'right' }}>{l.is_shortage_claim
                        ? <span className="small" style={{ color: 'var(--muted)' }} title="These units never entered stock">no stock</span>
                        : l.on_hand}</td>
                      <td style={{ textAlign: 'right' }} title={{
                        grn_variant_rate: 'The rate this variant was received at on the GRN breakdown',
                        invoice_line_rate: 'The rate the supplier billed on this invoice line',
                        weighted_avg_cost: 'Weighted-average purchase cost (no GRN rate survives for this line)',
                      }[l.cost_source] || 'Purchase cost'}>
                        {money(l.grn_rate ?? l.rate)}
                        {l.cost_source === 'weighted_avg_cost' && <span className="small" style={{ color: 'var(--muted)' }}> avg</span>}
                      </td>
                      <td className="num">{editable
                        ? <input value={q} placeholder="0" style={over ? { borderColor: 'var(--danger)' } : undefined}
                            title={over ? `Only ${l.available_qty} available to return` : ''}
                            onChange={(e) => setQtys({ ...qtys, [l.id]: e.target.value })} />
                        : l.qty}</td>
                      <td style={{ textAlign: 'right' }}>{money(amt)}</td>
                    </ProductRow>
                  )
                })}</tbody>
              </table>
              </div>
              <div className="items-foot">
                <span>{detail.lines.length - (detail.shortage_lines || 0)} received item(s)</span>
                {detail.shortage_lines > 0 && (
                  <span style={{ color: 'var(--warn)' }}>
                    ⚠ {detail.shortage_lines} shortage claim(s)
                  </span>
                )}
              </div>
            </Section>
          </div>
          {editable && (
            <div className="actionbar">
              <div className="field" style={{ width: 180 }}><label>Reason</label><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. damaged / wrong item" /></div>
              <DateField label="Date" width={150} value={date} onChange={setDate} />
              <div className="spacer" />
              <button className="btn primary" onClick={post}>Post Debit Note</button>
            </div>
          )}
        </div>
      ) : <div className="empty">Select a return, or click “+ New” to return goods against an invoice.</div>}
      {zoom && <ProductCardModal product={zoom} onClose={() => setZoom(null)} />}
    </div>
  )
}

// ---------- reports ----------
// Group headings and their order come from the server (services/reports.GROUPS),
// so the screen and the catalogue can never drift apart. This is only the
// fallback for a server too old to serve them.
const REPORT_GROUPS = {
  transport: 'Transport Reports', invoice: 'Invoice Reports', stock: 'Stock Reports',
  purchase: 'Purchase Reports', purchase_return: 'Purchase Return Reports',
  outward: 'Outward Reports', master: 'Other Reports',
}
const PARAM_LABEL = { date_from: 'From', date_to: 'To', as_on: 'As on' }

// ==========================================================================
//  Ask a report
//  ------------------------------------------------------------------------
//  A question in English or Tamil instead of picking one of 33 reports and
//  setting its filters. The server routes the question to a real report from the
//  catalogue and answers in the ordinary {columns, rows, totals} shape, so the
//  answer lands in the table below unchanged.
//
//  The reading is shown, always. A router that quietly picks the wrong report
//  hands someone a table of real numbers answering a question they did not ask,
//  and nothing on screen would give them a way to notice. Saying "I read this as
//  Purchase Items Report, 1–31 July, supplier AMS Garments" costs one line and
//  makes a misread visible in the moment rather than a fortnight later.
// ==========================================================================

// Same shape the server's /csv writes, because someone comparing the two should
// not have to wonder which is authoritative.
const toCsv = (columns, rows, totals) => {
  const cell = (v) => {
    // Dates leave in the format the screen showed them in. A download that
    // disagrees with the table it came from is the one nobody trusts.
    const s = v == null ? '' : String(fmtLoose(v))
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [columns.map(cell).join(',')]
  rows.forEach((r) => lines.push(columns.map((c) => cell(r[c])).join(',')))
  if (totals && Object.keys(totals).length) {
    lines.push('')
    lines.push(['TOTALS', ...Object.entries(totals).map(([k, v]) => cell(`${k}=${v}`))].join(','))
  }
  return lines.join('\r\n')
}

const downloadCsv = (name, text) => {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url; a.download = name; a.click()
  URL.revokeObjectURL(url)
}

// ---------- speaking the question instead of typing it ----------
//
// The browser's own recogniser, not an upload to a transcription service: it is
// free, adds no key to configure, sends no audio anywhere, and already speaks
// ta-IN. What comes out is dropped into the same box someone would have typed
// into, so the question takes the identical path from there.
//
// WHERE THIS DOES NOT WORK, AND WHY IT SAYS SO
// Speech recognition needs a secure context — https, or localhost. run.bat binds
// 0.0.0.0:8000 and the README tells people to reach the app from a phone at
// http://<computer-ip>:8000, and on that origin the API is simply absent. A mic
// button that silently does nothing there would read as a broken feature, so the
// reason is detected and shown instead.
const SpeechRec = typeof window !== 'undefined' &&
  (window.SpeechRecognition || window.webkitSpeechRecognition)

//: The recogniser has to be told which language to expect — it cannot be left to
//: work it out, and the two are far enough apart that guessing wrong transcribes
//: to nonsense. So this is a choice someone makes, and it is remembered.
const VOICE_LANGS = [
  ['en-IN', 'EN', 'English — Indian English'],
  ['ta-IN', 'தமிழ்', 'Tamil'],
]

const voiceBlockedBecause = () => {
  if (!SpeechRec) return 'this browser has no speech recognition — Chrome or Edge does'
  if (!window.isSecureContext) {
    return `voice needs https or localhost, and this page is on `
      + `${window.location.protocol}//${window.location.hostname} — `
      + `open the app at http://localhost:8000 on the machine running it`
  }
  return null
}

function VoiceButton({ onInterim, onSpoken, disabled }) {
  const [listening, setListening] = useState(false)
  const [err, setErr] = useState('')
  const [lang, setLang] = useState(() => {
    try { return localStorage.getItem('essa_voice_lang') || 'en-IN' } catch { return 'en-IN' }
  })
  const rec = useRef(null)
  const blocked = voiceBlockedBecause()

  const pickLang = (l) => {
    setLang(l)
    try { localStorage.setItem('essa_voice_lang', l) } catch { /* private mode */ }
  }

  // A live recogniser holding the microphone open after this screen is gone is
  // both a stuck mic light and a callback firing into an unmounted component.
  useEffect(() => () => { try { rec.current?.abort() } catch { /* already gone */ } }, [])

  const stop = () => { try { rec.current?.stop() } catch { /* not started */ } }

  const start = () => {
    if (blocked || listening) return
    setErr('')
    const r = new SpeechRec()
    rec.current = r
    r.lang = lang
    r.interimResults = true      // so the words appear while they are being said
    r.continuous = false         // one question per press, not an open microphone
    r.maxAlternatives = 1

    r.onresult = (e) => {
      let interim = '', final = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const chunk = e.results[i][0].transcript
        if (e.results[i].isFinal) final += chunk; else interim += chunk
      }
      if (interim) onInterim(interim)
      // Asked as soon as it is heard, and the words stay in the box: a mishearing
      // is then visible both in the box and in the reading above the table, which
      // is a better correction loop than making someone press Ask every time.
      //
      // Listening is cleared here rather than left to `onend`. With
      // continuous = false the recogniser does end itself, but not always
      // promptly — and until it does, the button still pulses as though the mic
      // were open and the language switch stays disabled.
      if (final) { onInterim(final); setListening(false); onSpoken(final.trim()) }
    }
    r.onerror = (e) => {
      setErr({
        'not-allowed': 'microphone permission was refused — allow it in the browser address bar',
        'service-not-allowed': 'the browser blocked speech recognition on this page',
        'no-speech': 'nothing was heard — try again a little closer to the microphone',
        'audio-capture': 'no microphone was found',
        'network': 'speech recognition could not reach the network',
        'aborted': '',
      }[e.error] || `speech recognition failed (${e.error})`)
      setListening(false)
    }
    r.onend = () => setListening(false)
    try { r.start(); setListening(true) } catch (e) { setErr('could not start the microphone') }
  }

  if (blocked) {
    return (
      <span className="askvoice-off" title={`Voice input unavailable: ${blocked}`}>
        🎤 <span className="askvoice-why">off</span>
      </span>
    )
  }

  return (
    <span className="askvoice">
      <button className={'askmic' + (listening ? ' on' : '')} disabled={disabled}
        onClick={() => (listening ? stop() : start())}
        title={listening ? 'Stop listening' : `Ask by voice (${VOICE_LANGS.find(([l]) => l === lang)?.[2]})`}>
        {listening ? '⏹' : '🎤'}
      </button>
      {/* Which language to listen for. Two buttons rather than a select: it is a
          two-way switch someone flips mid-shift, not a setting to go and find. */}
      <span className="asklangs">
        {VOICE_LANGS.map(([l, short, full]) => (
          <button key={l} className={'asklang' + (lang === l ? ' on' : '')} title={`Listen for ${full}`}
            disabled={listening} onClick={() => pickLang(l)}>{short}</button>
        ))}
      </span>
      {listening && <span className="asklistening" title="Listening — speak your question">listening…</span>}
      {err && <span className="askvoice-err" title={err}>{err}</span>}
    </span>
  )
}

function AskBar({ value, onChange, onAsk, busy, engine }) {
  return (
    <div className="askbar">
      <span className="ico" aria-hidden="true">✨</span>
      {/* the clear button is positioned against this wrapper, not the bar, so
          adding controls to the right of the input cannot displace it */}
      <span className="askfield">
        <input value={value} onChange={(e) => onChange(e.target.value)}
          placeholder="Ask a question, or press 🎤 — “what did we buy last month”, “நிலுவை பாக்கி எவ்வளவு”"
          title="Ask in English or Tamil, typed or spoken. The question is routed to one of the reports on the left."
          onKeyDown={(e) => { if (e.key === 'Enter' && value.trim()) onAsk() }} />
        {value && <button className="askclear" title="Clear" onClick={() => onChange('')}>×</button>}
      </span>
      {/* Spoken words land in the same box, so voice is a way of filling this
          control rather than a second path through the feature. */}
      <VoiceButton onInterim={onChange} onSpoken={(t) => onAsk(t)} disabled={busy} />
      {/* onAsk() called with no argument on purpose — onClick={onAsk} would hand
          it the click event as the question text. */}
      <button className="btn primary" disabled={busy || !value.trim()} onClick={() => onAsk()}
        title="Find the report that answers this">{busy ? 'Reading…' : 'Ask'}</button>
      {engine === 'keywords' && (
        <span className="askengine" title={'No vision/API key is set, so questions are matched on keywords rather than read. '
          + 'Turn on a key in the top bar for full sentences and Tamil phrasing this list does not cover.'}>
          keyword mode
        </span>
      )}
    </div>
  )
}

// What the question was taken to mean, what the report could honour, and what it
// could not. The last of those is the point: only `stock_movement` filters by
// product and nothing filters by supplier, so a question can easily ask for a cut
// the report cannot make.
function AskReading({ read, onDismiss }) {
  const chips = Object.entries(read.applied || {})
  return (
    <div className={'askread' + (read.report_key ? '' : ' miss')}>
      <div className="askread-top">
        <span className="askread-what">
          {read.report_key
            ? <><b>{read.report_name}</b> — {read.reading}</>
            : <>Nothing in the catalogue answers that. <span className="small">{read.reading}</span></>}
        </span>
        <span className="spacer" />
        {read.engine === 'model' && read.confidence !== 'high' && (
          <span className="askflag" title="The router was not certain of this match — check it is the report you meant before trusting the figures.">
            {read.confidence} confidence
          </span>
        )}
        {read.engine === 'keywords' && (
          <span className="askflag" title="Matched on keywords, not read as a sentence.">keywords</span>
        )}
        <button className="askclear" title="Dismiss this reading" onClick={onDismiss}>×</button>
      </div>
      {chips.length > 0 && (
        <div className="askchips">
          {chips.map(([k, v]) => (
            <span key={k} className="askchip" title={`The report ran with ${k} = ${fmtLoose(v)}`}>
              {(PARAM_LABEL[k] || k.replace(/_/g, ' '))}: <b>{String(fmtLoose(v))}</b>
            </span>
          ))}
        </div>
      )}
      {(read.narrowed || []).map((n, i) => (
        <div key={'n' + i} className="asknote">↳ {n}</div>
      ))}
      {(read.ignored || []).map((n, i) => (
        <div key={'i' + i} className="asknote warn">⚠ {n}</div>
      ))}
      {read.degraded && <div className="asknote warn">⚠ {read.degraded}</div>}
    </div>
  )
}

function Reports() {
  const [cat, setCat] = useState([])
  const [groups, setGroups] = useState([])
  const [key, setKey] = useState(null)
  const [rep, setRep] = useState(null)
  const [q, setQ] = useState('')
  const [filters, setFilters] = useState({})     // the values behind a report's params
  const [busy, setBusy] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)
  // --- ask ---
  const [ask, setAsk] = useState('')
  const [asked, setAsked] = useState(null)      // the interpretation, while it stands
  const [asking, setAsking] = useState(false)
  const [askMeta, setAskMeta] = useState(null)  // engine + example questions
  useEffect(() => {
    api.reportGroups().then(setGroups).catch(() => {})
    api.reportCatalogue().then((c) => { setCat(c); if (c[0]) pick(c[0].key) })
    api.reportAskExamples().then(setAskMeta).catch(() => {})
  }, [])
  const entry = cat.find((r) => r.key === key)
  // a filter only counts if the selected report declares it — switching from a
  // date-ranged report to one without dates must not keep filtering silently
  const active = (f = filters, e = entry) =>
    Object.fromEntries(Object.entries(f).filter(([k, v]) => v && (e?.params || []).includes(k)))
  const load = (k, f) => {
    setBusy(true)
    const e = cat.find((r) => r.key === k) || entry
    return api.runReport(k, active(f, e)).then(setRep).finally(() => setBusy(false))
  }
  // Picking a report by hand, or touching a filter, means the question no longer
  // describes what is on screen — so the reading goes rather than sitting there
  // captioning a table it no longer refers to.
  const pick = (k) => { setKey(k); setRep(null); setQ(''); setAsked(null); load(k, filters) }
  const setFilter = (p, v) => {
    const next = { ...filters, [p]: v }
    setFilters(next)
    setAsked(null)
    load(key, next)
  }

  // An answered question drives the ordinary controls: it selects the report in
  // the list and writes its filters into the filter panel. So the search box, the
  // filter panel and the export keep working on the result exactly as they would
  // if the report had been picked by hand — nothing downstream needs an ask path.
  // `text` is passed explicitly by the example buttons: they setAsk() and ask in
  // the same handler, and a runAsk() closing over `ask` would still be holding
  // the previous render's value at that point. Typed rather than trusted, because
  // wiring this straight to an onClick hands it a MouseEvent instead.
  const runAsk = async (text) => {
    const question = (typeof text === 'string' ? text : ask).trim()
    if (!question) return
    setAsking(true); setQ('')
    try {
      const r = await api.askReport(question)
      setAsked(r.interpretation)
      if (r.ok && r.report) {
        setKey(r.interpretation.report_key)
        setFilters(r.interpretation.applied || {})
        setRep(r.report)
      } else {
        setRep(null)
      }
    } catch (e) {
      // The frontend is served off disk and picks up a rebuild on refresh, but
      // routes are registered when Python starts — so a backend left running
      // from before this endpoint existed serves the new screen and then rejects
      // its calls. That is a restart, not a broken feature, and saying "Method
      // Not Allowed" sends someone looking in entirely the wrong place.
      const stale = e.status === 404 || e.status === 405
      setAsked({
        report_key: null, engine: 'none',
        reading: stale
          ? 'the server is still running the code from before this feature was added — '
            + 'restart the backend (Ctrl-C in the run window, then run.bat again) and ask again'
          : (e.detail || 'the question could not be sent'),
      })
      setRep(null)
    }
    setAsking(false)
  }
  const rows = rep ? rep.rows.filter((row) => !q || rep.columns.some((c) => String(row[c] ?? '').toLowerCase().includes(q.toLowerCase()))) : []
  // a stock or transport report over a year runs to thousands of rows
  const repPage = usePaged(rows, 100)
  const grouped = cat.reduce((a, r) => { (a[r.group] = a[r.group] || []).push(r); return a }, {})
  const order = groups.length ? groups : Object.entries(REPORT_GROUPS).map(([k2, n]) => ({ key: k2, name: n }))
  const dateParams = (entry?.params || []).filter((p) => PARAM_LABEL[p])
  // A report's columns are whatever the report returns, so a cell is formatted
  // by what it IS rather than by which column it sits in: figures grouped, dates
  // read back as DD-MM-YYYY like everywhere else, everything else left alone.
  const fmt = (v) => typeof v === 'number' ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : (fmtLoose(v) ?? '')
  // A question that routed nowhere: no table, and no report claiming to be it
  const miss = !!(asked && !asked.report_key)
  // Dismissing a miss puts the previously selected report back, rather than
  // leaving the pane empty with a live selection behind it
  const dismissReading = () => { setAsked(null); if (!rep && key) load(key, filters) }
  return (
    <div className="body">
      <Sidebar id="reports" label="Reports">
        <div className="head"><h3>Reports · {cat.length}</h3></div>
        <div className="list" style={{ padding: '6px 0' }}>
          {order.filter((g) => grouped[g.key]?.length).map((g) => (
            <div key={g.key}>
              <div style={{ padding: '10px 14px 4px', fontSize: 11, textTransform: 'uppercase', color: 'var(--muted)', letterSpacing: '.5px' }}>
                {g.name} <span style={{ opacity: 0.6 }}>({grouped[g.key].length})</span>
              </div>
              {grouped[g.key].map(r => (
                // A question nothing answered leaves no report on screen, so
                // nothing in this list is highlighted either — a highlight with
                // no table next to it reads as "this is what you are looking at".
                <div key={r.key} className={'doc-row' + (key === r.key && !miss ? ' sel' : '')}
                  style={{ padding: '8px 14px' }} onClick={() => pick(r.key)}>
                  <div className="t" style={{ fontWeight: key === r.key && !miss ? 700 : 400 }}>{r.name}</div>
                </div>
              ))}
            </div>
          ))}
        </div>
      </Sidebar>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <AskBar value={ask} onChange={setAsk} onAsk={runAsk} busy={asking}
          engine={askMeta?.engine} />
        {asked && <AskReading read={asked} onDismiss={dismissReading} />}
        {/* Examples until the first question — a blank box teaches nobody what it
            will accept, and the Tamil ones are the only signal that it does. */}
        {!asked && !ask && (askMeta?.examples || []).length > 0 && (
          <div className="askegs">
            <span className="small">Try:</span>
            {askMeta.examples.map((e) => (
              <button key={e.q} className="askeg" title={e.note}
                onClick={() => { setAsk(e.q); runAsk(e.q) }}>{e.q}</button>
            ))}
          </div>
        )}
        {rep ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', padding: '14px var(--gutter)', borderBottom: '1px solid var(--line)', flexWrap: 'wrap', gap: 8 }}>
              <h2 style={{ margin: 0 }}>{entry?.name}</h2>
              <div style={{ marginLeft: 16, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                {Object.entries(rep.totals).map(([k, v]) => (
                  <span key={k} className="small">{k.replace(/_/g, ' ')}: <b style={{ color: 'var(--text)' }}>{fmt(v)}</b></span>
                ))}
              </div>
              <div className="spacer" style={{ flex: 1 }} />
              <SearchBox value={q} onChange={setQ} placeholder="Search these rows…" style={{ width: 200 }} />
              {/* Only the filters this report declares — see services/reports.run.
                  Behind the same ⛭ control every other screen uses, so a report
                  with a date range and a list with a status scope are the same
                  gesture. Reports with no filters simply don't show the button. */}
              {dateParams.length > 0 && (
                <FilterButton open={filtersOpen} onToggle={() => setFiltersOpen((o) => !o)}
                  active={Object.keys(active()).length} />
              )}
              {/* the export carries the same filters, so it can't quietly hand back
                  a different set of rows than the one on screen.

                  Once rows have been narrowed after the report ran, the server
                  cannot reproduce them from filters alone — asking it would return
                  the wider set under the same button. So a narrowed result is
                  written from the rows on screen instead. */}
              {asked?.narrowed?.length ? (
                <button className="btn" onClick={() => downloadCsv(`${key}.csv`, toCsv(rep.columns, rep.rows, rep.totals))}
                  title="Download exactly these rows, including the narrowing applied after the report ran">
                  Export CSV</button>
              ) : (
                <a className="btn" href={api.reportCsvUrl(key, active())} target="_blank"
                  rel="noreferrer" title="Download exactly these rows, with these filters">Export CSV</a>
              )}
            </div>
            {dateParams.length > 0 && (
              <div style={{ padding: '0 var(--gutter)' }}>
                <FilterPanel open={filtersOpen} active={Object.keys(active()).length}
                  onClear={() => { setFilters({}); load(key, {}) }}
                  hint={`This report accepts: ${dateParams.map((p) => PARAM_LABEL[p]).join(', ')}`}>
                  {dateParams.map((p) => (
                    <DateField key={p} label={PARAM_LABEL[p]} width={150}
                      value={filters[p] || ''} onChange={(v) => setFilter(p, v)} />
                  ))}
                </FilterPanel>
              </div>
            )}
            <div style={{ flex: 1, overflow: 'auto', padding: '0 var(--gutter) var(--gutter)' }}>
              {rep.note && (
                <div className="small" style={{ padding: '10px 0 2px', color: 'var(--muted)' }}>
                  ⓘ {rep.note}
                </div>
              )}
              <div className="small" style={{ padding: '8px 0', color: 'var(--muted)' }}>
                {busy ? 'running…' : <>{rows.length} of {rep.rows.length} rows{q ? ` matching “${q}”` : ''}</>}</div>
              <div className="tablewrap">
              <table className="items">
                <thead><tr>{rep.columns.map(c => <th key={c} style={{ textAlign: typeof rep.rows[0]?.[c] === 'number' ? 'right' : 'left' }}>{c.replace(/_/g, ' ')}</th>)}</tr></thead>
                <tbody>{repPage.slice.map((row, i) => (
                  <tr key={i}>{rep.columns.map(c => (
                    <td key={c} className={typeof row[c] === 'number' ? 'mono' : ''} style={{ textAlign: typeof row[c] === 'number' ? 'right' : 'left' }}>
                      {typeof row[c] === 'number' ? fmt(row[c]) : (row[c] || '')}</td>
                  ))}</tr>
                ))}</tbody>
              </table>
              </div>
              {rep.rows.length === 0 && <p className="empty" style={{ marginTop: 40 }}>No data yet.</p>}
            </div>
            {/* the CSV export takes the WHOLE report, not the page — a download
                that silently stopped at row 100 would be the worst kind of wrong */}
            <Pager {...repPage} noun="row" />
          </>
        ) : asked && !asked.report_key ? (
          <div className="empty" style={{ marginTop: 60 }}>
            No report answers that question.<br />
            <span className="small">Pick one from the list on the left.</span>
          </div>
        ) : <div className="empty">Loading report…</div>}
      </div>
    </div>
  )
}

// ---------- vision settings modal ----------
function VisionSettings({ onClose, onChanged, toast }) {
  const [st, setSt] = useState(null)
  const [key, setKey] = useState('')
  const [models, setModels] = useState([])
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.getSettings().then((s) => {
      setSt(s)
      if (s.has_key) api.listModels().then((r) => { if (r.ok) setModels(r.models) })
    })
  }, [])

  const activate = async () => {
    if (!key.trim()) { toast('Paste your Anthropic API key first', 'err'); return }
    setBusy(true)
    try {
      const r = await api.setVisionKey(key.trim(), null)
      if (!r.ok) { toast(r.message || 'Could not activate', 'err'); setBusy(false); return }
      if (r.models) setModels(r.models)
      toast(r.verified ? '👁 Vision on · ' + (r.chosen_model || '') : '👁 Vision on (' + r.message + ')', 'ok')
      setKey(''); setSt(r); onChanged && onChanged()
    } catch (e) { toast('Failed: ' + (e.detail || e.message), 'err') }
    setBusy(false)
  }
  const changeModel = async (m) => {
    const r = await api.setModel(m); setSt(r); onChanged && onChanged()
    toast('Model set to ' + m, 'ok')
  }
  const turnOff = async () => {
    setBusy(true)
    const r = await api.turnOffVision(); setSt(r); setModels([]); onChanged && onChanged()
    toast('Vision turned off — uploads use offline OCR', 'ok'); setBusy(false)
  }

  const on = st?.vision_enabled
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(42,35,32,.45)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div style={{ width: 540, background: 'var(--panel)', border: '1px solid var(--line)',
        borderRadius: 12, padding: 24 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>👁 Vision extraction</h2>
          <div className="spacer" style={{ flex: 1 }} />
          <button className="btn" style={{ padding: '2px 9px' }} onClick={onClose}>×</button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '14px 0',
          padding: '10px 12px', borderRadius: 8,
          background: on ? 'var(--ok-bg)' : 'var(--panel-2)', border: '1px solid ' + (on ? 'var(--ok-line)' : 'var(--line)') }}>
          <span style={{ fontSize: 20 }}>{on ? '🟢' : '⚪'}</span>
          <div>
            <div style={{ fontWeight: 600 }}>{on ? 'Vision mode is ON' : 'Vision mode is OFF'}</div>
            <div className="small">{on
              ? `key ${st.key_masked} · model ${st.model}`
              : `new uploads currently use ${st?.active_live_provider || 'offline OCR'}`}</div>
          </div>
        </div>

        <div className="field" style={{ marginBottom: 10 }}>
          <label>Anthropic API key</label>
          <input type="password" value={key} placeholder={on ? '•••• stored — paste a new key to replace' : 'sk-ant-...'}
            onChange={(e) => setKey(e.target.value)} autoComplete="off" />
        </div>
        {on && (
          <div className="field" style={{ marginBottom: 14 }}>
            <label>Model {models.length ? `(${models.length} available on your key)` : ''}</label>
            {models.length ? (
              <select value={st.model || ''} onChange={(e) => changeModel(e.target.value)}
                style={{ width: '100%', background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--line)', borderRadius: 7, padding: '8px' }}>
                {!models.find(m => m.id === st.model) && st.model && <option value={st.model}>{st.model} (current)</option>}
                {models.map(m => <option key={m.id} value={m.id}>{m.display_name} — {m.id}</option>)}
              </select>
            ) : (
              <input value={st.model || ''} disabled />
            )}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button className="btn primary" disabled={busy} onClick={activate}>
            {busy ? 'Checking…' : 'Activate vision'}</button>
          {on && <button className="btn" disabled={busy} onClick={turnOff}>Turn off</button>}
          <div className="spacer" style={{ flex: 1 }} />
        </div>
      </div>
    </div>
  )
}

// ---------- scanning overlay (shown while an upload is being extracted) ----------
function ScanningOverlay({ url, name, vision }) {
  return (
    <div className="scan-overlay">
      <div className="scan-frame">
        {url
          ? <img src={url} alt={name} />
          : <div className="scan-placeholder">{name}</div>}
        <div className="scan-tint" />
        <div className="scan-line" />
      </div>
      <div className="scan-cap"><span className="scan-dot" />
        {vision ? 'Reading invoice with vision' : 'Scanning invoice (OCR)'}<span className="scan-ellipsis" /></div>
      <div className="scan-sub">{name} · extracting supplier, line items and totals…</div>
    </div>
  )
}

// ---------- LR Entry form (one consignment, keyed in by hand) ----------
//
// Mirrors the warehouse Transport Entry screen field for field: the left column
// is the consignment as it travelled, the right is the paperwork against it and
// where the bundles land. Importing a register page is still the fast way in —
// this is for the consignment that arrives with no page to photograph, and for
// correcting one that did.
//
//: How long goods are bought to stand before they are expected to have moved.
//: It is a house policy rather than a fact about any one consignment, so it
//: arrives filled in and is changed on the deliveries that differ, not typed on
//: the ninety-nine that do not. Left blank it was simply never recorded, and the
//: Item Locator's stock age then had nothing to call late. The server carries
//: the same default for rows that never pass through this form — see
//: models.LREntry.stock_holding_days.
const DEFAULT_HOLDING_DAYS = 90

// [key, label, type, opts]. `req` marks the boxes the server also enforces
// (REQUIRED_MANUAL in routers/lr.py); `list` names a master dropdown, `src` a
// master with its own table. combo = dropdown you can also type a new value into.
//
// NOTE: this array is EVALUATED when the module loads, so anything interpolated
// into it must already be declared above — a `const` further down the file is in
// its temporal dead zone here, and the ReferenceError takes the whole bundle
// with it. A blank page, not a broken form.
const LR_FORM_LEFT = [
  ['lr_mode', 'LR Mode', 'select', { req: 1, list: 'lr_mode' }],
  ['lr_no', 'LR No', 'text', { req: 1 }],
  ['lr_date', 'LR Date', 'date', { req: 1 }],
  ['recv_date', 'Received Date', 'date', {}],
  ['supplier_name', 'Supplier', 'combo', { req: 1, src: 'suppliers', wide: 1 }],
  ['agent', 'Agent / Commission', 'combo', { req: 1, src: 'agents' }],
  ['agent_commission', 'Commission %', 'num', {}],
  ['transport', 'Transport', 'combo', { src: 'transports', wide: 1 }],
  ['auto_transfer_location', 'Auto transfer Location', 'select', { list: 'auto_transfer_location' }],
  ['purchase_manager', 'Purchase Manager', 'combo', { list: 'purchase_manager' }],
  ['stock_holding_days', 'Stock Holding Period (days)', 'num',
   { hint: `${DEFAULT_HOLDING_DAYS} days unless this delivery says otherwise` }],
  ['additional_margin', 'Additional Margin', 'num', {}],
  ['bundle', 'No Of Bundles', 'num', { req: 1 }],
  ['boxes', 'No Of Boxes', 'num', { req: 1 }],
  ['qty', 'No Of Pieces', 'num', { req: 1 }],
  ['amount', 'Goods Value', 'num', {}],
]
// The reference screen also carried Company, Bundle Rack, Section, Remark, Due
// Date, Pay Mode, PackageSlip No/Date, Actual & Charged Weight, From/Receiving
// City, Loading Charge and Cash/Cheque. Every one came back empty on every
// consignment Essa receives, so they were removed outright rather than kept as
// boxes nobody fills.
const LR_FORM_RIGHT = [
  ['lr_entry_date', 'LR Entry Date', 'date', { req: 1 }],
  ['lr_entry_no', 'LR Entry No', 'ro', {}],
  ['inv_no', 'Invoice No', 'text', {}],
  ['inv_date', 'Inv Date', 'date', {}],
  ['freight_amount', 'Freight Charge', 'charge', { flag: 'freight_applicable' }],
  // the G. TOTAL at the foot of the LR — freight plus L.R. charge, H.C., S.T.
  // charge and the rest. Read off the page on import; typed here when the
  // consignment arrives with no LR copy to photograph.
  ['freight_total', 'Total Charges', 'num', {}],
  // ours, not on their form: how the freight actually settled.
  ['paid_topay', 'Paid / ToPay', 'select', { fixed: ['TOPAY', 'PAID', 'NO'] }],
  ['item', 'Item', 'text', { wide: 1 }],
]
const LR_FORM_KEYS = [...LR_FORM_LEFT, ...LR_FORM_RIGHT]
  .filter(([, , t]) => t !== 'ro').map(([k]) => k)
  .concat('freight_applicable')

function LRField({ spec, form, set, opts, lists }) {
  const [key, label, type, o = {}] = spec
  const v = form[key] ?? ''
  const style = o.wide ? { gridColumn: '1 / -1' } : null
  const choices = o.fixed || (o.list ? (opts[o.list] || []) : (lists[o.src] || []))
  const req = o.req ? <span style={{ color: 'var(--danger)' }}> *</span> : null

  if (type === 'charge') {
    // checkbox + amount, as on their form. The box says the charge APPLIES at
    // all, which a zero amount can't express — nothing quoted yet vs quoted nil.
    return (
      <div className="field" style={style}>
        <label>{label}</label>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {/* unticking clears the amount: an amount saved under an unticked box
              would claim a charge the entry says does not apply */}
          <input type="checkbox" style={{ width: 16, flex: '0 0 auto' }}
            checked={!!form[o.flag]}
            onChange={(e) => { set(o.flag, e.target.checked); if (!e.target.checked) set(key, '') }} />
          <input value={v} inputMode="decimal" placeholder="0.00"
            disabled={!form[o.flag]}
            title={form[o.flag] ? '' : 'Tick the box to record this charge'}
            onChange={(e) => set(key, e.target.value)} />
        </div>
      </div>
    )
  }
  return (
    <div className="field" style={style}>
      <label>{label}{req}</label>
      {type === 'ro'
        ? <input value={v || '— on save —'} disabled title="Allocated by the system when the entry is saved" />
        : type === 'area'
          ? <input value={v} onChange={(e) => set(key, e.target.value)} placeholder="anything worth noting about this consignment" />
          : type === 'select'
            ? <select value={v} onChange={(e) => set(key, e.target.value)}>
                <option value=""></option>
                {choices.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            : type === 'combo'
              ? <>
                  <input value={v} list={'lrl-' + key} onChange={(e) => set(key, e.target.value)}
                    placeholder="pick one, or type a new name" />
                  <datalist id={'lrl-' + key}>{choices.map((c) => <option key={c} value={c} />)}</datalist>
                </>
              : type === 'date'
                /* a saved row may hold a date read off a register page in the
                   page's own format — DateField shows what it can and never
                   discards what it can't */
                ? <DateField inline value={v} onChange={(x) => set(key, x)} />
                : <input value={v} type="text"
                    inputMode={type === 'num' ? 'decimal' : undefined}
                    onChange={(e) => set(key, e.target.value)} />}
      {o.hint && <div className="small" style={{ marginTop: 3 }}>{o.hint}</div>}
    </div>
  )
}

const today = () => new Date().toISOString().slice(0, 10)
// A fresh form: the two dates default to today, exactly as their screen does.
const blankLR = () => ({ lr_entry_date: today(), lr_date: today(), recv_date: today(),
  auto_transfer_location: 'NONE', freight_applicable: false,
  stock_holding_days: DEFAULT_HOLDING_DAYS })

function LREntryForm({ editing, opts, lists, onDone, onCancel, toast, reloadOpts }) {
  const [form, setForm] = useState(() => editing ? { ...editing } : blankLR())
  const [busy, setBusy] = useState(false)
  const [pendingFiles, setPendingFiles] = useState([])   // queued before first save
  const [attType, setAttType] = useState('')
  const [atts, setAtts] = useState(editing?.attachments || [])
  const fileRef = useRef(null)
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))
  useEffect(() => {
    setForm(editing ? { ...editing } : blankLR())
    setAtts(editing?.attachments || [])
    setPendingFiles([])
  }, [editing])

  const queueFile = (e) => {
    const f = e.target.files[0]; if (!f) return
    setPendingFiles((p) => [...p, { file: f, doc_type: attType || 'Other' }])
    e.target.value = ''
  }
  const dropAttachment = async (a) => {
    await api.lrDeleteAttachment(a.id)
    setAtts((x) => x.filter((y) => y.id !== a.id))
  }
  // Files picked before the entry exists have nowhere to attach yet, so they
  // ride along in state and go up the moment it has an id.
  const flushFiles = async (id) => {
    const done = []
    for (const p of pendingFiles) {
      try { done.push(await api.lrAddAttachment(id, p.file, p.doc_type)) }
      catch { toast(`Could not attach ${p.file.name}`, 'err') }
    }
    setPendingFiles([])
    return done
  }

  const submit = async (next) => {
    setBusy(true)
    try {
      const body = {}
      LR_FORM_KEYS.forEach((k) => { if (form[k] !== undefined) body[k] = form[k] })
      const saved = editing ? await api.lrUpdate(editing.id, body) : await api.lrCreate(body)
      const uploaded = await flushFiles(saved.id)
      if (saved.duplicate_of)
        toast(`Saved ${saved.lr_entry_no} — but LR/Invoice already exists on entry #${saved.duplicate_of.join(', #')}. Check it isn't a double entry.`, 'err')
      else
        toast(`✓ ${editing ? 'Updated' : 'Saved'} ${saved.lr_entry_no}${uploaded.length ? ` · ${uploaded.length} file(s) attached` : ''}`, 'ok')
      reloadOpts()
      if (next) {
        // Save&Next: keep the header the operator would only retype — same lorry,
        // same supplier, same day — and clear what is per-consignment.
        const keep = ['lr_mode', 'lr_entry_date', 'lr_date', 'recv_date',
          'supplier_name', 'agent', 'transport',
          'auto_transfer_location', 'purchase_manager']
        const carried = blankLR()
        keep.forEach((k) => { if (form[k]) carried[k] = form[k] })
        setForm(carried); setAtts([])
        onDone(saved, true)
      } else { onDone(saved, false) }
    } catch (e) {
      toast(e.detail || 'Save failed', 'err')
    }
    setBusy(false)
  }

  return (
    <div className="section">
      <h4>{editing ? `Edit entry ${editing.lr_entry_no || '#' + editing.id}` : 'New transport entry'}
        <button className="h4btn" onClick={onCancel}>✕ close</button></h4>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(340px, 100%), 1fr))', gap: '0 28px' }}>
        {[LR_FORM_LEFT, LR_FORM_RIGHT].map((col, ci) => (
          <div key={ci} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 12px', alignContent: 'start' }}>
            {col.map((spec) => <LRField key={spec[0]} spec={spec} form={form} set={set} opts={opts} lists={lists} />)}
          </div>
        ))}
      </div>

      <h4 style={{ marginTop: 22 }}>File attachments</h4>
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div className="field" style={{ width: 190 }}><label>Type</label>
          <select value={attType} onChange={(e) => setAttType(e.target.value)}>
            <option value="">Type</option>
            {(opts.attachment_type || []).map((t) => <option key={t} value={t}>{t}</option>)}
          </select></div>
        <button className="btn" onClick={() => fileRef.current?.click()}>＋ Add file</button>
        <input ref={fileRef} type="file" style={{ display: 'none' }} onChange={queueFile} />
      </div>
      {(atts.length > 0 || pendingFiles.length > 0) && (
        <table className="items" style={{ marginTop: 10, maxWidth: 620 }}>
          <thead><tr><th>File</th><th style={{ width: 150 }}>Type</th><th style={{ width: 90 }}>Action</th></tr></thead>
          <tbody>
            {atts.map((a) => (
              <tr key={a.id}><td><a href={a.url} target="_blank" rel="noreferrer">{a.filename}</a></td>
                <td>{a.doc_type}</td>
                <td><button className="btn" style={{ padding: '2px 7px' }} onClick={() => dropAttachment(a)}>×</button></td></tr>
            ))}
            {pendingFiles.map((p, i) => (
              <tr key={'p' + i} style={{ opacity: 0.7 }}>
                <td>{p.file.name} <span className="small">(uploads on save)</span></td>
                <td>{p.doc_type}</td>
                <td><button className="btn" style={{ padding: '2px 7px' }}
                  onClick={() => setPendingFiles((x) => x.filter((_, j) => j !== i))}>×</button></td></tr>
            ))}
          </tbody>
        </table>
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 18 }}>
        <button className="btn primary" disabled={busy} onClick={() => submit(false)}>
          {busy ? 'Saving…' : editing ? '💾 Update' : '💾 Save'}</button>
        {!editing && <button className="btn" disabled={busy} onClick={() => submit(true)}
          title="Save this one and start another, keeping the supplier / lorry / dates">💾 Save &amp; Next</button>}
        <button className="btn" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

// ---------- LR search ----------
//: [key, label, width, which master list stands behind it]. Supplier and
//: Transport are the same two masters the entry form offers, and they have to be
//: offered here for the same reason: a register searched for a supplier spelled
//: the other way finds nothing and says "no rows", which reads as "no such
//: consignment" rather than "not how it is spelled in here". `q` has no list —
//: it searches four different columns at once and no single master covers it.
const LR_SEARCH_FIELDS = [
  ['q', 'LR / Invoice / Entry no / item', 240, null],
  ['supplier', 'Supplier', 160, 'suppliers'],
  ['transport', 'Transport', 130, 'transports'],
]
function LRSearchPanel({ onResults, onClear, toast, lists }) {
  const [f, setF] = useState({})
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }))
  const run = async () => {
    try { onResults(await api.lrSearch(f)) }
    catch { toast('Search failed', 'err') }
  }
  const clear = () => { setF({}); onClear() }
  // "all" is the absence of a filter, not a filter — counting it would show 2
  // active on a panel nobody has touched
  const activeCount = Object.entries(f)
    .filter(([, v]) => v && v !== 'all').length
  return (
    <Section id="lr.search" title="Search the register"
      summary={activeCount ? `${activeCount} filter(s) set` : 'no filters set'}>
      <FilterPanel open active={activeCount} onClear={clear} onApply={run}
        hint={activeCount
          ? `${activeCount} filter(s) — Apply, or press Enter in any box`
          : 'Set any of these, then Apply. Enter runs it too.'}>
        {LR_SEARCH_FIELDS.map(([k, label, w, src]) => {
          const choices = (src && lists?.[src]) || []
          return (
            <div key={k} className="field" style={{ width: w }}><label>{label}</label>
              {/* a combo, not a select: the register holds names typed off a
                  register page long before anybody made a master of them, so the
                  list has to guide without refusing what is not on it */}
              <input value={f[k] || ''} list={choices.length ? 'lrs-' + k : undefined}
                placeholder={choices.length ? 'pick one, or type' : undefined}
                onChange={(e) => set(k, e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') run() }} />
              {choices.length > 0 && (
                <datalist id={'lrs-' + k}>
                  {choices.map((c) => <option key={c} value={c} />)}
                </datalist>
              )}
            </div>
          )
        })}
        <DateField label="Received from" width={140} value={f.date_from || ''}
          onChange={(v) => set('date_from', v)} />
        <DateField label="to" width={140} value={f.date_to || ''}
          onChange={(v) => set('date_to', v)} />
        <div className="field" style={{ width: 120 }}><label>Received</label>
          <select value={f.received || 'all'} onChange={(e) => set('received', e.target.value)}
            title="Whether the warehouse has taken the consignment in">
            <option value="all">All</option><option value="pending">Not received</option>
            <option value="received">Received</option></select></div>
        <div className="field" style={{ width: 120 }}><label>Invoice</label>
          <select value={f.status || 'all'} onChange={(e) => set('status', e.target.value)}
            title="Whether an invoice has been matched to the consignment">
            <option value="all">All</option><option value="linked">Linked</option>
            <option value="unlinked">Not linked</option></select></div>
      </FilterPanel>
    </Section>
  )
}

// ---------- LR Entry (upload register image → OCR grid → save) ----------
// Columns in the import grid — exactly what vision returns (lr.LR_FIELDS), so a
// register page that carries a column can be reviewed before it is saved.
const LR_COLS = [
  ['recv_date', 'Recv Date', 100], ['transport', 'Transport', 110],
  ['lr_mode', 'Mode', 100], ['bundle', 'Bundle', 60], ['boxes', 'Boxes', 55],
  ['lr_no', 'LR No', 110], ['lr_date', 'LR Date', 100], ['supplier_name', 'Supplier', 180],
  ['agent', 'Agent', 120], ['inv_no', 'Inv No', 80], ['inv_date', 'Inv Date', 100],
  ['qty', 'Pieces', 60], ['amount', 'Goods Value', 95],
  ['paid_topay', 'Paid/ToPay', 80], ['freight_amount', 'Freight', 70],
  // freight is the first line of a transporter's bill, not the bill. `charges`
  // is the block under it (H.C., S.T. charge, …) and `freight_total` the G.
  // TOTAL printed at its foot — what the lorry is actually paid.
  ['freight_charges', 'Charges', 130], ['freight_total', 'Total Charges', 95],
  ['item', 'Item', 110],
]
//: read off the page and totalled, never typed in this grid
const LR_READONLY_COLS = new Set(['freight_charges'])
//: which of those are dates — picked from a calendar, never retyped
const LR_DATE_COLS = new Set(['recv_date', 'lr_date', 'inv_date'])
// The SAVED register is laid out to fit one screen rather than scroll sideways.
// Two devices get it there without dropping anything:
//   * `sub` puts a second, muted line in the same cell, so a value and its date
//     (LR no + LR date, invoice + invoice date) share one column instead of two.
//   * `pair` prints two counts as "2 / 1" in one cell (bundles / boxes).
// `num` right-aligns figures — a register exists to be read down its columns,
// and ragged left-aligned numbers cannot be compared at a glance.
const LR_REG_COLS = [
  { k: 'lr_entry_no', h: 'Entry No', w: 88, mono: 1 },
  { k: 'recv_date', h: 'Recv Date', w: 86, mono: 1 },
  { k: 'transport', h: 'Transport', w: 132, sub: 'lr_mode' },
  { k: 'supplier_name', h: 'Supplier', w: 168, sub: 'agent' },
  { k: 'lr_no', h: 'LR No', w: 112, sub: 'lr_date', mono: 1 },
  { k: 'inv_no', h: 'Invoice', w: 112, sub: 'inv_date', mono: 1 },
  // The receipt raised against this consignment. Next to the invoice it
  // belongs to, because the two are quoted together when a delivery is
  // queried — and blank until the goods are actually received, which is
  // itself the answer to "has this come in yet?".
  { k: 'grn_no', h: 'GRN No', w: 116, mono: 1 },
  { k: 'item', h: 'Item', w: 92 },
  { k: 'bundle', h: 'Bdl / Box', w: 68, num: 1, pair: 'boxes' },
  { k: 'qty', h: 'Pieces', w: 58, num: 1 },
  { k: 'amount', h: 'Goods Value', w: 88, num: 1 },
  { k: 'paid_topay', h: 'Paid/ToPay', w: 84, edit: 1 },
  { k: 'freight_amount', h: 'Freight', w: 76, num: 1, edit: 1 },
  // the transporter's G. TOTAL — freight plus the charge lines under it. Given
  // its own column rather than replacing freight, because a bill of 455 made of
  // 425 haulage and 30 sundries is two facts and a payment run needs both.
  { k: 'freight_total', h: 'Charges', w: 82, num: 1, edit: 1 },
  { k: 'purchase_manager', h: 'Purch Mgr', w: 92 },
]
// freight settlement — completed or corrected when the lorry actually delivers,
// so these stay editable on already-saved rows
const LR_SETTLE_COLS = LR_REG_COLS.filter((c) => c.edit).map((c) => c.k)
function LREntryView({ toast }) {
  const [rows, setRows] = useState([])
  const [docId, setDocId] = useState(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState([])
  const refresh = useCallback(() => api.lrList().then(setSaved), [])
  useEffect(() => { refresh() }, [refresh])
  // A consignment scanned on the warehouse phone is written to this same
  // register, and the person who scanned it is on a dock, not at this desk — so
  // the desk must not have to reload the page to learn a lorry arrived. Polled
  // while the tab is on screen, and again the moment it comes back to the front.
  // Only `saved` is replaced: an open form, a search result and a half-typed
  // freight cell all live in their own state and are left alone.
  useEffect(() => {
    const tick = () => { if (document.visibilityState === 'visible') refresh().catch(() => {}) }
    const t = setInterval(tick, 15000)
    document.addEventListener('visibilitychange', tick)
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', tick) }
  }, [refresh])

  // --- manual entry, dropdown masters and search ---
  const [form, setForm] = useState(null)         // null = closed, {} = new, row = edit
  const [opts, setOpts] = useState({})           // keyed dropdown lists
  const [lists, setLists] = useState({})         // masters with their own tables
  const loadOpts = useCallback(() => {
    api.masterOptions().then(setOpts).catch(() => {})
    Promise.all([api.listSuppliers(), api.agents(), api.transports()])
      .then(([s, a, t]) => setLists({
        suppliers: s.map((x) => x.name).filter(Boolean),
        agents: a.map((x) => x.name).filter(Boolean),
        transports: t.map((x) => x.name).filter(Boolean),
      })).catch(() => {})
  }, [])
  useEffect(() => { loadOpts() }, [loadOpts])

  const [searching, setSearching] = useState(false)   // search panel open
  const [found, setFound] = useState(null)       // search results, null = not filtered
  const shown = found ? found.rows : saved
  // the register, and the rows just read off a page — both grow without limit,
  // and both are read a screenful at a time
  // 25, matching the invoice's line-items table rather than the 50 this had:
  // the register is a wide table and the pager stays hidden below 25 rows
  // anyway, so a page of 50 meant the control appeared only past 51 entries.
  const savedPage = usePaged(shown, 25)
  const extractPage = usePaged(rows, 50)
  const openNew = () => { setForm({}); setRows([]) }
  const openEdit = async (r) => {
    try { setForm(await api.lrGet(r.id)) } catch { toast('Could not open that entry', 'err') }
  }
  const afterSave = (_row, keepOpen) => {
    refresh()
    setFound(null)          // the saved row may not match the active filter — show the full list
    if (!keepOpen) setForm(null)
  }
  const removeEntry = async (r) => {
    if (!window.confirm(`Delete entry ${r.lr_entry_no || '#' + r.id} (${r.supplier_name || 'no supplier'})?`)) return
    try { await api.lrDelete(r.id); toast('Entry deleted', 'ok'); setForm(null); setFound(null); refresh() }
    catch (e) { toast(e.detail || 'Delete failed', 'err') }
  }

  const [dup, setDup] = useState(null)
  const onFile = async (e) => {
    const file = e.target.files[0]; if (!file) return
    setBusy(true); setNote('Reading register…')
    try {
      const r = await api.lrExtract(file)
      setRows(r.rows || []); setDocId(r.document_id); setNote(r.note || ''); setDup(r.duplicates || null)
      const d = r.duplicates
      if (d && (d.duplicates > 0 || d.doubtful > 0))
        toast(`Read ${d.total} rows · ${d.duplicates} duplicate, ${d.doubtful} doubtful`, 'ok')
      else
        toast(`Read ${r.rows?.length || 0} rows (${r.provider})`, 'ok')
    // err.detail is the sentence the server sent ("File storage: blob storage
    // refused the upload: HTTP 400 — …"); err.message is only the status code.
    // Showing the code alone turned every distinct failure into "Extract failed:
    // 500", which is the same toast whatever went wrong.
    } catch (err) { toast('Extract failed: ' + (err.detail || err.message), 'err'); setNote('') }
    setBusy(false); e.target.value = ''
  }
  const isExact = (r) => r._status === 'duplicate'         // excluded from save
  const isDoubtful = (r) => r._status === 'doubtful'       // kept, needs approval
  const upd = (i, k, v) => { const c = rows.map(x => ({ ...x })); c[i][k] = v; setRows(c) }
  const del = (i) => setRows(rows.filter((_, j) => j !== i))
  const save = async () => {
    if (!rows.length) { toast('Nothing to save', 'err'); return }
    // send new + (approved) doubtful rows; exact duplicates are dropped, and the server re-checks
    const clean = rows.filter(r => !isExact(r))
    if (!clean.length) { toast('All rows are exact duplicates — nothing new to save', 'err'); return }
    const r = await api.lrSave(docId, clean)
    const skipMsg = r.skipped_duplicates ? ` · ${r.skipped_duplicates} exact duplicate(s) skipped` : ''
    toast(`✓ Saved ${r.saved} LR entries${skipMsg} · masters updated`, 'ok')
    setRows([]); setNote(''); setDup(null); refresh()
  }
  // --- freight settlement on saved rows (Paid/ToPay · Freight · Cash/Chq) ---
  const [pending, setPending] = useState({})            // `${id}:${field}` → in-progress value
  const cellKey = (r, k) => `${r.id}:${k}`
  const cellVal = (r, k) => pending[cellKey(r, k)] ?? r[k] ?? ''
  const drop = (key) => setPending((p) => { const c = { ...p }; delete c[key]; return c })
  const commitCell = async (r, k) => {
    const key = cellKey(r, k)
    const raw = pending[key]
    if (raw === undefined || String(raw) === String(r[k] ?? '')) { drop(key); return }
    let val = raw === '' ? null : raw
    if (k === 'freight_amount' && val !== null) {
      if (isNaN(Number(val))) { toast('Freight must be a number', 'err'); drop(key); return }
      val = Number(val)
    }
    try {
      const upd = await api.lrUpdate(r.id, { [k]: val })
      setSaved((list) => list.map((x) => (x.id === upd.id ? upd : x)))
      drop(key)
    } catch (err) { toast('Could not save: ' + (err.detail || err.message), 'err'); drop(key) }
  }

  const toSave = rows.filter(r => !isExact(r))
  const nDoubtful = rows.filter(isDoubtful).length
  const qtySum = toSave.reduce((s, x) => s + (+x.qty || 0), 0)
  return (
    <div className="screen">
      {/* the subtitle yields before any control does — a clipped button is a
          control someone cannot reach, a clipped sentence is only a shorter one */}
      <div className="pagehead">
        <h2>LR Entry</h2>
        <div style={{ flex: 1 }} />
        <button className="btn" onClick={() => setSearching((s) => !s)}
          title="Find entries by LR / invoice number, supplier, date, rack…">🔍 Search</button>
        <button className="btn" onClick={openNew}>📄 New entry</button>
        <label className="btn primary uploadbtn">{busy ? 'Reading…' : 'Import LR image / PDF'}
          <input type="file" accept="image/*,.pdf" onChange={onFile} disabled={busy} /></label>
      </div>
      <div className="screenbody">
        {form !== null && (
          <LREntryForm editing={form.id ? form : null} opts={opts} lists={lists}
            onDone={afterSave} onCancel={() => setForm(null)} toast={toast} reloadOpts={loadOpts} />
        )}
        {searching && (
          <LRSearchPanel toast={toast} lists={lists}
            onResults={setFound} onClear={() => setFound(null)} />
        )}
        {found && (
          <div className="warnbox clean" style={{ marginBottom: 14 }}>
            <h4 style={{ border: 'none', margin: 0 }}>
              {found.count} matching entr{found.count === 1 ? 'y' : 'ies'}
              {found.shown < found.count ? ` (showing ${found.shown})` : ''} · Σ pieces <b>{found.totals.qty}</b>
              {' · '}Σ bundles <b>{found.totals.bundle}</b>{' · '}Σ boxes <b>{found.totals.boxes}</b>
              {' · '}Σ goods value <b>₹ {money(found.totals.amount)}</b>
              {' · '}Σ freight <b>₹ {money(found.totals.freight_amount)}</b>
              {/* what the transporters are owed — freight plus the charge lines
                  under it, which is the figure a payment run actually needs */}
              {' · '}Σ transport charges <b>₹ {money(found.totals.freight_total)}</b>
            </h4>
          </div>
        )}
        {note && <div className="warnbox" style={{ marginBottom: 14 }}><h4 style={{ border: 'none', margin: 0, color: 'var(--muted)' }}>{note}</h4></div>}
        {dup && (dup.duplicates > 0 || dup.doubtful > 0) && (
          <div className="warnbox" style={{ marginBottom: 14, borderColor: 'var(--warn)' }}>
            <h4 style={{ border: 'none', margin: 0 }}>
              {dup.duplicates > 0 && <>🚫 {dup.duplicates} exact duplicate{dup.duplicates > 1 ? 's' : ''} (identical to an existing row) — skipped. </>}
              {dup.doubtful > 0 && <>⚠ {dup.doubtful} doubtful row{dup.doubtful > 1 ? 's' : ''} — same LR/Invoice but other values differ; the changed cells are highlighted, please verify before saving. </>}
              {dup.new} new row{dup.new === 1 ? '' : 's'}.
            </h4>
          </div>
        )}
        {rows.length === 0 && saved.length === 0 && form === null && (
          <div className="empty" style={{ marginTop: 40 }}>
            Import an LR register page to auto-extract its rows, or press <b>New entry</b> to key one in.
          </div>
        )}
        {rows.length > 0 && (
          <>
            <Section id="lr.extracted" title="Extracted rows — review & save">
              <div className="tablewrap">
                <table className="items" style={{ minWidth: 1560 }}>
                  <thead><tr><th style={{ minWidth: 70 }}>Status</th>{LR_COLS.map(([k, l, w]) => <th key={k} style={{ minWidth: w }}>{l}</th>)}<th></th></tr></thead>
                  <tbody>{extractPage.slice.map((r) => {
                    const i = rows.indexOf(r)
                    return (
                    <tr key={i} style={isExact(r) ? { background: 'var(--danger-bg)', opacity: 0.6 }
                      : isDoubtful(r) ? { background: 'var(--warn-bg)' } : undefined}>
                      <td style={{ whiteSpace: 'nowrap', fontSize: 11, fontWeight: 600 }}>
                        {isExact(r) ? <span style={{ color: 'var(--danger)' }} title="Identical to an existing row — will be skipped">🚫 duplicate</span>
                          : isDoubtful(r) ? <span style={{ color: 'var(--warn)' }}
                              title={'Same LR/Invoice, but these differ from the saved row:\n' +
                                (r._diffs || []).map(f => `${f}: saved “${r._conflict_with?.[f] ?? ''}” vs this “${r[f] ?? ''}”`).join('\n')}>⚠ verify</span>
                          : <span style={{ color: 'var(--ok)' }}>new</span>}
                        {/* a reading is not the page. When the register was written
                            in Tamil the row says so, and every translated cell can
                            be hovered to see exactly what the clerk wrote. */}
                        {r.source_language && (
                          <div style={{ color: 'var(--muted)', fontWeight: 400, marginTop: 3 }}
                            title={`Read in ${r.source_language} and translated to English:\n`
                              + Object.entries(r.original_values || {})
                                .map(([f, v]) => `${f}: ${v}`).join('\n')}>
                            🌐 {r.source_language}</div>
                        )}
                      </td>
                      {LR_COLS.map(([k]) => {
                        const changed = isDoubtful(r) && (r._diffs || []).includes(k)
                        const orig = r.original_values?.[k]
                        const charges = Object.entries(r.freight_charges || {})
                        return <td key={k} style={changed ? { background: 'var(--warn-line)' } : undefined}
                          title={changed ? `Saved row has: ${r._conflict_with?.[k] ?? '(blank)'}`
                            : orig ? `On the page (${r.source_language || 'original'}): ${orig}`
                            : k === 'freight_total' && charges.length
                              ? `Freight ${r.freight_amount ?? 0}\n`
                                + charges.map(([n, v]) => `${n} ${v}`).join('\n')
                              : undefined}>
                          {/* vision reads a register page's dates in whatever the page
                              used; the picker both corrects them and normalises them */}
                          {LR_DATE_COLS.has(k)
                            ? <DateField inline value={r[k]} onChange={(v) => upd(i, k, v)} />
                            : LR_READONLY_COLS.has(k)
                              /* the charge lines as the LR printed them. Read, not
                                 typed: the total beside them is the editable figure,
                                 and two ways to change one number is one too many */
                              ? <div className="cellsub" style={{ whiteSpace: 'normal', lineHeight: 1.5 }}>
                                  {charges.length
                                    ? charges.map(([n, v]) => `${n} ${v}`).join(' · ')
                                    : <span style={{ color: 'var(--muted)' }}>—</span>}</div>
                              : <input value={r[k] ?? ''} onChange={(e) => upd(i, k, e.target.value)} />}
                          {orig && <div className="cellsub" style={{ direction: 'ltr' }}>🌐 {orig}</div>}
                          {/* the page's own total is kept even when the lines
                              disagree with it — but never silently */}
                          {k === 'freight_total' && r.freight_note && (
                            <div className="cellsub" style={{ color: 'var(--warn)', whiteSpace: 'normal' }}>
                              ⚠ {r.freight_note}</div>)}</td>
                    })}<td><button className="btn" style={{ padding: '2px 7px' }} onClick={() => del(i)}>×</button></td></tr>
                    )
                  })}</tbody>
                </table>
              </div>
              <Pager {...extractPage} noun="read row" />
              {/* the totals are of EVERY row, not the page — a Σ that counted
                  only what is on screen would drop as you paged through it */}
              <div className="items-foot"><span>{toSave.length} to save{nDoubtful ? ` (incl. ${nDoubtful} to verify)` : ''}{rows.length !== toSave.length ? ` · ${rows.length - toSave.length} exact dup skipped` : ''}</span><span>Σ qty <b>{qtySum}</b></span>
                <button className="btn primary" style={{ marginLeft: 'auto' }} onClick={save}>Save {toSave.length} Entr{toSave.length === 1 ? 'y' : 'ies'}</button></div>
            </Section>
          </>
        )}
        {shown.length > 0 && (
          <Section id="lr.saved" title={`${found ? 'Search results' : 'Saved LR entries'} · ${shown.length}`} summary={`${shown.length} row(s)`}>
            <div className="tablewrap">
              <table className="items reg">
                <thead><tr>
                  <th style={{ width: 66 }}>Invoice</th>
                  {LR_REG_COLS.map((c) => <th key={c.k} style={{ width: c.w }}
                    className={c.num ? 'num' : undefined}>{c.h}</th>)}
                  <th style={{ width: 96 }}>Received</th><th style={{ width: 44 }}>Files</th>
                  {/* actions last: the row reads left-to-right as data, and what
                      you can DO with it sits at the end where the eye finishes */}
                  <th style={{ width: 62 }}></th>
                </tr></thead>
                <tbody>{savedPage.slice.map((r) => (
                  <tr key={r.id}>
                    <td style={{ fontSize: 11, fontWeight: 600 }}>
                      {r.mismatches && r.mismatches.length
                        ? <span style={{ color: 'var(--warn)' }} title={r.mismatches.map(m => `${m.field}: register ${m.register} vs invoice ${m.invoice}`).join('\n')}>⚠ conflict</span>
                        : r.matched ? <span style={{ color: 'var(--ok)' }}>✓ linked</span>
                        : <span style={{ color: 'var(--muted)' }}>pending</span>}
                    </td>
                    {LR_REG_COLS.map((c) => {
                      const k = c.k
                      const m = r.mismatches && r.mismatches.find(x => x.field === k)
                      const cls = (c.num ? 'num ' : '') + (c.mono ? 'mono' : '')
                      if (c.edit) return (
                        <td key={k} className={cls}
                          title={k === 'freight_total' && Object.keys(r.freight_charges || {}).length
                            ? 'The transporter’s G. TOTAL — editable.\n'
                              + `Freight ${r.freight_amount ?? 0}\n`
                              + Object.entries(r.freight_charges).map(([n, v]) => `${n} ${v}`).join('\n')
                            : 'Freight settlement — editable; saves when you leave the cell'}>
                          <input value={cellVal(r, k)}
                            onChange={(e) => setPending({ ...pending, [cellKey(r, k)]: e.target.value })}
                            onBlur={() => commitCell(r, k)}
                            onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} /></td>
                      )
                      if (k === 'lr_entry_no') return (
                        <td key={k} className={cls}
                          title={r.entry_source === 'manual' ? 'Keyed in on the form' : 'Read off an imported register page'}>
                          {r.lr_entry_no || '—'}{' '}
                          <span style={{ color: 'var(--muted)' }}>{r.entry_source === 'manual' ? '✎' : '⬇'}</span></td>
                      )
                      // "2 / 1" — bundles and boxes are one fact about the packaging
                      const val = c.pair
                        ? `${r[k] ?? '—'} / ${r[c.pair] ?? '—'}`
                        : LR_DATE_COLS.has(k) ? fmtDate(r[k], '')
                        : (r[k] ?? '')
                      // what the page said, when it wasn't English — kept against
                      // the row so a reading can always be checked against it
                      const orig = r.original_values?.[k]
                      return (
                        <td key={k} className={cls}
                          style={m ? { background: 'var(--warn-bg)' } : undefined}
                          title={m ? `Register: ${m.register}\nInvoice: ${m.invoice}`
                            : orig ? `On the page (${r.source_language || 'original'}): ${orig}` : undefined}>
                          <div className="cellmain">{val}{m ? ' ⚠' : ''}{orig ? ' 🌐' : ''}</div>
                          {c.sub && r[c.sub]
                            ? <div className="cellsub">
                                {LR_DATE_COLS.has(c.sub) ? fmtDate(r[c.sub], '') : r[c.sub]}</div>
                            : null}
                        </td>
                      )
                    })}
                    {/* set by whoever takes the packages in, from the phone app */}
                    <td>{r.received_by
                      ? <span title="Recorded in the warehouse phone app">✓ {r.received_by}</span>
                      : <span style={{ color: 'var(--muted)' }} title="Nobody has taken this consignment in on the phone app yet">—</span>}</td>
                    <td style={{ textAlign: 'center' }}>{r.attachments?.length
                      ? <span title={r.attachments.map(a => `${a.doc_type}: ${a.filename}`).join('\n')}>📎 {r.attachments.length}</span>
                      : ''}</td>
                    <td style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
                      <button className="iconbtn" onClick={() => openEdit(r)}
                        aria-label="Edit entry" title="Edit this entry">✎</button>
                      <button className="iconbtn danger" onClick={() => removeEntry(r)}
                        aria-label="Delete entry"
                        title={r.matched ? 'Linked to an invoice — unlink before deleting' : 'Delete this entry'}>🗑</button>
                    </td>{/* end row */}
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <Pager {...savedPage} noun="entry" nouns="entries" />
          </Section>
        )}
        {found && found.rows.length === 0 && (
          <div className="empty" style={{ marginTop: 20 }}>No entries match those filters.</div>
        )}
      </div>
    </div>
  )
}

// ---------- masters (categories / agents / transports / dropdown lists) ----------
// The keyed lists behind the LR Entry form. FIXED ones are vocabulary the app
// owns — shown, but not editable, because a typo there would become a new "mode"
// of transport. The rest fill themselves from what is typed on the form.
const OPTION_TABS = [
  ['purchase_manager', 'Purchase Managers', 'Who owns the buy behind a consignment.'],
  ['lr_mode', 'LR Modes', 'How a consignment travelled. Fixed list.', 1],
  ['auto_transfer_location', 'Transfer Locations', 'Onward branch, or NONE to keep the goods here.', 1],
  ['attachment_type', 'Attachment Types', 'What a file pinned to an LR entry is. Fixed list.', 1],
]

function OptionList({ kind, title, blurb, fixed, values, reload, toast }) {
  const [adding, setAdding] = useState('')
  const add = async () => {
    const v = adding.trim(); if (!v) return
    try { await api.addMasterOption(kind, v); setAdding(''); reload(); toast(`Added “${v}”`, 'ok') }
    catch (e) { toast(e.detail || 'Could not add', 'err') }
  }
  const drop = async (v) => {
    if (!window.confirm(`Remove “${v}” from ${title}? Entries already using it keep it.`)) return
    try { await api.deleteMasterOption(kind, v); reload() }
    catch (e) { toast(e.detail || 'Could not remove', 'err') }
  }
  return (
    <>
      <h2 style={{ marginTop: 0 }}>{title}</h2>
      {!fixed && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 14, maxWidth: 420 }}>
          <input value={adding} onChange={(e) => setAdding(e.target.value)} placeholder={`Add to ${title}…`}
            onKeyDown={(e) => { if (e.key === 'Enter') add() }}
            style={{ flex: 1, background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--line)', borderRadius: 8, padding: '8px 10px' }} />
          <button className="btn primary" onClick={add}>Add</button>
        </div>
      )}
      {values.length === 0 && <div className="empty">Nothing in this list yet.</div>}
      {values.map((v) => (
        <div key={v} style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, padding: '11px 14px', marginBottom: 7, maxWidth: 420 }}>
          <span style={{ flex: 1 }}>{v}</span>
          {fixed
            ? <span className="small" style={{ color: 'var(--muted)' }}>fixed</span>
            : <button className="btn" style={{ padding: '2px 8px' }} onClick={() => drop(v)}>×</button>}
        </div>
      ))}
    </>
  )
}

// ---------- unit types: what a dozen becomes ----------
// Two lists that answer one question between them. The TYPES say how many
// individual items are in one of a unit — a pair is 2, a dozen is 12 — and the
// dozen→pieces conversion is arithmetic over exactly those numbers. The RULES say
// which type a given product IS, read off its description, so nobody re-picks
// "pillow cover = pair" on every receipt.
//
// Neither is retroactive, and the screen says so: a product freezes its own
// factor the day it is created, because stock on a shelf was counted under the
// rule that was in force when it arrived.
function UnitTypes({ toast }) {
  const [data, setData] = useState({ types: [], rules: [] })
  const [adding, setAdding] = useState({ code: '', name: '', pieces: '2', aliases: '' })
  const [rule, setRule] = useState({ pattern: '', unit_type: 'PAIR' })
  const [edit, setEdit] = useState({})              // code -> in-progress pieces
  const load = useCallback(() => api.unitTypes().then(setData).catch(() => {}), [])
  useEffect(() => { load() }, [load])

  const addType = async () => {
    const code = adding.code.trim().toUpperCase()
    if (!code) return
    try {
      await api.addUnitType({ code, name: adding.name.trim() || code,
        pieces: +adding.pieces || 1,
        aliases: adding.aliases.split(',').map(s => s.trim()).filter(Boolean) })
      setAdding({ code: '', name: '', pieces: '2', aliases: '' }); load()
      toast(`✓ ${code} added`, 'ok')
    } catch (e) { toast(e.detail || 'Could not add the unit', 'err') }
  }
  const savePieces = async (t) => {
    const v = +edit[t.code]
    setEdit((p) => { const c = { ...p }; delete c[t.code]; return c })
    if (!v || v === t.pieces) return
    try { await api.editUnitType(t.code, { pieces: v }); load(); toast(`✓ 1 ${t.code} = ${v} piece(s)`, 'ok') }
    catch (e) { toast(e.detail || 'Could not change it', 'err') }
  }
  const dropType = async (t) => {
    if (!window.confirm(`Remove the unit ${t.code}?\n\nRefused if any product is counted in it.`)) return
    try { await api.deleteUnitType(t.code); load() }
    catch (e) { toast(e.detail || 'Could not remove it', 'err') }
  }
  const addRule = async () => {
    const pattern = rule.pattern.trim()
    if (!pattern) return
    try {
      await api.addUnitRule({ pattern, unit_type: rule.unit_type, scope: 'keyword' })
      setRule({ ...rule, pattern: '' }); load()
      toast(`✓ “${pattern}” is counted in ${rule.unit_type}`, 'ok')
    } catch (e) { toast(e.detail || 'Could not add the rule', 'err') }
  }
  const dropRule = async (r) => {
    try { await api.deleteUnitRule(r.id); load() }
    catch (e) { toast(e.detail || 'Could not remove it', 'err') }
  }
  const box = { background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8 }
  const inp = { background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--line)', borderRadius: 8, padding: '8px 10px' }
  return (
    <>
      <h2 style={{ marginTop: 0 }}>Unit Types</h2>
      <div style={{ display: 'flex', gap: 8, margin: '14px 0', flexWrap: 'wrap', alignItems: 'center' }}>
        <input value={adding.code} onChange={(e) => setAdding({ ...adding, code: e.target.value })}
          placeholder="CODE" style={{ ...inp, width: 110, textTransform: 'uppercase' }} />
        <input value={adding.name} onChange={(e) => setAdding({ ...adding, name: e.target.value })}
          placeholder="Name" style={{ ...inp, width: 150 }} />
        <input value={adding.pieces} onChange={(e) => setAdding({ ...adding, pieces: e.target.value })}
          placeholder="pieces" style={{ ...inp, width: 90 }} title="How many individual items are in one of these" />
        <input value={adding.aliases} onChange={(e) => setAdding({ ...adding, aliases: e.target.value })}
          placeholder="Aliases on invoices, comma separated" style={{ ...inp, flex: 1, minWidth: 220 }} />
        <button className="btn primary" onClick={addType}>Add unit</button>
      </div>
      <table className="items" style={{ maxWidth: 860 }}>
        <thead><tr><th style={{ width: 120 }}>Code</th><th>Name</th>
          <th style={{ width: 130, textAlign: 'right' }} title="Individual items in one of these">Pieces in one</th>
          <th>Printed on invoices as</th><th style={{ width: 80 }}></th></tr></thead>
        <tbody>
          {(data.types || []).map((t) => (
            <tr key={t.code}>
              <td className="mono"><b>{t.code}</b>{!t.countable && (
                <span className="badge review" style={{ marginLeft: 6 }} title="Measured, not counted — no piece labels">measured</span>)}</td>
              <td>{t.name}</td>
              <td className="num"><input value={edit[t.code] ?? t.pieces}
                onChange={(e) => setEdit({ ...edit, [t.code]: e.target.value })}
                onBlur={() => savePieces(t)}
                onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} /></td>
              <td className="mono small" style={{ color: 'var(--muted)' }}>{(t.aliases || []).join(', ') || '—'}</td>
              <td>{t.is_seed
                ? <span className="small" style={{ color: 'var(--muted)' }}>built in</span>
                : <button className="btn" style={{ padding: '2px 8px' }} onClick={() => dropType(t)}>×</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 style={{ marginTop: 28 }}>Which unit a product is</h2>
      <div style={{ display: 'flex', gap: 8, margin: '14px 0', maxWidth: 620 }}>
        <input value={rule.pattern} onChange={(e) => setRule({ ...rule, pattern: e.target.value })}
          placeholder="wording in the description, e.g. pillow cover"
          onKeyDown={(e) => { if (e.key === 'Enter') addRule() }} style={{ ...inp, flex: 1 }} />
        <select value={rule.unit_type} onChange={(e) => setRule({ ...rule, unit_type: e.target.value })} style={inp}>
          {(data.types || []).map((t) => <option key={t.code} value={t.code}>{t.code}</option>)}
        </select>
        <button className="btn primary" onClick={addRule}>Add rule</button>
      </div>
      {(data.rules || []).map((r) => (
        <div key={r.id} style={{ ...box, display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', marginBottom: 7, maxWidth: 620 }}>
          <span style={{ flex: 1 }}>“{r.pattern}”{r.scope === 'category' ? ' (category)' : ''}</span>
          <span className="mono">{r.unit_type}</span>
          <span className="small" style={{ color: 'var(--muted)' }}>{r.source}{r.hits ? ` · ${r.hits}×` : ''}</span>
          <button className="btn" style={{ padding: '2px 8px' }} onClick={() => dropRule(r)}>×</button>
        </div>
      ))}
    </>
  )
}

// ==========================================================================
//  The ERP masters — one renderer for all seventeen
//  ------------------------------------------------------------------------
//  Product, Brand, Tax, Item, Supplier, Trade Agreement, Agent, Tailor,
//  Transport, Configuration, Product Attributes, Attribute Filter, Employee,
//  Employee Incharge, HR Configuration, Salary Management, Employee In/Out.
//
//  None of them is written here. Each arrives as a DEFINITION from the server —
//  its groups, its fields, their types, which are mandatory, and every dropdown
//  already resolved — and this renders whatever it is handed. A field added to
//  the definition appears here with no change to this file, and the * it wears
//  is the same `req` flag the API refuses to save without, so the form and the
//  server can never disagree about what is required.
// ==========================================================================
// A checkbox is one word and a box. Given a field cell of its own — label above,
// box below — seven of them leave a section mostly white space with the controls
// scattered across it, which is what these forms looked like. They flow in a
// single dense row at the foot of their section instead, where a tick is next to
// its own name and the eye can run along them.
// ---------- voice into a form ----------
// One recogniser, driven two ways: a mic on a single field, and a mic that fills
// the whole record from one sentence. The rules for holding a microphone open are
// the same either way, so they live here rather than in each button.
//
// Language is shared with the Reports ask bar (same localStorage key): whoever
// set it to Tamil there meant it here too.
function useSpeechInput({ onFinal, onInterim }) {
  const [listening, setListening] = useState(false)
  const [err, setErr] = useState('')
  const [lang, setLangState] = useState(() => {
    try { return localStorage.getItem('essa_voice_lang') || 'en-IN' } catch { return 'en-IN' }
  })
  const rec = useRef(null)
  const blocked = voiceBlockedBecause()
  const setLang = (l) => {
    setLangState(l)
    try { localStorage.setItem('essa_voice_lang', l) } catch { /* private mode */ }
  }
  // a recogniser still holding the mic after this screen is gone is both a stuck
  // mic light and a callback firing into an unmounted component
  useEffect(() => () => { try { rec.current?.abort() } catch { /* already gone */ } }, [])

  const stop = () => { try { rec.current?.stop() } catch { /* not started */ } }
  const start = () => {
    if (blocked || listening) return
    setErr('')
    const r = new SpeechRec()
    rec.current = r
    r.lang = lang
    r.interimResults = true
    r.continuous = false            // one field, or one sentence, per press
    r.maxAlternatives = 1
    r.onresult = (e) => {
      let interim = '', final = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const chunk = e.results[i][0].transcript
        if (e.results[i].isFinal) final += chunk; else interim += chunk
      }
      if (interim && onInterim) onInterim(interim)
      if (final) { setListening(false); onFinal(final.trim()) }
    }
    r.onerror = (e) => {
      setErr({
        'not-allowed': 'microphone permission was refused — allow it in the address bar',
        'service-not-allowed': 'the browser blocked speech recognition on this page',
        'no-speech': 'nothing was heard — try again, closer to the microphone',
        'audio-capture': 'no microphone was found',
        network: 'speech recognition could not reach the network',
        aborted: '',
      }[e.error] || `speech recognition failed (${e.error})`)
      setListening(false)
    }
    r.onend = () => setListening(false)
    try { r.start(); setListening(true) } catch { setErr('could not start the microphone') }
  }
  return { listening, err, start, stop, lang, setLang, blocked }
}

//: Tamil is transcribed as Tamil, and a master record has to be English — its
//: labels, its dropdown vocabularies and every search that will later look for
//: the row. So anything that is not English English goes to the server to be
//: understood and translated in one step (services/voice_form.py).
const isEnglish = (lang) => String(lang || '').toLowerCase().startsWith('en')

// The mic on one box. Hidden until the field is hovered or focused — thirty
// always-visible mics on a Product Master is thirty things to look past.
function FieldMic({ f, master, onValue, toast }) {
  const [busy, setBusy] = useState(false)
  const heardRef = useRef('')
  const take = async (text, lang) => {
    if (isEnglish(lang)) { onValue(coerceSpoken(f, text)); return }
    // Tamil: the box would otherwise end up holding "பில்லோ", and nobody finds
    // that product again by typing "pillow"
    heardRef.current = text
    setBusy(true)
    try {
      const r = await api.voiceFill(master, text, [f.key], lang)
      const got = r.fills?.[f.key]
      if (got !== undefined && got !== null) onValue(got)
      else if (r.reason === 'no-key') {
        onValue(text)
        toast('No vision/AI key is set, so Tamil cannot be translated — the Tamil words were kept', 'err')
      } else {
        onValue(text)
        toast(`Kept what was heard — “${text}” could not be placed in ${f.label}`, 'err')
      }
    } catch {
      onValue(text)
      toast('Translation failed — the Tamil words were kept so nothing is lost', 'err')
    }
    setBusy(false)
  }
  const { listening, err, start, stop, blocked, lang } = useSpeechInput({
    onFinal: (text) => take(text, lang),
  })
  if (blocked) return null            // the form still types; see the Dictate hint
  return (
    <button type="button" className={'fieldmic' + (listening ? ' on' : '') + (busy ? ' busy' : '')}
      onClick={() => (listening ? stop() : start())}
      title={err || (busy ? `Translating “${heardRef.current}”…`
        : listening ? 'Listening — say the value'
        : `Speak the ${f.label}${isEnglish(lang) ? '' : ' (Tamil is translated to English)'}`)}>
      {busy ? '…' : listening ? '⏹' : '🎤'}
    </button>
  )
}

// One sentence, several boxes. The form's own labels are the grammar — see
// voicefill.js — so the example offered here is built from this master's first
// few fields rather than written out for one of them.
function MasterDictate({ def, data, onFills, toast }) {
  const [heard, setHeard] = useState(null)
  const [busy, setBusy] = useState(false)
  // Nine labels repeat across the Employee master's two address blocks, so a
  // bare "City" in the filled list would not say which one moved.
  const shown = (f) => (f._dup ? `${f._group} · ${f.label}` : f.label)

  const take = async (text, spokenLang) => {
    if (isEnglish(spokenLang)) {
      const { fills, preamble } = parseDictation(def, text)
      if (fills.length) {
        setHeard({ text, filled: fills.map((x) => shown(x.field)), preamble })
        onFills(fills)
        toast(`✓ Filled ${fills.length} field${fills.length === 1 ? '' : 's'} by voice`, 'ok')
        return
      }
      // nothing matched a label. Rather than stop at "not understood", let the
      // server try — it also handles English phrased the way a person phrases it
    }
    setHeard({ text, working: true })
    setBusy(true)
    try {
      const r = await api.voiceFill(def.key, text, null, spokenLang)
      const targets = dictationTargets(def)
      const fills = Object.entries(r.fills || {})
        .map(([key, value]) => ({ field: targets.find((f) => f.key === key), value }))
        .filter((x) => x.field)
      setHeard({ text, english: r.english, filled: fills.map((x) => shown(x.field)),
        preamble: r.unused, dropped: r.dropped, reason: r.reason })
      if (fills.length) {
        onFills(fills)
        toast(`✓ Filled ${fills.length} field${fills.length === 1 ? '' : 's'} by voice`, 'ok')
      } else if (r.reason === 'no-key') {
        toast('Tamil needs the vision/AI key — set it from “vision” in the header', 'err')
      } else {
        toast('Nothing in that could be placed on this form', 'err')
      }
    } catch {
      setHeard({ text, reason: 'the server could not be reached' })
      toast('Could not reach the server to understand that', 'err')
    }
    setBusy(false)
  }

  const { listening, err, start, stop, lang, setLang, blocked } =
    useSpeechInput({
      onFinal: (text) => take(text, lang),
      onInterim: (t) => setHeard({ text: t, interim: true }),
    })

  const sample = useMemo(() => {
    const fs = (def.groups || []).flatMap((g) => g.fields || [])
      .filter((f) => f.type !== 'check' && f.type !== 'date').slice(0, 3)
    const say = (f) => `${f.label.replace(/\s*\(.*\)$/, ' $&').replace(/[()]/g, '')} ${
      f.options?.length ? f.options[0] : f.type === 'num' || f.type === 'money' ? '10' : '…'}`
    return fs.map(say).join(', ')
  }, [def])

  if (blocked) {
    return (
      <span className="small" style={{ color: 'var(--muted)' }} title={blocked}>
        🎤 voice off — {blocked}
      </span>
    )
  }
  return (
    <>
      <span className="asklangs" title="Which language to listen for">
        {VOICE_LANGS.map(([l, short, full]) => (
          <button key={l} className={'asklang' + (lang === l ? ' on' : '')} title={`Listen for ${full}`}
            disabled={listening} onClick={() => setLang(l)}>{short}</button>
        ))}
      </span>
      <button className={'btn dictate' + (listening ? ' on' : '')} disabled={busy}
        onClick={() => (listening ? stop() : start())}
        title={isEnglish(lang)
          ? `Say the field name then its value, several in one breath — e.g. “${sample}”`
          : 'Speak in Tamil — it is understood and the form is filled in English'}>
        {busy ? '⏳ Understanding…' : listening ? '⏹ Listening…' : '🎤 Dictate'}
      </button>
      {(heard || err) && (
        <div className="heardstrip">
          {err && <span className="hwhy">{err}</span>}
          {heard && <>
            <b>heard</b> “{heard.text}”
            {/* what it was understood to MEAN, shown whenever that differs from
                what was said — a Tamil sentence filling English boxes has to be
                checkable, or it is a translation nobody can audit */}
            {heard.english && heard.english !== heard.text
              ? <> · <b>in English</b> “{heard.english}”</> : null}
            {heard.working ? <> · understanding…</> : null}
            {heard.filled?.length ? <> · <b>filled</b> {heard.filled.join(', ')}</> : null}
            {heard.dropped?.length
              ? <span className="hwhy"> · not a listed option, left alone: {heard.dropped.join(', ')}</span>
              : null}
            {heard.preamble && !heard.interim && !heard.working
              ? <span className="hwhy"> · not understood: “{heard.preamble}”</span> : null}
            {heard.reason === 'no-key'
              ? <span className="hwhy"> · Tamil needs the vision/AI key — set it from “vision” in the header</span>
              : heard.reason
                ? <span className="hwhy"> · {heard.reason}</span>
                : (!heard.interim && !heard.working && !heard.filled?.length && isEnglish(lang))
                  ? <span className="hwhy"> · say a field name first, e.g. “{sample}”</span> : null}
          </>}
        </div>
      )}
    </>
  )
}

function MasterCheck({ f, value, onChange }) {
  return (
    <label className="mcheck" title={f.help || f.label}>
      <input type="checkbox" checked={!!value} onChange={(e) => onChange(f.key, e.target.checked)} />
      <span>{f.label}</span>
    </label>
  )
}

function MasterField({ f, value, onChange, master, toast }) {
  const set = (v) => onChange(f.key, v)
  const common = { value: value ?? '', onChange: (e) => set(e.target.value) }
  // A date is picked, not dictated — a misheard digit in a date is a wrong date
  // that looks right, and the picker is faster anyway.
  const mic = f.type !== 'date' && (
    <FieldMic f={f} master={master} toast={toast} onValue={(v) => set(
      // a description is added to; a one-line box is replaced, because a
      // mishearing there is re-dictated rather than edited
      f.type === 'textarea' && value ? `${value} ${v}`.trim() : v)} />
  )
  return (
    <div className={'field' + (mic ? ' hasmic' : '')} style={f.wide ? { gridColumn: '1 / -1' } : null}>
      <label>{f.label}{f.req ? ' *' : ''}</label>
      {mic}
      {f.type === 'textarea' ? <textarea rows={3} {...common} />
        : f.type === 'date' ? <DateField inline value={value} onChange={set} />
        : f.type === 'multiselect' ? (
          // a plain multi-select scrolls a 300-brand list into a 4-line box;
          // comma-separated text with the vocabulary behind it stays typeable
          <>
            <input list={'mopt-' + f.key} {...common}
              placeholder="type to pick, comma-separated for several" />
            <datalist id={'mopt-' + f.key}>
              {(f.options || []).map((o) => <option key={o} value={o} />)}
            </datalist>
          </>
        ) : f.options ? (
          <>
            <input list={'mopt-' + f.key} {...common} placeholder="type to search…" />
            <datalist id={'mopt-' + f.key}>
              {(f.options || []).map((o) => <option key={o} value={o} />)}
            </datalist>
          </>
        ) : <input type={f.type === 'num' || f.type === 'money' ? 'number' : 'text'}
              step="any" {...common} />}
      {f.help && f.type !== 'check' && <div className="srcnote" style={{ color: 'var(--muted)' }}>{f.help}</div>}
    </div>
  )
}

//: a child table (Tailor's works, Transport's city rates, Brand's B2B margins)
function MasterGrid({ grid, rows, onChange }) {
  const upd = (i, k, v) => onChange(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  return (
    <div className="section" style={{ marginTop: 14 }}>
      <h4>{grid.title}</h4>
      <div className="tablewrap">
      <table className="items">
        <thead><tr>{grid.columns.map((c) => <th key={c.key}>{c.label}</th>)}<th /></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {grid.columns.map((c) => (
                <td key={c.key} className={c.type === 'num' || c.type === 'money' ? 'num' : ''}>
                  <input list={c.options ? `g-${grid.key}-${c.key}` : undefined}
                    value={r[c.key] ?? ''} onChange={(e) => upd(i, c.key, e.target.value)} />
                  {c.options && (
                    <datalist id={`g-${grid.key}-${c.key}`}>
                      {c.options.map((o) => <option key={o} value={o} />)}
                    </datalist>
                  )}
                </td>
              ))}
              <td><button className="btn" style={{ padding: '2px 7px' }}
                onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <div className="items-foot">
        <span>{rows.length} row(s)</span>
        <button className="btn" style={{ marginLeft: 'auto', padding: '3px 10px' }}
          onClick={() => onChange([...rows, {}])}>+ add row</button>
      </div>
    </div>
  )
}

//: Product's Purchase Entry Attributes — which attributes a purchase entry asks
//: for, and how hard it asks
function MasterMatrix({ matrix, value, onChange }) {
  const get = (row, col) => !!(value?.[row]?.[col])
  const set = (row, col, on) => onChange({ ...value, [row]: { ...(value?.[row] || {}), [col]: on } })
  return (
    <div className="section" style={{ marginTop: 14 }}>
      <h4>{matrix.title}</h4>
      {matrix.help && <div className="calchint">{matrix.help}</div>}
      <table className="items" style={{ maxWidth: 560 }}>
        <thead><tr><th>Name</th>
          {matrix.columns.map((c) => <th key={c} style={{ width: 70, textAlign: 'center' }}>{c}</th>)}
        </tr></thead>
        <tbody>
          {matrix.rows.map((row) => (
            <tr key={row}>
              <td className="mono" style={{ fontSize: 11 }}>{row}</td>
              {matrix.columns.map((c) => (
                <td key={c} style={{ textAlign: 'center' }}>
                  <input type="checkbox" checked={get(row, c)}
                    onChange={(e) => set(row, c, e.target.checked)} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MasterScreen({ mkey, onBack, toast }) {
  const [def, setDef] = useState(null)
  const [list, setList] = useState([])
  const [q, setQ] = useState('')
  const [form, setForm] = useState(null)          // null = list, {} = new, {id} = edit
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => api.masterRecords(mkey, q).then((r) => setList(r.records)), [mkey, q])
  const recPage = usePaged(list, 50)
  useEffect(() => { api.masterDefinition(mkey).then(setDef); setForm(null) }, [mkey])
  useEffect(() => { load() }, [load])
  // a settings master is one row, not a list — open it straight away
  useEffect(() => {
    if (def?.singleton && form === null) setForm(list[0] ? { ...list[0] } : {})
  }, [def, list])   // eslint-disable-line react-hooks/exhaustive-deps

  if (!def) return <div className="empty">Loading…</div>
  const data = form?.data || {}
  const setField = (k, v) => setForm({ ...form, data: { ...data, [k]: v } })
  // Several fields at once, from one dictated sentence. Applied in a single
  // setForm — field by field, each would be computed against the same stale
  // `data` and only the last one would survive.
  const applyFills = (fills) => {
    const next = { ...data }
    fills.forEach(({ field, value }) => {
      next[field.key] = field.type === 'date' ? (toISODate(value) || value) : value
    })
    setForm({ ...form, data: next })
  }
  const blank = () => {
    const d = {}
    def.groups.forEach((g) => g.fields.forEach((f) => { if (f.default !== undefined) d[f.key] = f.default }))
    setForm({ data: d, grids: {}, matrix: {} })
  }
  const save = async () => {
    setBusy(true)
    try {
      const body = { data, grids: form.grids || {}, matrix: form.matrix || {} }
      const r = form.id ? await api.masterUpdate(mkey, form.id, body)
        : await api.masterCreate(mkey, body)
      toast(`✓ ${def.label} saved — ${r.name || r.code || '#' + r.id}`, 'ok')
      await load()
      if (!def.singleton) setForm(null); else setForm({ ...r })
    } catch (e) { toast(e.detail || 'Could not save', 'err') }
    setBusy(false)
  }
  const remove = async (r) => {
    if (!window.confirm(`Delete ${def.label} “${r.name || r.code || r.id}”?`)) return
    try { await api.masterDelete(mkey, r.id); toast('Deleted', 'ok'); setForm(null); load() }
    catch (e) { toast(e.detail || 'Could not delete', 'err') }
  }

  return (
    <div className="screen">
      <div className="pagehead">
        <button className="btn" onClick={onBack}>‹ Masters</button>
        <h2>{def.label}</h2>
        <span className="small pagesub">{def.sub}</span>
        <div style={{ flex: 1 }} />
        {!def.singleton && form === null && (
          <button className="btn primary" onClick={blank}>📄 New {def.label}</button>
        )}
        {/* Voice sits with the form's own actions, not in a settings screen: it
            is how this record gets filled in, and it is offered at the moment
            somebody is looking at 30 empty boxes. */}
        {form !== null && (
          <MasterDictate def={def} data={data} toast={toast} onFills={applyFills} />
        )}
        {form !== null && !def.singleton && (
          <button className="btn" onClick={() => setForm(null)}>✕ Close</button>
        )}
      </div>

      <div className="screenbody">
      {form !== null ? (
        <>
          {/* Inputs in a responsive grid — three or four columns on a wide screen
              rather than two enormous ones — then the section's checkboxes in a
              dense row beneath a rule. Every section then has the same shape:
              things you type, then things you tick. */}
          {def.groups.map((g) => {
            const checks = g.fields.filter((f) => f.type === 'check')
            const typed = g.fields.filter((f) => f.type !== 'check')
            return (
              <div className="section" key={g.title}>
                <h4>{g.title}</h4>
                {typed.length > 0 && (
                  <div className="mgrid">
                    {typed.map((f) => (
                      <MasterField key={f.key} f={f} value={data[f.key]} onChange={setField}
                        master={mkey} toast={toast} />
                    ))}
                  </div>
                )}
                {checks.length > 0 && (
                  <div className={'mchecks' + (typed.length ? '' : ' bare')}>
                    {checks.map((f) => (
                      <MasterCheck key={f.key} f={f} value={data[f.key]} onChange={setField} />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
          {(def.grids || []).map((grid) => (
            <MasterGrid key={grid.key} grid={grid} rows={form.grids?.[grid.key] || []}
              onChange={(rows) => setForm({ ...form, grids: { ...(form.grids || {}), [grid.key]: rows } })} />
          ))}
          {def.matrix && (
            <MasterMatrix matrix={def.matrix} value={form.matrix || {}}
              onChange={(m) => setForm({ ...form, matrix: m })} />
          )}
          <div style={{ display: 'flex', gap: 8, margin: '16px 0 30px' }}>
            <div style={{ flex: 1 }} />
            {form.id && <button className="btn" onClick={() => remove(form)}>Delete</button>}
            <button className="btn primary" disabled={busy} onClick={save}>
              {busy ? 'Saving…' : `Save ${def.label}`}</button>
          </div>
        </>
      ) : (
        <>
          <SearchBox value={q} onChange={setQ} placeholder={`Search ${def.label}…`} style={{ maxWidth: 320 }} />
          <div className="small" style={{ margin: '10px 0', color: 'var(--muted)' }}>
            {list.length} record(s) · {def.groups.reduce((n, g) => n + g.fields.length, 0)} fields,
            {' '}{def.groups.reduce((n, g) => n + g.fields.filter((f) => f.req).length, 0)} mandatory
          </div>
          {list.length === 0 && <div className="empty" style={{ marginTop: 30 }}>
            Nothing in {def.label} yet — press <b>New {def.label}</b>.</div>}
          {recPage.slice.map((r) => (
            <div key={r.id} className="doc-row" onClick={() => setForm({ ...r })}
              style={{ background: 'var(--panel)', border: '1px solid var(--line)',
                borderRadius: 8, padding: '11px 14px', marginBottom: 7, maxWidth: 720 }}>
              <div className="t">{r.name || r.code || '#' + r.id}</div>
              <div className="m">
                {r.code && <span className="mono">{r.code}</span>}
                {!r.active && <span className="badge review">inactive</span>}
                <span style={{ marginLeft: 'auto' }}>
                  {Object.values(r.data || {}).filter((v) => v !== '' && v != null).length} field(s) filled</span>
              </div>
            </div>
          ))}
          <Pager {...recPage} noun="record" style={{ maxWidth: 720 }} />
        </>
      )}
      </div>
    </div>
  )
}

// The masters this app already RUNS on, as opposed to the seventeen it now also
// carries. These are not reference data: a category decides how a product is
// classified at GRN, a unit type decides whether a billed dozen becomes twelve
// pieces or six pairs, and agents and transporters fill themselves from what the
// extractor reads off documents. They keep their own editors — a keyed list and
// a conversion table are not a form — and the hub opens them like anything else.
const BUILTIN_MASTERS = [
  ['categories', 'Product Categories', 'Category Master', '▦',
    'From GRN PRODUCT DETAILS.xlsx — what every product is classified as'],
  ['units', 'Unit Types', 'Unit & Conversion Master', '⚖',
    'Pieces in a dozen, pieces in a pair — what a billed dozen becomes on the shelf'],
  ['agents', 'Agents', 'Agent Master', '👤',
    'Fills itself from the agent named on invoices and LR pages'],
  ['transports', 'Transporters', 'Transport Master', '🚚',
    'Fills itself from the transporter named on invoices and LR pages'],
  ...OPTION_TABS.map(([k, label, blurb, fixed]) =>
    [k, label, fixed ? 'Fixed list' : 'Open list', '▤', blurb]),
]

//: the frame a built-in master opens in, so every master — generic or bespoke —
//: comes back to the hub the same way
function MasterPane({ title, sub, onBack, children }) {
  return (
    <div className="screen">
      {/* The band, not a heading floating inside the scroll area. A master used
          to draw its title with the page header's padding and border stripped
          off, so opening one from the hub swapped a full-width white bar for
          bare text — the same screen furniture appearing and disappearing as
          you moved between modules. */}
      <div className="pagehead">
        <button className="btn" onClick={onBack}>‹ Masters</button>
        <h2>{title}</h2>
        {sub && <span className="small pagesub">{sub}</span>}
      </div>
      <div className="screenbody">{children}</div>
    </div>
  )
}

function MasterCard({ n, icon, label, sub, meta, note, flag, onClick }) {
  return (
    <button className="mastercard" onClick={onClick} title={note || meta}>
      <span className="mc-icon">{icon}</span>
      <span className="mc-body">
        <b>{label}</b>
        <span className="mc-sub">{sub}</span>
        {meta && <span className="mc-meta">{meta}</span>}
      </span>
      <span className="mc-n">{n}</span>
      {flag && <span className="mc-flag" title={flag}>⚠</span>}
    </button>
  )
}

// ONE hub for every master. It used to be two places — a sidebar listing the
// eight this app runs on, and a card grid of the seventeen from the ERP — so
// "the masters" meant a different screen depending on which one you were after,
// and the sidebar list gave no clue what any of them held. Now they are one
// grid, in two labelled groups, because the difference between them is real and
// worth stating: the first seventeen are reference data, the last eight are
// wired into extraction, GRN and labelling and are working right now.
function Masters({ toast }) {
  const [open, setOpen] = useState(null)      // null = hub, else {kind, key}
  const [erp, setErp] = useState([])
  const [cats, setCats] = useState(null)
  const [agents, setAgents] = useState([])
  const [transports, setTransports] = useState([])
  const [opts, setOpts] = useState({})
  const [q, setQ] = useState('')
  const [section, setSection] = useState('')
  const loadOpts = useCallback(() => api.masterOptions().then(setOpts).catch(() => {}), [])
  useEffect(() => {
    api.masterList().then(setErp).catch(() => {})
    api.categories().then(setCats).catch(() => {})
    api.agents().then(setAgents).catch(() => {})
    api.transports().then(setTransports).catch(() => {})
    loadOpts()
  }, [loadOpts])

  // Computed before any branch: a hook cannot be called conditionally, and the
  // 686-code category master is exactly the list that needs paging.
  const catShown = cats ? cats.items.filter((c) =>
    (!section || c.section === section)
    && (!q || c.name.toLowerCase().includes(q.toLowerCase()))) : []
  const catPage = usePaged(catShown, 100)

  // one of the seventeen — the generic renderer handles all of them
  if (open?.kind === 'erp') {
    return <MasterScreen mkey={open.key} onBack={() => setOpen(null)} toast={toast} />
  }

  const back = () => setOpen(null)
  const meta = BUILTIN_MASTERS.find(([k]) => k === open?.key)
  if (open?.kind === 'builtin') {
    const [key, label, sub, , note] = meta
    if (key === 'categories') {
      const shown = catShown
      return (
        <MasterPane title={label} sub={`${cats ? cats.count : 0} codes`} onBack={back}>
          <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <select value={section} onChange={(e) => setSection(e.target.value)}
              style={{ background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--line)', borderRadius: 8, padding: '8px' }}>
              <option value="">All sections</option>
              {(cats?.sections || []).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <SearchBox value={q} onChange={setQ} placeholder="Search category…" style={{ width: 240 }} />
            <span className="small" style={{ color: 'var(--muted)' }}>{shown.length} shown</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(230px, 100%), 1fr))', gap: 8 }}>
            {catPage.slice.map((c) => (
              <div key={c.id} style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, padding: '9px 12px' }}>
                <span className="mono" style={{ color: 'var(--muted)', fontSize: 11 }}>{c.section}</span>
                <div>{c.name}</div>
              </div>
            ))}
          </div>
          <Pager {...catPage} noun="category" nouns="categories" />
        </MasterPane>
      )
    }
    if (key === 'units') {
      return <MasterPane title={label} sub={sub} onBack={back}><UnitTypes toast={toast} /></MasterPane>
    }
    if (key === 'agents' || key === 'transports') {
      const rows = key === 'agents' ? agents : transports
      return (
        <MasterPane title={label} sub={`${rows.length} on record`} onBack={back}>
          {rows.length === 0 && <div className="empty" style={{ marginTop: 24 }}>None yet.</div>}
          {rows.map((r) => (
            <div key={r.id} style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, padding: '11px 14px', marginBottom: 7, maxWidth: 520 }}>
              {r.name}{r.phone ? ' · ' + r.phone : ''}</div>
          ))}
        </MasterPane>
      )
    }
    // a keyed dropdown list (purchase managers, LR modes, transfer locations…)
    const tab = OPTION_TABS.find(([k]) => k === key)
    return (
      <MasterPane title={label} sub={sub} onBack={back}>
        <OptionList kind={tab[0]} title={tab[1]} blurb={tab[2]} fixed={tab[3]}
          values={opts[tab[0]] || []} reload={loadOpts} toast={toast} />
      </MasterPane>
    )
  }

  const builtinCount = (k) => k === 'categories' ? (cats?.count ?? 0)
    : k === 'agents' ? agents.length
    : k === 'transports' ? transports.length
    : (opts[k] || []).length
  const grid = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(258px, 100%), 1fr))', gap: 10 }

  return (
    <div className="screen">
      <div className="pagehead">
        <h2>Masters</h2>
        <div className="pagesub small">
          The reference data the warehouse runs on, and the lists this app fills for itself
        </div>
      </div>
      <div className="screenbody">
      <h5 className="masterhead" style={{ marginTop: 0 }}>ERP masters</h5>
      <div style={grid}>
        {erp.map((m, i) => (
          <MasterCard key={m.key} n={m.count || i + 1} icon={m.icon} label={m.label} sub={m.sub}
            meta={`${m.fields} fields · ${m.required} required`
              + (m.grids.length ? ` · ${m.grids.length} grid` : '')
              + (m.has_matrix ? ' · matrix' : '')}
            note={m.note}
            flag={m.unverified ? 'Fields inferred — no screenshot supplied' : null}
            onClick={() => setOpen({ kind: 'erp', key: m.key })} />
        ))}
      </div>

      <h5 className="masterhead">In use by this app</h5>
      <div style={grid}>
        {BUILTIN_MASTERS.map(([key, label, sub, icon, note]) => (
          <MasterCard key={key} n={builtinCount(key) || '—'} icon={icon} label={label}
            sub={sub} note={note}
            onClick={() => setOpen({ kind: 'builtin', key })} />
        ))}
      </div>
      </div>
    </div>
  )
}

// ==========================================================================
//  Label Designer · QR / Label Printing
//  ------------------------------------------------------------------------
//  Two screens, on purpose, because two different people use them.
//
//  A template is a LAYOUT: "product_name sits at 2mm/6mm in 8pt bold, the QR is
//  a 20mm square at 28mm/12mm". It holds field REFERENCES and never a product's
//  values. That is what lets one template print the whole warehouse, and what
//  makes a corrected price appear on the next sticker without anyone reopening
//  the designer. Designing is occasional and careful; printing is daily and in
//  a hurry, and putting them on one screen would mean the daily job walks past
//  every control of the careful one.
//
//  Millimetres throughout, because a label is cut to a physical size and the
//  person checking the output holds a ruler against it. `PX_PER_MM` is a zoom
//  factor on the screen only — nothing is ever stored in pixels.
// ==========================================================================

//: pt → screen px at a given zoom. A font size is in points because that is
//: what a font size is; everything else on a label is in millimetres.
const ptPx = (pt, px) => (pt / 72) * 25.4 * px
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
//: Positions snap to a half-millimetre. Free-floating decimals look identical on
//: screen and produce labels whose rows are a hair out of line across a sheet.
const snap = (v) => Math.round(v * 2) / 2

// One label, drawn from a template and a set of values. The same component is
// the designer's canvas and the printing screen's proof, so what someone
// designs is literally what the other screen shows them before they print.
// `interactive` is what separates the two.
function LabelSurface({ tpl, values, symbols, px, selId, onSelect, onChange,
                       interactive, specs, innerRef, onBegin }) {
  const els = [...(tpl.elements || [])].sort((a, b) => a.z - b.z)
  // Typing directly on the label. Local to the canvas because it is a gesture,
  // not a property: it starts on a double-click, ends on Enter or a click
  // elsewhere, and only then does anything reach the draft.
  const [editing, setEditing] = useState(null)   // {id, value}
  // The same thing in a ref, because Escape has to be able to cancel the edit
  // BEFORE the blur that closing the box provokes — a cancel that a stray blur
  // can still commit is not a cancel.
  const editRef = useRef(null)
  const begin = onBegin || (() => {})

  // What a text box currently reads — the same expression the printer uses, so
  // what is typed over is exactly what would have printed.
  const shownText = (e) => (
    (e.use_text || e.field === 'custom_text') ? (e.text || '') : (values[e.field] ?? ''))

  const startEdit = (ev, el, spec) => {
    if (!interactive || el.locked) return
    if (!['text', 'static'].includes(spec.kind)) return
    ev.preventDefault(); ev.stopPropagation()
    onSelect(el.id)
    const start = { id: el.id, value: String(shownText(el)) }
    editRef.current = start; setEditing(start)
  }

  const commitEdit = (keep) => {
    const ed = editRef.current
    editRef.current = null
    setEditing(null)
    if (!ed || !keep) return
    const el = els.find((x) => x.id === ed.id)
    if (!el || String(shownText(el)) === ed.value) return
    // Emptying a field's box is how you give the product's own value back —
    // otherwise a mistyped override would be a blank line with no visible cause.
    onChange(el.id, el.field === 'custom_text'
      ? { text: ed.value }
      : { text: ed.value, use_text: ed.value !== '' })
  }

  // Selecting is separate from dragging, because a LOCKED element must still be
  // selectable — the properties panel is the only place its lock can be undone,
  // and an element you cannot select is one you cannot unlock.
  const pick = (ev, el) => {
    if (!interactive) return
    ev.stopPropagation()
    if (editing && editing.id === el.id) return   // clicking inside the editor
    onSelect(el.id)
    if (!el.locked) drag(ev, el, 'move')
  }

  const drag = (ev, el, mode) => {
    if (!interactive || el.locked) return
    ev.preventDefault(); ev.stopPropagation()
    onSelect(el.id)
    const x0 = ev.clientX, y0 = ev.clientY
    const o = { x: el.x, y: el.y, w: el.w, h: el.h }
    const square = specs[el.field]?.kind === 'qr'
    // One history entry for the whole gesture, taken on the first millimetre
    // that actually moves: an undo that walks back through every frame of a drag
    // is unusable, and a click that selects a box is not an edit to undo.
    let opened = false
    const move = (e) => {
      if (!opened) { opened = true; begin() }
      const dx = (e.clientX - x0) / px, dy = (e.clientY - y0) / px
      if (mode === 'move') {
        onChange(el.id, { x: snap(clamp(o.x + dx, 0, tpl.width_mm - o.w)),
                          y: snap(clamp(o.y + dy, 0, tpl.height_mm - o.h)) },
                 { silent: true })
      } else {
        let w = snap(clamp(o.w + dx, 1, tpl.width_mm - o.x))
        let h = snap(clamp(o.h + dy, 0.3, tpl.height_mm - o.y))
        // a QR is square by definition — a stretched symbol is an unreadable one
        if (square) { w = h = Math.min(w, h) }
        onChange(el.id, { w, h }, { silent: true })
      }
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <div className="lsurface" ref={innerRef}
      style={{ width: tpl.width_mm * px, height: tpl.height_mm * px,
               border: tpl.border ? '1px solid #000' : '1px dashed #ccc',
               fontFamily: tpl.font }}
      onPointerDown={() => { if (!interactive) return; commitEdit(true); onSelect(null) }}>
      {els.map((e) => {
        const spec = specs[e.field]
        if (!spec) return null
        const sel = interactive && selId === e.id
        const box = {
          position: 'absolute', left: e.x * px, top: e.y * px,
          width: e.w * px, height: e.h * px, zIndex: e.z,
          opacity: e.visible === false ? (interactive ? 0.28 : 0) : 1,
          outline: sel ? '1.5px solid var(--brand)' : (e.border ? '.5px solid #000' : 'none'),
          cursor: interactive ? (e.locked ? 'not-allowed' : 'move') : 'default',
        }
        let inner
        if (spec.kind === 'qr') {
          inner = <div className="lqr" style={{ width: '100%', height: '100%', background: '#fff' }}
            dangerouslySetInnerHTML={{ __html: symbols.qr_svg || '' }} />
        } else if (spec.kind === 'barcode') {
          inner = <div className="lqr" style={{ width: '100%', height: '100%', background: '#fff' }}
            dangerouslySetInnerHTML={{ __html: symbols.barcode_svg || '' }} />
        } else if (spec.kind === 'line') {
          inner = <div style={{ width: '100%', height: '100%', background: e.color || '#000' }} />
        } else if (spec.kind === 'image') {
          inner = e.src
            ? <img src={e.src} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
            : <span className="lph">logo</span>
        } else {
          const raw = shownText(e)
          const txt = `${e.prefix || ''}${e.uppercase ? String(raw).toUpperCase() : raw}${e.suffix || ''}`
          const type = {
            display: 'block', width: '100%', height: '100%',
            fontFamily: e.font || tpl.font, fontSize: ptPx(e.size, px),
            lineHeight: `${e.h * px}px`, fontWeight: e.bold ? 700 : 400,
            fontStyle: e.italic ? 'italic' : 'normal',
            textAlign: e.align || 'left', color: e.color || '#000',
          }
          inner = editing && editing.id === e.id ? (
            // eslint-disable-next-line jsx-a11y/no-autofocus
            <input className="ltextin" autoFocus value={editing.value}
              style={{ ...type, textTransform: e.uppercase ? 'uppercase' : 'none' }}
              onChange={(ev) => {
                const next = { id: e.id, value: ev.target.value }
                editRef.current = next; setEditing(next)
              }}
              onPointerDown={(ev) => ev.stopPropagation()}
              onBlur={() => commitEdit(true)}
              onKeyDown={(ev) => {
                ev.stopPropagation()          // arrow keys move the caret, not the box
                if (ev.key === 'Enter') { ev.preventDefault(); commitEdit(true) }
                if (ev.key === 'Escape') { ev.preventDefault(); commitEdit(false) }
              }} />
          ) : (
            <span style={{ ...type, overflow: 'hidden', whiteSpace: 'nowrap',
                           textOverflow: 'ellipsis' }}>
              {txt === '' ? (interactive ? <span className="lph">{spec.label}</span> : '') : txt}
            </span>
          )
        }
        return (
          <div key={e.id} style={box}
            title={interactive ? `${spec.label}${e.locked ? ' · locked' : ''}`
              + (['text', 'static'].includes(spec.kind) && !e.locked ? ' · double-click to type over it' : '')
              : undefined}
            onDoubleClick={(ev) => startEdit(ev, e, spec)}
            onPointerDown={(ev) => pick(ev, e)}>
            {inner}
            {sel && !e.locked && (
              <span className="lhandle" onPointerDown={(ev) => drag(ev, e, 'resize')}
                title="Drag to resize" />
            )}
            {sel && e.locked && <span className="llock" title="Locked — unlock it in Properties to move it">🔒</span>}
            {/* A line that has stopped following the product prints the same
                words on every garment. That is sometimes exactly right and
                sometimes a mistake nobody would spot on screen — so it is
                marked on the canvas, not just in the panel. */}
            {interactive && e.use_text && !(editing && editing.id === e.id) && (
              <span className="lfixed" title={`Fixed text — this box no longer shows the product's ${spec.label}. Clear it to get the live value back.`}>✎</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

//: Zoom steps, in screen pixels per millimetre. 8 shows a 50mm label at 400px,
//: which is about the size it prints at on a normal monitor.
const ZOOMS = [4, 6, 8, 11, 15]

//: The stock sizes the server offers, if it is old enough not to offer any.
//: Typing a size always works, so this is a convenience and never a constraint.
const FALLBACK_SIZES = [
  { key: '50x35', w: 50, h: 35, label: '50 × 35 — standard garment tag' },
  { key: '100x150', w: 100, h: 150, label: '100 × 150 — shipping' },
]

// The label's own size — a control, not a number buried three panels away.
// Presets are the roll someone actually loaded; the two boxes are for the die
// that is not on the list. Both go through one `onResize`, so a preset and a
// typed size do exactly the same thing to the design.
function SizeBox({ draft, sizes, scaleOn, onScaleOn, onResize, column }) {
  // Held as text while it is being typed. Clamping "1" to 10 the instant it is
  // typed makes "100" impossible to enter — so the numbers are taken on Enter
  // or when the box is left, and clamped then.
  const [w, setW] = useState(String(draft.width_mm))
  const [h, setH] = useState(String(draft.height_mm))
  useEffect(() => { setW(String(draft.width_mm)); setH(String(draft.height_mm)) },
    [draft.width_mm, draft.height_mm])
  const list = (sizes && sizes.length) ? sizes : FALLBACK_SIZES
  const here = `${+draft.width_mm}x${+draft.height_mm}`
  const preset = list.find((s) => `${s.w}x${s.h}` === here)
  return (
    <div className={'lsize' + (column ? ' col' : '')}>
      <select value={preset ? preset.key : ''} title="The label stock this design is cut to"
        onChange={(e) => {
          const s = list.find((x) => x.key === e.target.value)
          if (s) onResize(s.w, s.h)
        }}>
        <option value="">{preset ? 'Custom size…' : `Custom · ${draft.width_mm} × ${draft.height_mm} mm`}</option>
        {list.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
      </select>
      <div className="mm">
        <input type="number" step="0.5" min="10" max="300" value={w} title="Width in millimetres"
          onChange={(e) => setW(e.target.value)}
          onBlur={() => onResize(parseFloat(w), parseFloat(h))}
          onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} />
        <span>×</span>
        <input type="number" step="0.5" min="10" max="300" value={h} title="Height in millimetres"
          onChange={(e) => setH(e.target.value)}
          onBlur={() => onResize(parseFloat(w), parseFloat(h))}
          onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} />
        <span>mm</span>
        <button className="btn" title="Turn the label on its side"
          onClick={() => onResize(draft.height_mm, draft.width_mm)}>⇄</button>
      </div>
      <label className="chk" title="On: the whole design grows or shrinks with the label, type included — a 50mm design becomes the same design at 100mm. Off: the fields keep their millimetre positions and only the ones hanging over the new edge are pulled back in.">
        <input type="checkbox" checked={scaleOn} onChange={(e) => onScaleOn(e.target.checked)} />
        {' '}Scale the design with the label
      </label>
    </div>
  )
}

function LabelDesigner({ toast, role }) {
  const [cat, setCat] = useState(null)            // the field catalogue
  const [templates, setTemplates] = useState([])
  const [draft, setDraft] = useState(null)        // the template being edited
  const [dirty, setDirty] = useState(false)
  const [selId, setSelId] = useState(null)
  const [px, setPx] = useState(8)
  const [preview, setPreview] = useState(null)    // {values, qr_svg, barcode_svg, source}
  const [products, setProducts] = useState([])
  const [previewId, setPreviewId] = useState('')  // which product the canvas shows
  const [qrNote, setQrNote] = useState('')
  const [err, setErr] = useState('')              // why the screen is empty
  const [scaleOn, setScaleOn] = useState(true)    // resize the design with the label
  const surfRef = useRef(null)                    // the label itself, for drop coords
  const canvRef = useRef(null)                    // the scroll area, for Fit
  // Element ids only have to be unique within one template, and they are what
  // selection and every edit key off — so they are minted from a counter rather
  // than from the clock, which two adds in the same millisecond would collide on.
  const seq = useRef(0)
  const newId = () => `e${Date.now().toString(36)}${(seq.current += 1)}`

  const specs = useMemo(() => {
    const m = {}; (cat?.fields || []).forEach((f) => { m[f.key] = f }); return m
  }, [cat])

  // ---- undo / redo -------------------------------------------------------
  // The whole draft is the unit of history. It is a few kilobytes of JSON, every
  // edit already builds a new object rather than mutating the old one, and a
  // stack of snapshots cannot drift out of step with the document the way a
  // stack of inverse-operations can. Entries are pushed BEFORE a change lands,
  // by whoever makes it — so what an undo restores is a state that was on screen.
  const hist = useRef({ past: [], future: [], tag: null, at: 0 })
  const [histN, setHistN] = useState([0, 0])      // [undoable, redoable], for the buttons
  const draftRef = useRef(null)
  useEffect(() => { draftRef.current = draft })

  const resetHistory = () => {
    hist.current = { past: [], future: [], tag: null, at: 0 }
    setHistN([0, 0])
  }
  //: `tag` coalesces a run of edits of the same kind into one step: dragging a
  //: box across the label is one thing that happened, not sixty, and typing a
  //: font size is one thing, not four. A different tag, or a pause, starts a new
  //: entry. Untagged edits — adding, deleting, aligning — always get their own.
  const push = (tag) => {
    const h = hist.current, d = draftRef.current
    if (!d) return
    const now = Date.now()
    if (tag && h.tag === tag && now - h.at < 900) { h.at = now; return }
    if (h.past[h.past.length - 1] === d) { h.at = now; return }   // nothing moved since
    h.past.push(d)
    if (h.past.length > 80) h.past.shift()        // 80 steps back is further than anyone goes
    h.future = []; h.tag = tag || null; h.at = now
    setHistN([h.past.length, 0])
  }
  const step = (back) => {
    const h = hist.current, d = draftRef.current
    const from = back ? h.past : h.future
    const to = back ? h.future : h.past
    if (!from.length || !d) return
    to.push(d)
    const next = from.pop()
    h.tag = null
    draftRef.current = next
    setDraft(next); setDirty(true)
    // the element that was selected may not exist in the state being restored
    setSelId((s) => (next.elements.some((e) => e.id === s) ? s : null))
    setHistN([h.past.length, h.future.length])
  }
  const undo = () => step(true)
  const redo = () => step(false)

  const loadTemplates = useCallback(() => api.labelTemplates().then(setTemplates), [])
  // Deliberately once, on mount. `toast` is re-created on every render of the
  // app shell, so listing it here would re-run this on every render — and since
  // the failure path reports an error, a server that 404s these routes would
  // re-render, re-fetch, re-fail and shout forever.
  useEffect(() => {
    api.labelFields().then(setCat).catch((e) => setErr(
      (e.status === 404 || e.status === 405)
        ? 'restart'
        : `The field catalogue could not be read (${e.message || 'the request failed'}).`))
    loadTemplates().catch(() => {})
    api.listProducts().then(setProducts).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadTemplates])

  // the values the canvas draws. Re-fetched when the chosen product changes,
  // because the whole point of previewing against real stock is seeing a real
  // description overflow its box before a roll of stickers proves it
  useEffect(() => {
    api.labelPreviewValues(previewId || undefined).then(setPreview).catch(() => {})
  }, [previewId])

  const sel = draft?.elements.find((e) => e.id === selId) || null

  // The QR's size is the one property whose mistake is invisible on screen: a
  // symbol scaled down to fit still looks like a QR and only stops working once
  // it is on a garment. Keyed on the WIDTH alone, so a drag re-checks when the
  // size settles rather than on every frame of it.
  const qrW = draft?.elements.find((e) => specs[e.field]?.kind === 'qr')?.w
  useEffect(() => {
    if (qrW == null) { setQrNote(''); return undefined }
    let live = true
    api.labelQrCheck(qrW, previewId || undefined)
      .then((r) => { if (live) setQrNote(r.warning || '') }).catch(() => {})
    return () => { live = false }
  }, [qrW, previewId])

  //: Every mutator takes the same optional third argument: `{silent}` while a
  //: gesture is still running (the gesture pushed its own entry when it began),
  //: `{tag}` for a run of keystrokes that is really one edit.
  const edit = (patch, opt) => {
    if (!opt?.silent) push(opt?.tag)
    setDraft((d) => ({ ...d, ...patch })); setDirty(true)
  }
  const editEl = (id, patch, opt) => {
    if (!opt?.silent) push(opt?.tag)
    setDraft((d) => ({ ...d, elements: d.elements.map((e) => (e.id === id ? { ...e, ...patch } : e)) }))
    setDirty(true)
  }

  // Changing the label's size. Two jobs, and which one is wanted is the whole
  // reason for the tick-box: someone who bought a bigger roll wants the SAME
  // design, bigger — someone correcting 50×35 to 50×40 wants the design left
  // exactly where it is. Either way nothing may end up over the edge, because
  // the server clamps on save and a design that silently moves between the
  // screen and the sticker is worse than one that moves while you watch.
  const resizeLabel = (w0, h0) => {
    const lo = cat?.min_mm || 10, hi = cat?.max_mm || 300
    const w = clamp(+w0 || draft.width_mm, lo, hi)
    const h = clamp(+h0 || draft.height_mm, lo, hi)
    if (w === draft.width_mm && h === draft.height_mm) return
    push('size')
    const r2 = (v) => Math.round(v * 100) / 100
    setDraft((d) => {
      const sx = w / d.width_mm, sy = h / d.height_mm
      const k = Math.min(sx, sy)                  // type scales by the smaller of the two
      const els = d.elements.map((e) => {
        const n = scaleOn
          ? { ...e, x: r2(e.x * sx), y: r2(e.y * sy), w: r2(e.w * sx), h: r2(e.h * sy),
              size: Math.round(clamp(e.size * k, 3, 72) * 2) / 2 }
          : { ...e }
        if (specs[e.field]?.kind === 'qr') { n.w = n.h = Math.min(n.w, n.h) }
        n.w = clamp(n.w, 0.5, w); n.h = clamp(n.h, 0.2, h)
        n.x = snap(clamp(n.x, 0, w - n.w)); n.y = snap(clamp(n.y, 0, h - n.h))
        return n
      })
      return { ...d, width_mm: w, height_mm: h, elements: els }
    })
    setDirty(true)
  }

  // Zoom until the whole label is in view. Once any size is designable this is
  // the only zoom control that keeps working — a 100 × 150 shipping label at
  // "100%" is taller than the screen.
  const fitZoom = () => {
    const box = canvRef.current
    if (!box || !draft) return
    const z = Math.min((box.clientWidth - 70) / draft.width_mm,
                       (box.clientHeight - 130) / draft.height_mm)
    setPx(clamp(Math.round(z * 2) / 2, 1, 20))
  }

  const addField = (key, at) => {
    const spec = specs[key]; if (!spec || !draft) return
    push()
    const w = Math.min(spec.w || 20, draft.width_mm)
    const h = Math.min(spec.h || 4, draft.height_mm)
    const id = newId()
    const el = {
      id, field: key, w, h,
      x: snap(clamp(at ? at.x - w / 2 : 2, 0, draft.width_mm - w)),
      y: snap(clamp(at ? at.y - h / 2 : 2, 0, draft.height_mm - h)),
      size: spec.size || 8, bold: !!spec.bold, italic: false,
      align: spec.align || 'left', font: '', color: '#000000',
      prefix: '', suffix: '', uppercase: false, border: false,
      visible: true, locked: false, text: '', src: '',
      z: (draft.elements.reduce((m, e) => Math.max(m, e.z), 0) || 0) + 1,
    }
    setDraft((d) => ({ ...d, elements: [...d.elements, el] }))
    setSelId(id); setDirty(true)
  }
  const removeEl = (id) => {
    push()
    setDraft((d) => ({ ...d, elements: d.elements.filter((e) => e.id !== id) }))
    setSelId(null); setDirty(true)
  }
  const dupEl = (el) => {
    push()
    const id = newId()
    const copy = { ...el, id, x: snap(clamp(el.x + 2, 0, draft.width_mm - el.w)),
      y: snap(clamp(el.y + 2, 0, draft.height_mm - el.h)), locked: false,
      z: (draft.elements.reduce((m, e) => Math.max(m, e.z), 0) || 0) + 1 }
    setDraft((d) => ({ ...d, elements: [...d.elements, copy] }))
    setSelId(id); setDirty(true)
  }
  // z is re-numbered on every save, so "forward" only has to end up above the
  // element it was below — swapping with the neighbour is exactly that
  const restack = (el, dir) => {
    const ordered = [...draft.elements].sort((a, b) => a.z - b.z)
    const i = ordered.findIndex((e) => e.id === el.id)
    const j = i + dir
    if (j < 0 || j >= ordered.length) return
    push()
    const a = ordered[i].z, b = ordered[j].z
    setDraft((d) => ({ ...d, elements: d.elements.map((e) =>
      e.id === ordered[i].id ? { ...e, z: b } : e.id === ordered[j].id ? { ...e, z: a } : e) }))
    setDirty(true)
  }
  const align = (how) => {
    if (!sel) return
    const p = { left: { x: 0 }, right: { x: snap(draft.width_mm - sel.w) },
      hcentre: { x: snap((draft.width_mm - sel.w) / 2) }, top: { y: 0 },
      bottom: { y: snap(draft.height_mm - sel.h) },
      vcentre: { y: snap((draft.height_mm - sel.h) / 2) } }[how]
    if (p) editEl(sel.id, p)
  }

  // arrow keys nudge, Delete removes — a mm of precision is not a mouse's job
  useEffect(() => {
    if (!draft || !selId) return
    const onKey = (e) => {
      const t = e.target.tagName
      if (t === 'INPUT' || t === 'SELECT' || t === 'TEXTAREA') return
      const el = draft.elements.find((x) => x.id === selId)
      if (!el || el.locked) return
      const step = e.shiftKey ? 1 : 0.5
      const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key]
      if (d) {
        e.preventDefault()
        // a held-down arrow key is one nudge that went too far, not thirty
        editEl(el.id, { x: snap(clamp(el.x + d[0], 0, draft.width_mm - el.w)),
                        y: snap(clamp(el.y + d[1], 0, draft.height_mm - el.h)) },
               { tag: `nudge:${el.id}` })
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault(); removeEl(el.id)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // Ctrl-Z / Ctrl-Y anywhere on the screen — except inside a text box, where the
  // browser's own undo is the one the person means.
  useEffect(() => {
    if (!draft) return undefined
    const onKey = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return
      const t = e.target.tagName
      if (t === 'INPUT' || t === 'SELECT' || t === 'TEXTAREA') return
      const k = e.key.toLowerCase()
      if (k === 'z' && !e.shiftKey) { e.preventDefault(); undo() }
      else if (k === 'y' || (k === 'z' && e.shiftKey)) { e.preventDefault(); redo() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const newTemplate = () => {
    setDraft({ id: null, name: '', description: '', width_mm: 50, height_mm: 35,
      padding_mm: 2, border: true, target: 'product',
      font: 'Arial, Helvetica, sans-serif', elements: [], active: true, is_default: false })
    setSelId(null); setDirty(true); resetHistory()
  }
  // History belongs to the template on the bench. Carrying it across a Close
  // would let an undo pull one template's layout into another one.
  const openTemplate = (t) => {
    setDraft({ ...t, elements: [...(t.elements || [])] })
    setSelId(null); setDirty(false); resetHistory()
  }
  const save = async () => {
    if (!draft.name.trim()) { toast('Give the template a name first', 'err'); return }
    try {
      const body = { ...draft, created_by: role || 'admin' }
      const saved = draft.id ? await api.saveLabelTemplate(draft.id, body)
        : await api.createLabelTemplate(body)
      setDraft({ ...saved, elements: [...(saved.elements || [])] })
      setDirty(false); await loadTemplates()
      toast('✓ Template saved', 'ok')
    } catch (e) { toast(e.detail || 'Could not save the template', 'err') }
  }
  const act = async (fn, ok) => {
    try { await fn(); await loadTemplates(); toast(ok, 'ok') }
    catch (e) { toast(e.detail || 'That did not work', 'err') }
  }
  const close = () => {
    if (dirty && !window.confirm('Close without saving? The changes to this layout will be lost.')) return
    setDraft(null); setSelId(null); setDirty(false); resetHistory()
  }

  // A screen that says "loading…" forever is the one thing this must not do: the
  // usual reason it cannot load is a backend still running the code from before
  // these routes existed, which is a restart rather than a fault — and the
  // person looking at the blank screen is the person who can do it.
  if (err) return (
    <div className="screen scrolls">
      <div className="pagehead"><h2>Label Designer</h2></div>
      <div className="screenbody">
        <div className="warnbox" style={{ maxWidth: 620 }}>
          <h4>{err === 'restart' ? 'The server needs restarting' : 'Label Designer could not start'}</h4>
          <div className="small" style={{ color: 'var(--text-2)', lineHeight: 1.5 }}>
            {err === 'restart' ? <>
              This page was loaded from disk, but the routes it calls are registered when
              Python starts — and this server was started before Label Designer existed,
              so it answers <code>/api/labels/…</code> with 404. Stop the ESSA server
              (Ctrl-C in the run window), start it again with <code>run.bat</code>, and
              reload this page.
            </> : err}
          </div>
          <button className="btn" style={{ marginTop: 12 }} onClick={() => window.location.reload()}>
            ↻ Reload</button>
        </div>
      </div>
    </div>
  )
  if (!cat) return <div className="body"><div className="empty" style={{ marginTop: 100 }}>Loading the label fields…</div></div>

  // ---- the template list ----
  if (!draft) return (
    <div className="screen scrolls">
      <div className="pagehead">
        <h2>Label Designer</h2>
        {/* the subtitle used to be what pushed the actions to the right edge —
            without one, the spacer has to do it explicitly */}
        <div style={{ flex: 1 }} />
        <button className="btn primary" onClick={newTemplate}>+ New template</button>
      </div>
      <div className="screenbody">
        {templates.length === 0 && <div className="empty" style={{ marginTop: 60 }}>
          No templates yet.</div>}
        <div className="tplgrid">
          {templates.map((t) => (
            <div key={t.id} className={'tplcard' + (t.active ? '' : ' off')}>
              <div className="tplthumb">
                {/* fits the card in BOTH directions — bounding only the width
                    let a 150mm-tall shipping label make a 500px-tall card */}
                <LabelSurface tpl={t} values={cat.sample}
                  px={Math.min(3.4, 170 / t.width_mm, 150 / t.height_mm)}
                  symbols={{ qr_svg: preview?.qr_svg, barcode_svg: preview?.barcode_svg }}
                  specs={specs} interactive={false} selId={null}
                  onSelect={() => {}} onChange={() => {}} />
              </div>
              <div className="tplbody">
                <div className="nm">{t.name}
                  {t.is_default && <span className="badge confirmed" title="What the printing screen opens on">default</span>}
                  {!t.active && <span className="badge">inactive</span>}
                </div>
                <div className="small" style={{ color: 'var(--muted)' }}>
                  {t.width_mm}×{t.height_mm} mm · {(t.elements || []).length} field(s) ·
                  {t.target === 'unit' ? ' per-piece' : ' per-SKU'}
                </div>
                {t.description && <div className="small" style={{ color: 'var(--text-2)' }}>{t.description}</div>}
                <div className="tplacts">
                  <button className="btn" onClick={() => openTemplate(t)}>Edit</button>
                  <button className="btn" onClick={() => act(() => api.duplicateLabelTemplate(t.id), '✓ Duplicated')}>Duplicate</button>
                  {!t.is_default && <button className="btn" onClick={() => act(() => api.setDefaultLabelTemplate(t.id), '✓ Default template set')}>Make default</button>}
                  <button className="btn" onClick={() => act(() => api.setLabelTemplateActive(t.id, !t.active), t.active ? '✓ Deactivated' : '✓ Activated')}>
                    {t.active ? 'Deactivate' : 'Activate'}</button>
                  <a className="btn" href={api.labelPreviewUrl(t.id, previewId || undefined, 6)} target="_blank" rel="noreferrer">Print proof</a>
                  {!t.is_default && <button className="btn danger" onClick={() => {
                    if (window.confirm(`Delete “${t.name}”? Labels already printed from it are unaffected.`)) act(() => api.deleteLabelTemplate(t.id), '✓ Deleted')
                  }}>Delete</button>}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )

  // ---- the builder ----
  const groups = cat.groups.map((g) => [g, cat.fields.filter((f) => f.group === g)])
  const used = new Set(draft.elements.map((e) => e.field))
  // measured against the LABEL, not the scroll area it sits in — the drop lands
  // where the cursor is on the sticker, which is the whole promise of dragging it
  const onDrop = (ev) => {
    ev.preventDefault()
    const key = ev.dataTransfer.getData('text/label-field')
    if (!key) return
    const r = surfRef.current?.getBoundingClientRect()
    addField(key, r ? { x: (ev.clientX - r.left) / px, y: (ev.clientY - r.top) / px } : null)
  }

  return (
    <div className="body ldesign">
      {/* LEFT — the palette */}
      <div className="lpanel">
        <div className="head"><h3>Available information</h3></div>
        <div className="list">
          {groups.map(([g, fs]) => (
            <div key={g} className="lgroup">
              <h5>{g}</h5>
              {fs.map((f) => (
                <button key={f.key} className={'lfield' + (used.has(f.key) ? ' used' : '')}
                  draggable onDragStart={(e) => e.dataTransfer.setData('text/label-field', f.key)}
                  onClick={() => addField(f.key)}
                  title={f.hint || `Add ${f.label} to the label`}>
                  <span className="nm">{f.label}</span>
                  <span className="kd">{f.kind === 'text' ? 'field' : f.kind}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>

      {/* CENTRE — the canvas */}
      <div className="lcanvaswrap">
        <div className="toolbar ltoolbar">
          <input value={draft.name} onChange={(e) => edit({ name: e.target.value }, { tag: 'name' })}
            placeholder="Template name" style={{ width: 196, fontWeight: 600 }} />
          <div className="segbar">
            <button className="seg" onClick={undo} disabled={!histN[0]}
              title="Undo (Ctrl-Z)">↶</button>
            <button className="seg" onClick={redo} disabled={!histN[1]}
              title="Redo (Ctrl-Y)">↷</button>
          </div>
          <SizeBox draft={draft} sizes={cat.sizes} scaleOn={scaleOn}
            onScaleOn={setScaleOn} onResize={resizeLabel} />
          <div className="segbar">
            {ZOOMS.map((z) => (
              <button key={z} className={'seg' + (px === z ? ' on' : '')} onClick={() => setPx(z)}
                title={`${z} screen pixels per millimetre`}>{z === 8 ? '100%' : `${Math.round(z / 8 * 100)}%`}</button>
            ))}
            <button className="seg" onClick={fitZoom} title="Zoom until the whole label is in view">Fit</button>
          </div>
          <div className="spacer" />
          <select value={previewId} onChange={(e) => setPreviewId(e.target.value)}
            style={{ width: 200 }} title="Draw the canvas with a real product's data">
            <option value="">Sample data</option>
            {products.map((p) => <option key={p.id} value={p.id}>{p.sku} · {(p.name || p.description)?.slice(0, 40)}</option>)}
          </select>
          <button className="btn" onClick={close}>Close</button>
          <button className="btn primary" onClick={save} disabled={!dirty}>
            {dirty ? 'Save template' : 'Saved'}</button>
        </div>

        <div className="lcanvas" ref={canvRef} onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
          {preview && (
            <LabelSurface tpl={draft} values={preview.values} symbols={preview}
              px={px} selId={selId} onSelect={setSelId} onChange={editEl}
              onBegin={push} interactive specs={specs} innerRef={surfRef} />
          )}
          {draft.elements.length === 0 && (
            <div className="small" style={{ marginTop: 16, color: 'var(--muted)' }}>
              Empty label — drag a field from the left.
            </div>
          )}
          {qrNote && <div className="warnbox" style={{ marginTop: 18, maxWidth: 520 }}>
            <h4>The QR may not scan at this size</h4>
            <div className="small" style={{ color: 'var(--text-2)' }}>{qrNote}</div>
          </div>}
        </div>
      </div>

      {/* RIGHT — properties */}
      <div className="lprops">
        {!sel && (
          <>
            <div className="head"><h3>Label properties</h3></div>
            <div className="lform">
              <div className="field"><label>Description</label>
                <input value={draft.description || ''} placeholder="What this template is for"
                  onChange={(e) => edit({ description: e.target.value }, { tag: 'desc' })} /></div>
              <div className="field"><label>Label size</label>
                <SizeBox draft={draft} sizes={cat.sizes} scaleOn={scaleOn}
                  onScaleOn={setScaleOn} onResize={resizeLabel} column />
              </div>
              <div className="field"><label>Prints one label per</label>
                <select value={draft.target} onChange={(e) => edit({ target: e.target.value })}
                  title="A per-SKU label carries the product's QR; a per-piece label carries that garment's own code">
                  <option value="product">SKU (product QR)</option>
                  <option value="unit">Piece (each garment's own code)</option>
                </select></div>
              <div className="field"><label>Default font</label>
                <select value={draft.font} onChange={(e) => edit({ font: e.target.value })}>
                  {cat.fonts.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                </select></div>
              <label className="chk"><input type="checkbox" checked={!!draft.border}
                onChange={(e) => edit({ border: e.target.checked })} /> Print a border round the label</label>
              <label className="chk"><input type="checkbox" checked={!!draft.active}
                onChange={(e) => edit({ active: e.target.checked })} /> Active (offered when printing)</label>
              {/* Ticking makes this one the default; there is no un-ticking,
                  because a warehouse with no default template has nothing for
                  the printing screen to open on. Another template taking the
                  title is how this one loses it. */}
              <label className="chk" title={draft.is_default
                ? 'Already the default — make another template the default to change it'
                : 'The template QR / Label Printing opens on'}>
                <input type="checkbox" checked={!!draft.is_default} disabled={!!draft.is_default}
                  onChange={(e) => edit({ is_default: e.target.checked })} /> Default template</label>
            </div>
          </>
        )}
        {sel && (
          <>
            <div className="head"><h3>{specs[sel.field]?.label}</h3></div>
            <div className="lform">
              <div className="row2">
                <div className="field"><label>X (mm)</label>
                  <input type="number" step="0.5" value={sel.x} disabled={sel.locked}
                    onChange={(e) => editEl(sel.id, { x: clamp(+e.target.value || 0, 0, draft.width_mm - sel.w) }, { tag: `x:${sel.id}` })} /></div>
                <div className="field"><label>Y (mm)</label>
                  <input type="number" step="0.5" value={sel.y} disabled={sel.locked}
                    onChange={(e) => editEl(sel.id, { y: clamp(+e.target.value || 0, 0, draft.height_mm - sel.h) }, { tag: `y:${sel.id}` })} /></div>
              </div>
              <div className="row2">
                <div className="field"><label>{specs[sel.field]?.kind === 'qr' ? 'QR size (mm)' : 'Width (mm)'}</label>
                  <input type="number" step="0.5" value={sel.w} disabled={sel.locked}
                    onChange={(e) => {
                      const w = clamp(+e.target.value || 1, 0.5, draft.width_mm - sel.x)
                      editEl(sel.id, specs[sel.field]?.kind === 'qr' ? { w, h: w } : { w }, { tag: `w:${sel.id}` })
                    }} /></div>
                <div className="field"><label>Height (mm)</label>
                  <input type="number" step="0.5" value={sel.h}
                    disabled={sel.locked || specs[sel.field]?.kind === 'qr'}
                    onChange={(e) => editEl(sel.id, { h: clamp(+e.target.value || 1, 0.2, draft.height_mm - sel.y) }, { tag: `h:${sel.id}` })} /></div>
              </div>
              <div className="lalign">
                <span className="small">Align on label</span>
                <div>
                  {[['left', '⇤'], ['hcentre', '↔'], ['right', '⇥'], ['top', '⇡'], ['vcentre', '↕'], ['bottom', '⇣']].map(([k, g]) => (
                    <button key={k} className="btn" disabled={sel.locked} onClick={() => align(k)} title={k}>{g}</button>
                  ))}
                </div>
              </div>

              {['text', 'static'].includes(specs[sel.field]?.kind) && <>
                {/* Words. A template normally holds a REFERENCE — "whatever this
                    product's colour is" — and that is what makes one template
                    print the whole warehouse. Typing over a box (here, or by
                    double-clicking it on the label) breaks that link for that
                    one box, on purpose and visibly: some lines really are the
                    same on every garment, and a care instruction is not a
                    column in the products table. */}
                {sel.field === 'custom_text' ? (
                  <div className="field"><label>Text</label>
                    <input value={sel.text || ''} placeholder="What this line should say"
                      onChange={(e) => editEl(sel.id, { text: e.target.value }, { tag: `t:${sel.id}` })} /></div>
                ) : <>
                  <label className="chk" title="Off: this box prints whatever the product says, and a corrected value reaches the next label on its own. On: it prints these words on every label, whatever the product says.">
                    <input type="checkbox" checked={!!sel.use_text}
                      onChange={(e) => editEl(sel.id, e.target.checked
                        ? { use_text: true, text: sel.text || String(preview?.values?.[sel.field] ?? '') }
                        : { use_text: false })} />
                    {' '}Fixed text instead of the {specs[sel.field]?.label?.toLowerCase()}
                  </label>
                  {sel.use_text && (
                    <div className="field">
                      <input value={sel.text || ''} placeholder="What this line should say"
                        onChange={(e) => editEl(sel.id, { text: e.target.value }, { tag: `t:${sel.id}` })} />
                      <span className="small" style={{ color: 'var(--warn)' }}>
                        Printed on every label.
                      </span>
                    </div>
                  )}
                </>}
                <div className="field"><label>Font</label>
                  <select value={sel.font || ''} onChange={(e) => editEl(sel.id, { font: e.target.value })}>
                    <option value="">Template default</option>
                    {cat.fonts.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                  </select></div>
                <div className="row2">
                  <div className="field"><label>Font size (pt)</label>
                    <input type="number" step="0.5" value={sel.size}
                      onChange={(e) => editEl(sel.id, { size: clamp(+e.target.value || 8, 3, 72) }, { tag: `s:${sel.id}` })} /></div>
                  <div className="field"><label>Text align</label>
                    <select value={sel.align} onChange={(e) => editEl(sel.id, { align: e.target.value })}>
                      {cat.aligns.map((a) => <option key={a} value={a}>{a}</option>)}
                    </select></div>
                </div>
                <div className="row2">
                  <div className="field"><label>Prefix</label>
                    <input value={sel.prefix || ''} placeholder="Size: "
                      onChange={(e) => editEl(sel.id, { prefix: e.target.value }, { tag: `p:${sel.id}` })} /></div>
                  <div className="field"><label>Suffix</label>
                    <input value={sel.suffix || ''} placeholder="% OFF"
                      onChange={(e) => editEl(sel.id, { suffix: e.target.value }, { tag: `f:${sel.id}` })} /></div>
                </div>
                <label className="chk"><input type="checkbox" checked={!!sel.bold}
                  onChange={(e) => editEl(sel.id, { bold: e.target.checked })} /> Bold</label>
                <label className="chk"><input type="checkbox" checked={!!sel.italic}
                  onChange={(e) => editEl(sel.id, { italic: e.target.checked })} /> Italic</label>
                <label className="chk"><input type="checkbox" checked={!!sel.uppercase}
                  onChange={(e) => editEl(sel.id, { uppercase: e.target.checked })} /> UPPERCASE</label>
              </>}

              {specs[sel.field]?.kind === 'image' && (
                <div className="field"><label>Image</label>
                  <input type="file" accept="image/*" onChange={(e) => {
                    const f = e.target.files[0]; if (!f) return
                    if (f.size > 400 * 1024) { toast('Use an image under 400 KB — it is stored inside the template', 'err'); return }
                    const rd = new FileReader()
                    rd.onload = () => editEl(sel.id, { src: rd.result })
                    rd.readAsDataURL(f)
                  }} />
                </div>
              )}

              <div className="row2">
                <div className="field"><label>Colour</label>
                  <input type="color" value={sel.color || '#000000'}
                    onChange={(e) => editEl(sel.id, { color: e.target.value }, { tag: `c:${sel.id}` })} /></div>
                <div className="field"><label>Layer</label>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button className="btn" onClick={() => restack(sel, 1)} title="Bring forward">▲</button>
                    <button className="btn" onClick={() => restack(sel, -1)} title="Send backward">▼</button>
                  </div></div>
              </div>
              <label className="chk"><input type="checkbox" checked={!!sel.border}
                onChange={(e) => editEl(sel.id, { border: e.target.checked })} /> Box border</label>
              <label className="chk"><input type="checkbox" checked={sel.visible !== false}
                onChange={(e) => editEl(sel.id, { visible: e.target.checked })} /> Visible</label>
              <label className="chk" title="Locked fields cannot be moved or resized by accident — the QR and the SKU are worth locking once they are right">
                <input type="checkbox" checked={!!sel.locked}
                  onChange={(e) => editEl(sel.id, { locked: e.target.checked })} /> 🔒 Lock this field</label>

              <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                <button className="btn" onClick={() => dupEl(sel)}>Duplicate</button>
                <button className="btn danger" disabled={sel.locked} onClick={() => removeEl(sel.id)}>Remove</button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ==========================================================================
//  QR / Label Printing — the daily screen
//  ------------------------------------------------------------------------
//  Pick stock, pick a template, check the proof, print. Nothing here can change
//  a design, which is the point: the person doing this is holding a roll of
//  stickers, not deciding what a label looks like.
// ==========================================================================
function LabelPrinting({ toast }) {
  const [templates, setTemplates] = useState([])
  const [tplId, setTplId] = useState(0)
  const [products, setProducts] = useState([])
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState({})        // {product_id: qty}
  const [preview, setPreview] = useState(null)
  const [cat, setCat] = useState(null)

  useEffect(() => {
    api.labelFields().then(setCat).catch(() => {})
    api.labelTemplates().then((ts) => {
      const live = ts.filter((t) => t.active)
      setTemplates(live)
      const def = live.find((t) => t.is_default) || live[0]
      if (def) setTplId(def.id)
    }).catch((e) => toast(
      e.status === 404
        ? 'This server was started before Label Printing existed — restart the ESSA server and reload.'
        : 'Could not load the templates', 'err'))
    api.listProducts().then(setProducts).catch(() => {})
    // once, on mount — see the same note in LabelDesigner: `toast` changes
    // identity every render, and this effect's failure path raises one
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const tpl = templates.find((t) => t.id === tplId) || null
  const perPiece = tpl?.target === 'unit'

  const specs = useMemo(() => {
    const m = {}; (cat?.fields || []).forEach((f) => { m[f.key] = f }); return m
  }, [cat])

  // The proof is drawn to FIT the panel it sits in, not at a fixed zoom. It used
  // to render at 8 px/mm whatever the template was: a 50mm label came out 400px
  // wide inside a 300px column and lost both its edges to the scrollbar, and a
  // 100 × 150 shipping label was a corner of itself. The panel is a fixed column
  // and templates are any size between 10 and 300mm, so the only stable answer
  // is to measure the box and scale to it — never magnifying past 8 px/mm, which
  // is roughly life size on a normal monitor.
  const proofRef = useRef(null)
  const [proofW, setProofW] = useState(0)
  useEffect(() => {
    const el = proofRef.current
    if (!el) return undefined
    setProofW(el.clientWidth)
    // once, on mount: the box is always rendered, and re-observing on every
    // render would tear the observer down and build it again for nothing
    if (typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver((es) => setProofW(es[0].contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  //: 12px of room for the box's own padding, so the label never sits edge to edge
  const proofPx = (tpl && proofW)
    ? Math.max(0.6, Math.min(8, (proofW - 12) / tpl.width_mm))
    : 8

  const visible = products.filter((p) => matches(p, q, ['sku', 'description', 'size', 'color', 'category', 'supplier_name', 'barcode']))
  const page = usePaged(visible, 50)
  const ids = Object.keys(picked).map(Number)
  // a per-piece run prints one label per garment, so its count is the live piece
  // codes, not a quantity anybody types
  const total = ids.reduce((n, id) => {
    const p = products.find((x) => x.id === id)
    return n + (perPiece ? (p?.live_units || 0) : (+picked[id] || 0))
  }, 0)

  const defaultQty = (p) => Math.max(1, Math.round(p.stock_qty || 1))

  // The last row clicked, so shift-click can select the run between it and the
  // next one. A whole delivery of one design is thirty consecutive rows — six
  // sizes across five colours, all wanting labels — and ticking those one at a
  // time is where this screen actually costs somebody their morning.
  const lastClicked = useRef(null)

  const toggle = (p, shiftKey = false) => {
    const rows = page.slice
    const here = rows.findIndex((r) => r.id === p.id)

    if (shiftKey && lastClicked.current != null) {
      const from = rows.findIndex((r) => r.id === lastClicked.current)
      if (from >= 0 && here >= 0) {
        const [a, b] = from < here ? [from, here] : [here, from]
        // The run takes the state the ANCHOR row is about to have, so
        // shift-clicking always does one thing to the whole range rather than
        // inverting each row and leaving a stripe of the ones already on.
        const turningOn = picked[p.id] == null
        setPicked((sel) => {
          const n = { ...sel }
          for (let i = a; i <= b; i++) {
            const r = rows[i]
            if (turningOn) n[r.id] = n[r.id] ?? defaultQty(r)
            else delete n[r.id]
          }
          return n
        })
        lastClicked.current = p.id
        return
      }
    }

    lastClicked.current = p.id
    setPicked((sel) => {
      const n = { ...sel }
      if (n[p.id] != null) delete n[p.id]
      else n[p.id] = defaultQty(p)
      return n
    })
  }

  // Everything on THIS page, not every row the filter matched. A search for
  // "Cherry" can be four hundred products across sixteen pages, and a tick box
  // that quietly selected all of them is how somebody prints four hundred
  // labels meaning to print twenty. The count beside it says which it is.
  const pageIds = page.slice.map((r) => r.id)
  const allOnPage = pageIds.length > 0 && pageIds.every((id) => picked[id] != null)
  const someOnPage = pageIds.some((id) => picked[id] != null)
  const toggleAllOnPage = () => setPicked((sel) => {
    const n = { ...sel }
    if (allOnPage) pageIds.forEach((id) => delete n[id])
    else page.slice.forEach((r) => { n[r.id] = n[r.id] ?? defaultQty(r) })
    return n
  })

  const allMatching = visible.length > 0 && visible.every((r) => picked[r.id] != null)
  const selectAllMatching = () => setPicked((sel) => {
    const n = { ...sel }
    visible.forEach((r) => { n[r.id] = n[r.id] ?? defaultQty(r) })
    return n
  })

  const setQty = (id, v) => setPicked((s) => ({ ...s, [id]: Math.max(1, Math.min(+v || 1, 2000)) }))

  // The proof is drawn from one of the selected products rather than from sample
  // data, because a label that comes out wrong is usually wrong about a
  // product's data — a description too long for its box, a missing MRP — and
  // that is invisible against a sample record that has every field filled.
  useEffect(() => {
    const first = ids[0]
    api.labelPreviewValues(first || undefined).then(setPreview).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids[0]])

  const blocked = ids.map((id) => products.find((p) => p.id === id))
    .filter((p) => p && p.can_print === false)

  const doPrint = () => {
    if (!tpl) { toast('Choose a template first', 'err'); return }
    if (!ids.length) { toast('Select at least one product', 'err'); return }
    if (blocked.length) {
      toast(`${blocked[0].sku}: ${blocked[0].print_block}`, 'err'); return
    }
    const url = perPiece
      ? api.labelPrintUrl(tpl.id, [], { unitProducts: ids })
      : api.labelPrintUrl(tpl.id, ids.map((id) => ({ id, qty: picked[id] })))
    window.open(url, '_blank')
  }

  return (
    <div className="screen scrolls">
      <div className="pagehead">
        <h2>QR / Label Printing</h2>
      </div>
      <div className="lprintwrap">
        <div>
          <div className="toolbar">
            {/* supplier was always searchable here — it just never said so */}
            <SearchBox value={q} onChange={setQ} placeholder="Search product / SKU / size / colour / supplier…"
              style={{ width: 320 }} />
            <div className="field" style={{ width: 280, margin: 0 }}><label>Template</label>
              <select value={tplId} onChange={(e) => setTplId(+e.target.value)}>
                {templates.length === 0 && <option value={0}>No active template</option>}
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}{t.is_default ? ' — default' : ''} ({t.width_mm}×{t.height_mm} mm)
                  </option>
                ))}
              </select></div>
            <div className="spacer" />
            {/* Everything the search matched, across every page — the thing you
                want after filtering to one supplier or one design. Separate
                from the header tick box, and it says the number, because
                "select all" meaning four hundred rows when the screen shows
                fifty is how the wrong run gets printed. */}
            {visible.length > page.slice.length && (
              <button className="btn" onClick={selectAllMatching}
                disabled={allMatching}
                title={`Select every product the current search matched (${visible.length})`}>
                Select all {visible.length}{q ? ' matching' : ''}
              </button>
            )}
            <button className="btn" onClick={() => setPicked({})} disabled={!ids.length}>Clear selection</button>
          </div>

          {perPiece && <div className="infobox" style={{ marginBottom: 12 }}>
            <b>{tpl.name}</b> prints one label per garment, each carrying that piece's own
            code — so the quantity is the number of piece codes the SKU has, not a number
            you choose.
          </div>}

          <div className="tablewrap">
          <table className="items">
            <thead><tr>
              <th style={{ width: 34 }}>
                {/* Indeterminate when only part of the page is selected, so the
                    box shows the three states it actually has rather than
                    reading as "none" whenever it is not all. */}
                <input type="checkbox" checked={allOnPage}
                  ref={(el) => { if (el) el.indeterminate = someOnPage && !allOnPage }}
                  onChange={toggleAllOnPage} disabled={!page.slice.length}
                  title={allOnPage ? 'Clear the products on this page'
                    : `Select all ${page.slice.length} products on this page`} />
              </th><th>SKU</th><th>Product</th>
              <th>Size</th><th>Colour</th>
              {/* Who it came from — on screen only, to tell two identical-looking
                  rows apart before printing a hundred tags of the wrong one. It
                  is NOT a label field: what prints is whatever the template lays
                  out, and no template is touched by this column. */}
              <th>Supplier</th>
              <th style={{ textAlign: 'right' }}>Stock</th>
              <th style={{ textAlign: 'right', width: 110 }}>{perPiece ? 'Pieces' : 'Labels'}</th>
            </tr></thead>
            <tbody>
              {page.slice.map((p) => {
                const on = picked[p.id] != null
                return (
                  <tr key={p.id} className={on ? 'sel' : ''}
                    style={p.can_print === false ? { color: 'var(--muted)' } : undefined}>
                    {/* onClick, not onChange: the modifier keys are on the mouse
                        event and a change event has none, so shift-click has to
                        be read here. */}
                    <td><input type="checkbox" checked={on} readOnly
                      onClick={(e) => toggle(p, e.shiftKey)}
                      title={p.can_print === false ? p.print_block
                        : 'Select for printing — shift-click to take a run of rows'} /></td>
                    <td className="mono">{p.sku || '—'}</td>
                    <td>{p.name || p.description}
                      {p.can_print === false && <span className="badge needs_review" style={{ marginLeft: 6 }}
                        title={p.print_block}>cannot print</span>}</td>
                    <td>{p.size || '—'}</td>
                    <td>{p.color || '—'}</td>
                    <td title={p.supplier_name ? `Received from ${p.supplier_name}` : 'No supplier recorded against this item'}>
                      {p.supplier_name || '—'}</td>
                    <td className="num">{p.stock_qty}</td>
                    <td style={{ textAlign: 'right' }}>
                      {!on ? '—' : perPiece ? (p.live_units || 0)
                        : <input type="number" min="1" value={picked[p.id]} style={{ width: 78 }}
                            onChange={(e) => setQty(p.id, e.target.value)} />}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          </div>
          <Pager {...page} noun="product" />
        </div>

        <div className="lprintside">
          <div className="section">
            <h4>Preview
              {tpl && (
                <span className="proofsize">
                  {tpl.width_mm} × {tpl.height_mm} mm
                  {proofPx < 7.9 ? ` · shown at ${Math.round((proofPx / 8) * 100)}%` : ''}
                </span>
              )}
            </h4>
            <div className="small" style={{ color: 'var(--muted)', margin: '-4px 0 10px' }}>
              {ids.length ? `${preview?.values?.sku || 'A selected product'}, in the chosen template.`
                : 'Sample data — select a product to see its own.'}
            </div>
            <div className="lproof" ref={proofRef}>
              {tpl && preview && cat
                ? <LabelSurface tpl={tpl} values={preview.values} symbols={preview} px={proofPx}
                    specs={specs} interactive={false} selId={null}
                    onSelect={() => {}} onChange={() => {}} />
                : <div className="small">Nothing to preview yet.</div>}
            </div>
            <div className="items-foot" style={{ marginTop: 12 }}>
              <span>Selected <b>{ids.length}</b> product{ids.length === 1 ? '' : 's'}</span>
              <span>Labels <b>{total}</b></span>
            </div>
            {blocked.length > 0 && <div className="warnbox" style={{ marginTop: 12 }}>
              <h4>{blocked.length} selected product(s) cannot be printed</h4>
              <div className="small" style={{ color: 'var(--text-2)' }}>{blocked[0].print_block}</div>
            </div>}
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <a className="btn" href={tpl ? api.labelPreviewUrl(tpl.id, ids[0], 6) : '#'}
                target="_blank" rel="noreferrer"
                onClick={(e) => { if (!tpl) e.preventDefault() }}
                title="Open a full sheet of six, exactly as it will print">Preview sheet</a>
              <button className="btn primary" onClick={doPrint} disabled={!total}>
                🖨 Print {total || ''} label{total === 1 ? '' : 's'}</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ==========================================================================
//  Notifications
//  ------------------------------------------------------------------------
//  The dashboard already says what is waiting on someone — but only to whoever
//  opens the dashboard. This is the same queues, carried: a bell that counts
//  what has not been seen, a panel that opens the screen which clears it, and a
//  roster of who is meant to be watching.
//
//  Read means read AT A NUMBER. Acknowledging "4 drafts" stores 4; a fifth makes
//  it unread again, a third leaves it quiet. That is what makes a bell you can
//  clear and still trust — see services/notifications.py.
// ==========================================================================

const NOTIF_LEVEL = {
  critical: { dot: '🔥', label: 'Critical', tone: 'crit' },
  warn: { dot: '🔴', label: 'Needs attention', tone: 'warn' },
  info: { dot: '🟠', label: 'For information', tone: 'info' },
}

// One notice, wherever it is shown — the bell panel and the dashboard section
// render the same row, so a notice never says two different things in two places.
function NoticeRow({ n, onOpen, onRead, onMute, compact }) {
  return (
    <div className={'notice' + (n.unread ? ' unread' : '')}>
      <span className="ndot" title={NOTIF_LEVEL[n.level]?.label}>{n.dot}</span>
      <div className="nbody">
        <div className="ntitle">{n.title}</div>
        <div className="nsub">{n.body}</div>
        <div className="nmeta">
          {n.waiting ? <>waiting {n.waiting}</> : null}
          {n.read_by ? <> · read by {n.read_by}</> : null}
        </div>
      </div>
      <div className="nacts">
        <button className="btn" onClick={() => onOpen(n)} title="Open the screen that clears this">Open</button>
        {!compact && n.unread && (
          <button className="btn" onClick={() => onRead(n)} title="Acknowledge it at this count — it comes back if it grows">Mark read</button>
        )}
        {!compact && onMute && (
          <button className="btn" onClick={() => onMute(n)} title="Silence this queue until it is unmuted">Mute</button>
        )}
      </div>
    </div>
  )
}

// The bell. Polls only the four counts — the feed is read when the panel is
// actually opened, because the full pass walks every queue in the warehouse.
//
// The button and the panel are deliberately two components: the bell belongs on
// the chrome, and the panel must NOT be a child of it. Everything under .topbar
// is styled for a dark brown bar — `.topbar .btn:not(.primary)` paints buttons
// transparent with near-white text — so a white panel rendered inside the header
// comes out with invisible buttons. Overlays in this app live at the app root
// (see how VisionSettings and ScanningOverlay are mounted), and `tick` is what
// lets the badge re-read itself after the panel marks something read.
function NotificationBell({ onOpen, tick }) {
  const [counts, setCounts] = useState(null)
  const [err, setErr] = useState('')

  const poll = useCallback(() => api.notificationCount().then((c) => { setCounts(c); setErr('') })
    .catch((e) => setErr(e.status === 404 ? 'restart' : '')), [])
  useEffect(() => { poll() }, [poll, tick])
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === 'visible') poll() }, 60000)
    document.addEventListener('visibilitychange', poll)
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', poll) }
  }, [poll])

  const unread = counts?.unread || 0
  return (
    <button className={'bell' + (unread ? ' has' : '')} onClick={onOpen}
      title={err === 'restart'
        ? 'The server is running code from before notifications existed — restart it'
        : unread ? `${unread} unread notification${unread === 1 ? '' : 's'}` : 'Notifications — nothing unread'}>
      🔔{unread > 0 && <span className={'bellcount' + (counts.critical ? ' crit' : '')}>{unread}</span>}
    </button>
  )
}

function NotificationPanel({ go, user, toast, onClose, onChanged }) {
  const [feed, setFeed] = useState(null)
  const [tab, setTab] = useState('inbox')
  const [err, setErr] = useState('')
  useEffect(() => {
    api.notifications().then(setFeed)
      .catch((e) => setErr(e.status === 404 ? 'restart' : 'failed'))
  }, [])
  const apply = (f) => f.then((r) => { setFeed(r); onChanged() })
    .catch(() => toast('Could not save that', 'err'))

  return (
    <div className="piece-wrap" onClick={onClose}>
      <div className="piece-card notifpanel" onClick={(e) => e.stopPropagation()}>
        <div className="piece-head">
          <b>🔔 Notifications</b>
          <div className="segbar" style={{ marginLeft: 12 }}>
            <button className={'seg' + (tab === 'inbox' ? ' on' : '')} onClick={() => setTab('inbox')}>Inbox</button>
            <button className={'seg' + (tab === 'people' ? ' on' : '')} onClick={() => setTab('people')}
              title="Who is meant to be watching these, and on what number">People</button>
          </div>
          <button className="btn" style={{ marginLeft: 'auto' }} onClick={onClose}
            title="Close">✕</button>
        </div>
        <div className="piece-body" style={{ maxHeight: '68vh', overflow: 'auto' }}>
          {err && <div className="warnbox" style={{ marginBottom: 12 }}>
            <h4>{err === 'restart' ? 'Notifications need a restart' : 'The queues could not be read'}</h4>
            <div className="small" style={{ color: 'var(--text-2)' }}>
              {err === 'restart'
                ? 'The server is still running the code from before this existed. Stop it in the run window (Ctrl-C) and start run.bat again.'
                : 'Nothing was returned. Refresh, or check the run window for an error.'}</div></div>}

          {tab === 'inbox' && !err && (!feed ? <div className="empty">Reading the queues…</div> : (
            <>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
                <span className="small" style={{ color: 'var(--text-2)' }}>
                  {feed.counts.total
                    ? <>{feed.counts.total} open · <b>{feed.counts.unread}</b> unread</>
                    : 'Nothing is waiting — every queue in the warehouse is clear.'}
                </span>
                {feed.counts.unread > 0 && <button className="btn" style={{ marginLeft: 'auto' }}
                  onClick={() => apply(api.notificationsReadAll(user))}>Mark all read</button>}
              </div>
              {feed.notices.map((n) => (
                <NoticeRow key={n.key} n={n}
                  onOpen={(x) => { onClose(); go(x.module) }}
                  onRead={(x) => apply(api.notificationsRead([x.key], user))}
                  onMute={(x) => apply(api.notificationMute(x.key, true, user))} />
              ))}
              {!feed.notices.length && <div className="empty" style={{ marginTop: 20 }}>
                Nothing open. Notices appear here the moment a queue stops being empty.</div>}
              <MutedList onChanged={(r) => { setFeed(r); onChanged() }} user={user} />
            </>
          ))}

          {tab === 'people' && <RecipientList toast={toast} />}
        </div>
      </div>
    </div>
  )
}

// Muted queues, listed where they were muted from. A silence nobody can find is
// a silence nobody can undo, and this is the screen someone comes back to.
function MutedList({ onChanged, user }) {
  const [rows, setRows] = useState([])
  // Through api rather than a bare fetch: only the calls in that module carry
  // the signed-in token, and one that goes round it is one the server refuses.
  const load = useCallback(() => api.notificationsMuted()
    .then(setRows).catch(() => {}), [])
  useEffect(() => { load() }, [load])
  if (!rows.length) return null
  return (
    <div style={{ marginTop: 16, borderTop: '1px solid var(--line)', paddingTop: 12 }}>
      <div className="small" style={{ color: 'var(--muted)', marginBottom: 8 }}>Muted queues</div>
      {rows.map((m) => (
        <div key={m.key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '5px 0' }}>
          <span className="small" style={{ flex: 1 }}>{m.title}
            {!m.open_now && <span style={{ color: 'var(--muted)' }}> · clear right now</span>}</span>
          <button className="btn" style={{ padding: '2px 9px' }}
            onClick={() => api.notificationMute(m.key, false, user).then((r) => { onChanged(r); load() })}>Unmute</button>
        </div>
      ))}
    </div>
  )
}

// Who is meant to be watching, and on what number. Delivery is in-app today —
// said plainly here rather than implied by a number box that looks like it sends.
function RecipientList({ toast }) {
  const [rows, setRows] = useState([])
  const [form, setForm] = useState({ name: '', mobile: '', role: '', levels: ['critical', 'warn', 'info'] })
  const load = useCallback(() => api.notificationRecipients().then(setRows).catch(() => {}), [])
  useEffect(() => { load() }, [load])
  const add = async () => {
    if (!form.name.trim()) { toast('A name is needed', 'err'); return }
    try {
      await api.addRecipient(form)
      setForm({ name: '', mobile: '', role: '', levels: ['critical', 'warn', 'info'] })
      load(); toast('✓ Added to the list', 'ok')
    } catch (e) { toast(e.detail || 'Could not add them', 'err') }
  }
  const toggleLevel = (r, lvl) => {
    const has = (r.levels || []).includes(lvl)
    const levels = has ? r.levels.filter((l) => l !== lvl) : [...(r.levels || []), lvl]
    api.updateRecipient(r.id, { levels }).then(load).catch(() => toast('Could not save', 'err'))
  }
  const remove = (r) => {
    if (!window.confirm(`Remove ${r.name} from the notification list?`)) return
    api.deleteRecipient(r.id).then(load).catch(() => toast('Could not remove them', 'err'))
  }
  return (
    <>
      <div className="small" style={{ color: 'var(--text-2)', marginBottom: 12, lineHeight: 1.6 }}>
        Who watches these queues, and on what number. <b>Notices are delivered in the app</b> —
        the bell here and the Notifications tab in the warehouse phone app. The number is held
        against the person so a channel that dials out (SMS or WhatsApp) can be switched on later
        without collecting this list again.
      </div>
      {rows.length > 0 && (
        <div className="tablewrap">
          <table className="items">
            <thead><tr><th>Name</th><th>Mobile</th><th>Watches</th>
              <th style={{ width: 210 }}>Gets</th><th style={{ width: 34 }}></th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id}>
                <td><b>{r.name}</b></td>
                <td className="mono">{r.mobile || '—'}</td>
                <td className="small">{r.role || '—'}</td>
                <td>{Object.keys(NOTIF_LEVEL).map((lvl) => (
                  <button key={lvl} className={'fchip' + ((r.levels || []).includes(lvl) ? ' on' : '')}
                    style={{ marginRight: 4 }} title={NOTIF_LEVEL[lvl].label}
                    onClick={() => toggleLevel(r, lvl)}>{NOTIF_LEVEL[lvl].dot}</button>
                ))}</td>
                <td><button className="btn" style={{ padding: '2px 7px' }} onClick={() => remove(r)}>×</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {!rows.length && <div className="empty" style={{ margin: '10px 0' }}>
        Nobody on the list yet.</div>}
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', marginTop: 14, flexWrap: 'wrap' }}>
        <div className="field" style={{ minWidth: 150 }}><label>Name</label>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="e.g. Sharu" /></div>
        <div className="field" style={{ minWidth: 160 }}><label>Mobile number</label>
          <input value={form.mobile} onChange={(e) => setForm({ ...form, mobile: e.target.value })}
            inputMode="tel" placeholder="+91 98765 43210" /></div>
        <div className="field" style={{ minWidth: 150 }}><label>What they watch</label>
          <input value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}
            placeholder="e.g. Warehouse in-charge" /></div>
        <button className="btn primary" onClick={add}>Add</button>
      </div>
    </>
  )
}

// ==========================================================================
//  Dead Stock & Clearance
//  ------------------------------------------------------------------------
//  Five screens over ONE read of the stock: the dashboard is it totalled, the
//  register is it listed, the summary is it grouped, the cash impact is it
//  valued, and the worksheet is the part of it somebody has decided to act on.
//  Only the worksheet writes anything, and what it writes is a plan — the
//  quantity sold and the cash realised are read back off the till against the
//  product, so no clearance line ever becomes a second stock record.
// ==========================================================================

const DS_TABS = [
  ['dashboard', '📊 Dashboard', 'What has gone quiet, and what it is worth'],
  ['register', '📋 Register', 'Every dead line, with its age band and clearance price'],
  ['worksheet', '🏷 Clearance Worksheet', 'Campaigns, their actions, and what actually sold'],
  ['summary', '📈 Summary', 'Dead stock by category and by age band'],
  ['cash', '💰 Cash Impact', 'Capital locked, cash expected, and what it would earn'],
  ['rules', '⚙ Discount Rules', 'The age ladder and the assumptions behind the projection'],
]

//: the age bands, coloured by how urgent they are rather than by name
const DS_TONE = { critical: 'crit', dead: 'dead', approaching: 'warn', healthy: 'ok' }
const DS_DOT = { critical: '🔥', dead: '🔴', approaching: '🟠', healthy: '🟢' }

//: what the age is being measured from, said in words on the row it applies to
const DS_BASIS = {
  sale: 'since the last till sale',
  dispatch: 'since it was dispatched to a store',
  received: 'never sold — since it last came in',
  never: 'nothing to date it from',
}

const rupees = (v) => (v == null ? '—' : '₹ ' + money(v))
const pct = (v) => (v == null ? '—' : v + '%')

function DsTiles({ tiles }) {
  return (
    <div className="dgrid">
      {tiles.map((t, i) => (
        <DashTile key={i} label={t.label} value={t.value} sub={t.sub} tone={t.tone}
          hint={t.hint} onClick={t.onClick || (() => {})} />
      ))}
    </div>
  )
}

// The three warnings, in the order they should be acted on. Not one 90-day
// event: at 60 days a small markdown may still move the line, and at 180 the
// question has stopped being "what discount" and become "who takes the lot".
function DsAlerts({ alerts, go, compact }) {
  if (!alerts?.length) {
    return compact ? null : (
      <div className="warnbox clean" style={{ marginBottom: 14 }}>
        <h4 style={{ border: 'none', margin: 0 }}>Nothing has gone quiet — every stocked line has moved inside the window.</h4>
      </div>
    )
  }
  return (
    <div style={{ display: 'grid', gap: 10, marginBottom: 16 }}>
      {alerts.map((a) => (
        <div key={a.level} className="warnbox"
          style={a.level === 'approaching' ? undefined : { borderColor: 'var(--danger-line)', background: 'var(--danger-bg)' }}>
          <h4 style={{ border: 'none', margin: 0, color: a.level === 'approaching' ? 'var(--warn)' : 'var(--danger)' }}>
            {DS_DOT[a.level]} {a.title} — {a.lines} product{a.lines === 1 ? '' : 's'}, {a.note}
          </h4>
          <div className="small" style={{ color: 'var(--text-2)', marginTop: 4 }}>
            {a.qty} pcs · stock value <b>{rupees(a.stock_value)}</b> · expected on clearance <b>{rupees(a.expected_realisation)}</b>
            {go && <>{'  '}<button className="btn" style={{ padding: '2px 9px', marginLeft: 8 }}
              onClick={() => go(a.level)}>View these</button></>}
          </div>
        </div>
      ))}
    </div>
  )
}

// Where the sales came from. Said on every screen that counts a sale, because a
// register that quietly counts nothing when the till is off is worse than one
// that admits it — every line would read as dead.
function DsSource({ pos }) {
  if (!pos) return null
  return (
    <div className="small" style={{ color: 'var(--muted)', marginTop: 8 }}>
      {pos.available
        ? <>Sales read from the shop (POS) — {pos.linked_products} item{pos.linked_products === 1 ? '' : 's'} sold there are warehouse stock
            {pos.last_sale ? <>, last bill {pos.last_sale}</> : null}. Stock dispatched to a store counts as movement too.</>
        : <><b>The shop (POS) is not installed here</b>, so no till sales are visible. Ages are measured from
            dispatches and receipts only — a line sold at the counter would still read as unsold.</>}
    </div>
  )
}

function DeadStock({ toast, go, intent, onIntentUsed }) {
  const [tab, setTab] = useState('dashboard')
  const [sum, setSum] = useState(null)
  const [alerts, setAlerts] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const loadSummary = useCallback(() => {
    setBusy(true)
    return Promise.all([api.deadStockSummary(), api.deadStockAlerts()])
      .then(([s, a]) => { setSum(s); setAlerts(a); setErr('') })
      .catch((e) => setErr(e.status === 404
        ? 'The server is still running the code from before Dead Stock & Clearance existed — restart the backend (Ctrl-C in the run window, then run.bat again) and reload this page.'
        : (e.message || 'Could not read the stock')))
      .finally(() => setBusy(false))
  }, [])
  useEffect(() => { loadSummary() }, [loadSummary])

  // the register opens filtered to whatever band was clicked on the dashboard
  const [regStatus, setRegStatus] = useState('dead')
  const openRegister = (status) => { setRegStatus(status || 'dead'); setTab('register') }
  // A card on the MAIN dashboard says which screen it meant, and filtered how —
  // "₹1.42L locked" opens the register on the lines holding it, not a landing
  // page somebody then has to navigate. Consumed once, so coming back to this
  // module later opens where it was left rather than replaying the last click.
  useEffect(() => {
    if (!intent) return
    if (intent.tab) setTab(intent.tab)
    if (intent.status) setRegStatus(intent.status)
    onIntentUsed && onIntentUsed()
  }, [intent])   // eslint-disable-line react-hooks/exhaustive-deps

  const t = sum?.totals
  const c = sum?.counts
  const cash = sum?.cash_impact

  return (
    <div className="screen scrolls">
      <div className="pagehead">
        <h2>Dead Stock &amp; Clearance</h2>
        <div className="pagesub small">
          Stock that has stopped moving, what it is worth to clear, and whether the clearance worked
        </div>
        <div style={{ flex: 1 }} />
        <button className="btn" onClick={loadSummary} disabled={busy}
          title="Re-read the stock, the till and the ledger">{busy ? 'Reading…' : '↻ Refresh'}</button>
      </div>

      <div style={{ padding: '14px var(--gutter) 0' }}>
        <div className="segbar" role="tablist" aria-label="Dead stock screens">
          {DS_TABS.map(([key, label, hint]) => (
            <button key={key} role="tab" aria-selected={tab === key} title={hint}
              className={'seg' + (tab === key ? ' on' : '')} onClick={() => setTab(key)}>{label}</button>
          ))}
        </div>
      </div>

      <div className="dash">
        {err && <div className="warnbox" style={{ marginBottom: 14 }}>
          <h4>Dead stock could not be read</h4>
          <div className="small" style={{ color: 'var(--text-2)' }}>{err}</div>
        </div>}

        {tab === 'dashboard' && sum && (
          <>
            <DsAlerts alerts={alerts?.alerts} go={openRegister} />
            <Section id="ds-tiles" title="Dead stock at a glance"
              summary={`${c.dead_total.lines} line(s) past ${c.thresholds.dead} days`}>
              <DsTiles tiles={[
                { label: 'Dead stock', value: c.dead_total.qty + ' pcs',
                  sub: `${c.dead_total.lines} product line(s) with no movement for ${c.thresholds.dead}+ days`,
                  tone: c.dead_total.lines ? 'warn' : '', onClick: () => openRegister('dead'),
                  hint: 'Open the register filtered to dead stock' },
                { label: 'Stock value', value: rupees(t.stock_value),
                  sub: 'capital sitting on the shelf', tone: c.dead_total.lines ? 'warn' : '',
                  onClick: () => setTab('cash') },
                { label: 'Expected cash', value: rupees(t.expected_realisation),
                  sub: 'if every line clears at its ladder price', onClick: () => setTab('cash') },
                { label: 'Recovery', value: t.recovery_pct == null ? '—' : t.recovery_pct + '%',
                  sub: 'expected cash against what it cost', onClick: () => setTab('cash') },
                { label: 'Approaching', value: c.approaching.qty + ' pcs',
                  sub: `${c.approaching.lines} line(s) quiet for ${c.thresholds.approaching}+ days — dead in under a month`,
                  tone: c.approaching.lines ? 'warn' : '', onClick: () => openRegister('approaching'),
                  hint: 'Open the register filtered to what is about to go dead' },
                { label: 'Critical', value: c.critical.qty + ' pcs',
                  sub: `${c.critical.lines} line(s) unsold for ${c.thresholds.critical}+ days`,
                  tone: c.critical.lines ? 'warn' : '', onClick: () => openRegister('critical') },
              ]} />
              <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                <button className="btn" onClick={() => openRegister('dead')}>View dead stock</button>
                <button className="btn primary" onClick={() => setTab('register')}>
                  Create a clearance worksheet</button>
              </div>
              <DsSource pos={sum.pos} />
            </Section>

            {sum.oldest.length > 0 && (
              <Section id="ds-oldest" title="Quietest lines" summary={`${sum.oldest.length} shown`}>
                <div className="tablewrap">
                  <table className="items">
                    <thead><tr><th>SKU</th><th>Product</th><th className="num">Qty</th>
                      <th className="num">Days</th><th>Measured from</th><th className="num">Stock value</th>
                      <th className="num">Discount</th><th className="num">Expected</th></tr></thead>
                    <tbody>{sum.oldest.map((r) => (
                      <tr key={r.product_id}>
                        <td className="mono">{r.sku}</td>
                        <td>{r.name}{r.size ? <span className="small"> · {r.size}</span> : null}</td>
                        <td className="num">{r.qty}</td>
                        <td className="num"><b>{r.days_idle}</b></td>
                        <td className="small">{DS_BASIS[r.basis]}</td>
                        <td className="num">{rupees(r.stock_value)}</td>
                        <td className="num">{pct(r.discount_pct)}</td>
                        <td className="num">{rupees(r.expected_realisation)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </Section>
            )}
          </>
        )}

        {tab === 'register' && <DsRegister toast={toast} status={regStatus} setStatus={setRegStatus}
          onChanged={loadSummary} />}
        {tab === 'worksheet' && <DsWorksheets toast={toast} />}
        {tab === 'summary' && sum && <DsSummary sum={sum} />}
        {tab === 'cash' && sum && <DsCash sum={sum} toast={toast} onSaved={loadSummary} />}
        {tab === 'rules' && <DsRules toast={toast} onSaved={loadSummary} />}
      </div>
    </div>
  )
}

// ---------- the register: every dead line, and what to do with it ----------
function DsRegister({ toast, status, setStatus, onChanged }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [q, setQ] = useState('')
  const [f, setF] = useState({ bucket: '', category: '', supplier: '', size: '', min_value: '', min_qty: '' })
  const [open, setOpen] = useState(false)          // filter panel
  const [sel, setSel] = useState(() => new Set())
  const [actions, setActions] = useState({})       // product_id → chosen action
  const [adding, setAdding] = useState(false)      // the "add to clearance" dialog

  const load = useCallback(() => {
    setBusy(true)
    return api.deadStock({ q, status, ...f })
      .then(setData).catch((e) => toast(e.message || 'Could not read the register', 'err'))
      .finally(() => setBusy(false))
  }, [q, status, f, toast])
  useEffect(() => { load() }, [load])

  const rows = data?.rows || []
  const page = usePaged(rows, 50)
  const chosen = rows.filter((r) => sel.has(r.product_id))
  const toggle = (id) => setSel((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  const allShown = page.slice.every((r) => sel.has(r.product_id))
  const toggleAll = () => setSel((s) => {
    const n = new Set(s)
    page.slice.forEach((r) => (allShown ? n.delete(r.product_id) : n.add(r.product_id)))
    return n
  })
  const selTotals = {
    qty: chosen.reduce((a, r) => a + r.qty, 0),
    cost: chosen.reduce((a, r) => a + r.stock_value, 0),
    expected: chosen.reduce((a, r) => a + r.expected_realisation, 0),
  }
  const active = Object.values(f).filter(Boolean).length + (q ? 1 : 0)

  return (
    <>
      <Section id="ds-register" title="Dead Stock Register"
        summary={data ? `${rows.length} line(s)` : 'reading…'}
        actions={<>
          <SearchBox value={q} onChange={setQ} placeholder="SKU, product, category, supplier…" />
          <FilterButton open={open} onToggle={() => setOpen((o) => !o)} active={active} />
        </>}>
        <div className="toolbar" style={{ marginBottom: 10 }}>
          <FilterChips value={status} onChange={setStatus} options={[
            ['dead', 'Dead', null, `No movement for ${data?.rules?.dead_after_days ?? 90}+ days — includes critical`],
            ['critical', 'Critical', null, `Unsold for ${data?.rules?.critical_days ?? 180}+ days`],
            ['approaching', 'Approaching', null, `Quiet for ${data?.rules?.approaching_days ?? 60}+ days, not dead yet`],
            ['healthy', 'Healthy', null, 'Still moving'],
            ['all', 'All stock', null, 'Every stocked line, whatever its age'],
          ]} />
        </div>
        <FilterPanel open={open} active={active} onClear={() => {
          setQ(''); setF({ bucket: '', category: '', supplier: '', size: '', min_value: '', min_qty: '' })
        }} onApply={load} hint="Narrow the register, then select the lines to clear.">
          <div><label>Age band</label>
            <select value={f.bucket} onChange={(e) => setF({ ...f, bucket: e.target.value })}>
              <option value="">Any</option>
              {(data?.options?.buckets || []).map((b) => <option key={b} value={b}>{b}</option>)}
            </select></div>
          <div><label>Category</label>
            <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
              <option value="">Any</option>
              {(data?.options?.categories || []).map((x) => <option key={x} value={x}>{x}</option>)}
            </select></div>
          <div><label>Supplier</label>
            <select value={f.supplier} onChange={(e) => setF({ ...f, supplier: e.target.value })}>
              <option value="">Any</option>
              {(data?.options?.suppliers || []).map((x) => <option key={x} value={x}>{x}</option>)}
            </select></div>
          <div><label>Size</label>
            <select value={f.size} onChange={(e) => setF({ ...f, size: e.target.value })}>
              <option value="">Any</option>
              {(data?.options?.sizes || []).map((x) => <option key={x} value={x}>{x}</option>)}
            </select></div>
          <div><label>Stock value at least</label>
            <input value={f.min_value} inputMode="decimal"
              onChange={(e) => setF({ ...f, min_value: e.target.value })} placeholder="₹" /></div>
          <div><label>Quantity at least</label>
            <input value={f.min_qty} inputMode="decimal"
              onChange={(e) => setF({ ...f, min_qty: e.target.value })} placeholder="pcs" /></div>
        </FilterPanel>

        {busy && !data && <div className="empty" style={{ marginTop: 30 }}>Reading the stock…</div>}
        {data && rows.length === 0 && (
          <div className="empty" style={{ marginTop: 30 }}>
            {status === 'dead'
              ? `Nothing has been still for ${data.rules.dead_after_days}+ days. Try “All stock” to see what is moving.`
              : 'Nothing matches these filters.'}
          </div>
        )}
        {rows.length > 0 && (
          <>
            <div className="tablewrap">
              <table className="items" style={{ minWidth: 1420 }}>
                <thead><tr>
                  <th style={{ width: 30 }}>
                    <input type="checkbox" checked={allShown} onChange={toggleAll}
                      title="Select every line on this page" /></th>
                  <th style={{ width: 96 }}>SKU</th>
                  <th style={{ minWidth: 190 }}>Product</th>
                  <th style={{ width: 58 }}>Size</th>
                  <th className="num" style={{ width: 56 }}>Qty</th>
                  <th className="num" style={{ width: 76 }}>Cost</th>
                  <th className="num" style={{ width: 76 }}>MRP</th>
                  <th style={{ width: 96 }}>Last sold</th>
                  <th className="num" style={{ width: 62 }}>Days</th>
                  <th style={{ width: 108 }}>Age band</th>
                  <th className="num" style={{ width: 66 }}>Disc</th>
                  <th className="num" style={{ width: 88 }}>Clearance</th>
                  <th className="num" style={{ width: 96 }}>Expected</th>
                  <th style={{ width: 132 }}>Action</th>
                </tr></thead>
                <tbody>{page.slice.map((r) => (
                  <tr key={r.product_id} style={sel.has(r.product_id) ? { background: 'var(--brand-50)' } : undefined}>
                    <td><input type="checkbox" checked={sel.has(r.product_id)}
                      onChange={() => toggle(r.product_id)} /></td>
                    <td className="mono">{r.sku}</td>
                    <td>{r.name}
                      {r.category && <div className="cellsub">{r.category}</div>}</td>
                    <td>{r.size || '—'}</td>
                    <td className="num">{r.qty}</td>
                    <td className="num">{rupees(r.cost_price)}</td>
                    <td className="num">{r.mrp == null
                      ? <span title={`No MRP on this product — the clearance price is worked out off ${r.price_source === 'cost' ? 'cost, which is a loss, not a markdown' : 'the sale price'}`}
                          style={{ color: 'var(--warn)' }}>none</span>
                      : rupees(r.mrp)}</td>
                    <td className="small" title={DS_BASIS[r.basis]}>
                      {fmtDate(r.moved_on)}
                      <div className="cellsub">{r.basis === 'sale' ? 'till sale'
                        : r.basis === 'dispatch' ? 'dispatched' : 'never sold'}</div></td>
                    <td className="num"><b>{r.days_idle}</b></td>
                    <td>{DS_DOT[r.status]} <span className="small">{r.bucket}</span></td>
                    <td className="num">{pct(r.discount_pct)}</td>
                    <td className="num">{rupees(r.clearance_price)}</td>
                    <td className="num">{rupees(r.expected_realisation)}</td>
                    <td>
                      <select value={actions[r.product_id] || 'Review'}
                        onChange={(e) => setActions({ ...actions, [r.product_id]: e.target.value })}
                        title="What to do with this line — carried onto the worksheet">
                        {DS_ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <Pager {...page} noun="line" />
            <div className="items-foot">
              <span>{rows.length} line(s) · Σ {data.totals.qty} pcs</span>
              <span>Σ stock value <b>{rupees(data.totals.stock_value)}</b></span>
              <span>Σ expected <b>{rupees(data.totals.expected_realisation)}</b></span>
              <button className="btn primary" style={{ marginLeft: 'auto' }}
                disabled={!chosen.length} onClick={() => setAdding(true)}
                title={chosen.length ? 'Put these lines on a clearance worksheet' : 'Select some lines first'}>
                Add {chosen.length || ''} to clearance</button>
            </div>
            {chosen.length > 0 && (
              <div className="small" style={{ color: 'var(--text-2)', marginTop: 6 }}>
                Selected: {chosen.length} line(s) · {Math.round(selTotals.qty * 1000) / 1000} pcs ·
                cost <b>{rupees(selTotals.cost)}</b> · expected <b>{rupees(selTotals.expected)}</b>
              </div>
            )}
          </>
        )}
        <DsSource pos={data?.pos} />
      </Section>

      {adding && (
        <DsAddToClearance rows={chosen} actions={actions} toast={toast}
          onClose={() => setAdding(false)}
          onDone={() => { setAdding(false); setSel(new Set()); load(); onChanged && onChanged() }} />
      )}
    </>
  )
}

const DS_ACTIONS = ['Clear Now', 'Markdown', 'Bundle', 'Promotional Sale',
  'Transfer to Store', 'Return to Supplier', 'Hold', 'Review']

// Putting selected lines on a worksheet — onto a draft that is already open, or
// onto a new one. Two ways in, because a clearance is built over a morning, not
// in one pass of the register.
function DsAddToClearance({ rows, actions, onClose, onDone, toast }) {
  const [drafts, setDrafts] = useState([])
  const [target, setTarget] = useState('new')
  const [name, setName] = useState(() => {
    const d = new Date()
    return `${d.toLocaleString('en-IN', { month: 'long' })} ${d.getFullYear()} Clearance`
  })
  const iso = (d) => d.toISOString().slice(0, 10)
  const [from, setFrom] = useState(() => iso(new Date()))
  const [to, setTo] = useState(() => iso(new Date(Date.now() + 30 * 864e5)))
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.clearanceList('draft').then(setDrafts).catch(() => {}) }, [])

  const ids = rows.map((r) => r.product_id)
  const picked = {}
  ids.forEach((id) => { picked[String(id)] = actions[id] || 'Review' })
  const totals = {
    qty: rows.reduce((a, r) => a + r.qty, 0),
    cost: rows.reduce((a, r) => a + r.stock_value, 0),
    expected: rows.reduce((a, r) => a + r.expected_realisation, 0),
  }

  const save = async () => {
    setBusy(true)
    try {
      const r = target === 'new'
        ? await api.clearanceCreate({ name, starts_on: from, ends_on: to, product_ids: ids, actions: picked })
        : await api.clearanceAddLines(+target, ids, picked)
      toast(`✓ ${r.added} line(s) on “${r.name}”${r.skipped ? ` · ${r.skipped} already there or out of stock` : ''}`, 'ok')
      onDone()
    } catch (e) { toast(e.detail || 'Could not add the lines', 'err'); setBusy(false) }
  }

  return (
    <div className="piece-wrap" onClick={onClose}>
      <div className="piece-card" style={{ maxWidth: 560 }} onClick={(e) => e.stopPropagation()}>
        <div className="piece-head"><b>Add to a clearance worksheet</b>
          <button className="btn" style={{ marginLeft: 'auto' }} onClick={onClose}>✕</button></div>
        <div className="piece-body">
          <div className="small" style={{ color: 'var(--text-2)', marginBottom: 12 }}>
            {rows.length} line(s) · {Math.round(totals.qty * 1000) / 1000} pcs ·
            stock cost <b>{rupees(totals.cost)}</b> · expected <b>{rupees(totals.expected)}</b>.
            The age, band, discount and price are copied onto the worksheet as they are today — that is
            what the campaign is approved at, and it does not drift as the stock goes on ageing.
          </div>
          <div className="field"><label>Worksheet</label>
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="new">➕ A new worksheet</option>
              {drafts.map((d) => <option key={d.id} value={d.id}>{d.name} · {d.line_count} line(s)</option>)}
            </select></div>
          {target === 'new' && <>
            <div className="field"><label>Campaign name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div style={{ display: 'flex', gap: 10 }}>
              <DateField label="Runs from" value={from} onChange={setFrom} />
              <DateField label="Runs to" value={to} onChange={setTo} />
            </div>
            <div className="small" style={{ color: 'var(--muted)', marginTop: 4 }}>
              Sales at the till between these dates are what this campaign realised.
            </div>
          </>}
          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" onClick={save} disabled={busy || !ids.length}>
              {busy ? 'Adding…' : `Add ${ids.length} line(s)`}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------- the worksheets, and how each one is actually doing ----------
function DsWorksheets({ toast }) {
  const [list, setList] = useState([])
  const [open, setOpen] = useState(null)          // the campaign being read
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => {
    setBusy(true)
    return api.clearanceList('all').then(setList).catch(() => {}).finally(() => setBusy(false))
  }, [])
  useEffect(() => { load() }, [load])

  const openOne = async (id) => {
    try { setOpen(await api.clearanceGet(id)) }
    catch { toast('Could not open that worksheet', 'err') }
  }
  const patch = async (body) => {
    try { const c = await api.clearanceUpdate(open.id, body); setOpen(c); load() }
    catch (e) { toast(e.detail || 'Could not save', 'err') }
  }
  const patchLine = async (lineId, body) => {
    try { setOpen(await api.clearanceUpdateLine(open.id, lineId, body)) }
    catch (e) { toast(e.detail || 'Could not save the line', 'err') }
  }
  const dropLine = async (lineId) => {
    try { setOpen(await api.clearanceDeleteLine(open.id, lineId)) }
    catch (e) { toast(e.detail || 'Could not remove the line', 'err') }
  }
  const remove = async (c) => {
    if (!window.confirm(`Delete “${c.name}”?\n\nThe worksheet and its ${c.line_count} line(s) go. No stock is affected — a clearance worksheet holds none.`)) return
    try { await api.clearanceDelete(c.id); toast('Worksheet deleted', 'ok'); setOpen(null); load() }
    catch (e) { toast(e.detail || 'Could not delete it', 'err') }
  }

  if (open) {
    const t = open.totals
    return (
      <Section id="ds-sheet" title={open.name}
        summary={`${open.status} · ${open.line_count} line(s)`}
        actions={<button className="btn" onClick={() => { setOpen(null); load() }}>‹ All worksheets</button>}>
        <div className="dgrid" style={{ marginBottom: 14 }}>
          <DashTile label="Products" value={t.qty + ' pcs'} sub={`${open.line_count} line(s) in the campaign`} />
          <DashTile label="Stock cost" value={rupees(t.stock_cost)} sub="what these pieces cost us" />
          <DashTile label="Expected" value={rupees(t.expected_realisation)} sub="at the approved clearance prices" />
          <DashTile label="Actually sold" value={t.sold_qty + ' pcs'}
            sub={t.sell_through_pct == null ? 'nothing yet' : `${t.sell_through_pct}% sell-through`}
            tone={t.sold_qty ? '' : 'warn'} />
          <DashTile label="Actually realised" value={rupees(t.actual_realisation)}
            sub={t.realisation_pct == null ? 'read from the till' : `${t.realisation_pct}% of expected`} />
          <DashTile label="Remaining" value={t.remaining_qty + ' pcs'} sub="still on the shelf" />
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 14 }}>
          <div className="field" style={{ minWidth: 220 }}><label>Campaign name</label>
            <input defaultValue={open.name} onBlur={(e) => e.target.value !== open.name && patch({ name: e.target.value })} /></div>
          <DateField label="Runs from" value={open.starts_on} onChange={(v) => patch({ starts_on: v })} />
          <DateField label="Runs to" value={open.ends_on} onChange={(v) => patch({ ends_on: v })} />
          <div className="field"><label>Status</label>
            <select value={open.status} onChange={(e) => patch({ status: e.target.value })}
              title="Draft is being built · Active is running · Closed stops counting new sales">
              <option value="draft">Draft</option><option value="active">Active</option>
              <option value="closed">Closed</option>
            </select></div>
          <button className="btn" onClick={() => remove(open)} title="Delete this worksheet">🗑 Delete</button>
        </div>
        <div className="small" style={{ color: 'var(--muted)', marginBottom: 10 }}>
          Sold and realised are the till's own figures for these products between the campaign dates —
          nothing is keyed in here, and nothing here holds stock.
        </div>
        <div className="tablewrap">
          <table className="items" style={{ minWidth: 1180 }}>
            <thead><tr>
              <th style={{ width: 96 }}>SKU</th><th style={{ minWidth: 180 }}>Product</th>
              <th className="num" style={{ width: 56 }}>Days</th><th style={{ width: 104 }}>Band</th>
              <th className="num" style={{ width: 66 }}>Disc %</th>
              <th className="num" style={{ width: 84 }}>Price</th>
              <th className="num" style={{ width: 60 }}>Qty</th>
              <th className="num" style={{ width: 92 }}>Expected</th>
              <th className="num" style={{ width: 56 }}>Sold</th>
              <th className="num" style={{ width: 72 }}>Left</th>
              <th className="num" style={{ width: 96 }}>Realised</th>
              <th style={{ width: 132 }}>Action</th><th style={{ width: 34 }}></th>
            </tr></thead>
            <tbody>{(open.lines || []).map((l) => (
              <tr key={l.id}>
                <td className="mono">{l.sku}</td>
                <td>{l.name}{l.size ? <span className="small"> · {l.size}</span> : null}</td>
                <td className="num">{l.days_idle}</td>
                <td className="small">{l.bucket}</td>
                <td className="num">
                  <input defaultValue={l.discount_pct} inputMode="decimal" style={{ width: 52 }}
                    title="Override the ladder for this line — the price and the expected figure follow it"
                    onBlur={(e) => Number(e.target.value) !== l.discount_pct
                      && patchLine(l.id, { discount_pct: Number(e.target.value) })} /></td>
                <td className="num">{rupees(l.clearance_price)}</td>
                <td className="num">{l.qty}</td>
                <td className="num">{rupees(l.expected_realisation)}</td>
                <td className="num"><b>{l.sold_qty}</b></td>
                <td className="num">{l.remaining_qty}</td>
                <td className="num">{rupees(l.actual_realisation)}</td>
                <td><select value={l.action || 'Review'} onChange={(e) => patchLine(l.id, { action: e.target.value })}>
                  {DS_ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}</select></td>
                <td><button className="btn" style={{ padding: '2px 7px' }} title="Remove this line"
                  onClick={() => dropLine(l.id)}>×</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        {!open.lines?.length && <div className="empty" style={{ marginTop: 24 }}>
          No lines yet — open the Register, select what to clear and add it here.</div>}
      </Section>
    )
  }

  return (
    <Section id="ds-sheets" title="Clearance worksheets" summary={`${list.length} campaign(s)`}>
      {busy && !list.length && <div className="empty" style={{ marginTop: 30 }}>Reading…</div>}
      {!busy && !list.length && (
        <div className="empty" style={{ marginTop: 30 }}>
          No clearance worksheets yet. Open the <b>Register</b>, select the lines to clear and add them to one.
        </div>
      )}
      {list.length > 0 && (
        <div className="tablewrap">
          <table className="items">
            <thead><tr><th>Campaign</th><th>Status</th><th>Period</th>
              <th className="num">Lines</th><th className="num">Pcs</th><th className="num">Stock cost</th>
              <th className="num">Expected</th><th className="num">Sold</th>
              <th className="num">Realised</th><th className="num">Sell-through</th><th></th></tr></thead>
            <tbody>{list.map((c) => (
              <tr key={c.id} style={{ cursor: 'pointer' }} onClick={() => openOne(c.id)}>
                <td><b>{c.name}</b>{c.note && <div className="cellsub">{c.note}</div>}</td>
                <td><span className={'badge ' + (c.status === 'closed' ? 'posted' : c.status === 'active' ? 'confirmed' : 'draft')}>{c.status}</span></td>
                <td className="small">{fmtDate(c.starts_on)} → {fmtDate(c.ends_on)}</td>
                <td className="num">{c.line_count}</td>
                <td className="num">{c.totals.qty}</td>
                <td className="num">{rupees(c.totals.stock_cost)}</td>
                <td className="num">{rupees(c.totals.expected_realisation)}</td>
                <td className="num">{c.totals.sold_qty}</td>
                <td className="num">{rupees(c.totals.actual_realisation)}</td>
                <td className="num">{c.totals.sell_through_pct == null ? '—' : c.totals.sell_through_pct + '%'}</td>
                <td><button className="btn" style={{ padding: '2px 7px' }}
                  onClick={(e) => { e.stopPropagation(); remove(c) }}>×</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Section>
  )
}

// ---------- the summary: the same dead stock, grouped ----------
function DsSummary({ sum }) {
  const t = sum.totals
  const maxCat = Math.max(1, ...sum.by_category.map((g) => g.stock_value))
  return (
    <>
      <Section id="ds-sum-tiles" title="Dead stock summary" summary={`${t.lines} line(s)`}>
        <DsTiles tiles={[
          { label: 'Dead stock', value: t.qty + ' pcs', sub: `${t.lines} product line(s)` },
          { label: 'Stock value', value: rupees(t.stock_value), sub: 'at weighted-average cost' },
          { label: 'Expected cash', value: rupees(t.expected_realisation), sub: 'at ladder prices' },
          { label: 'Recovery', value: t.recovery_pct == null ? '—' : t.recovery_pct + '%',
            sub: 'expected cash ÷ stock cost' },
        ]} />
      </Section>

      <Section id="ds-by-cat" title="By category" summary={`${sum.by_category.length} categor(y/ies)`}>
        {!sum.by_category.length && <div className="empty">Nothing dead to group.</div>}
        {sum.by_category.length > 0 && (
          <div className="tablewrap">
            <table className="items">
              <thead><tr><th>Category</th><th className="num">Lines</th><th className="num">Qty</th>
                <th className="num">Cost value</th><th className="num">Expected realisation</th>
                <th className="num">Recovery</th><th style={{ width: 180 }}>Share of locked capital</th></tr></thead>
              <tbody>{sum.by_category.map((g) => (
                <tr key={g.category}>
                  <td>{g.category}</td>
                  <td className="num">{g.lines}</td>
                  <td className="num">{g.qty}</td>
                  <td className="num">{rupees(g.stock_value)}</td>
                  <td className="num">{rupees(g.expected_realisation)}</td>
                  <td className="num">{g.recovery_pct == null ? '—' : g.recovery_pct + '%'}</td>
                  <td><div style={{ background: 'var(--panel-3)', borderRadius: 3, height: 10 }}>
                    <div style={{ width: `${Math.round(g.stock_value / maxCat * 100)}%`, height: '100%',
                      background: 'var(--brand)', borderRadius: 3 }} /></div></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Section>

      <Section id="ds-by-band" title="By age band" summary="the ladder, as it actually falls">
        {!sum.by_bucket.length && <div className="empty">Nothing dead to band.</div>}
        {sum.by_bucket.length > 0 && (
          <div className="tablewrap">
            <table className="items">
              <thead><tr><th>Age band</th><th className="num">Discount</th><th className="num">Lines</th>
                <th className="num">Qty</th><th className="num">Cost value</th>
                <th className="num">Expected realisation</th></tr></thead>
              <tbody>{sum.by_bucket.map((b) => (
                <tr key={b.bucket}>
                  <td>{b.bucket}</td>
                  <td className="num">{pct(b.discount_pct)}</td>
                  <td className="num">{b.lines}</td>
                  <td className="num">{b.qty}</td>
                  <td className="num">{rupees(b.stock_value)}</td>
                  <td className="num">{rupees(b.expected_realisation)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  )
}

// ---------- what clearing it is worth, and on what assumptions ----------
function DsCash({ sum, toast, onSaved }) {
  const c = sum.cash_impact
  const [turns, setTurns] = useState(String(c.stock_turns ?? ''))
  const [margin, setMargin] = useState(String(c.gross_margin_pct ?? ''))
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await api.saveDeadStockRules({ stock_turns: Number(turns), gross_margin_pct: Number(margin) })
      toast('✓ Assumptions saved', 'ok'); onSaved()
    } catch (e) { toast(e.detail || 'Could not save', 'err') }
    setBusy(false)
  }
  return (
    <>
      <Section id="ds-cash" title="Cash impact" summary="what the shelf is holding, and what it would return">
        <DsTiles tiles={[
          { label: 'Capital locked', value: rupees(c.capital_locked),
            sub: 'cost of stock that has stopped moving', tone: c.capital_locked ? 'warn' : '' },
          { label: 'Expected cash', value: rupees(c.expected_cash),
            sub: 'if every dead line clears at its ladder price' },
          { label: 'Expected recovery', value: c.recovery_pct == null ? '—' : c.recovery_pct + '%',
            sub: 'cash out against cost in' },
          { label: 'Annual revenue potential', value: rupees(c.annual_revenue_potential),
            sub: `that cash turned over ${c.stock_turns}× a year` },
          { label: 'Annual gross profit', value: rupees(c.annual_gross_profit),
            sub: `at ${c.gross_margin_pct}% gross margin` },
        ]} />
        <div className="small" style={{ color: 'var(--text-2)', marginTop: 12, maxWidth: 760, lineHeight: 1.6 }}>
          The first three figures are facts about stock that exists. The last two are a
          <b> projection</b>: freed capital, assumed to turn over {c.stock_turns} times a year at
          {' '}{c.gross_margin_pct}% margin. They are here because “₹{money(c.capital_locked)} is asleep on a shelf”
          is not an argument anyone acts on, and “that capital would earn ₹{money(c.annual_gross_profit)} a year
          if it were working” is.
        </div>
      </Section>
      <Section id="ds-assume" title="Assumptions" summary="used by the two projected figures above">
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="field"><label>Stock turns per year</label>
            <input value={turns} inputMode="decimal" onChange={(e) => setTurns(e.target.value)} style={{ width: 120 }} /></div>
          <div className="field"><label>Gross margin %</label>
            <input value={margin} inputMode="decimal" onChange={(e) => setMargin(e.target.value)} style={{ width: 120 }} /></div>
          <button className="btn primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save assumptions'}</button>
        </div>
      </Section>
    </>
  )
}

// ---------- the ladder, as a policy someone owns ----------
function DsRules({ toast, onSaved }) {
  const [rules, setRules] = useState(null)
  const [defaults, setDefaults] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.deadStockRules().then((r) => { setRules(r.rules); setDefaults(r.defaults) }).catch(() => {})
  }, [])
  if (!rules) return <div className="empty" style={{ marginTop: 30 }}>Reading the rules…</div>

  const setBucket = (i, k, v) => {
    const b = rules.buckets.map((x, j) => (j === i ? { ...x, [k]: v } : x))
    setRules({ ...rules, buckets: b })
  }
  const addBucket = () => setRules({
    ...rules,
    buckets: [...rules.buckets, { from: 0, to: null, label: '', discount: 0 }],
  })
  const dropBucket = (i) => setRules({ ...rules, buckets: rules.buckets.filter((_, j) => j !== i) })
  const save = async () => {
    setBusy(true)
    try {
      const r = await api.saveDeadStockRules({
        buckets: rules.buckets.map((b) => ({
          from: Number(b.from) || 0,
          to: b.to === '' || b.to == null ? null : Number(b.to),
          label: b.label, discount: Number(b.discount) || 0,
        })),
        approaching_days: Number(rules.approaching_days),
        dead_after_days: Number(rules.dead_after_days),
        critical_days: Number(rules.critical_days),
      })
      setRules(r.rules)
      toast('✓ Rules saved — every screen uses them from now on', 'ok')
      onSaved()
    } catch (e) { toast(e.detail || 'Could not save the rules', 'err') }
    setBusy(false)
  }

  return (
    <>
      <Section id="ds-ladder" title="Discount ladder"
        summary={`${rules.buckets.length} band(s)`}
        actions={<button className="btn" onClick={() => setRules({ ...rules, ...defaults })}
          title="Put back the ladder this install shipped with">Reset to default</button>}>
        <div className="small" style={{ color: 'var(--text-2)', marginBottom: 12, maxWidth: 720 }}>
          The discount a line is offered at, by how long it has been still. Bands are read in order and
          the last one is open-ended. Changing a percentage here changes what the register suggests —
          it never re-prices a worksheet that has already been approved.
        </div>
        <div className="tablewrap">
          <table className="items" style={{ maxWidth: 720 }}>
            <thead><tr><th style={{ width: 90 }}>From (days)</th><th style={{ width: 90 }}>To (days)</th>
              <th>Label</th><th className="num" style={{ width: 100 }}>Discount %</th>
              <th style={{ width: 34 }}></th></tr></thead>
            <tbody>{rules.buckets.map((b, i) => (
              <tr key={i}>
                <td><input value={b.from} inputMode="numeric" onChange={(e) => setBucket(i, 'from', e.target.value)} /></td>
                <td><input value={b.to == null ? '' : b.to} inputMode="numeric" placeholder="∞"
                  onChange={(e) => setBucket(i, 'to', e.target.value)} /></td>
                <td><input value={b.label} onChange={(e) => setBucket(i, 'label', e.target.value)} /></td>
                <td className="num"><input value={b.discount} inputMode="decimal" style={{ width: 70 }}
                  onChange={(e) => setBucket(i, 'discount', e.target.value)} /></td>
                <td><button className="btn" style={{ padding: '2px 7px' }} onClick={() => dropBucket(i)}>×</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <button className="btn" style={{ marginTop: 10 }} onClick={addBucket}>➕ Add a band</button>
      </Section>

      <Section id="ds-thresholds" title="When a line is called dead"
        summary="the three lines the alerts are drawn at">
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="field"><label>🟠 Approaching after (days)</label>
            <input value={rules.approaching_days} inputMode="numeric" style={{ width: 120 }}
              onChange={(e) => setRules({ ...rules, approaching_days: e.target.value })} /></div>
          <div className="field"><label>🔴 Dead after (days)</label>
            <input value={rules.dead_after_days} inputMode="numeric" style={{ width: 120 }}
              onChange={(e) => setRules({ ...rules, dead_after_days: e.target.value })} /></div>
          <div className="field"><label>🔥 Critical after (days)</label>
            <input value={rules.critical_days} inputMode="numeric" style={{ width: 120 }}
              onChange={(e) => setRules({ ...rules, critical_days: e.target.value })} /></div>
        </div>
        <div style={{ marginTop: 14 }}>
          <button className="btn primary" onClick={save} disabled={busy}>
            {busy ? 'Saving…' : 'Save rules'}</button>
        </div>
      </Section>
    </>
  )
}

// ==========================================================================
//  Warehouse menu · Dashboard
//  ------------------------------------------------------------------------
//  Eleven modules laid out on one strip made the strip into the screen, and it
//  still scrolled sideways at 1600px. They are not eleven unrelated things —
//  they are one warehouse, walked through in an order (a consignment arrives,
//  an invoice is read, goods are received, stock moves, money settles). So they
//  live behind one WAREHOUSE menu, in that order, each saying what it is for.
//
//  The dashboard sits at the top of that same menu, above a rule, because it is
//  not a twelfth module — it is the way in to the eleven. It earns first place
//  by answering the question that used to be answered by opening all of them:
//  what is waiting on me. Every figure on it is a link to the screen that clears
//  it — a number nobody can act on is decoration.
// ==========================================================================

// ==========================================================================
//  Item Locator — one scanned tag, the whole account of an item
//  ------------------------------------------------------------------------
//  Somebody is holding a garment and wants to know what it is. That question is
//  never only "what is it": it is followed, every time, by where it came from,
//  where it is meant to be, where it has gone and what it cost — and those live
//  on four different screens, each of which has to be found first.
//
//  Every tag the warehouse prints is accepted, because the person holding one
//  does not know which kind it is. A product QR, the per-piece code off a single
//  garment, a carton label, a printed barcode, or a SKU typed in from a
//  scribbled note all go into the same box.
// ==========================================================================

// A block of labelled figures. Rows whose value is null are dropped rather than
// printed as dashes: eleven attributes with three filled in is a card about three
// attributes, and eight empty lines between them is eight lines of nothing to
// read past. A row that must show even when empty passes a value of ''.
function KV({ rows }) {
  const shown = rows.filter((r) => r && r[1] !== null && r[1] !== undefined)
  if (!shown.length) return <div className="empty" style={{ margin: '4px 0', fontSize: 13 }}>Nothing recorded.</div>
  return (
    <div className="kv">
      {shown.map(([k, v]) => (
        <React.Fragment key={k}>
          <div className="k">{k}</div>
          <div>{v === '' ? '—' : v}</div>
        </React.Fragment>
      ))}
    </div>
  )
}

// Print the sticker from here.
//
// The commonest reason anybody is on this screen holding a garment is that its
// tag has come off or cannot be read. Sending them to QR / Label Printing to
// find the item a second time — by the code they have just established they
// cannot read — is the one thing this screen exists to avoid. So the same sheet
// the printing screen opens is opened from here, against the item already on
// the page.
//
// Three sheets, because there are three questions: how many stickers of this
// DESIGN, every remaining PIECE of it, and the one piece in your hand. The last
// is only offered when a piece code was what was scanned, because only then is
// there a particular garment to mean.
function LocatorPrint({ res, toast }) {
  const p = res.product
  const [templates, setTemplates] = useState([])
  const [tpl, setTpl] = useState(0)
  const [copies, setCopies] = useState('1')
  // Active ones only, and the default pre-picked — the same shape the printing
  // screen loads, so the two offer the same list rather than two opinions of it.
  // A failure is silent here: the print endpoint falls back to the default
  // template on its own, so a locator that could not reach the designer can
  // still print.
  useEffect(() => {
    api.labelTemplates().then((ts) => {
      const live = (ts || []).filter((t) => t.active)
      setTemplates(live)
      const def = live.find((t) => t.is_default) || live[0]
      if (def) setTpl(def.id)
    }).catch(() => {})
  }, [])

  const ok = res.printing?.can_print !== false
  const why = res.printing?.why
  const open = (url) => window.open(url, '_blank')
  const guard = () => {
    if (ok) return true
    toast(why || 'Labels cannot be printed for this item', 'err')
    return false
  }
  const n = Math.max(1, Math.min(999, Math.round(+copies || 1)))
  const pieces = res.unit_counts?.in_stock || 0

  return (
    <div className="section locprint">
      <h4>QR &amp; labels</h4>
      <div className="locprint-row">
        <img className="locprint-qr" alt="QR code"
          src={api.qrSvgUrl(p.product_id, 4)}
          title={p.qr_payload || p.sku} />
        <div className="locprint-acts">
          <div className="field" style={{ width: 200 }}>
            <label>Template</label>
            <select value={tpl} onChange={(e) => setTpl(+e.target.value)}
              title="Laid out in Label Designer. Without a choice the default is used.">
              {!templates.length && <option value={0}>Default template</option>}
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}{t.is_default ? ' · default' : ''}</option>
              ))}
            </select>
          </div>
          <div className="field" style={{ width: 92 }}>
            <label>Copies</label>
            <input value={copies} inputMode="numeric"
              onChange={(e) => setCopies(e.target.value)}
              title="How many stickers of this design" />
          </div>
          <button className="btn primary" disabled={!ok}
            title={ok ? `Open a sheet of ${n} label(s) for ${p.sku}` : why}
            onClick={() => guard() && open(api.labelPrintUrl(tpl, [{ id: p.product_id, qty: n }]))}>
            🖨 Print {n} label{n === 1 ? '' : 's'}
          </button>
          {/* every remaining piece, each carrying its OWN code — not n copies of
              one sticker. A garment's tag is unique to that garment. */}
          {pieces > 0 && (
            <button className="btn" disabled={!ok}
              title={ok ? `One label per piece — ${pieces} in stock, each with its own code` : why}
              onClick={() => guard() && open(api.labelPrintUrl(tpl, [], { unitProducts: [p.product_id] }))}>
              🏷 All {pieces} piece label{pieces === 1 ? '' : 's'}
            </button>
          )}
          {res.unit?.id && (
            <button className="btn" disabled={!ok}
              title={ok ? `Reprint the tag for piece ${res.unit.code}` : why}
              onClick={() => guard() && open(api.labelPrintUrl(tpl, [], { units: [res.unit.id] }))}>
              ⧉ This piece only
            </button>
          )}
          <a className="btn" href={api.labelUrl(p.product_id)} target="_blank" rel="noreferrer"
            title="The plain label this product prints without a template">Plain label</a>
        </div>
      </div>
      <div className={'small' + (ok ? '' : ' printblocked')}>
        {ok
          ? <>Opens in a new tab, ready for the printer. Piece labels count as printed,
              so Inventory offers a reprint afterwards rather than pretending they were not.</>
          : <>⚠ {why}</>}
      </div>
    </div>
  )
}

// How long it has been standing. The number of days is the single most useful
// thing anyone can say about a garment nobody has sold, and the transport
// register already carries the holding period it was bought against — so this
// can say it is LATE, which is the part that needs acting on.
function AgeChip({ age }) {
  if (!age) return null
  const late = age.overdue_by > 0
  return (
    <span className={'agechip' + (late ? ' late' : '')}
      title={`Received ${fmtDate(age.received_on)}`
        + (age.holding_days ? ` · bought against a ${age.holding_days}-day holding period` : '')}>
      {age.days} day{age.days === 1 ? '' : 's'}{late ? ` · ${age.overdue_by} over` : ''}
    </span>
  )
}

function LocatorRows({ title, rows, cols, empty }) {
  if (!rows || !rows.length) return (
    <div className="section"><h4>{title}</h4>
      <div className="empty" style={{ margin: '6px 0', fontSize: 13 }}>{empty}</div></div>
  )
  return (
    <div className="section">
      <h4>{title} <span className="panelsum">{rows.length}</span></h4>
      <div className="tablewrap">
        <table className="items">
          <thead><tr>{cols.map(([, h, num]) =>
            <th key={h} className={num ? 'num' : undefined}>{h}</th>)}</tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}>{cols.map(([k, h, num, fmt]) => (
              <td key={h} className={num ? 'num' : undefined}>
                {fmt ? fmt(r[k], r) : (r[k] ?? '—')}</td>
            ))}</tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  )
}

function ItemLocator({ toast }) {
  const [code, setCode] = useState('')
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const box = useRef(null)

  // The box holds focus because a scanner IS a keyboard: it types the code and
  // presses Enter. A screen that has to be clicked first turns every scan into a
  // scan plus a click, which is the whole saving gone.
  useEffect(() => { box.current?.focus() }, [])

  const look = async () => {
    const c = code.trim()
    if (!c) return
    setBusy(true); setErr('')
    try {
      const r = await api.locateItem(c)
      setRes(r); setCode('')
    } catch (e) {
      setRes(null)
      setErr(e.detail || 'Could not look that up')
    }
    setBusy(false)
    box.current?.focus()
  }

  const p = res?.product
  const con = res?.consignment
  const pr = res?.pricing
  const ws = res?.warehouse_stock
  //: the newest receipt — what the consignment, the age and the money hang off
  const first = res?.receipts?.[0]
  const money2 = (v) => (v == null ? '—' : '₹ ' + money(v))
  //: every date on this screen, in the house format — an ISO timestamp off a
  //  movement row and a plain date off an invoice both read the same way
  const day = (v) => fmtDate(v)

  return (
    <div className="screen scrolls">
      <div className="pagehead"><h2>Item Locator</h2></div>

      {/* this screen had no gutter at all: the header band was padded and every
          panel under it ran flush to the window edge, so the title started 22px
          in and the scan box started at 0 */}
      <div className="screenbody">
      <div className="section" style={{ maxWidth: 760 }}>
        <div className="field">
          <label>Scan a tag, or type a code</label>
          <div style={{ display: 'flex', gap: 8 }}>
            <input ref={box} value={code} style={{ flex: 1, fontSize: 15 }}
              placeholder="Product QR, piece code, carton label, barcode or SKU"
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') look() }} />
            <button className="btn primary" disabled={busy || !code.trim()}
              onClick={look}>{busy ? 'Looking…' : 'Go'}</button>
          </div>
        </div>
        {err && <div className="empty" style={{ margin: '10px 0' }}>{err}</div>}
      </div>

      {res?.kind === 'bundle' && (
        <div className="section">
          <h4>Carton {res.bundle?.code}</h4>
          <div className="small" style={{ lineHeight: 1.8 }}>
            This is a CARTON label, not a garment — it names a box, so the answer
            is about the box.<br />
            Location <b>{res.bundle?.location || 'not put away yet'}</b> ·
            status <b>{res.bundle?.status || '—'}</b> ·
            GRN <b>{res.bundle?.grn_no || '—'}</b> ·
            invoice <b>{res.bundle?.invoice_number || '—'}</b> ·
            <b> {res.bundle?.qty ?? '—'}</b> inside
          </div>
        </div>
      )}

      {res?.kind === 'product' && (
        <>
          <div className="section">
            <h4>{p?.description || p?.name || 'Item'}
              <span className="panelsum">{p?.sku}</span></h4>
            <div className="small" style={{ marginBottom: 2 }}>
              {[p?.name !== p?.description ? p?.name : null, p?.variant,
                p?.category].filter(Boolean).join(' · ') || 'No attributes recorded yet'}
            </div>
            {res.unit && (
              <div className="small" style={{ marginTop: 6 }}>
                Scanned ONE piece: <b className="mono">{res.unit.code}</b>
                {' '}(#{res.unit.seq}) · {res.unit.status} — the difference between
                {' '}this design and this exact garment.
              </div>
            )}
          </div>

          {/* Four blocks that used to be four screens. Somebody holding the
              garment asks all of these in one breath — what is it, where did it
              come from, what did it cost, and how much of it is left — so they
              are answered side by side rather than one tab at a time. */}
          <div className="locgrid">
            <div className="section">
              <h4>Consignment &amp; invoice</h4>
              <KV rows={[
                ['Barcode', <span className="mono">{p?.supplier_barcode || p?.barcode || p?.sku}</span>],
                ['LR entry no / stock age',
                  (con?.lr_entry_no || res.age) ? (
                    <>{con?.lr_entry_no || '—'} <AgeChip age={res.age} /></>
                  ) : null],
                ['LR no / date', con ? `${con.lr_no || '—'} / ${fmtDate(con.lr_date)}` : null],
                ['Mode / transport', con
                  ? [con.mode, con.transport].filter(Boolean).join(' · ') || null : null],
                ['Supplier', con?.supplier_name || first?.supplier || p?.supplier || ''],
                ['Agent', con?.agent
                  ? con.agent + (con.agent_commission ? ` · ${con.agent_commission}%` : '') : null],
                ['GRN', first?.grn_no || null],
                ['Invoice no', first?.invoice_number || con?.inv_no || ''],
                ['Invoice date', day(first?.invoice_date || con?.inv_date)],
                ['Entry date', con?.lr_entry_date ? fmtDate(con.lr_entry_date) : null],
                ['Received on', res.age?.received_on ? fmtDate(res.age.received_on) : null],
                ['Consignment', con
                  ? [con.qty ? `${con.qty} pcs` : null, con.amount ? money2(con.amount) : null,
                     con.boxes ? `${con.boxes} box(es)` : null].filter(Boolean).join(' · ') || null
                  : null],
                ['Purchase manager', con?.purchase_manager || null],
                ['Onward branch', con?.auto_transfer_location || null],
                ['Freight', con?.paid_topay || null],
              ]} />
            </div>

            <div className="section">
              <h4>Cost &amp; margin</h4>
              <KV rows={[
                ['Base / cost price', pr?.cost ? money2(pr.cost) : ''],
                ['Last purchase rate',
                  pr?.last_rate && pr.last_rate !== pr.cost ? money2(pr.last_rate) : null],
                ['Net cost (incl. purchase tax)', pr?.net_cost
                  ? <>{money2(pr.net_cost)}{pr.purchase_tax_pct
                      ? <span className="small"> &nbsp;[{money2(pr.net_cost - pr.cost)} @ {pr.purchase_tax_pct}%]</span>
                      : null}</>
                  : null],
                ['MRP', pr?.mrp ? money2(pr.mrp) : ''],
                ['Selling price', pr?.sale_price
                  ? <>{money2(pr.sale_price)}{pr.discount
                      ? <span className="small"> &nbsp;[{money2(pr.mrp)} − {money2(pr.discount)}]</span>
                      : null}</>
                  : ''],
                ['Margin', pr?.margin != null
                  ? <>{money2(pr.margin)} <span className="small">&nbsp;{pr.margin_pct}% of the sell price</span></>
                  : null],
                ['Net margin', pr?.net_margin != null
                  ? <>{money2(pr.net_margin)} <span className="small">&nbsp;{pr.net_margin_pct}% — after the purchase tax</span></>
                  : null],
                ['Mark-up on cost', pr?.markup_pct != null
                  ? <span title="The same gap read from the other end: margin over COST, not over the sell price.">{pr.markup_pct}%</span>
                  : null],
              ]} />
            </div>

            <div className="section">
              <h4>Product attributes</h4>
              <KV rows={[
                ['Brand', p?.brand || ''], ['Colour', p?.color || ''],
                ['Pattern', p?.pattern], ['Style', p?.style],
                ['Material', p?.material], ['Type', p?.product_type],
                ['Sleeve', p?.sleeve], ['Fit', p?.fit],
                ['Design', p?.design_no || ''], ['Size', p?.size || ''],
                ['Category', p?.category], ['HSN', p?.hsn],
                ['UOM', p?.uom], ['SKU', <span className="mono">{p?.sku}</span>],
              ]} />
            </div>

            <div className="section">
              <h4>Warehouse stock</h4>
              {/* The stock figure is one number and it is the END of a story.
                  These are the story, and they add up to it. */}
              <KV rows={[
                ['Purchase qty', ws?.purchased ?? ''],
                ['Transferred out', ws?.transferred ?? ''],
                ['Returned to supplier', ws?.returned ?? ''],
                ['Damaged at the dock', ws?.damaged || null],
                ['Short on the bill', ws?.short || null],
                ['Excess over the bill', ws?.excess || null],
                ['Journal (physical audit)', ws?.adjusted || null],
                ['Reversed (unposted GRN)', ws?.reversed || null],
                ['Stock', <b>{ws?.stock ?? p?.stock_qty ?? 0}</b>],
                ['Stock value', p?.stock_value == null ? null : money2(p.stock_value)],
                ['Piece codes', `${res.unit_counts?.in_stock ?? 0} in stock of ${res.unit_counts?.total ?? 0} printed`],
              ]} />
            </div>
          </div>

          <LocatorPrint res={res} toast={toast} />

          <LocatorRows title="Retail stock — where the pieces are now" rows={res.locations}
            empty="Nothing has been dispatched, so every piece is still in this warehouse."
            cols={[['location', 'Location'], ['sent', 'Sent', true],
                   ['accepted', 'Accepted', true],
                   ['short_by', 'Short', true, (v) => (v ? <b style={{ color: 'var(--warn)' }}>{v}</b> : '—')],
                   ['sold', 'Sold', true], ['price', 'Price', true, money2],
                   ['discount', 'Discount', true, money2],
                   ['selling_price', 'Selling price', true, money2]]} />

          <LocatorRows title="Transfers" rows={res.transfers}
            empty="Never transferred out of the warehouse."
            cols={[['code', 'Code'], ['from', 'From'], ['to', 'To'],
                   ['packed_on', 'Packed on', false, day], ['packed_qty', 'Packed', true],
                   ['received_on', 'Received on', false, day],
                   ['received_qty', 'Received', true],
                   ['short_by', 'Short', true, (v) => (v ? <b style={{ color: 'var(--warn)' }}>{v}</b> : '—')],
                   ['status', 'Status']]} />

          {/* Whether it SOLD or is merely gone. From this side of the wall those
              two look identical — stock zero, nothing on the shelf — so the till
              is asked, read-only, and says so when it is not there to ask. */}
          {res.sales?.available ? (
            <LocatorRows
              title={`Sold at the till${res.sales.bills
                ? ` — ${res.sales.qty} on ${res.sales.bills} bill(s), ${money2(res.sales.amount)}` : ''}`}
              rows={res.sales.rows}
              empty="Never sold. It has not been through the till."
              cols={[['bill_no', 'Bill'], ['date', 'Date', false, day],
                     ['kind', 'Kind', false, (v) => (v === 'return' ? 'RETURN' : 'sale')],
                     ['customer', 'Customer'], ['qty', 'Qty', true],
                     ['rate', 'Rate', true, money2], ['tax', 'Tax', true, money2],
                     ['amount', 'Amount', true, money2]]} />
          ) : (
            <div className="section"><h4>Sold at the till</h4>
              <div className="empty" style={{ margin: '6px 0', fontSize: 13 }}>
                The shop's own database is not beside this warehouse, so nothing is
                known here about what has sold.
              </div>
            </div>
          )}

          <LocatorRows title="Where it came from" rows={res.receipts}
            empty="No receipt recorded — this item has not been booked in against a GRN."
            cols={[['grn_no', 'GRN'], ['supplier', 'Supplier'],
                   ['invoice_number', 'Invoice'], ['invoice_date', 'Inv date', false, day],
                   ['qty', 'Qty', true], ['rate', 'Rate', true, money2],
                   ['status', 'Status']]} />

          <LocatorRows title="Where it is" rows={res.cartons}
            empty="Not in any carton — either never bundled, or already opened."
            cols={[['code', 'Carton'], ['location', 'Location'],
                   ['qty', 'Qty', true], ['status', 'Status'],
                   ['grn_no', 'GRN'], ['invoice_number', 'Invoice']]} />

          <LocatorRows title="Where it went" rows={res.dispatches}
            empty="Never dispatched — it has not left this warehouse."
            cols={[['code', 'Dispatch'], ['date', 'Date', false, day],
                   ['to', 'To'], ['qty', 'Sent', true],
                   ['accepted_qty', 'Accepted', true], ['status', 'Status']]} />

          <LocatorRows title="Stock movements" rows={res.movements}
            empty="No movements recorded against this item."
            cols={[['at', 'When', false, day], ['kind', 'Kind'],
                   ['qty_delta', 'Change', true],
                   ['balance_after', 'Balance', true], ['note', 'Note']]} />
        </>
      )}
      </div>
    </div>
  )
}

const DASHBOARD = { key: 'dashboard', icon: '🏠', label: 'Dashboard',
  blurb: 'What is waiting on someone, across every module' }

// ==========================================================================
//  Users & Access — the super admin's screen
//  ------------------------------------------------------------------------
//  Everything on it is refused by the server for anyone else, so this is the
//  presentation of a rule rather than the rule itself. What it adds is the part
//  a 403 cannot: it says what each role means beside the control that sets it,
//  because "admin" and "user" are only obvious to whoever chose the words.
//
//  Deliberately not a modal. Adding people, resetting a forgotten password and
//  checking who has never signed in are the same sitting, and a dialog that has
//  to be closed to see the list turns that into three.
// ==========================================================================

const ROLE_HELP = {
  user: 'The floor — LR, invoice entry, GRN, inventory, label printing, dispatch and receipt. On the phone app too.',
  admin: 'All of the floor, plus the setup behind it — masters, suppliers, label design — and reports, payments, returns and dead stock.',
  superadmin: 'Everything, plus this screen and the server settings (the vision key and model).',
}

// ==========================================================================
//  Access editor — what one account may do, screen by screen
//  ------------------------------------------------------------------------
//  Three roles answer "how much of this app is yours" in three steps, and three
//  steps is not enough for a warehouse. A receiving clerk needs the GRN screen
//  and must not post a return; a stock auditor needs to read every screen and
//  change none of them. Both of those live inside one role, so the only way to
//  express either was to hand over the whole role.
//
//  Two things about this grid are worth knowing before ticking anything, and
//  both are said on the screen rather than only here:
//
//    * The ROLE IS STILL THE CEILING. Ticking Reports for a floor user does not
//      open Reports — the server still refuses it. A tick can only ever take
//      access away, never add it, which is what makes a mis-tick harmless.
//    * NO TICKS AT ALL means the role decides on its own, exactly as it did
//      before this existed. An empty grid is not "denied everything"; it is
//      "unrestricted", and it is what every existing account starts as.
// ==========================================================================
function AccessEditor({ user, catalog, onSave, onClose, toast }) {
  const [map, setMap] = useState(() => ({ ...(user.permissions?.screens || {}) }))
  const [data, setData] = useState(() => [...(user.permissions?.data || [])])
  const [busy, setBusy] = useState(false)
  // The grid is normally drawn from the catalog that rides along with the user
  // list. When that is missing the screen must not open as an empty box: an
  // empty grid already MEANS something here — unrestricted — so a grid with no
  // rows because the server is old would read as a grid with nothing ticked.
  // Fetch it directly, and if that 404s, say what is actually wrong.
  const [own, setOwn] = useState(null)
  const [stale, setStale] = useState(false)
  useEffect(() => {
    if ((catalog.screens || []).length) return
    api.permissionCatalog().then(setOwn)
      .catch((e) => setStale(e.status === 404 || e.status === 405))
  }, [catalog])
  const cat = (catalog.screens || []).length ? catalog : (own || catalog)
  const acts = cat.actions || []
  const screens = cat.screens || []
  const restricted = Object.keys(map).length > 0

  const has = (k, a) => (map[k] || []).includes(a)
  const toggle = (k, a) => setMap((m) => {
    const cur = new Set(m[k] || [])
    if (cur.has(a)) cur.delete(a); else { cur.add(a); cur.add('view') }
    const kept = acts.map((x) => x.key).filter((x) => cur.has(x))
    const next = { ...m }
    if (kept.length) next[k] = kept; else delete next[k]
    return next
  })
  // A whole column at once. Sixteen rows of five boxes is eighty clicks to say
  // "read everything", which is a thing nobody does twice.
  const column = (a) => {
    const every = screens.every((sc) => has(sc.key, a))
    setMap((m) => {
      const next = { ...m }
      screens.forEach((sc) => {
        const cur = new Set(next[sc.key] || [])
        if (every) cur.delete(a); else { cur.add(a); cur.add('view') }
        const kept = acts.map((x) => x.key).filter((x) => cur.has(x))
        if (kept.length) next[sc.key] = kept; else delete next[sc.key]
      })
      return next
    })
  }
  const row = (k) => setMap((m) => {
    const every = acts.every((a) => (m[k] || []).includes(a.key))
    const next = { ...m }
    if (every) delete next[k]; else next[k] = acts.map((a) => a.key)
    return next
  })

  const submit = async () => {
    setBusy(true)
    try {
      await api.setUserPermissions(user.id, { screens: map, data })
      toast(Object.keys(map).length
        ? `✓ ${user.username} is restricted to ${Object.keys(map).length} screen(s)`
        : `✓ ${user.username} is back to their role — nothing restricted`, 'ok')
      await onSave(); onClose()
    } catch (e) { toast(e.detail || 'Could not save that', 'err') }
    setBusy(false)
  }

  const groups = []
  screens.forEach((sc) => {
    const g = groups.find((x) => x.name === sc.group)
    if (g) g.rows.push(sc); else groups.push({ name: sc.group, rows: [sc] })
  })

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal accessmodal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>Access — {user.username}</b>
          <span className={'badge role-' + user.role}>{user.role_label}</span>
          <button className="modal-x" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          {!screens.length && (
            <div className="accessnote on">
              {stale
                ? <>This server was started before per-screen access existed.
                    <b> Restart the ESSA server and reload this page</b> — the grid
                    is drawn from a list only the server can supply, and until it
                    can there is nothing here to tick.</>
                : <>Loading the screen list…</>}
            </div>
          )}
          <div className={'accessnote' + (restricted ? ' on' : '')}>
            {restricted
              ? <>Restricted to what is ticked below. Their <b>{user.role_label}</b> role
                  is still the ceiling — a tick on a screen that role cannot reach
                  does nothing.</>
              : <>Nothing is ticked, so this account is <b>unrestricted</b> and its
                  role decides on its own — exactly as before. Tick anything and it
                  becomes restricted to what is ticked.</>}
          </div>
          <div className="tablewrap">
            <table className="items accessgrid">
              <thead><tr>
                <th>Screen</th>
                {acts.map((a) => (
                  <th key={a.key} title={a.why}>
                    <button className="link" onClick={() => column(a.key)}
                      title={`${a.label} on every screen`}>{a.label}</button>
                  </th>
                ))}
              </tr></thead>
              <tbody>
                {groups.map((g) => (
                  <React.Fragment key={g.name}>
                    <tr className="accessgroup">
                      <td colSpan={acts.length + 1}>{g.name}</td>
                    </tr>
                    {g.rows.map((sc) => {
                      // A screen this role cannot reach at all is shown, not
                      // hidden — otherwise the grid silently changes shape per
                      // role and nobody can tell a screen that is missing from a
                      // screen that is off — but it says so, and its boxes are
                      // dead, because ticking one would promise access the server
                      // is going to refuse.
                      const over = sc.min && !atLeast(user.role, sc.min)
                      return (
                        <tr key={sc.key} className={over ? 'overrole' : undefined}>
                          <td>
                            <button className="link" disabled={over}
                              onClick={() => row(sc.key)}
                              title={over ? '' : 'Everything on this screen'}>{sc.label}</button>
                            {over && <span className="small"> · needs {ROLE_LABEL[sc.min]}</span>}
                          </td>
                          {acts.map((a) => (
                            <td key={a.key} className="num">
                              <input type="checkbox" disabled={over}
                                checked={has(sc.key, a.key)}
                                onChange={() => toggle(sc.key, a.key)} />
                            </td>
                          ))}
                        </tr>
                      )
                    })}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>

          <h4 style={{ marginTop: 18 }}>Figures to withhold</h4>
          <div className="small" style={{ marginBottom: 8 }}>
            Ticked here, the figure is stripped by the server before it reaches
            this account — on every screen, not hidden by the one showing it.
          </div>
          <div className="datagrid">
            {(catalog.data_permissions || []).map((d) => (
              <label className="mcheck" key={d.key} title={d.why}>
                <input type="checkbox" checked={data.includes(d.key)}
                  onChange={() => setData((v) => (v.includes(d.key)
                    ? v.filter((x) => x !== d.key) : [...v, d.key]))} />
                {d.label}
              </label>
            ))}
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={() => { setMap({}); setData([]) }}
            title="Back to role-only — this account stops being restricted">Clear all</button>
          <span style={{ flex: 1 }} />
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy} onClick={submit}>
            {busy ? 'Saving…' : 'Save access'}</button>
        </div>
      </div>
    </div>
  )
}

function Users({ toast, me }) {
  const [rows, setRows] = useState([])
  const [roles, setRoles] = useState([])
  const [catalog, setCatalog] = useState({ actions: [], screens: [], data_permissions: [] })
  const [access, setAccess] = useState(null)     // the account whose grid is open
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const blank = { username: '', password: '', role: 'user', full_name: '' }
  const [form, setForm] = useState(blank)

  const load = useCallback(() => {
    setBusy(true)
    return api.listUsers()
      .then((r) => {
        setRows(r.users || []); setRoles(r.roles || []); setErr('')
        if (r.catalog) setCatalog(r.catalog)
      })
      .catch((e) => setErr(e.detail || 'Could not load the user list'))
      .finally(() => setBusy(false))
  }, [])
  useEffect(() => { load() }, [load])

  const add = async (e) => {
    e.preventDefault()
    if (!form.username.trim() || !form.password) { toast('Username and password are both needed', 'err'); return }
    try {
      await api.createUser({ ...form, username: form.username.trim() })
      setForm(blank); load(); toast(`✓ ${form.username.trim()} can now sign in`, 'ok')
    } catch (e2) { toast(e2.detail || 'Could not create that account', 'err') }
  }

  const patch = async (u, body, note) => {
    try { await api.updateUser(u.id, body); await load(); if (note) toast(note, 'ok') }
    catch (e) { toast(e.detail || 'Could not save that change', 'err') }
  }

  // The one field on an account that is neither identity nor a permission, and
  // the one thing that was previously impossible to correct: somebody added as
  // "sharu" with no full name, or spelt wrong on the day. The username is left
  // alone deliberately — it is what tokens, ledger notes and "recorded by" lines
  // already carry, and renaming it would orphan every one of them.
  const rename = async (u) => {
    const name = window.prompt(`Full name for ${u.username}:`, u.full_name || '')
    if (name === null) return
    patch(u, { full_name: name.trim() },
      `✓ ${u.username} is ${name.trim() || 'unnamed'}`)
  }

  const reset = async (u) => {
    // window.prompt rather than a field on the row: a reset is rare, and a
    // password box sitting open on every row is a shoulder-surfing invitation
    // for the ninety-nine percent of the time nobody is resetting anything.
    const pw = window.prompt(`New password for ${u.username}.\n\nThey are signed out of every device as soon as this is set.`)
    if (!pw) return
    try { await api.resetUserPassword(u.id, pw); toast(`✓ Password reset for ${u.username}`, 'ok') }
    catch (e) { toast(e.detail || 'Could not reset it', 'err') }
  }

  const remove = async (u) => {
    if (!window.confirm(`Delete ${u.username}?\n\nDeactivating instead keeps the record of what they did and can be undone. Deleting cannot.`)) return
    try { await api.deleteUser(u.id); await load(); toast(`✓ ${u.username} deleted`, 'ok') }
    catch (e) { toast(e.detail || 'Could not delete that account', 'err') }
  }

  const isMe = (u) => u.username === me
  const when = (iso) => fmtDate(iso)

  return (
    <div className="screen scrolls">
      {/* this screen used to open with its own heading inside its own card, at
          its own 18px indent — so it was the one module whose title did not
          start where every other module's title starts. It wears the same band
          as the rest now, and the card below it holds the page's gutter. */}
      <div className="pagehead">
        <h2>👤 Users &amp; Access</h2>
        <div className="pagesub small">
          Who can sign in, and what each account is allowed to open
        </div>
      </div>
      <div className="screenbody">
      {/* the app's own panel, not a hand-rolled one — this card used to carry a
          12px radius and 20px of padding against the 14/16 every other panel in
          the app uses, so it read as a slightly different object */}
      <div className="section" style={{ maxWidth: 1080 }}>

        <div className="small" style={{ color: 'var(--text-2)', marginBottom: 14, lineHeight: 1.7 }}>
          Everyone signs in with their own account, on the desktop app and on the phone —
          the same account works on both. Three levels:
          <div style={{ marginTop: 8 }}>{Object.keys(ROLE_HELP).map((r) => (
            <div key={r} style={{ display: 'flex', gap: 10, marginTop: 5 }}>
              <span className={'badge role-' + r} style={{ flex: '0 0 92px' }}>{ROLE_LABEL[r]}</span>
              <span style={{ flex: 1 }}>{ROLE_HELP[r]}</span>
            </div>
          ))}</div>
        </div>

        {err && <div className="empty" style={{ margin: '10px 0' }}>{err}</div>}
        {busy && !rows.length && <div className="empty" style={{ margin: '10px 0' }}>Loading…</div>}

        {rows.length > 0 && (
          <div className="tablewrap">
            <table className="items">
              <thead><tr>
                <th>Username</th><th>Name</th><th style={{ width: 150 }}>Role</th>
                <th style={{ width: 100 }}>Last signed in</th><th style={{ width: 90 }}>Added</th>
                <th style={{ width: 200 }}></th>
              </tr></thead>
              <tbody>{rows.map((u) => (
                <tr key={u.id} style={u.active ? undefined : { opacity: 0.55 }}>
                  <td><b>{u.username}</b>{isMe(u) && <span className="small" style={{ color: 'var(--text-2)' }}> · you</span>}</td>
                  <td className="small">{u.full_name || '—'}</td>
                  <td>
                    {/* Your own row shows the role but cannot change it — the
                        account you are signed in as is the one holding the door
                        open, and the server refuses this too. */}
                    {isMe(u) ? <span className={'badge role-' + u.role}>{u.role_label}</span> : (
                      <select className="sel" value={u.role} title={ROLE_HELP[u.role]}
                        onChange={(e) => patch(u, { role: e.target.value }, `✓ ${u.username} is now ${ROLE_LABEL[e.target.value]}`)}>
                        {roles.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                      </select>
                    )}
                  </td>
                  <td className="small">{u.last_login_at ? when(u.last_login_at) : <span title="This account has never been used">never</span>}</td>
                  <td className="small">{when(u.created_at)}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn" style={{ padding: '2px 8px', marginRight: 5 }}
                      onClick={() => rename(u)}
                      title="Change the name shown against this account">Edit name</button>
                    {!isMe(u) && (
                      <button className="btn" style={{ padding: '2px 8px', marginRight: 5 }}
                        onClick={() => setAccess(u)}
                        title="Choose screen by screen what this account may view, create, modify, delete and print">
                        Access{u.permissions?.screens
                          ? ` · ${Object.keys(u.permissions.screens).length}` : ''}</button>
                    )}
                    <button className="btn" style={{ padding: '2px 8px', marginRight: 5 }}
                      onClick={() => reset(u)} title="Set a new password and sign them out everywhere">Reset password</button>
                    {!isMe(u) && <>
                      <button className="btn" style={{ padding: '2px 8px', marginRight: 5 }}
                        onClick={() => patch(u, { active: !u.active }, u.active ? `${u.username} can no longer sign in` : `✓ ${u.username} can sign in again`)}
                        title={u.active ? 'Stop this account signing in — reversible' : 'Let this account sign in again'}>
                        {u.active ? 'Deactivate' : 'Reactivate'}</button>
                      <button className="btn" style={{ padding: '2px 8px' }} onClick={() => remove(u)} title="Delete permanently">×</button>
                    </>}
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}

        <form onSubmit={add} style={{ display: 'flex', gap: 8, alignItems: 'flex-end', marginTop: 16, flexWrap: 'wrap' }}>
          <div className="field" style={{ minWidth: 150 }}><label>Username</label>
            <input value={form.username} autoComplete="off"
              onChange={(e) => setForm({ ...form, username: e.target.value })} placeholder="e.g. sharu" /></div>
          <div className="field" style={{ minWidth: 170 }}><label>Full name</label>
            <input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              placeholder="e.g. Sharu Kumar" /></div>
          <div className="field" style={{ minWidth: 160 }}><label>Password</label>
            <input type="password" value={form.password} autoComplete="new-password"
              onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="at least 6 characters" /></div>
          <div className="field" style={{ minWidth: 140 }}><label>Role</label>
            <select className="sel" value={form.role} title={ROLE_HELP[form.role]}
              onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {(roles.length ? roles : [{ value: 'user', label: 'User' }]).map((r) =>
                <option key={r.value} value={r.value}>{r.label}</option>)}
            </select></div>
          <button className="btn primary" type="submit">Add user</button>
        </form>
        <div className="small" style={{ color: 'var(--text-2)', marginTop: 8 }}>{ROLE_HELP[form.role]}</div>
      </div>
      </div>

      {access && (
        <AccessEditor user={access} catalog={catalog} toast={toast}
          onSave={load} onClose={() => setAccess(null)} />
      )}
    </div>
  )
}

// The one account action everybody has, whatever their role.
function ChangePassword({ onClose, toast }) {
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)

  const save = async (e) => {
    e.preventDefault()
    if (next !== again) { toast('The two new passwords do not match', 'err'); return }
    setBusy(true)
    try {
      const r = await api.changePassword(cur, next)
      // The change rotated the signing seed, so the token this tab is holding
      // is already dead — swap in the fresh one rather than dropping the user
      // at the login screen for having done as they were asked.
      if (r.token) session.set(r.token)
      toast('✓ Password changed', 'ok'); onClose()
    } catch (e2) { toast(e2.detail || 'Could not change it', 'err'); setBusy(false) }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(42,35,32,.45)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div style={{ width: 380, background: 'var(--panel)', border: '1px solid var(--line)',
        borderRadius: 12, padding: 24 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>Change password</h2>
          <div style={{ flex: 1 }} />
          <button className="btn" style={{ padding: '2px 9px' }} onClick={onClose}>×</button>
        </div>
        <form onSubmit={save}>
          <div className="field"><label>Current password</label>
            <input type="password" value={cur} autoComplete="current-password"
              onChange={(e) => setCur(e.target.value)} autoFocus /></div>
          <div className="field"><label>New password</label>
            <input type="password" value={next} autoComplete="new-password"
              onChange={(e) => setNext(e.target.value)} placeholder="at least 6 characters" /></div>
          <div className="field"><label>New password again</label>
            <input type="password" value={again} autoComplete="new-password"
              onChange={(e) => setAgain(e.target.value)} /></div>
          <div className="small" style={{ color: 'var(--text-2)', margin: '10px 0' }}>
            Your other devices — including the phone app — are signed out when this is saved.
          </div>
          <button className="btn primary" type="submit" disabled={busy} style={{ width: '100%' }}>
            {busy ? 'Saving…' : 'Change password'}</button>
        </form>
      </div>
    </div>
  )
}

// ==========================================================================
//  Roles
//  ------------------------------------------------------------------------
//  Three ranked roles, matching backend/app/services/users.py. Everything is
//  compared by RANK rather than by equality — `rank >= admin` rather than
//  `role === 'admin'` — because the super admin is an admin who can also do
//  more, and equality tests quietly lock the top role out of the middle one's
//  screens. That bug is invisible until the one person who has the top role
//  tries to use the app.
//
//  This gating is a courtesy, not the enforcement. A screen hidden here is
//  still refused by the server (see backend/app/security.py) if it is reached
//  another way — the point of hiding it is that the floor is not shown twelve
//  buttons that answer "not for you".
// ==========================================================================
const ROLE_RANK = { user: 1, admin: 2, superadmin: 3 }
const ROLE_LABEL = { user: 'User', admin: 'Admin', superadmin: 'Super Admin' }
const rank = (role) => ROLE_RANK[role] || 0
const atLeast = (role, need) => rank(role) >= rank(need)

const MODULES = [
  { key: 'lr', icon: '🚚', label: 'LR Entry', blurb: 'The transport register — consignments as they arrive' },
  { key: 'documents', icon: '🧾', label: 'Invoice Entry', blurb: 'Read a supplier invoice and review what came off it' },
  { key: 'purchases', icon: '📋', label: 'GRN', blurb: 'Receive against an invoice — count, claim shortages, post' },
  { key: 'inventory', icon: '📦', label: 'Inventory', blurb: 'Stock on hand, labels and per-piece codes' },
  // Beside Inventory because it is a question about the same stock — not what
  // we hold, but what has stopped moving and what clearing it is worth.
  // Beside Inventory because it is a question about the same stock, asked from
  // the other end: not what we hold, but what THIS ONE is.
  { key: 'locator', icon: '🔎', label: 'Item Locator', blurb: 'Scan any tag — what it is, where it came from, where it is, where it went' },
  { key: 'deadstock', icon: '🧊', label: 'Dead Stock & Clearance', blurb: 'Stock nobody is buying, the discount ladder, and whether the clearance worked', min: 'admin' },
  // Design and printing are two entries because they are two jobs, and they are
  // also two roles: the designer is opened when a new roll of label stock is
  // bought, by whoever set the warehouse up, while the printing screen is
  // opened every time goods are put away, by whoever is putting them away.
  { key: 'labels', icon: '🏷', label: 'Label Designer', blurb: 'Lay out the sticker once — which field prints where, and how big the QR is', min: 'admin' },
  { key: 'labelprint', icon: '🖨', label: 'QR / Label Printing', blurb: 'Pick stock, pick a template, print the labels' },
  { key: 'outward', icon: '📤', label: 'Stock Outward', blurb: 'Dispatch stock to a shop or another godown' },
  { key: 'inward', icon: '📥', label: 'Stock Inward', blurb: 'Accept a dispatched transfer, line by line' },
  { key: 'returns', icon: '↩', label: 'Returns', blurb: 'Debit notes raised against a posted GRN', min: 'admin' },
  { key: 'payments', icon: '₹', label: 'Payments', blurb: 'Settle supplier bills and read the ledger', min: 'admin' },
  { key: 'reports', icon: '📊', label: 'Reports', blurb: 'Every register, filtered and exportable', min: 'admin' },
  { key: 'suppliers', icon: '🏭', label: 'Suppliers', blurb: 'Supplier master and trained invoice formats', min: 'admin' },
  { key: 'masters', icon: '⚙', label: 'Masters', blurb: 'Categories, agents, transporters and the dropdown lists', min: 'admin' },
  { key: 'users', icon: '👤', label: 'Users & Access', blurb: 'Who can sign in, and how much of this they see', min: 'superadmin' },
]

// ==========================================================================
//  POS — the retail shop, mounted at /pos
//  ------------------------------------------------------------------------
//  The shop (Taqua Silks) is a finished Flask app with its own database and
//  its own login. It is served by this same backend, at this same origin, and
//  shown here in a frame — not rewritten screen by screen into React. Same
//  origin is the load-bearing part: on a second port its login cookie would be
//  third-party inside the frame and the browser would drop it.
//
//  It sits beside Warehouse rather than inside it because it is the other half
//  of the business, not a twelfth warehouse screen: the warehouse receives
//  goods, the shop sells them. Its screens are listed here for the same reason
//  the warehouse's are — so the way in is the menu, not a tour of the frame's
//  own navigation.
// ==========================================================================

const POS_HOME = { key: 'pos:home', icon: '🏠', label: 'Shop Dashboard', path: '/',
  blurb: "Today's sales, low stock and the last few bills" }

// In the shop's own order — the same sequence as the navigation inside the
// frame, so choosing a screen here and then moving around in there does not feel
// like two different products. A sale goes left to right: build it on the floor
// or at the counter, look it up afterwards, take it back, alter it.
const POS_SCREENS = [
  { key: 'pos:floor', icon: '📱', label: 'Floor Sales', path: '/floor/',
    blurb: 'Build a sale on the phone while walking the floor' },
  { key: 'pos:counter', icon: '🧮', label: 'Billing Counter', path: '/pos/',
    blurb: 'Scan, bill and take payment at the counter' },
  { key: 'pos:inventory', icon: '📦', label: 'Shop Stock', path: '/inventory/',
    blurb: 'What is on the shop floor, with the warehouse QR on every item' },
  { key: 'pos:customers', icon: '🧍', label: 'Customers', path: '/customers/',
    blurb: 'Customer master, loyalty points and history' },
  { key: 'pos:invoices', icon: '🧾', label: 'Invoices', path: '/pos/invoices',
    blurb: 'Every bill raised, searchable and reprintable' },
  { key: 'pos:returns', icon: '↩️', label: 'Returns', path: '/returns/',
    blurb: 'Take goods back against a bill and raise a credit note' },
  { key: 'pos:alterations', icon: '✂️', label: 'Alteration', path: '/alterations/',
    blurb: 'Garments out for tailoring, and what each tailor is holding' },
  { key: 'pos:staff', icon: '👥', label: 'Staff', path: '/staff/',
    blurb: 'Attendance, roles and sales commission' },
  { key: 'pos:reports', icon: '📈', label: 'Shop Reports', path: '/reports/',
    blurb: 'Sales, tax and low-stock registers' },
]

const POS_ITEMS = [POS_HOME, null, ...POS_SCREENS]

// One frame, keyed on the path so choosing another screen from the menu loads
// it rather than leaving the frame on whatever it had drifted to.
function PosScreen({ screen, available, error }) {
  if (available === false) return (
    <div className="empty" style={{ margin: 40, maxWidth: 620 }}>
      <p><b>The POS module is not loaded.</b></p>
      <p className="small">{error || 'The server started without it.'}</p>
      <p className="small">It lives in the <b>Textile Retail Shop</b> folder beside this
        project. Install the backend requirements (<code>pip install -r requirements.txt</code>)
        and restart the server.</p>
    </div>
  )
  return (
    <div className="posframe">
      <iframe key={screen.path} src={'/pos' + screen.path} title={'POS — ' + screen.label} />
    </div>
  )
}

// The one navigation control. It says which screen is open even while closed,
// because a collapsed navigation that doesn't say where you are is how someone
// loses the screen and re-opens the menu only to find out. `items` may hold a
// null, which draws a rule — that is what separates the dashboard from the
// modules it leads into. Warehouse and POS are the same control with different
// contents; nothing about it was ever specific to the warehouse.
function NavMenu({ tab, setTab, items, icon, label, hint }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef(null)
  useEffect(() => {
    if (!open) return
    const away = (e) => { if (wrap.current && !wrap.current.contains(e.target)) setOpen(false) }
    const esc = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])

  const here = items.find((m) => m && m.key === tab)
  return (
    <div className="navmenu" ref={wrap}>
      <button className={'navmenu-btn' + (here ? ' active' : '') + (open ? ' open' : '')}
        aria-haspopup="menu" aria-expanded={open}
        title={here ? `${label} — ${here.label} is open. Click for the other screens.` : hint}
        onClick={() => setOpen((o) => !o)}>
        <span aria-hidden="true">{icon}</span> {label}
        {here && <span className="where">{here.label}</span>}
        <span className="caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="navmenu-pop" role="menu">
          {items.map((m, i) => (m ? (
            <button key={m.key} role="menuitem" title={m.blurb}
              className={'navmenu-item' + (m.key === tab ? ' on' : '')}
              onClick={() => { setTab(m.key); setOpen(false) }}>
              <span className="ico" aria-hidden="true">{m.icon}</span>
              <span className="txt">
                <span className="lbl">{m.label}</span>
              </span>
            </button>
          ) : <div key={'sep' + i} className="navmenu-sep" role="separator" />))}
        </div>
      )}
    </div>
  )
}

// ==========================================================================
//  Charts
//  ------------------------------------------------------------------------
//  Hand-drawn SVG rather than a charting library. Five chart types on one
//  screen do not justify a dependency several times the size of this entire
//  bundle, and the specs below — 2px lines, ≤24px bars with a 4px rounded
//  data-end, a 2px surface gap between touching marks, hairline solid grid —
//  are easier to hold exactly when they are written out than when they are
//  configured through someone else's theme object.
//
//  Fixed rules these all keep:
//    * The form is chosen by the data's job, not by variety. One series over
//      time is a line; two series compared is grouped columns; magnitude across
//      names is bars in ONE colour (colouring those by value would encode the
//      bar length twice and waste the only free channel); an ordered band is one
//      hue getting darker; part-to-whole with few slices is a ring.
//    * Never two y-scales on one plot. Two measures of different size are two
//      charts.
//    * Text never wears the series colour — a swatch beside the label carries
//      identity, so a pale hue is never asked to be legible as type.
//    * Every chart has a table twin. A value that only exists inside a tooltip
//      is a value someone cannot read, print or check.
// ==========================================================================

const VB = { w: 620, h: 240 }                    // viewBox; charts scale to their cell
const PAD = { t: 16, r: 16, b: 34, l: 58 }       // bottom band sized to hold the x labels
const PLOT = { w: VB.w - PAD.l - PAD.r, h: VB.h - PAD.t - PAD.b }
const VIZ = ['var(--viz-1)', 'var(--viz-2)', 'var(--viz-3)', 'var(--viz-4)', 'var(--viz-5)']
const SEQ = 'var(--viz-2)'                       // single-series / sequential default

// Axis ticks land on clean numbers — a gridline at 83,417 is a gridline nobody
// reads a value off.
const niceTicks = (max, count = 4) => {
  if (!(max > 0)) return [0, 1]
  const raw = max / count
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 2.5, 5, 10].find((m) => mag * m >= raw) * mag
  const out = []
  for (let v = 0; v <= max + step * 0.001; v += step) out.push(v)
  return out
}
const compact = (v) => {
  const n = Math.abs(v)
  if (n >= 1e7) return (v / 1e7).toFixed(n >= 1e8 ? 0 : 1) + 'Cr'
  if (n >= 1e5) return (v / 1e5).toFixed(n >= 1e6 ? 0 : 1) + 'L'
  if (n >= 1000) return (v / 1000).toFixed(n >= 1e4 ? 0 : 1) + 'k'
  return String(Math.round(v * 100) / 100)
}

// The frame every plotted chart shares: hairline solid gridlines (never dashed —
// dashing reads as "projection" when it is just a grid), clean ticks, and axis
// text in the muted ink rather than in any series colour.
function Grid({ ticks, max }) {
  return (
    <g>
      {ticks.map((t) => {
        const y = PAD.t + PLOT.h - (max ? (t / max) * PLOT.h : 0)
        return (
          <g key={t}>
            <line x1={PAD.l} x2={PAD.l + PLOT.w} y1={y} y2={y}
              stroke="var(--viz-grid)" strokeWidth="1" />
            <text x={PAD.l - 8} y={y + 3.5} textAnchor="end" className="viztick">{compact(t)}</text>
          </g>
        )
      })}
    </g>
  )
}

// x labels thin out rather than overlap — a collided axis is unreadable, and
// every value is still in the tooltip and the table.
const everyNth = (n, keep = 6) => Math.max(1, Math.ceil(n / keep))

function ChartCard({ title, note, legend, rows, columns, children }) {
  const [table, setTable] = useState(false)
  return (
    <div className="vizcard">
      {/* Title and the table switch hold the first row on their own; the legend
          takes its own line below. Sharing one row made the switch wrap under the
          legend on the cards that have one, so the control sat in a different
          place on different cards. */}
      <div className="vizhead">
        <h5>{title}</h5>
        <span className="spacer" />
        <button className="vizswap" onClick={() => setTable((t) => !t)}
          title={table ? 'Back to the chart' : 'Show these numbers as a table'}>
          {table ? 'Chart' : 'Table'}
        </button>
      </div>
      {legend && <div className="vizlegendrow">{legend}</div>}
      {note && <div className="viznote">{note}</div>}
      {/* The table twin is the same numbers, not a summary of them — so a value
          is never reachable only by hovering. */}
      {table ? (
        <div className="vizscroll">
          <table className="items">
            <thead><tr>{columns.map((c) => <th key={c} className={c === columns[0] ? '' : 'num'}>{c}</th>)}</tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={i}>{r.map((cell, j) => (
                <td key={j} className={j ? 'num mono' : ''}>{cell}</td>
              ))}</tr>
            ))}</tbody>
          </table>
        </div>
      ) : children}
    </div>
  )
}

function Legend({ items }) {
  return (
    <span className="vizlegend">
      {items.map((it) => (
        <span key={it.label} className="vizkey">
          <i style={{ background: it.color }} /> {it.label}
        </span>
      ))}
    </span>
  )
}

// Tooltip in HTML over the SVG rather than SVG text: it needs a background,
// padding and wrapping, all of which are free here and fiddly in SVG.
function VizTip({ at, children }) {
  if (!at) return null
  return (
    <div className="viztip" style={{ left: `${at.x}%`, top: `${at.y}%` }}>{children}</div>
  )
}

// ---- one series over time ----
function LineChart({ labels, values, unit, valueFmt }) {
  const [hover, setHover] = useState(null)
  const max = Math.max(...values, 0)
  const ticks = niceTicks(max)
  const top = ticks[ticks.length - 1] || 1
  const x = (i) => PAD.l + (values.length === 1 ? PLOT.w / 2 : (i / (values.length - 1)) * PLOT.w)
  const y = (v) => PAD.t + PLOT.h - (v / top) * PLOT.h
  const pts = values.map((v, i) => `${x(i)},${y(v)}`).join(' ')
  const nth = everyNth(labels.length)
  const peak = values.indexOf(max)
  const fmt = valueFmt || compact
  return (
    <div className="vizplot">
      <svg viewBox={`0 0 ${VB.w} ${VB.h}`} className="vizsvg" role="img"
        aria-label={`${unit} over ${labels.length} months`}>
        <Grid ticks={ticks} max={top} />
        {/* area wash at ~10%, never a saturated block */}
        <polygon fill={SEQ} fillOpacity="0.10"
          points={`${PAD.l},${PAD.t + PLOT.h} ${pts} ${PAD.l + PLOT.w},${PAD.t + PLOT.h}`} />
        <polyline points={pts} fill="none" stroke={SEQ} strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round" />
        {/* the peak is direct-labelled; every other value rides the axis and the
            tooltip, because a number on every point goes unread */}
        {max > 0 && (() => {
          // The label flips below the point when there is no room above it.
          // Drawn above unconditionally it escapes the plot and lands on the card
          // title — a label clipped by, or overflowing, its own chart.
          const above = y(max) - PAD.t > 16
          const lx = Math.min(Math.max(x(peak), PAD.l + 16), PAD.l + PLOT.w - 16)
          return (
            <g>
              <circle cx={x(peak)} cy={y(max)} r="4.5" fill={SEQ}
                stroke="var(--panel)" strokeWidth="2" />
              <text x={lx} y={y(max) + (above ? -10 : 18)} textAnchor="middle"
                className="vizlabel">{fmt(max)}</text>
            </g>
          )
        })()}
        {labels.map((l, i) => i % nth === 0 && (
          <text key={l} x={x(i)} y={VB.h - 12} textAnchor="middle" className="viztick">{l}</text>
        ))}
        {/* hit bands are the full plot height and far wider than the 2px line */}
        {labels.map((l, i) => (
          <rect key={'h' + i} x={x(i) - PLOT.w / (labels.length * 2)} y={PAD.t}
            width={PLOT.w / labels.length} height={PLOT.h} fill="transparent"
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
        ))}
        {hover != null && (
          <g pointerEvents="none">
            <line x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={PAD.t + PLOT.h}
              stroke="var(--viz-axis)" strokeWidth="1" />
            <circle cx={x(hover)} cy={y(values[hover])} r="4.5" fill={SEQ}
              stroke="var(--panel)" strokeWidth="2" />
          </g>
        )}
      </svg>
      {hover != null && (
        <VizTip at={{ x: (x(hover) / VB.w) * 100, y: (y(values[hover]) / VB.h) * 100 }}>
          <b>{labels[hover]}</b><br />{fmt(values[hover])} {unit}
        </VizTip>
      )}
    </div>
  )
}

// ---- two series compared over time ----
function GroupedBars({ labels, series, unit }) {
  const [hover, setHover] = useState(null)
  const max = Math.max(...series.flatMap((s) => s.values), 0)
  const ticks = niceTicks(max)
  const top = ticks[ticks.length - 1] || 1
  const band = PLOT.w / labels.length
  const GAP = 2                                   // the surface gap between neighbours
  const bw = Math.min(24, (band - 14 - GAP) / series.length)
  const nth = everyNth(labels.length)
  return (
    <div className="vizplot">
      <svg viewBox={`0 0 ${VB.w} ${VB.h}`} className="vizsvg" role="img"
        aria-label={`${series.map((s) => s.name).join(' and ')} by month`}>
        <Grid ticks={ticks} max={top} />
        {labels.map((l, i) => {
          const groupW = bw * series.length + GAP * (series.length - 1)
          const x0 = PAD.l + band * i + (band - groupW) / 2
          return (
            <g key={l}>
              {series.map((s, si) => {
                const v = s.values[i] || 0
                const h = (v / top) * PLOT.h
                const bx = x0 + si * (bw + GAP)
                return v > 0 ? (
                  // rounded at the data end, square at the baseline
                  <path key={s.name} fill={VIZ[si]} d={roundedTop(bx, PAD.t + PLOT.h - h, bw, h, 4)} />
                ) : null
              })}
              <rect x={PAD.l + band * i} y={PAD.t} width={band} height={PLOT.h} fill="transparent"
                onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
            </g>
          )
        })}
        {labels.map((l, i) => i % nth === 0 && (
          <text key={l} x={PAD.l + band * i + band / 2} y={VB.h - 12} textAnchor="middle"
            className="viztick">{l}</text>
        ))}
      </svg>
      {hover != null && (
        <VizTip at={{ x: ((PAD.l + band * hover + band / 2) / VB.w) * 100, y: 8 }}>
          <b>{labels[hover]}</b>
          {series.map((s, si) => (
            <div key={s.name}><i className="tipkey" style={{ background: VIZ[si] }} />
              {s.name}: {compact(s.values[hover] || 0)} {unit}</div>
          ))}
        </VizTip>
      )}
    </div>
  )
}

// A bar rounded on the data end only — square where it meets the baseline, so
// the bar reads as growing from the axis rather than floating.
const roundedTop = (x, y, w, h, r) => {
  const rr = Math.min(r, h, w / 2)
  return `M${x},${y + h} L${x},${y + rr} Q${x},${y} ${x + rr},${y} `
    + `L${x + w - rr},${y} Q${x + w},${y} ${x + w},${y + rr} L${x + w},${y + h} Z`
}

// ---- magnitude across names: one colour for every bar ----
function HBars({ rows, unit, ordinal }) {
  const [hover, setHover] = useState(null)
  const max = Math.max(...rows.map((r) => r.value), 0) || 1
  const H = Math.max(120, rows.length * 34 + 20)
  const LW = 140                                  // room for the name
  const barW = VB.w - LW - 80
  const ORD = ['var(--viz-ord-1)', 'var(--viz-ord-2)', 'var(--viz-ord-3)', 'var(--viz-ord-4)']
  return (
    <div className="vizplot">
      <svg viewBox={`0 0 ${VB.w} ${H}`} className="vizsvg" role="img" aria-label={unit}>
        {rows.map((r, i) => {
          const y = 12 + i * 34
          const w = Math.max(2, (r.value / max) * barW)
          // ordinal = an ordered scale (ageing bands), so one hue gets darker.
          // Otherwise every bar is slot-1 blue: these names have no order, and
          // shading them by value would encode the length twice.
          const fill = ordinal ? ORD[Math.min(i, ORD.length - 1)] : SEQ
          return (
            <g key={r.label} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <text x={LW - 10} y={y + 15} textAnchor="end" className="vizname">{r.label}</text>
              <path fill={fill} d={roundedRight(LW, y, w, 20, 4)} />
              <text x={LW + w + 8} y={y + 15} className="vizlabel">{compact(r.value)}</text>
              <rect x={0} y={y - 6} width={VB.w} height={32} fill="transparent" />
            </g>
          )
        })}
      </svg>
      {hover != null && (
        <VizTip at={{ x: 50, y: ((12 + hover * 34) / H) * 100 }}>
          <b>{rows[hover].label}</b><br />{compact(rows[hover].value)} {unit}
          {rows[hover].bills != null && <> · {rows[hover].bills} bill(s)</>}
        </VizTip>
      )}
    </div>
  )
}

const roundedRight = (x, y, w, h, r) => {
  const rr = Math.min(r, w, h / 2)
  return `M${x},${y} L${x + w - rr},${y} Q${x + w},${y} ${x + w},${y + rr} `
    + `L${x + w},${y + h - rr} Q${x + w},${y + h} ${x + w - rr},${y + h} L${x},${y + h} Z`
}

// ---- part-to-whole, at a glance ----
// A ring is only honest for a few slices read as shares, so the tail is folded
// into one neutral "Other" rather than spawning more hues — and the fold is
// counted in its label so the ring still visibly totals the whole.
function Donut({ rows, unit }) {
  const [hover, setHover] = useState(null)
  const total = rows.reduce((a, r) => a + r.value, 0) || 1
  const R = 82, r0 = 50, cx = 110, cy = 110
  let angle = -Math.PI / 2
  const arcs = rows.map((row, i) => {
    const frac = row.value / total
    const a0 = angle
    const a1 = angle + frac * Math.PI * 2
    angle = a1
    // a 2px gap in the surface separates touching segments — no stroke around
    // the mark, which would add ink that is not data
    const gap = rows.length > 1 ? 0.012 : 0
    return { row, i, a0: a0 + gap, a1: Math.max(a0 + gap, a1 - gap), frac }
  })
  const arcPath = (a0, a1) => {
    const big = a1 - a0 > Math.PI ? 1 : 0
    const p = (a, rad) => `${cx + Math.cos(a) * rad},${cy + Math.sin(a) * rad}`
    return `M${p(a0, R)} A${R},${R} 0 ${big} 1 ${p(a1, R)} L${p(a1, r0)} A${r0},${r0} 0 ${big} 0 ${p(a0, r0)} Z`
  }
  return (
    <div className="vizplot donut">
      <svg viewBox="0 0 620 224" className="vizsvg" role="img" aria-label={unit}>
        {arcs.map(({ row, i, a0, a1 }) => (
          <path key={row.label} d={arcPath(a0, a1)}
            fill={row.other ? 'var(--viz-other)' : VIZ[i % VIZ.length]}
            opacity={hover == null || hover === i ? 1 : 0.45}
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
        ))}
        {/* the whole, in the hole */}
        <text x={cx} y={cy - 2} textAnchor="middle" className="vizhero">{compact(total)}</text>
        <text x={cx} y={cy + 16} textAnchor="middle" className="viztick">{unit}</text>
        {/* labels sit outside the ring in ink, never in the slice colour */}
        {arcs.map(({ row, i, frac }) => (
          <g key={'l' + row.label} transform={`translate(238, ${18 + i * 30})`}
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <rect x="-6" y="-12" width="380" height="26" fill="transparent" />
            <rect x="0" y="-7" width="12" height="12" rx="3"
              fill={row.other ? 'var(--viz-other)' : VIZ[i % VIZ.length]} />
            <text x="20" y="3" className="vizname">{row.label}</text>
            <text x="330" y="3" textAnchor="end" className="vizlabel">
              {compact(row.value)} · {(frac * 100).toFixed(frac < 0.01 ? 2 : 0)}%
            </text>
          </g>
        ))}
      </svg>
    </div>
  )
}

// A figure and the screen that clears it. `tone` is 'warn' only when there is
// something to do — a tile that shouts at zero teaches people to ignore it.
function DashTile({ label, value, sub, tone, hint, onClick }) {
  return (
    <button className={'dtile' + (tone ? ' ' + tone : '')} onClick={onClick}
      title={hint || `Open ${label}`}>
      <span className="lbl">{label}</span>
      <span className={'val' + (longValue(value) ? ' long' : '')}>{value}</span>
      <span className="sub">{sub || ' '}</span>
    </button>
  )
}

const sum = (rows, f) => rows.reduce((a, r) => a + (+f(r) || 0), 0)

// The graphical half. Kept in its own component so its data is fetched only when
// someone actually opens it — the aggregation walks every product and movement,
// and the static view has no use for it.
function DashboardCharts({ money }) {
  const [c, setC] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    api.dashboardCharts().then(setC).catch((e) => setErr(
      // The frontend is read off disk and refreshes with the browser, but routes
      // are registered when Python starts. A backend left running from before
      // this endpoint existed serves the new screen and 404s its calls — a
      // restart, not a fault, and a bare "404" sends someone hunting the wrong thing.
      (e.status === 404 || e.status === 405)
        ? 'the server is still running the code from before the charts were added — '
          + 'restart the backend (Ctrl-C in the run window, then run.bat again) and reload this page'
        : `${e.message || 'the request failed'} — the figures on the static view are unaffected`))
  }, [])

  if (err) return <div className="warnbox"><h4>The charts could not be loaded</h4>
    <div className="small" style={{ color: 'var(--text-2)' }}>{err}</div></div>
  if (!c) return <div className="empty" style={{ marginTop: 60 }}>Building the charts…</div>

  const mv = c.movement
  const hasAny = c.purchases.values.some((v) => v > 0) ||
    mv.series.some((s) => s.values.some((v) => v > 0)) || c.stock_by_category.length > 0
  if (!hasAny) return (
    <div className="empty" style={{ marginTop: 60 }}>Nothing to plot yet.</div>
  )

  return (
    <div className="vizgrid">
      <ChartCard title="Purchases per month"
        note="Posted receipts, on the invoice date rather than the day they were keyed."
        columns={['Month', 'Purchases (₹)']}
        rows={c.purchases.labels.map((l, i) => [l, money(c.purchases.values[i])])}>
        <LineChart labels={c.purchases.labels} values={c.purchases.values} unit="₹" />
      </ChartCard>

      <ChartCard title="Stock received and dispatched"
        note="Pieces moved per month. Both are counted positive so the two can be compared."
        legend={<Legend items={[{ label: mv.series[0].name, color: VIZ[0] },
                                { label: mv.series[1].name, color: VIZ[1] }]} />}
        columns={['Month', mv.series[0].name, mv.series[1].name]}
        rows={mv.labels.map((l, i) => [l, mv.series[0].values[i], mv.series[1].values[i]])}>
        <GroupedBars labels={mv.labels} series={mv.series} unit="pcs" />
      </ChartCard>

      <ChartCard title="Stock value by category"
        note="Only stock a posted GRN created — the same rule behind the Stock value tile, so the ring totals it."
        columns={['Category', 'Value (₹)']}
        rows={c.stock_by_category.map((r) => [r.label, money(r.value)])}>
        <Donut rows={c.stock_by_category} unit="₹ stock value" />
      </ChartCard>

      <ChartCard title="Top suppliers by purchase value"
        note="Posted receipts, all periods."
        columns={['Supplier', 'Purchases (₹)']}
        rows={c.top_suppliers.map((r) => [r.label, money(r.value)])}>
        <HBars rows={c.top_suppliers} unit="₹" />
      </ChartCard>

      <ChartCard title="Payables by age"
        note="What is owed, by how long it has been owed. An unreadable invoice date counts as oldest rather than being dropped, so this totals the payables tile."
        columns={['Age', 'Outstanding (₹)', 'Bills']}
        rows={c.payables_ageing.map((r) => [r.label, money(r.value), r.bills])}>
        <HBars rows={c.payables_ageing} unit="₹ outstanding" ordinal />
      </ChartCard>
    </div>
  )
}

// The dead-stock headline on the main dashboard: the alert, four KPIs, the ageing
// shape and whether clearance is working. Reads the module's OWN summary — one
// arithmetic, two screens, so the dashboard can never quote a figure the register
// disagrees with.
function DashDeadStock({ sum, open }) {
  const t = sum.totals, c = sum.counts, cash = sum.cash_impact
  const dead = c.dead_total
  const trend = sum.trend || []
  const bands = (sum.by_bucket || []).map((b) => ({ label: b.bucket, value: b.qty }))
  const critical = c.critical

  if (!dead.lines) {
    return (
      <Section id="dash-dead" title="Dead Stock & Clearance" summary="all clear">
        <div className="warnbox clean">
          <h4 style={{ border: 'none', margin: 0 }}>
            Nothing has been still for {c.thresholds.dead}+ days — every stocked line is moving.
          </h4>
          <div className="small" style={{ color: 'var(--text-2)', marginTop: 4 }}>
            {c.approaching.lines
              ? <>{c.approaching.lines} line(s) have been quiet for {c.thresholds.approaching}+ days and
                  will cross the line within a month. <button className="btn" style={{ padding: '2px 9px' }}
                    onClick={() => open({ tab: 'register', status: 'approaching' })}>See them</button></>
              : 'Nothing is approaching the line either.'}
          </div>
        </div>
      </Section>
    )
  }

  return (
    <Section id="dash-dead" title="Dead Stock & Clearance"
      summary={`${dead.lines} product line(s) · ${rupees(t.stock_value)} locked`}
      actions={<button className="btn primary" style={{ padding: '3px 11px' }}
        onClick={() => open({ tab: 'register', status: 'dead' })}>Review dead stock</button>}>

      {/* The notification management actually reads, with the two things they can
          do about it beside it rather than in another module's menu. */}
      <div className="warnbox" style={{ borderColor: 'var(--danger-line)', background: 'var(--danger-bg)', marginBottom: 14 }}>
        <h4 style={{ border: 'none', margin: 0, color: 'var(--danger)' }}>
          🔴 {dead.lines} product{dead.lines === 1 ? '' : 's'} have crossed {c.thresholds.dead} days without a sale
        </h4>
        <div className="small" style={{ color: 'var(--text-2)', marginTop: 4 }}>
          {rupees(t.stock_value)} of stock value requires clearance review
          {critical.lines ? <> · <b>{critical.lines}</b> of them unsold for {c.thresholds.critical}+ days</> : null}
          {sum.pos && !sum.pos.available
            ? <> · <b>the till is not readable here</b>, so these ages come from dispatches and receipts only</>
            : null}
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <button className="btn" onClick={() => open({ tab: 'register', status: 'dead' })}>Review dead stock</button>
          <button className="btn" onClick={() => open({ tab: 'worksheet' })}>Clearance worksheets</button>
          {critical.lines > 0 && (
            <button className="btn" onClick={() => open({ tab: 'register', status: 'critical' })}>
              {critical.lines} critical</button>
          )}
        </div>
      </div>

      <div className="dgrid">
        <DashTile label="Dead stock" value={dead.qty + ' pcs'} tone="warn"
          sub={`${t.skus} SKU${t.skus === 1 ? '' : 's'} with no sale for ${c.thresholds.dead}+ days`}
          hint="Open the Dead Stock Register"
          onClick={() => open({ tab: 'register', status: 'dead' })} />
        <DashTile label="Stock value" value={rupees(t.stock_value)} tone="warn"
          sub="locked — capital sitting on the shelf"
          hint="Open the products holding it"
          onClick={() => open({ tab: 'register', status: 'dead' })} />
        <DashTile label="Clearance" value={rupees(t.expected_realisation)}
          sub="expected at the ladder's prices"
          hint="Open the Clearance Worksheet"
          onClick={() => open({ tab: 'worksheet' })} />
        <DashTile label="Recovery" value={t.recovery_pct == null ? '—' : t.recovery_pct + '%'}
          sub="expected cash against what it cost"
          hint="Open the Cash Impact"
          onClick={() => open({ tab: 'cash' })} />
        <DashTile label="Critical" value={critical.lines} tone={critical.lines ? 'warn' : ''}
          sub={critical.lines
            ? `unsold for ${c.thresholds.critical}+ days — ${rupees(critical.stock_value)}`
            : 'nothing past the critical line'}
          hint="Open the register filtered to the worst of it"
          onClick={() => open({ tab: 'register', status: 'critical' })} />
        <DashTile label="Approaching" value={c.approaching.lines}
          tone={c.approaching.lines ? 'warn' : ''}
          sub={c.approaching.lines
            ? `quiet ${c.thresholds.approaching}+ days — dead within a month`
            : 'nothing about to go dead'}
          hint="Open the register filtered to what is about to go dead"
          onClick={() => open({ tab: 'register', status: 'approaching' })} />
      </div>

      <div className="vizgrid" style={{ marginTop: 16 }}>
        <ChartCard title="Dead stock by age"
          note={`Pieces past ${c.thresholds.dead} days, by how long they have been still. One hue darkening, because the bands are an ordered scale.`}
          columns={['Age band', 'Pieces', 'Lines', 'Stock value', 'Expected']}
          rows={(sum.by_bucket || []).map((b) => [b.bucket, b.qty, b.lines,
            money(b.stock_value), money(b.expected_realisation)])}>
          {bands.length
            ? <HBars rows={bands} unit="pcs" ordinal />
            : <div className="empty" style={{ margin: '20px 0' }}>Nothing dead to band.</div>}
        </ChartCard>

        <ChartCard title="Clearance performance"
          note="What each month's worksheets promised against what the till has actually taken for those products, inside those dates. The gap is the shortfall, and it closes on its own as the goods sell."
          legend={<Legend items={[{ label: 'Expected', color: VIZ[0] },
                                  { label: 'Realised', color: VIZ[1] }]} />}
          columns={['Month', 'Campaigns', 'Expected', 'Realised', 'Realised %', 'Sell-through %']}
          rows={trend.map((r) => [r.month, r.campaigns, money(r.expected), money(r.actual),
            r.realisation_pct == null ? '—' : r.realisation_pct + '%',
            r.sell_through_pct == null ? '—' : r.sell_through_pct + '%'])}>
          {trend.length
            ? <GroupedBars labels={trend.map((r) => r.month)}
                series={[{ name: 'Expected', values: trend.map((r) => r.expected) },
                         { name: 'Realised', values: trend.map((r) => r.actual) }]} unit="₹" />
            : <div className="empty" style={{ margin: '20px 0', lineHeight: 1.6 }}>
                No clearance worksheet has been run yet — so there is nothing to judge.<br />
                <button className="btn" style={{ marginTop: 10 }}
                  onClick={() => open({ tab: 'register', status: 'dead' })}>Build the first one</button>
              </div>}
        </ChartCard>
      </div>
    </Section>
  )
}

function Dashboard({ modules, go, company, docs, refreshDocs, user, openDeadStock }) {
  const [d, setD] = useState(null)
  const [partial, setPartial] = useState(false)
  const [busy, setBusy] = useState(false)
  // Which half is showing. Remembered, because someone who works from the charts
  // wants the charts every morning, not the tiles again.
  const [view, setViewState] = useState(() => {
    try { return localStorage.getItem('essa_dash_view') || 'static' } catch { return 'static' }
  })
  const setView = (v) => {
    setViewState(v)
    try { localStorage.setItem('essa_dash_view', v) } catch { /* private mode */ }
  }

  // allSettled, not all: an inventory scan that fails must not blank the six
  // figures that loaded. What is missing is said out loud instead.
  const load = useCallback(() => {
    setBusy(true)
    return Promise.allSettled([
      api.listPurchases(), api.inventorySummary(), api.listOutwards('posted'),
      api.listOutwards('draft'), api.pendingBills(), api.listReturns(),
      api.lrList(), api.listSuppliers(), api.notifications(), api.deadStockSummary(),
    ]).then((r) => {
      const v = (i, fb) => (r[i].status === 'fulfilled' ? r[i].value : fb)
      setPartial(r.some((x) => x.status === 'rejected'))
      setD({
        grns: v(0, []), stock: v(1, {}), transit: v(2, []), outDrafts: v(3, []),
        bills: v(4, []), returns: v(5, []), lr: v(6, []), suppliers: v(7, []),
        // The same feed the bell reads. One call rather than a second pass for
        // the dead-stock tile: the notices already carry it, and two reads of
        // one queue is two chances to print two different numbers for it.
        notifs: v(8, { notices: [], counts: {} }),
        // the same read the module opens on — KPIs, age bands and the clearance
        // trend, so the section below is the module's own arithmetic and not a
        // second calculation of it that could disagree
        dead: v(9, null),
      })
    }).finally(() => setBusy(false))
  }, [])
  useEffect(() => { load() }, [load])

  if (!d) return <div className="empty" style={{ marginTop: 120 }}>Loading the dashboard…</div>

  const toReview = docs.filter((x) => x.status === 'needs_review').length
  const grnDrafts = d.grns.filter((g) => g.status === 'draft')
  const grnPosted = d.grns.filter((g) => g.status === 'posted')
  const shortLines = sum(d.grns, (g) => g.short_lines)
  const shortValue = sum(d.grns, (g) => g.short_value)
  const lrPending = d.lr.filter((e) => !e.received_by).length
  const lrUnlinked = d.lr.filter((e) => !e.matched).length
  const payable = sum(d.bills, (b) => b.outstanding)
  const overdue = d.bills.filter((b) => (b.days || 0) > 30)
  const retDrafts = d.returns.filter((r) => r.status === 'draft').length
  const pendingDetail = d.stock.pending_detail || 0
  const transitQty = sum(d.transit, (o) => o.total_qty)
  // dead + critical: both are past the line, and a headline that left out the
  // worst rows would be the one figure nobody could reconcile against the module
  const notices = d.notifs?.notices || []
  const deadBands = notices.filter((n) => n.key === 'deadstock.dead' || n.key === 'deadstock.critical')
  const deadLines = sum(deadBands, (n) => n.count)
  const deadValue = sum(deadBands, (n) => n.value)
  const criticalLines = sum(notices.filter((n) => n.key === 'deadstock.critical'), (n) => n.count)

  const attention = [
    { key: 'documents', label: 'Invoices to review', value: toReview, tone: toReview ? 'warn' : '',
      sub: toReview ? 'read, but something did not reconcile' : 'nothing waiting',
      hint: 'Open Invoice Entry — documents extracted but not yet confirmed' },
    { key: 'purchases', label: 'GRNs in draft', value: grnDrafts.length, tone: grnDrafts.length ? 'warn' : '',
      sub: grnDrafts.length ? `₹ ${money(sum(grnDrafts, (g) => g.grand_total))} not yet in stock` : 'all receipts posted',
      hint: 'Open GRN — receipts counted but not posted, so the goods are not stock yet' },
    { key: 'inward', label: 'Transfers in transit', value: d.transit.length, tone: d.transit.length ? 'warn' : '',
      sub: d.transit.length ? `${transitQty} pcs dispatched, not accepted` : 'nothing on the road',
      hint: 'Open Stock Inward — dispatched transfers no destination has checked in' },
    { key: 'lr', label: 'Consignments not received', value: lrPending, tone: lrPending ? 'warn' : '',
      sub: lrPending ? 'in the register, not taken in' : 'register is clear',
      hint: 'Open LR Entry — consignments booked but nobody has signed for them' },
    { key: 'purchases', label: 'Open shortage claims', value: shortLines, tone: shortLines ? 'warn' : '',
      sub: shortLines ? `₹ ${money(shortValue)} billed and not delivered` : 'no open claims',
      hint: 'Open GRN — goods billed that the boxes did not hold, not yet waived or claimed' },
    { key: 'payments', label: 'Payable to suppliers', value: '₹ ' + money(payable),
      tone: overdue.length ? 'warn' : '',
      sub: d.bills.length ? `${d.bills.length} bill${d.bills.length === 1 ? '' : 's'}${overdue.length ? ` · ${overdue.length} over 30 days` : ''}` : 'nothing outstanding',
      hint: 'Open Payments — unpaid supplier invoices' },
    { key: 'deadstock', label: 'Dead stock', value: deadLines,
      tone: deadLines ? 'warn' : '',
      sub: deadLines
        ? `₹ ${money(deadValue)} of capital asleep${criticalLines ? ` · ${criticalLines} line(s) critical` : ''}`
        : 'every stocked line is still moving',
      hint: 'Open Dead Stock & Clearance — products with no sale inside the window' },
  ]
  const open = attention.filter((a) => a.tone === 'warn').length

  const recentDocs = docs.slice(0, 6)
  const recentGrns = d.grns.slice(0, 6)

  return (
    <div className="screen scrolls">
      <div className="pagehead">
        <h2>Dashboard</h2>
        <div className="pagesub small">
          {company || 'Essa'} — {open
            ? `${open} thing${open === 1 ? '' : 's'} waiting on someone`
            : 'nothing is waiting — every queue is clear'}
        </div>
        {/* Two ways of reading the same warehouse: the figures, or their shape
            over time. A segmented control rather than tabs — it is one thing
            with two states, not two screens. */}
        <div className="segbar" role="tablist" aria-label="Dashboard view">
          <button role="tab" aria-selected={view === 'static'}
            className={'seg' + (view === 'static' ? ' on' : '')} onClick={() => setView('static')}
            title="Figures, counts and what is waiting on someone">▦ Figures</button>
          <button role="tab" aria-selected={view === 'graphical'}
            className={'seg' + (view === 'graphical' ? ' on' : '')} onClick={() => setView('graphical')}
            title="The same warehouse as trends and shares over time">📈 Charts</button>
        </div>
        <button className="btn" onClick={() => { refreshDocs(); load() }} disabled={busy}
          title="Re-read every figure on this screen">{busy ? 'Refreshing…' : '↻ Refresh'}</button>
      </div>

      <div className="dash">
        {view === 'graphical' && <DashboardCharts money={money} />}
        {view === 'static' && <>
        {partial && <div className="warnbox" style={{ marginBottom: 'var(--sp-5)' }}>
          <h4>Some figures did not load</h4>
          <div className="small" style={{ color: 'var(--text-2)' }}>
            The tiles below show what the server did return. Refresh to try the rest again —
            a blank tile here means unread, not zero.
          </div>
        </div>}

        {/* The notification centre, on the screen people already open. The bell
            in the header carries the same feed for everywhere else — this is it
            in full, where there is room to read the sentence under each line. */}
        <Section id="dash-notices" title="Notifications"
          summary={notices.length
            ? `${d.notifs.counts.unread || 0} unread of ${notices.length}`
            : 'nothing open'}
          actions={(d.notifs.counts?.unread || 0) > 0 ? (
            <button className="btn" onClick={() => api.notificationsReadAll(user).then(load)}>
              Mark all read</button>
          ) : null}>
          {!notices.length && (
            <div className="empty" style={{ padding: '18px 0' }}>
              Nothing is waiting. A notice appears the moment a queue stops being empty —
              and clears itself when the queue does.
            </div>
          )}
          {notices.map((n) => (
            <NoticeRow key={n.key} n={n} compact
              onOpen={(x) => go(x.module)}
              onRead={() => {}} />
          ))}
        </Section>

        <Section id="dash-attention" title="Waiting on someone"
          summary={open ? `${open} queue${open === 1 ? '' : 's'} not clear` : 'all clear'}>
          <div className="dgrid">
            {attention.map((a, i) => (
              <DashTile key={i} label={a.label} value={a.value} sub={a.sub} tone={a.tone}
                hint={a.hint} onClick={() => go(a.key)} />
            ))}
          </div>
        </Section>

        {/* Dead stock, for the people who decide about it rather than the people
            who clear it. Four figures, the ageing shape, and whether the last
            clearance actually worked — every one of them a link into the module
            already filtered, because a number management cannot open is a number
            they have to ask somebody about. The worksheet itself stays where it
            belongs; this is its headline. */}
        {d.dead && <DashDeadStock sum={d.dead} open={openDeadStock} />}

        <Section id="dash-stock" title="Stock at a glance"
          summary={`${d.stock.product_count || 0} records · ₹ ${money(d.stock.total_stock_value)}`}>
          <div className="dgrid">
            <DashTile label="Stock value" value={'₹ ' + money(d.stock.total_stock_value)}
              sub="at purchase cost, posted receipts only" onClick={() => go('inventory')}
              hint="Open Inventory — only records a posted GRN created are counted" />
            <DashTile label="Pieces on hand" value={(d.stock.total_units || 0).toLocaleString('en-IN')}
              sub={`${d.stock.product_count || 0} inventory records`} onClick={() => go('inventory')} />
            <DashTile label="Awaiting physical detail" value={pendingDetail}
              tone={pendingDetail ? 'warn' : ''}
              sub={pendingDetail ? 'colour, size and fit not recorded yet' : 'every item detailed'}
              hint="Open Inventory — items received but never looked at on the phone"
              onClick={() => go('inventory')} />
            <DashTile label="Purchase returns in draft" value={retDrafts}
              tone={retDrafts ? 'warn' : ''}
              sub={retDrafts ? 'debit notes not yet raised' : 'no open debit notes'}
              onClick={() => go('returns')} />
            <DashTile label="Outward drafts" value={d.outDrafts.length}
              tone={d.outDrafts.length ? 'warn' : ''}
              sub={d.outDrafts.length ? 'packed but not dispatched' : 'nothing packed'}
              onClick={() => go('outward')} />
            <DashTile label="LR rows without an invoice" value={lrUnlinked}
              sub={lrUnlinked ? 'no invoice matched to them yet' : 'every row is linked'}
              hint="Open LR Entry — register rows no invoice has been matched against"
              onClick={() => go('lr')} />
          </div>
          <div className="items-foot">
            <span>Posted GRNs <b>{grnPosted.length}</b></span>
            <span>Suppliers <b>{d.suppliers.length}</b></span>
            <span>Documents <b>{docs.length}</b></span>
            <span>LR entries <b>{d.lr.length}</b></span>
            {d.stock.excluded_products > 0 && <span title="Records not traceable to a posted GRN — debris, or kept at zero after an unpost. They are not stock, so they are not valued.">
              Excluded from stock <b>{d.stock.excluded_products}</b></span>}
          </div>
        </Section>

        <Section id="dash-modules" title="Warehouse modules" summary={`${modules.length} screens`}>
          <div className="modgrid">
            {modules.map((m) => (
              <button key={m.key} className="modcard" onClick={() => go(m.key)} title={m.blurb}>
                <span className="ico" aria-hidden="true">{m.icon}</span>
                <span className="lbl">{m.label}</span>
              </button>
            ))}
          </div>
        </Section>

        <Section id="dash-recent" title="Latest activity" defaultOpen={false}
          summary={`${recentDocs.length} documents · ${recentGrns.length} receipts`}>
          <div className="drecent">
            <div>
              <h5>Documents</h5>
              {recentDocs.length === 0 && <div className="small">Nothing uploaded yet.</div>}
              {recentDocs.map((x) => (
                <button key={x.id} className="drow" onClick={() => go('documents')}
                  title="Open Invoice Entry">
                  <span className="nm">{x.supplier_name || x.filename}</span>
                  <span className={'badge ' + x.status}>{x.status.replace('_', ' ')}</span>
                  <span className="amt">₹ {money(x.grand_total)}</span>
                </button>
              ))}
            </div>
            <div>
              <h5>Goods receipts</h5>
              {recentGrns.length === 0 && <div className="small">No GRN has been created yet.</div>}
              {recentGrns.map((g) => (
                <button key={g.id} className="drow" onClick={() => go('purchases')}
                  title="Open GRN">
                  <span className="nm">{g.grn_no || '#' + g.id} · {g.supplier_name || '—'}</span>
                  {/* same two colours the GRN screen uses for the same two states */}
                  <span className={'badge ' + (g.status === 'posted' ? 'confirmed' : 'uploaded')}>{g.status}</span>
                  <span className="amt">₹ {money(g.grand_total)}</span>
                </button>
              ))}
            </div>
          </div>
        </Section>
        </>}
      </div>
    </div>
  )
}

// ---------- app shell ----------
export default function App() {
  // the open tab survives a reload — a warehouse screen is left on the module
  // someone works in, and losing it on every refresh is a small daily tax
  const [tab, setTabState] = useState(() => localStorage.getItem('essa_tab') || 'dashboard')
  const setTab = (t) => { setTabState(t); try { localStorage.setItem('essa_tab', t) } catch { /* private mode */ } }
  const [status, setStatus] = useState(null)
  const [docs, setDocs] = useState([])
  const [sel, setSel] = useState(null)
  const [selPurchase, setSelPurchase] = useState(null)
  const [toastMsg, setToastMsg] = useState(null)
  const [showSettings, setShowSettings] = useState(false)
  // The notification panel is mounted at the root (see below) while its bell
  // sits on the chrome, so the open flag lives here. `notifTick` is bumped when
  // the panel changes something, which is what makes the badge re-read.
  const [notifsOpen, setNotifsOpen] = useState(false)
  const [notifTick, setNotifTick] = useState(0)
  // Which Dead Stock screen a dashboard card asked for. Held here because the
  // card and the module are siblings — see the useEffect in DeadStock.
  const [dsIntent, setDsIntent] = useState(null)
  const openDeadStock = (intent) => { setDsIntent(intent || null); setTab('deadstock') }
  const [scanning, setScanning] = useState(null)   // {url, name} while extracting
  const [docQuery, setDocQuery] = useState('')
  const [docScope, setDocScope] = useState('all')
  // scope chip + search, applied together — the same pairing every list uses
  const shownDocs = docs
    .filter((d) => docScope === 'all' || d.status === docScope)
    .filter((d) => matches(d, docQuery, ['supplier_name', 'filename', 'invoice_number', 'status']))
  const docPage = usePaged(shownDocs, 50)
  const [authed, setAuthed] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [user, setUser] = useState('')
  const [role, setRole] = useState('')
  //: what this ACCOUNT may open, screen by screen. Empty means unrestricted and
  //: the role decides on its own — see services/permissions.has_map.
  const [perms, setPerms] = useState({})
  const [showPassword, setShowPassword] = useState(false)

  const refreshStatus = useCallback(() => api.status().then(setStatus), [])
  const refresh = useCallback(() => api.listDocuments().then(setDocs), [])

  // verify any stored token on load (so a refresh doesn't force re-login).
  // The role comes back from the server on every verify rather than being kept
  // beside the token: a role changed by the super admin then takes effect on
  // the next reload, instead of waiting for the person to sign out.
  useEffect(() => {
    const t = session.get()
    if (!t) { setAuthChecked(true); return }
    api.verifyToken(t).then((r) => {
      if (r.ok) {
        setAuthed(true); setUser(r.user); setRole(r.role || '')
        setPerms(r.permissions || {})
      } else session.clear()
    }).catch(() => {}).finally(() => setAuthChecked(true))
  }, [])

  // A token that expires, or an account deactivated mid-shift, comes back as a
  // 401 on whatever call happens next. Handled once here so it returns the
  // whole app to the login screen, rather than leaving each panel to show its
  // own failure and the person to guess why everything stopped working.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      session.clear(); setAuthed(false); setSel(null)
    })
  }, [])

  useEffect(() => { if (authed) { refreshStatus(); refresh() } }, [authed, refresh, refreshStatus])
  const toast = (m, kind) => { setToastMsg({ m, kind }); setTimeout(() => setToastMsg(null), 3000) }
  const handleLogin = (token, u, r, perms) => {
    session.set(token); setUser(u); setRole(r || ''); setPerms(perms || {}); setAuthed(true)
  }
  const logout = () => {
    // Clears the cookie as well as the stored token. Without the call the
    // cookie outlives the logout, and the invoice images on a shared terminal
    // stay fetchable by the next person to open the tab.
    api.logout()
    session.clear(); setAuthed(false); setSel(null); setTab('dashboard')
  }
  const gotoPurchase = (id) => { setSelPurchase(id); setTab('purchases') }

  const onUpload = async (e) => {
    // Several pages of ONE invoice, in the order they were picked. A bill of
    // sixty lines prints as two pages and only the last carries the totals, so
    // uploading them separately produced two half-documents and left the second
    // to be keyed into the first by hand.
    const files = Array.from(e.target.files || []); if (!files.length) return
    const file = files[0]
    // show the first page being "scanned" while the backend extracts
    const url = file.type.startsWith('image/') ? URL.createObjectURL(file) : null
    setScanning({ url, name: files.length > 1 ? `${files.length} pages` : file.name })
    setTab('documents')
    try {
      const res = await api.upload(files)
      await refresh()
      setSel(res.document.id)
      toast(res.supplier_recognised
        ? `✓ Recognised ${res.document.supplier_name}${res.profile_used ? ' (using trained format)' : ''}`
        : '✓ Extracted — supplier not recognised, review & train', 'ok')
      if (res.lr_filled && res.lr_filled.length) {
        toast(`🔗 Matched & filled ${res.lr_filled.length} LR row(s) from this invoice`, 'ok')
        const conflicts = res.lr_filled.reduce((n, x) => n + ((x.mismatches && x.mismatches.length) || 0), 0)
        if (conflicts) toast(`⚠ ${conflicts} value(s) disagree with the register — see LR Entry (conflict flag)`, 'err')
      }
    } catch (err) { toast('Upload failed: ' + (err.detail || err.message), 'err') }
    finally {
      setScanning(null)
      if (url) URL.revokeObjectURL(url)
    }
    e.target.value = ''
  }

  const delOne = async (e, id) => {
    e.stopPropagation()
    if (!window.confirm('Delete this document and its extraction?')) return
    try {
      await api.deleteDocument(id)
      if (sel === id) setSel(null)
      await refresh(); toast('Document deleted', 'ok')
    } catch (err) { toast(err.detail || 'Delete failed', 'err') }
  }
  const clearAll = async () => {
    if (!window.confirm('Clear ALL transaction data — documents, LR entries, GRNs, inventory, outwards, returns and payments?\nThis cannot be undone.')) return
    const wipeMasters = window.confirm('Also wipe the MASTERS (suppliers, trained formats, agents, transporters)?\n\nOK = fully empty database (only the category master stays).\nCancel = keep suppliers & trained formats.')
    const res = await api.clearAllDocuments(wipeMasters)
    setSel(null); await refresh()
    toast(wipeMasters ? '✓ Database emptied — only category master kept' : '✓ Transactions cleared — suppliers & training kept', 'ok')
    return res
  }

  const providers = status?.providers || {}
  // A module above this person's rank is absent from the menu and from the
  // dashboard grid, rather than present and refusing when clicked.
  //
  // …and so is one their ACCOUNT has been restricted out of. An empty grant map
  // means unrestricted — see services/permissions.has_map — so this narrows only
  // for accounts somebody has deliberately narrowed. Courtesy, not enforcement:
  // the server refuses either way (backend/app/security.py). The point of hiding
  // it is that nobody is shown twelve buttons that answer "not for you".
  const granted = perms?.screens || null
  const modules = MODULES.filter((m) => (!m.min || atLeast(role, m.min))
    && (!granted || (granted[m.key] || []).length))
  const isSuper = atLeast(role, 'superadmin')
  // A `pos:` tab is a screen of the shop rather than one of ours, and is served
  // in a frame — so it is answered before the warehouse chain below.
  const posScreen = POS_ITEMS.find((p) => p && p.key === tab)
  // The open tab is remembered across sessions, so the person who signs in at
  // this terminal is not always the one who left it on Payments. One guard in
  // front of the whole chain rather than a check inside each branch: a screen
  // added later is covered by its MODULES entry alone, and cannot be forgotten.
  const wanted = MODULES.find((m) => m.key === tab)
  const denied = wanted && wanted.min && !atLeast(role, wanted.min) ? wanted : null

  if (!authChecked) return <div className="login-wrap"><div className="login-bg" /></div>
  if (!authed) return <LoginScreen onLogin={handleLogin} />

  return (
    <div className="app">
      {/* Brand and account actions on one row, navigation on its own below it.
          The eleven modules are one warehouse, so they sit behind one menu on
          that row; what the row opens with is the dashboard, which is the only
          screen that answers "which of the eleven did I need". */}
      <div className="topbar">
        <div className="brand">Essa <span>·</span> Document Intake<small>{status?.company?.name} — invoice → data, trained per supplier</small></div>
        <div className="spacer" />
        {/* The vision key and model are server-wide settings, so the gear is a
            super admin's. For everyone else the pill still reports whether
            vision is on — that changes how an upload behaves and is worth
            knowing — but it does not open a screen the server would refuse. */}
        {isSuper ? (
          <button className={'pill ' + (providers.claude_vision ? 'on' : 'off')} style={{ cursor: 'pointer' }}
            title="Configure vision extraction" onClick={() => setShowSettings(true)}>
            👁 vision {providers.claude_vision ? 'on' : 'off'} ⚙</button>
        ) : (
          <span className={'pill ' + (providers.claude_vision ? 'on' : 'off')}
            title="Vision extraction — a super admin configures this">
            👁 vision {providers.claude_vision ? 'on' : 'off'}</span>
        )}
        <span className={'pill ' + (providers.tesseract ? 'on' : 'off')}>OCR {providers.tesseract ? 'on' : 'off'}</span>
        <NotificationBell onOpen={() => setNotifsOpen(true)} tick={notifTick} />
        <label className="btn primary uploadbtn"
          title="One invoice. Pick both pages together if it is printed on more than one.">
          Upload invoice<input type="file" accept="image/*,.pdf" multiple onChange={onUpload} /></label>
        {/* Who you are signed in as, and at what level. On a shared warehouse
            terminal the second half is the load-bearing one: it is the answer
            to "why can I not see Reports today", visible without asking. */}
        <span className={'badge role-' + (role || 'user')} style={{ marginLeft: 2 }}
          title={ROLE_HELP[role] || ''}>{ROLE_LABEL[role] || role}</span>
        <button className="btn" title="Change your password"
          onClick={() => setShowPassword(true)}>{user}</button>
        <button className="btn" title={'Sign out of ' + user} onClick={logout}>Logout</button>
      </div>
      {/* Not wrapped in .tabs: that class styles a strip of tab buttons, and its
          rules (nowrap above all) reach into the menu's own buttons and stop the
          descriptions wrapping. There is no strip left to style anyway. */}
      <div className="navbar">
        <NavMenu tab={tab} setTab={setTab} items={[DASHBOARD, null, ...modules]}
          icon="🏬" label="Warehouse" hint="Every warehouse screen" />
        <span className="navsep" aria-hidden="true" />
        <NavMenu tab={tab} setTab={setTab} items={POS_ITEMS}
          icon="🛍" label="POS" hint="The retail shop — billing, floor sales and shop stock" />
      </div>

      {denied ? (
        <div className="body"><div className="empty" style={{ margin: 'auto', maxWidth: 420, lineHeight: 1.7 }}>
          <div style={{ fontSize: 26, marginBottom: 6 }}>{denied.icon}</div>
          <b>{denied.label}</b> needs {ROLE_LABEL[denied.min]} access.<br />
          You are signed in as <b>{user}</b> ({ROLE_LABEL[role] || role}).
          <div style={{ marginTop: 12 }}>
            <button className="btn primary" onClick={() => setTab('dashboard')}>Back to the dashboard</button>
          </div>
        </div></div>
      ) : posScreen ? (
        <PosScreen screen={posScreen} available={status?.pos?.available}
          error={status?.pos?.error} />
      ) : tab === 'dashboard' ? (
        <Dashboard modules={modules} go={setTab} company={status?.company?.name}
          docs={docs} refreshDocs={refresh} user={user} openDeadStock={openDeadStock} />
      ) : tab === 'lr' ? (
        <div className="body"><LREntryView toast={toast} /></div>
      ) : tab === 'documents' ? (
        <div className="body">
          <Sidebar id="documents" label="Documents">
            <div className="head"><h3>Documents · {docs.length}</h3>
              {/* Emptying every transaction table is irreversible and global,
                  so it is a super admin's button even though this screen is
                  the floor's. The server refuses it for anyone else too. */}
              {docs.length > 0 && isSuper && <button className="btn" style={{ padding: '3px 9px', fontSize: 11 }}
                onClick={clearAll} title="Delete all documents & transaction data">Clear all</button>}</div>
            {docs.length > 0 && <>
              <SearchBox value={docQuery} onChange={setDocQuery}
                placeholder="Search supplier, invoice, status…" />
              <div className="toolbar"><FilterChips value={docScope} onChange={setDocScope} options={[
                ['needs_review', 'To review', docs.filter((d) => d.status === 'needs_review').length, 'Read, but something did not reconcile'],
                ['confirmed', 'Confirmed', docs.filter((d) => d.status === 'confirmed').length, 'Corrected and saved'],
                ['posted', 'Posted', docs.filter((d) => d.status === 'posted').length, 'Already booked into stock'],
                ['all', 'All', docs.length, 'Every document'],
              ]} /></div>
            </>}
            <div className="list">
              {docs.length === 0 && <div className="empty" style={{ marginTop: 30, fontSize: 13 }}>No documents. Click “Upload invoice” to add one.</div>}
              {docs.length > 0 && shownDocs.length === 0 && <div className="empty" style={{ marginTop: 30, fontSize: 13 }}>
                Nothing matches. Try “All” or clear the search.</div>}
              {docPage.slice.map((d) => (
                <div key={d.id} className={'doc-row' + (sel === d.id ? ' sel' : '')} onClick={() => setSel(d.id)}>
                  <div className="t" style={{ display: 'flex', alignItems: 'center' }}>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.supplier_name || d.filename}</span>
                    <button className="rowdel" title="Delete document" onClick={(e) => delOne(e, d.id)}>×</button>
                  </div>
                  <div className="m">
                    <span className={'badge ' + d.status}>{d.status.replace('_', ' ')}</span>
                    <span>#{d.invoice_number || '—'}</span>
                    <span style={{ marginLeft: 'auto' }} className={'conf ' + confClass(d.confidence)}>{d.confidence != null ? Math.round(d.confidence * 100) + '%' : ''}</span>
                  </div>
                  <div className="m"><span>₹ {money(d.grand_total)}</span></div>
                </div>
              ))}
            </div>
            <Pager {...docPage} noun="document" />
          </Sidebar>
          <Review docId={sel} onSaved={refresh} onCreateGrn={gotoPurchase} toast={toast} />
        </div>
      ) : tab === 'purchases' ? (
        <Purchases selId={selPurchase} setSelId={setSelPurchase} toast={toast} />
      ) : tab === 'inventory' ? (
        <Inventory toast={toast} />
      ) : tab === 'deadstock' ? (
        <DeadStock toast={toast} go={setTab} intent={dsIntent}
          onIntentUsed={() => setDsIntent(null)} />
      ) : tab === 'labels' ? (
        <LabelDesigner toast={toast} role={role} />
      ) : tab === 'locator' ? (
        <ItemLocator toast={toast} />
      ) : tab === 'labelprint' ? (
        <LabelPrinting toast={toast} />
      ) : tab === 'outward' ? (
        <StockOutward toast={toast} />
      ) : tab === 'inward' ? (
        <StockInward toast={toast} />
      ) : tab === 'returns' ? (
        <Returns toast={toast} />
      ) : tab === 'payments' ? (
        <Payments toast={toast} />
      ) : tab === 'reports' ? (
        <Reports />
      ) : tab === 'masters' ? (
        // No role check here any more — the guard above the chain covers every
        // module from its MODULES entry, so this cannot drift out of step with
        // the menu the way a second copy of the rule would.
        <Masters toast={toast} />
      ) : tab === 'users' ? (
        <Users toast={toast} me={user} />
      ) : tab === 'suppliers' ? (
        <Suppliers toast={toast} />
      ) : (
        // an unknown saved tab (a module renamed since it was stored) lands on
        // the dashboard rather than on a blank screen
        <Dashboard modules={modules} go={setTab} company={status?.company?.name}
          docs={docs} refreshDocs={refresh} user={user} openDeadStock={openDeadStock} />
      )}

      {scanning && <ScanningOverlay url={scanning.url} name={scanning.name}
        vision={!!providers.claude_vision} />}
      {/* At the app root, not in the header the bell sits in: everything under
          .topbar is styled for the dark chrome, and a light panel mounted there
          inherits white button text on a white card. */}
      {notifsOpen && <NotificationPanel go={setTab} user={user} toast={toast}
        onClose={() => setNotifsOpen(false)}
        onChanged={() => setNotifTick((t) => t + 1)} />}
      {showSettings && <VisionSettings onClose={() => setShowSettings(false)}
        onChanged={refreshStatus} toast={toast} />}
      {showPassword && <ChangePassword onClose={() => setShowPassword(false)} toast={toast} />}
      {toastMsg && <div className={'toast ' + toastMsg.kind}>{toastMsg.m}</div>}
    </div>
  )
}
