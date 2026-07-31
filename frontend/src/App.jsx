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
function SearchBox({ value, onChange, placeholder, style }) {
  return (
    <div className="searchbox" style={style}>
      <span className="searchicon">⌕</span>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder || 'Search…'} />
      {value && <button className="searchclear" title="Clear" onClick={() => onChange('')}>×</button>}
    </div>
  )
}

// The ESSA-AI logo mark — the letters "AI" (rotating during the login intro)
function AiLogo({ size = 120 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="lg" x1="0" y1="0" x2="120" y2="120">
          <stop offset="0" stopColor="#4f8cff" /><stop offset="1" stopColor="#2fb6a3" />
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

function Field({ label, value, onChange, flagged, note, wide, source }) {
  return (
    <div className={'field' + (flagged ? ' flag' : '') + (source && !flagged ? ' fromlr' : '')}
      style={wide ? { gridColumn: '1 / -1' } : null}>
      <label>{label}</label>
      <input value={value ?? ''} onChange={(e) => onChange(e.target.value)} />
      {flagged && <div className="flagnote">⚠ needs review{note ? ' · ' + note : ''}</div>}
      {source && !flagged && <div className="srcnote">🔗 from {source}</div>}
    </div>
  )
}

// ---------- line items ----------
// full per-line field set; table scrolls horizontally so nothing is dropped
const ITEM_COLS = [
  ['barcode', 'Barcode', false, 90], ['description', 'Description', false, 200],
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
      <table className="items" style={{ minWidth: 1080 }}>
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
      source={fromLr(path)}
      onChange={(v) => setData(setPath(data, path, opts.raw ? v : num(v)))} />
  )

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

          <div className="section">
            <h4>Supplier</h4>
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
          </div>

          <div className="section">
            <h4>Supplier Bank</h4>
            <div className="grid">
              {f('supplier.bank.name', 'Bank Name', { raw: true })}
              {f('supplier.bank.account_no', 'Account No', { raw: true })}
              {f('supplier.bank.ifsc', 'IFSC', { raw: true })}
              {f('supplier.bank.branch', 'Branch', { raw: true })}
            </div>
          </div>

          <div className="section">
            <h4>Buyer (bill to)</h4>
            <div className="grid">
              {f('buyer.name', 'Name', { raw: true })}
              {f('buyer.gstin', 'GSTIN', { raw: true })}
              {f('buyer.state', 'State', { raw: true })}
              {f('buyer.address', 'Address', { raw: true, wide: true })}
            </div>
          </div>

          <div className="section">
            <h4>Invoice</h4>
            <div className="grid">
              {f('invoice.number', 'Invoice No', { raw: true })}
              {f('invoice.date', 'Date', { raw: true })}
              {f('invoice.due_date', 'Due Date', { raw: true })}
              {f('invoice.challan_no', 'Challan / DC No', { raw: true })}
              {f('invoice.order_no', 'Order No', { raw: true })}
              {f('invoice.order_date', 'Order Date', { raw: true })}
              {f('invoice.reference_no', 'Reference No', { raw: true })}
              {f('invoice.terms', 'Payment Terms', { raw: true })}
              {f('invoice.agent', 'Agent', { raw: true })}
              {f('invoice.broker', 'Broker', { raw: true })}
            </div>
          </div>

          <div className="section">
            <h4>E-invoice &amp; Transport
              <button className="h4btn" disabled={saving} onClick={fetchFromLr}
                title="Fill LR No, LR Date, Transporter and Book City from the matching LR register row">
                ⟲ fetch from LR register</button>
            </h4>
            <div className="grid">
              {f('invoice.irn', 'IRN', { raw: true, wide: true })}
              {f('invoice.ack_no', 'ACK No', { raw: true })}
              {f('invoice.irn_date', 'IRN Date', { raw: true })}
              {f('invoice.eway_bill', 'E-way Bill', { raw: true })}
              {f('invoice.tran_id', 'Transport / EWB Tran ID', { raw: true })}
              {f('invoice.lr_no', 'LR No', { raw: true })}
              {f('invoice.lr_date', 'LR Date', { raw: true })}
              {f('invoice.transporter', 'Transporter', { raw: true })}
              {f('invoice.destination', 'Destination', { raw: true })}
              {f('invoice.book_city', 'Book City', { raw: true })}
              {f('invoice.delivery_note', 'Delivery Note', { raw: true })}
            </div>
            {lrCands && <LrCandidates info={lrCands} busy={saving}
              onLink={linkLrRow} onClose={() => setLrCands(null)} />}
          </div>

          <div className="section">
            <h4>Line Items</h4>
            <LineItems items={data.line_items || []} setItems={(it) => setData({ ...data, line_items: it })} />
          </div>

          <div className="section">
            <h4>Taxes</h4>
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
          </div>

          <div className="section">
            <h4>Totals</h4>
            <div className="grid">
              {f('totals.total_qty', 'Total Qty')}
              {f('totals.sub_total', 'Sub Total')}
              {f('totals.taxable_total', 'Taxable Total')}
              {f('totals.tax_total', 'Tax Total')}
              {f('totals.grand_total', 'Grand Total')}
              {f('totals.amount_in_words', 'Amount in Words', { raw: true, wide: true })}
            </div>
          </div>

          <div className="section">
            <h4>GRN &amp; Notes</h4>
            <div className="grid">
              {f('meta.grn_no', 'GRN No', { raw: true })}
              {f('meta.grn_date', 'GRN Date', { raw: true })}
              {f('meta.received_by', 'Received By', { raw: true })}
              {f('meta.notes', 'Notes', { raw: true, wide: true })}
            </div>
          </div>
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
  ['sale_price', 'Sale price', 90], ['margin_pct', 'Margin %', 80]]
const blankVariant = (rate, category) => ({ ...Object.fromEntries(SPLIT_ATTRS.map(([k]) => [k, ''])),
  category: category || '', qty: '', rate: rate ?? '', mrp: '', sale_price: '', margin_pct: '' })
// quantities are floats — compare with the same tolerance the server posts with
const sameQty = (a, b) => Math.abs((+a || 0) - (+b || 0)) < 0.001
const variantLabel = (r) => SPLIT_ATTRS.map(([k]) => r[k]).filter(Boolean).join(' · ')

function Purchases({ selId, setSelId, toast }) {
  const [list, setList] = useState([])
  const [grn, setGrn] = useState(null)
  const [q, setQ] = useState('')
  const [opts, setOpts] = useState({})             // attribute option lists (phone app's)
  const [cats, setCats] = useState([])             // category master names
  const [splitFor, setSplitFor] = useState(null)   // line id whose breakdown is open
  const [srows, setSrows] = useState([])           // editable variant rows
  const refresh = useCallback(() => api.listPurchases().then(setList), [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => { if (selId) api.getPurchase(selId).then(setGrn); else setGrn(null) }, [selId])
  useEffect(() => { api.productOptions().then(setOpts).catch(() => {}) }, [])
  useEffect(() => { api.categories().then((c) => setCats((c.items || []).map((i) => i.name))).catch(() => {}) }, [])
  useEffect(() => { setSplitFor(null); setSrows([]) }, [selId])

  const reload = async () => { const g = await api.getPurchase(selId); setGrn(g); refresh(); return g }

  const post = async () => {
    try {
      const r = await api.postGrn(selId)
      const sizes = r.size_rows ? ` · ${r.size_rows} size row(s)` : ''
      toast(`✓ Posted to inventory · ${r.products_created} new, ${r.products_updated} updated${sizes}`, 'ok')
      reload()
    } catch (e) { toast('Post failed: ' + (e.detail || e.message), 'err') }
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
  const editable = !!grn && grn.status !== 'posted'
  const [pcat, setPcat] = useState({})             // in-progress category per line id
  return (
    <div className="body">
      <div className="sidebar">
        <div className="head"><h3>GRNs · {list.length}</h3></div>
        {list.length > 0 && <SearchBox value={q} onChange={setQ} placeholder="Search supplier, invoice, status…" />}
        <div className="list">
          {list.length === 0 && <div className="empty" style={{ marginTop: 30, fontSize: 13 }}>No GRNs yet. Open a confirmed document and click “Create GRN”.</div>}
          {list.filter((p) => matches(p, q, ['supplier_name', 'invoice_number', 'status'])).map((p) => (
            <div key={p.id} className={'doc-row' + (selId === p.id ? ' sel' : '')} onClick={() => setSelId(p.id)}>
              <div className="t">{p.supplier_name || 'GRN #' + p.id}</div>
              <div className="m">
                <span className={'badge ' + (p.status === 'posted' ? 'confirmed' : 'uploaded')}>{p.status}</span>
                <span>#{p.invoice_number || '—'}</span>
                <span style={{ marginLeft: 'auto' }}>₹ {money(p.grand_total)}</span>
              </div>
              <div className="m"><span>{p.line_count} lines · {p.new_products} new product(s)</span></div>
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
            <div className="section">
              <h4>Lines → inventory match</h4>
              <div className="small" style={{ margin: '-6px 0 10px', color: 'var(--muted)' }}>
                A bundle line (e.g. 250 pcs billed as one row) can be <b>broken down by
                attributes</b> — size, colour, material, pattern, fit, type, design no. Each
                distinct combination becomes its own product with its own QR, priced and
                dispatched on its own. Set <b>Category</b> here and the products are created
                already mapped, instead of arriving “unmapped” in Inventory.
              </div>
              {/* the category master, shared by the line cells and the breakdown editor */}
              <datalist id="essa-cats">{cats.map((c) => <option key={c} value={c} />)}</datalist>
              <table className="items">
                <thead><tr><th>Product</th><th>QR code</th><th>Description</th>
                  <th style={{ minWidth: 150 }}>Category</th><th>HSN</th>
                  <th style={{ textAlign: 'right' }}>Qty</th><th style={{ textAlign: 'right' }}>Rate</th>
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
                        <td style={{ textAlign: 'right' }}>{money(l.rate)}</td>
                        <td style={{ textAlign: 'right' }}>{money(l.amount)}</td>
                        <td>{l.splits.length
                          ? <span className={'badge ' + (l.split_balanced ? 'confirmed' : 'review')}
                              title={l.split_balanced ? 'Breakdown adds up to the billed qty'
                                : `${l.split_remainder} of ${l.qty} not yet broken down`}>
                              {l.splits.length} item{l.splits.length === 1 ? '' : 's'}{l.split_balanced ? '' : ' ⚠'}</span>
                          : <span className={'badge ' + (l.is_new_product ? 'review' : 'confirmed')}>
                              {l.is_new_product ? 'new' : 'matched'}</span>}</td>
                        {editable && (
                          <td style={{ whiteSpace: 'nowrap' }}>
                            <button className="btn" style={{ padding: '2px 8px' }} onClick={() => (splitFor === l.id ? setSplitFor(null) : openSplit(l))}
                              title="Break the bundle into what actually arrived — size, colour, material…">
                              {splitFor === l.id ? 'Close' : l.splits.length ? 'Edit breakdown' : 'Break down'}</button>
                            {!l.splits.length && (
                              <button className="btn" style={{ padding: '2px 8px', marginLeft: 4 }} onClick={() => scanInto(l, null)}
                                title="Scan a QR code to pin this line to an existing product">⌗ QR</button>
                            )}
                          </td>
                        )}
                      </tr>

                      {/* saved variant rows — one product each once posted */}
                      {splitFor !== l.id && l.splits.map((s) => (
                        <tr key={s.id} style={{ background: 'rgba(127,127,127,0.06)' }}>
                          <td className="mono" style={{ color: 'var(--muted)' }}>{s.product_sku || '—'}</td>
                          <td className="mono">{s.product_barcode || s.code || <span style={{ color: 'var(--muted)' }}>on post</span>}</td>
                          <td style={{ paddingLeft: 22 }}>↳ <b>{s.label}</b>
                            {s.mrp != null && <span className="small" style={{ marginLeft: 8, color: 'var(--muted)' }}>MRP {money(s.mrp)}</span>}
                            {s.sale_price != null && <span className="small" style={{ marginLeft: 8, color: 'var(--muted)' }}>sale {money(s.sale_price)}</span>}
                            {s.margin_pct != null && <span className="small" style={{ marginLeft: 8, color: 'var(--muted)' }}>{s.margin_pct}%</span>}</td>
                          <td className="mono" style={{ fontSize: 11 }}>{s.category || l.category
                            || <span style={{ color: 'var(--muted)' }}>auto</span>}</td>
                          <td>{l.hsn}</td>
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

                      {/* attribute-breakdown editor */}
                      {splitFor === l.id && (
                        <tr>
                          <td colSpan={editable ? 10 : 9} style={{ background: 'rgba(127,127,127,0.08)', padding: '12px 14px' }}>
                            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
                              <b>Breakdown of “{l.description}”</b>
                              <span className="small" style={{ color: sameQty(splitSum, l.qty) ? 'var(--ok, #2a8)' : 'var(--muted)' }}>
                                {splitSum} of {l.qty} assigned{sameQty(splitSum, l.qty) ? ' ✓' : ` · ${Math.round((l.qty - splitSum) * 1000) / 1000} left`}
                              </span>
                              <span className="small" style={{ color: 'var(--muted)' }}>
                                — fill only the attributes that differ; each row becomes one product
                              </span>
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
                <span>{grn.new_products} will be created as new products</span></div>
            </div>
          </div>
          <div className="actionbar">
            <span className="small">{grn.status === 'posted'
              ? 'Posted — stock has been updated in Inventory. Unpost to correct it, then post again.'
              : unbalanced
                ? `⚠ ${unbalanced} line(s) have a breakdown that doesn’t add up to the billed quantity — fix them before posting.`
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
  ['sale_price', 'Sale price'], ['margin_pct', 'Margin %'],
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
      toast(r.identifiers_generated?.length ? `✓ Generated ${r.identifiers_generated.join(' + ')}` : '✓ Already had a barcode', 'ok')
      await open(detail.id); load()
    } catch (err) { toast(err.detail || 'Could not generate barcode', 'err') }
  }
  const [scan, setScan] = useState('')
  const lookup = async (code) => {
    const c = (code ?? scan).trim(); if (!c) return
    try { const p = await api.lookupByCode(c); await open(p.id); setScan(''); toast(`✓ ${p.sku} · ${p.description}`, 'ok') }
    catch (err) { toast(err.detail || `No product for “${c}”`, 'err') }
  }
  const [labelScope, setLabelScope] = useState('detailed')
  const detailedCount = products.filter((p) => p.detailed).length
  const labelCount = labelScope === 'detailed' ? detailedCount : products.length
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
        <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <SearchBox value={q} onChange={setQ} placeholder="Search SKU, barcode, description, HSN, supplier…"
            style={{ maxWidth: 420 }} />
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input value={scan} onChange={(e) => setScan(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') lookup() }}
              placeholder="🔍 Scan / enter barcode or SKU…" style={{ width: 240 }} />
            <button className="btn" onClick={() => lookup()}>Fetch</button>
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginLeft: 'auto' }}>
            <select value={labelScope} onChange={(e) => setLabelScope(e.target.value)} title="Which products to print labels for">
              <option value="detailed">Detailed only ({detailedCount})</option>
              <option value="all">All products ({products.length})</option>
            </select>
            <button className="btn" onClick={printLabels}
              title="Open a print-ready barcode-label sheet">🖨 Print labels ({labelCount})</button>
          </div>
        </div>
        <table className="items">
          <thead><tr><th>SKU</th><th>Barcode</th><th>Description</th><th>Size</th><th>Category</th><th>HSN</th><th>Supplier</th>
            <th style={{ textAlign: 'right' }}>Stock</th><th style={{ textAlign: 'right' }}>Avg cost</th>
            <th style={{ textAlign: 'right' }}>Value</th></tr></thead>
          <tbody>
            {products.filter((p) => matches(p, q, ['sku', 'barcode', 'description', 'hsn', 'supplier_name', 'category', 'size'])).map((p) => (
              <tr key={p.id} style={{ cursor: 'pointer', background: detail?.id === p.id ? 'var(--panel-2)' : '' }} onClick={() => open(p.id)}>
                <td className="mono" style={{ color: 'var(--muted)' }}>{p.sku}</td>
                <td className="mono">{p.barcode || '—'}</td>
                <td>{p.description}</td>
                {/* sizes split off one bundle line share a description — the size is what tells them apart */}
                <td>{p.size || '—'}</td>
                <td className="mono" style={{ fontSize: 11 }}>{p.category
                  || <span className="badge review" title="No confident category match — open the product to pick one">unmapped</span>}</td>
                <td>{p.hsn}</td><td>{p.supplier_name || '—'}</td>
                <td style={{ textAlign: 'right' }}>{p.stock_qty} {p.uom}</td>
                <td style={{ textAlign: 'right' }}>{money(p.avg_cost)}</td>
                <td style={{ textAlign: 'right' }}>₹ {money(p.stock_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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

            <h4 style={{ color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase', margin: '18px 0 8px' }}>Codes &amp; label</h4>
            {detail.barcode ? (
              <div>
                <div style={{ background: '#fff', border: '1px solid var(--line)', borderRadius: 6, padding: '8px 10px', textAlign: 'center' }}>
                  {/* QR carries the whole record; EAN-13 is what the 1D handhelds read */}
                  <img src={api.qrSvgUrl(detail.id)} alt="QR" style={{ width: 104, height: 104 }} />
                  <img src={api.barcodeSvgUrl(detail.id)} alt="barcode" style={{ maxWidth: '100%', height: 56 }} />
                  <div className="mono" style={{ fontSize: 12, marginTop: 2, color: '#000' }}>{detail.barcode}</div>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <a className="btn primary" href={api.labelUrl(detail.id)} target="_blank" rel="noreferrer">🖨 Print label</a>
                  <button className="btn" onClick={genBarcode} title="Re-run identifier assignment">Ensure IDs</button>
                </div>
              </div>
            ) : detail.detailed ? (
              <button className="btn primary" onClick={genBarcode}>Generate barcode + SKU</button>
            ) : (
              <p className="small">Barcode is generated automatically once all product details are set (via the mobile detail form), or generate it manually after detailing.</p>
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
                <div className="k">Margin %</div><div>{detail.margin_pct != null ? detail.margin_pct + '%' : '—'}</div>
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

// ---------- stock outward ----------
function StockOutward({ toast }) {
  const [list, setList] = useState([])
  const [products, setProducts] = useState([])
  const [sel, setSel] = useState(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ date: '', to_destination: '', packed_by: '', lines: [] })
  const [q, setQ] = useState('')
  const refresh = useCallback(() => api.listOutwards().then(setList), [])
  useEffect(() => { refresh(); api.listProducts().then(setProducts) }, [refresh])
  useEffect(() => { if (sel) api.getOutward(sel).then(setForm2); function setForm2(o){ setDetail(o) } }, [sel])
  const [detail, setDetail] = useState(null)

  const addLine = () => setForm({ ...form, lines: [...form.lines, { product_id: '', qty: 1 }] })
  const updLine = (i, k, v) => { const l = form.lines.map(x => ({ ...x })); l[i][k] = v; setForm({ ...form, lines: l }) }
  const rmLine = (i) => setForm({ ...form, lines: form.lines.filter((_, j) => j !== i) })
  const save = async () => {
    const lines = form.lines.filter(l => l.product_id).map(l => ({ product_id: +l.product_id, qty: +l.qty }))
    if (!lines.length) { toast('Add at least one product', 'err'); return }
    const o = await api.createOutward({ ...form, lines })
    toast(`✓ Outward ${o.code} created`, 'ok'); setCreating(false)
    setForm({ date: '', to_destination: '', packed_by: '', lines: [] }); refresh(); setSel(o.id)
  }
  const post = async () => {
    try { const r = await api.postOutward(sel); toast(`✓ Dispatched · ${r.total_qty} units out`, 'ok'); api.getOutward(sel).then(setDetail); refresh() }
    catch (e) {
      const d = e.detail
      if (d && d.error === 'insufficient_stock') toast('Insufficient stock: ' + d.problems.map(p => `${p.product} (need ${p.requested}, have ${p.on_hand})`).join('; '), 'err')
      else toast('Post failed', 'err')
    }
  }
  const prodName = (id) => { const p = products.find(x => x.id === +id); return p ? `${p.description} · stock ${p.stock_qty}` : '' }
  return (
    <div className="body">
      <div className="sidebar">
        <div className="head"><h3>Outwards · {list.length}</h3>
          <button className="btn primary" style={{ padding: '4px 10px' }} onClick={() => { setCreating(true); setSel(null) }}>+ New</button></div>
        {list.length > 0 && <SearchBox value={q} onChange={setQ} placeholder="Search destination, code, status…" />}
        <div className="list">
          {list.filter((o) => matches(o, q, ['to_destination', 'code', 'status'])).map((o) => (
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
              <div className="field"><label>Date</label><input value={form.date} placeholder="2026-07-15" onChange={(e) => setForm({ ...form, date: e.target.value })} /></div>
              <div className="field"><label>To (destination)</label><input value={form.to_destination} placeholder="e.g. Tasjue Silks, Tirupur" onChange={(e) => setForm({ ...form, to_destination: e.target.value })} /></div>
              <div className="field"><label>Packed by</label><input value={form.packed_by} onChange={(e) => setForm({ ...form, packed_by: e.target.value })} /></div>
              <div className="field"><label>From location</label><input value="WAREHOUSE" disabled /></div>
            </div>
            <div className="section" style={{ marginTop: 18 }}>
              <h4>Items to dispatch</h4>
              <table className="items"><thead><tr><th style={{ width: '65%' }}>Product</th>
                <th style={{ textAlign: 'right' }}>Qty</th><th></th></tr></thead>
                <tbody>{form.lines.map((l, i) => (
                  <tr key={i}>
                    <td><select value={l.product_id} onChange={(e) => updLine(i, 'product_id', e.target.value)}
                      style={{ width: '100%', background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--line)', borderRadius: 5, padding: '5px' }}>
                      <option value="">— select product —</option>
                      {products.map(p => <option key={p.id} value={p.id}>{p.description} (stock {p.stock_qty})</option>)}
                    </select></td>
                    <td className="num"><input value={l.qty} onChange={(e) => updLine(i, 'qty', e.target.value)} /></td>
                    <td><button className="btn" style={{ padding: '2px 7px' }} onClick={() => rmLine(i)}>×</button></td>
                  </tr>
                ))}</tbody>
              </table>
              <button className="btn" style={{ marginTop: 8 }} onClick={addLine}>+ add item</button>
            </div>
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
            </div>
            <div className="section"><h4>Items</h4>
              <table className="items"><thead><tr><th>Barcode</th><th>Description</th>
                <th style={{ textAlign: 'right' }}>Qty</th><th style={{ textAlign: 'right' }}>Cost</th>
                <th style={{ textAlign: 'right' }}>On hand</th></tr></thead>
                <tbody>{detail.lines.map(l => (
                  <tr key={l.id}><td className="mono">{l.barcode || '—'}</td><td>{l.description}</td>
                    <td style={{ textAlign: 'right' }}>{l.qty}</td><td style={{ textAlign: 'right' }}>{money(l.rate)}</td>
                    <td style={{ textAlign: 'right' }}>{l.stock_on_hand}</td></tr>
                ))}</tbody>
              </table>
              <div className="items-foot"><span>{detail.lines.length} items</span><span>Σ qty <b>{detail.total_qty}</b></span></div>
            </div>
          </div>
          <div className="actionbar">
            <span className="small">{detail.status === 'posted' ? 'Dispatched — stock reduced in Inventory.' : 'Posting reduces warehouse stock for each item.'}</span>
            <div className="spacer" />
            <button className="btn primary" disabled={detail.status === 'posted'} onClick={post}>
              {detail.status === 'posted' ? 'Posted ✓' : 'Post Outward (reduce stock)'}</button>
          </div>
        </div>
      ) : <div className="empty">Select an outward, or click “+ New” to dispatch stock.</div>}
    </div>
  )
}
function Stat({ label, value }) {
  return (
    <div style={{ flex: 1, background: 'var(--panel)', border: '1px solid var(--line)',
      borderRadius: 10, padding: '14px 18px' }}>
      <div style={{ color: 'var(--muted)', fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
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
            <div className="section">
              <h4>Pending bills — select, then set cash / discount / TDS / debit</h4>
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
            </div>

            {ledger && ledger.rows.length > 0 && <div className="section">
              <h4>Supplier ledger</h4>
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
            </div>}
          </div>
          <div className="actionbar">
            <div className="field" style={{ width: 120 }}><label>Mode</label>
              <select value={head.mode} onChange={(e) => setHead({ ...head, mode: e.target.value })}
                style={{ width: '100%', background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--line)', borderRadius: 7, padding: '7px' }}>
                <option>NEFT</option><option>RTGS</option><option>Cash</option><option>Cheque</option></select></div>
            <div className="field" style={{ width: 140 }}><label>Ref / UTR</label><input value={head.ref_no} onChange={(e) => setHead({ ...head, ref_no: e.target.value })} /></div>
            <div className="field" style={{ width: 120 }}><label>Date</label><input value={head.date} placeholder="2026-07-15" onChange={(e) => setHead({ ...head, date: e.target.value })} /></div>
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
  const [detail, setDetail] = useState(null)
  const [qtys, setQtys] = useState({})
  const [reason, setReason] = useState('')
  const [date, setDate] = useState('')
  const [q, setQ] = useState('')
  const refresh = useCallback(() => api.listReturns().then(setList), [])
  useEffect(() => { refresh() }, [refresh])

  const openPicker = () => { api.listPurchases().then(p => setPurchases(p.filter(x => x.status === 'posted'))); setPicking(true); setDetail(null) }
  const startReturn = async (purchaseId) => {
    const r = await api.buildReturn(purchaseId)
    setDetail(r); setPicking(false); setQtys({}); setReason(''); setDate('')
  }
  const openReturn = (id) => api.getReturn(id).then(r => { setDetail(r); setPicking(false); setQtys({}) })
  const post = async () => {
    try {
      const line_qtys = {}; Object.entries(qtys).forEach(([k, v]) => { if (+v > 0) line_qtys[k] = +v })
      if (!Object.keys(line_qtys).length) { toast('Set a return qty on at least one line', 'err'); return }
      const res = await api.postReturn(detail.id, { reason, date, line_qtys })
      toast(`✓ Debit note ${detail.code} posted · ₹${money(res.debit_total)}`, 'ok')
      api.getReturn(detail.id).then(setDetail); refresh()
    } catch (e) { toast('Post failed: ' + (e.detail || e.message), 'err') }
  }
  const editable = detail && detail.status !== 'posted'
  const draftTotal = detail ? detail.lines.reduce((s, l) => s + (+qtys[l.id] || 0) * (l.rate || 0), 0) : 0

  return (
    <div className="body">
      <div className="sidebar">
        <div className="head"><h3>Returns · {list.length}</h3>
          <button className="btn primary" style={{ padding: '4px 10px' }} onClick={openPicker}>+ New</button></div>
        {list.length > 0 && <SearchBox value={q} onChange={setQ} placeholder="Search supplier, invoice, code…" />}
        <div className="list">
          {list.filter((r) => matches(r, q, ['supplier_name', 'invoice_number', 'code', 'status'])).map((r) => (
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
          <table className="items"><thead><tr><th>Supplier</th><th>Invoice</th><th>Date</th>
            <th style={{ textAlign: 'right' }}>Grand total</th><th></th></tr></thead>
            <tbody>{purchases.map(p => (
              <tr key={p.id}><td>{p.supplier_name}</td><td className="mono">{p.invoice_number}</td>
                <td>{p.invoice_date}</td><td style={{ textAlign: 'right' }}>₹ {money(p.grand_total)}</td>
                <td><button className="btn" style={{ padding: '3px 10px' }} onClick={() => startReturn(p.id)}>Return →</button></td></tr>
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
              <div className="k">Debit note vs</div><div className="mono">{detail.invoice_number}</div>
              <div className="k">Debit total</div><div>₹ {money(detail.status === 'posted' ? detail.total : draftTotal)}{editable ? ' + tax' : ''}</div>
            </div>
            <div className="section"><h4>{editable ? 'Set return quantity per line' : 'Returned lines'}</h4>
              <table className="items"><thead><tr><th>Barcode</th><th>Description</th><th>HSN</th>
                <th style={{ textAlign: 'right' }}>Rate</th><th style={{ textAlign: 'right' }}>On hand</th>
                <th style={{ textAlign: 'right' }}>{editable ? 'Return qty' : 'Qty'}</th>
                <th style={{ textAlign: 'right' }}>Amount</th></tr></thead>
                <tbody>{detail.lines.map(l => {
                  const q = editable ? (qtys[l.id] ?? '') : l.qty
                  const amt = editable ? (+qtys[l.id] || 0) * (l.rate || 0) : l.amount
                  if (!editable && !l.qty) return null
                  return (
                    <tr key={l.id}><td className="mono">{l.barcode || '—'}</td><td>{l.description}</td><td>{l.hsn}</td>
                      <td style={{ textAlign: 'right' }}>{money(l.rate)}</td>
                      <td style={{ textAlign: 'right' }}>{l.on_hand}</td>
                      <td className="num">{editable
                        ? <input value={q} placeholder="0" onChange={(e) => setQtys({ ...qtys, [l.id]: e.target.value })} />
                        : l.qty}</td>
                      <td style={{ textAlign: 'right' }}>{money(amt)}</td></tr>
                  )
                })}</tbody>
              </table>
            </div>
          </div>
          {editable && (
            <div className="actionbar">
              <div className="field" style={{ width: 180 }}><label>Reason</label><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. damaged / wrong item" /></div>
              <div className="field" style={{ width: 120 }}><label>Date</label><input value={date} placeholder="2026-07-15" onChange={(e) => setDate(e.target.value)} /></div>
              <div className="spacer" />
              <span className="small">Posting reverses stock and raises a debit note against the invoice.</span>
              <button className="btn primary" onClick={post}>Post Debit Note</button>
            </div>
          )}
        </div>
      ) : <div className="empty">Select a return, or click “+ New” to return goods against an invoice.</div>}
    </div>
  )
}

// ---------- reports ----------
const REPORT_GROUPS = { stock: 'Stock', purchase: 'Purchase', finance: 'Finance', master: 'Masters' }
function Reports() {
  const [cat, setCat] = useState([])
  const [key, setKey] = useState(null)
  const [rep, setRep] = useState(null)
  const [q, setQ] = useState('')
  useEffect(() => { api.reportCatalogue().then((c) => { setCat(c); if (c[0]) pick(c[0].key) }) }, [])
  const pick = (k) => { setKey(k); setRep(null); setQ(''); api.runReport(k).then(setRep) }
  const rows = rep ? rep.rows.filter((row) => !q || rep.columns.some((c) => String(row[c] ?? '').toLowerCase().includes(q.toLowerCase()))) : []
  const grouped = cat.reduce((a, r) => { (a[r.group] = a[r.group] || []).push(r); return a }, {})
  const fmt = (v) => typeof v === 'number' ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : (v ?? '')
  return (
    <div className="body">
      <div className="sidebar">
        <div className="head"><h3>Reports</h3></div>
        <div className="list" style={{ padding: '6px 0' }}>
          {Object.entries(grouped).map(([g, items]) => (
            <div key={g}>
              <div style={{ padding: '8px 14px 4px', fontSize: 11, textTransform: 'uppercase', color: 'var(--muted)', letterSpacing: '.5px' }}>{REPORT_GROUPS[g] || g}</div>
              {items.map(r => (
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
            <div style={{ display: 'flex', alignItems: 'center', padding: '14px 22px', borderBottom: '1px solid var(--line)' }}>
              <h2 style={{ margin: 0 }}>{cat.find(r => r.key === key)?.name}</h2>
              <div style={{ marginLeft: 16, display: 'flex', gap: 16 }}>
                {Object.entries(rep.totals).map(([k, v]) => (
                  <span key={k} className="small">{k.replace(/_/g, ' ')}: <b style={{ color: 'var(--text)' }}>{fmt(v)}</b></span>
                ))}
              </div>
              <div className="spacer" style={{ flex: 1 }} />
              <SearchBox value={q} onChange={setQ} placeholder="Filter rows…" style={{ width: 220, marginRight: 10 }} />
              <a className="btn" href={api.reportCsvUrl(key)} target="_blank" rel="noreferrer">Export CSV</a>
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: '0 22px 22px' }}>
              <div className="small" style={{ padding: '8px 0', color: 'var(--muted)' }}>{rows.length} of {rep.rows.length} rows{q ? ` matching “${q}”` : ''}</div>
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
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', zIndex: 100,
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
          background: on ? '#12241b' : 'var(--panel-2)', border: '1px solid ' + (on ? '#24503a' : 'var(--line)') }}>
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

// ---------- LR Entry (upload register image → OCR grid → save) ----------
const LR_COLS = [
  ['recv_date', 'Recv Date', 100], ['transport', 'Transport', 110], ['bundle', 'Bundle', 60],
  ['lr_no', 'LR No', 110], ['lr_date', 'LR Date', 100], ['supplier_name', 'Supplier', 180],
  ['inv_no', 'Inv No', 80], ['inv_date', 'Inv Date', 100], ['qty', 'Qty', 55],
  ['amount', 'Amount', 90], ['paid_topay', 'Paid/ToPay', 80], ['freight_amount', 'Freight', 70],
  ['cash_cheque', 'Cash/Chq', 80], ['item', 'Item', 110],
]
// freight settlement — completed or corrected when the lorry actually delivers,
// so these stay editable on already-saved rows
const LR_SETTLE_COLS = ['paid_topay', 'freight_amount', 'cash_cheque']
function LREntryView({ toast }) {
  const [rows, setRows] = useState([])
  const [docId, setDocId] = useState(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState([])
  const refresh = useCallback(() => api.lrList().then(setSaved), [])
  useEffect(() => { refresh() }, [refresh])

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
      const upd = await api.lrSettle(r.id, { [k]: val })
      setSaved((list) => list.map((x) => (x.id === upd.id ? upd : x)))
      drop(key)
    } catch (err) { toast('Could not save: ' + err.message, 'err'); drop(key) }
  }

  const toSave = rows.filter(r => !isExact(r))
  const nDoubtful = rows.filter(isDoubtful).length
  const qtySum = toSave.reduce((s, x) => s + (+x.qty || 0), 0)
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 22px', borderBottom: '1px solid var(--line)' }}>
        <h2 style={{ margin: 0 }}>LR Entry</h2>
        <span className="small">Import an LR register image / PDF — the rows are read automatically (no manual entry).</span>
        <div style={{ flex: 1 }} />
        <label className="btn primary uploadbtn">{busy ? 'Reading…' : 'Import LR image / PDF'}
          <input type="file" accept="image/*,.pdf" onChange={onFile} disabled={busy} /></label>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 22 }}>
        {note && <div className="warnbox" style={{ marginBottom: 14 }}><h4 style={{ border: 'none', margin: 0, color: 'var(--muted)' }}>{note}</h4></div>}
        {dup && (dup.duplicates > 0 || dup.doubtful > 0) && (
          <div className="warnbox" style={{ marginBottom: 14, borderColor: '#e0a800' }}>
            <h4 style={{ border: 'none', margin: 0 }}>
              {dup.duplicates > 0 && <>🚫 {dup.duplicates} exact duplicate{dup.duplicates > 1 ? 's' : ''} (identical to an existing row) — skipped. </>}
              {dup.doubtful > 0 && <>⚠ {dup.doubtful} doubtful row{dup.doubtful > 1 ? 's' : ''} — same LR/Invoice but other values differ; the changed cells are highlighted, please verify before saving. </>}
              {dup.new} new row{dup.new === 1 ? '' : 's'}.
            </h4>
          </div>
        )}
        {rows.length === 0 && saved.length === 0 && <div className="empty" style={{ marginTop: 40 }}>Import an LR register page to auto-extract its rows.</div>}
        {rows.length > 0 && (
          <>
            <div className="section"><h4>Extracted rows — review &amp; save</h4>
              <div style={{ overflowX: 'auto' }}>
                <table className="items" style={{ minWidth: 1560 }}>
                  <thead><tr><th style={{ minWidth: 70 }}>Status</th>{LR_COLS.map(([k, l, w]) => <th key={k} style={{ minWidth: w }}>{l}</th>)}<th></th></tr></thead>
                  <tbody>{rows.map((r, i) => (
                    <tr key={i} style={isExact(r) ? { background: 'rgba(200,60,60,0.12)', opacity: 0.6 }
                      : isDoubtful(r) ? { background: 'rgba(224,168,0,0.10)' } : undefined}>
                      <td style={{ whiteSpace: 'nowrap', fontSize: 11, fontWeight: 600 }}>
                        {isExact(r) ? <span style={{ color: '#c0392b' }} title="Identical to an existing row — will be skipped">🚫 duplicate</span>
                          : isDoubtful(r) ? <span style={{ color: '#b8860b' }}
                              title={'Same LR/Invoice, but these differ from the saved row:\n' +
                                (r._diffs || []).map(f => `${f}: saved “${r._conflict_with?.[f] ?? ''}” vs this “${r[f] ?? ''}”`).join('\n')}>⚠ verify</span>
                          : <span style={{ color: 'var(--ok, #2a8)' }}>new</span>}
                      </td>
                      {LR_COLS.map(([k]) => {
                        const changed = isDoubtful(r) && (r._diffs || []).includes(k)
                        return <td key={k} style={changed ? { background: 'rgba(224,168,0,0.28)' } : undefined}
                          title={changed ? `Saved row has: ${r._conflict_with?.[k] ?? '(blank)'}` : undefined}>
                          <input value={r[k] ?? ''} onChange={(e) => upd(i, k, e.target.value)} /></td>
                    })}<td><button className="btn" style={{ padding: '2px 7px' }} onClick={() => del(i)}>×</button></td></tr>
                  ))}</tbody>
                </table>
              </div>
              <div className="items-foot"><span>{toSave.length} to save{nDoubtful ? ` (incl. ${nDoubtful} to verify)` : ''}{rows.length !== toSave.length ? ` · ${rows.length - toSave.length} exact dup skipped` : ''}</span><span>Σ qty <b>{qtySum}</b></span>
                <button className="btn primary" style={{ marginLeft: 'auto' }} onClick={save}>Save {toSave.length} Entr{toSave.length === 1 ? 'y' : 'ies'}</button></div>
            </div>
          </>
        )}
        {saved.length > 0 && (
          <div className="section"><h4>Saved LR entries · {saved.length}</h4>
            <div className="small" style={{ margin: '-6px 0 10px', color: 'var(--muted)' }}>
              The freight columns (Paid/ToPay, Freight, Cash/Chq) are editable here — complete or
              correct them when the lorry delivers and the money changes hands.
              <b> Received by</b> comes from the warehouse phone app (<span className="mono">/m</span> →
              Consignments), where whoever takes the packages in records their name.
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="items" style={{ minWidth: 1460 }}>
                <thead><tr><th style={{ minWidth: 70 }}>Invoice</th>{LR_COLS.map(([k, l, w]) => <th key={k} style={{ minWidth: w }}>{l}</th>)}
                  <th style={{ minWidth: 110 }}>Received by</th></tr></thead>
                <tbody>{saved.map((r) => (
                  <tr key={r.id}>
                    <td style={{ fontSize: 11, fontWeight: 600 }}>
                      {r.mismatches && r.mismatches.length
                        ? <span style={{ color: '#b8860b' }} title={r.mismatches.map(m => `${m.field}: register ${m.register} vs invoice ${m.invoice}`).join('\n')}>⚠ conflict</span>
                        : r.matched ? <span style={{ color: 'var(--ok, #2a8)' }}>✓ linked</span>
                        : <span style={{ color: 'var(--muted)' }}>pending</span>}
                    </td>
                    {LR_COLS.map(([k]) => {
                      const m = r.mismatches && r.mismatches.find(x => x.field === k)
                      if (LR_SETTLE_COLS.includes(k)) return (
                        <td key={k} title="Freight settlement — editable; saves when you leave the cell">
                          <input value={cellVal(r, k)}
                            onChange={(e) => setPending({ ...pending, [cellKey(r, k)]: e.target.value })}
                            onBlur={() => commitCell(r, k)}
                            onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} /></td>
                      )
                      return <td key={k} style={m ? { background: 'rgba(224,168,0,0.18)' } : undefined}
                        title={m ? `Register: ${m.register}\nInvoice: ${m.invoice}` : undefined}>{r[k] ?? ''}{m ? ' ⚠' : ''}</td>
                    })}
                    {/* set by whoever takes the packages in, from the phone app */}
                    <td>{r.received_by
                      ? <span title="Recorded in the warehouse phone app">✓ {r.received_by}</span>
                      : <span style={{ color: 'var(--muted)' }} title="Nobody has taken this consignment in on the phone app yet">not received</span>}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------- masters (categories / agents / transports) ----------
function Masters() {
  const [tab, setTab] = useState('categories')
  const [cats, setCats] = useState(null)
  const [q, setQ] = useState('')
  const [section, setSection] = useState('')
  const [agents, setAgents] = useState([])
  const [transports, setTransports] = useState([])
  useEffect(() => { api.categories().then(setCats); api.agents().then(setAgents); api.transports().then(setTransports) }, [])
  const shown = cats ? cats.items.filter(c => (!section || c.section === section) && (!q || c.name.toLowerCase().includes(q.toLowerCase()))) : []
  return (
    <div className="body">
      <div className="sidebar">
        <div className="head"><h3>Masters</h3></div>
        <div className="list" style={{ padding: '6px 0' }}>
          {[['categories', `Product Categories · ${cats ? cats.count : '…'}`],
            ['agents', `Agents · ${agents.length}`],
            ['transports', `Transporters · ${transports.length}`]].map(([k, label]) => (
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
      </div>
    </div>
  )
}

// ---------- app shell ----------
export default function App() {
  const [tab, setTab] = useState('lr')
  const [status, setStatus] = useState(null)
  const [docs, setDocs] = useState([])
  const [sel, setSel] = useState(null)
  const [selPurchase, setSelPurchase] = useState(null)
  const [toastMsg, setToastMsg] = useState(null)
  const [showSettings, setShowSettings] = useState(false)
  const [scanning, setScanning] = useState(null)   // {url, name} while extracting
  const [docQuery, setDocQuery] = useState('')
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
      <div className="topbar">
        <div className="brand">Essa <span>·</span> Document Intake<small>{status?.company?.name} — invoice → data, trained per supplier</small></div>
        <div className="tabs">
          <button className={tab === 'lr' ? 'active' : ''} onClick={() => setTab('lr')}>LR Entry</button>
          <button className={tab === 'documents' ? 'active' : ''} onClick={() => setTab('documents')}>Invoice Entry</button>
          <button className={tab === 'purchases' ? 'active' : ''} onClick={() => setTab('purchases')}>GRN</button>
          <button className={tab === 'inventory' ? 'active' : ''} onClick={() => setTab('inventory')}>Inventory</button>
          <button className={tab === 'outward' ? 'active' : ''} onClick={() => setTab('outward')}>Stock Outward</button>
          <button className={tab === 'returns' ? 'active' : ''} onClick={() => setTab('returns')}>Returns</button>
          <button className={tab === 'payments' ? 'active' : ''} onClick={() => setTab('payments')}>Payments</button>
          <button className={tab === 'reports' ? 'active' : ''} onClick={() => setTab('reports')}>Reports</button>
          <button className={tab === 'suppliers' ? 'active' : ''} onClick={() => setTab('suppliers')}>Suppliers</button>
          {role === 'admin' && <button className={tab === 'masters' ? 'active' : ''} onClick={() => setTab('masters')}>Masters</button>}
        </div>
        <div className="spacer" />
        <button className={'pill ' + (providers.claude_vision ? 'on' : 'off')} style={{ cursor: 'pointer', background: 'none' }}
          title="Configure vision extraction" onClick={() => setShowSettings(true)}>
          👁 vision {providers.claude_vision ? 'on' : 'off'} ⚙</button>
        <span className={'pill ' + (providers.tesseract ? 'on' : 'off')}>OCR {providers.tesseract ? 'on' : 'off'}</span>
        <label className="btn primary uploadbtn">Upload invoice<input type="file" accept="image/*,.pdf" onChange={onUpload} /></label>
        <button className="btn" style={{ padding: '7px 12px' }} title={'Signed in as ' + user} onClick={logout}>Logout</button>
      </div>

      {tab === 'lr' ? (
        <div className="body"><LREntryView toast={toast} /></div>
      ) : tab === 'documents' ? (
        <div className="body">
          <div className="sidebar">
            <div className="head"><h3>Documents · {docs.length}</h3>
              {docs.length > 0 && <button className="btn" style={{ padding: '3px 9px', fontSize: 11 }}
                onClick={clearAll} title="Delete all documents & transaction data">Clear all</button>}</div>
            {docs.length > 0 && <SearchBox value={docQuery} onChange={setDocQuery}
              placeholder="Search supplier, invoice, status…" />}
            <div className="list">
              {docs.length === 0 && <div className="empty" style={{ marginTop: 30, fontSize: 13 }}>No documents. Click “Upload invoice” to add one.</div>}
              {docs.filter((d) => matches(d, docQuery, ['supplier_name', 'filename', 'invoice_number', 'status'])).map((d) => (
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
      ) : tab === 'returns' ? (
        <Returns toast={toast} />
      ) : tab === 'payments' ? (
        <Payments toast={toast} />
      ) : tab === 'reports' ? (
        <Reports />
      ) : tab === 'masters' ? (
        role === 'admin' ? <Masters /> : <div className="empty">Masters are admin-only.</div>
      ) : <Suppliers toast={toast} />}

      {scanning && <ScanningOverlay url={scanning.url} name={scanning.name}
        vision={!!providers.claude_vision} />}
      {showSettings && <VisionSettings onClose={() => setShowSettings(false)}
        onChanged={refreshStatus} toast={toast} />}
      {toastMsg && <div className={'toast ' + toastMsg.kind}>{toastMsg.m}</div>}
    </div>
  )
}
