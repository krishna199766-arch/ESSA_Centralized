import React, { useEffect, useState, useCallback, useRef } from 'react'
import { api } from './api.js'

// ---------- helpers ----------
const num = (v) => (v === '' || v == null ? null : isNaN(+v) ? v : +v)
const money = (v) => (v == null || v === '' ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const confClass = (c) => (c == null ? '' : c >= 0.9 ? 'hi' : c >= 0.6 ? 'mid' : 'lo')

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
      onLogin(r.token, r.user, r.role)
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

// dd/mm/yyyy for reading back — the form this business writes, shown next to the
// picker so there is never any doubt which of 03/04 is the month
const readableDate = (iso) => {
  const s = toISODate(iso)
  if (!s) return ''
  const [y, m, d] = s.split('-')
  return `${d}/${m}/${y}`
}

function DateField({ label, value, onChange, style, width, required, title, inline }) {
  const iso = toISODate(value)
  const unreadable = value && !iso
  const input = (
    <>
      <input type="date" value={iso} title={title || (iso ? readableDate(iso) : '')}
        onChange={(e) => onChange(e.target.value)} />
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

function Field({ label, value, onChange, flagged, note, wide, source, date }) {
  return (
    <div className={'field' + (flagged ? ' flag' : '') + (source && !flagged ? ' fromlr' : '')}
      style={wide ? { gridColumn: '1 / -1' } : null}>
      <label>{label}</label>
      {date
        ? <DateField inline value={value} onChange={onChange} />
        : <input value={value ?? ''} onChange={(e) => onChange(e.target.value)} />}
      {flagged && <div className="flagnote">⚠ needs review{note ? ' · ' + note : ''}</div>}
      {source && !flagged && <div className="srcnote">🔗 from {source}</div>}
    </div>
  )
}

// ---------- line items ----------
// Full per-line field set; the table scrolls horizontally so nothing is dropped.
//
// `barcode` is deliberately NOT a column. It still comes off the invoice and is
// still stored on the line — `inventory.match_product` keys a re-buy on it, which
// is what keeps one item's cost history in one product — but it is the supplier's
// number, not ours, and nobody reviewing an invoice checks it by eye. The
// trade-off: a misread supplier barcode can no longer be corrected here.
const ITEM_COLS = [
  ['description', 'Description', false, 200],
  ['brand', 'Brand', false, 90], ['design', 'Design', false, 90], ['size', 'Size', false, 80],
  ['hsn', 'HSN', false, 80], ['qty', 'Qty', true, 60], ['uom', 'UOM', false, 60],
  ['mrp', 'MRP', true, 70], ['rate', 'Rate', true, 70], ['discount_pct', 'Disc %', true, 60],
  ['taxable_value', 'Taxable', true, 90], ['amount', 'Amount', true, 90],
]
function LineItems({ items, setItems }) {
  const upd = (i, k, v) => { const c = items.map((x) => ({ ...x })); c[i][k] = num(v); setItems(c) }
  const addRow = () => setItems([...items, { description: '', qty: null, rate: null, amount: null, uom: 'PCS' }])
  const delRow = (i) => setItems(items.filter((_, j) => j !== i))
  const qtySum = items.reduce((s, x) => s + (+x.qty || 0), 0)
  const amtSum = items.reduce((s, x) => s + (+(x.taxable_value ?? x.amount) || 0), 0)
  return (
    <div>
      <div style={{ overflowX: 'auto' }}>
      <table className="items" style={{ minWidth: 990 }}>
        <thead><tr>{ITEM_COLS.map(([k, l, , w]) => <th key={k} style={{ minWidth: w }}>{l}</th>)}<th></th></tr></thead>
        <tbody>
          {items.map((it, i) => (
            <tr key={i}>
              {ITEM_COLS.map(([k, , isNum]) => (
                <td key={k} className={isNum ? 'num' : ''}>
                  <input value={it[k] ?? ''} onChange={(e) => upd(i, k, e.target.value)} />
                </td>
              ))}
              <td><button className="btn" style={{ padding: '2px 7px' }} onClick={() => delRow(i)}>×</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <div className="items-foot">
        <span>{items.length} lines</span>
        <span>Σ qty <b>{qtySum.toLocaleString('en-IN')}</b></span>
        <span>Σ value <b>{money(amtSum)}</b></span>
        <button className="btn" style={{ padding: '3px 10px', marginLeft: 'auto' }} onClick={addRow}>+ add line</button>
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
                    <td>{c.lr_date || '—'}</td>
                    <td>{c.transport || '—'}</td>
                    <td>{c.inv_no || '—'}{c.inv_date ? ` · ${c.inv_date}` : ''}</td>
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
function Review({ docId, onSaved, onCreateGrn, toast }) {
  const [doc, setDoc] = useState(null)
  const [data, setData] = useState(null)
  const [flags, setFlags] = useState({})
  const [warnings, setWarnings] = useState([])
  const [train, setTrain] = useState(true)
  const [saving, setSaving] = useState(false)
  const [lrCands, setLrCands] = useState(null)   // register rows offered to link

  useEffect(() => {
    if (!docId) return
    api.getDocument(docId).then((d) => {
      setDoc(d.document)
      setData(structuredClone(d.extraction?.data || {}))
      setFlags(d.extraction?.field_flags || {})
      setWarnings(d.extraction?.warnings || [])
    })
  }, [docId])

  if (!docId) return <div className="empty">Select a document from the left, or upload a new invoice to extract it.</div>
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
      source={fromLr(path)} date={opts.date}
      onChange={(v) => setData(setPath(data, path, opts.raw ? v : num(v)))} />
  )
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
    } catch (e) { toast('Save failed: ' + e.message, 'err') }
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
    } catch (e) { toast('Could not create GRN: ' + e.message, 'err') }
    setSaving(false)
  }

  return (
    <div className="main">
      <div className="viewer"><img src={api.imageUrl(docId)} alt="invoice" /></div>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div className="editor">
          <div className={'warnbox ' + (warnings.filter((w)=>!w.includes('OCR')&&!w.includes('vision')&&!w.includes('sample')).length ? '' : 'clean')} style={{ marginBottom: 20 }}>
            <h4>{warnings.length ? `${warnings.length} check(s)` : 'All internal checks passed'}
              {doc && <span className="small" style={{ float: 'right' }}>via {doc && data.template_key ? '' : ''}extraction · confidence <b className={'conf ' + confClass(doc?.confidence)}>{doc ? Math.round(doc.confidence * 100) + '%' : '—'}</b></span>}
            </h4>
            {warnings.length ? <ul>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
          </div>

          <Section id="rev.supplier" title="Supplier">
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
          </Section>

          <Section id="rev.supplier-bank" title="Supplier Bank">
            <div className="grid">
              {f('supplier.bank.name', 'Bank Name', { raw: true })}
              {f('supplier.bank.account_no', 'Account No', { raw: true })}
              {f('supplier.bank.ifsc', 'IFSC', { raw: true })}
              {f('supplier.bank.branch', 'Branch', { raw: true })}
            </div>
          </Section>

          <Section id="rev.buyer-bill-to" title="Buyer (bill to)">
            <div className="grid">
              {f('buyer.name', 'Name', { raw: true })}
              {f('buyer.gstin', 'GSTIN', { raw: true })}
              {f('buyer.state', 'State', { raw: true })}
              {f('buyer.address', 'Address', { raw: true, wide: true })}
            </div>
          </Section>

          <Section id="rev.invoice" title="Invoice">
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
          </Section>

          <div className="section">
            <h4>E-invoice &amp; Transport
              <button className="h4btn" disabled={saving} onClick={fetchFromLr}
                title="Fill LR No, LR Date, Transporter and Book City from the matching LR register row">
                ⟲ fetch from LR register</button>
            </h4>
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

          <Section id="rev.line-items" title="Line Items">
            <LineItems items={data.line_items || []} setItems={(it) => setData({ ...data, line_items: it })} />
          </Section>

          <Section id="rev.taxes" title="Taxes">
            <div className="grid">
              {f('taxes.cgst_rate', 'CGST %')}
              {f('taxes.cgst_amount', 'CGST Amount')}
              {f('taxes.sgst_rate', 'SGST %')}
              {f('taxes.sgst_amount', 'SGST Amount')}
              {f('taxes.igst_rate', 'IGST %')}
              {f('taxes.igst_amount', 'IGST Amount')}
              {f('taxes.tds_amount', 'TDS')}
              {f('taxes.special_discount', 'Special Discount')}
              {f('taxes.other_charges', 'Other Charges')}
              {f('taxes.freight', 'Freight')}
              {f('taxes.round_off', 'Round Off')}
            </div>
          </Section>

          <Section id="rev.totals" title="Totals">
            <div className="grid">
              {f('totals.total_qty', 'Total Qty')}
              {f('totals.sub_total', 'Sub Total')}
              {f('totals.taxable_total', 'Taxable Total')}
              {f('totals.tax_total', 'Tax Total')}
              {f('totals.grand_total', 'Grand Total')}
              {f('totals.amount_in_words', 'Amount in Words', { raw: true, wide: true })}
            </div>
          </Section>

          <Section id="rev.grn-notes" title="GRN &amp; Notes">
            <div className="grid">
              {f('meta.grn_no', 'GRN No', { raw: true })}
              {fd('meta.grn_date', 'GRN Date')}
              {f('meta.received_by', 'Received By', { raw: true })}
              {f('meta.notes', 'Notes', { raw: true, wide: true })}
            </div>
          </Section>
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
  return (
    <div className="body">
      <div className="sidebar">
        <div className="head"><h3>Suppliers · {list.length}</h3></div>
        <SearchBox value={q} onChange={setQ} placeholder="Search name, GSTIN, state…" />
        <div className="list">
          {list.filter((s) => matches(s, q, ['name', 'gstin', 'state'])).map((s) => (
            <div key={s.id} className={'sup-row' + (sel === s.id ? ' sel' : '')} onClick={() => setSel(s.id)}>
              <div className="t">{s.name}</div>
              <div className="m">
                <span className={'trainflag ' + (s.has_profile ? 'yes' : 'no')}>{s.has_profile ? `trained v· ${s.profile_samples} sample(s)` : 'not trained'}</span>
                <span>{s.document_count} doc(s)</span>
              </div>
            </div>
          ))}
        </div>
      </div>
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
          ) : <p className="small">No profile yet. Confirm one of this supplier's invoices with “Train” enabled to teach the system its format — after that, new invoices from them are auto-recognised and extracted with this profile.</p>}
        </div>
      ) : <div className="empty">Select a supplier to see its learned format.</div>}
    </div>
  )
}

// ---------- purchases / GRN ----------
// The attributes that make a breakdown row its own stock item — same set the phone
// detail form and the QR payload carry. [key, label, column width]
const SPLIT_ATTRS = [
  ['size', 'Size', 90], ['color', 'Colour', 100], ['material', 'Material', 100],
  ['pattern', 'Pattern', 100], ['fit', 'Fit', 85], ['product_type', 'Type', 90],
  ['design_no', 'Design No', 95],
]
const SPLIT_MONEY = [['qty', 'Qty', 70], ['rate', 'Rate', 85], ['mrp', 'MRP', 85],
  ['sale_price', 'Sale price', 90], ['sale_discount_pct', 'Discount %', 90]]
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
const round3 = (n) => Math.round((+n || 0) * 1000) / 1000

function Purchases({ selId, setSelId, toast }) {
  const [list, setList] = useState([])
  const [grn, setGrn] = useState(null)
  const [q, setQ] = useState('')
  const [opts, setOpts] = useState({})             // attribute option lists (phone app's)
  const [cats, setCats] = useState([])             // category master names
  const [splitFor, setSplitFor] = useState(null)   // line id whose breakdown is open
  const [srows, setSrows] = useState([])           // editable variant rows
  const [shortFor, setShortFor] = useState(null)   // line id whose shortage is open
  const [shrows, setShrows] = useState([])         // editable shortage rows
  const [shortOpts, setShortOpts] = useState({ reasons: [] })
  const refresh = useCallback(() => api.listPurchases().then(setList), [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => { if (selId) api.getPurchase(selId).then(setGrn); else setGrn(null) }, [selId])
  useEffect(() => { api.productOptions().then(setOpts).catch(() => {}) }, [])
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
      toast(`✓ Posted to inventory · ${r.products_created} new, ${r.products_updated} updated${sizes}${short}`, 'ok')
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
    const from = (s) => {
      const r = blankVariant(l.rate)
      Object.keys(r).forEach((k) => { if (s[k] != null) r[k] = s[k] })
      return r
    }
    // a new row inherits the line's category (or the mapping it would get), so the
    // common case — one category, several sizes — needs no repetition
    setSrows(l.splits.length ? l.splits.map(from)
      : [blankVariant(l.rate, l.category || l.category_suggestion?.best)])
  }
  const setLineCat = async (l, name) => {
    try { await api.editLine(l.id, { category: name }); await reload() }
    catch (e) { toast(e.detail || 'Could not set the category', 'err') }
  }
  const updSrow = (i, k, v) => setSrows(srows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  const splitSum = srows.reduce((s, r) => s + (+r.qty || 0), 0)
  const saveSplit = async (l, rows) => {
    try {
      await api.setLineSplits(l.id, rows)
      setSplitFor(null); setSrows([])
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
  const [pcat, setPcat] = useState({})             // in-progress category per line id
  return (
    <div className="body">
      <div className="sidebar">
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
          {shown.map((p) => (
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
      </div>
      {grn ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="editor">
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
              <h2 style={{ margin: 0 }}>{grn.supplier_name}</h2>
              <span className={'badge ' + (grn.status === 'posted' ? 'confirmed' : 'uploaded')}>{grn.status}</span>
            </div>
            <div className="kv" style={{ margin: '12px 0 20px', gridTemplateColumns: '130px 1fr 130px 1fr' }}>
              <div className="k">GRN No</div><div>{grn.grn_no || '—'}</div>
              <div className="k">Invoice</div><div>{grn.invoice_number} · {grn.invoice_date}</div>
              <div className="k">Taxable</div><div>₹ {money(grn.taxable_total)}</div>
              <div className="k">Grand total</div><div>₹ {money(grn.grand_total)}</div>
            </div>
            <Section id="grn.lines" title="Lines → inventory match"
              summary={`${grn.lines.length} line(s) · ${grn.new_products} new product(s)`}>
              <div className="small" style={{ margin: '-6px 0 10px', color: 'var(--muted)' }}>
                A bundle line (e.g. 250 pcs billed as one row) can be <b>broken down by
                attributes</b> — size, colour, material, pattern, fit, type, design no. Each
                distinct combination becomes its own product with its own QR, priced and
                dispatched on its own. Set <b>Category</b> here and the products are created
                already mapped, instead of arriving “unmapped” in Inventory.
                {' '}Anything billed that <b>wasn’t in the box</b> goes under <b>Shortage</b> —
                it stays out of stock, the invoice keeps its own quantity, and the gap becomes
                a claim the debit note is built from.
              </div>
              {/* the category master, shared by the line cells and the breakdown editor */}
              <datalist id="essa-cats">{cats.map((c) => <option key={c} value={c} />)}</datalist>
              <table className="items">
                <thead><tr><th>Product</th><th>QR code</th><th>Description</th>
                  <th style={{ minWidth: 150 }}>Category</th><th>HSN</th>
                  <th style={{ textAlign: 'right' }} title="What the supplier invoiced">Billed</th>
                  <th style={{ textAlign: 'right' }} title="What actually came out of the boxes — this is what becomes stock">Received</th>
                  <th style={{ textAlign: 'right' }}>Rate</th>
                  <th style={{ textAlign: 'right' }}>Amount</th><th>Match</th>
                  {editable && <th></th>}</tr></thead>
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
                        <td style={{ textAlign: 'right' }}>{money(l.rate)}</td>
                        <td style={{ textAlign: 'right' }}>{money(l.amount)}</td>
                        <td>{l.splits.length
                          ? <span className={'badge ' + (l.split_balanced ? 'confirmed' : 'review')}
                              title={(l.split_balanced ? 'Breakdown adds up to what was received'
                                : `${l.split_remainder} of ${receivedQty(l)} received not yet broken down`)
                                + ' — this bundle line does not receive stock itself; the rows below do'}>
                              split · {l.splits.length}{l.split_balanced ? '' : ' ⚠'}</span>
                          : <span className={'badge ' + (l.is_new_product ? 'review' : 'confirmed')}>
                              {l.is_new_product ? 'new' : 'matched'}</span>}</td>
                        {editable && (
                          <td style={{ whiteSpace: 'nowrap' }}>
                            <button className="btn" style={{ padding: '2px 8px' }} onClick={() => (splitFor === l.id ? setSplitFor(null) : openSplit(l))}
                              title="Break the bundle into what actually arrived — size, colour, material…">
                              {splitFor === l.id ? 'Close' : l.splits.length ? 'Edit breakdown' : 'Break down'}</button>
                            <button className="btn" style={{ padding: '2px 8px', marginLeft: 4 }}
                              onClick={() => (shortFor === l.id ? setShortFor(null) : openShortage(l))}
                              title="Record what the supplier billed and the boxes didn't hold — it stays out of stock and becomes a claim">
                              {shortFor === l.id ? 'Close' : l.has_shortage ? '⚠ Shortage' : 'Shortage'}</button>
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
                          <td style={{ textAlign: 'right' }}>{money(s.rate)}</td>
                          <td style={{ textAlign: 'right' }}>{s.claimable ? money(s.amount) : '—'}</td>
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

                      {/* saved variant rows — one product each once posted */}
                      {splitFor !== l.id && l.splits.map((s) => (
                        <tr key={s.id} style={{ background: 'var(--panel-2)' }}>
                          <td className="mono" style={{ color: 'var(--muted)' }}>{s.product_sku || '—'}</td>
                          <td className="mono">{s.product_barcode || s.code || <span style={{ color: 'var(--muted)' }}>on post</span>}</td>
                          <td style={{ paddingLeft: 22 }}>↳ <b>{s.label}</b>
                            {s.mrp != null && <span className="small" style={{ marginLeft: 8, color: 'var(--muted)' }}>MRP {money(s.mrp)}</span>}
                            {s.sale_price != null && <span className="small" style={{ marginLeft: 8, color: 'var(--muted)' }}>sale {money(s.sale_price)}</span>}
                            {s.sale_discount_pct != null && <span className="small" style={{ marginLeft: 8, color: 'var(--muted)' }}>−{s.sale_discount_pct}%</span>}</td>
                          <td className="mono" style={{ fontSize: 11 }}>{s.category || l.category
                            || <span style={{ color: 'var(--muted)' }}>auto</span>}</td>
                          <td>{l.hsn}</td>
                          {/* a variant row IS a received quantity — the supplier never
                              billed it separately, so there is nothing under "Billed" */}
                          <td style={{ textAlign: 'right', color: 'var(--muted)' }}>—</td>
                          <td style={{ textAlign: 'right' }}>{s.qty}</td>
                          <td style={{ textAlign: 'right' }}>{money(s.rate)}</td>
                          <td style={{ textAlign: 'right' }}>{money(s.amount)}</td>
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
                            <td colSpan={editable ? 11 : 10} style={{ background: 'var(--warn-bg)', padding: '12px 14px' }}>
                              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
                                <b>Shortage on “{l.description}”</b>
                                <span className="small" style={{ color: over ? 'var(--danger)' : missing ? 'var(--warn)' : 'var(--muted)' }}>
                                  {over ? `${missing} short of only ${l.qty} billed — more than the invoice`
                                    : <>Into stock <b>{recv}</b> of {l.qty} billed
                                      {missing ? ` · ${missing} short — ₹ ${money(missing * (+l.rate || 0))} to claim` : ''}
                                      {extra ? ` · ${extra} extra` : ''}</>}
                                </span>
                              </div>
                              <div className="small" style={{ color: 'var(--muted)', marginBottom: 10 }}>
                                Normally keyed on the phone by whoever opens the cartons — they are the
                                only ones who can know it. Recorded here, the missing units stay <b>out of
                                stock</b>, the invoice keeps its own quantity so payables still reconcile,
                                and the gap becomes a claim the debit note is built from.
                              </div>
                              <table className="items" style={{ margin: 0 }}>
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
                              <datalist id="essa-short-reasons">
                                {(shortOpts.reasons || []).map((v) => <option key={v} value={v} />)}
                              </datalist>
                              <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
                                <button className="btn" onClick={() => setShrows([...shrows, blankShortage()])}>+ add row</button>
                                <div style={{ flex: 1 }} />
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
                          <td colSpan={editable ? 11 : 10} style={{ background: 'var(--panel-2)', padding: '12px 14px' }}>
                            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
                              <b>Breakdown of “{l.description}”</b>
                              {/* the target is what ARRIVED — once a shortage is recorded the
                                  rows only have to reach that, which is the point of recording it */}
                              <span className="small" style={{ color: sameQty(splitSum, receivedQty(l)) ? 'var(--ok)' : 'var(--muted)' }}>
                                {splitSum} of {receivedQty(l)} assigned
                                {l.has_shortage ? ` (${l.qty} billed, ${l.missing_qty} short)` : ''}
                                {sameQty(splitSum, receivedQty(l)) ? ' ✓' : ` · ${round3(receivedQty(l) - splitSum)} left`}
                              </span>
                              <span className="small" style={{ color: 'var(--muted)' }}>
                                — fill only the attributes that differ; each row becomes one product
                              </span>
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
                            <div style={{ overflowX: 'auto' }}>
                              <table className="items" style={{ margin: 0, minWidth: 1340 }}>
                                <thead><tr>
                                  {SPLIT_ATTRS.map(([k, label, w]) => <th key={k} style={{ minWidth: w }}>{label}</th>)}
                                  <th style={{ minWidth: 150 }}>Category</th>
                                  {SPLIT_MONEY.map(([k, label, w]) => <th key={k} style={{ minWidth: w, textAlign: 'right' }}>{label}</th>)}
                                  <th></th></tr></thead>
                                <tbody>{srows.map((r, i) => (
                                  <tr key={i}>
                                    {SPLIT_ATTRS.map(([k]) => (
                                      <td key={k}><input list={'essa-opt-' + k} value={r[k]}
                                        onChange={(e) => updSrow(i, k, e.target.value)} /></td>
                                    ))}
                                    <td><input list="essa-cats" className="mono" style={{ fontSize: 11 }}
                                      placeholder={l.category || 'auto'} value={r.category}
                                      onChange={(e) => updSrow(i, 'category', e.target.value)} /></td>
                                    {SPLIT_MONEY.map(([k]) => (
                                      <td key={k} className="num"><input value={r[k]}
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
                            <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
                              <button className="btn" onClick={() => setSrows([...srows,
                                blankVariant(l.rate, srows[srows.length - 1]?.category || l.category || l.category_suggestion?.best)])}>+ add row</button>
                              <button className="btn" title="Copy the last row's attributes into a new row — change just what differs"
                                disabled={!srows.length}
                                onClick={() => setSrows([...srows, { ...srows[srows.length - 1], qty: '' }])}>⧉ duplicate last</button>
                              <div style={{ flex: 1 }} />
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
                ? `⚠ ${unbalanced} line(s) have a breakdown that doesn’t add up to what was received — fix them, or record what didn’t arrive as a shortage.`
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
  ['size', 'Size'], ['color', 'Colour'], ['material', 'Material'], ['pattern', 'Pattern'],
  ['fit', 'Fit'], ['product_type', 'Type'], ['design_no', 'Design No'],
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
    try { const p = await api.lookupByCode(c); await open(p.id); setScan(''); toast(`✓ ${p.sku} · ${p.description}`, 'ok') }
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
  const printLabels = () => {
    if (!products.length) { toast('No products yet — post a GRN to create products first.', 'err'); return }
    if (labelCount === 0) { toast('No detailed products yet. Detail products first, or switch to “All products”.', 'err'); return }
    window.open(api.labelsUrl(null, labelScope), '_blank')
  }
  return (
    <div className="body">
      <div style={{ flex: 1, overflowY: 'auto', padding: 22 }}>
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
            <div className="small" style={{ color: 'var(--muted)', margin: '-4px 0 10px' }}>
              Stock is only ever created by posting a GRN, so a record that traces back to
              no posted GRN is not stock. These are hidden from Inventory, excluded from the
              valuation and blocked from printing labels — a stale code carries a real-looking
              QR and scans like any other, so it can’t be left in circulation.
            </div>
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
        <table className="items">
          <thead><tr><th style={{ width: 46 }}>QR</th><th>SKU</th><th>Description</th><th>Size</th><th>Category</th><th>HSN</th><th>Supplier</th>
            <th style={{ textAlign: 'right' }}>Stock</th><th style={{ textAlign: 'right' }}>Avg cost</th>
            <th style={{ textAlign: 'right' }}>Value</th></tr></thead>
          <tbody>
            {visible.map((p) => (
              <tr key={p.id} style={{ cursor: 'pointer', background: detail?.id === p.id ? 'var(--panel-2)' : '' }} onClick={() => open(p.id)}>
                {/* The real QR, small enough for a list and still scannable off the
                    screen. `lazy` keeps a long list from firing a request per row. */}
                <td style={{ padding: 2 }}>
                  <img src={api.qrSvgUrl(p.id, 2)} alt={`QR ${p.sku}`} loading="lazy"
                    title={`Scan or click to open ${p.sku}`}
                    style={{ width: 34, height: 34, display: 'block', background: '#fff', borderRadius: 3, padding: 1 }} />
                </td>
                <td className="mono" style={{ color: 'var(--muted)' }}>{p.sku}</td>
                <td>{p.description}</td>
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
              <span>{units.product.description}</span>
              <span className="small">
                {[units.product.size, units.product.color].filter(Boolean).join(' · ')}
              </span>
              <span style={{ marginLeft: 'auto' }} className="small">
                {units.count} piece{units.count === 1 ? '' : 's'} of {units.product.stock_qty} {units.product.uom} in stock
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
                  {units.units.map((u) => {
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
              <div className="k">Description</div><div><b>{detail.description}</b></div>
              <div className="k">HSN</div><div>{detail.hsn || '—'}</div>
              <div className="k">UOM</div><div>{detail.uom || '—'}</div>
              <div className="k">MRP</div><div>{detail.mrp != null ? '₹ ' + money(detail.mrp) : '—'}</div>
              <div className="k">Category</div>
              <div>{detail.category
                ? <>{detail.category}{detail.category_section && <span style={{ color: 'var(--muted)' }}> · {detail.category_section}</span>}</>
                : <span className="badge review" title="No confident match from the description — set it on the GRN line and re-post">unmapped</span>}</div>
              <div className="k">Supplier</div><div>{detail.supplier_name || '—'}</div>
            </div>
            <div className="small" style={{ color: 'var(--muted)', margin: '8px 0 0' }}>
              Set on the invoice and the <b>GRN</b>. To correct any of it, open the GRN,
              press <b>↺ Unpost</b>, fix the line and post again.
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
            <div className="small" style={{ color: 'var(--muted)', marginBottom: 4 }}>
              Set on the <b>GRN breakdown</b> when the goods are received, and updated in the
              phone app{detail.detailed_by ? <> — last detailed by <b>{detail.detailed_by}</b></> : ''}.
            </div>

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
                    Supplier's printed code: <span className="mono">{detail.barcode}</span> — kept so a
                    re-buy matches this product, not printed on our label.
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
              <p className="small">The SKU and its QR are assigned automatically once all product details are set (via the mobile detail form), or generate them manually after detailing.</p>
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
                <div className="k">By</div><div>{detail.detailed_by || '—'}{detail.detailed_at ? ' · ' + detail.detailed_at.slice(0, 10) : ''}</div>
              </div>
            ) : <p className="small">Not yet detailed. Use the ESSA Warehouse mobile app to record color, size, material, MRP, sale price, etc.</p>}

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
      b.invoice_number ? `Invoice ${b.invoice_number}${b.invoice_date ? ' · ' + b.invoice_date : ''}` : null,
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
        <div className="piece-foot">
          <span className="small">Scanning this QR anywhere in the app resolves to this product — it carries
            the whole record, so it reads with no network too.</span>
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
  const [zoom, setZoom] = useState(null)          // a product card, opened large
  const [cards, setCards] = useState({})          // product_id -> full record, for the draft rows
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

  const addLine = () => setForm({ ...form, lines: [...form.lines, { product_id: '', qty: 1 }] })
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
      <div className="sidebar">
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
          {shown.map((o) => (
            <div key={o.id} className={'doc-row' + (sel === o.id && !creating ? ' sel' : '')} onClick={() => { setSel(o.id); setCreating(false) }}>
              <div className="t">{o.to_destination || o.code}</div>
              <div className="m"><span className={'badge ' + (o.status === 'posted' ? 'confirmed' : 'uploaded')}>{o.status}</span>
                <span>{o.code}</span><span style={{ marginLeft: 'auto' }}>{o.total_qty} units</span></div>
            </div>
          ))}
        </div>
      </div>
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
              <div className="small" style={{ margin: '-6px 0 10px', color: 'var(--muted)' }}>
                Scan the garment’s QR (or a piece label) to add it — the full record
                appears below, so the size and colour going into the box are the ones
                on the note. Scanning the same item again adds one more.
              </div>
              <ScanBox onScan={addScanned} placeholder="Scan a QR / piece label / SKU to add…" />
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
                          {p.description}{p.size ? ' · ' + p.size : ''}{p.color ? ' · ' + p.color : ''} (stock {p.stock_qty})</option>)}
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
              <button className="btn" style={{ marginTop: 8 }} onClick={addLine}>+ add item</button>
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
              <div className="k">Code</div><div>{detail.code}</div><div className="k">Date</div><div>{detail.date || '—'}</div>
              <div className="k">From</div><div>{detail.from_location}</div><div className="k">Packed by</div><div>{detail.packed_by || '—'}</div>
              {detail.status === 'received' && <>
                <div className="k">Received by</div><div>{detail.received_by || '—'}</div>
                <div className="k">Received on</div><div>{detail.received_date || (detail.received_at || '').slice(0, 10) || '—'}</div>
              </>}
            </div>
            {detail.status !== 'draft' && (
              <div style={{ marginBottom: 14 }}>
                <ScanBox onScan={verify} label="Verify"
                  placeholder="Scan a garment to check it belongs to this dispatch…" />
              </div>
            )}
            <Section id="outward.items" title="Items">
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
      <div className="sidebar">
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
          {list.filter((o) => matches(o, q, ['to_destination', 'code', 'status'])).map((o) => (
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
      </div>
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
              <div className="k">Dispatched</div><div>{detail.date || (detail.posted_at || '').slice(0, 10) || '—'}</div>
              <div className="k">From</div><div>{detail.from_company} · {detail.from_location}</div>
              <div className="k">Packed by</div><div>{detail.packed_by || '—'}</div>
              {detail.status === 'received' && <>
                <div className="k">Received by</div><div>{detail.received_by || '—'}</div>
                <div className="k">Received on</div><div>{detail.received_date || (detail.received_at || '').slice(0, 10) || '—'}</div>
              </>}
            </div>
            {editable && (
              <div style={{ marginBottom: 14 }}>
                <ScanBox onScan={scan} label="Count in"
                  placeholder="Scan each garment as it comes out of the box…" />
                <div className="small" style={{ color: 'var(--muted)', marginTop: 6 }}>
                  Every line is accepted in full unless you say otherwise — scan or type
                  a lower figure for anything that didn’t turn up.
                </div>
              </div>
            )}
            <Section id="inward.lines" title={editable ? 'Check the goods in' : 'Goods received'}>
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
              <span className="small">A shortfall is recorded as a transfer discrepancy — the stock already
                left the warehouse, so settle it with a stock adjustment once it is traced.</span>
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
      <div className="val">{value}</div>
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
      <div className="sidebar">
        <div className="head"><h3>Suppliers · payables</h3></div>
        <SearchBox value={q} onChange={setQ} placeholder="Search supplier, GSTIN…" />
        <div className="list">
          {suppliers.filter((s) => matches(s, q, ['name', 'gstin'])).map((s) => (
            <div key={s.id} className={'sup-row' + (sel === s.id ? ' sel' : '')} onClick={() => loadSupplier(s.id)}>
              <div className="t">{s.name}</div>
              <div className="m"><span>{s.document_count} bill(s)</span></div>
            </div>
          ))}
        </div>
      </div>
      {sel ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="editor">
            {ledger && <div style={{ display: 'flex', gap: 14, marginBottom: 18 }}>
              <Stat label="Outstanding" value={'₹ ' + money(ledger.outstanding)} />
              <Stat label="Pending bills" value={bills.length} />
            </div>}
            <Section id="pay.pending-bills" title="Pending bills — select, then set cash / discount / TDS / debit">
              {bills.length === 0 ? <p className="small">No outstanding invoices for this supplier.</p> : (
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
                        <td className="mono">{b.invoice_number}</td><td>{b.invoice_date}</td>
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
                  <tr key={i}><td>{r.date || '—'}</td><td>{r.type}</td>
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
  const draftTotal = detail ? detail.lines.reduce((s, l) => s + (+qtys[l.id] || 0) * (l.rate || 0), 0) : 0

  return (
    <div className="body">
      <div className="sidebar">
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
          {shown.map((r) => (
            <div key={r.id} className={'doc-row' + (detail?.id === r.id && !picking ? ' sel' : '')} onClick={() => openReturn(r.id)}>
              <div className="t">{r.supplier_name}</div>
              <div className="m"><span className={'badge ' + (r.status === 'posted' ? 'confirmed' : 'uploaded')}>{r.status}</span>
                <span>{r.code}</span><span style={{ marginLeft: 'auto' }}>₹ {money(r.total)}</span></div>
              <div className="m"><span>vs {r.invoice_number}</span></div>
            </div>
          ))}
        </div>
      </div>
      {picking ? (
        <div className="editor">
          <h2 style={{ marginTop: 0 }}>New Purchase Return — pick a reference invoice</h2>
          <div className="small" style={{ margin: '-6px 0 12px', color: 'var(--muted)' }}>
            An invoice with goods <b>short at receiving</b> can be claimed on its own — the
            quantities were counted when the boxes were opened, so that debit note writes
            itself and nobody counts again.
          </div>
          <table className="items"><thead><tr><th>Supplier</th><th>Invoice</th><th>Date</th>
            <th style={{ textAlign: 'right' }}>Grand total</th>
            <th style={{ textAlign: 'right' }}>Short</th><th></th></tr></thead>
            <tbody>{purchases.map(p => (
              <tr key={p.id}><td>{p.supplier_name}</td><td className="mono">{p.invoice_number}</td>
                <td>{p.invoice_date}</td><td style={{ textAlign: 'right' }}>₹ {money(p.grand_total)}</td>
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
                <span className="small" style={{ color: 'var(--muted)', marginLeft: 8 }}>
                  what the supplier billed us for these goods — not the MRP or the sale price,
                  so the debit reconciles against their invoice.
                </span>
              </div>
            </div>
            <Section id="return.lines" title={editable ? 'Set return quantity per line' : 'Returned lines'}>
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
              <div className="items-foot">
                <span>{detail.lines.length - (detail.shortage_lines || 0)} received item(s)</span>
                {detail.shortage_lines > 0 && (
                  <span style={{ color: 'var(--warn)' }}>
                    ⚠ {detail.shortage_lines} shortage claim(s) · goods that never arrived, already counted
                    at the dock — posting these reduces the payable and moves no stock
                  </span>
                )}
                <span>a bundle broken down at GRN comes back as its variants — each at its own received cost</span>
              </div>
            </Section>
          </div>
          {editable && (
            <div className="actionbar">
              <div className="field" style={{ width: 180 }}><label>Reason</label><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. damaged / wrong item" /></div>
              <DateField label="Date" width={150} value={date} onChange={setDate} />
              <div className="spacer" />
              <span className="small">Posting reverses stock and raises a debit note against the invoice,
                valued at the GRN cost of each item.</span>
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

function Reports() {
  const [cat, setCat] = useState([])
  const [groups, setGroups] = useState([])
  const [key, setKey] = useState(null)
  const [rep, setRep] = useState(null)
  const [q, setQ] = useState('')
  const [filters, setFilters] = useState({})     // the values behind a report's params
  const [busy, setBusy] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)
  useEffect(() => {
    api.reportGroups().then(setGroups).catch(() => {})
    api.reportCatalogue().then((c) => { setCat(c); if (c[0]) pick(c[0].key) })
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
  const pick = (k) => { setKey(k); setRep(null); setQ(''); load(k, filters) }
  const setFilter = (p, v) => {
    const next = { ...filters, [p]: v }
    setFilters(next)
    load(key, next)
  }
  const rows = rep ? rep.rows.filter((row) => !q || rep.columns.some((c) => String(row[c] ?? '').toLowerCase().includes(q.toLowerCase()))) : []
  const grouped = cat.reduce((a, r) => { (a[r.group] = a[r.group] || []).push(r); return a }, {})
  const order = groups.length ? groups : Object.entries(REPORT_GROUPS).map(([k2, n]) => ({ key: k2, name: n }))
  const dateParams = (entry?.params || []).filter((p) => PARAM_LABEL[p])
  const fmt = (v) => typeof v === 'number' ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : (v ?? '')
  return (
    <div className="body">
      <div className="sidebar">
        <div className="head"><h3>Reports · {cat.length}</h3></div>
        <div className="list" style={{ padding: '6px 0' }}>
          {order.filter((g) => grouped[g.key]?.length).map((g) => (
            <div key={g.key}>
              <div style={{ padding: '10px 14px 4px', fontSize: 11, textTransform: 'uppercase', color: 'var(--muted)', letterSpacing: '.5px' }}>
                {g.name} <span style={{ opacity: 0.6 }}>({grouped[g.key].length})</span>
              </div>
              {grouped[g.key].map(r => (
                <div key={r.key} className={'doc-row' + (key === r.key ? ' sel' : '')} style={{ padding: '8px 14px' }} onClick={() => pick(r.key)}>
                  <div className="t" style={{ fontWeight: key === r.key ? 700 : 400 }}>{r.name}</div>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {rep ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', padding: '14px 22px', borderBottom: '1px solid var(--line)', flexWrap: 'wrap', gap: 8 }}>
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
                  a different set of rows than the one on screen */}
              <a className="btn" href={api.reportCsvUrl(key, active())} target="_blank"
                rel="noreferrer" title="Download exactly these rows, with these filters">Export CSV</a>
            </div>
            {dateParams.length > 0 && (
              <div style={{ padding: '0 22px' }}>
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
            <div style={{ flex: 1, overflow: 'auto', padding: '0 22px 22px' }}>
              {rep.note && (
                <div className="small" style={{ padding: '10px 0 2px', color: 'var(--muted)' }}>
                  ⓘ {rep.note}
                </div>
              )}
              <div className="small" style={{ padding: '8px 0', color: 'var(--muted)' }}>
                {busy ? 'running…' : <>{rows.length} of {rep.rows.length} rows{q ? ` matching “${q}”` : ''}</>}</div>
              <table className="items">
                <thead><tr>{rep.columns.map(c => <th key={c} style={{ textAlign: typeof rep.rows[0]?.[c] === 'number' ? 'right' : 'left' }}>{c.replace(/_/g, ' ')}</th>)}</tr></thead>
                <tbody>{rows.map((row, i) => (
                  <tr key={i}>{rep.columns.map(c => (
                    <td key={c} className={typeof row[c] === 'number' ? 'mono' : ''} style={{ textAlign: typeof row[c] === 'number' ? 'right' : 'left' }}>
                      {typeof row[c] === 'number' ? fmt(row[c]) : (row[c] || '')}</td>
                  ))}</tr>
                ))}</tbody>
              </table>
              {rep.rows.length === 0 && <p className="empty" style={{ marginTop: 40 }}>No data yet.</p>}
            </div>
          </>
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
    } catch (e) { toast('Failed: ' + e.message, 'err') }
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
        <p className="small" style={{ marginTop: 0 }}>
          With a vision model on, new invoice uploads are read directly from the image —
          far better on photos, skew and handwriting than the offline OCR fallback.
        </p>

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
            <div className="flagnote" style={{ color: 'var(--muted)' }}>Pick a model your key supports. A wrong model causes "NotFoundError → fell back to OCR".</div>
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button className="btn primary" disabled={busy} onClick={activate}>
            {busy ? 'Checking…' : 'Activate vision'}</button>
          {on && <button className="btn" disabled={busy} onClick={turnOff}>Turn off</button>}
          <div className="spacer" style={{ flex: 1 }} />
          <span className="small">Key is validated with Anthropic, stored locally, never displayed.</span>
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
// [key, label, type, opts]. `req` marks the boxes the server also enforces
// (REQUIRED_MANUAL in routers/lr.py); `list` names a master dropdown, `src` a
// master with its own table. combo = dropdown you can also type a new value into.
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
  ['stock_holding_days', 'Stock Holding Period (days)', 'num', {}],
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
  ['freight_amount', 'Fright Charge', 'charge', { flag: 'freight_applicable' }],
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
    </div>
  )
}

const today = () => new Date().toISOString().slice(0, 10)
// A fresh form: the two dates default to today, exactly as their screen does.
const blankLR = () => ({ lr_entry_date: today(), lr_date: today(), recv_date: today(),
  auto_transfer_location: 'NONE', freight_applicable: false })

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
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '0 28px' }}>
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
        <span className="small" style={{ color: 'var(--muted)' }}>
          LR copy, weight slip, a photo of damaged bundles — kept against this consignment.
        </span>
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
const LR_SEARCH_FIELDS = [
  ['q', 'LR / Invoice / Entry no / item', 240], ['supplier', 'Supplier', 160],
  ['transport', 'Transport', 130],
]
function LRSearchPanel({ onResults, onClear, toast }) {
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
        {LR_SEARCH_FIELDS.map(([k, label, w]) => (
          <div key={k} className="field" style={{ width: w }}><label>{label}</label>
            <input value={f[k] || ''} onChange={(e) => set(k, e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') run() }} /></div>
        ))}
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
  ['item', 'Item', 110],
]
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
  { k: 'item', h: 'Item', w: 92 },
  { k: 'bundle', h: 'Bdl / Box', w: 68, num: 1, pair: 'boxes' },
  { k: 'qty', h: 'Pieces', w: 58, num: 1 },
  { k: 'amount', h: 'Goods Value', w: 88, num: 1 },
  { k: 'paid_topay', h: 'Paid/ToPay', w: 84, edit: 1 },
  { k: 'freight_amount', h: 'Freight', w: 78, num: 1, edit: 1 },
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
    } catch (err) { toast('Extract failed: ' + err.message, 'err'); setNote('') }
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
    } catch (err) { toast('Could not save: ' + err.message, 'err'); drop(key) }
  }

  const toSave = rows.filter(r => !isExact(r))
  const nDoubtful = rows.filter(isDoubtful).length
  const qtySum = toSave.reduce((s, x) => s + (+x.qty || 0), 0)
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      {/* the subtitle yields before any control does — a clipped button is a
          control someone cannot reach, a clipped sentence is only a shorter one */}
      <div className="pagehead">
        <h2>LR Entry</h2>
        <span className="small pagesub">Import a register page and the rows are read automatically — or key one consignment in.</span>
        <div style={{ flex: 1 }} />
        <button className="btn" onClick={() => setSearching((s) => !s)}
          title="Find entries by LR / invoice number, supplier, date, rack…">🔍 Search</button>
        <button className="btn" onClick={openNew}>📄 New entry</button>
        <label className="btn primary uploadbtn">{busy ? 'Reading…' : 'Import LR image / PDF'}
          <input type="file" accept="image/*,.pdf" onChange={onFile} disabled={busy} /></label>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 22 }}>
        {form !== null && (
          <LREntryForm editing={form.id ? form : null} opts={opts} lists={lists}
            onDone={afterSave} onCancel={() => setForm(null)} toast={toast} reloadOpts={loadOpts} />
        )}
        {searching && (
          <LRSearchPanel toast={toast} onResults={setFound} onClear={() => setFound(null)} />
        )}
        {found && (
          <div className="warnbox clean" style={{ marginBottom: 14 }}>
            <h4 style={{ border: 'none', margin: 0 }}>
              {found.count} matching entr{found.count === 1 ? 'y' : 'ies'}
              {found.shown < found.count ? ` (showing ${found.shown})` : ''} · Σ pieces <b>{found.totals.qty}</b>
              {' · '}Σ bundles <b>{found.totals.bundle}</b>{' · '}Σ boxes <b>{found.totals.boxes}</b>
              {' · '}Σ goods value <b>₹ {money(found.totals.amount)}</b>
              {' · '}Σ freight <b>₹ {money(found.totals.freight_amount)}</b>
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
              <div style={{ overflowX: 'auto' }}>
                <table className="items" style={{ minWidth: 1560 }}>
                  <thead><tr><th style={{ minWidth: 70 }}>Status</th>{LR_COLS.map(([k, l, w]) => <th key={k} style={{ minWidth: w }}>{l}</th>)}<th></th></tr></thead>
                  <tbody>{rows.map((r, i) => (
                    <tr key={i} style={isExact(r) ? { background: 'var(--danger-bg)', opacity: 0.6 }
                      : isDoubtful(r) ? { background: 'var(--warn-bg)' } : undefined}>
                      <td style={{ whiteSpace: 'nowrap', fontSize: 11, fontWeight: 600 }}>
                        {isExact(r) ? <span style={{ color: 'var(--danger)' }} title="Identical to an existing row — will be skipped">🚫 duplicate</span>
                          : isDoubtful(r) ? <span style={{ color: 'var(--warn)' }}
                              title={'Same LR/Invoice, but these differ from the saved row:\n' +
                                (r._diffs || []).map(f => `${f}: saved “${r._conflict_with?.[f] ?? ''}” vs this “${r[f] ?? ''}”`).join('\n')}>⚠ verify</span>
                          : <span style={{ color: 'var(--ok)' }}>new</span>}
                      </td>
                      {LR_COLS.map(([k]) => {
                        const changed = isDoubtful(r) && (r._diffs || []).includes(k)
                        return <td key={k} style={changed ? { background: 'var(--warn-line)' } : undefined}
                          title={changed ? `Saved row has: ${r._conflict_with?.[k] ?? '(blank)'}` : undefined}>
                          {/* vision reads a register page's dates in whatever the page
                              used; the picker both corrects them and normalises them */}
                          {LR_DATE_COLS.has(k)
                            ? <DateField inline value={r[k]} onChange={(v) => upd(i, k, v)} />
                            : <input value={r[k] ?? ''} onChange={(e) => upd(i, k, e.target.value)} />}</td>
                    })}<td><button className="btn" style={{ padding: '2px 7px' }} onClick={() => del(i)}>×</button></td></tr>
                  ))}</tbody>
                </table>
              </div>
              <div className="items-foot"><span>{toSave.length} to save{nDoubtful ? ` (incl. ${nDoubtful} to verify)` : ''}{rows.length !== toSave.length ? ` · ${rows.length - toSave.length} exact dup skipped` : ''}</span><span>Σ qty <b>{qtySum}</b></span>
                <button className="btn primary" style={{ marginLeft: 'auto' }} onClick={save}>Save {toSave.length} Entr{toSave.length === 1 ? 'y' : 'ies'}</button></div>
            </Section>
          </>
        )}
        {shown.length > 0 && (
          <Section id="lr.saved" title={`${found ? 'Search results' : 'Saved LR entries'} · ${shown.length}`} summary={`${shown.length} row(s)`}>
            <div className="small" style={{ margin: '-6px 0 10px', color: 'var(--muted)' }}>
              Paid/ToPay and Freight are editable in place — complete or correct them when the lorry
              delivers and the money changes hands. <b>Open</b> any row to edit the whole entry or
              attach the LR copy.
              <b> Received by</b> comes from the warehouse phone app (<span className="mono">/m</span> →
              Consignments), where whoever takes the packages in records their name.
            </div>
            <div style={{ overflowX: 'auto' }}>
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
                <tbody>{shown.map((r) => (
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
                        <td key={k} className={cls} title="Freight settlement — editable; saves when you leave the cell">
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
                        : (r[k] ?? '')
                      return (
                        <td key={k} className={cls}
                          style={m ? { background: 'var(--warn-bg)' } : undefined}
                          title={m ? `Register: ${m.register}\nInvoice: ${m.invoice}` : undefined}>
                          <div className="cellmain">{val}{m ? ' ⚠' : ''}</div>
                          {c.sub && r[c.sub] ? <div className="cellsub">{r[c.sub]}</div> : null}
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
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
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
      <p className="small">{blurb}{fixed ? '' : ' Values typed on the LR Entry form are remembered here automatically.'}</p>
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

function Masters({ toast }) {
  const [tab, setTab] = useState('categories')
  const [cats, setCats] = useState(null)
  const [q, setQ] = useState('')
  const [section, setSection] = useState('')
  const [agents, setAgents] = useState([])
  const [transports, setTransports] = useState([])
  const [opts, setOpts] = useState({})
  const loadOpts = useCallback(() => api.masterOptions().then(setOpts).catch(() => {}), [])
  useEffect(() => { api.categories().then(setCats); api.agents().then(setAgents); api.transports().then(setTransports); loadOpts() }, [loadOpts])
  const shown = cats ? cats.items.filter(c => (!section || c.section === section) && (!q || c.name.toLowerCase().includes(q.toLowerCase()))) : []
  const optTab = OPTION_TABS.find(([k]) => k === tab)
  return (
    <div className="body">
      <div className="sidebar">
        <div className="head"><h3>Masters</h3></div>
        <div className="list" style={{ padding: '6px 0' }}>
          {[['categories', `Product Categories · ${cats ? cats.count : '…'}`],
            ['agents', `Agents · ${agents.length}`],
            ['transports', `Transporters · ${transports.length}`],
            ...OPTION_TABS.map(([k, label]) => [k, `${label} · ${(opts[k] || []).length}`]),
          ].map(([k, label]) => (
            <div key={k} className={'doc-row' + (tab === k ? ' sel' : '')} style={{ padding: '11px 14px' }} onClick={() => setTab(k)}>
              <div className="t" style={{ fontWeight: tab === k ? 700 : 400 }}>{label}</div>
            </div>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 22 }}>
        {tab === 'categories' && (
          <>
            <div style={{ display: 'flex', gap: 10, marginBottom: 14, alignItems: 'center' }}>
              <h2 style={{ margin: 0 }}>Product Categories</h2>
              <span className="small">from GRN PRODUCT DETAILS.xlsx · {cats ? cats.count : 0} codes</span>
              <div style={{ flex: 1 }} />
              <select value={section} onChange={e => setSection(e.target.value)}
                style={{ background: 'var(--panel2)', color: 'var(--text)', border: '1px solid var(--line)', borderRadius: 8, padding: '8px' }}>
                <option value="">All sections</option>
                {(cats?.sections || []).map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <SearchBox value={q} onChange={setQ} placeholder="Search category…" style={{ width: 220 }} />
            </div>
            <div className="small" style={{ marginBottom: 8 }}>{shown.length} shown</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 8 }}>
              {shown.map(c => (
                <div key={c.id} style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, padding: '9px 12px' }}>
                  <span className="mono" style={{ color: 'var(--muted)', fontSize: 11 }}>{c.section}</span>
                  <div>{c.name}</div>
                </div>
              ))}
            </div>
          </>
        )}
        {tab === 'agents' && (
          <>
            <h2 style={{ marginTop: 0 }}>Agents</h2>
            <p className="small">Auto-created from the "agent" field on extracted invoices. {agents.length ? '' : 'None yet — confirm an invoice that names an agent.'}</p>
            {agents.map(a => <div key={a.id} style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, padding: '11px 14px', marginBottom: 7 }}>{a.name}{a.phone ? ' · ' + a.phone : ''}</div>)}
          </>
        )}
        {tab === 'transports' && (
          <>
            <h2 style={{ marginTop: 0 }}>Transporters</h2>
            <p className="small">Auto-created from the "transporter" field on extracted invoices/LRs.</p>
            {transports.map(t => <div key={t.id} style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, padding: '11px 14px', marginBottom: 7 }}>{t.name}{t.phone ? ' · ' + t.phone : ''}</div>)}
          </>
        )}
        {optTab && (
          <OptionList kind={optTab[0]} title={optTab[1]} blurb={optTab[2]} fixed={optTab[3]}
            values={opts[optTab[0]] || []} reload={loadOpts} toast={toast} />
        )}
      </div>
    </div>
  )
}

// ---------- app shell ----------
export default function App() {
  // the open tab survives a reload — a warehouse screen is left on the module
  // someone works in, and losing it on every refresh is a small daily tax
  const [tab, setTabState] = useState(() => localStorage.getItem('essa_tab') || 'lr')
  const setTab = (t) => { setTabState(t); try { localStorage.setItem('essa_tab', t) } catch { /* private mode */ } }
  const [status, setStatus] = useState(null)
  const [docs, setDocs] = useState([])
  const [sel, setSel] = useState(null)
  const [selPurchase, setSelPurchase] = useState(null)
  const [toastMsg, setToastMsg] = useState(null)
  const [showSettings, setShowSettings] = useState(false)
  const [scanning, setScanning] = useState(null)   // {url, name} while extracting
  const [docQuery, setDocQuery] = useState('')
  const [docScope, setDocScope] = useState('all')
  // scope chip + search, applied together — the same pairing every list uses
  const shownDocs = docs
    .filter((d) => docScope === 'all' || d.status === docScope)
    .filter((d) => matches(d, docQuery, ['supplier_name', 'filename', 'invoice_number', 'status']))
  const [authed, setAuthed] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [user, setUser] = useState('')
  const [role, setRole] = useState('')

  const refreshStatus = useCallback(() => api.status().then(setStatus), [])
  const refresh = useCallback(() => api.listDocuments().then(setDocs), [])

  // verify any stored token on load (so a refresh doesn't force re-login)
  useEffect(() => {
    const t = localStorage.getItem('essa_token')
    if (!t) { setAuthChecked(true); return }
    api.verifyToken(t).then((r) => {
      if (r.ok) { setAuthed(true); setUser(r.user); setRole(r.role || '') }
      else localStorage.removeItem('essa_token')
    }).catch(() => {}).finally(() => setAuthChecked(true))
  }, [])

  useEffect(() => { if (authed) { refreshStatus(); refresh() } }, [authed, refresh, refreshStatus])
  const toast = (m, kind) => { setToastMsg({ m, kind }); setTimeout(() => setToastMsg(null), 3000) }
  const handleLogin = (token, u, r) => { localStorage.setItem('essa_token', token); setUser(u); setRole(r || ''); setAuthed(true) }
  const logout = () => { localStorage.removeItem('essa_token'); setAuthed(false); setSel(null) }
  const gotoPurchase = (id) => { setSelPurchase(id); setTab('purchases') }

  const onUpload = async (e) => {
    const file = e.target.files[0]; if (!file) return
    // show the image being "scanned" while the backend extracts
    const url = file.type.startsWith('image/') ? URL.createObjectURL(file) : null
    setScanning({ url, name: file.name })
    setTab('documents')
    try {
      const res = await api.upload(file)
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
    } catch (err) { toast('Upload failed: ' + err.message, 'err') }
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

  if (!authChecked) return <div className="login-wrap"><div className="login-bg" /></div>
  if (!authed) return <LoginScreen onLogin={handleLogin} />

  return (
    <div className="app">
      {/* Brand and account actions on one row, navigation on its own below it.
          Eleven modules and five controls never fitted on a single line — the
          last button was clipped at 1600px — and squeezing them is the wrong
          trade: navigation is the most-used thing on the screen. */}
      <div className="topbar">
        <div className="brand">Essa <span>·</span> Document Intake<small>{status?.company?.name} — invoice → data, trained per supplier</small></div>
        <div className="spacer" />
        <button className={'pill ' + (providers.claude_vision ? 'on' : 'off')} style={{ cursor: 'pointer' }}
          title="Configure vision extraction" onClick={() => setShowSettings(true)}>
          👁 vision {providers.claude_vision ? 'on' : 'off'} ⚙</button>
        <span className={'pill ' + (providers.tesseract ? 'on' : 'off')}>OCR {providers.tesseract ? 'on' : 'off'}</span>
        <label className="btn primary uploadbtn">Upload invoice<input type="file" accept="image/*,.pdf" onChange={onUpload} /></label>
        <button className="btn" title={'Signed in as ' + user} onClick={logout}>Logout</button>
      </div>
      <div className="navbar">
        <div className="tabs">
          <button className={tab === 'lr' ? 'active' : ''} onClick={() => setTab('lr')}>LR Entry</button>
          <button className={tab === 'documents' ? 'active' : ''} onClick={() => setTab('documents')}>Invoice Entry</button>
          <button className={tab === 'purchases' ? 'active' : ''} onClick={() => setTab('purchases')}>GRN</button>
          <button className={tab === 'inventory' ? 'active' : ''} onClick={() => setTab('inventory')}>Inventory</button>
          <button className={tab === 'outward' ? 'active' : ''} onClick={() => setTab('outward')}>Stock Outward</button>
          <button className={tab === 'inward' ? 'active' : ''} onClick={() => setTab('inward')}>Stock Inward</button>
          <button className={tab === 'returns' ? 'active' : ''} onClick={() => setTab('returns')}>Returns</button>
          <button className={tab === 'payments' ? 'active' : ''} onClick={() => setTab('payments')}>Payments</button>
          <button className={tab === 'reports' ? 'active' : ''} onClick={() => setTab('reports')}>Reports</button>
          <button className={tab === 'suppliers' ? 'active' : ''} onClick={() => setTab('suppliers')}>Suppliers</button>
          {role === 'admin' && <button className={tab === 'masters' ? 'active' : ''} onClick={() => setTab('masters')}>Masters</button>}
        </div>
      </div>

      {tab === 'lr' ? (
        <div className="body"><LREntryView toast={toast} /></div>
      ) : tab === 'documents' ? (
        <div className="body">
          <div className="sidebar">
            <div className="head"><h3>Documents · {docs.length}</h3>
              {docs.length > 0 && <button className="btn" style={{ padding: '3px 9px', fontSize: 11 }}
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
              {shownDocs.map((d) => (
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
          </div>
          <Review docId={sel} onSaved={refresh} onCreateGrn={gotoPurchase} toast={toast} />
        </div>
      ) : tab === 'purchases' ? (
        <Purchases selId={selPurchase} setSelId={setSelPurchase} toast={toast} />
      ) : tab === 'inventory' ? (
        <Inventory toast={toast} />
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
        role === 'admin' ? <Masters toast={toast} /> : <div className="empty">Masters are admin-only.</div>
      ) : <Suppliers toast={toast} />}

      {scanning && <ScanningOverlay url={scanning.url} name={scanning.name}
        vision={!!providers.claude_vision} />}
      {showSettings && <VisionSettings onClose={() => setShowSettings(false)}
        onChanged={refreshStatus} toast={toast} />}
      {toastMsg && <div className={'toast ' + toastMsg.kind}>{toastMsg.m}</div>}
    </div>
  )
}
