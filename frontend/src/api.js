const J = (r) => { if (!r.ok) throw new Error(r.status); return r.json() }

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

  status: () => fetch('/api/status').then(J),
  listDocuments: () => fetch('/api/documents').then(J),
  getDocument: (id) => fetch(`/api/documents/${id}`).then(J),
  deleteDocument: (id) => fetch(`/api/documents/${id}`, { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('del'), { detail: j.detail }); return j }),
  clearAllDocuments: (wipeMasters) => fetch('/api/documents/clear-all' + (wipeMasters ? '?wipe_masters=true' : ''), { method: 'DELETE' }).then(J),
  imageUrl: (id) => `/api/documents/${id}/image`,
  upload: (file) => {
    const fd = new FormData()
    fd.append('file', file)
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

  // reports. Each catalogue entry declares the filters it accepts (`params`);
  // the server drops anything a given report doesn't take, so both calls can
  // pass the same bag without a per-report branch.
  reportCatalogue: () => fetch('/api/reports').then(J),
  reportGroups: () => fetch('/api/reports/groups').then(J),
  runReport: (key, params) => fetch(`/api/reports/${key}${qs(params)}`).then(J),
  reportCsvUrl: (key, params) => `/api/reports/${key}/csv${qs(params)}`,

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
