// ---------------------------------------------------------------------------
//  The signed-in session
//  --------------------------------------------------------------------------
//  Every call below is a bare `fetch`, and there are well over a hundred of
//  them. Rather than thread a token argument through all of those, the module
//  declares its own `fetch` at the top — a module-scoped binding shadows the
//  global one for this file, so the calls underneath are unchanged and none can
//  be forgotten later.
//
//  The token also goes back as a cookie at login, which is what carries the
//  <img> tags pointing at invoice scans and any report opened in a new tab —
//  neither of those can send a header.
// ---------------------------------------------------------------------------
const TOKEN_KEY = 'essa_token'

export const session = {
  get: () => localStorage.getItem(TOKEN_KEY) || '',
  set: (t) => { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) },
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

// Called by App when a call comes back 401, so an expired or revoked token
// returns the whole app to the login screen instead of leaving every panel
// showing its own error.
let onUnauthorized = () => {}
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn || (() => {}) }

const fetch = (url, opts = {}) => {
  const token = session.get()
  const headers = new Headers(opts.headers || {})
  if (token) headers.set('X-Essa-Token', token)
  return window.fetch(url, { ...opts, headers, credentials: 'same-origin' })
    .then((r) => {
      // 401 is "not signed in" and 403 is "signed in, not allowed" — only the
      // first should bounce anyone out. A user who opens an admin screen from a
      // stale bookmark gets told, and stays where they are.
      if (r.status === 401 && !String(url).includes('/api/auth/')) onUnauthorized()
      return r
    })
}

// The message the server sent, not "Error: 403" — the API answers a refused
// call with a sentence worth showing ("This needs admin access — you are signed
// in as user.").
// What the status means when the body carries no message of its own. A failure
// that answers with HTML — a gateway timeout, a crashed function — leaves
// `detail` undefined, and every screen then falls back to its own generic
// sentence ("Could not merge them"), which says nothing about what happened.
const STATUS_HINT = {
  401: 'not signed in',
  403: 'not allowed for this account',
  404: 'not found',
  413: 'the file is too large',
  502: 'the server could not reach something it depends on',
  504: 'timed out — a long invoice can take minutes; try again, or read it from the document',
}

const J = async (r) => {
  if (!r.ok) {
    const j = await r.json().catch(() => ({}))
    const detail = j.detail
      || `HTTP ${r.status}${STATUS_HINT[r.status] ? ' — ' + STATUS_HINT[r.status] : ''}`
    throw Object.assign(new Error(String(r.status)), { status: r.status, detail })
  }
  return r.json()
}

// ?a=1&b=2 from an object, skipping blanks — so an untouched filter is absent
// rather than sent as an empty string the server has to special-case
const qs = (params) => {
  const p = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => { if (v !== '' && v != null) p.append(k, v) })
  return p.toString() ? '?' + p : ''
}

export const api = {
  // auth
  login: (username, password) => fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('login'), { detail: j.detail }); return j }),
  verifyToken: (token) => fetch('/api/auth/verify?token=' + encodeURIComponent(token || '')).then(J),
  logout: () => fetch('/api/auth/logout', { method: 'POST' }).then(J).catch(() => ({})),
  changePassword: (current_password, new_password) => fetch('/api/auth/change-password', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password, new_password }) }).then(J),

  // users — super admin only; the server refuses these for anyone else
  listUsers: () => fetch('/api/users').then(J),
  createUser: (body) => fetch('/api/users', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) }).then(J),
  updateUser: (id, body) => fetch(`/api/users/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) }).then(J),
  resetUserPassword: (id, new_password) => fetch(`/api/users/${id}/password`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_password }) }).then(J),
  deleteUser: (id) => fetch(`/api/users/${id}`, { method: 'DELETE' }).then(J),

  status: () => fetch('/api/status').then(J),
  // Aggregated series behind the graphical dashboard, in one call. `status` is
  // kept on the error for the same reason as askReport: a 404/405 here is a
  // server still running from before this endpoint existed, which is a restart
  // rather than a fault, and the screen can say which.
  dashboardCharts: (months) => fetch('/api/dashboard/charts' + (months ? `?months=${months}` : ''))
    .then(r => { if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status }); return r.json() }),
  listDocuments: () => fetch('/api/documents').then(J),
  getDocument: (id) => fetch(`/api/documents/${id}`).then(J),
  // Read an already-uploaded document again — for one whose reading ran out of
  // time and left it with no extraction attached.
  reExtract: (id) => fetch(`/api/documents/${id}/extract`, { method: 'POST' }).then(J),
  // Fold another document's pages into this one — for an invoice whose pages
  // were uploaded separately and became two half-invoices.
  mergeDocuments: (id, from_id) => fetch(`/api/documents/${id}/merge`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from_id }) }).then(J),
  deleteDocument: (id) => fetch(`/api/documents/${id}`, { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('del'), { detail: j.detail }); return j }),
  clearAllDocuments: (wipeMasters) => fetch('/api/documents/clear-all' + (wipeMasters ? '?wipe_masters=true' : ''), { method: 'DELETE' }).then(J),
  // `v` is the document's content hash — see _doc_out. Without it the URL names
  // a recycled row id and the browser can serve the previous occupant's invoice.
  imageUrl: (id, v) => `/api/documents/${id}/image` + (v ? `?v=${v}` : ''),
  // One invoice, however many pages it was photographed as. The field is
  // repeated rather than renamed, so the server sees a list either way and a
  // single page behaves exactly as it always did.
  upload: (files) => {
    const fd = new FormData()
    for (const f of (files.length === undefined ? [files] : files)) fd.append('file', f)
    return fetch('/api/documents/upload', { method: 'POST', body: fd }).then(J)
  },
  confirm: (id, data, train) =>
    fetch(`/api/documents/${id}/confirm`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data, train }),
    }).then(J),
  // pull LR No / LR Date / Transporter / Book City in from the LR register.
  // lrEntryId links a row the user picked from the suggestions instead of relying
  // on the invoice-no / LR-no match.
  fetchTransport: (id, lrEntryId) => fetch(
    `/api/documents/${id}/fetch-transport` + (lrEntryId ? `?lr_entry_id=${lrEntryId}` : ''),
    { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('lrfetch'), { detail: j.detail }); return j }),
  exportUrl: (id, format) => `/api/documents/${id}/export?format=${format}`,
  listSuppliers: () => fetch('/api/suppliers').then(J),
  getSupplier: (id) => fetch(`/api/suppliers/${id}`).then(J),

  // purchases / GRN
  listPurchases: () => fetch('/api/purchases').then(J),
  getPurchase: (id) => fetch(`/api/purchases/${id}`).then(J),
  buildGrn: (docId) => fetch(`/api/purchases/from-document/${docId}`, { method: 'POST' }).then(J),
  postGrn: (id) => fetch(`/api/purchases/${id}/post`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('post'), { detail: j.detail }); return j }),
  // reverse a posted GRN back to draft (guarded: payments / debit notes / dispatches)
  unpostCheck: (id) => fetch(`/api/purchases/${id}/unpost-check`).then(J),
  unpostGrn: (id) => fetch(`/api/purchases/${id}/unpost`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('unpost'), { detail: j.detail }); return j }),
  deletePurchase: (id) => fetch(`/api/purchases/${id}`, { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('del'), { detail: j.detail }); return j }),
  // size breakup of one billed line — [] clears it
  setLineSplits: (lineId, rows) => fetch(`/api/purchases/lines/${lineId}/splits`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('splits'), { detail: j.detail }); return j }),
  // shortage entry: what the supplier billed and the boxes didn't hold. Recorded
  // before posting — after it, the gap is invisible (stock says 40, the invoice
  // says 50, and nothing remembers why). [] clears the line's shortages.
  setLineShortages: (lineId, rows, recorded_by) => fetch(`/api/purchases/lines/${lineId}/shortages`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows, recorded_by }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('short'), { detail: j.detail }); return j }),
  grnShortages: (pid) => fetch(`/api/purchases/${pid}/shortages`).then(J),
  shortageOptions: () => fetch('/api/purchases/shortage-options').then(J),
  // accept a shortage rather than claim it (supplier is re-sending, or it's too small)
  waiveShortage: (sid, reason, by) => fetch(`/api/purchases/shortages/${sid}/waive`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason, by }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('waive'), { detail: j.detail }); return j }),
  unwaiveShortage: (sid) => fetch(`/api/purchases/shortages/${sid}/unwaive`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('waive'), { detail: j.detail }); return j }),

  // set a line's category master mapping ('' clears it, back to auto)
  editLine: (lineId, fields) => fetch(`/api/purchases/lines/${lineId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('line'), { detail: j.detail }); return j }),
  // pin a line (or one size of it) to a product by scanned QR / barcode / SKU
  scanLineCode: (lineId, code, split_id) => fetch(`/api/purchases/lines/${lineId}/scan`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code, split_id }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('scan'), { detail: j.detail }); return j }),

  // inventory
  inventorySummary: () => fetch('/api/inventory/summary').then(J),
  listProducts: () => fetch('/api/inventory/products').then(J),
  getProduct: (id) => fetch(`/api/inventory/products/${id}`).then(J),
  // no editProduct: a product is what its GRN made it — correct it by unposting
  // the GRN, fixing the line and posting again (stock is corrected via adjustStock)
  adjustStock: (id, new_qty, note) => fetch(`/api/inventory/products/${id}/adjust-stock`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ new_qty, note }) }).then(J),
  generateBarcode: (id) => fetch(`/api/inventory/products/${id}/generate-barcode`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('bc'), { detail: j.detail }); return j }),
  lookupByCode: (code) => fetch('/api/inventory/lookup?code=' + encodeURIComponent(code))
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('lk'), { detail: j.detail }); return j }),
  labelUrl: (id) => `/api/inventory/products/${id}/label`,
  labelsUrl: (ids, status) => {
    const p = []
    if (ids && ids.length) p.push('ids=' + ids.join(','))
    if (status) p.push('status=' + status)
    return '/api/inventory/labels' + (p.length ? '?' + p.join('&') : '')
  },
  // per-piece codes: one inventory record of 8 has 8 child codes, each its own QR.
  // `status` is kept on the error because a 404 here means something specific and
  // fixable — a server still running from before this endpoint existed, serving the
  // new UI off disk — and the screen can say so instead of "could not load".
  productUnits: (id) => fetch(`/api/inventory/products/${id}/units`)
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('units'), { status: r.status, detail: j.detail }); return j }),
  generateUnits: (id) => fetch(`/api/inventory/products/${id}/units/generate`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('units'), { status: r.status, detail: j.detail }); return j }),
  unitQrSvgUrl: (uid, scale) => `/api/inventory/units/${uid}/qr.svg` + (scale ? `?scale=${scale}` : ''),
  unitLabelUrl: (uid) => `/api/inventory/units/${uid}/label`,
  unitLabelsUrl: (productId, ids) => (ids && ids.length)
    ? `/api/inventory/unit-labels?ids=${ids.join(',')}`
    : `/api/inventory/unit-labels?product_id=${productId}`,

  // ---- Label Designer + Label Printing ----------------------------------
  // A template stores field REFERENCES and never a product's values, so these
  // calls carry a layout in one direction and resolve data in the other. That
  // separation is the module: design once here, print anything with it below.
  // `status` is kept on the error, as askReport and productUnits do: the
  // frontend is read off disk and refreshes with the browser, but routes are
  // registered when Python starts. A backend left running from before these
  // endpoints existed serves the new screen and 404s its calls — a restart, not
  // a fault, and only a message that knows it was a 404 can say so. The shared
  // `J` helper throws a bare Error, so these two cannot use it.
  labelFields: () => fetch('/api/labels/fields')
    .then(r => { if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status }); return r.json() }),
  labelTemplates: () => fetch('/api/labels/templates')
    .then(r => { if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status }); return r.json() }),
  labelTemplate: (id) => fetch(`/api/labels/templates/${id}`).then(J),
  createLabelTemplate: (body) => fetch('/api/labels/templates', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('tpl'), { detail: j.detail }); return j }),
  saveLabelTemplate: (id, body) => fetch(`/api/labels/templates/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('tpl'), { detail: j.detail }); return j }),
  duplicateLabelTemplate: (id) => fetch(`/api/labels/templates/${id}/duplicate`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('tpl'), { detail: j.detail }); return j }),
  setDefaultLabelTemplate: (id) => fetch(`/api/labels/templates/${id}/default`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('tpl'), { detail: j.detail }); return j }),
  setLabelTemplateActive: (id, active) => fetch(`/api/labels/templates/${id}/active`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('tpl'), { detail: j.detail }); return j }),
  deleteLabelTemplate: (id) => fetch(`/api/labels/templates/${id}`, { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('tpl'), { detail: j.detail }); return j }),
  // what one label would say, with its symbols already rendered — the canvas
  // draws these itself, so a field being dragged carries its real string and an
  // overflowing description is seen while designing rather than on a sticker
  labelPreviewValues: (productId) => fetch('/api/labels/preview-values' + qs({ product_id: productId })).then(J),
  // is a QR drawn this big still scannable? The one property whose mistake is
  // invisible until the labels are on garments
  labelQrCheck: (boxMm, productId) => fetch('/api/labels/qr-check' + qs({ box_mm: boxMm, product_id: productId })).then(J),
  labelPreviewUrl: (id, productId, copies) => `/api/labels/templates/${id}/preview` + qs({ product_id: productId, copies }),
  // items: [{id, qty}] for SKU labels; unitProducts / units for per-piece ones
  labelPrintUrl: (templateId, items, opts) => {
    const o = opts || {}
    return '/api/labels/print' + qs({
      template_id: templateId,
      items: (items || []).map((i) => `${i.id}:${i.qty || 1}`).join(','),
      units: (o.units || []).join(','),
      unit_products: (o.unitProducts || []).join(','),
    })
  },

  // inventory integrity: a record is stock only if a posted GRN put it there.
  // Scan is read-only and is what the Repair screen shows before anything is
  // deleted; repair removes only debris (never a product kept after an unpost).
  integrityScan: () => fetch('/api/inventory/integrity').then(J),
  integrityRepair: (dryRun) => fetch('/api/inventory/repair' + (dryRun ? '?dry_run=true' : ''),
    { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('repair'), { detail: j.detail }); return j }),

  // dropdown option sets (same lists the phone app uses, incl. sizes)
  productOptions: () => fetch('/api/inventory/product-options').then(J),
  barcodeSvgUrl: (id) => `/api/inventory/products/${id}/barcode.svg`,
  // scale drives the QR's module size — 2 is right for a list thumbnail, the
  // default 4 for the detail panel and print
  qrSvgUrl: (id, scale) => `/api/inventory/products/${id}/qr.svg` + (scale ? `?scale=${scale}` : ''),
  // map a free-text description onto the Product Category master
  categorize: (description) =>
    fetch('/api/inventory/categorize?description=' + encodeURIComponent(description || '')).then(J),
  // the full product record as the stock screens show it — QR, name, size,
  // colour, batch. Takes anything scannable: a product QR, a piece label, a SKU.
  // Item Locator: one scanned code, everything known about that item —
  // where it came from, where it is, where it went.
  locateItem: (code) => fetch('/api/inventory/locate?code=' + encodeURIComponent(code)).then(J),
  productCard: (code) => fetch('/api/inventory/product-card?code=' + encodeURIComponent(code || ''))
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('card'), { detail: j.detail }); return j }),

  // stock outward (dispatch) + stock inward (the destination accepting it)
  listOutwards: (status) => fetch('/api/outward' + (status ? '?status=' + status : '')).then(J),
  getOutward: (id) => fetch(`/api/outward/${id}`).then(J),
  createOutward: (body) => fetch('/api/outward', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(J),
  postOutward: (id, allowNeg) => fetch(`/api/outward/${id}/post?allow_negative=${!!allowNeg}`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('post'), { detail: j.detail }); return j }),
  // stock inward: accept a dispatched transfer, line by line
  receiveOutward: (id, body) => fetch(`/api/outward/${id}/receive`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('recv'), { detail: j.detail }); return j }),
  // is this scanned garment on this transfer, and which line?
  verifyOutward: (id, code) => fetch(`/api/outward/${id}/verify?code=` + encodeURIComponent(code || ''))
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('verify'), { detail: j.detail }); return j }),

  // payments
  pendingBills: (supplierId) => fetch(`/api/payments/pending${supplierId ? '?supplier_id=' + supplierId : ''}`).then(J),
  listPayments: () => fetch('/api/payments').then(J),
  createPayment: (body) => fetch('/api/payments', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(J),
  supplierLedger: (id) => fetch(`/api/payments/supplier/${id}/ledger`).then(J),

  // purchase returns
  listReturns: () => fetch('/api/returns').then(J),
  getReturn: (id) => fetch(`/api/returns/${id}`).then(J),
  // `shortagesOnly` claims the goods that never arrived on their own — the usual
  // case, since a short delivery is chased long before anyone knows whether what
  // did arrive is any good. Shortage lines come back pre-filled: the count was
  // done at the dock, so nobody counts again.
  buildReturn: (purchaseId, shortagesOnly) => fetch(
    `/api/returns/from-purchase/${purchaseId}` + (shortagesOnly ? '?shortages_only=true' : ''),
    { method: 'POST' }).then(J),
  postReturn: (id, body) => fetch(`/api/returns/${id}/post`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('post'), { detail: j.detail }); return j }),

  // masters
  categories: (q) => fetch('/api/masters/categories' + (q ? '?q=' + encodeURIComponent(q) : '')).then(J),
  agents: () => fetch('/api/masters/agents').then(J),
  transports: () => fetch('/api/masters/transports').then(J),
  // the small keyed dropdown lists (company, city, rack, section, modes, …)
  masterOptions: () => fetch('/api/masters/options').then(J),
  addMasterOption: (kind, value) => fetch('/api/masters/options', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, value }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('opt'), { detail: j.detail }); return j }),
  deleteMasterOption: (kind, value) => fetch(
    `/api/masters/options?kind=${encodeURIComponent(kind)}&value=${encodeURIComponent(value)}`,
    { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('opt'), { detail: j.detail }); return j }),

  // unit types: how many individual items are in one of a unit (a pair is 2, a
  // dozen is 12), plus the rules that say which unit a product is. Together they
  // are what turns a billed dozen into 12 handkerchiefs or 6 pairs of pillow
  // covers — and into that many QR labels.
  unitTypes: () => fetch('/api/masters/unit-types').then(J),
  addUnitType: (body) => fetch('/api/masters/unit-types', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('ut'), { detail: j.detail }); return j }),
  editUnitType: (code, fields) => fetch(`/api/masters/unit-types/${encodeURIComponent(code)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('ut'), { detail: j.detail }); return j }),
  deleteUnitType: (code) => fetch(`/api/masters/unit-types/${encodeURIComponent(code)}`, { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('ut'), { detail: j.detail }); return j }),
  addUnitRule: (body) => fetch('/api/masters/unit-rules', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('ur'), { detail: j.detail }); return j }),
  deleteUnitRule: (id) => fetch(`/api/masters/unit-rules/${id}`, { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('ur'), { detail: j.detail }); return j }),
  // "1 DOZ → 12 pcs → 6 PAIR · 6 QR label(s)" for a quantity, before it is posted
  unitPreview: (params) => fetch('/api/masters/unit-preview' + qs(params)).then(J),

  // The 17 ERP masters. One set of endpoints for all of them: the shape of each
  // comes from its definition (services/master_defs.py), so a field added there
  // is typed, validated and saved without anything changing here.
  masterList: () => fetch('/api/master-data').then(J),
  masterDefinition: (key) => fetch(`/api/master-data/${key}/definition`).then(J),
  masterRecords: (key, q) => fetch(`/api/master-data/${key}/records` + qs({ q })).then(J),
  masterRecord: (key, id) => fetch(`/api/master-data/${key}/records/${id}`).then(J),
  masterCreate: (key, body) => fetch(`/api/master-data/${key}/records`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('m'), { detail: j.detail }); return j }),
  masterUpdate: (key, id, body) => fetch(`/api/master-data/${key}/records/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('m'), { detail: j.detail }); return j }),
  masterDelete: (key, id) => fetch(`/api/master-data/${key}/records/${id}`, { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('m'), { detail: j.detail }); return j }),

  // LR entry
  lrExtract: (file) => { const fd = new FormData(); fd.append('file', file);
    return fetch('/api/lr/extract', { method: 'POST', body: fd }).then(J) },
  lrSave: (document_id, rows) => fetch('/api/lr/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_id, rows }) }).then(J),
  lrList: () => fetch('/api/lr').then(J),
  lrGet: (id) => fetch(`/api/lr/${id}`).then(J),
  // key in ONE consignment (the LR Entry form's Save / Save&Next)
  lrCreate: (body) => fetch('/api/lr', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('lr'), { detail: j.detail }); return j }),
  // partial edit — freight settlement on delivery, the rack once put away, or
  // any other field on the entry. Only what you send is touched.
  lrUpdate: (id, fields) => fetch(`/api/lr/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('lr'), { detail: j.detail }); return j }),
  lrDelete: (id) => fetch(`/api/lr/${id}`, { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('lr'), { detail: j.detail }); return j }),
  lrSearch: (filters) => {
    const p = new URLSearchParams()
    Object.entries(filters || {}).forEach(([k, v]) => { if (v) p.append(k, v) })
    return fetch('/api/lr/search' + (p.toString() ? '?' + p : '')).then(J)
  },
  lrAddAttachment: (id, file, doc_type) => {
    const fd = new FormData(); fd.append('file', file); fd.append('doc_type', doc_type || '')
    return fetch(`/api/lr/${id}/attachments`, { method: 'POST', body: fd })
      .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('att'), { detail: j.detail }); return j })
  },
  lrDeleteAttachment: (attId) => fetch(`/api/lr/attachments/${attId}`, { method: 'DELETE' }).then(J),

  // Dead stock & clearance. The register, the dashboard, the alerts and the
  // summary are all the same server-side read, grouped differently — which is
  // why they are four calls and not one screen holding four copies of the data.
  //
  // `status` is kept on the error the way dashboardCharts does it: a 404 here is
  // a backend started before this module existed, which is a restart rather than
  // a fault, and the screen can say so instead of showing "failed".
  deadStock: (filters) => fetch('/api/dead-stock/register' + qs(filters))
    .then(r => { if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status }); return r.json() }),
  deadStockSummary: () => fetch('/api/dead-stock/summary')
    .then(r => { if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status }); return r.json() }),
  deadStockAlerts: () => fetch('/api/dead-stock/alerts')
    .then(r => { if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status }); return r.json() }),
  deadStockRules: () => fetch('/api/dead-stock/rules').then(J),
  saveDeadStockRules: (body) => fetch('/api/dead-stock/rules', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('rules'), { detail: j.detail }); return j }),
  clearanceList: (status) => fetch('/api/dead-stock/campaigns' + qs({ status })).then(J),
  clearanceGet: (id) => fetch(`/api/dead-stock/campaigns/${id}`).then(J),
  clearanceCreate: (body) => fetch('/api/dead-stock/campaigns', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('c'), { detail: j.detail }); return j }),
  clearanceUpdate: (id, body) => fetch(`/api/dead-stock/campaigns/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('c'), { detail: j.detail }); return j }),
  clearanceDelete: (id) => fetch(`/api/dead-stock/campaigns/${id}`, { method: 'DELETE' }).then(J),
  clearanceAddLines: (id, product_ids, actions) => fetch(`/api/dead-stock/campaigns/${id}/lines`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product_ids, actions: actions || {} }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('c'), { detail: j.detail }); return j }),
  clearanceUpdateLine: (id, lineId, body) => fetch(`/api/dead-stock/campaigns/${id}/lines/${lineId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('c'), { detail: j.detail }); return j }),
  clearanceDeleteLine: (id, lineId) => fetch(`/api/dead-stock/campaigns/${id}/lines/${lineId}`, { method: 'DELETE' }).then(J),

  // Notifications. The bell polls `notificationCount` (four numbers); the panel
  // and the dashboard read the whole feed. Both are the same server-side pass
  // over the queues, so they can never disagree about what is open.
  notifications: () => fetch('/api/notifications')
    .then(r => { if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status }); return r.json() }),
  notificationCount: () => fetch('/api/notifications/count')
    .then(r => { if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status }); return r.json() }),
  notificationsRead: (keys, by) => fetch('/api/notifications/read', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keys, by }) }).then(J),
  notificationsReadAll: (by) => fetch('/api/notifications/read-all', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ by }) }).then(J),
  notificationMute: (key, muted, by) => fetch('/api/notifications/mute', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, muted, by }) }).then(J),
  notificationsMuted: () => fetch('/api/notifications/muted').then(J),
  notificationRecipients: () => fetch('/api/notifications/recipients').then(J),
  addRecipient: (body) => fetch('/api/notifications/recipients', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('r'), { detail: j.detail }); return j }),
  updateRecipient: (id, body) => fetch(`/api/notifications/recipients/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('r'), { detail: j.detail }); return j }),
  deleteRecipient: (id) => fetch(`/api/notifications/recipients/${id}`, { method: 'DELETE' }).then(J),

  // Voice into a master form. Only non-English speech comes here (or English the
  // local matcher could not place): Tamil is transcribed as Tamil, and a master
  // record has to come out in English — the labels, the dropdown vocabularies and
  // every later search are English. See services/voice_form.py.
  voiceStatus: () => fetch('/api/voice/status').then(J),
  voiceFill: (master, transcript, fields, language) => fetch('/api/voice/fill', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ master, transcript, fields, language }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('v'), { detail: j.detail }); return j }),

  // reports. Each catalogue entry declares the filters it accepts (`params`);
  // the server drops anything a given report doesn't take, so both calls can
  // pass the same bag without a per-report branch.
  reportCatalogue: () => fetch('/api/reports').then(J),
  reportGroups: () => fetch('/api/reports/groups').then(J),
  runReport: (key, params) => fetch(`/api/reports/${key}${qs(params)}`).then(J),
  reportCsvUrl: (key, params) => `/api/reports/${key}/csv${qs(params)}`,
  // Ask a question instead of picking a report and setting filters. POST because
  // the question is free text in any script — Tamil in a query string is a
  // percent-encoding problem nobody needs.
  askReport: (q) => fetch('/api/reports/ask', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ q }) })
    .then(async r => { const j = await r.json().catch(() => ({}))
      // `status` is kept because 404/405 here means something specific and
      // fixable — a server still running from before this endpoint existed,
      // serving the new UI off disk — and the screen can say so.
      if (!r.ok) throw Object.assign(new Error('ask'), { status: r.status, detail: j.detail })
      return j }),
  reportAskExamples: () => fetch('/api/reports/ask-examples').then(J),

  // settings / vision
  getSettings: () => fetch('/api/settings').then(J),
  setVisionKey: (api_key, model) => fetch('/api/settings/vision', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key, model }) }).then(J),
  listModels: () => fetch('/api/settings/models').then(J),
  setModel: (model) => fetch('/api/settings/model', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }) }).then(J),
  turnOffVision: () => fetch('/api/settings/vision/off', { method: 'POST' }).then(J),
}
